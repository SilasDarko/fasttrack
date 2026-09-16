from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://fasttrack:fasttrack@localhost:5432/fasttrack"

    embedding_provider: Literal["deterministic", "openai"] = "deterministic"
    llm_provider: Literal["deterministic", "openai"] = "deterministic"

    openai_api_key: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"
    openai_chat_model: str = "gpt-4o-mini"

    embedding_dimensions: int = 1536

    agent_max_iterations: int = 6
    runbook_stale_days: int = 180

    tool_default_limit: int = 10
    tool_max_limit: int = 20
    retrieval_max_limit: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
