"""CLI auth/cost reporting tests. All runs are dry and offline."""

import time

import pytest

from textinator import xai_auth
from textinator.cli import main


@pytest.fixture(autouse=True)
def isolated_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTINATOR_HOME", str(tmp_path / "textinator-home"))
    monkeypatch.delenv("TEXTINATOR_XAI_AUTH_FILE", raising=False)
    monkeypatch.delenv("TEXTINATOR_XAI_AUTH", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)


def test_auth_status_reports_sources_without_secrets(monkeypatch, capsys):
    monkeypatch.setenv("XAI_API_KEY", "do-not-print-this")
    assert main(["auth", "status"]) == 0
    output = capsys.readouterr().out
    assert "OAuth:  not configured" in output
    assert "API key: available" in output
    assert "default: OAuth" in output
    assert "do-not-print-this" not in output


def test_api_dry_run_uses_short_metered_label(tmp_path, monkeypatch, capsys):
    source = tmp_path / "note.txt"
    source.write_text("A short note for dry-run reporting.", encoding="utf-8")
    monkeypatch.setenv("XAI_API_KEY", "api-key")

    assert main(
        [
            str(source),
            "--backend",
            "grok",
            "--xai-auth",
            "api",
            "--dry-run",
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "cost:             metered" in output
    assert "$" not in output


def test_oauth_dry_run_uses_short_subscription_label(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "note.txt"
    source.write_text("A short OAuth dry run.", encoding="utf-8")
    xai_auth._save_state(
        {
            "version": 1,
            "access_token": "oauth-access",
            "refresh_token": "oauth-refresh",
            "token_type": "Bearer",
            "expires_at": time.time() + 7200,
            "token_endpoint": "https://auth.x.ai/oauth2/token",
            "updated_at": time.time(),
        }
    )

    assert main(
        [
            str(source),
            "--backend",
            "grok",
            "--dry-run",
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "cost:             subscription" in output
    assert "$" not in output


def test_grok_list_voices_uses_live_catalog(monkeypatch, capsys):
    seen = {}

    def discover(auth_mode, *, allow_api_fallback=False):
        seen.update(
            auth_mode=auth_mode,
            allow_api_fallback=allow_api_fallback,
        )
        return (
            [
                {"voice_id": "cvabc123", "name": "Sample Voice", "custom": True},
                {"voice_id": "orion", "name": "Orion", "custom": False},
            ],
            None,
        )

    monkeypatch.setattr("textinator.backends.grok.discover_voices", discover)
    assert main(
        [
            "--backend",
            "grok",
            "--xai-auth",
            "api",
            "--list-voices",
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "cvabc123" in output and "Sample Voice (custom)" in output
    assert "orion" in output and "Orion (built-in)" in output
    assert seen == {"auth_mode": "api", "allow_api_fallback": False}


def test_grok_list_voices_reports_unavailable(monkeypatch, capsys):
    from textinator.backends.grok import GrokError

    def fail(*args, **kwargs):
        raise GrokError("XAI_API_KEY is not set")

    monkeypatch.setattr("textinator.backends.grok.discover_voices", fail)
    assert main(
        [
            "--backend",
            "grok",
            "--xai-auth",
            "api",
            "--list-voices",
        ]
    ) == 4
    assert (
        "Grok unavailable: XAI_API_KEY is not set"
        in capsys.readouterr().err
    )
