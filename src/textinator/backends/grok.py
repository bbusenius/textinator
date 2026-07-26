"""Grok (xAI) TTS backend.

POST https://api.x.ai/v1/tts with an OAuth access token or XAI_API_KEY.
The pipeline's character budget and content cache apply regardless of the
credential source. This backend is never the default.

Docs: https://docs.x.ai/developers/model-capabilities/audio/text-to-speech
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from . import Backend
from ..xai_auth import (
    XAIAuthError,
    XAICredential,
    auth_status,
    planned_auth_source,
    resolve_credentials,
)

API_URL = "https://api.x.ai/v1/tts"
VOICES = ("ara", "eve", "rex", "sal", "leo")
_TIMEOUT_SECONDS = 120


class GrokError(RuntimeError):
    pass


class GrokHTTPError(GrokError):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        super().__init__(f"xAI API error {status_code}: {detail}")


def _get(url: str, bearer_token: str) -> bytes:
    """One authenticated GET. Isolated so tests can stub it."""
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {bearer_token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode(errors="replace")[:500]
        except Exception:
            detail = ""
        raise GrokHTTPError(exc.code, detail or str(exc.reason)) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise GrokError(f"could not reach xAI API: {exc}") from exc


def list_custom_voices(
    auth_mode: str | None = None,
    *,
    allow_api_fallback: bool = False,
) -> list[dict]:
    """The team's cloned voices: [{"voice_id": ..., "name": ...}, ...].

    Raises GrokError without credentials or on network trouble; callers that
    only want decoration should catch and move on.
    """
    try:
        credential = resolve_credentials(
            auth_mode, allow_api_fallback=allow_api_fallback
        )
    except XAIAuthError as exc:
        raise GrokError(str(exc)) from exc
    try:
        raw = _get(
            f"{credential.base_url}/custom-voices",
            credential.token,
        )
    except GrokHTTPError as exc:
        if credential.source != "oauth" or exc.status_code not in {401, 403}:
            raise
        try:
            if exc.status_code == 403 and allow_api_fallback:
                credential = resolve_credentials("api")
            else:
                credential = resolve_credentials(
                    auth_mode,
                    force_refresh=True,
                    allow_api_fallback=allow_api_fallback,
                )
        except XAIAuthError as auth_exc:
            raise GrokError(str(auth_exc)) from auth_exc
        raw = _get(f"{credential.base_url}/custom-voices", credential.token)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GrokError("xAI custom-voices response was not valid JSON") from exc
    return payload.get("voices", [])


def _post(url: str, payload: dict, bearer_token: str) -> bytes:
    """One HTTPS POST returning raw audio bytes. Isolated so tests can stub it."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode(errors="replace")[:500]
        except Exception:
            pass
        raise GrokHTTPError(exc.code, detail or str(exc.reason)) from exc
    except urllib.error.URLError as exc:
        raise GrokError(f"could not reach xAI API: {exc.reason}") from exc


class GrokBackend(Backend):
    name = "grok"
    suffix = "mp3"
    # API cap is 15,000 chars/request; smaller chunks give finer-grained cache
    # hits, so a failed long run never re-pays for what already succeeded.
    max_chars = 4000
    paid = True

    def __init__(
        self,
        language: str = "en",
        speed: float = 1.0,
        auth_mode: str | None = None,
        allow_api_fallback: bool = False,
    ):
        self.language = language
        self.speed = speed
        self.auth_mode = auth_mode
        self.allow_api_fallback = allow_api_fallback
        self._last_credential: XAICredential | None = None

    @property
    def default_voice(self) -> str:
        return "eve"

    @property
    def cost_label(self) -> str:
        source = (
            self._last_credential.source
            if self._last_credential is not None
            else planned_auth_source(
                self.auth_mode,
                allow_api_fallback=self.allow_api_fallback,
            )
        )
        if source == "oauth":
            return "subscription"
        if source == "api":
            return "metered"
        return "unavailable"

    @property
    def auth_label(self) -> str:
        source = (
            self._last_credential.source
            if self._last_credential is not None
            else planned_auth_source(
                self.auth_mode,
                allow_api_fallback=self.allow_api_fallback,
            )
        )
        if source == "oauth":
            return "OAuth"
        if source == "api":
            return "API key"
        return "unavailable"

    @property
    def api_fallback_available(self) -> bool:
        return bool(auth_status()["api"])

    def use_api_key(self) -> None:
        """Explicitly select metered API-key authentication for this run."""
        self.auth_mode = "api"
        self.allow_api_fallback = False
        self._last_credential = None

    def _credentials(self, *, force_refresh: bool = False) -> XAICredential:
        try:
            credential = resolve_credentials(
                self.auth_mode,
                force_refresh=force_refresh,
                allow_api_fallback=self.allow_api_fallback,
            )
        except XAIAuthError as exc:
            raise GrokError(str(exc)) from exc
        self._last_credential = credential
        return credential

    def prepare_auth(self) -> XAICredential:
        """Resolve/refresh credentials before a run sends any text."""
        return self._credentials()

    def params_fingerprint(self) -> str:
        return f"language={self.language}|speed={self.speed}"

    def synthesize(self, text: str, voice: str, out_path: Path) -> None:
        credential = self._credentials()
        payload = {
            "text": text,
            "language": self.language,
            "voice_id": voice,
            # spine does its own normalization; don't pay for theirs
            "text_normalization": False,
        }
        if self.speed != 1.0:
            payload["speed"] = self.speed
        try:
            audio = _post(
                f"{credential.base_url}/tts",
                payload,
                credential.token,
            )
        except GrokHTTPError as exc:
            if credential.source != "oauth" or exc.status_code not in {401, 403}:
                raise
            if exc.status_code == 403 and self.allow_api_fallback:
                try:
                    credential = resolve_credentials("api")
                except XAIAuthError as auth_exc:
                    raise GrokError(str(auth_exc)) from auth_exc
                self._last_credential = credential
            else:
                credential = self._credentials(force_refresh=True)
            audio = _post(
                f"{credential.base_url}/tts",
                payload,
                credential.token,
            )
        if not audio:
            raise GrokError("xAI API returned empty audio")
        # JSON instead of audio bytes means something went sideways
        if audio[:1] in (b"{", b"["):
            raise GrokError(f"xAI API returned JSON, not audio: {audio[:200]!r}")
        out_path.write_bytes(audio)
