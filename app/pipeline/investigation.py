import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import LLMOutputInvalidError, LLMUnavailableError
from app.metrics import INVESTIGATIONS, LLM_CALL_DURATION, LLM_CALLS
from app.models import Alert, Investigation
from app.reasoning.base import (
    AlertSnapshot,
    FinalizeAction,
    InvestigationContext,
    ReasoningProvider,
    ToolCallAction,
)
from app.reasoning.factory import get_reasoning_provider
from app.pipeline.diagnosis_validation import validate_diagnosis
from app.errors import DiagnosisInvalidError
from app.tools.executor import execute_tool

_MAX_DIAGNOSIS_ATTEMPTS = 2


async def run_investigation(
    session: AsyncSession,
    investigation: Investigation,
    alert: Alert,
    reasoning: ReasoningProvider | None = None,
) -> Investigation:
    if investigation.status != "pending":
        return investigation

    reasoning = reasoning or get_reasoning_provider()
    provider_name = get_settings().llm_provider
    context = InvestigationContext(
        alert=AlertSnapshot(service=alert.service, severity=alert.severity, message=alert.message)
    )

    diagnosis = None
    for _ in range(get_settings().agent_max_iterations):
        action = await _decide(reasoning, context, provider_name)
        if action is None:
            return await _fail(session, investigation, "llm_unavailable")

        if isinstance(action, ToolCallAction):
            record = await execute_tool(session, investigation.id, action.tool_name, action.arguments)
            context.history.append(record)
            continue

        diagnosis = action.diagnosis
        break
    else:
        return await _fail(session, investigation, "agent_loop_exhausted")

    if diagnosis is None:
        return await _fail(session, investigation, "agent_loop_exhausted")

    attempts = 0
    while True:
        attempts += 1
        try:
            await validate_diagnosis(session, diagnosis, context.history)
            break
        except DiagnosisInvalidError as exc:
            if attempts >= _MAX_DIAGNOSIS_ATTEMPTS:
                return await _fail(session, investigation, "diagnosis_invalid", str(exc))
            action = await _decide(reasoning, context, provider_name)
            if action is None:
                return await _fail(session, investigation, "llm_unavailable")
            if isinstance(action, ToolCallAction):
                return await _fail(session, investigation, "diagnosis_invalid", str(exc))
            diagnosis = action.diagnosis

    investigation.status = "completed"
    investigation.diagnosis = diagnosis.model_dump(mode="json")
    investigation.suggested_action = diagnosis.suggested_action
    INVESTIGATIONS.labels(status="completed").inc()
    await session.flush()
    return investigation


async def _decide(
    reasoning: ReasoningProvider, context: InvestigationContext, provider_name: str
) -> ToolCallAction | FinalizeAction | None:
    start = time.perf_counter()
    try:
        action = await reasoning.decide_next_action(context)
    except (LLMUnavailableError, LLMOutputInvalidError):
        LLM_CALLS.labels(provider=provider_name, status="error").inc()
        LLM_CALL_DURATION.labels(provider=provider_name).observe(time.perf_counter() - start)
        return None
    LLM_CALLS.labels(provider=provider_name, status="success").inc()
    LLM_CALL_DURATION.labels(provider=provider_name).observe(time.perf_counter() - start)
    return action


async def _fail(
    session: AsyncSession, investigation: Investigation, error_code: str, message: str | None = None
) -> Investigation:
    investigation.status = "failed"
    investigation.diagnosis = {"error": error_code, "message": message or error_code}
    INVESTIGATIONS.labels(status="failed").inc()
    await session.flush()
    return investigation
