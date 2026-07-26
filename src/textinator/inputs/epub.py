"""epub input adapter: spine documents in reading order, chapters from the TOC.

Chapter titles resolve in priority order: TOC entry for the file -> first
heading in the file -> "Section N". Nav/TOC documents are skipped so the
table of contents is never read aloud.
"""

from __future__ import annotations

from pathlib import Path

from . import Chapter, Document
from .htmltext import html_to_text


def is_epub(source: str) -> bool:
    return source.lower().endswith(".epub")


def _flatten_toc(entries, mapping: dict[str, str]) -> None:
    """TOC is a nested mix of Link and (Section, [children]) — flatten to
    {href-without-fragment: title}, keeping the first title seen per file."""
    from ebooklib import epub as ebooklib_epub

    for entry in entries:
        if isinstance(entry, tuple) and len(entry) == 2:
            section, children = entry
            href = getattr(section, "href", "") or ""
            title = getattr(section, "title", "") or ""
            if href and title:
                mapping.setdefault(href.split("#")[0], title)
            _flatten_toc(children, mapping)
        elif isinstance(entry, ebooklib_epub.Link):
            mapping.setdefault(entry.href.split("#")[0], entry.title or "")


def load(source: str) -> Document:
    import ebooklib
    from ebooklib import epub as ebooklib_epub

    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {source}")
    book = ebooklib_epub.read_epub(str(path), options={"ignore_ncx": False})

    titles = book.get_metadata("DC", "title")
    book_title = titles[0][0].strip() if titles else path.stem

    toc_titles: dict[str, str] = {}
    _flatten_toc(book.toc, toc_titles)

    chapters: list[Chapter] = []
    for item_id, _linear in book.spine:
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        if isinstance(item, ebooklib_epub.EpubNav) or "nav" in (
            item.get_name().lower().rsplit("/", 1)[-1].split(".")[0],
        ):
            continue
        text, first_heading = html_to_text(
            item.get_content().decode("utf-8", errors="replace")
        )
        if not text.strip():
            continue
        title = (
            toc_titles.get(item.get_name(), "")
            or first_heading
            or f"Section {len(chapters) + 1}"
        )
        chapters.append(Chapter(title=title, text=text))

    if not chapters:
        raise ValueError(f"no readable content found in {source}")
    return Document(title=book_title, chapters=chapters)
