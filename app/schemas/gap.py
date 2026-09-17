"""Gap analysis schema."""

from pydantic import BaseModel, Field


class GapAnalysis(BaseModel):
    rule_id: str
    criterion: str
    gap: str
    missing_requirement: str
    recommendation: str
    expected_evidence: list[str] = Field(default_factory=list)
    severity: str = "MEDIUM"
