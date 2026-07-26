import pytest

from textinator.inputs.paste import derive_title, load


def test_derive_title_first_line():
    assert derive_title("My Great Note\n\nBody text.") == "My Great Note"


def test_derive_title_strips_markdown_heading():
    assert derive_title("## A Heading\nbody") == "A Heading"


def test_derive_title_skips_blank_lines():
    assert derive_title("\n\n  \nActual title") == "Actual title"


def test_derive_title_truncates_long_lines():
    long_line = "word " * 40
    title = derive_title(long_line)
    assert len(title) <= 71
    assert title.endswith("…")


def test_derive_title_fallback():
    assert derive_title("", fallback="my-file") == "my-file"


def test_load_file(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("Title Line\n\nSome body.")
    doc = load(str(f))
    assert doc.title == "Title Line"
    assert "Some body." in doc.text


def test_load_missing_file():
    with pytest.raises(FileNotFoundError):
        load("/nonexistent/nope.txt")


def test_load_empty_file(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("   \n")
    with pytest.raises(ValueError):
        load(str(f))


def test_load_stdin(monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("Stdin title\n\nbody"))
    doc = load(None)
    assert doc.title == "Stdin title"
