"""Minimal HTML -> plain text for epub content documents.

epub XHTML is already clean (no ads/nav soup), so a small stdlib extractor
gives us full control: block elements become paragraph breaks, script/style
vanish, <sup> (footnote refs) is skipped, and the first heading is captured
as a chapter-title candidate.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p", "div", "section", "article", "aside", "header", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "ul", "ol", "blockquote", "figure", "figcaption",
    "table", "tr", "pre", "hr",
}
_SKIP_TAGS = {"script", "style", "head", "title", "sup"}
_HEADING_TAGS = {"h1", "h2", "h3"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[list[str]] = [[]]
        self._skip_depth = 0
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []
        self.first_heading = ""

    def _break(self) -> None:
        if self.blocks[-1]:
            self.blocks.append([])

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "br":
            self.blocks[-1].append(" ")
        elif tag in _BLOCK_TAGS:
            self._break()
            if tag in _HEADING_TAGS and not self.first_heading:
                self._heading_tag = tag

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._break()
            if tag == self._heading_tag:
                heading = re.sub(r"\s+", " ", "".join(self._heading_parts)).strip()
                if heading:
                    self.first_heading = heading
                self._heading_tag = None

    def handle_data(self, data):
        if self._skip_depth:
            return
        self.blocks[-1].append(data)
        if self._heading_tag:
            self._heading_parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """Return (text, first_heading) from an HTML/XHTML document."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    paragraphs = []
    for block in parser.blocks:
        paragraph = re.sub(r"\s+", " ", "".join(block)).strip()
        if paragraph:
            paragraphs.append(paragraph)
    return "\n\n".join(paragraphs), parser.first_heading
