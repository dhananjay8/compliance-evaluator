"""API request/response payloads."""

from pydantic import BaseModel, Field

from app.schemas.document import DocumentCategory


class CreateAssessmentRequest(BaseModel):
    name: str | None = None
    framework: str = "SOC2"
    customer_id: str | None = None


class CreateAssessmentResponse(BaseModel):
    assessment_id: str
    status: str


class DocumentUploadItem(BaseModel):
    document_id: str
    filename: str
    sha256: str
    size_bytes: int
    document_type: str | None = None


class DocumentUploadResponse(BaseModel):
    assessment_id: str
    uploaded: list[DocumentUploadItem]
    rejected: list[dict] = Field(default_factory=list)


class EvaluateRequest(BaseModel):
    rule_ids: list[str] | None = None  # optional scoping for demos/tests
    framework: str | None = None


class EvaluateResponse(BaseModel):
    assessment_id: str
    status: str
    rules_queued: int | None = None


class StatusResponse(BaseModel):
    assessment_id: str
    status: str
    framework: str
    document_ids: list[str]
    error: str | None = None
    created_at: str | None = None
    completed_at: str | None = None


class ExplicitRoutingItem(BaseModel):
    filename: str
    document_type: DocumentCategory
