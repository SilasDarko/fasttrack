import time

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.embeddings.factory import get_embedding_provider
from app.errors import VectorSearchError
from app.metrics import RETRIEVAL_DURATION, TOOL_EXECUTION_DURATION, TOOL_EXECUTION_FAILURES
from app.models import ToolExecutionLog
from app.reasoning.base import ToolCallRecord
from app.tools.base import TOOL_REGISTRY, ToolDeps

_RETRIEVAL_TOOLS = {
    "retrieve_runbook": "runbook",
    "search_prior_incidents": "prior_incident",
}


async def execute_tool(
    session: AsyncSession, investigation_id: int, tool_name: str, arguments: dict
) -> ToolCallRecord:
    tool = TOOL_REGISTRY.get(tool_name)
    start = time.perf_counter()

    if tool is None:
        return await _fail(session, investigation_id, tool_name, arguments, "unknown_tool", start)

    try:
        params = tool.input_model.model_validate(arguments)
    except PydanticValidationError:
        return await _fail(session, investigation_id, tool_name, arguments, "invalid_input", start)

    deps = ToolDeps(settings=get_settings(), embedding_provider=get_embedding_provider())

    try:
        raw_output = await tool.handler(session, params, deps)
    except VectorSearchError:
        return await _fail(
            session, investigation_id, tool_name, arguments, "vector_search_failed", start
        )
    except (OperationalError, DBAPIError, TimeoutError):
        return await _fail(
            session, investigation_id, tool_name, arguments, "database_unavailable", start
        )

    try:
        validated = tool.output_model.model_validate(raw_output)
    except PydanticValidationError:
        return await _fail(
            session, investigation_id, tool_name, arguments, "tool_output_invalid", start
        )

    latency_ms = (time.perf_counter() - start) * 1000
    output_dict = validated.model_dump(mode="json")
    input_dict = params.model_dump(mode="json")

    await _persist_log(
        session, investigation_id, tool_name, input_dict, output_dict, "success", None, latency_ms
    )
    TOOL_EXECUTION_DURATION.labels(tool_name=tool_name).observe(latency_ms / 1000)
    if tool_name in _RETRIEVAL_TOOLS:
        RETRIEVAL_DURATION.labels(evidence_type=_RETRIEVAL_TOOLS[tool_name]).observe(
            latency_ms / 1000
        )

    return ToolCallRecord(tool_name=tool_name, input=input_dict, output=output_dict, status="success")


async def _fail(
    session: AsyncSession,
    investigation_id: int,
    tool_name: str,
    arguments: dict,
    error_code: str,
    start: float,
) -> ToolCallRecord:
    latency_ms = (time.perf_counter() - start) * 1000
    await _persist_log(
        session, investigation_id, tool_name, arguments, None, "error", error_code, latency_ms
    )
    TOOL_EXECUTION_DURATION.labels(tool_name=tool_name).observe(latency_ms / 1000)
    TOOL_EXECUTION_FAILURES.labels(tool_name=tool_name, error_code=error_code).inc()
    if tool_name in _RETRIEVAL_TOOLS:
        RETRIEVAL_DURATION.labels(evidence_type=_RETRIEVAL_TOOLS[tool_name]).observe(
            latency_ms / 1000
        )
    return ToolCallRecord(
        tool_name=tool_name, input=arguments, output=None, status="error", error_code=error_code
    )


async def _persist_log(
    session: AsyncSession,
    investigation_id: int,
    tool_name: str,
    input_json: dict,
    output_json: dict | None,
    status: str,
    error_code: str | None,
    latency_ms: float,
) -> None:
    session.add(
        ToolExecutionLog(
            investigation_id=investigation_id,
            tool_name=tool_name,
            input_json=input_json,
            output_json=output_json,
            status=status,
            error_code=error_code,
            latency_ms=latency_ms,
        )
    )
    await session.flush()
