"""Assessment application service — bridges API and the LangGraph pipeline."""

import asyncio
import uuid

from fastapi import UploadFile

from app.api.schemas import DocumentUploadItem, ExplicitRoutingItem
from app.config import Settings
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

_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _content_type_for(filename: str) -> str:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _CONTENT_TYPES.get(ext, "application/octet-stream")


class AssessmentService:
    """Application use cases; graph jobs are tracked and owned by this process."""

    def __init__(self, ctx: GraphContext, settings: Settings | None = None):
        self.ctx = ctx
        self.settings = settings or ctx.settings
        self._tasks: dict[asyncio.Task[None], str] = {}
        self._is_shutting_down = False

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
        """Validate, store, and register uploads without reading oversized files fully."""
        assessment = await self._require_assessment(assessment_id)
        if assessment.status not in (AssessmentStatus.PENDING,):
            raise UploadValidationError(
                f"Assessment {assessment_id} is {assessment.status}; uploads closed"
            )
        available_slots = self.settings.max_files_per_assessment - len(assessment.document_ids)
        if len(files) > available_slots:
            raise UploadValidationError(
                f"Upload would exceed the {self.settings.max_files_per_assessment}-file "
                "assessment limit"
            )

        routing_map = {r.filename: r.document_type for r in (routing or [])}
        uploaded: list[DocumentUploadItem] = []
        rejected: list[dict] = []

        for file in files:
            filename = file.filename or "unnamed.pdf"
            document_id: str | None = None
            try:
                # Read only one byte beyond the configured limit so an oversized
                # multipart upload cannot be fully materialized in application memory.
                data = await file.read(self.settings.max_file_size_bytes + 1)
                safe_name = validate_upload(filename, data, self.settings)
                document_id = str(uuid.uuid4())
                sha = sha256_bytes(data)
                path = await self.ctx.storage.save(assessment_id, document_id, data)
                ref = DocumentRef(
                    document_id=document_id,
                    assessment_id=assessment_id,
                    filename=safe_name,
                    content_type=_content_type_for(safe_name),
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
                await self._cleanup_failed_upload(assessment_id, document_id)
                rejected.append({"filename": filename, "reason": exc.message})
            except AppError as exc:
                await self._cleanup_failed_upload(assessment_id, document_id)
                rejected.append({"filename": filename, "reason": exc.message})
            except BaseException:
                await self._cleanup_failed_upload(assessment_id, document_id)
                raise

        await self.ctx.store.save_assessment(assessment)
        log.info(
            "documents_uploaded",
            assessment_id=assessment_id,
            uploaded=len(uploaded),
            rejected=len(rejected),
        )
        return uploaded, rejected

    async def _cleanup_failed_upload(
        self, assessment_id: str, document_id: str | None
    ) -> None:
        if document_id is None:
            return
        try:
            await self.ctx.store.delete_document(document_id)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "upload_compensation_failed",
                resource="document_metadata",
                assessment_id=assessment_id,
                document_id=document_id,
                error=str(exc),
            )
        try:
            await self.ctx.storage.delete(assessment_id, document_id)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "upload_compensation_failed",
                resource="document_bytes",
                assessment_id=assessment_id,
                document_id=document_id,
                error=str(exc),
            )

    async def start_evaluation(self, assessment_id: str,
                               rule_ids: list[str] | None = None) -> None:
        """Atomically claim an assessment, then launch a tracked graph task."""
        if self._is_shutting_down:
            raise UploadValidationError("Service is shutting down")

        assessment = await self._require_assessment(assessment_id)
        if assessment.status == AssessmentStatus.PROCESSING:
            raise UploadValidationError("Assessment already processing")
        if assessment.status != AssessmentStatus.PENDING:
            raise UploadValidationError(
                "Assessment has already been submitted; create a new assessment to re-evaluate"
            )
        if not assessment.document_ids:
            raise UploadValidationError("No documents uploaded for this assessment")

        if not await self.ctx.store.try_start_assessment(assessment_id):
            raise UploadValidationError("Assessment status changed; reload before retrying")

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
        task = asyncio.create_task(
            self._run(initial), name=f"assessment:{assessment_id}"
        )
        self._tasks[task] = assessment_id
        task.add_done_callback(lambda completed: self._tasks.pop(completed, None))

    async def shutdown(self) -> None:
        """Drain active assessments, cancelling work that exceeds the shutdown budget."""
        self._is_shutting_down = True
        tasks = list(self._tasks)
        if not tasks:
            return

        _, pending = await asyncio.wait(
            tasks, timeout=self.settings.assessment_shutdown_timeout_seconds
        )
        if pending:
            log.warning(
                "assessment_shutdown_timeout",
                count=len(pending),
                assessment_ids=[self._tasks.get(task) for task in pending],
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def _run(self, initial: ComplianceState) -> None:
        try:
            await run_assessment(self.ctx, initial)
        except asyncio.CancelledError:
            assessment_id = initial["assessment_id"]
            log.warning("assessment_cancelled_for_shutdown", assessment_id=assessment_id)
            try:
                await self.ctx.store.update_assessment_status(
                    assessment_id,
                    AssessmentStatus.FAILED,
                    error="Assessment cancelled because the service is shutting down",
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "assessment_shutdown_status_update_failed",
                    assessment_id=assessment_id,
                    error=str(exc),
                )
            raise
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
