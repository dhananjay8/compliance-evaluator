"""Elasticsearch persistence + retrieval operations.

All index access goes through this store so nodes/services never build raw queries.
"""

from datetime import datetime, timezone
from typing import Any

from elasticsearch import AsyncElasticsearch
from elasticsearch.helpers import async_bulk

from app.config import Settings, get_settings
from app.core.errors import ElasticsearchError, RuleLoadError
from app.core.logging import get_logger
from app.elasticsearch.mappings import all_index_definitions
from app.schemas.assessment import Assessment, AssessmentStatus, CriterionResult
from app.schemas.document import DocumentRef, PolicyChunk
from app.schemas.evidence import RetrievedEvidence
from app.schemas.rule import ComplianceRule

log = get_logger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ElasticsearchStore:
    def __init__(self, client: AsyncElasticsearch, settings: Settings | None = None):
        self.client = client
        self.settings = settings or get_settings()

    def _idx(self, logical: str) -> str:
        return self.settings.index_name(logical)

    # ------------------------------------------------------------------
    # Index bootstrap
    # ------------------------------------------------------------------
    async def ensure_indices(self) -> list[str]:
        created: list[str] = []
        for logical, body in all_index_definitions(self.settings).items():
            name = self._idx(logical)
            try:
                exists = await self.client.indices.exists(index=name)
                if not exists:
                    await self.client.indices.create(index=name, **body)
                    created.append(name)
                    log.info("es_index_created", index=name)
            except Exception as exc:  # noqa: BLE001
                raise ElasticsearchError(f"Failed to ensure index {name}: {exc}") from exc
        return created

    async def ping(self) -> bool:
        try:
            return bool(await self.client.ping())
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------
    async def index_document(self, doc: DocumentRef, *, page_count: int | None = None,
                             classification: dict | None = None) -> None:
        body = doc.model_dump(mode="json")
        body["document_type"] = doc.document_type.value if doc.document_type else None
        if page_count is not None:
            body["page_count"] = page_count
        if classification:
            body.update(classification)
        try:
            await self.client.index(
                index=self._idx(self.settings.idx_policy_documents),
                id=doc.document_id,
                document=body,
            )
        except Exception as exc:  # noqa: BLE001
            raise ElasticsearchError(f"Failed to index document {doc.document_id}: {exc}") from exc

    async def delete_document(self, document_id: str) -> None:
        """Delete a newly-created document record during upload compensation."""
        try:
            await self.client.delete(
                index=self._idx(self.settings.idx_policy_documents),
                id=document_id,
            )
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status_code", None) == 404 or "not_found" in str(exc):
                return
            raise ElasticsearchError(
                f"Failed to remove document {document_id}: {exc}"
            ) from exc

    async def update_document_classification(self, document_id: str, document_type: str,
                                             source: str, confidence: float) -> None:
        try:
            await self.client.update(
                index=self._idx(self.settings.idx_policy_documents),
                id=document_id,
                doc={
                    "document_type": document_type,
                    "classification_source": source,
                    "classification_confidence": confidence,
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise ElasticsearchError(f"Failed to update classification for {document_id}: {exc}") from exc

    async def find_document_by_hash(self, assessment_id: str, sha256: str) -> dict | None:
        resp = await self.client.search(
            index=self._idx(self.settings.idx_policy_documents),
            query={"bool": {"must": [
                {"term": {"assessment_id": assessment_id}},
                {"term": {"sha256": sha256}},
            ]}},
            size=1,
        )
        hits = resp["hits"]["hits"]
        return hits[0]["_source"] if hits else None

    # ------------------------------------------------------------------
    # Chunks
    # ------------------------------------------------------------------
    async def existing_chunk_hashes(self, assessment_id: str, embedding_model: str) -> set[str]:
        """Content hashes already embedded with the current model (dedup/idempotency)."""
        hashes: set[str] = set()
        try:
            async for doc in self._scan(
                index=self._idx(self.settings.idx_policy_chunks),
                query={"bool": {"must": [
                    {"term": {"assessment_id": assessment_id}},
                    {"term": {"embedding_model": embedding_model}},
                ]}},
                _source=["content_hash"],
            ):
                hashes.add(doc["content_hash"])
        except Exception as exc:  # noqa: BLE001
            log.warning("existing_chunk_hashes_failed", error=str(exc))
        return hashes

    async def bulk_index_chunks(self, chunks: list[PolicyChunk],
                                embeddings: list[list[float]]) -> int:
        if not chunks:
            return 0
        index = self._idx(self.settings.idx_policy_chunks)
        actions = []
        for chunk, vector in zip(chunks, embeddings, strict=True):
            body = chunk.model_dump(mode="json")
            body["document_type"] = chunk.document_type.value
            body["policy_category"] = chunk.document_type.value
            body["embedding"] = vector
            body["indexed_at"] = _utcnow_iso()
            actions.append({"_index": index, "_id": chunk.chunk_id, "_source": body})
        try:
            success, errors = await async_bulk(self.client, actions, raise_on_error=False)
            if errors:
                log.error("chunk_bulk_partial_failure", failed=len(errors), index=index)
                raise ElasticsearchError(
                    f"Bulk chunk indexing partially failed: {len(errors)} of "
                    f"{len(actions)} chunks"
                )
            return int(success)
        except ElasticsearchError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ElasticsearchError(f"Bulk chunk indexing failed: {exc}") from exc

    async def get_chunks_by_ids(self, chunk_ids: list[str]) -> dict[str, dict]:
        if not chunk_ids:
            return {}
        resp = await self.client.mget(
            index=self._idx(self.settings.idx_policy_chunks), ids=chunk_ids
        )
        return {d["_id"]: d["_source"] for d in resp["docs"] if d.get("found")}

    # ------------------------------------------------------------------
    # Hybrid retrieval
    # ------------------------------------------------------------------
    async def knn_search(self, *, assessment_id: str, query_vector: list[float],
                         query_text: str, top_k: int, min_similarity: float,
                         document_types: list[str] | None = None,
                         bm25_candidates: int = 8) -> list[RetrievedEvidence]:
        """Run both retrieval legs; incomplete hybrid results are an operational error.

        kNN scores are Elasticsearch's normalized cosine scores in [0, 1].
        Returning an empty list is reserved for two successful searches with no
        qualifying evidence, so callers can distinguish absence from outage.
        """
        index = self._idx(self.settings.idx_policy_chunks)
        filters: list[dict] = [{"term": {"assessment_id": assessment_id}}]
        if document_types:
            filters.append({"terms": {"document_type": document_types}})

        evidence: dict[str, RetrievedEvidence] = {}

        # --- vector leg ---
        failures: list[str] = []
        try:
            knn_resp = await self.client.search(
                index=index,
                knn={
                    "field": "embedding",
                    "query_vector": query_vector,
                    "k": top_k,
                    "num_candidates": max(top_k * 4, 50),
                    "filter": filters,
                },
                size=top_k,
                _source=["chunk_id", "document_id", "filename", "document_type",
                         "page_number", "section", "text"],
            )
            for hit in knn_resp["hits"]["hits"]:
                # Elasticsearch maps cosine similarity to (_score + 1) / 2.
                score = max(0.0, min(1.0, hit.get("_score") or 0.0))
                if score < min_similarity:
                    continue
                src = hit["_source"]
                evidence[hit["_id"]] = RetrievedEvidence(
                    chunk_id=src["chunk_id"],
                    document_id=src["document_id"],
                    filename=src["filename"],
                    document_type=src.get("document_type"),
                    page_number=src["page_number"],
                    section=src.get("section"),
                    text=src["text"],
                    similarity_score=round(score, 4),
                    retrieval_method="knn",
                )
        except Exception as exc:  # noqa: BLE001
            failures.append("vector")
            log.error("knn_search_failed", error=str(exc), assessment_id=assessment_id)

        # --- BM25 leg ---
        try:
            bm25_resp = await self.client.search(
                index=index,
                query={"bool": {
                    "must": [{"match": {"text": {"query": query_text, "operator": "or"}}}],
                    "filter": filters,
                }},
                size=bm25_candidates,
                _source=["chunk_id", "document_id", "filename", "document_type",
                         "page_number", "section", "text"],
            )
            max_score = bm25_resp["hits"]["max_score"] or 1.0
            for hit in bm25_resp["hits"]["hits"]:
                if hit["_id"] in evidence:
                    evidence[hit["_id"]].retrieval_method = "hybrid"
                    continue
                src = hit["_source"]
                norm = min(1.0, (hit["_score"] or 0.0) / max_score) if max_score else 0.0
                evidence[hit["_id"]] = RetrievedEvidence(
                    chunk_id=src["chunk_id"],
                    document_id=src["document_id"],
                    filename=src["filename"],
                    document_type=src.get("document_type"),
                    page_number=src["page_number"],
                    section=src.get("section"),
                    text=src["text"],
                    similarity_score=round(norm, 4),
                    retrieval_method="bm25",
                )
        except Exception as exc:  # noqa: BLE001
            failures.append("keyword")
            log.error("bm25_search_failed", error=str(exc), assessment_id=assessment_id)

        if failures:
            failed_legs = ", ".join(failures)
            raise ElasticsearchError(
                f"Hybrid evidence retrieval incomplete; failed search leg(s): {failed_legs}"
            )

        ranked = sorted(evidence.values(), key=lambda e: e.similarity_score, reverse=True)
        return ranked

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------
    async def bulk_index_rules(self, rules: list[ComplianceRule]) -> int:
        index = self._idx(self.settings.idx_compliance_rules)
        actions = []
        for rule in rules:
            body = rule.model_dump(mode="json")
            body["retrieval_text"] = rule.retrieval_text()
            actions.append({"_index": index, "_id": rule.rule_id, "_source": body})
        success, errors = await async_bulk(self.client, actions, raise_on_error=False)
        if errors:
            log.error("rule_bulk_partial_failure", failed=len(errors))
        return int(success)

    async def load_rules(self, framework: str, rule_ids: list[str] | None = None) -> list[ComplianceRule]:
        index = self._idx(self.settings.idx_compliance_rules)
        try:
            if rule_ids:
                requested_ids = list(dict.fromkeys(rule_ids))
                resp = await self.client.mget(index=index, ids=requested_ids)
                loaded = [
                    ComplianceRule(**doc["_source"])
                    for doc in resp["docs"]
                    if doc.get("found")
                ]
                valid_rules = [
                    rule for rule in loaded if rule.framework == framework and rule.active
                ]
                valid_ids = {rule.rule_id for rule in valid_rules}
                invalid_ids = [rule_id for rule_id in requested_ids if rule_id not in valid_ids]
                if invalid_ids:
                    raise RuleLoadError(
                        f"Requested rules are missing, inactive, or outside framework "
                        f"{framework}: {', '.join(invalid_ids)}"
                    )
                rules = valid_rules
            else:
                rules = []
                async for doc in self._scan(
                    index=index,
                    query={"bool": {"must": [
                        {"term": {"framework": framework}},
                        {"term": {"active": True}},
                    ]}},
                ):
                    rules.append(ComplianceRule(**doc))
        except (ElasticsearchError, RuleLoadError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuleLoadError(f"Failed to load rules: {exc}") from exc
        rules.sort(key=lambda r: r.rule_id)
        return rules

    async def count_rules(self, framework: str) -> int:
        resp = await self.client.count(
            index=self._idx(self.settings.idx_compliance_rules),
            query={"bool": {"must": [
                {"term": {"framework": framework}},
                {"term": {"active": True}},
            ]}},
        )
        return int(resp["count"])

    # ------------------------------------------------------------------
    # Assessments + results
    # ------------------------------------------------------------------
    async def save_assessment(self, assessment: Assessment) -> None:
        try:
            await self.client.index(
                index=self._idx(self.settings.idx_assessments),
                id=assessment.assessment_id,
                document=assessment.model_dump(mode="json"),
            )
        except Exception as exc:  # noqa: BLE001
            raise ElasticsearchError(f"Failed to save assessment: {exc}") from exc

    async def get_assessment(self, assessment_id: str) -> Assessment | None:
        try:
            resp = await self.client.get(
                index=self._idx(self.settings.idx_assessments), id=assessment_id
            )
            return Assessment(**resp["_source"])
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status_code", None) == 404 or "not_found" in str(exc):
                return None
            raise ElasticsearchError(f"Failed to load assessment {assessment_id}: {exc}") from exc

    async def update_assessment_status(self, assessment_id: str, status: AssessmentStatus,
                                       error: str | None = None) -> None:
        now = _utcnow_iso()
        doc: dict[str, Any] = {
            "status": status.value,
            "updated_at": now,
            "error": error,
        }
        if status in (AssessmentStatus.COMPLETED, AssessmentStatus.FAILED, AssessmentStatus.PARTIAL):
            doc["completed_at"] = now
        else:
            doc["completed_at"] = None
        try:
            await self.client.update(
                index=self._idx(self.settings.idx_assessments), id=assessment_id, doc=doc
            )
        except Exception as exc:  # noqa: BLE001
            raise ElasticsearchError(f"Failed to update assessment status: {exc}") from exc

    async def try_start_assessment(self, assessment_id: str) -> bool:
        """Atomically claim an assessment unless another request is already running it."""
        now = _utcnow_iso()
        try:
            response = await self.client.update(
                index=self._idx(self.settings.idx_assessments),
                id=assessment_id,
                retry_on_conflict=3,
                script={
                    "lang": "painless",
                    "source": (
                        "if (ctx._source.status != params.pending) { "
                        "ctx.op = 'noop'; "
                        "} else { "
                        "ctx._source.status = params.processing; "
                        "ctx._source.updated_at = params.now; "
                        "ctx._source.completed_at = null; "
                        "ctx._source.error = null; "
                        "}"
                    ),
                    "params": {
                        "pending": AssessmentStatus.PENDING.value,
                        "processing": AssessmentStatus.PROCESSING.value,
                        "now": now,
                    },
                },
            )
            return response.get("result") == "updated"
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status_code", None) == 404 or "not_found" in str(exc):
                return False
            raise ElasticsearchError(
                f"Failed to claim assessment {assessment_id}: {exc}"
            ) from exc

    async def save_result(self, assessment_id: str, result: CriterionResult) -> None:
        body = result.model_dump(mode="json")
        body["assessment_id"] = assessment_id
        body["evaluated_at"] = _utcnow_iso()
        try:
            await self.client.index(
                index=self._idx(self.settings.idx_assessment_results),
                id=f"{assessment_id}:{result.rule_id}",
                document=body,
            )
        except Exception as exc:  # noqa: BLE001
            raise ElasticsearchError(f"Failed to save result {result.rule_id}: {exc}") from exc

    async def get_results(self, assessment_id: str) -> list[CriterionResult]:
        results: list[CriterionResult] = []
        async for doc in self._scan(
            index=self._idx(self.settings.idx_assessment_results),
            query={"term": {"assessment_id": assessment_id}},
        ):
            doc.pop("assessment_id", None)
            doc.pop("evaluated_at", None)
            results.append(CriterionResult(**doc))
        results.sort(key=lambda r: r.rule_id)
        return results

    async def get_result(self, assessment_id: str, rule_id: str) -> CriterionResult | None:
        try:
            resp = await self.client.get(
                index=self._idx(self.settings.idx_assessment_results),
                id=f"{assessment_id}:{rule_id}",
            )
            src = resp["_source"]
            src.pop("assessment_id", None)
            src.pop("evaluated_at", None)
            return CriterionResult(**src)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status_code", None) == 404 or "not_found" in str(exc):
                return None
            raise ElasticsearchError(f"Failed to load result {rule_id}: {exc}") from exc

    async def refresh(self, logical: str) -> None:
        await self.client.indices.refresh(index=self._idx(logical))

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    async def _scan(self, *, index: str, query: dict, _source: list[str] | None = None):
        """Simple search_after-free scan via scroll for small local datasets."""
        resp = await self.client.search(
            index=index, query=query, size=500, scroll="2m",
            _source=_source if _source else True,
        )
        scroll_id = resp.get("_scroll_id")
        try:
            while True:
                hits = resp["hits"]["hits"]
                if not hits:
                    break
                for hit in hits:
                    yield hit["_source"]
                resp = await self.client.scroll(scroll_id=scroll_id, scroll="2m")
                scroll_id = resp.get("_scroll_id", scroll_id)
        finally:
            if scroll_id:
                try:
                    await self.client.clear_scroll(scroll_id=scroll_id)
                except Exception:  # noqa: BLE001
                    pass
