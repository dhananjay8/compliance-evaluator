"""FastAPI application entrypoint."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health, router
from app.api.service import AssessmentService
from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.elasticsearch.client import close_es_client
from app.embeddings.provider import close_embedding_provider
from app.graph.runner import build_context
from app.llm.provider import close_llm_provider

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    service: AssessmentService | None = None
    try:
        ctx = build_context(settings)
        app.state.graph_context = ctx
        service = AssessmentService(ctx, settings)
        app.state.assessment_service = service

        # Best-effort index bootstrap; readiness endpoint reports ES health.
        try:
            created = await ctx.store.ensure_indices()
            if created:
                log.info("indices_created", indices=created)
        except Exception as exc:  # noqa: BLE001
            log.error("index_bootstrap_failed", error=str(exc))

        yield
    finally:
        try:
            if service is not None:
                await service.shutdown()
        finally:
            cleanup = await asyncio.gather(
                close_es_client(),
                close_llm_provider(),
                close_embedding_provider(),
                return_exceptions=True,
            )
            for resource, result in zip(
                ("elasticsearch", "llm_provider", "embedding_provider"),
                cleanup,
                strict=True,
            ):
                if isinstance(result, BaseException):
                    log.error("resource_shutdown_failed", resource=resource, error=str(result))


def create_app() -> FastAPI:
    app = FastAPI(
        title="Compliance Evaluator",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    app.include_router(health)
    return app


app = create_app()
