"""AI-powered gap analysis for NOT_COMPLIANT criteria."""

import json

from pydantic import BaseModel, ValidationError

from app.compliance.prompts import GAP_JSON_SCHEMA, GAP_SYSTEM, GAP_USER_TEMPLATE
from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.llm.provider import LLMProvider
from app.schemas.evaluation import ComplianceEvaluation
from app.schemas.gap import GapAnalysis
from app.schemas.rule import ComplianceRule

log = get_logger(__name__)


class _GapPayload(BaseModel):
    gap: str
    missing_requirement: str
    recommendation: str
    expected_evidence: list[str] = []
    severity: str = "MEDIUM"


class GapAnalyzer:
    def __init__(self, llm: LLMProvider, settings: Settings | None = None):
        self.llm = llm
        self.settings = settings or get_settings()

    async def analyze(
        self,
        rule: ComplianceRule,
        evaluation: ComplianceEvaluation,
    ) -> GapAnalysis:
        prompt = GAP_USER_TEMPLATE.format(
            rule_id=rule.rule_id,
            criterion=rule.criterion,
            title=rule.title,
            requirement=rule.requirement,
            expected_evidence=", ".join(rule.expected_evidence) or "n/a",
            severity=rule.severity.value,
            verdict=evaluation.verdict.value,
            evidence_status=evaluation.evidence_status.value,
            missing_requirements="; ".join(evaluation.missing_requirements) or "unspecified",
            reasoning=evaluation.reasoning[:1500],
        )
        try:
            raw = await self.llm.complete(prompt, system=GAP_SYSTEM, json_schema=GAP_JSON_SCHEMA)
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            payload = _GapPayload.model_validate(json.loads(text))
            return GapAnalysis(
                rule_id=rule.rule_id,
                criterion=rule.criterion,
                gap=payload.gap,
                missing_requirement=payload.missing_requirement,
                recommendation=payload.recommendation,
                expected_evidence=payload.expected_evidence or rule.expected_evidence,
                severity=payload.severity,
            )
        except (ValidationError, ValueError) as exc:
            log.warning("gap_analysis_parse_failed", rule_id=rule.rule_id, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            log.warning("gap_analysis_llm_failed", rule_id=rule.rule_id, error=str(exc))

        # Deterministic fallback — never fabricate, always useful.
        missing = "; ".join(evaluation.missing_requirements) or rule.requirement
        return GapAnalysis(
            rule_id=rule.rule_id,
            criterion=rule.criterion,
            gap=(
                f"The submitted policy corpus does not demonstrate compliance with "
                f"{rule.criterion} ({rule.title}). Evidence status: "
                f"{evaluation.evidence_status.value}."
            ),
            missing_requirement=missing,
            recommendation=(
                f"Add explicit policy language addressing: {rule.requirement}"
            ),
            expected_evidence=rule.expected_evidence,
            severity=rule.severity.value,
        )
