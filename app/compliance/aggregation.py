"""Aggregate per-rule evaluations into criterion results + summary."""

from app.schemas.assessment import AssessmentSummary, CriterionResult
from app.schemas.evaluation import ComplianceEvaluation, Verdict
from app.schemas.gap import GapAnalysis
from app.schemas.rule import ComplianceRule


def build_criterion_result(
    rule: ComplianceRule,
    evaluation: ComplianceEvaluation,
    gap: GapAnalysis | None,
) -> CriterionResult:
    return CriterionResult(
        rule_id=rule.rule_id,
        criterion=rule.criterion,
        title=rule.title,
        verdict=evaluation.verdict,
        evidence_status=evaluation.evidence_status,
        confidence=evaluation.confidence,
        reasoning=evaluation.reasoning,
        citations=evaluation.citations,
        missing_requirements=evaluation.missing_requirements,
        contradictions=evaluation.contradictions,
        gap_analysis=gap if evaluation.verdict == Verdict.NOT_COMPLIANT else None,
    )


def aggregate_results(results: list[CriterionResult]) -> AssessmentSummary:
    total = len(results)
    compliant = sum(1 for r in results if r.verdict == Verdict.COMPLIANT)
    by_status: dict[str, int] = {}
    for r in results:
        key = r.evidence_status.value
        by_status[key] = by_status.get(key, 0) + 1
    return AssessmentSummary(
        total=total,
        compliant=compliant,
        not_compliant=total - compliant,
        compliance_percentage=round(100.0 * compliant / total, 1) if total else 0.0,
        by_evidence_status=by_status,
    )
