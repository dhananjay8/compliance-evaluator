#!/usr/bin/env python3
"""Seed compliance rules into Elasticsearch from a JSON dataset.

Usage:
    python scripts/seed_rules.py [path/to/rules.json]
Default: fixtures/rules/soc2_demo_rules.json
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.elasticsearch.client import close_es_client, get_es_client
from app.elasticsearch.store import ElasticsearchStore
from app.schemas.rule import ComplianceRule

log = get_logger("seed_rules")
DEFAULT_DATASET = Path("fixtures/rules/soc2_demo_rules.json")


async def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json=False)
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATASET
    if not path.exists():
        log.error("dataset_not_found", path=str(path))
        return 1

    payload = json.loads(path.read_text())
    rules = [ComplianceRule(**r) for r in payload["rules"]]
    log.info("rules_loaded", count=len(rules), dataset=payload.get("dataset"))

    store = ElasticsearchStore(get_es_client(settings), settings)
    await store.ensure_indices()
    indexed = await store.bulk_index_rules(rules)
    await store.refresh(settings.idx_compliance_rules)
    total = await store.count_rules(settings.default_framework)
    log.info("rules_seeded", indexed=indexed, total_active=total)
    await close_es_client()
    return 0 if indexed == len(rules) else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
