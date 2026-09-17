"""Aggregation + schema validation tests."""

import pytest
from pydantic import ValidationError

from app.compliance.aggregation import aggregate_results, build_criterion_result
from app.schemas.assessment import CriterionResult
from app.schemas.evaluation import (
    ComplianceEvaluation,
    EvidenceStatus,
    Verdict,
)
from app.schemas.gap import GapAnalysis
from app.schemas.rule import ComplianceRule


def _rule(rid="R1") -> ComplianceRule:
    return ComplianceRule(
        rule_id=rid, criterion="CC6.1", title="t", description="d",
        requirement="req",
    )


def _eval(rid="R1", verdict=Verdict.COMPLIANT, status=EvidenceStatus.EXPLICIT):
    return ComplianceEvaluation(
        rule_id=rid, verdict=verdict, evidence_status=status,
        confidence=0.9, reasoning="r",
    )


def test_summary_counts():
    results = [
        build_criterion_result(_rule("R1"), _eval("R1"), None),
        build_criterion_result(_rule("R2"), _eval("R2", Verdict.NOT_COMPLIANT, EvidenceStatus.ABSENT),
                               GapAnalysis(rule_id="R2", criterion="CC6.1", gap="g",
                                           missing_requirement="m", recommendation="r")),
    ]
    summary = aggregate_results(results)
    assert summary.total == 2
    assert summary.compliant == 1
    assert summary.not_compliant == 1
    assert summary.compliance_percentage == 50.0
    assert summary.by_evidence_status == {"EXPLICIT": 1, "ABSENT": 1}


def test_gap_only_attached_to_not_compliant():
    gap = GapAnalysis(rule_id="R1", criterion="CC6.1", gap="g",
                      missing_requirement="m", recommendation="r")
    result = build_criterion_result(_rule(), _eval(), gap)
    assert result.gap_analysis is None  # COMPLIANT → no gap


def test_confidence_bounds_enforced():
    with pytest.raises(ValidationError):
        ComplianceEvaluation(
            rule_id="R", verdict=Verdict.COMPLIANT,
            evidence_status=EvidenceStatus.EXPLICIT,
            confidence=1.5, reasoning="x",
        )


def test_invalid_verdict_rejected():
    with pytest.raises(ValidationError):
        ComplianceEvaluation(
            rule_id="R", verdict="MAYBE",  # type: ignore[arg-type]
            evidence_status=EvidenceStatus.EXPLICIT,
            confidence=0.5, reasoning="x",
        )
