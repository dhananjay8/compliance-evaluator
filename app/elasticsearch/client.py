"""Async Elasticsearch client factory (singleton per process)."""

from elasticsearch import AsyncElasticsearch

from app.config import Settings, get_settings
from app.core.errors import ElasticsearchError
from app.core.logging import get_logger

log = get_logger(__name__)

_client: AsyncElasticsearch | None = None


def get_es_client(settings: Settings | None = None) -> AsyncElasticsearch:
    global _client
    if _client is None:
        settings = settings or get_settings()
        kwargs: dict = {
            "hosts": [settings.elasticsearch_url],
            "request_timeout": settings.es_request_timeout,
        }
        if settings.elasticsearch_username:
            kwargs["basic_auth"] = (
                settings.elasticsearch_username,
                settings.elasticsearch_password or "",
            )
        _client = AsyncElasticsearch(**kwargs)
        log.info("elasticsearch_client_created", url=settings.elasticsearch_url)
    return _client


async def close_es_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def assert_es_available(client: AsyncElasticsearch) -> None:
    try:
        info = await client.info()
        log.info("elasticsearch_connected", cluster=info.get("cluster_name"), version=info.get("version", {}).get("number"))
    except Exception as exc:  # noqa: BLE001
        raise ElasticsearchError(f"Elasticsearch unavailable: {exc}") from exc
