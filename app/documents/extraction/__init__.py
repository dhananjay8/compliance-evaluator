"""Document extraction — dispatches on file extension."""

from pathlib import Path

from app.core.errors import DocumentExtractionError
from app.documents.extraction.docx import DocxExtractor
from app.documents.extraction.pdf import PdfExtractor
from app.schemas.document import DocumentRef, ExtractedDocument

__all__ = ["DocumentExtractor", "DocxExtractor", "PdfExtractor"]


class DocumentExtractor:
    """Routes to the format-specific extractor (.pdf, .docx)."""

    def __init__(self) -> None:
        self._pdf = PdfExtractor()
        self._docx = DocxExtractor()

    def extract(self, doc_ref: DocumentRef, path: Path) -> ExtractedDocument:
        ext = path.suffix.lower() or Path(doc_ref.filename).suffix.lower()
        if ext == ".docx":
            return self._docx.extract(doc_ref, path)
        if ext == ".pdf":
            return self._pdf.extract(doc_ref, path)
        raise DocumentExtractionError(
            f"Unsupported document type {ext!r} for {doc_ref.filename}",
            detail={"document_id": doc_ref.document_id},
        )
