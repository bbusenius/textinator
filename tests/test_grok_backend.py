"""Grok backend tests — ALL mocked. No test in this file may hit the paid API."""

import pytest

from textinator.backends import get_backend
from textinator.backends.grok import GrokBackend, GrokError, GrokHTTPError
from textinator.xai_auth import XAICredential

FAKE_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 64  # mp3 frame sync header + padding


@pytest.fixture
def grok(monkeypatch, tmp_path):
    monkeypatch.setenv("TEXTINATOR_HOME", str(tmp_path / "auth"))
    monkeypatch.setenv("XAI_API_KEY", "test-key-not-real")
    return GrokBackend(auth_mode="api")


def test_registry_returns_grok(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "k")
    assert isinstance(get_backend("grok"), GrokBackend)


def test_is_guarded_without_inventing_a_price(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "k")
    assert GrokBackend.paid is True
    assert GrokBackend(auth_mode="api").cost_label == "metered"
    assert GrokBackend.max_chars <= 15_000  # API hard cap


def test_synthesize_posts_correct_payload(grok, monkeypatch, tmp_path):
    captured = {}

    def fake_post(url, payload, api_key):
        captured.update(url=url, payload=payload, api_key=api_key)
        return FAKE_MP3

    monkeypatch.setattr("textinator.backends.grok._post", fake_post)
    out = tmp_path / "out.mp3"
    grok.synthesize("Hello world.", "eve", out)

    assert captured["url"] == "https://api.x.ai/v1/tts"
    assert captured["api_key"] == "test-key-not-real"
    assert captured["payload"]["text"] == "Hello world."
    assert captured["payload"]["voice_id"] == "eve"
    assert captured["payload"]["language"] == "en"
    assert captured["payload"]["text_normalization"] is False
    assert out.read_bytes() == FAKE_MP3


def test_missing_api_key_fails_before_any_request(monkeypatch, tmp_path):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "textinator.backends.grok._post",
        lambda *a: pytest.fail("must not POST without a key"),
    )
    backend = GrokBackend(auth_mode="api")
    with pytest.raises(GrokError, match="XAI_API_KEY"):
        backend.synthesize("hi", "eve", tmp_path / "x.mp3")


def test_json_error_response_raises(grok, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "textinator.backends.grok._post",
        lambda *a: b'{"error": "quota exceeded"}',
    )
    with pytest.raises(GrokError, match="JSON, not audio"):
        grok.synthesize("hi", "eve", tmp_path / "x.mp3")


def test_empty_response_raises(grok, monkeypatch, tmp_path):
    monkeypatch.setattr("textinator.backends.grok._post", lambda *a: b"")
    with pytest.raises(GrokError, match="empty"):
        grok.synthesize("hi", "eve", tmp_path / "x.mp3")


def test_list_custom_voices(monkeypatch):
    from textinator.backends import grok as grok_mod

    monkeypatch.setenv("TEXTINATOR_XAI_AUTH", "api")
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    monkeypatch.setattr(
        grok_mod, "_get",
        lambda url, key: b'{"voices": [{"voice_id": "cvabc123", "name": "Sample Voice"}], "total_count": 1}',
    )
    voices = grok_mod.list_custom_voices()
    assert voices == [{"voice_id": "cvabc123", "name": "Sample Voice"}]


def test_list_builtin_voices_uses_live_xai_catalog(monkeypatch):
    from textinator.backends import grok as grok_mod

    monkeypatch.setenv("XAI_API_KEY", "test-key")
    captured = {}

    def fake_get(url, key):
        captured.update(url=url, key=key)
        return (
            b'{"voices": ['
            b'{"voice_id": "orion", "name": "Orion"},'
            b'{"voice_id": "eve", "name": "Eve"}'
            b"]}"
        )

    monkeypatch.setattr(grok_mod, "_get", fake_get)
    voices = grok_mod.list_builtin_voices("api")
    assert [voice["voice_id"] for voice in voices] == ["orion", "eve"]
    assert captured == {
        "url": "https://api.x.ai/v1/tts/voices",
        "key": "test-key",
    }


