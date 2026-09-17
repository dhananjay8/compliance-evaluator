"""Ollama embeddings provider — uses /api/embed (batched)."""

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings, get_settings
from app.core.errors import EmbeddingError
from app.core.logging import get_logger

log = get_logger(__name__)


class OllamaEmbeddingProvider:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._model = self.settings.embedding_model
        self._dims = self.settings.embedding_dims
        self._client = httpx.AsyncClient(
            base_url=self.settings.ollama_url,
            timeout=self.settings.embedding_timeout_seconds,
        )

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dims(self) -> int:
        return self._dims

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.post("/api/embed", json={"model": self._model, "input": texts})
        resp.raise_for_status()
        data = resp.json()
        return data["embeddings"]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        batch = self.settings.embedding_batch_size
        for i in range(0, len(texts), batch):
            chunk = texts[i:i + batch]
            try:
                vectors.extend(await self._embed_batch(chunk))
            except Exception as exc:  # noqa: BLE001
                raise EmbeddingError(f"Ollama embedding failed: {exc}") from exc
        for vec in vectors:
            if len(vec) != self._dims:
                raise EmbeddingError(
                    f"Embedding dim mismatch: expected {self._dims}, got {len(vec)}. "
                    "Update EMBEDDING_DIMS or the model."
                )
        return vectors

    async def health(self) -> bool:
        try:
            resp = await self._client.get("/api/tags")
            if resp.status_code != 200:
                return False
            models = {m.get("name", "").split(":")[0] for m in resp.json().get("models", [])}
            return self._model.split(":")[0] in models
        except Exception:  # noqa: BLE001
            return False
