"""URL input adapter: fetch a URL and turn whatever it is into a Document.

HTML pages go through trafilatura article extraction. URLs that point at a
PDF or an epub (by content type, extension, or magic bytes) are downloaded
and routed to the matching input adapter — so a link to an arXiv PDF or a
Gutenberg epub works the same as a saved file.
"""

from __future__ import annotations

import tempfile
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from . import Document

_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
_TIMEOUT_SECONDS = 60


class ExtractionError(RuntimeError):
    pass


def is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def _fetch(url: str) -> tuple[str, bytes]:
    """GET the URL; return (content_type, body). Isolated so tests can stub it."""
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get_content_type() or ""
            return content_type, response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise ExtractionError(f"could not fetch {url}: {exc}") from exc


def _kind(url: str, content_type: str, body: bytes) -> str:
    """Classify a response as 'pdf', 'epub', or 'html'."""
    suffix = PurePosixPath(urlparse(url).path).suffix.lower()
    if (
        content_type == "application/pdf"
        or suffix == ".pdf"
        or body[:5] == b"%PDF-"
    ):
        return "pdf"
    if content_type == "application/epub+zip" or (
        suffix == ".epub" and body[:2] == b"PK"
    ):
        return "epub"
    return "html"


def _load_binary(kind: str, url: str, body: bytes) -> Document:
    """Route a downloaded pdf/epub through its file adapter via a temp file."""
    from . import epub as epub_input
    from . import pdf as pdf_input

    adapter = pdf_input if kind == "pdf" else epub_input
    with tempfile.NamedTemporaryFile(suffix=f".{kind}", delete=False) as handle:
        handle.write(body)
        temp_path = Path(handle.name)
    try:
        document = adapter.load(str(temp_path))
    finally:
        temp_path.unlink(missing_ok=True)
    # a temp filename is a useless title; fall back to the URL's basename
    if document.title == temp_path.stem:
        document.title = PurePosixPath(urlparse(url).path).stem or url
    return document


def extract(html: str, url: str = "") -> Document:
    """Extract article text + title from raw HTML (separated for testability)."""
    import trafilatura

    text = trafilatura.extract(
        html,
        url=url or None,
        include_comments=False,
        include_tables=False,  # tables are death in audio
        favor_precision=True,
    )
    if not text or not text.strip():
        raise ExtractionError(
            f"could not extract readable text from {url or 'the page'}"
        )

    metadata = trafilatura.extract_metadata(html, default_url=url or None)
    title = (metadata.title or "").strip() if metadata else ""
    if not title:
        from .paste import derive_title

        title = derive_title(text, fallback=url or "Untitled")
    return Document(title=title, text=text)


def load(url: str) -> Document:
    """Fetch ``url`` and convert it: article, PDF, or epub. Needs network."""
    try:
        content_type, body = _fetch(url)
    except ExtractionError:
        # some sites reject non-browser clients inconsistently; trafilatura
        # has its own fetch heuristics, so give the HTML path a second shot
        import trafilatura

        html = trafilatura.fetch_url(url)
        if html is None:
            raise
        return extract(html, url)

    kind = _kind(url, content_type, body)
    if kind in ("pdf", "epub"):
        return _load_binary(kind, url, body)

    charset = "utf-8"
    return extract(body.decode(charset, errors="replace"), url)
