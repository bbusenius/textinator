"""Normalize raw text for the ear.

Input adapters hand us plain-ish text (possibly with markdown artifacts, URLs,
odd whitespace). This module turns it into text a TTS engine reads well.
Paragraph boundaries (blank lines) are preserved — the chunker relies on them.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Abbreviations that read badly aloud -> spoken equivalents.
_ABBREVIATIONS = [
    (re.compile(r"\be\.g\.,?\s", re.IGNORECASE), "for example, "),
    (re.compile(r"\bi\.e\.,?\s", re.IGNORECASE), "that is, "),
    (re.compile(r"\betc\.(?=[\s)])", re.IGNORECASE), "et cetera"),
    (re.compile(r"\bvs\.\s", re.IGNORECASE), "versus "),
    (re.compile(r"\bapprox\.\s", re.IGNORECASE), "approximately "),
]

_MD_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_EMPHASIS = re.compile(r"(\*\*\*|\*\*|\*|___|__|_)(?=\S)(.+?)(?<=\S)\1")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_CODE_FENCE = re.compile(r"^```.*?^```\s*$", re.MULTILINE | re.DOTALL)
_MD_INLINE_CODE = re.compile(r"`([^`]+)`")
# [ \t] not \s: \s matches newlines and would swallow paragraph breaks
_MD_LIST_MARKER = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+", re.MULTILINE)
_MD_BLOCKQUOTE = re.compile(r"^[ \t]*>[ \t]?", re.MULTILINE)
_BARE_URL = re.compile(r"https?://[^\s)\]>,]+")
_ZERO_WIDTH = re.compile(r"[​‌‍﻿]")
_FOOTNOTE_MARKER = re.compile(r"\[\d{1,3}\]")


def _url_to_speakable(match: re.Match) -> str:
    """Replace a bare URL with just its domain, which reads naturally."""
    try:
        host = urlparse(match.group(0)).hostname or ""
    except ValueError:
        return ""
    return host.removeprefix("www.")


def normalize(text: str) -> str:
    """Clean raw text into TTS-friendly prose, preserving paragraph breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ZERO_WIDTH.sub("", text)

    # Code blocks are death in audio — drop fenced blocks entirely.
    text = _MD_CODE_FENCE.sub("", text)

    # Strip markdown decoration, keep the words.
    text = _MD_HEADING.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_EMPHASIS.sub(r"\2", text)
    text = _MD_INLINE_CODE.sub(r"\1", text)
    text = _MD_LIST_MARKER.sub("", text)
    text = _MD_BLOCKQUOTE.sub("", text)

    # Citation/footnote markers like [12] are noise aloud.
    text = _FOOTNOTE_MARKER.sub("", text)

    # Bare URLs -> their domain ("Visit example.com for more").
    text = _BARE_URL.sub(_url_to_speakable, text)

    for pattern, replacement in _ABBREVIATIONS:
        text = pattern.sub(replacement, text)

    # Collapse intra-paragraph whitespace but keep paragraph breaks.
    paragraphs = re.split(r"\n\s*\n", text)
    cleaned = [re.sub(r"\s+", " ", p).strip() for p in paragraphs]
    return "\n\n".join(p for p in cleaned if p)
