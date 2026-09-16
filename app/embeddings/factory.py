from functools import lru_cache

from app.config import Settings, get_settings
from app.embeddings.base import EmbeddingProvider
from app.embeddings.deterministic import DeterministicEmbeddingProvider
from app.errors import ConfigurationError


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "deterministic":
        return DeterministicEmbeddingProvider(dimensions=settings.embedding_dimensions)

    if settings.embedding_provider == "openai":
        if not settings.openai_api_key:
            raise ConfigurationError(
                "EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY to be set",
                detail={"provider": "openai"},
            )
        from app.embeddings.openai_provider import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_embedding_model,
            dimensions=settings.embedding_dimensions,
        )

    raise ConfigurationError(f"Unknown EMBEDDING_PROVIDER: {settings.embedding_provider}")


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    return build_embedding_provider(get_settings())
