from textinator.normalize import normalize


def test_collapses_whitespace_but_keeps_paragraphs():
    text = "One   sentence\nhere.\n\n\nSecond    paragraph."
    assert normalize(text) == "One sentence here.\n\nSecond paragraph."


def test_strips_markdown_decoration():
    text = "# Title\n\nSome **bold** and *italic* and `code` and [a link](https://x.com/y)."
    assert normalize(text) == "Title\n\nSome bold and italic and code and a link."


def test_drops_fenced_code_blocks():
    text = "Before.\n\n```python\nprint('hi')\n```\n\nAfter."
    assert normalize(text) == "Before.\n\nAfter."


def test_bare_url_becomes_domain():
    assert normalize("Visit https://www.example.com/deep/path for more.") == (
        "Visit example.com for more."
    )


def test_expands_abbreviations():
    assert normalize("Fruit, e.g. apples.") == "Fruit, for example, apples."
    assert normalize("Tools, i.e. hammers.") == "Tools, that is, hammers."
    assert normalize("Cats vs. dogs") == "Cats versus dogs"


def test_removes_footnote_markers():
    assert normalize("A claim.[12] More text.") == "A claim. More text."


def test_strips_list_markers_and_blockquotes():
    text = "- first item\n- second item\n\n> quoted wisdom"
    assert normalize(text) == "first item second item\n\nquoted wisdom"


def test_windows_newlines():
    assert normalize("a\r\nb\r\n\r\nc") == "a b\n\nc"


def test_empty_input():
    assert normalize("   \n\n  ") == ""
