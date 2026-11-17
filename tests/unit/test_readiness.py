import json
from types import SimpleNamespace

import pytest

from app.api.routes import readiness


class HealthCheck:
    def __init__(self, result: bool | Exception):
        self.result = result

    async def check(self) -> bool:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
async def test_readiness_returns_success_when_dependencies_are_healthy():
    store = SimpleNamespace(ping=HealthCheck(True).check)
    llm = SimpleNamespace(health=HealthCheck(True).check, model_name="test-llm")
    embedder = SimpleNamespace(health=HealthCheck(True).check, model_name="test-embedder")
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                graph_context=SimpleNamespace(store=store, llm=llm, embedder=embedder)
            )
        )
    )

    response = await readiness(request)

    assert response.status_code == 200
    assert json.loads(response.body)["ready"] is True


@pytest.mark.asyncio
async def test_readiness_returns_service_unavailable_when_a_dependency_fails():
    store = SimpleNamespace(ping=HealthCheck(RuntimeError("unavailable")).check)
    llm = SimpleNamespace(health=HealthCheck(True).check, model_name="test-llm")
    embedder = SimpleNamespace(health=HealthCheck(False).check, model_name="test-embedder")
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                graph_context=SimpleNamespace(store=store, llm=llm, embedder=embedder)
            )
        )
    )

    response = await readiness(request)
    body = json.loads(response.body)

    assert response.status_code == 503
    assert body["ready"] is False
    assert body["elasticsearch"] is False
    assert body["embeddings"]["ok"] is False
