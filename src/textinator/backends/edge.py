"""edge-tts backend: free Azure neural voices via the unofficial Edge endpoint.

Online + unofficial (can break if Microsoft changes the endpoint), but free,
no key, and high quality. The dev/default backend.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from . import Backend


class EdgeBackend(Backend):
    name = "edge"
    suffix = "mp3"
    # edge-tts streams internally, but keep chunks modest so cache hits are
    # granular and a mid-stream failure loses little work.
    max_chars = 4000
    paid = False

    def __init__(self, rate: str = "+0%"):
        self.rate = rate

    @property
    def default_voice(self) -> str:
        return "en-US-GuyNeural"

    def params_fingerprint(self) -> str:
        return f"rate={self.rate}"

    def synthesize(self, text: str, voice: str, out_path: Path) -> None:
        import edge_tts

        async def _run() -> None:
            communicate = edge_tts.Communicate(text, voice, rate=self.rate)
            await communicate.save(str(out_path))

        asyncio.run(_run())

    @staticmethod
    def list_voices(language_prefix: str | None = None) -> list[dict]:
        """Fetch available voices, optionally filtered by locale prefix (e.g. 'en')."""
        import edge_tts

        voices = asyncio.run(edge_tts.list_voices())
        if language_prefix:
            voices = [
                v for v in voices
                if v["Locale"].lower().startswith(language_prefix.lower())
            ]
        return sorted(voices, key=lambda v: v["ShortName"])
