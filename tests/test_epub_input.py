import json
import subprocess

import pytest

from textinator.inputs.epub import is_epub, load
from textinator.inputs.htmltext import html_to_text


# --- html_to_text ------------------------------------------------------------

def test_html_blocks_become_paragraphs():
    text, _ = html_to_text(
        "<html><body><p>First para.</p><p>Second para.</p></body></html>"
    )
    assert text == "First para.\n\nSecond para."


def test_html_first_heading_captured():
    text, heading = html_to_text(
        "<body><h1>Chapter One</h1><p>It begins.</p></body>"
    )
    assert heading == "Chapter One"
    assert text == "Chapter One\n\nIt begins."


def test_html_skips_script_style_sup():
    text, _ = html_to_text(
        "<body><style>p{}</style><p>Real<sup><a>3</a></sup> text.</p>"
        "<script>var x=1;</script></body>"
    )
    assert text == "Real text."


def test_html_entities_decoded():
    text, _ = html_to_text("<p>Fish &amp; chips &mdash; lovely</p>")
    assert text == "Fish & chips — lovely"


# --- epub adapter ------------------------------------------------------------

@pytest.fixture
def sample_epub(tmp_path):
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("test-book-id")
    book.set_title("The Test Book")
    book.set_language("en")
    book.add_author("Testy Author")

    chapters = []
    bodies = {
        "Chapter One": "It was a dark and stormy test suite. " * 8,
        "Chapter Two": "The assertions passed, every one of them. " * 8,
        "Chapter Three": "In the end the coverage was total. " * 8,
    }
    for i, (title, body) in enumerate(bodies.items(), 1):
        ch = epub.EpubHtml(
            title=title, file_name=f"chap{i}.xhtml", lang="en"
        )
        ch.content = f"<html><body><h1>{title}</h1><p>{body}</p></body></html>"
        book.add_item(ch)
        chapters.append(ch)

    book.toc = tuple(
        epub.Link(f"chap{i}.xhtml", title, f"ch{i}")
        for i, title in enumerate(bodies, 1)
    )
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *chapters]

    path = tmp_path / "test-book.epub"
    epub.write_epub(str(path), book)
    return path


def test_is_epub():
    assert is_epub("book.epub")
    assert is_epub("BOOK.EPUB")
    assert not is_epub("book.txt")
    assert not is_epub("https://example.com/a")


def test_epub_title_and_chapters(sample_epub):
    doc = load(str(sample_epub))
    assert doc.title == "The Test Book"
    assert [c.title for c in doc.chapters] == [
        "Chapter One", "Chapter Two", "Chapter Three",
    ]
    assert "dark and stormy" in doc.chapters[0].text


def test_epub_nav_not_read_aloud(sample_epub):
    doc = load(str(sample_epub))
    combined = " ".join(c.text for c in doc.chapters)
    # the nav doc lists all chapter titles as links; if it leaked in, the
    # first chapter's text would contain the others' titles
    assert "Chapter Two" not in doc.chapters[0].text
    assert combined.count("Chapter Two") == 1  # its own heading only


def test_epub_missing_file():
    with pytest.raises(FileNotFoundError):
        load("/nonexistent/book.epub")


# --- m4b chapters end to end (dummy backend, real ffmpeg) ---------------------

def test_epub_to_m4b_with_chapter_marks(sample_epub, dummy_backend, tmp_path):
    from textinator.inputs.epub import load as load_epub
    from textinator.pipeline import run

    doc = load_epub(str(sample_epub))
    result = run(
        doc,
        dummy_backend,
        feed_dir=tmp_path / "feed",
        cache_dir=tmp_path / "cache",
        episode_format="m4b",
    )
    assert result.episode_path.suffix == ".m4b"

    probe = json.loads(
        subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_chapters", str(result.episode_path),
            ],
            capture_output=True, text=True, check=True,
        ).stdout
    )
    titles = [c["tags"]["title"] for c in probe["chapters"]]
    assert titles == ["Chapter One", "Chapter Two", "Chapter Three"]
    # chapters are contiguous from zero
    assert float(probe["chapters"][0]["start_time"]) == 0.0
    for prev, cur in zip(probe["chapters"], probe["chapters"][1:]):
        assert prev["end_time"] == cur["start_time"]
    # enclosure mime in the feed is audio/mp4
    assert result.episode.mime_type == "audio/mp4"
