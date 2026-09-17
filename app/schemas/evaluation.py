"""LLM evaluation contract — strict structured output."""

from enum import StrEnum

from pydantic import BaseModel, Field


class Verdict(StrEnum):
    COMPLIANT = "COMPLIANT"
    NOT_COMPLIANT = "NOT_COMPLIANT"


class EvidenceStatus(StrEnum):
    EXPLICIT = "EXPLICIT"          # evidence explicitly satisfies the requirement
    PARTIAL = "PARTIAL"            # related but insufficient/ambiguous evidence
    ABSENT = "ABSENT"              # no relevant evidence in the corpus
    CONTRADICTORY = "CONTRADICTORY"  # evidence contradicts the requirement


class Citation(BaseModel):
    filename: str
    page_number: int
    section: str | None = None
    quote: str  # verbatim substring of the source chunk text
    chunk_id: str | None = None


class ComplianceEvaluation(BaseModel):
    """Structured output contract for the evaluator LLM."""

    rule_id: str
    verdict: Verdict
    evidence_status: EvidenceStatus
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    evidence: list[str] = Field(default_factory=list)  # short verbatim quotes relied upon
    missing_requirements: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    recommended_gap: str | None = None

    # populated by code after validation (not by the LLM)
    validated: bool = Field(default=False, exclude=True)
    validation_notes: list[str] = Field(default_factory=list, exclude=True)
