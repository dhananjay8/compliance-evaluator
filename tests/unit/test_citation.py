"""Citation validation tests — verbatim enforcement (CASE 7)."""

from app.compliance.citation import (
    apply_citation_validation,
    requires_downgrade,
    validate_citations,
)
from app.schemas.evaluation import (
    Citation,
    ComplianceEvaluation,
    EvidenceStatus,
    Verdict,
)
from app.schemas.evidence import RetrievedEvidence


def _evidence() -> list[RetrievedEvidence]:
    return [
        RetrievedEvidence(
            chunk_id="c1", document_id="d1", filename="Access_Control.pdf",
            page_number=4, section="Privileged Access",
            text="Privileged user access is reviewed every 90 days by the security team.",
            similarity_score=0.9,
        ),
        RetrievedEvidence(
            chunk_id="c2", document_id="d2", filename="InfoSec.pdf",
            page_number=12, section=None,
            text="Multi-factor authentication is required for all remote access.",
            similarity_score=0.8,
        ),
    ]


def _evaluation(citations: list[Citation]) -> ComplianceEvaluation:
    return ComplianceEvaluation(
        rule_id="SOC2-CC6.1-004",
        verdict=Verdict.COMPLIANT,
        evidence_status=EvidenceStatus.EXPLICIT,
        confidence=0.9,
        reasoning="explicit",
        citations=citations,
    )


def test_valid_verbatim_citation_accepted():
    ev = _evaluation([Citation(
        filename="Access_Control.pdf", page_number=4,
        quote="Privileged user access is reviewed every 90 days",
    )])
    valid, rejections = validate_citations(ev, _evidence())
    assert len(valid) == 1 and not rejections
    assert valid[0].chunk_id == "c1"  # resolved via filename+page


def test_paraphrased_citation_rejected():
    ev = _evaluation([Citation(
        filename="Access_Control.pdf", page_number=4,
        quote="Access is checked quarterly by security staff",  # paraphrase — not verbatim
    )])
    valid, rejections = validate_citations(ev, _evidence())
    assert not valid and rejections


def test_fabricated_document_rejected():
    ev = _evaluation([Citation(
        filename="Nonexistent.pdf", page_number=99,
        quote="Privileged user access is reviewed every 90 days",
    )])
    valid, rejections = validate_citations(ev, _evidence())
    assert not valid and rejections


def test_whitespace_tolerant_matching():
    ev = _evaluation([Citation(
        filename="Access_Control.pdf", page_number=4, chunk_id="c1",
        quote="privileged   user access is reviewed   every 90 days",
    )])
    valid, _ = validate_citations(ev, _evidence())
    assert len(valid) == 1


def test_compliant_without_valid_citation_requires_downgrade():
    ev = _evaluation([Citation(
        filename="Access_Control.pdf", page_number=4,
        quote="invented quote not in source",
    )])
    apply_citation_validation(ev, _evidence())
    assert requires_downgrade(ev)


def test_cross_document_citation_valid():
    ev = _evaluation([Citation(
        filename="InfoSec.pdf", page_number=12,
        quote="Multi-factor authentication is required for all remote access",
    )])
    valid, _ = validate_citations(ev, _evidence())
    assert len(valid) == 1 and valid[0].chunk_id == "c2"
