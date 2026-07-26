"""PDF adapter tests on a synthetic multi-page PDF built with PyMuPDF."""

import pytest

from textinator.inputs.pdf import is_pdf, load

BODY_PAGES = [
    (
        "The Study of Widgets\n"
        "Widgets have fascinated engineers for decades. Their infor-\n"
        "mation density is unmatched in the field of gadgetry.\n"
        "This paragraph continues with enough prose to look like a real "
        "document body for extraction purposes."
    ),
    (
        "Chapter two of the widget study discusses methodology. The team "
        "measured every widget twice, then averaged the results carefully."
    ),
    (
        "In conclusion, widgets remain excellent. Further research is "
        "planned for the coming fiscal year."
    ),
]


@pytest.fixture
def sample_pdf(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    for page_number, body in enumerate(BODY_PAGES, 1):
        page = doc.new_page(width=595, height=842)  # A4
        # repeated header (edge band, same text every page)
        page.insert_text((72, 40), "Widget Quarterly — Vol. 7", fontsize=9)
        # body text well inside the page
        page.insert_textbox(
            pymupdf.Rect(72, 150, 523, 700), body, fontsize=12
        )
        # footer page number
        page.insert_text((290, 820), str(page_number), fontsize=9)
    path = tmp_path / "widgets.pdf"
    doc.set_metadata({"title": "The Study of Widgets"})
    doc.save(str(path))
    doc.close()
    return path


def test_is_pdf():
    assert is_pdf("paper.pdf")
    assert is_pdf("PAPER.PDF")
    assert not is_pdf("paper.epub")


def test_pdf_body_extracted(sample_pdf):
    doc = load(str(sample_pdf))
    assert "fascinated engineers for decades" in doc.text
    assert "measured every widget twice" in doc.text
    assert "widgets remain excellent" in doc.text


def test_pdf_title_from_metadata(sample_pdf):
    assert load(str(sample_pdf)).title == "The Study of Widgets"


def test_pdf_headers_and_page_numbers_dropped(sample_pdf):
    doc = load(str(sample_pdf))
    assert "Widget Quarterly" not in doc.text
    # standalone page numbers gone (digits inside prose survive)
    for line in doc.text.splitlines():
        assert line.strip() not in {"1", "2", "3"}


def test_pdf_dehyphenation(sample_pdf):
    doc = load(str(sample_pdf))
    assert "information density" in doc.text
    assert "infor- mation" not in doc.text and "infor-mation" not in doc.text


def test_pdf_missing_file():
    with pytest.raises(FileNotFoundError):
        load("/nonexistent/nope.pdf")


def test_pdf_empty_raises(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    path = tmp_path / "blank.pdf"
    doc.save(str(path))
    doc.close()
    with pytest.raises(ValueError, match="no extractable text"):
        load(str(path))
