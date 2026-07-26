"""Input adapters: turn a source (file, stdin, URL, epub, PDF) into a Document."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Chapter:
    """One structural unit of a Document (epub chapter, heading section...)."""

    title: str
    text: str


@dataclass
class Document:
    """Normalized-input unit: a title and the raw text to be spoken.

    When ``chapters`` is non-empty it is the authoritative content (``text``
    may be empty) and the pipeline can emit real chapter markers.
    """

    title: str
    text: str = ""
    chapters: list[Chapter] = field(default_factory=list)
