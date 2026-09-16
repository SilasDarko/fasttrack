from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.embeddings.base import EmbeddingProvider


@dataclass(frozen=True)
class ToolDeps:
    settings: Settings
    embedding_provider: EmbeddingProvider


Handler = Callable[[AsyncSession, BaseModel, ToolDeps], Awaitable[dict]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Handler

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


TOOL_REGISTRY: dict[str, Tool] = {}


def register_tool(tool: Tool) -> None:
    TOOL_REGISTRY[tool.name] = tool


def all_tool_schemas() -> list[dict]:
    return [tool.openai_schema() for tool in TOOL_REGISTRY.values()]
