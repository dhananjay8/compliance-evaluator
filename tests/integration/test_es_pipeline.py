"""Integration tests — real Elasticsearch + Ollama.

Run with: COMPLIANCE_IT=1 pytest tests/integration -q
Requires: ES at ELASTICSEARCH_URL (default localhost:9200) and Ollama with
nomic-embed-text pulled.
"""

import os
import uuid

import pytest

from tests.conftest import requires_services

pytestmark = [requires_services, pytest.mark.integration]


@pytest.fixture
async def store(settings):
    from app.elasticsearch.client import get_es_client
    from app.elasticsearch.store import ElasticsearchStore

    settings.elasticsearch_url = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
    settings.es_index_prefix = f"it_{uuid.uuid4().hex[:8]}_"
    s = ElasticsearchStore(get_es_client(settings), settings)
    await s.ensure_indices()
    yield s
    for logical in ("policy_documents", "policy_chunks", "compliance_rules",
                    "assessments", "assessment_results"):
        try:
            await s.client.indices.delete(index=s._idx(logical))
        except Exception:
            pass


@pytest.mark.asyncio
async def test_indices_created(store, settings):
    for logical in ("policy_documents", "policy_chunks", "compliance_rules",
                    "assessments", "assessment_results"):
        assert await store.client.indices.exists(index=store._idx(logical))


@pytest.mark.asyncio
async def test_rule_seed_and_load(store):
    from app.schemas.rule import ComplianceRule

    rules = [
        ComplianceRule(rule_id="SOC2-T-001", criterion="CC6.1", title="t1",
                       description="d", requirement="r"),
        ComplianceRule(rule_id="SOC2-T-002", criterion="CC7.3", title="t2",
                       description="d", requirement="r"),
    ]
    assert await store.bulk_index_rules(rules) == 2
    await store.refresh("compliance_rules")
    loaded = await store.load_rules("SOC2")
    assert {r.rule_id for r in loaded} == {"SOC2-T-001", "SOC2-T-002"}


@pytest.mark.asyncio
async def test_chunk_index_and_knn_retrieval(store, settings):
    """chunk → embedding → index → kNN retrieval round-trip (real Ollama)."""
    from app.embeddings.ollama import OllamaEmbeddingProvider
    from app.retrieval.retriever import EvidenceRetriever
    from app.schemas.document import DocumentCategory, PolicyChunk
    from app.schemas.rule import ComplianceRule

    embedder = OllamaEmbeddingProvider(settings)
    if not await embedder.health():
        pytest.skip("Ollama embedding model unavailable")

    chunks = [
        PolicyChunk(chunk_id="c1", document_id="d1", assessment_id="a-it",
                    filename="access.pdf", document_type=DocumentCategory.ACCESS_CONTROL,
                    page_number=1, section="Access", chunk_index=0,
                    text="Privileged user access is reviewed every 90 days.",
                    content_hash="h1"),
        PolicyChunk(chunk_id="c2", document_id="d2", assessment_id="a-it",
                    filename="vendor.pdf", document_type=DocumentCategory.VENDOR_MANAGEMENT,
                    page_number=2, section="Vendors", chunk_index=0,
                    text="Vendors are assessed for security risk before engagement.",
                    content_hash="h2"),
    ]
    vectors = await embedder.embed([c.text for c in chunks])
    assert len(vectors[0]) == settings.embedding_dims
    assert await store.bulk_index_chunks(chunks, vectors) == 2
    await store.refresh("policy_chunks")

    retriever = EvidenceRetriever(store, embedder, settings)
    rule = ComplianceRule(
        rule_id="SOC2-CC6.1-004", criterion="CC6.1",
        title="Periodic access reviews", description="access review cadence",
        requirement="periodic access reviews at defined cadence",
        keywords=["access review", "privileged"],
    )
    evidence = await retriever.retrieve_for_rule("a-it", rule)
    assert evidence
    assert evidence[0].chunk_id == "c1"  # access chunk outranks vendor chunk
    assert evidence[0].similarity_score >= settings.min_similarity


@pytest.mark.asyncio
async def test_metadata_filtering(store, settings):
    from app.schemas.document import DocumentCategory, PolicyChunk

    chunks = [
        PolicyChunk(chunk_id="f1", document_id="d1", assessment_id="a-x",
                    filename="a.pdf", document_type=DocumentCategory.ACCESS_CONTROL,
                    page_number=1, chunk_index=0, text="access review",
                    content_hash="x1"),
        PolicyChunk(chunk_id="f2", document_id="d2", assessment_id="a-y",
                    filename="b.pdf", document_type=DocumentCategory.ACCESS_CONTROL,
                    page_number=1, chunk_index=0, text="access review",
                    content_hash="x2"),
    ]
    await store.bulk_index_chunks(chunks, [[0.1] * settings.embedding_dims] * 2)
    await store.refresh("policy_chunks")
    hits = await store.knn_search(
        assessment_id="a-x", query_vector=[0.1] * settings.embedding_dims,
        query_text="access", top_k=5, min_similarity=0.0,
    )
    assert {h.chunk_id for h in hits} == {"f1"}  # other assessment filtered out
