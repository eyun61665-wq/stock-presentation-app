import pytest

from pdf_extractor import PDFExtractionError, PL_KEYWORDS, extract_pdf_candidates


def test_pdf_text_extraction_returns_page_context():
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Sales 1,234")
    content = document.tobytes()
    document.close()
    result = extract_pdf_candidates(content, ("Sales",))
    assert result["image_pdf"] is False
    assert result["candidates"][0]["ページ"] == 1
    assert result["candidates"][0]["候補値"] == 1234


def test_image_pdf_and_broken_pdf_are_handled():
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    document.new_page()
    content = document.tobytes()
    document.close()
    assert extract_pdf_candidates(content, PL_KEYWORDS)["image_pdf"] is True
    with pytest.raises(PDFExtractionError):
        extract_pdf_candidates(b"not a pdf", PL_KEYWORDS)
