from openai import AsyncOpenAI

from app.errors import LLMUnavailableError


class OpenAIEmbeddingProvider:
    def __init__(self, api_key: str, model: str, dimensions: int = 1536) -> None:
        self.dimensions = dimensions
        self._model = model
        self._client = AsyncOpenAI(api_key=api_key)

    async def embed(self, text: str) -> list[float]:
        try:
            response = await self._client.embeddings.create(
                model=self._model, input=text, dimensions=self.dimensions
            )
        except Exception as exc:  # openai SDK raises various typed errors
            raise LLMUnavailableError(f"OpenAI embeddings call failed: {exc}") from exc
        return list(response.data[0].embedding)
