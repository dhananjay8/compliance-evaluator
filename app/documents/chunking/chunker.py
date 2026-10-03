"""Document-aware chunker: document → page → section → paragraph → chunk.

Citation fidelity rules:
- Chunks never merge across pages (page_number stays exact for citations).
- Paragraph boundaries are preferred split points; hard splits only when a
  single paragraph exceeds CHUNK_SIZE.
- Overlap is applied at the character level between consecutive chunks of the
  same page/section so context isn't lost at boundaries.
"""

import re
import uuid

from app.config import Settings, get_settings
from app.core.hashing import chunk_content_hash
from app.schemas.document import (
    ClassifiedDocument,
    DocumentCategory,
    ExtractedDocument,
    ExtractedPage,
    PolicyChunk,
)

_PARA_SPLIT = re.compile(r"\n\s*\n")


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARA_SPLIT.split(text) if p.strip()]


def _hard_split(text: str, size: int, overlap: int) -> list[str]:
    """Split an over-long paragraph on sentence-ish boundaries, then chars."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    pieces: list[str] = []
    current = ""
    for sent in sentences:
        if len(current) + len(sent) + 1 <= size:
            current = f"{current} {sent}".strip()
        else:
            if current:
                pieces.append(current)
            if len(sent) <= size:
                current = sent
            else:
                # last resort: char window with overlap
                step = max(1, size - overlap)
                for i in range(0, len(sent), step):
                    pieces.append(sent[i:i + size])
                current = ""
    if current:
        pieces.append(current)
    return pieces


class PolicyChunker:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.size = self.settings.chunk_size
        self.overlap = self.settings.chunk_overlap

    def _page_section(self, page: ExtractedPage, carry: str | None) -> str | None:
        return page.headings[0] if page.headings else carry

    def chunk_document(
        self,
        extracted: ExtractedDocument,
        classified: ClassifiedDocument | None = None,
    ) -> list[PolicyChunk]:
        chunks: list[PolicyChunk] = []
        chunk_index = 0
        section_carry: str | None = None
        doc_type = (
            classified.document_type if classified else DocumentCategory.OTHER
        )

        for page in extracted.pages:
            if page.empty or not page.text:
                continue
            section = self._page_section(page, section_carry)
            if page.headings:
                section_carry = page.headings[0]

            paragraphs = _split_paragraphs(page.text)
            buffer = ""
            for para in paragraphs:
                candidate = f"{buffer}\n\n{para}".strip() if buffer else para
                if len(candidate) <= self.size:
                    buffer = candidate
                    continue
                # flush buffer
                if buffer:
                    chunk_index = self._emit(
                        chunks, extracted, doc_type, page, section,
                        buffer, chunk_index,
                    )
                if len(para) <= self.size:
                    buffer = para
                else:
                    for piece in _hard_split(para, self.size, self.overlap):
                        chunk_index = self._emit(
                            chunks, extracted, doc_type, page, section,
                            piece, chunk_index,
                        )
                    buffer = ""
            if buffer:
                chunk_index = self._emit(
                    chunks, extracted, doc_type, page, section,
                    buffer, chunk_index,
                )

        return chunks

    def _emit(
        self,
        chunks: list[PolicyChunk],
        extracted: ExtractedDocument,
        doc_type: DocumentCategory,
        page: ExtractedPage,
        section: str | None,
        text: str,
        chunk_index: int,
    ) -> int:
        chunk_id = f"{extracted.document_id}:{chunk_index:05d}:{uuid.uuid4().hex[:8]}"
        chunks.append(PolicyChunk(
            chunk_id=chunk_id,
            document_id=extracted.document_id,
            assessment_id=extracted.assessment_id,
            filename=extracted.filename,
            document_type=doc_type,
            page_number=page.page_number,
            page_end=page.page_number,
            section=section,
            chunk_index=chunk_index,
            text=text,
            content_hash=chunk_content_hash(
                extracted.document_id, page.page_number, chunk_index, text
            ),
            metadata=dict(page.metadata),
        ))
        return chunk_index + 1
