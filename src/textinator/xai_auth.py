"""Standalone xAI OAuth and API-key credential handling for Textinator.

Textinator owns its OAuth grant and token store.  It does not read credentials
from Hermes, OpenCode, or any other application.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

XAI_OAUTH_ISSUER = "https://auth.x.ai"
XAI_OAUTH_DISCOVERY_URL = f"{XAI_OAUTH_ISSUER}/.well-known/openid-configuration"
XAI_OAUTH_DEVICE_CODE_URL = f"{XAI_OAUTH_ISSUER}/oauth2/device/code"
XAI_OAUTH_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_OAUTH_SCOPE = "openid profile email offline_access grok-cli:access api:access"
XAI_API_BASE_URL = "https://api.x.ai/v1"
REFRESH_SKEW_SECONDS = 60 * 60
_HTTP_TIMEOUT_SECONDS = 20
_VALID_AUTH_MODES = frozenset({"oauth", "api"})
_thread_lock = threading.RLock()


class XAIAuthError(RuntimeError):
    """xAI credentials are missing, invalid, or could not be refreshed."""


@dataclass(frozen=True)
class XAICredential:
    token: str
    source: str  # "oauth" or "api"
    base_url: str = XAI_API_BASE_URL

    @property
    def cost_label(self) -> str:
        return "subscription" if self.source == "oauth" else "metered"


def auth_store_path() -> Path:
    """Return Textinator's private xAI OAuth token-store path."""
    override = os.environ.get("TEXTINATOR_XAI_AUTH_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    textinator_home = os.environ.get("TEXTINATOR_HOME", "").strip()
    if textinator_home:
        return Path(textinator_home).expanduser() / "xai-auth.json"
    data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return root / "textinator" / "xai-auth.json"


@contextmanager
def _store_lock() -> Iterator[None]:
    """Serialize token refreshes across threads and local processes."""
    path = auth_store_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    lock_path = path.with_suffix(path.suffix + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with _thread_lock:
        try:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            os.close(fd)


def _read_state_unlocked() -> dict | None:
    path = auth_store_path()
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise XAIAuthError(f"could not read xAI OAuth credentials: {exc}") from exc
    if not isinstance(state, dict):
        raise XAIAuthError("xAI OAuth credential file is not a JSON object")
    return state


def _read_state() -> dict | None:
    with _store_lock():
        return _read_state_unlocked()


def _write_state_unlocked(state: dict) -> None:
    path = auth_store_path()
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        path.chmod(0o600)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _save_state(state: dict) -> None:
    with _store_lock():
        _write_state_unlocked(state)


def _validate_auth_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "auth.x.ai"
        or parsed.port not in (None, 443)
    ):
        raise XAIAuthError(f"refusing non-xAI OAuth endpoint: {url}")
    return url


def _decode_jwt_exp(token: str) -> float | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        return float(decoded["exp"])
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _expires_at(payload: dict, now: float | None = None) -> float:
    now = time.time() if now is None else now
    try:
        return now + max(0, int(payload.get("expires_in", 0)))
    except (TypeError, ValueError):
        pass
    token_exp = _decode_jwt_exp(str(payload.get("access_token") or ""))
    return token_exp or now


def _oauth_configured(state: dict | None) -> bool:
    if not isinstance(state, dict):
        return False
    return bool(
        str(state.get("access_token") or "").strip()
        and str(state.get("refresh_token") or "").strip()
    )


def _form_request(url: str, fields: dict, timeout: float = _HTTP_TIMEOUT_SECONDS) -> tuple[int, dict, str]:
    """POST form data and return status, decoded JSON (if any), and raw text."""
    _validate_auth_url(url)
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode(),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read().decode(errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        raise XAIAuthError(f"could not reach xAI OAuth service: {exc}") from exc
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {}
    return status, payload if isinstance(payload, dict) else {}, raw


def _discover() -> dict:
    request = urllib.request.Request(
        _validate_auth_url(XAI_OAUTH_DISCOVERY_URL),
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise XAIAuthError(f"xAI OAuth discovery failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise XAIAuthError("xAI OAuth discovery returned an invalid response")
    token_endpoint = _validate_auth_url(str(payload.get("token_endpoint") or ""))
    return {"token_endpoint": token_endpoint}


def oauth_login(
    *,
    open_browser: bool = True,
    print_fn: Callable[[str], None] = print,
) -> dict:
    """Run xAI's device-code flow and save a standalone Textinator grant."""
    discovery = _discover()
    status, device, raw = _form_request(
        XAI_OAUTH_DEVICE_CODE_URL,
        {"client_id": XAI_OAUTH_CLIENT_ID, "scope": XAI_OAUTH_SCOPE},
    )
    if status != 200:
        raise XAIAuthError(f"xAI device authorization failed: {raw or status}")
    required = ("device_code", "user_code", "verification_uri", "expires_in", "interval")
    missing = [key for key in required if key not in device]
    if missing:
        raise XAIAuthError(
            f"xAI device authorization omitted: {', '.join(missing)}"
        )

    verification_url = str(
        device.get("verification_uri_complete") or device["verification_uri"]
    )
    user_code = str(device["user_code"])
    print_fn(f"Open: {verification_url}")
    print_fn(f"Code: {user_code}")
    if open_browser:
        try:
            webbrowser.open(verification_url)
        except Exception:
            pass
    print_fn("Waiting for authorization...")

    deadline = time.monotonic() + max(1, int(device["expires_in"]))
    interval = max(1, int(device["interval"]))
    token_payload: dict | None = None
    while time.monotonic() < deadline:
        status, payload, raw = _form_request(
            discovery["token_endpoint"],
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": XAI_OAUTH_CLIENT_ID,
                "device_code": str(device["device_code"]),
            },
        )
        if status == 200:
            token_payload = payload
            break
        error = str(payload.get("error") or "")
        if error == "authorization_pending":
            time.sleep(interval)
            continue
        if error == "slow_down":
            interval = min(interval + 1, 30)
            time.sleep(interval)
            continue
        raise XAIAuthError(f"xAI authorization failed: {raw or status}")
    if token_payload is None:
        raise XAIAuthError("timed out waiting for xAI authorization")

    access_token = str(token_payload.get("access_token") or "").strip()
    refresh_token = str(token_payload.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        raise XAIAuthError("xAI authorization did not return refreshable credentials")
    now = time.time()
    state = {
        "version": 1,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": str(token_payload.get("token_type") or "Bearer"),
        "expires_at": _expires_at(token_payload, now),
        "token_endpoint": discovery["token_endpoint"],
        "updated_at": now,
    }
    _save_state(state)
    return auth_status()


def _oauth_credential(*, force_refresh: bool = False) -> XAICredential:
    with _store_lock():
        state = _read_state_unlocked()
        if not _oauth_configured(state):
            raise XAIAuthError(
                "xAI OAuth is not configured; run `textinator auth xai`"
            )
        assert state is not None
        try:
            expires_at = float(state.get("expires_at", 0))
        except (TypeError, ValueError):
            expires_at = 0
        needs_refresh = force_refresh or expires_at <= time.time() + REFRESH_SKEW_SECONDS
        if not needs_refresh:
            return XAICredential(str(state["access_token"]).strip(), "oauth")

        refresh_token = str(state.get("refresh_token") or "").strip()
        token_endpoint = str(state.get("token_endpoint") or "").strip()
        if not token_endpoint:
            token_endpoint = _discover()["token_endpoint"]
        status, payload, raw = _form_request(
            token_endpoint,
            {
                "grant_type": "refresh_token",
                "client_id": XAI_OAUTH_CLIENT_ID,
                "refresh_token": refresh_token,
            },
        )
        if status != 200:
            raise XAIAuthError(f"xAI OAuth refresh failed: {raw or status}")
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise XAIAuthError("xAI OAuth refresh returned no access token")
        now = time.time()
        state.update(
            {
                "access_token": access_token,
                "refresh_token": str(payload.get("refresh_token") or refresh_token),
                "token_type": str(payload.get("token_type") or "Bearer"),
                "expires_at": _expires_at(payload, now),
                "updated_at": now,
            }
        )
        _write_state_unlocked(state)
        return XAICredential(access_token, "oauth")


def _auth_mode(value: str | None) -> str:
    mode = (value or os.environ.get("TEXTINATOR_XAI_AUTH") or "oauth").lower().strip()
    if mode not in _VALID_AUTH_MODES:
        raise XAIAuthError(
            f"invalid xAI auth mode {mode!r}; use oauth or api"
        )
    return mode


def resolve_credentials(
    mode: str | None = None,
    *,
    force_refresh: bool = False,
    allow_api_fallback: bool = False,
) -> XAICredential:
    """Resolve explicitly selected xAI credentials.

    A failed configured OAuth grant never silently becomes metered API usage.
    Callers must pass ``allow_api_fallback=True`` or explicitly select ``api``.
    """
    selected = _auth_mode(mode)
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if selected == "api":
        if not api_key:
            raise XAIAuthError("XAI_API_KEY is not set")
        return XAICredential(api_key, "api")

    state = _read_state()
    if _oauth_configured(state):
        try:
            return _oauth_credential(force_refresh=force_refresh)
        except XAIAuthError:
            if allow_api_fallback and api_key:
                return XAICredential(api_key, "api")
            raise

    if allow_api_fallback and api_key:
        return XAICredential(api_key, "api")
    raise XAIAuthError("xAI OAuth is not configured; run `textinator auth xai`")


def planned_auth_source(
    mode: str | None = None,
    *,
    allow_api_fallback: bool = False,
) -> str | None:
    """Return the locally configured source without refreshing or networking."""
    selected = _auth_mode(mode)
    if selected == "api":
        return "api" if os.environ.get("XAI_API_KEY", "").strip() else None
    try:
        state = _read_state()
    except XAIAuthError:
        state = None
    if _oauth_configured(state):
        return "oauth"
    if allow_api_fallback and os.environ.get("XAI_API_KEY", "").strip():
        return "api"
    return None


def auth_status() -> dict:
    """Return secret-free local xAI authentication status."""
    state = _read_state()
    oauth = _oauth_configured(state)
    api = bool(os.environ.get("XAI_API_KEY", "").strip())
    return {
        "oauth": oauth,
        "api": api,
        "default": _auth_mode(None),
        "path": str(auth_store_path()),
    }


def oauth_logout() -> bool:
    """Delete Textinator's OAuth grant, leaving XAI_API_KEY untouched."""
    with _store_lock():
        path = auth_store_path()
        if not path.exists():
            return False
        path.unlink()
        return True
