"""Aggregation, gap analysis, final report, and persistence nodes."""

import time
from datetime import datetime, timezone

from app.compliance.aggregation import aggregate_results, build_criterion_result
from app.core.logging import get_logger
from app.graph.context import GraphContext
from app.graph.state import ComplianceState
from app.schemas.assessment import AssessmentStatus
from app.schemas.evaluation import Verdict
from app.schemas.workflow import WorkflowError

log = get_logger(__name__)


def make_aggregate_criterion_results(ctx: GraphContext):
    async def aggregate_criterion_results(state: ComplianceState) -> dict:
        started = time.perf_counter()
        rules = {r.rule_id: r for r in state.get("rules", [])}
        evaluations = {e.rule_id: e for e in state.get("evaluations", [])}
        errors = list(state.get("errors", []))

        results = []
        for rule_id, rule in rules.items():
            evaluation = evaluations.get(rule_id)
            if evaluation is None:
                errors.append(WorkflowError(
                    node="aggregate_criterion_results",
                    code="missing_evaluation",
                    message=f"rule {rule_id} has no evaluation (upstream failure)",
                    rule_id=rule_id,
                ))
                continue
            results.append(build_criterion_result(rule, evaluation, gap=None))

        summary = aggregate_results(results)
        timings = dict(state.get("timings", {}))
        timings["aggregate_criterion_results"] = time.perf_counter() - started
        log.info(
            "node_complete", node="aggregate_criterion_results",
            total=summary.total, compliant=summary.compliant,
        )
        return {"final_results": results, "summary": summary, "errors": errors, "timings": timings}
    return aggregate_criterion_results


def make_generate_gap_analysis(ctx: GraphContext):
    async def generate_gap_analysis(state: ComplianceState) -> dict:
        """LLM gap analysis for every NOT_COMPLIANT result."""
        started = time.perf_counter()
        rules = {r.rule_id: r for r in state.get("rules", [])}
        evaluations = {e.rule_id: e for e in state.get("evaluations", [])}
        errors = list(state.get("errors", []))
        gaps = []
        results = list(state.get("final_results", []))

        for result in results:
            if result.verdict != Verdict.NOT_COMPLIANT:
                continue
            rule = rules.get(result.rule_id)
            evaluation = evaluations.get(result.rule_id)
            if rule is None or evaluation is None:
                continue
            try:
                gap = await ctx.gap_analyzer.analyze(rule, evaluation)
                gaps.append(gap)
                result.gap_analysis = gap
            except Exception as exc:  # noqa: BLE001
                errors.append(WorkflowError(
                    node="generate_gap_analysis", code="gap_analysis_error",
                    message=str(exc)[:500], rule_id=result.rule_id,
                ))

        timings = dict(state.get("timings", {}))
        timings["generate_gap_analysis"] = time.perf_counter() - started
        log.info("node_complete", node="generate_gap_analysis", gaps=len(gaps))
        return {
            "gap_analyses": gaps,
            "final_results": results,
            "errors": errors,
            "timings": timings,
        }
    return generate_gap_analysis


def make_generate_final_report(ctx: GraphContext):
    async def generate_final_report(state: ComplianceState) -> dict:
        started = time.perf_counter()
        summary = state.get("summary")
        report = {
            "assessment_id": state["assessment_id"],
            "framework": state.get("framework") or ctx.settings.default_framework,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary.model_dump(mode="json") if summary else {},
            "documents": [
                {
                    "document_id": d.document_id,
                    "filename": d.filename,
                    "sha256": d.sha256,
                }
                for d in state.get("documents", [])
            ],
            "classified_documents": [
                {
                    "document_id": c.document_id,
                    "filename": c.filename,
                    "document_type": c.document_type.value,
                    "classification_source": c.classification_source,
                    "confidence": c.confidence,
                }
                for c in state.get("classified_documents", [])
            ],
            "results": [r.model_dump(mode="json") for r in state.get("final_results", [])],
            "errors": [e.model_dump(mode="json") for e in state.get("errors", [])],
            "timings": state.get("timings", {}),
            "models": {
                "llm": ctx.llm.model_name,
                "embedding": ctx.embedder.model_name,
            },
        }
        timings = dict(state.get("timings", {}))
        timings["generate_final_report"] = time.perf_counter() - started
        log.info("node_complete", node="generate_final_report")
        return {"report": report, "timings": timings}
    return generate_final_report


def make_persist_assessment(ctx: GraphContext):
    async def persist_assessment(state: ComplianceState) -> dict:
        """Persist per-rule results + final assessment status."""
        started = time.perf_counter()
        errors = list(state.get("errors", []))
        fatal = state.get("fatal_error")
        results = state.get("final_results", [])

        persisted = 0
        for result in results:
            try:
                await ctx.store.save_result(state["assessment_id"], result)
                persisted += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(WorkflowError(
                    node="persist_assessment", code="persist_error",
                    message=str(exc)[:500], rule_id=result.rule_id,
                ))

        if fatal:
            status = AssessmentStatus.FAILED if not results else AssessmentStatus.PARTIAL
        elif errors and persisted < len(results):
            status = AssessmentStatus.PARTIAL
        elif any(e.code == "missing_evaluation" for e in errors):
            status = AssessmentStatus.PARTIAL
        else:
            status = AssessmentStatus.COMPLETED

        try:
            await ctx.store.update_assessment_status(
                state["assessment_id"], status, error=fatal
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(WorkflowError(
                node="persist_assessment", code="status_update_error",
                message=str(exc)[:500],
            ))

        timings = dict(state.get("timings", {}))
        timings["persist_assessment"] = time.perf_counter() - started
        log.info(
            "node_complete", node="persist_assessment",
            status=status.value, persisted=persisted,
        )
        return {"errors": errors, "timings": timings, "_final_status": status.value}
    return persist_assessment
