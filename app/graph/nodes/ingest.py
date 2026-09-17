"""Ingestion-stage nodes: ingest → extract → classify → normalize."""

import time

from app.core.errors import AppError, DocumentExtractionError
from app.core.logging import get_logger
from app.graph.context import GraphContext
from app.graph.state import ComplianceState
from app.schemas.workflow import WorkflowError

log = get_logger(__name__)


def _err(node: str, exc: Exception, **kw) -> WorkflowError:
    return WorkflowError(
        node=node,
        code=getattr(exc, "code", "unexpected_error"),
        message=str(exc)[:500],
        retryable=getattr(exc, "retryable", False),
        **kw,
    )


def make_ingest_documents(ctx: GraphContext):
    async def ingest_documents(state: ComplianceState) -> dict:
        """Register uploaded documents; dedupe by sha256 within the assessment."""
        started = time.perf_counter()
        docs = state.get("documents", [])
        errors = list(state.get("errors", []))
        seen: set[str] = set()
        unique = []
        for doc in docs:
            if doc.sha256 in seen:
                errors.append(WorkflowError(
                    node="ingest_documents",
                    code="duplicate_document",
                    message=f"Duplicate document skipped: {doc.filename}",
                    document_id=doc.document_id,
                ))
                continue
            seen.add(doc.sha256)
            unique.append(doc)
            await ctx.store.index_document(doc)
        if not unique:
            return {
                "documents": unique,
                "errors": errors,
                "fatal_error": "no valid documents to process",
            }
        timings = dict(state.get("timings", {}))
        timings["ingest_documents"] = time.perf_counter() - started
        log.info("node_complete", node="ingest_documents", documents=len(unique))
        return {"documents": unique, "errors": errors, "timings": timings}
    return ingest_documents


def make_extract_document_text(ctx: GraphContext):
    async def extract_document_text(state: ComplianceState) -> dict:
        started = time.perf_counter()
        errors = list(state.get("errors", []))
        extracted = []
        for doc in state.get("documents", []):
            try:
                path = ctx.storage.path_for(doc.assessment_id, doc.document_id)
                result = ctx.extractor.extract(doc, path)
                extracted.append(result)
                for warning in result.extraction_warnings:
                    errors.append(WorkflowError(
                        node="extract_document_text",
                        code="page_extraction_warning",
                        message=warning,
                        document_id=doc.document_id,
                    ))
            except DocumentExtractionError as exc:
                errors.append(_err("extract_document_text", exc, document_id=doc.document_id))
            except Exception as exc:  # noqa: BLE001
                errors.append(_err("extract_document_text", exc, document_id=doc.document_id))
        timings = dict(state.get("timings", {}))
        timings["extract_document_text"] = time.perf_counter() - started
        log.info("node_complete", node="extract_document_text", extracted=len(extracted))
        if not extracted:
            return {
                "extracted_documents": extracted,
                "errors": errors,
                "timings": timings,
                "fatal_error": "no documents yielded extractable text",
            }
        return {"extracted_documents": extracted, "errors": errors, "timings": timings}
    return extract_document_text


def make_classify_documents(ctx: GraphContext):
    async def classify_documents(state: ComplianceState) -> dict:
        started = time.perf_counter()
        errors = list(state.get("errors", []))
        refs = {d.document_id: d for d in state.get("documents", [])}
        classified = []
        for ext in state.get("extracted_documents", []):
            ref = refs.get(ext.document_id)
            if ref is None:
                continue
            try:
                result = await ctx.classifier.classify(ref, ext)
                classified.append(result)
                await ctx.store.update_document_classification(
                    ext.document_id,
                    result.document_type.value,
                    result.classification_source,
                    result.confidence,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(_err("classify_documents", exc, document_id=ext.document_id))
        timings = dict(state.get("timings", {}))
        timings["classify_documents"] = time.perf_counter() - started
        log.info(
            "node_complete", node="classify_documents",
            classified=[(c.filename, c.document_type.value, c.classification_source) for c in classified],
        )
        return {"classified_documents": classified, "errors": errors, "timings": timings}
    return classify_documents


def make_normalize_documents(ctx: GraphContext):
    async def normalize_documents(state: ComplianceState) -> dict:
        """Attach classification onto extracted docs; drop empty pages from scope."""
        started = time.perf_counter()
        classified = {c.document_id: c for c in state.get("classified_documents", [])}
        normalized = []
        for ext in state.get("extracted_documents", []):
            cls = classified.get(ext.document_id)
            if cls is None:
                continue
            # keep only pages with text; provenance stays on the page objects
            ext.pages = [p for p in ext.pages if not p.empty]
            normalized.append(ext)
        timings = dict(state.get("timings", {}))
        timings["normalize_documents"] = time.perf_counter() - started
        log.info("node_complete", node="normalize_documents", documents=len(normalized))
        if not normalized:
            return {
                "extracted_documents": normalized,
                "timings": timings,
                "fatal_error": "no non-empty pages across documents",
            }
        return {"extracted_documents": normalized, "timings": timings}
    return normalize_documents
