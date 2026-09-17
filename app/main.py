"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health, router
from app.api.service import AssessmentService
from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.elasticsearch.client import close_es_client
from app.graph.runner import build_context

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    ctx = build_context(settings)
    app.state.graph_context = ctx
    app.state.assessment_service = AssessmentService(ctx, settings)

    # Best-effort index bootstrap; readiness endpoint reports ES health.
    try:
        created = await ctx.store.ensure_indices()
        if created:
            log.info("indices_created", indices=created)
    except Exception as exc:  # noqa: BLE001
        log.error("index_bootstrap_failed", error=str(exc))

    yield
    await close_es_client()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Compliance Evaluator",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    app.include_router(health)
    return app


app = create_app()
