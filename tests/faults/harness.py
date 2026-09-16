"""Test-only fault injection seams.

Nothing here ships in app/ -- production code only exposes ordinary dependency
seams (a `session` parameter, provider factories, the tool registry). These
helpers monkeypatch those seams to reproduce specific, realistic failure
conditions so tests/test_fault_injection.py can verify the application
degrades gracefully (structured error, no crash, correct logging/metrics)
instead of propagating an unhandled exception.
"""

from sqlalchemy.exc import DBAPIError, OperationalError

from app.embeddings.factory import get_embedding_provider
from app.tools.base import TOOL_REGISTRY, Tool


def operational_timeout_error() -> OperationalError:
    """What SQLAlchemy raises for a real statement/connection timeout."""
    return OperationalError(
        "SELECT ...", {}, Exception("canceling statement due to statement timeout")
    )


def vector_operator_unavailable_error() -> DBAPIError:
    """Mirrors the real Postgres error when the pgvector extension/operator
    cannot be resolved (e.g. the extension was dropped or is not installed):
    `operator does not exist: vector <=> vector`."""
    return DBAPIError(
        "SELECT ... embedding <=> ...", {}, Exception("operator does not exist: vector <=> vector")
    )


def make_raiser(exc: Exception):
    async def _raise(*args, **kwargs):
        raise exc

    return _raise


def patch_session_execute(monkeypatch, session, exc: Exception) -> None:
    monkeypatch.setattr(session, "execute", make_raiser(exc))


def patch_session_flush(monkeypatch, session, exc: Exception) -> None:
    """For pipeline code paths (ingestion) that write via session.add() +
    session.flush() rather than an explicit session.execute() select."""
    monkeypatch.setattr(session, "flush", make_raiser(exc))


def corrupt_query_embedding_dimension(monkeypatch, wrong_dimensions: int = 64) -> None:
    """Makes the next embed() call return a vector of the wrong length, so a
    real pgvector query against the 1536-dim columns genuinely raises
    Postgres's own "different vector dimensions" error -- no exception is
    fabricated, the database raises it for real."""
    provider = get_embedding_provider()
    original = provider.embed_sync

    async def _wrong_dim_embed(text: str):
        return original(text)[:wrong_dimensions]

    monkeypatch.setattr(provider, "embed", _wrong_dim_embed)


def patch_tool_output(monkeypatch, tool_name: str, bad_output: dict) -> None:
    """Forces a specific tool's handler to return schema-violating output,
    to exercise the executor's output-validation error path."""
    real_tool = TOOL_REGISTRY[tool_name]

    async def _broken_handler(session, params, deps):
        return bad_output

    monkeypatch.setitem(
        TOOL_REGISTRY,
        tool_name,
        Tool(
            name=real_tool.name,
            description=real_tool.description,
            input_model=real_tool.input_model,
            output_model=real_tool.output_model,
            handler=_broken_handler,
        ),
    )


class TimesOutReasoningProvider:
    """A reasoning provider that raises LLMUnavailableError after N tool
    calls, simulating an OpenAI timeout partway through an investigation."""

    def __init__(self, fail_after_calls: int = 0):
        self._remaining_ok_calls = fail_after_calls
        self._deterministic = None

    async def decide_next_action(self, context):
        from app.errors import LLMUnavailableError
        from app.reasoning.deterministic import DeterministicReasoningProvider

        if self._remaining_ok_calls <= 0:
            raise LLMUnavailableError("simulated OpenAI timeout")
        self._remaining_ok_calls -= 1
        if self._deterministic is None:
            self._deterministic = DeterministicReasoningProvider()
        return await self._deterministic.decide_next_action(context)


class UnavailableDependencyReasoningProvider:
    """Simulates a generic downstream dependency (e.g. a config service, a
    secrets store) being unavailable to the agent loop itself."""

    async def decide_next_action(self, context):
        from app.errors import DependencyUnavailableError

        raise DependencyUnavailableError("simulated dependency outage")
