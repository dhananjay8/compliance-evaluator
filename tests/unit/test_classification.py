"""Document classification tests — explicit routing, heuristic, LLM fallback."""

import pytest

from app.documents.classification.classifier import DocumentClassifier
from app.schemas.document import (
    DocumentCategory,
    DocumentRef,
    ExtractedDocument,
    ExtractedPage,
)


def _ref(dtype=None, filename="doc.pdf") -> DocumentRef:
    return DocumentRef(
        document_id="d1", assessment_id="a1", filename=filename,
        size_bytes=1, sha256="x" * 64, storage_path="/tmp/x",
        document_type=dtype,
    )


pytestmark = pytest.mark.asyncio


def _extracted(text: str, filename="doc.pdf") -> ExtractedDocument:
    return ExtractedDocument(
        document_id="d1", assessment_id="a1", filename=filename, sha256="s",
        page_count=1, pages=[ExtractedPage(page_number=1, text=text)],
    )


async def test_explicit_routing_wins(settings):
    clf = DocumentClassifier(settings)
    ref = _ref(dtype=DocumentCategory.VENDOR_MANAGEMENT, filename="random.pdf")
    result = await clf.classify(ref, _extracted("incident incident incident"))
    assert result.document_type == DocumentCategory.VENDOR_MANAGEMENT
    assert result.classification_source == "explicit"
    assert result.confidence == 1.0


async def test_filename_hint_classification(settings):
    clf = DocumentClassifier(settings)
    ref = _ref(filename="incident_response_policy.pdf")
    text = "This policy covers security incident response and incident escalation."
    result = await clf.classify(ref, _extracted(text, "incident_response_policy.pdf"))
    assert result.document_type == DocumentCategory.INCIDENT_RESPONSE
    assert result.classification_source == "heuristic"


async def test_text_keyword_classification(settings):
    clf = DocumentClassifier(settings)
    ref = _ref(filename="policy_v2.pdf")
    text = (
        "Access control requirements: user provisioning requires approval. "
        "Privileged access is reviewed quarterly. Multi-factor authentication "
        "is required. Least privilege applies to all access control decisions."
    )
    result = await clf.classify(ref, _extracted(text))
    assert result.document_type == DocumentCategory.ACCESS_CONTROL


async def test_unknown_document_falls_to_other(settings):
    settings.classifier_llm_enabled = False
    clf = DocumentClassifier(settings)
    ref = _ref(filename="zzz.pdf")
    result = await clf.classify(ref, _extracted("lorem ipsum dolor sit amet"))
    assert result.document_type == DocumentCategory.OTHER


class _FakeLLM:
    async def complete(self, prompt, **kw):
        return '{"document_type": "VENDOR_MANAGEMENT", "confidence": 0.9, "rationale": "vendor terms"}'


async def test_llm_fallback_for_low_confidence(settings):
    settings.classifier_llm_threshold = 0.99  # force heuristic below threshold
    clf = DocumentClassifier(settings, llm=_FakeLLM())
    ref = _ref(filename="mystery.pdf")
    text = "third party suppliers are assessed before engagement."
    result = await clf.classify(ref, _extracted(text))
    assert result.document_type == DocumentCategory.VENDOR_MANAGEMENT
    assert result.classification_source == "llm"
