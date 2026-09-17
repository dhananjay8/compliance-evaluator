"""Chunking / embedding / indexing nodes."""

import time

from app.core.errors import EmbeddingError
from app.core.logging import get_logger
from app.graph.context import GraphContext
from app.graph.state import ComplianceState
from app.schemas.workflow import WorkflowError

log = get_logger(__name__)


def make_chunk_documents(ctx: GraphContext):
    async def chunk_documents(state: ComplianceState) -> dict:
        started = time.perf_counter()
        classified = {c.document_id: c for c in state.get("classified_documents", [])}
        chunks = []
        for ext in state.get("extracted_documents", []):
            chunks.extend(ctx.chunker.chunk_document(ext, classified.get(ext.document_id)))
        timings = dict(state.get("timings", {}))
        timings["chunk_documents"] = time.perf_counter() - started
        log.info("node_complete", node="chunk_documents", chunks=len(chunks))
        if not chunks:
            return {"chunks": chunks, "timings": timings, "fatal_error": "no chunks produced"}
        return {"chunks": chunks, "timings": timings}
    return chunk_documents


def make_generate_embeddings(ctx: GraphContext):
    async def generate_embeddings(state: ComplianceState) -> dict:
        """Embed chunks; skip chunks already indexed with same model+hash."""
        started = time.perf_counter()
        errors = list(state.get("errors", []))
        chunks = state.get("chunks", [])
        model = ctx.embedder.model_name

        existing = await ctx.store.existing_chunk_hashes(state["assessment_id"], model)
        to_embed = [c for c in chunks if c.content_hash not in existing]
        skipped = len(chunks) - len(to_embed)

        embeddings: dict[str, list[float]] = {}
        try:
            if to_embed:
                vectors = await ctx.embedder.embed([c.text for c in to_embed])
                for chunk, vec in zip(to_embed, vectors, strict=True):
                    chunk.embedding_model = model
                    embeddings[chunk.chunk_id] = vec
        except EmbeddingError as exc:
            errors.append(WorkflowError(
                node="generate_embeddings", code=exc.code,
                message=str(exc)[:500], retryable=True,
            ))
            timings = dict(state.get("timings", {}))
            timings["generate_embeddings"] = time.perf_counter() - started
            return {
                "errors": errors, "timings": timings,
                "fatal_error": f"embedding generation failed: {exc}",
            }

        timings = dict(state.get("timings", {}))
        timings["generate_embeddings"] = time.perf_counter() - started
        log.info(
            "node_complete", node="generate_embeddings",
            embedded=len(embeddings), reused=skipped, model=model,
        )
        return {
            "chunk_embeddings": embeddings,
            "errors": errors,
            "timings": timings,
            "_embed_skip_count": skipped,
        }
    return generate_embeddings


def make_index_policy_chunks(ctx: GraphContext):
    async def index_policy_chunks(state: ComplianceState) -> dict:
        started = time.perf_counter()
        chunks = state.get("chunks", [])
        embeddings = state.get("chunk_embeddings", {})

        # Only chunks with fresh embeddings need indexing; the rest already exist.
        to_index = [c for c in chunks if c.chunk_id in embeddings]
        indexed = 0
        errors = list(state.get("errors", []))
        if to_index:
            try:
                indexed = await ctx.store.bulk_index_chunks(
                    to_index, [embeddings[c.chunk_id] for c in to_index]
                )
                await ctx.store.refresh(ctx.settings.idx_policy_chunks)
            except Exception as exc:  # noqa: BLE001
                errors.append(WorkflowError(
                    node="index_policy_chunks", code="elasticsearch_error",
                    message=str(exc)[:500], retryable=True,
                ))
                return {
                    "errors": errors,
                    "fatal_error": f"chunk indexing failed: {exc}",
                }
        total = indexed + (len(chunks) - len(to_index))
        timings = dict(state.get("timings", {}))
        timings["index_policy_chunks"] = time.perf_counter() - started
        log.info("node_complete", node="index_policy_chunks", indexed=indexed, total_available=total)
        return {"indexed_chunk_count": total, "errors": errors, "timings": timings}
    return index_policy_chunks
