import pytest

from textinator.inputs.url import ExtractionError, extract, is_url

ARTICLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>The Care and Feeding of Robots — Example Site</title>
  <meta name="author" content="Jane Doe">
</head>
<body>
  <nav><a href="/">Home</a> <a href="/about">About</a> Subscribe now!</nav>
  <article>
    <h1>The Care and Feeding of Robots</h1>
    <p>Robots need regular maintenance to stay happy. This is the first
    paragraph of a very serious article about robot care, with enough words
    that extraction treats it as real content rather than boilerplate.</p>
    <p>The second paragraph explains that oiling joints weekly prevents the
    dreaded squeak, and that a well-fed robot is a productive robot. Battery
    hygiene matters more than most owners realize.</p>
  </article>
  <footer>Copyright 2026 · Privacy Policy · Cookie Settings</footer>
</body>
</html>"""


def test_is_url():
    assert is_url("https://example.com/a")
    assert is_url("http://example.com")
    assert not is_url("note.txt")
    assert not is_url("-")
    assert not is_url("ftp://example.com")


def test_extract_article_text():
    doc = extract(ARTICLE_HTML, "https://example.com/robots")
    assert "regular maintenance" in doc.text
    assert "oiling joints weekly" in doc.text


def test_extract_drops_boilerplate():
    doc = extract(ARTICLE_HTML, "https://example.com/robots")
    assert "Subscribe now" not in doc.text
    assert "Privacy Policy" not in doc.text


def test_extract_gets_title():
    doc = extract(ARTICLE_HTML, "https://example.com/robots")
    assert "Care and Feeding of Robots" in doc.title


def test_extract_empty_page_raises():
    with pytest.raises(ExtractionError):
        extract("<html><body></body></html>", "https://example.com/empty")


# --- content-type / extension routing ----------------------------------------

def _make_pdf_bytes(tmp_path, text="A PDF fetched from a URL. " * 20):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(72, 150, 523, 700), text, fontsize=12)
    path = tmp_path / "dl.pdf"
    doc.save(str(path))
    doc.close()
    return path.read_bytes()


def _make_epub_bytes(tmp_path):
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("url-epub")
    book.set_title("Fetched Epub")
    book.set_language("en")
    ch = epub.EpubHtml(title="Only Chapter", file_name="c1.xhtml", lang="en")
    ch.content = "<html><body><h1>Only Chapter</h1><p>Epub over HTTP works.</p></body></html>"
    book.add_item(ch)
    book.toc = (epub.Link("c1.xhtml", "Only Chapter", "c1"),)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", ch]
    path = tmp_path / "dl.epub"
    epub.write_epub(str(path), book)
    return path.read_bytes()


def test_url_to_pdf_routed_by_content_type(tmp_path, monkeypatch):
    from textinator.inputs import url as url_mod

    body = _make_pdf_bytes(tmp_path)
    monkeypatch.setattr(url_mod, "_fetch", lambda u: ("application/pdf", body))
    doc = url_mod.load("https://example.com/paper")  # no .pdf extension
    assert "fetched from a URL" in doc.text


def test_url_to_pdf_routed_by_extension_and_magic(tmp_path, monkeypatch):
    from textinator.inputs import url as url_mod

    body = _make_pdf_bytes(tmp_path)
    # wrong content-type: extension + %PDF- magic still win
    monkeypatch.setattr(url_mod, "_fetch", lambda u: ("text/plain", body))
    doc = url_mod.load("https://example.com/files/report.pdf")
    assert "fetched from a URL" in doc.text
    # no PDF metadata title -> derived from first text line (never the
    # random temp-file name)
    assert doc.title.startswith("A PDF fetched from a URL")


def test_url_to_epub_routed(tmp_path, monkeypatch):
    from textinator.inputs import url as url_mod

    body = _make_epub_bytes(tmp_path)
    monkeypatch.setattr(
        url_mod, "_fetch", lambda u: ("application/epub+zip", body)
    )
    doc = url_mod.load("https://example.com/book")
    assert doc.title == "Fetched Epub"
    assert [c.title for c in doc.chapters] == ["Only Chapter"]


def test_url_html_still_extracts_articles(monkeypatch):
    from textinator.inputs import url as url_mod

    monkeypatch.setattr(
        url_mod, "_fetch", lambda u: ("text/html", ARTICLE_HTML.encode())
    )
    doc = url_mod.load("https://example.com/robots")
    assert "regular maintenance" in doc.text
