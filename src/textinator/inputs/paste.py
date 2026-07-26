"""Paste/text input adapter: a text file or stdin. The v0 workhorse."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from . import Document

_MAX_TITLE_LEN = 70


def derive_title(text: str, fallback: str = "Untitled") -> str:
    """First non-empty line, cleaned and trimmed, as an episode title."""
    for line in text.splitlines():
        line = re.sub(r"^#{1,6}\s+", "", line).strip()
        line = re.sub(r"\s+", " ", line)
        if line:
            if len(line) > _MAX_TITLE_LEN:
                line = line[:_MAX_TITLE_LEN].rsplit(" ", 1)[0] + "…"
            return line
    return fallback


def load_text(text: str) -> Document:
    """Wrap an already-in-hand string (web UI paste box) as a Document."""
    if not text.strip():
        raise ValueError("no text provided")
    return Document(title=derive_title(text), text=text)


def load(source: str | None) -> Document:
    """Load text from a file path, or from stdin when source is None or '-'."""
    if source is None or source == "-":
        text = sys.stdin.read()
        if not text.strip():
            raise ValueError("no text on stdin — pipe or paste something in")
        return Document(title=derive_title(text), text=text)

    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {source}")
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise ValueError(f"file is empty: {source}")
    return Document(title=derive_title(text, fallback=path.stem), text=text)
