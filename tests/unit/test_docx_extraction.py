"""DOCX extraction unit tests — synthetic docx files built via zipfile."""

import uuid
import zipfile
from pathlib import Path

import pytest

from app.core.errors import DocumentExtractionError, UploadValidationError
from app.documents.chunking.chunker import PolicyChunker
from app.documents.extraction import DocxExtractor, DocumentExtractor
from app.schemas.document import DocumentRef
from app.security.validation import validate_upload

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _doc(path: Path, body_xml: str, parts: dict[str, str] | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{_W}" xmlns:r="{_R}"><w:body>{body_xml}</w:body></w:document>',
        )
        for name, content in (parts or {}).items():
            z.writestr(name, content)
    return path


def _p(text: str, style: str | None = None, bold: bool = False, num: bool = False) -> str:
    ppr = ""
    inner = ""
    if style:
        inner += f'<w:pStyle w:val="{style}"/>'
    if num:
        inner += "<w:numPr/>"
    if inner:
        ppr = f"<w:pPr>{inner}</w:pPr>"
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f"<w:p>{ppr}<w:r>{rpr}<w:t>{text}</w:t></w:r></w:p>"


def _tbl(rows: list[list[str]]) -> str:
    trs = "".join(
        "<w:tr>" + "".join(f"<w:tc>{_p(c)}</w:tc>" for c in row) + "</w:tr>"
        for row in rows
    )
    return f"<w:tbl>{trs}</w:tbl>"


def _ref(tmp_path, filename="policy.docx") -> DocumentRef:
    return DocumentRef(
        document_id=str(uuid.uuid4()),
        assessment_id="a1",
        filename=filename,
        size_bytes=100,
        sha256="x" * 64,
        storage_path=str(tmp_path / filename),
    )


# ---------------------------------------------------------------------------
# styled docx
# ---------------------------------------------------------------------------

def test_styled_docx_splits_on_headings(tmp_path):
    path = _doc(tmp_path / "styled.docx", "".join([
        _p("Corporate Security Policy", "Heading1"),
        _p("1. Purpose", "Heading2"),
        _p("This policy defines security requirements."),
        _p("2. Scope", "Heading2"),
        _p("Applies to all employees."),
        _tbl([["Version", "Owner"], ["1.0", "CISO"]]),
    ]))
    result = DocxExtractor().extract(_ref(tmp_path), path)

    assert result.page_count == 3
    purpose, scope = result.pages[1], result.pages[2]
    assert purpose.headings == ["1. Purpose"]
    assert purpose.metadata["heading_path"] == ["Corporate Security Policy", "1. Purpose"]
    assert "defines security requirements" in purpose.text
    assert scope.metadata["heading_path"] == ["Corporate Security Policy", "2. Scope"]
    # table stays a single atomic block within its section
    assert "Version | Owner" in scope.text and "1.0 | CISO" in scope.text


def test_toc_paragraphs_skipped(tmp_path):
    path = _doc(tmp_path / "toc.docx", "".join([
        _p("Contents", "TOCHeading"),
        _p("1. Purpose\t1", "TOC1"),
        _p("1. Purpose", "Heading1"),
        _p("Real body text."),
    ]))
    result = DocxExtractor().extract(_ref(tmp_path), path)
    assert result.page_count == 1
    assert result.pages[0].headings == ["1. Purpose"]
    assert "Contents" not in result.pages[0].text


# ---------------------------------------------------------------------------
# altChunk docx (embedded HTML)
# ---------------------------------------------------------------------------

def test_altchunk_html_sections(tmp_path):
    html = (
        "<html><body>"
        "<h2>1. Purpose</h2><p>Purpose text here.</p>"
        "<h1>2.&nbsp;&nbsp;Access Policy</h1><p>Access body.</p>"
        "<ul><li>Review quarterly</li></ul>"
        "<table><tr><td>Version</td><td>1.0</td></tr></table>"
        "</body></html>"
    )
    rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId101" Type="x" Target="chunk1.html"/>'
        "</Relationships>"
    )
    path = _doc(
        tmp_path / "alt.docx",
        '<w:altChunk r:id="rId101"/>',
        parts={
            "word/_rels/document.xml.rels": rels,
            "word/chunk1.html": html,
        },
    )
    result = DocxExtractor().extract(_ref(tmp_path), path)

    assert result.page_count == 2
    assert result.pages[0].headings == ["1. Purpose"]
    access = result.pages[1]
    assert access.headings == ["2. Access Policy"]
    assert "• Review quarterly" in access.text
    assert "Version | 1.0" in access.text


# ---------------------------------------------------------------------------
# flat docx (no styles — bold-run heuristic)
# ---------------------------------------------------------------------------

def test_flat_docx_bold_heading_heuristic(tmp_path):
    path = _doc(tmp_path / "flat.docx", "".join([
        _p("Purpose", bold=True),
        _p("The purpose of this policy is to outline network requirements."),
        _p("Scope", bold=True),
        _p("This policy applies to all network controls."),
    ]))
    result = DocxExtractor().extract(_ref(tmp_path), path)

    assert result.page_count == 2
    assert result.pages[0].headings == ["Purpose"]
    assert result.pages[1].headings == ["Scope"]
    assert "outline network requirements" in result.pages[0].text


def test_invalid_docx_raises(tmp_path):
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(DocumentExtractionError):
        DocxExtractor().extract(_ref(tmp_path, "bad.docx"), bad)


# ---------------------------------------------------------------------------
# dispatcher / validation / chunker integration
# ---------------------------------------------------------------------------

def test_dispatcher_routes_docx(tmp_path):
    path = _doc(tmp_path / "d.docx", _p("Purpose", "Heading1") + _p("Body."))
    result = DocumentExtractor().extract(_ref(tmp_path, "d.docx"), path)
    assert result.pages[0].headings == ["Purpose"]


def test_validate_upload_accepts_docx(tmp_path, settings):
    path = _doc(tmp_path / "ok.docx", _p("Hi"))
    assert validate_upload("ok.docx", path.read_bytes(), settings) == "ok.docx"
    with pytest.raises(UploadValidationError):
        validate_upload("evil.docx", b"MZ not a zip", settings)


def test_chunks_respect_sections_and_carry_heading_path(settings, tmp_path):
    path = _doc(tmp_path / "s.docx", "".join([
        _p("Policy", "Heading1"),
        _p("1. Purpose", "Heading2"),
        _p("First section body text."),
        _p("2. Scope", "Heading2"),
        _p("Second section body text."),
    ]))
    extracted = DocxExtractor().extract(_ref(tmp_path), path)
    chunks = PolicyChunker(settings).chunk_document(extracted)

    purpose = [c for c in chunks if c.section == "1. Purpose"]
    scope = [c for c in chunks if c.section == "2. Scope"]
    assert purpose and scope
    assert all("Second section" not in c.text for c in purpose)
    assert purpose[0].metadata["heading_path"] == ["Policy", "1. Purpose"]
    assert purpose[0].metadata["source_format"] == "docx"
