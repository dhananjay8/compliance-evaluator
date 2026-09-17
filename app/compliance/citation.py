"""Citation validation — code-level, never delegated to the LLM.

A citation is valid only if its quote is a verbatim substring of the cited
chunk's stored text (compared after whitespace normalization). Invalid
citations are rejected; a COMPLIANT verdict with zero valid citations is
downgraded by the evaluator.
"""

import re

from app.core.logging import get_logger
from app.schemas.evaluation import Citation, ComplianceEvaluation, Verdict
from app.schemas.evidence import RetrievedEvidence

log = get_logger(__name__)

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


def _quote_in_text(quote: str, text: str) -> bool:
    return _norm(quote) in _norm(text)


def validate_citations(
    evaluation: ComplianceEvaluation,
    evidence: list[RetrievedEvidence],
) -> tuple[list[Citation], list[str]]:
    """Return (valid_citations, rejection_reasons)."""
    by_chunk = {e.chunk_id: e for e in evidence}
    valid: list[Citation] = []
    rejections: list[str] = []

    for cit in evaluation.citations:
        # Resolve the chunk: prefer chunk_id, else match filename+page.
        chunk = by_chunk.get(cit.chunk_id or "")
        if chunk is None:
            candidates = [
                e for e in evidence
                if e.filename == cit.filename and e.page_number == cit.page_number
            ]
            chunk = candidates[0] if candidates else None
            if chunk is not None:
                cit.chunk_id = chunk.chunk_id

        if chunk is None:
            rejections.append(
                f"citation '{cit.quote[:50]}…' has no matching evidence chunk "
                f"(file={cit.filename}, page={cit.page_number})"
            )
            continue
        if not cit.quote or not _quote_in_text(cit.quote, chunk.text):
            rejections.append(
                f"citation quote not found verbatim in chunk {chunk.chunk_id}"
            )
            continue
        # normalize metadata to the actual source chunk
        cit.filename = chunk.filename
        cit.page_number = chunk.page_number
        cit.section = cit.section or chunk.section
        valid.append(cit)

    return valid, rejections


def apply_citation_validation(
    evaluation: ComplianceEvaluation,
    evidence: list[RetrievedEvidence],
) -> ComplianceEvaluation:
    """Mutate evaluation: keep only valid citations, record notes."""
    valid, rejections = validate_citations(evaluation, evidence)
    evaluation.citations = valid
    evaluation.validation_notes.extend(rejections)
    evaluation.validated = True
    if rejections:
        log.info(
            "citations_rejected",
            rule_id=evaluation.rule_id,
            rejected=len(rejections),
            kept=len(valid),
        )
    return evaluation


def requires_downgrade(evaluation: ComplianceEvaluation) -> bool:
    """COMPLIANT without a single valid verbatim citation cannot stand."""
    return evaluation.verdict == Verdict.COMPLIANT and not evaluation.citations
