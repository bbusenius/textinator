"""TTS backend interface and registry.

Backends are pluggable and chosen per run; none is special. A backend turns one
text chunk into one audio file. The pipeline handles chunking, caching, and
stitching — backends stay dumb.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class Backend(ABC):
    """One TTS engine. Subclasses synthesize a single chunk to an audio file."""

    #: registry name, e.g. "edge"
    name: str
    #: audio container the backend emits for chunks, e.g. "mp3"
    suffix: str = "mp3"
    #: max characters per synthesize() call — the chunker honors this
    max_chars: int = 4000
    #: True if this backend costs money per character
    paid: bool = False

    @property
    @abstractmethod
    def default_voice(self) -> str: ...

    @property
    def cost_label(self) -> str:
        """Short, non-speculative description shown before synthesis."""
        return "metered" if self.paid else "free"

    @abstractmethod
    def synthesize(self, text: str, voice: str, out_path: Path) -> None:
        """Generate audio for ``text`` and write it to ``out_path``."""

    def params_fingerprint(self) -> str:
        """Extra settings that affect audio output, folded into the cache key."""
        return ""


def get_backend(name: str) -> Backend:
    """Instantiate a backend by name. Imports lazily so optional deps stay optional."""
    if name == "edge":
        from .edge import EdgeBackend

        return EdgeBackend()
    if name == "kokoro":
        from .kokoro import KokoroBackend

        return KokoroBackend()
    if name == "grok":
        from .grok import GrokBackend

        return GrokBackend()
    raise ValueError(f"Unknown backend: {name!r} (available: edge, kokoro, grok)")


AVAILABLE_BACKENDS = ("edge", "kokoro", "grok")
