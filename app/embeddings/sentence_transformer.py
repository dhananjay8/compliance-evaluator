"""Sentence-Transformers embedding provider (optional extra).

Install with: pip install .[st]
Default model: BAAI/bge-small-en-v1.5 (384 dims) — set EMBEDDING_DIMS accordingly.
"""

import asyncio

from app.config import Settings, get_settings
from app.core.errors import EmbeddingError
from app.core.logging import get_logger

log = get_logger(__name__)


class SentenceTransformerProvider:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingError(
                "sentence-transformers not installed. Use EMBEDDING_PROVIDER=ollama "
                "or install the 'st' extra."
            ) from exc
        self._model_name = self.settings.embedding_model
        self._model = SentenceTransformer(self._model_name)
        self._dims = self._model.get_sentence_embedding_dimension()
        if self._dims != self.settings.embedding_dims:
            log.warning(
                "embedding_dims_mismatch",
                configured=self.settings.embedding_dims,
                actual=self._dims,
            )

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dims(self) -> int:
        return self._dims

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            vectors = await asyncio.to_thread(
                self._model.encode, texts, normalize_embeddings=True, show_progress_bar=False
            )
            return [v.tolist() for v in vectors]
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(f"sentence-transformers embedding failed: {exc}") from exc

    async def health(self) -> bool:
        return self._model is not None
