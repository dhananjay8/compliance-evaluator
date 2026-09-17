"""Structured compliance evaluator.

Pipeline per rule: build prompt → LLM (JSON-schema constrained) → Pydantic
validation → citation validation → optional retry → verdict enforcement.

Verdict enforcement is code-owned: the LLM proposes, the contract disposes.
- COMPLIANT requires evidence_status=EXPLICIT and ≥1 valid verbatim citation.
- Anything else → NOT_COMPLIANT with the intermediate status preserved.
"""

import json
import time

from pydantic import ValidationError

from app.compliance.citation import apply_citation_validation, requires_downgrade
from app.compliance.prompts import (
    EVALUATION_JSON_SCHEMA,
    EVALUATOR_SYSTEM,
    EVALUATOR_USER_TEMPLATE,
    RETRY_SUFFIX,
    build_evidence_block,
)
from app.config import Settings, get_settings
from app.core.errors import LLMError, LLMStructuredOutputError
from app.core.logging import get_logger
from app.llm.provider import LLMProvider
from app.schemas.evaluation import ComplianceEvaluation, EvidenceStatus, Verdict
from app.schemas.evidence import RetrievedEvidence
from app.schemas.rule import ComplianceRule
from app.security.validation import detect_prompt_injection

log = get_logger(__name__)


class ComplianceEvaluator:
    def __init__(self, llm: LLMProvider, settings: Settings | None = None):
        self.llm = llm
        self.settings = settings or get_settings()

    async def evaluate(
        self,
        rule: ComplianceRule,
        evidence: list[RetrievedEvidence],
    ) -> ComplianceEvaluation:
        started = time.perf_counter()

        # Observability: flag injection-looking content in evidence (never blocks).
        for ev in evidence:
            findings = detect_prompt_injection(ev.text)
            if findings:
                log.warning(
                    "prompt_injection_pattern_detected",
                    rule_id=rule.rule_id,
                    chunk_id=ev.chunk_id,
                    patterns=findings,
                )

        # Fast path: no evidence at all → deterministic ABSENT/NOT_COMPLIANT.
        # Still ask the LLM? No — there is nothing to judge; save the call.
        if not evidence:
            evaluation = ComplianceEvaluation(
                rule_id=rule.rule_id,
                verdict=Verdict.NOT_COMPLIANT,
                evidence_status=EvidenceStatus.ABSENT,
                confidence=1.0,
                reasoning=(
                    "No policy evidence was retrieved from the submitted corpus "
                    "for this requirement. The submitted documents do not "
                    "demonstrate compliance."
                ),
                missing_requirements=[rule.requirement],
                recommended_gap=rule.requirement,
            )
            evaluation.validated = True
            return evaluation

        prompt = EVALUATOR_USER_TEMPLATE.format(
            rule_id=rule.rule_id,
            framework=rule.framework,
            criterion=rule.criterion,
            title=rule.title,
            description=rule.description,
            requirement=rule.requirement,
            expected_evidence=", ".join(rule.expected_evidence) or "n/a",
            severity=rule.severity.value,
            evidence_block=build_evidence_block(evidence),
        )

        evaluation: ComplianceEvaluation | None = None
        last_error = ""
        attempts = 1 + self.settings.citation_max_retries
        for attempt in range(attempts):
            attempt_prompt = prompt
            if attempt > 0 and last_error:
                attempt_prompt = prompt + RETRY_SUFFIX.format(reason=last_error)
            try:
                raw = await self.llm.complete(
                    attempt_prompt,
                    system=EVALUATOR_SYSTEM,
                    json_schema=EVALUATION_JSON_SCHEMA,
                )
                evaluation = self._parse(raw, rule.rule_id)
            except (LLMStructuredOutputError, LLMError) as exc:
                last_error = str(exc)
                log.warning(
                    "evaluation_attempt_failed",
                    rule_id=rule.rule_id,
                    attempt=attempt,
                    error=last_error,
                )
                continue

            apply_citation_validation(evaluation, evidence)
            if requires_downgrade(evaluation) and attempt < attempts - 1:
                last_error = (
                    "verdict was COMPLIANT but no citation quote matched the "
                    "evidence text verbatim"
                )
                evaluation = None
                continue
            break

        if evaluation is None:
            raise LLMStructuredOutputError(
                f"Evaluator produced no valid output for {rule.rule_id}: {last_error}"
            )

        self._enforce_verdict(evaluation)
        log.info(
            "rule_evaluated",
            rule_id=rule.rule_id,
            verdict=evaluation.verdict.value,
            evidence_status=evaluation.evidence_status.value,
            confidence=evaluation.confidence,
            citations=len(evaluation.citations),
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            model=self.llm.model_name,
        )
        return evaluation

    # ------------------------------------------------------------------
    def _parse(self, raw: str, rule_id: str) -> ComplianceEvaluation:
        text = raw.strip()
        # tolerate ```json fences even though schema mode should prevent them
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMStructuredOutputError(f"Non-JSON evaluator output: {exc}") from exc
        try:
            evaluation = ComplianceEvaluation.model_validate(data)
        except ValidationError as exc:
            raise LLMStructuredOutputError(f"Evaluator schema violation: {exc}") from exc
        if evaluation.rule_id != rule_id:
            evaluation.rule_id = rule_id  # pin to the rule we asked about
        return evaluation

    def _enforce_verdict(self, evaluation: ComplianceEvaluation) -> None:
        """Code-owned verdict semantics — the LLM cannot override these."""
        # 1. COMPLIANT only from EXPLICIT evidence.
        if evaluation.evidence_status != EvidenceStatus.EXPLICIT:
            if evaluation.verdict == Verdict.COMPLIANT:
                evaluation.validation_notes.append(
                    "verdict downgraded: evidence_status is not EXPLICIT"
                )
            evaluation.verdict = Verdict.NOT_COMPLIANT
        # 2. COMPLIANT requires ≥1 valid verbatim citation.
        if requires_downgrade(evaluation):
            evaluation.verdict = Verdict.NOT_COMPLIANT
            if evaluation.evidence_status == EvidenceStatus.EXPLICIT:
                evaluation.evidence_status = EvidenceStatus.PARTIAL
            evaluation.validation_notes.append(
                "verdict downgraded: no valid verbatim citation"
            )
        # 3. ABSENT evidence can never be COMPLIANT (redundant but explicit).
        if evaluation.evidence_status == EvidenceStatus.ABSENT:
            evaluation.verdict = Verdict.NOT_COMPLIANT
