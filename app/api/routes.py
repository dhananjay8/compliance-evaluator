"""REST endpoints."""

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from app.api.schemas import (
    CreateAssessmentRequest,
    CreateAssessmentResponse,
    DocumentUploadResponse,
    EvaluateRequest,
    EvaluateResponse,
    ExplicitRoutingItem,
    StatusResponse,
)
from app.api.service import AssessmentService
from app.core.errors import AppError, UploadValidationError
from app.schemas.assessment import AssessmentResultsResponse

router = APIRouter()


def get_service(request: Request) -> AssessmentService:
    return request.app.state.assessment_service


@router.post("/api/v1/assessments", response_model=CreateAssessmentResponse, status_code=201)
async def create_assessment(
    payload: CreateAssessmentRequest,
    service: AssessmentService = Depends(get_service),
):
    assessment = await service.create_assessment(
        payload.name, payload.framework, payload.customer_id
    )
    return CreateAssessmentResponse(
        assessment_id=assessment.assessment_id, status=assessment.status.value
    )


@router.post(
    "/api/v1/assessments/{assessment_id}/documents",
    response_model=DocumentUploadResponse,
)
async def upload_documents(
    assessment_id: str,
    service: AssessmentService = Depends(get_service),
    files: list[UploadFile] = File(...),
    routing: str | None = Form(default=None),
):
    """Upload PDFs. Optional `routing` form field: JSON array of
    {"filename": ..., "document_type": ...} for explicit Mode-A routing."""
    routing_items: list[ExplicitRoutingItem] | None = None
    if routing:
        try:
            routing_items = [ExplicitRoutingItem(**item) for item in json.loads(routing)]
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"Invalid routing JSON: {exc}") from exc
    try:
        uploaded, rejected = await service.upload_documents(
            assessment_id, files, routing_items
        )
    except UploadValidationError as exc:
        raise HTTPException(422, exc.message) from exc
    return DocumentUploadResponse(
        assessment_id=assessment_id, uploaded=uploaded, rejected=rejected
    )


@router.post(
    "/api/v1/assessments/{assessment_id}/evaluate",
    response_model=EvaluateResponse,
    status_code=202,
)
async def evaluate(
    assessment_id: str,
    payload: EvaluateRequest | None = None,
    service: AssessmentService = Depends(get_service),
):
    try:
        await service.start_evaluation(
            assessment_id, rule_ids=(payload.rule_ids if payload else None)
        )
    except UploadValidationError as exc:
        status = 404 if "not found" in exc.message.lower() else 409
        raise HTTPException(status, exc.message) from exc
    return EvaluateResponse(assessment_id=assessment_id, status="PROCESSING")


@router.get("/api/v1/assessments/{assessment_id}", response_model=StatusResponse)
async def get_assessment(
    assessment_id: str,
    service: AssessmentService = Depends(get_service),
):
    assessment = await service.ctx.store.get_assessment(assessment_id)
    if assessment is None:
        raise HTTPException(404, "Assessment not found")
    return StatusResponse(
        assessment_id=assessment.assessment_id,
        status=assessment.status.value,
        framework=assessment.framework,
        document_ids=assessment.document_ids,
        error=assessment.error,
        created_at=assessment.created_at.isoformat() if assessment.created_at else None,
        completed_at=assessment.completed_at.isoformat() if assessment.completed_at else None,
    )


@router.get(
    "/api/v1/assessments/{assessment_id}/results",
    response_model=AssessmentResultsResponse,
)
async def get_results(
    assessment_id: str,
    service: AssessmentService = Depends(get_service),
):
    assessment = await service.ctx.store.get_assessment(assessment_id)
    if assessment is None:
        raise HTTPException(404, "Assessment not found")
    results = await service.ctx.store.get_results(assessment_id)
    from app.compliance.aggregation import aggregate_results
    return AssessmentResultsResponse(
        assessment_id=assessment_id,
        framework=assessment.framework,
        status=assessment.status,
        summary=aggregate_results(results),
        results=results,
    )


@router.get("/api/v1/assessments/{assessment_id}/results/{rule_id}")
async def get_result_detail(
    assessment_id: str,
    rule_id: str,
    service: AssessmentService = Depends(get_service),
):
    result = await service.ctx.store.get_result(assessment_id, rule_id)
    if result is None:
        raise HTTPException(404, f"No result for rule {rule_id}")
    return result.model_dump(mode="json")


health = APIRouter()


@health.get("/health")
async def health_check():
    return {"status": "ok"}


@health.get("/ready")
async def readiness(request: Request):
    ctx = request.app.state.graph_context
    es_ok = await ctx.store.ping()
    llm_ok = await ctx.llm.health()
    emb_ok = await ctx.embedder.health()
    ready = es_ok and llm_ok and emb_ok
    return {
        "ready": ready,
        "elasticsearch": es_ok,
        "llm": {"ok": llm_ok, "model": ctx.llm.model_name},
        "embeddings": {"ok": emb_ok, "model": ctx.embedder.model_name},
    }