def test_discover_voices_merges_custom_first_without_static_fallback(monkeypatch):
    from textinator.backends import grok as grok_mod

    monkeypatch.setattr(
        grok_mod,
        "list_builtin_voices",
        lambda *args, **kwargs: [{"voice_id": "orion", "name": "Orion"}],
    )
    monkeypatch.setattr(
        grok_mod,
        "list_custom_voices",
        lambda *args, **kwargs: [
            {"voice_id": "cvabc123", "name": "Sample Voice"},
            {"voice_id": "orion", "name": "Duplicate"},
        ],
    )
    voices, warning = grok_mod.discover_voices("oauth")
    assert voices == [
        {
            "voice_id": "cvabc123",
            "name": "Sample Voice",
            "custom": True,
        },
        {
            "voice_id": "orion",
            "name": "Duplicate",
            "custom": True,
        },
    ]
    assert warning is None


def test_discover_voices_reports_custom_catalog_failure(monkeypatch):
    from textinator.backends import grok as grok_mod

    monkeypatch.setattr(
        grok_mod,
        "list_builtin_voices",
        lambda *args, **kwargs: [{"voice_id": "orion", "name": "Orion"}],
    )

    def fail_custom(*args, **kwargs):
        raise GrokError("custom endpoint timed out")

    monkeypatch.setattr(grok_mod, "list_custom_voices", fail_custom)
    voices, warning = grok_mod.discover_voices("oauth")
    assert voices == [
        {"voice_id": "orion", "name": "Orion", "custom": False}
    ]
    assert warning == "custom voices unavailable: custom endpoint timed out"


def test_list_custom_voices_requires_key(monkeypatch):
    from textinator.backends import grok as grok_mod

    monkeypatch.setenv("TEXTINATOR_XAI_AUTH", "api")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(
        grok_mod, "_get",
        lambda *a: pytest.fail("must not GET without a key"),
    )
    with pytest.raises(GrokError, match="XAI_API_KEY"):
        grok_mod.list_custom_voices()


def test_fingerprint_includes_language_and_speed(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "k")
    a = GrokBackend(language="en", speed=1.0).params_fingerprint()
    b = GrokBackend(language="pt-BR", speed=1.0).params_fingerprint()
    c = GrokBackend(language="en", speed=1.2).params_fingerprint()
    assert len({a, b, c}) == 3


def test_oauth_401_refreshes_once(monkeypatch, tmp_path):
    from textinator.backends import grok as grok_mod

    credentials = iter(
        [
            XAICredential("stale-oauth", "oauth"),
            XAICredential("fresh-oauth", "oauth"),
        ]
    )
    refresh_flags = []

    def fake_resolve(mode, *, force_refresh=False, allow_api_fallback=False):
        refresh_flags.append(force_refresh)
        return next(credentials)

    calls = []

    def fake_post(url, payload, token):
        calls.append(token)
        if token == "stale-oauth":
            raise GrokHTTPError(401, "expired")
        return FAKE_MP3

    monkeypatch.setattr(grok_mod, "resolve_credentials", fake_resolve)
    monkeypatch.setattr(grok_mod, "_post", fake_post)
    out = tmp_path / "oauth.mp3"
    GrokBackend(auth_mode="oauth").synthesize("hello", "eve", out)

    assert calls == ["stale-oauth", "fresh-oauth"]
    assert refresh_flags == [False, True]
    assert out.read_bytes() == FAKE_MP3


def test_explicit_api_fallback_handles_oauth_403(monkeypatch, tmp_path):
    from textinator.backends import grok as grok_mod

    def fake_resolve(mode, *, force_refresh=False, allow_api_fallback=False):
        if mode == "api":
            return XAICredential("api-key", "api")
        return XAICredential("oauth-token", "oauth")

    calls = []

    def fake_post(url, payload, token):
        calls.append(token)
        if token == "oauth-token":
            raise GrokHTTPError(403, "subscription endpoint denied")
        return FAKE_MP3

    monkeypatch.setattr(grok_mod, "resolve_credentials", fake_resolve)
    monkeypatch.setattr(grok_mod, "_post", fake_post)
    backend = GrokBackend(auth_mode="oauth", allow_api_fallback=True)
    backend.synthesize("hello", "eve", tmp_path / "fallback.mp3")

    assert calls == ["oauth-token", "api-key"]
    assert backend.auth_label == "API key"
    assert backend.cost_label == "metered"
