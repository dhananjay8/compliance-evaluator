#!/usr/bin/env python3
"""Create all Elasticsearch indices. Safe to re-run (idempotent)."""

import asyncio
import sys

sys.path.insert(0, ".")

from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.elasticsearch.client import close_es_client, get_es_client
from app.elasticsearch.store import ElasticsearchStore

log = get_logger("create_indices")


async def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json=False)
    store = ElasticsearchStore(get_es_client(settings), settings)
    if not await store.ping():
        log.error("elasticsearch_unreachable", url=settings.elasticsearch_url)
        return 1
    created = await store.ensure_indices()
    log.info("done", created=created or "all indices already exist")
    await close_es_client()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
