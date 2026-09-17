"""Atomic compliance rule schema (synthetic SOC2-style dataset)."""

from enum import StrEnum

from pydantic import BaseModel, Field


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ComplianceRule(BaseModel):
    rule_id: str
    framework: str = "SOC2"
    trust_service_category: str = "Security"
    criterion: str  # e.g. "CC6.1"
    sub_attribute: str | None = None
    title: str
    description: str
    requirement: str  # what constitutes sufficient policy evidence
    expected_evidence: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    severity: Severity = Severity.MEDIUM
    version: str = "2022"
    active: bool = True
    document_types: list[str] = Field(default_factory=list)  # optional routing hints

    def retrieval_text(self) -> str:
        """Semantic representation used for evidence retrieval."""
        parts = [self.title, self.description, self.requirement]
        if self.keywords:
            parts.append(" ".join(self.keywords))
        return "\n".join(p for p in parts if p)
