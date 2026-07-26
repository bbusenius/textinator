"""Kokoro backend tests — the model is mocked; no test loads Kokoro-82M."""

import numpy as np
import pytest

from textinator.backends import get_backend
from textinator.backends.kokoro import SAMPLE_RATE, KokoroBackend


class FakePipeline:
    """Stands in for kokoro.KPipeline: yields (graphemes, phonemes, audio)."""

    def __init__(self):
        self.calls = []

    def __call__(self, text, voice):
        self.calls.append((text, voice))
        yield "graphemes", "phonemes", np.zeros(2400, dtype=np.float32)
        yield "graphemes", "phonemes", np.ones(2400, dtype=np.float32) * 0.1


@pytest.fixture
def kokoro(monkeypatch):
    backend = KokoroBackend()
    fake = FakePipeline()
    monkeypatch.setattr(backend, "_get_pipeline", lambda lang: fake)
    return backend, fake


def test_registry_returns_kokoro():
    assert isinstance(get_backend("kokoro"), KokoroBackend)


def test_is_free_offline_wav():
    assert KokoroBackend.paid is False
    assert KokoroBackend.suffix == "wav"


def test_synthesize_writes_playable_wav(kokoro, tmp_path):
    import soundfile as sf

    backend, fake = kokoro
    out = tmp_path / "chunk.wav"
    backend.synthesize("Hello there.", "af_heart", out)

    assert fake.calls == [("Hello there.", "af_heart")]
    data, rate = sf.read(out)
    assert rate == SAMPLE_RATE
    assert len(data) == 4800  # both segments concatenated


def test_empty_generation_raises(monkeypatch, tmp_path):
    backend = KokoroBackend()
    monkeypatch.setattr(backend, "_get_pipeline", lambda lang: lambda text, voice: iter([]))
    with pytest.raises(RuntimeError, match="no audio"):
        backend.synthesize("hi", "af_heart", tmp_path / "x.wav")


def test_wav_chunks_stitch_to_mp3_episode(kokoro, tmp_path):
    """The full pipeline path kokoro exercises: wav chunks -> mp3 episode."""
    from textinator.inputs import Document
    from textinator.pipeline import run

    backend, _ = kokoro
    result = run(
        Document(title="Offline Test", text="One sentence. " * 30),
        backend,
        feed_dir=tmp_path / "feed",
        cache_dir=tmp_path / "cache",
    )
    assert result.episode_path.suffix == ".mp3"
    from textinator.stitch import probe_duration

    assert probe_duration(result.episode_path) > 0
