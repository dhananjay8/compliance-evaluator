"""Chunking unit tests — page fidelity, size limits, metadata."""

from app.documents.chunking.chunker import PolicyChunker
from app.schemas.document import (
    ClassifiedDocument,
    DocumentCategory,
    ExtractedDocument,
    ExtractedPage,
)


def _extracted(pages: list[str]) -> ExtractedDocument:
    return ExtractedDocument(
        document_id="d1",
        assessment_id="a1",
        filename="policy.pdf",
        sha256="x" * 64,
        page_count=len(pages),
        pages=[
            ExtractedPage(page_number=i + 1, text=t, headings=[f"Section {i+1}"])
            for i, t in enumerate(pages)
        ],
    )


def _classified() -> ClassifiedDocument:
    return ClassifiedDocument(
        document_id="d1", assessment_id="a1", filename="policy.pdf",
        document_type=DocumentCategory.ACCESS_CONTROL,
        classification_source="explicit",
    )


def test_chunks_respect_size_and_page_boundaries(settings):
    long_text = "Sentence one is here. " * 60  # ~1200 chars > CHUNK_SIZE=400
    chunks = PolicyChunker(settings).chunk_document(_extracted([long_text]), _classified())
    assert len(chunks) > 1
    assert all(c.page_number == 1 for c in chunks)
    assert all(len(c.text) <= settings.chunk_size for c in chunks)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_chunks_never_merge_across_pages(settings):
    doc = _extracted(["Page one content.", "Page two content."])
    chunks = PolicyChunker(settings).chunk_document(doc, _classified())
    pages = {c.page_number for c in chunks}
    assert pages == {1, 2}
    assert all(c.page_end == c.page_number for c in chunks)


def test_metadata_preserved(settings):
    doc = _extracted(["Privileged access is reviewed every 90 days."])
    chunks = PolicyChunker(settings).chunk_document(doc, _classified())
    c = chunks[0]
    assert c.document_id == "d1"
    assert c.assessment_id == "a1"
    assert c.filename == "policy.pdf"
    assert c.document_type == DocumentCategory.ACCESS_CONTROL
    assert c.section == "Section 1"
    assert len(c.content_hash) == 64


def test_empty_pages_skipped(settings):
    doc = _extracted(["Real content here."])
    doc.pages.append(ExtractedPage(page_number=2, text="", empty=True))
    chunks = PolicyChunker(settings).chunk_document(doc, _classified())
    assert all(c.page_number == 1 for c in chunks)


def test_paragraph_packing(settings):
    paras = "\n\n".join(["Short paragraph %d." % i for i in range(10)])
    chunks = PolicyChunker(settings).chunk_document(_extracted([paras]), _classified())
    assert len(chunks) >= 1
    assert all(len(c.text) <= settings.chunk_size for c in chunks)
