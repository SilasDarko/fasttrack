from typing import Protocol


class EmbeddingProvider(Protocol):
    """Turns text into a fixed-dimension vector for pgvector storage/search."""

    dimensions: int

    async def embed(self, text: str) -> list[float]: ...
