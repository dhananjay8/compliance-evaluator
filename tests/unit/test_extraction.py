"""PDF extraction unit tests — real PDFs via fpdf2, real PyMuPDF parsing."""

import uuid

import pytest

from app.core.errors import DocumentExtractionError
from app.documents.extraction.pdf import PdfExtractor
from app.schemas.document import DocumentRef


def _ref(tmp_path, filename="policy.pdf") -> DocumentRef:
    return DocumentRef(
        document_id=str(uuid.uuid4()),
        assessment_id="a1",
        filename=filename,
        size_bytes=100,
        sha256="x" * 64,
        storage_path=str(tmp_path / filename),
    )


def test_extracts_pages_and_headings(pdf_factory, tmp_path):
    path = pdf_factory(
        "policy.pdf",
        "Access Control Policy",
        [("1. Purpose", ["This policy defines access control."]),
         ("2. Reviews", ["Access is reviewed every 90 days."])],
    )
    ref = _ref(tmp_path)
    result = PdfExtractor().extract(ref, path)

    assert result.page_count >= 1
    assert result.pages[0].page_number == 1
    assert "access control" in result.pages[0].text.lower()
    assert result.pages[0].headings  # bold headings detected
    assert any("Purpose" in h or "Access Control Policy" in h for h in result.pages[0].headings)


def test_preserves_verbatim_text_for_citations(pdf_factory, tmp_path):
    quote = "Privileged user access is reviewed every 90 days by the security team."
    path = pdf_factory("p.pdf", "T", [("S", [quote])])
    result = PdfExtractor().extract(_ref(tmp_path), path)
    assert quote in result.pages[0].text


def test_invalid_pdf_raises(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    with pytest.raises(DocumentExtractionError):
        PdfExtractor().extract(_ref(tmp_path, "bad.pdf"), bad)


def test_empty_pdf_raises(pdf_factory, tmp_path):
    path = pdf_factory("empty.pdf", "", [])
    with pytest.raises(DocumentExtractionError, match="no extractable text"):
        PdfExtractor().extract(_ref(tmp_path, "empty.pdf"), path)
