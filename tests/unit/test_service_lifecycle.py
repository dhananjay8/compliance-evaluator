import asyncio
from types import SimpleNamespace

import pytest

from app.api.service import AssessmentService
from app.core.errors import UploadValidationError
from app.embeddings import provider as embedding_provider_factory
from app.embeddings.ollama import OllamaEmbeddingProvider
from app.llm import provider as llm_provider_factory
from app.llm.ollama import OllamaLLMProvider
from app.schemas.assessment import Assessment, AssessmentStatus


class FakeStore:
    def __init__(self):
        self.assessment = Assessment(
            assessment_id="assessment-1",
            framework="SOC2",
            status=AssessmentStatus.PENDING,
            document_ids=["document-1"],
        )
        self.status_updates = []
        self.client = self

    async def get_assessment(self, assessment_id):
        return self.assessment

    async def get(self, **kwargs):
        raise RuntimeError("document unavailable")

    async def try_start_assessment(self, assessment_id):
        if self.assessment.status is not AssessmentStatus.PENDING:
            return False
        self.assessment.status = AssessmentStatus.PROCESSING
        return True

    async def update_assessment_status(self, assessment_id, status, error=None):
        self.status_updates.append((assessment_id, status, error))


@pytest.mark.asyncio
async def test_shutdown_cancels_overdue_assessment_and_records_failure(settings, monkeypatch):
    settings.assessment_shutdown_timeout_seconds = 0
    store = FakeStore()
    ctx = SimpleNamespace(store=store, settings=settings)
    service = AssessmentService(ctx, settings)
    started = asyncio.Event()

    async def wait_for_cancellation(context, initial):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("app.api.service.run_assessment", wait_for_cancellation)

    await service.start_evaluation("assessment-1")
    await asyncio.wait_for(started.wait(), timeout=1)
    await service.shutdown()

    assert store.status_updates[-1][1] is AssessmentStatus.FAILED
    assert "shutting down" in store.status_updates[-1][2]
    with pytest.raises(UploadValidationError, match="shutting down"):
        await service.start_evaluation("assessment-1")


@pytest.mark.asyncio
async def test_provider_shutdown_closes_http_clients_and_clears_singletons(settings, monkeypatch):
    llm = OllamaLLMProvider(settings)
    embedder = OllamaEmbeddingProvider(settings)
    monkeypatch.setattr(llm_provider_factory, "_provider", llm)
    monkeypatch.setattr(embedding_provider_factory, "_provider", embedder)

    await asyncio.gather(
        llm_provider_factory.close_llm_provider(),
        embedding_provider_factory.close_embedding_provider(),
    )

    assert llm._client.is_closed
    assert embedder._client.is_closed
    assert llm_provider_factory._provider is None
    assert embedding_provider_factory._provider is None


@pytest.mark.asyncio
async def test_terminal_assessment_cannot_be_rerun_with_stale_results(settings):
    store = FakeStore()
    store.assessment.status = AssessmentStatus.PARTIAL
    ctx = SimpleNamespace(store=store, settings=settings)
    service = AssessmentService(ctx, settings)

    with pytest.raises(UploadValidationError, match="create a new assessment"):
        await service.start_evaluation("assessment-1")
