"""Assessment application service — bridges API and the LangGraph pipeline."""

import asyncio
import uuid

from fastapi import UploadFile

from app.api.schemas import DocumentUploadItem, ExplicitRoutingItem
from app.config import Settings, get_settings
from app.core.errors import AppError, UploadValidationError
from app.core.hashing import sha256_bytes
from app.core.logging import get_logger
from app.graph.context import GraphContext
from app.graph.runner import run_assessment
from app.graph.state import ComplianceState
from app.schemas.assessment import Assessment, AssessmentStatus
from app.schemas.document import DocumentRef
from app.security.validation import validate_upload

log = get_logger(__name__)


class AssessmentService:
    def __init__(self, ctx: GraphContext, settings: Settings | None = None):
        self.ctx = ctx
        self.settings = settings or ctx.settings

    async def create_assessment(self, name: str | None, framework: str,
                                customer_id: str | None) -> Assessment:
        assessment = Assessment(
            assessment_id=str(uuid.uuid4()),
            name=name,
            framework=framework,
            customer_id=customer_id,
            status=AssessmentStatus.PENDING,
        )
        await self.ctx.store.save_assessment(assessment)
        log.info("assessment_created", assessment_id=assessment.assessment_id)
        return assessment

    async def upload_documents(
        self,
        assessment_id: str,
        files: list[UploadFile],
        routing: list[ExplicitRoutingItem] | None = None,
    ) -> tuple[list[DocumentUploadItem], list[dict]]:
        assessment = await self._require_assessment(assessment_id)
        if assessment.status not in (AssessmentStatus.PENDING,):
            raise UploadValidationError(
                f"Assessment {assessment_id} is {assessment.status}; uploads closed"
            )
        if len(files) > self.settings.max_files_per_assessment:
            raise UploadValidationError(
                f"Too many files ({len(files)}); max {self.settings.max_files_per_assessment}"
            )

        routing_map = {r.filename: r.document_type for r in (routing or [])}
        uploaded: list[DocumentUploadItem] = []
        rejected: list[dict] = []

        for file in files:
            filename = file.filename or "unnamed.pdf"
            try:
                data = await file.read()
                safe_name = validate_upload(filename, data, self.settings)
                document_id = str(uuid.uuid4())
                sha = sha256_bytes(data)
                path = await self.ctx.storage.save(assessment_id, document_id, data)
                ref = DocumentRef(
                    document_id=document_id,
                    assessment_id=assessment_id,
                    filename=safe_name,
                    size_bytes=len(data),
                    sha256=sha,
                    storage_path=str(path),
                    document_type=routing_map.get(safe_name) or routing_map.get(filename),
                )
                await self.ctx.store.index_document(ref)
                assessment.document_ids.append(document_id)
                uploaded.append(DocumentUploadItem(
                    document_id=document_id,
                    filename=safe_name,
                    sha256=sha,
                    size_bytes=len(data),
                    document_type=ref.document_type.value if ref.document_type else None,
                ))
            except UploadValidationError as exc:
                rejected.append({"filename": filename, "reason": exc.message})
            except AppError as exc:
                rejected.append({"filename": filename, "reason": exc.message})

        await self.ctx.store.save_assessment(assessment)
        log.info(
            "documents_uploaded",
            assessment_id=assessment_id,
            uploaded=len(uploaded),
            rejected=len(rejected),
        )
        return uploaded, rejected

    async def start_evaluation(self, assessment_id: str,
                               rule_ids: list[str] | None = None) -> None:
        """Launch the LangGraph pipeline as a background task."""
        assessment = await self._require_assessment(assessment_id)
        if assessment.status == AssessmentStatus.PROCESSING:
            raise UploadValidationError("Assessment already processing")
        if not assessment.document_ids:
            raise UploadValidationError("No documents uploaded for this assessment")

        await self.ctx.store.update_assessment_status(assessment_id, AssessmentStatus.PROCESSING)

        # Load document refs from ES to rebuild state.
        docs: list[DocumentRef] = []
        for doc_id in assessment.document_ids:
            try:
                resp = await self.ctx.store.client.get(
                    index=self.ctx.store._idx(self.settings.idx_policy_documents),
                    id=doc_id,
                )
                docs.append(DocumentRef(**resp["_source"]))
            except Exception as exc:  # noqa: BLE001
                log.warning("document_ref_load_failed", document_id=doc_id, error=str(exc))

        initial: ComplianceState = {
            "assessment_id": assessment_id,
            "framework": assessment.framework,
            "customer_id": assessment.customer_id,
            "rule_ids": rule_ids or assessment.rule_ids,
            "documents": docs,
            "errors": [],
            "timings": {},
        }
        asyncio.create_task(self._run(initial))

    async def _run(self, initial: ComplianceState) -> None:
        try:
            await run_assessment(self.ctx, initial)
        except Exception as exc:  # noqa: BLE001
            log.error("assessment_graph_crashed",
                      assessment_id=initial.get("assessment_id"), error=str(exc))
            try:
                await self.ctx.store.update_assessment_status(
                    initial["assessment_id"], AssessmentStatus.FAILED, error=str(exc)[:500]
                )
            except Exception:  # noqa: BLE001
                pass

    async def _require_assessment(self, assessment_id: str) -> Assessment:
        assessment = await self.ctx.store.get_assessment(assessment_id)
        if assessment is None:
            raise UploadValidationError(f"Assessment not found: {assessment_id}")
        return assessment
