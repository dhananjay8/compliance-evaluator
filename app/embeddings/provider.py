"""EmbeddingProvider abstraction + factory.

Providers:
- "ollama"               → OllamaEmbeddingProvider (default; no torch dependency)
- "sentence_transformers"→ SentenceTransformerProvider (optional extra: pip install .[st])
"""

from typing import Protocol, runtime_checkable

from app.config import Settings, get_settings
from app.core.errors import EmbeddingError
from app.core.logging import get_logger

log = get_logger(__name__)


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dims(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def health(self) -> bool: ...


_provider: EmbeddingProvider | None = None


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    global _provider
    if _provider is not None:
        return _provider
    settings = settings or get_settings()
    kind = settings.embedding_provider.lower()
    if kind == "ollama":
        from app.embeddings.ollama import OllamaEmbeddingProvider
        _provider = OllamaEmbeddingProvider(settings)
    elif kind in ("sentence_transformers", "sentence-transformers", "st"):
        from app.embeddings.sentence_transformer import SentenceTransformerProvider
        _provider = SentenceTransformerProvider(settings)
    else:
        raise EmbeddingError(f"Unknown embedding provider: {settings.embedding_provider}")
    log.info("embedding_provider_selected", provider=kind, model=settings.embedding_model)
    return _provider


def reset_embedding_provider() -> None:
    global _provider
    _provider = None
