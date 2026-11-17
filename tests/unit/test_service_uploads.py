import pytest

from app.api.service import AssessmentService
from app.core.errors import ElasticsearchError, UploadValidationError
from app.schemas.assessment import Assessment


class FakeUpload:
    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self.data = data
        self.read_sizes = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self.data if size < 0 else self.data[:size]


class FakeStore:
    def __init__(self, assessment: Assessment):
        self.assessment = assessment
        self.saved = 0

    async def get_assessment(self, assessment_id):
        return self.assessment

    async def save_assessment(self, assessment):
        self.saved += 1

    async def index_document(self, document):
        raise AssertionError("Oversized files must be rejected before indexing")


class FakeStorage:
    async def save(self, *args):
        raise AssertionError("Oversized files must be rejected before storage")


class TrackingStore(FakeStore):
    def __init__(self, assessment):
        super().__init__(assessment)
        self.deleted_documents = []

    async def index_document(self, document):
        raise ElasticsearchError("index unavailable")

    async def delete_document(self, document_id):
        self.deleted_documents.append(document_id)


class TrackingStorage:
    def __init__(self):
        self.saved_ids = []
        self.deleted_ids = []

    async def save(self, assessment_id, document_id, data):
        self.saved_ids.append(document_id)
        return f"/uploads/{assessment_id}/{document_id}.pdf"

    async def delete(self, assessment_id, document_id):
        self.deleted_ids.append(document_id)


@pytest.mark.asyncio
async def test_oversized_upload_reads_only_one_byte_over_limit(settings):
    settings.max_file_size_mb = 1
    assessment = Assessment(assessment_id="assessment-1", document_ids=[])
    service = AssessmentService(
        type("Context", (), {
            "store": FakeStore(assessment),
            "storage": FakeStorage(),
            "settings": settings,
        })(),
        settings,
    )
    file = FakeUpload("large.pdf", b"%PDF-" + b"x" * settings.max_file_size_bytes)

    uploaded, rejected = await service.upload_documents("assessment-1", [file])

    assert uploaded == []
    assert rejected[0]["reason"].startswith("File exceeds")
    assert file.read_sizes == [settings.max_file_size_bytes + 1]


@pytest.mark.asyncio
async def test_upload_limit_applies_to_total_assessment_documents(settings):
    settings.max_files_per_assessment = 1
    assessment = Assessment(
        assessment_id="assessment-1",
        document_ids=["existing-document"],
    )
    service = AssessmentService(
        type("Context", (), {
            "store": FakeStore(assessment),
            "storage": FakeStorage(),
            "settings": settings,
        })(),
        settings,
    )
    file = FakeUpload("next.pdf", b"%PDF-1.4")

    with pytest.raises(UploadValidationError, match="assessment limit"):
        await service.upload_documents("assessment-1", [file])

    assert file.read_sizes == []


@pytest.mark.asyncio
async def test_failed_metadata_index_compensates_file_and_document_record(settings):
    assessment = Assessment(assessment_id="assessment-1", document_ids=[])
    store = TrackingStore(assessment)
    storage = TrackingStorage()
    service = AssessmentService(
        type("Context", (), {"store": store, "storage": storage, "settings": settings})(),
        settings,
    )

    uploaded, rejected = await service.upload_documents(
        "assessment-1",
        [FakeUpload("valid.pdf", b"%PDF-1.4 test document")],
    )

    assert uploaded == []
    assert rejected[0]["reason"] == "index unavailable"
    assert len(storage.saved_ids) == 1
    assert store.deleted_documents == storage.saved_ids
    assert storage.deleted_ids == storage.saved_ids
    assert assessment.document_ids == []


def test_chunk_overlap_must_be_smaller_than_chunk_size():
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError, match="CHUNK_OVERLAP"):
        Settings(CHUNK_SIZE=100, CHUNK_OVERLAP=100)


def test_evaluate_request_rejects_ignored_framework_field():
    from pydantic import ValidationError

    from app.api.schemas import EvaluateRequest

    with pytest.raises(ValidationError, match="framework"):
        EvaluateRequest(framework="SOC2")
