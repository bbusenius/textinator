"""Content-hash audio cache.

Audio is cached per chunk, keyed by (backend, voice, params, text). Re-running
identical text never regenerates — and for paid backends, never re-pays.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "textinator"


class AudioCache:
    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(backend: str, voice: str, params: str, text: str) -> str:
        payload = f"{backend}|{voice}|{params}|{text}".encode()
        return hashlib.sha256(payload).hexdigest()

    def path_for(self, key: str, suffix: str) -> Path:
        return self.cache_dir / f"{key}.{suffix}"

    def get(self, key: str, suffix: str) -> Path | None:
        """Return the cached audio path, or None on miss."""
        path = self.path_for(key, suffix)
        if path.is_file() and path.stat().st_size > 0:
            return path
        return None
