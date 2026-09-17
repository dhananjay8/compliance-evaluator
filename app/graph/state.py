"""Strongly-typed LangGraph state.

Design: heavy artifacts (extracted text, chunk bodies) are kept in state only
as long as needed; durable copies live in Elasticsearch / disk. The per-rule
loop is driven by `pending_rule_ids` + `current_rule_id`.
"""

from typing import Any, TypedDict

from app.schemas.assessment import AssessmentSummary, CriterionResult
from app.schemas.document import (
    ClassifiedDocument,
    DocumentRef,
    ExtractedDocument,
    PolicyChunk,
)
from app.schemas.evaluation import ComplianceEvaluation
from app.schemas.evidence import RetrievedEvidence
from app.schemas.gap import GapAnalysis
from app.schemas.rule import ComplianceRule
from app.schemas.workflow import WorkflowError


class ComplianceState(TypedDict, total=False):
    # identity / config
    assessment_id: str
    framework: str
    customer_id: str | None
    rule_ids: list[str] | None  # optional scoping from API

    # ingest → extraction → classification
    documents: list[DocumentRef]
    extracted_documents: list[ExtractedDocument]
    classified_documents: list[ClassifiedDocument]

    # chunking / embedding / indexing
    chunks: list[PolicyChunk]
    chunk_embeddings: dict[str, list[float]]  # chunk_id -> vector (transient)
    indexed_chunk_count: int

    # rules
    rules: list[ComplianceRule]
    pending_rule_ids: list[str]
    current_rule_id: str | None

    # per-rule working set
    retrieved_evidence: list[RetrievedEvidence]
    current_evaluation: ComplianceEvaluation | None

    # outputs
    evaluations: list[ComplianceEvaluation]
    gap_analyses: list[GapAnalysis]
    final_results: list[CriterionResult]
    summary: AssessmentSummary | None
    report: dict[str, Any] | None

    # bookkeeping
    errors: list[WorkflowError]
    fatal_error: str | None
    timings: dict[str, float]
