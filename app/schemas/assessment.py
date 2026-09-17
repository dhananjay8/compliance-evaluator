"""Assessment lifecycle + result schemas."""

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.evaluation import Citation, EvidenceStatus, Verdict
from app.schemas.gap import GapAnalysis


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssessmentStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class Assessment(BaseModel):
    assessment_id: str
    name: str | None = None
    framework: str = "SOC2"
    customer_id: str | None = None
    status: AssessmentStatus = AssessmentStatus.PENDING
    document_ids: list[str] = Field(default_factory=list)
    rule_ids: list[str] | None = None  # optional scoping
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None


class CriterionResult(BaseModel):
    rule_id: str
    criterion: str
    title: str
    verdict: Verdict
    evidence_status: EvidenceStatus
    confidence: float
    reasoning: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    gap_analysis: GapAnalysis | None = None


class AssessmentSummary(BaseModel):
    total: int = 0
    compliant: int = 0
    not_compliant: int = 0
    compliance_percentage: float = 0.0
    by_evidence_status: dict[str, int] = Field(default_factory=dict)


class AssessmentResultsResponse(BaseModel):
    assessment_id: str
    framework: str
    status: AssessmentStatus
    summary: AssessmentSummary
    results: list[CriterionResult] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
