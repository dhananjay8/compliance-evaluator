"""Rule loading + per-rule retrieval/evaluation loop nodes."""

import time

from app.core.errors import AppError
from app.core.logging import get_logger
from app.graph.context import GraphContext
from app.graph.state import ComplianceState
from app.schemas.workflow import WorkflowError

log = get_logger(__name__)


def make_load_compliance_framework(ctx: GraphContext):
    async def load_compliance_framework(state: ComplianceState) -> dict:
        """Load active rules for the framework; queue them for evaluation."""
        started = time.perf_counter()
        framework = state.get("framework") or ctx.settings.default_framework
        rule_ids = state.get("rule_ids")
        try:
            rules = await ctx.store.load_rules(framework, rule_ids)
        except AppError as exc:
            return {"fatal_error": f"rule load failed: {exc}"}
        if not rules:
            return {
                "fatal_error": (
                    f"no active compliance rules found for framework {framework}"
                    + (f" matching {rule_ids}" if rule_ids else "")
                    + " — seed rules first (scripts/seed_rules.py)"
                )
            }
        timings = dict(state.get("timings", {}))
        timings["load_compliance_framework"] = time.perf_counter() - started
        log.info("node_complete", node="load_compliance_framework", rules=len(rules))
        return {
            "rules": rules,
            "pending_rule_ids": [r.rule_id for r in rules],
            "evaluations": [],
            "timings": timings,
        }
    return load_compliance_framework


def make_retrieve_policy_evidence(ctx: GraphContext):
    async def retrieve_policy_evidence(state: ComplianceState) -> dict:
        """Pop next rule; hybrid-retrieve its evidence."""
        started = time.perf_counter()
        pending = list(state.get("pending_rule_ids", []))
        rules = {r.rule_id: r for r in state.get("rules", [])}
        if not pending:
            return {"current_rule_id": None, "retrieved_evidence": []}

        rule_id = pending.pop(0)
        rule = rules[rule_id]
        errors = list(state.get("errors", []))
        try:
            evidence = await ctx.retriever.retrieve_for_rule(state["assessment_id"], rule)
        except Exception as exc:  # noqa: BLE001
            errors.append(WorkflowError(
                node="retrieve_policy_evidence", code="retrieval_error",
                message=str(exc)[:500], retryable=True, rule_id=rule_id,
            ))
            evidence = []

        timings = dict(state.get("timings", {}))
        timings[f"retrieve:{rule_id}"] = time.perf_counter() - started
        return {
            "pending_rule_ids": pending,
            "current_rule_id": rule_id,
            "retrieved_evidence": evidence,
            "errors": errors,
            "timings": timings,
        }
    return retrieve_policy_evidence


def make_evaluate_evidence(ctx: GraphContext):
    async def evaluate_evidence(state: ComplianceState) -> dict:
        """LLM-evaluate current rule's evidence; validate citations; enforce verdict."""
        started = time.perf_counter()
        rule_id = state.get("current_rule_id")
        rules = {r.rule_id: r for r in state.get("rules", [])}
        rule = rules.get(rule_id)
        errors = list(state.get("errors", []))
        evaluations = list(state.get("evaluations", []))

        if rule is None:
            return {"errors": errors}

        try:
            evaluation = await ctx.evaluator.evaluate(rule, state.get("retrieved_evidence", []))
            evaluations.append(evaluation)
        except Exception as exc:  # noqa: BLE001
            errors.append(WorkflowError(
                node="evaluate_evidence",
                code=getattr(exc, "code", "evaluation_error"),
                message=str(exc)[:500],
                retryable=getattr(exc, "retryable", False),
                rule_id=rule_id,
            ))

        timings = dict(state.get("timings", {}))
        timings[f"evaluate:{rule_id}"] = time.perf_counter() - started
        return {"evaluations": evaluations, "errors": errors, "timings": timings}
    return evaluate_evidence
