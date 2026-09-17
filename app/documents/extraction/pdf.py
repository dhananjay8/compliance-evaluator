"""PyMuPDF-based PDF extraction preserving page/section/offset metadata.

Design notes:
- Page-level extraction keeps citation provenance exact.
- Headings are detected via font-size outliers in the page's text spans.
- Whitespace is normalized per-line only (never across page boundaries) so
  verbatim citation checks remain reliable.
- Extraction failures are per-page warnings, not fatal, unless the document
  yields zero text overall.
"""

import re
from pathlib import Path

import pymupdf as fitz  # PyMuPDF

from app.core.errors import DocumentExtractionError
from app.core.logging import get_logger
from app.schemas.document import DocumentRef, ExtractedDocument, ExtractedPage

log = get_logger(__name__)

_WS_RUN = re.compile(r"[ \t]+")
_HEADING_MIN_FONT_RATIO = 1.15  # span must be ≥15% larger than body median
_MAX_HEADING_LEN = 120


def _normalize_page_text(text: str) -> str:
    """Collapse intra-line whitespace, drop blank-line runs, keep line breaks."""
    lines = [_WS_RUN.sub(" ", line).strip() for line in text.splitlines()]
    out: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if not blank:
                out.append("")
            blank = True
            continue
        blank = False
        out.append(line)
    return "\n".join(out).strip()


def _detect_headings(page: fitz.Page, body_median: float) -> list[str]:
    """Heuristic heading detection from font sizes in the page dict."""
    headings: list[str] = []
    try:
        data = page.get_text("dict")
        for block in data.get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                text = "".join(s.get("text", "") for s in spans).strip()
                if not text or len(text) > _MAX_HEADING_LEN:
                    continue
                max_size = max(s.get("size", 0.0) for s in spans)
                is_bold = any("bold" in (s.get("font", "").lower()) for s in spans)
                if max_size >= body_median * _HEADING_MIN_FONT_RATIO or (
                    is_bold and max_size >= body_median
                ):
                    headings.append(text)
    except Exception:  # noqa: BLE001 — heading detection is best-effort
        pass
    # dedupe preserving order
    seen: set[str] = set()
    unique = []
    for h in headings:
        if h not in seen:
            seen.add(h)
            unique.append(h)
    return unique[:10]


def _body_median_size(doc: fitz.Document) -> float:
    sizes: list[float] = []
    for page in doc:
        try:
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("text", "").strip():
                            sizes.append(span.get("size", 0.0))
        except Exception:  # noqa: BLE001
            continue
        if len(sizes) > 400:
            break
    if not sizes:
        return 11.0
    sizes.sort()
    return sizes[len(sizes) // 2]


class PdfExtractor:
    """Extracts page-level text + headings from a PDF on disk."""

    def extract(self, doc_ref: DocumentRef, path: Path) -> ExtractedDocument:
        warnings: list[str] = []
        try:
            pdf = fitz.open(path)
        except Exception as exc:  # noqa: BLE001
            raise DocumentExtractionError(
                f"Cannot open PDF {doc_ref.filename}: {exc}",
                detail={"document_id": doc_ref.document_id},
            ) from exc

        try:
            if pdf.is_encrypted:
                try:
                    if not pdf.authenticate(""):
                        raise DocumentExtractionError(
                            f"PDF {doc_ref.filename} is password-protected",
                            detail={"document_id": doc_ref.document_id},
                        )
                except DocumentExtractionError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    raise DocumentExtractionError(
                        f"PDF {doc_ref.filename} is encrypted: {exc}",
                        detail={"document_id": doc_ref.document_id},
                    ) from exc

            median = _body_median_size(pdf)
            pages: list[ExtractedPage] = []
            offset = 0
            for i, page in enumerate(pdf):
                page_no = i + 1
                try:
                    raw = page.get_text("text")
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"page {page_no}: extraction failed ({exc})")
                    pages.append(ExtractedPage(
                        page_number=page_no, text="", empty=True,
                        char_offset_start=offset, char_offset_end=offset,
                    ))
                    continue
                text = _normalize_page_text(raw)
                headings = _detect_headings(page, median) if text else []
                pages.append(ExtractedPage(
                    page_number=page_no,
                    text=text,
                    char_offset_start=offset,
                    char_offset_end=offset + len(raw),
                    headings=headings,
                    empty=not bool(text),
                ))
                offset += len(raw) + 1

            if not any(p.text for p in pages):
                raise DocumentExtractionError(
                    f"PDF {doc_ref.filename} contains no extractable text "
                    "(scanned/image PDFs require the OCR extension point)",
                    detail={"document_id": doc_ref.document_id},
                )

            return ExtractedDocument(
                document_id=doc_ref.document_id,
                assessment_id=doc_ref.assessment_id,
                filename=doc_ref.filename,
                sha256=doc_ref.sha256,
                page_count=len(pages),
                pages=pages,
                extraction_warnings=warnings,
            )
        finally:
            pdf.close()
