"""Shared fixtures. DummyBackend produces real (tiny, silent) MP3s via local
ffmpeg — no network, no paid API — so stitch/probe/feed paths are tested for real."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from textinator.backends import Backend


class DummyBackend(Backend):
    name = "dummy"
    suffix = "mp3"
    max_chars = 200
    paid = False

    def __init__(self):
        self.calls: list[str] = []

    @property
    def default_voice(self) -> str:
        return "test-voice"

    def synthesize(self, text: str, voice: str, out_path: Path) -> None:
        self.calls.append(text)
        _make_silence_mp3(out_path, seconds=0.2)


class PaidDummyBackend(DummyBackend):
    name = "paid-dummy"
    paid = True


def _make_silence_mp3(out_path: Path, seconds: float = 0.2) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono",
            "-t", str(seconds), "-c:a", "libmp3lame", "-b:a", "48k",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def dummy_backend():
    return DummyBackend()


@pytest.fixture
def paid_backend():
    return PaidDummyBackend()


@pytest.fixture
def make_mp3(tmp_path):
    def _make(name: str = "clip.mp3", seconds: float = 0.2) -> Path:
        path = tmp_path / name
        _make_silence_mp3(path, seconds)
        return path

    return _make
