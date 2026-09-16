from functools import lru_cache

from app.config import Settings, get_settings
from app.errors import ConfigurationError
from app.reasoning.base import ReasoningProvider
from app.reasoning.deterministic import DeterministicReasoningProvider


def build_reasoning_provider(settings: Settings) -> ReasoningProvider:
    if settings.llm_provider == "deterministic":
        return DeterministicReasoningProvider()

    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise ConfigurationError(
                "LLM_PROVIDER=openai requires OPENAI_API_KEY to be set",
                detail={"provider": "openai"},
            )
        import app.tools  # noqa: F401  (registers the 5 tools)
        from app.reasoning.openai_provider import OpenAIReasoningProvider
        from app.tools.base import all_tool_schemas

        return OpenAIReasoningProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_chat_model,
            tool_schemas=all_tool_schemas(),
        )

    raise ConfigurationError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")


@lru_cache
def get_reasoning_provider() -> ReasoningProvider:
    return build_reasoning_provider(get_settings())
