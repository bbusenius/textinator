"""Split normalized text into TTS-sized chunks on natural boundaries.

Every backend has an input length cap. We pack paragraphs greedily up to
``max_chars``; paragraphs that are themselves too big get split on sentence
boundaries, and pathological single sentences get hard-split as a last resort.
No text is ever dropped.
"""

from __future__ import annotations

import re

# Sentence boundary: terminal punctuation followed by whitespace and something
# that plausibly starts a new sentence. Deliberately conservative.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…])\s+(?=[\"'(\[A-Z0-9])")


def _split_sentences(paragraph: str) -> list[str]:
    return _SENTENCE_BOUNDARY.split(paragraph)


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Last resort: split an oversized sentence at word boundaries."""
    pieces: list[str] = []
    while len(text) > max_chars:
        cut = text.rfind(" ", 0, max_chars)
        if cut <= 0:
            cut = max_chars
        pieces.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        pieces.append(text)
    return pieces


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Split ``text`` into chunks of at most ``max_chars``, on natural boundaries."""
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")

    # Units to pack: paragraphs, pre-split if oversized.
    units: list[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            units.append(paragraph)
            continue
        for sentence in _split_sentences(paragraph):
            if len(sentence) <= max_chars:
                units.append(sentence)
            else:
                units.extend(_hard_split(sentence, max_chars))

    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks
