import sys
import types

import httpx
import pytest

from app.core.errors import EmbeddingError
from app.embeddings.ollama import OllamaEmbeddingProvider
from app.embeddings.sentence_transformer import SentenceTransformerProvider
from app.llm.ollama import OllamaLLMProvider


class FakeResponse:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body

    def raise_for_status(self):
        return None


class FlakyClient:
    def __init__(self, *, fail_count=0, response=None):
        self.fail_count = fail_count
        self.response = response or FakeResponse({"message": {"content": "ok"}})
        self.calls = 0
        self.model_response = FakeResponse({"models": []})

    async def post(self, url, *, json):
        self.calls += 1
        if self.calls <= self.fail_count:
            request = httpx.Request("POST", f"http://test{url}")
            raise httpx.ConnectError("temporary connection failure", request=request)
        return self.response

    async def get(self, url):
        return self.model_response

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_llm_retries_use_configured_retry_count(settings, monkeypatch):
    settings.llm_max_retries = 1
    provider = OllamaLLMProvider(settings)
    client = FlakyClient(fail_count=1)
    provider._client = client
    monkeypatch.setattr(
        "app.llm.ollama.wait_exponential",
        lambda **kwargs: lambda retry_state: 0,
    )

    assert await provider._chat({}) == {"message": {"content": "ok"}}
    assert client.calls == 2
    await provider.close()


@pytest.mark.asyncio
async def test_embedding_retry_count_and_vector_count_are_validated(settings, monkeypatch):
    settings.embedding_max_retries = 1
    provider = OllamaEmbeddingProvider(settings)
    client = FlakyClient(
        fail_count=1,
        response=FakeResponse({"embeddings": [[0.1] * settings.embedding_dims]}),
    )
    provider._client = client
    monkeypatch.setattr(
        "app.embeddings.ollama.wait_exponential",
        lambda **kwargs: lambda retry_state: 0,
    )

    assert len(await provider.embed(["one"])) == 1
    assert client.calls == 2

    async def missing_vector(texts):
        return []

    provider._embed_batch = missing_vector
    with pytest.raises(EmbeddingError, match="count mismatch"):
        await provider.embed(["one"])
    await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested", "installed", "expected"),
    [
        ("qwen2.5:3b", ["qwen2.5:3b"], True),
        ("qwen2.5:3b", ["qwen2.5:7b"], False),
        ("qwen2.5", ["qwen2.5:latest"], True),
    ],
)
async def test_llm_health_requires_the_configured_model_tag(
    settings, requested, installed, expected
):
    settings.ollama_model = requested
    provider = OllamaLLMProvider(settings)
    client = FlakyClient()
    client.model_response = FakeResponse({"models": [{"name": name} for name in installed]})
    provider._client = client

    assert await provider.health() is expected
    await provider.close()


def test_sentence_transformer_dimension_mismatch_fails_fast(settings, monkeypatch):
    class Model:
        def __init__(self, name):
            pass

        def get_sentence_embedding_dimension(self):
            return settings.embedding_dims - 1

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = Model
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)

    with pytest.raises(EmbeddingError, match="dimension mismatch"):
        SentenceTransformerProvider(settings)
