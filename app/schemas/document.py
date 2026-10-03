"""Document, page, and chunk schemas."""

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentCategory(StrEnum):
    ACCESS_CONTROL = "ACCESS_CONTROL"
    INFORMATION_SECURITY = "INFORMATION_SECURITY"
    INCIDENT_RESPONSE = "INCIDENT_RESPONSE"
    CHANGE_MANAGEMENT = "CHANGE_MANAGEMENT"
    BUSINESS_CONTINUITY = "BUSINESS_CONTINUITY"
    DATA_PROTECTION = "DATA_PROTECTION"
    VENDOR_MANAGEMENT = "VENDOR_MANAGEMENT"
    RISK_MANAGEMENT = "RISK_MANAGEMENT"
    SECURITY_AWARENESS = "SECURITY_AWARENESS"
    HUMAN_RESOURCES = "HUMAN_RESOURCES"
    PHYSICAL_SECURITY = "PHYSICAL_SECURITY"
    ASSET_MANAGEMENT = "ASSET_MANAGEMENT"
    OTHER = "OTHER"


class DocumentRef(BaseModel):
    """A registered upload. Raw bytes live on disk; only metadata flows through state."""

    document_id: str
    assessment_id: str
    filename: str
    content_type: str = "application/pdf"
    size_bytes: int
    sha256: str
    storage_path: str
    document_type: DocumentCategory | None = None  # explicit routing (Mode A)
    uploaded_at: datetime = Field(default_factory=utcnow)


class ExtractedPage(BaseModel):
    page_number: int  # 1-based (for .docx: logical section index)
    text: str
    char_offset_start: int = 0
    char_offset_end: int = 0
    headings: list[str] = Field(default_factory=list)
    empty: bool = False
    metadata: dict = Field(default_factory=dict)  # e.g. heading_path for .docx


class ExtractedDocument(BaseModel):
    document_id: str
    assessment_id: str
    filename: str
    sha256: str
    page_count: int
    pages: list[ExtractedPage]
    extraction_warnings: list[str] = Field(default_factory=list)


class ClassifiedDocument(BaseModel):
    document_id: str
    assessment_id: str
    filename: str
    document_type: DocumentCategory
    classification_source: str  # "explicit" | "heuristic" | "llm"
    confidence: float = 1.0
    rationale: str | None = None


class PolicyChunk(BaseModel):
    chunk_id: str
    document_id: str
    assessment_id: str
    filename: str
    document_type: DocumentCategory = DocumentCategory.OTHER
    page_number: int
    page_end: int | None = None  # chunks may span a page boundary
    section: str | None = None
    chunk_index: int  # per-document sequential
    text: str
    content_hash: str
    embedding_model: str | None = None
    metadata: dict = Field(default_factory=dict)
