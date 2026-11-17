from types import SimpleNamespace

import pytest

from app.graph.nodes.evaluate import make_evaluate_evidence, make_retrieve_policy_evidence
from app.graph.nodes.report import (
    make_aggregate_criterion_results,
    make_persist_assessment,
)
from app.schemas.assessment import AssessmentStatus
from app.schemas.rule import ComplianceRule


class FailingRetriever:
    async def retrieve_for_rule(self, assessment_id, rule):
        raise RuntimeError("Elasticsearch search timeout")


class UnusedEvaluator:
    def __init__(self):
        self.calls = 0

    async def evaluate(self, rule, evidence):
        self.calls += 1
        raise AssertionError("A failed retrieval must not be treated as absent evidence")


class RecordingStore:
    def __init__(self):
        self.saved_results = []
        self.status = None

    async def save_result(self, assessment_id, result):
        self.saved_results.append(result)

    async def update_assessment_status(self, assessment_id, status, error=None):
        self.status = status


@pytest.mark.asyncio
async def test_retrieval_failure_is_partial_not_a_false_absence(settings):
    rule = ComplianceRule(
        rule_id="SOC2-001",
        criterion="CC6.1",
        title="Access reviews",
        description="Review access rights",
        requirement="Access rights are reviewed every 90 days",
    )
    store = RecordingStore()
    evaluator = UnusedEvaluator()
    ctx = SimpleNamespace(
        settings=settings,
        store=store,
        retriever=FailingRetriever(),
        evaluator=evaluator,
    )
    state = {
        "assessment_id": "assessment-1",
        "rules": [rule],
        "pending_rule_ids": [rule.rule_id],
        "errors": [],
        "evaluations": [],
        "timings": {},
    }

    state.update(await make_retrieve_policy_evidence(ctx)(state))
    state.update(await make_evaluate_evidence(ctx)(state))
    state.update(await make_aggregate_criterion_results(ctx)(state))
    await make_persist_assessment(ctx)(state)

    assert evaluator.calls == 0
    assert state["final_results"] == []
    assert any(error.code == "missing_evaluation" for error in state["errors"])
    assert store.saved_results == []
    assert store.status is AssessmentStatus.PARTIAL
