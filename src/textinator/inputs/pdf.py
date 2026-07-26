"""PDF input adapter — the swamp. Best-effort prose extraction via PyMuPDF.

Strategy:
- text blocks per page, in reading order (PyMuPDF's ``sort=True`` handles
  simple multi-column layouts);
- headers/footers = short lines whose text repeats on many pages near the
  top/bottom -> dropped, as are standalone page numbers;
- lines broken by hyphenation are rejoined ("infor-\\nmation" -> "information");
- blocks become paragraphs.

PDFs are typography, not structure — this will never be perfect, but it makes
articles and reports listenable.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from . import Document
from .paste import derive_title

_PAGE_NUMBER = re.compile(r"^\s*(?:page\s+)?[\divxlc]+\s*$", re.IGNORECASE)
# "-" at end of line followed by a lowercase continuation -> rejoin the word
_HYPHEN_BREAK = re.compile(r"(\w)-\n([a-z])")
_EDGE_BAND = 0.12  # top/bottom fraction of the page where furniture lives
_MIN_REPEATS = 3  # a line must appear on this many pages to be furniture


def is_pdf(source: str) -> bool:
    return source.lower().endswith(".pdf")


def _normalize_furniture(line: str) -> str:
    """Fingerprint for repeated-line detection: digits vary per page
    ("Chapter 2 · 17"), so mask them out."""
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", line.strip().lower()))


def _collect_blocks(doc) -> list[list[tuple[str, bool]]]:
    """Per page: [(block_text, in_edge_band), ...] in reading order."""
    pages = []
    for page in doc:
        height = page.rect.height or 1
        blocks = []
        for x0, y0, x1, y1, text, _no, block_type in page.get_text(
            "blocks", sort=True
        ):
            if block_type != 0 or not text.strip():  # 0 = text block
                continue
            in_band = (y1 < height * _EDGE_BAND) or (
                y0 > height * (1 - _EDGE_BAND)
            )
            blocks.append((text, in_band))
        pages.append(blocks)
    return pages


def _find_furniture(pages) -> set[str]:
    """Fingerprints of short edge-band lines that recur across pages."""
    counts: Counter[str] = Counter()
    for blocks in pages:
        seen = set()
        for text, in_band in blocks:
            if not in_band:
                continue
            for line in text.splitlines():
                line = line.strip()
                if line and len(line) <= 80:
                    seen.add(_normalize_furniture(line))
        counts.update(seen)
    return {fp for fp, n in counts.items() if n >= _MIN_REPEATS}


def _clean_block(text: str, furniture: set[str], in_band: bool) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _PAGE_NUMBER.match(stripped):
            continue
        if in_band and _normalize_furniture(stripped) in furniture:
            continue
        lines.append(stripped)
    if not lines:
        return ""
    joined = "\n".join(lines)
    joined = _HYPHEN_BREAK.sub(r"\1\2", joined)  # de-hyphenate
    return re.sub(r"\s+", " ", joined).strip()


def load(source: str) -> Document:
    import pymupdf

    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {source}")

    with pymupdf.open(str(path)) as doc:
        meta_title = (doc.metadata or {}).get("title", "").strip()
        pages = _collect_blocks(doc)

    furniture = _find_furniture(pages)
    paragraphs = []
    for blocks in pages:
        for text, in_band in blocks:
            cleaned = _clean_block(text, furniture, in_band)
            if cleaned:
                paragraphs.append(cleaned)

    body = "\n\n".join(paragraphs)
    if not body.strip():
        raise ValueError(
            f"no extractable text in {source} (scanned/image-only PDF?)"
        )
    title = meta_title or derive_title(body, fallback=path.stem)
    return Document(title=title, text=body)
