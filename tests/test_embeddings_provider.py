import pytest

from app.config import Settings
from app.embeddings.deterministic import DeterministicEmbeddingProvider
from app.embeddings.factory import build_embedding_provider
from app.errors import ConfigurationError


async def test_deterministic_embedding_is_repeatable():
    provider = DeterministicEmbeddingProvider(dimensions=64)
    v1 = await provider.embed("connection pool exhausted")
    v2 = await provider.embed("connection pool exhausted")
    assert v1 == v2


async def test_deterministic_embedding_differs_for_different_text():
    provider = DeterministicEmbeddingProvider(dimensions=64)
    v1 = await provider.embed("connection pool exhausted")
    v2 = await provider.embed("completely unrelated text about cats")
    assert v1 != v2


async def test_deterministic_embedding_has_configured_dimensions():
    provider = DeterministicEmbeddingProvider(dimensions=256)
    vector = await provider.embed("anything")
    assert len(vector) == 256


def test_openai_provider_selection_without_api_key_fails_fast():
    settings = Settings(embedding_provider="openai", openai_api_key=None)
    with pytest.raises(ConfigurationError):
        build_embedding_provider(settings)
