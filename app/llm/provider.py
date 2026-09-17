"""LLMProvider abstraction + factory.

Implementations:
- "ollama" → OllamaLLMProvider (default, local, open-source)

Future providers (OpenAI/Anthropic/Azure) plug in here without touching nodes.
"""

from typing import Any, Protocol, runtime_checkable

from app.config import Settings, get_settings
from app.core.errors import LLMError
from app.core.logging import get_logger

log = get_logger(__name__)


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_mode: bool = False,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> str: ...

    async def health(self) -> bool: ...


_provider: LLMProvider | None = None


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    global _provider
    if _provider is not None:
        return _provider
    settings = settings or get_settings()
    kind = settings.llm_provider.lower()
    if kind == "ollama":
        from app.llm.ollama import OllamaLLMProvider
        _provider = OllamaLLMProvider(settings)
    else:
        raise LLMError(f"Unknown LLM provider: {settings.llm_provider}")
    log.info("llm_provider_selected", provider=kind, model=settings.ollama_model)
    return _provider


def reset_llm_provider() -> None:
    global _provider
    _provider = None
