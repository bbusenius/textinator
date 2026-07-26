"""Standalone xAI auth tests. No request in this file may reach xAI."""

import os
import time

import pytest

from textinator import xai_auth


@pytest.fixture(autouse=True)
def isolated_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTINATOR_HOME", str(tmp_path / "textinator-home"))
    monkeypatch.delenv("TEXTINATOR_XAI_AUTH_FILE", raising=False)
    monkeypatch.delenv("TEXTINATOR_XAI_AUTH", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)


def _oauth_state(**overrides):
    state = {
        "version": 1,
        "access_token": "oauth-access",
        "refresh_token": "oauth-refresh",
        "token_type": "Bearer",
        "expires_at": time.time() + 7200,
        "token_endpoint": "https://auth.x.ai/oauth2/token",
        "updated_at": time.time(),
    }
    state.update(overrides)
    return state


def test_api_key_used_when_oauth_is_not_configured(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "api-key")
    credential = xai_auth.resolve_credentials("api")
    assert credential.source == "api"
    assert credential.token == "api-key"
    assert credential.cost_label == "metered"


def test_default_oauth_does_not_silently_use_api(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "api-key")
    with pytest.raises(xai_auth.XAIAuthError, match="OAuth is not configured"):
        xai_auth.resolve_credentials()


def test_oauth_is_preferred_over_environment_api_key(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "api-key")
    xai_auth._save_state(_oauth_state())
    credential = xai_auth.resolve_credentials()
    assert credential.source == "oauth"
    assert credential.token == "oauth-access"
    assert credential.cost_label == "subscription"


def test_expiring_oauth_is_refreshed(monkeypatch):
    xai_auth._save_state(_oauth_state(expires_at=0))
    captured = {}

    def fake_form(url, fields, timeout=xai_auth._HTTP_TIMEOUT_SECONDS):
        captured.update(url=url, fields=fields)
        return 200, {
            "access_token": "fresh-access",
            "refresh_token": "fresh-refresh",
            "expires_in": 21600,
        }, ""

    monkeypatch.setattr(xai_auth, "_form_request", fake_form)
    credential = xai_auth.resolve_credentials()
    assert credential.token == "fresh-access"
    assert captured["fields"]["grant_type"] == "refresh_token"
    assert xai_auth._read_state()["refresh_token"] == "fresh-refresh"


def test_failed_oauth_does_not_silently_use_api(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "api-key")
    xai_auth._save_state(_oauth_state(expires_at=0))
    monkeypatch.setattr(
        xai_auth,
        "_form_request",
        lambda *args, **kwargs: (401, {"error": "invalid_grant"}, "invalid_grant"),
    )
    with pytest.raises(xai_auth.XAIAuthError, match="refresh failed"):
        xai_auth.resolve_credentials()

    credential = xai_auth.resolve_credentials(allow_api_fallback=True)
    assert credential.source == "api"
    assert credential.token == "api-key"


def test_device_login_saves_private_refreshable_tokens(monkeypatch):
    monkeypatch.setattr(
        xai_auth,
        "_discover",
        lambda: {"token_endpoint": "https://auth.x.ai/oauth2/token"},
    )
    responses = iter(
        [
            (
                200,
                {
                    "device_code": "device",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://x.ai/device",
                    "verification_uri_complete": "https://x.ai/device?code=ABCD-1234",
                    "expires_in": 600,
                    "interval": 1,
                },
                "",
            ),
            (
                200,
                {
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 21600,
                    "token_type": "Bearer",
                },
                "",
            ),
        ]
    )
    monkeypatch.setattr(
        xai_auth, "_form_request", lambda *args, **kwargs: next(responses)
    )
    output = []
    status = xai_auth.oauth_login(open_browser=False, print_fn=output.append)

    assert status["oauth"] is True
    assert any("ABCD-1234" in line for line in output)
    assert xai_auth._read_state()["refresh_token"] == "refresh"
    assert os.stat(xai_auth.auth_store_path()).st_mode & 0o777 == 0o600


def test_logout_only_removes_textinator_oauth(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "api-key")
    xai_auth._save_state(_oauth_state())
    assert xai_auth.oauth_logout() is True
    assert xai_auth.auth_status() == {
        "oauth": False,
        "api": True,
        "default": "oauth",
        "path": str(xai_auth.auth_store_path()),
    }
    assert os.environ["XAI_API_KEY"] == "api-key"
