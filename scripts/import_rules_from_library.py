#!/usr/bin/env python3
"""Optional: derive compliance rules from a control-library composite index.

Reads `audit_library_control_composite` (written by an external control
library sync service) and maps SOC2 framework controls into the atomic
compliance_rules schema. Use this when a licensed rule corpus is already
published to Elasticsearch; otherwise use seed_rules.py.

Usage:
    python scripts/import_rules_from_library.py [--alias NAME] [--framework SOC2]
"""

import argparse
import asyncio
import sys

sys.path.insert(0, ".")

from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.elasticsearch.client import close_es_client, get_es_client
from app.elasticsearch.store import ElasticsearchStore
from app.schemas.rule import ComplianceRule, Severity

log = get_logger("import_rules")

REQ_LEVEL_TO_SEVERITY = {
    "MANDATORY": Severity.HIGH,
    "RECOMMENDED": Severity.MEDIUM,
    "OPTIONAL": Severity.LOW,
}


def map_control(doc: dict, framework: str) -> ComplianceRule | None:
    code = doc.get("code") or ""
    meta = doc.get("metadata") or {}
    category = doc.get("category") or {}
    criterion = category.get("code") or code.split(".")[0]
    description = meta.get("description") or doc.get("name") or ""
    requirements = meta.get("requirements") or []
    requirement = " ".join(requirements) if requirements else description
    if not requirement:
        return None
    masters = doc.get("masterControls") or []
    keywords = sorted({m.get("domain") for m in masters if m.get("domain")})
    return ComplianceRule(
        rule_id=f"{framework}-{code}",
        framework=framework,
        trust_service_category=(category.get("path") or [{}])[0].get("name", "Security"),
        criterion=criterion,
        sub_attribute=code,
        title=doc.get("name") or code,
        description=description,
        requirement=requirement,
        expected_evidence=[e.get("name") for e in (doc.get("evidence") or []) if e.get("name")],
        keywords=keywords,
        severity=REQ_LEVEL_TO_SEVERITY.get(doc.get("requirementLevel"), Severity.MEDIUM),
        version=(doc.get("framework") or {}).get("version") or "library",
        active=True,
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias", default="audit_library_control_composite")
    parser.add_argument("--framework", default="SOC2")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level, json=False)
    store = ElasticsearchStore(get_es_client(settings), settings)

    rules: list[ComplianceRule] = []
    async for doc in store._scan(
        index=args.alias,
        query={"term": {"framework.code.keyword": args.framework}},
    ):
        rule = map_control(doc, args.framework)
        if rule:
            rules.append(rule)

    if not rules:
        log.error("no_controls_found", alias=args.alias, framework=args.framework)
        return 1

    await store.ensure_indices()
    indexed = await store.bulk_index_rules(rules)
    await store.refresh(settings.idx_compliance_rules)
    log.info("library_rules_imported", indexed=indexed, framework=args.framework)
    await close_es_client()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
