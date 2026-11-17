import pytest

from app.core.errors import ElasticsearchError, RuleLoadError
from app.elasticsearch.store import ElasticsearchStore
from app.schemas.assessment import AssessmentStatus
from app.schemas.document import DocumentCategory, PolicyChunk
from app.schemas.rule import ComplianceRule


def _source(chunk_id: str = "chunk-1") -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": "document-1",
        "assessment_id": "assessment-1",
        "filename": "access.pdf",
        "document_type": DocumentCategory.ACCESS_CONTROL.value,
        "page_number": 2,
        "section": "Access reviews",
        "text": "Access is reviewed every 90 days.",
    }


class SearchClient:
    def __init__(self, *, knn=None, keyword=None, fail_knn: bool = False):
        self.knn = knn or {"hits": {"hits": []}}
        self.keyword = keyword or {"hits": {"max_score": None, "hits": []}}
        self.fail_knn = fail_knn

    async def search(self, **kwargs):
        if "knn" in kwargs:
            if self.fail_knn:
                raise RuntimeError("vector backend unavailable")
            return self.knn
        return self.keyword


@pytest.mark.asyncio
async def test_knn_score_uses_elasticsearch_normalized_cosine_score(settings):
    client = SearchClient(
        knn={"hits": {"hits": [{"_id": "chunk-1", "_score": 0.8, "_source": _source()}]}}
    )
    store = ElasticsearchStore(client, settings)

    evidence = await store.knn_search(
        assessment_id="assessment-1",
        query_vector=[0.1] * settings.embedding_dims,
        query_text="access reviews",
        top_k=5,
        min_similarity=0.7,
    )

    assert len(evidence) == 1
    assert evidence[0].similarity_score == 0.8


@pytest.mark.asyncio
async def test_hybrid_retrieval_surfaces_a_failed_search_leg(settings):
    client = SearchClient(
        fail_knn=True,
        keyword={
            "hits": {
                "max_score": 1.0,
                "hits": [{"_id": "chunk-1", "_score": 1.0, "_source": _source()}],
            }
        },
    )
    store = ElasticsearchStore(client, settings)

    with pytest.raises(ElasticsearchError, match="incomplete"):
        await store.knn_search(
            assessment_id="assessment-1",
            query_vector=[0.1] * settings.embedding_dims,
            query_text="access reviews",
            top_k=5,
            min_similarity=0.2,
        )


class RuleClient:
    def __init__(self, rules: list[ComplianceRule]):
        self.rules = {rule.rule_id: rule for rule in rules}
        self.requested_ids = []

    async def mget(self, *, index, ids):
        self.requested_ids = ids
        return {
            "docs": [
                (
                    {"_id": rule_id, "found": True, "_source": self.rules[rule_id].model_dump(mode="json")}
                    if rule_id in self.rules
                    else {"_id": rule_id, "found": False}
                )
                for rule_id in ids
            ]
        }


class UpdateClient:
    def __init__(self, result="updated"):
        self.result = result
        self.call = None

    async def update(self, **kwargs):
        self.call = kwargs
        return {"result": self.result}


def _rule(rule_id: str, *, framework: str = "SOC2", active: bool = True):
    return ComplianceRule(
        rule_id=rule_id,
        framework=framework,
        criterion="CC6.1",
        title="Access reviews",
        description="Review access",
        requirement="Access is periodically reviewed",
        active=active,
    )


@pytest.mark.asyncio
async def test_load_rules_deduplicates_ids_and_preserves_framework_scope(settings):
    client = RuleClient([_rule("SOC2-001")])
    store = ElasticsearchStore(client, settings)

    rules = await store.load_rules("SOC2", ["SOC2-001", "SOC2-001"])

    assert [rule.rule_id for rule in rules] == ["SOC2-001"]
    assert client.requested_ids == ["SOC2-001"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_rule",
    [_rule("ISO-001", framework="ISO27001"), _rule("SOC2-002", active=False)],
)
async def test_load_rules_rejects_inactive_or_cross_framework_ids(settings, invalid_rule):
    store = ElasticsearchStore(RuleClient([invalid_rule]), settings)

    with pytest.raises(RuleLoadError, match="outside framework"):
        await store.load_rules("SOC2", [invalid_rule.rule_id])


@pytest.mark.asyncio
async def test_bulk_chunk_indexing_fails_on_partial_success(settings, monkeypatch):
    async def partial_bulk(client, actions, *, raise_on_error):
        return 0, [{"index": {"status": 400}}]

    monkeypatch.setattr("app.elasticsearch.store.async_bulk", partial_bulk)
    chunk = PolicyChunk(
        chunk_id="chunk-1",
        document_id="document-1",
        assessment_id="assessment-1",
        filename="access.pdf",
        document_type=DocumentCategory.ACCESS_CONTROL,
        page_number=1,
        chunk_index=0,
        text="Access reviews.",
        content_hash="hash",
    )
    store = ElasticsearchStore(object(), settings)

    with pytest.raises(ElasticsearchError, match="partially failed"):
        await store.bulk_index_chunks([chunk], [[0.1] * settings.embedding_dims])


@pytest.mark.asyncio
async def test_try_start_assessment_uses_atomic_elasticsearch_update(settings):
    client = UpdateClient(result="updated")
    store = ElasticsearchStore(client, settings)

    assert await store.try_start_assessment("assessment-1") is True
    assert client.call["retry_on_conflict"] == 3
    assert client.call["script"]["params"]["pending"] == "PENDING"
    assert "ctx.op = 'noop'" in client.call["script"]["source"]


@pytest.mark.asyncio
async def test_try_start_assessment_reports_existing_claim(settings):
    store = ElasticsearchStore(UpdateClient(result="noop"), settings)

    assert await store.try_start_assessment("assessment-1") is False


@pytest.mark.asyncio
async def test_status_update_clears_stale_error_and_completion_time(settings):
    client = UpdateClient()
    store = ElasticsearchStore(client, settings)

    await store.update_assessment_status(
        "assessment-1", AssessmentStatus.PROCESSING
    )

    assert client.call["doc"]["error"] is None
    assert client.call["doc"]["completed_at"] is None
