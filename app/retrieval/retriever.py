"""Hybrid evidence retrieval: embed rule → kNN + BM25 → dedupe → top-N."""

import time

from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.elasticsearch.store import ElasticsearchStore
from app.embeddings.provider import EmbeddingProvider
from app.schemas.evidence import RetrievedEvidence
from app.schemas.rule import ComplianceRule

log = get_logger(__name__)


class EvidenceRetriever:
    def __init__(
        self,
        store: ElasticsearchStore,
        embedder: EmbeddingProvider,
        settings: Settings | None = None,
    ):
        self.store = store
        self.embedder = embedder
        self.settings = settings or get_settings()

    async def retrieve_for_rule(
        self,
        assessment_id: str,
        rule: ComplianceRule,
        *,
        document_types: list[str] | None = None,
    ) -> list[RetrievedEvidence]:
        started = time.perf_counter()
        query_text = rule.retrieval_text()
        vectors = await self.embedder.embed([query_text])
        query_vector = vectors[0] if vectors else []

        # Prefer rule-declared document type hints, else caller filter.
        doc_types = rule.document_types or document_types or None

        evidence = await self.store.knn_search(
            assessment_id=assessment_id,
            query_vector=query_vector,
            query_text=query_text,
            top_k=self.settings.top_k,
            min_similarity=self.settings.min_similarity,
            document_types=doc_types,
            bm25_candidates=self.settings.retrieval_bm25_candidates,
        )

        # If a document_type filter starved retrieval, retry corpus-wide once.
        if not evidence and doc_types:
            evidence = await self.store.knn_search(
                assessment_id=assessment_id,
                query_vector=query_vector,
                query_text=query_text,
                top_k=self.settings.top_k,
                min_similarity=self.settings.min_similarity,
                document_types=None,
                bm25_candidates=self.settings.retrieval_bm25_candidates,
            )

        capped = evidence[: self.settings.max_evidence_chunks]
        log.info(
            "evidence_retrieved",
            assessment_id=assessment_id,
            rule_id=rule.rule_id,
            candidates=len(evidence),
            returned=len(capped),
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return capped
