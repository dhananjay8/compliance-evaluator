"""Ollama chat provider with JSON-schema constrained output.

Uses POST /api/chat with `format` set to a JSON schema when provided
(Ollama >= 0.5 structured outputs), falling back to plain `json` mode.
"""

from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings, get_settings
from app.core.errors import LLMError, LLMTimeoutError
from app.core.logging import get_logger

log = get_logger(__name__)


class OllamaLLMProvider:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._model = self.settings.ollama_model
        self._client = httpx.AsyncClient(
            base_url=self.settings.ollama_url,
            timeout=httpx.Timeout(self.settings.llm_timeout_seconds, connect=10.0),
        )

    @property
    def model_name(self) -> str:
        return self._model

    @retry(
        retry=retry_if_exception_type((httpx.TransportError,)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def _chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        resp = await self._client.post("/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_mode: bool = False,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.settings.llm_temperature if temperature is None else temperature,
                "num_ctx": self.settings.llm_num_ctx,
            },
        }
        if json_schema is not None:
            payload["format"] = json_schema
        elif json_mode:
            payload["format"] = "json"

        try:
            data = await self._chat(payload)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"Ollama chat timed out: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"Ollama chat failed: {exc}") from exc

        content = (data.get("message") or {}).get("content", "")
        if not content:
            raise LLMError("Ollama returned empty completion")
        log.debug(
            "ollama_complete",
            model=self._model,
            eval_count=data.get("eval_count"),
            prompt_eval_count=data.get("prompt_eval_count"),
        )
        return content

    async def health(self) -> bool:
        try:
            resp = await self._client.get("/api/tags")
            if resp.status_code != 200:
                return False
            models = {m.get("name", "") for m in resp.json().get("models", [])}
            base = self._model.split(":")[0]
            return any(m == self._model or m.split(":")[0] == base for m in models)
        except Exception:  # noqa: BLE001
            return False
