import pytest

from app.config import Settings
from app.errors import ConfigurationError, LLMUnavailableError
from app.pipeline.investigation import run_investigation
from app.reasoning.base import FinalizeAction, InvestigationContext, ToolCallAction
from app.reasoning.factory import build_reasoning_provider
from app.schemas import Diagnosis
from tests.conftest import make_alert, make_investigation


class _AlwaysTimesOutProvider:
    async def decide_next_action(self, context: InvestigationContext):
        raise LLMUnavailableError("simulated OpenAI timeout")


class _BadArgsOnceThenFinalizeProvider:
    def __init__(self):
        self.calls = 0

    async def decide_next_action(self, context: InvestigationContext):
        self.calls += 1
        if self.calls == 1:
            # missing the required "service" field
            return ToolCallAction(tool_name="search_telemetry", arguments={})
        return FinalizeAction(
            diagnosis=Diagnosis(
                root_cause="no evidence needed",
                confidence=0.2,
                evidence_refs=[],
                suggested_action="escalate",
                reasoning="tool call failed, finalized without evidence",
            )
        )


async def test_llm_timeout_marks_investigation_failed_with_structured_reason(session):
    alert = await make_alert(session, fingerprint="fp-llm-1")
    investigation = await make_investigation(session, alert)
    await session.commit()

    result = await run_investigation(session, investigation, alert, reasoning=_AlwaysTimesOutProvider())

    assert result.status == "failed"
    assert result.diagnosis["error"] == "llm_unavailable"


async def test_malformed_tool_call_arguments_handled_without_crash(session):
    alert = await make_alert(session, fingerprint="fp-llm-2")
    investigation = await make_investigation(session, alert)
    await session.commit()

    result = await run_investigation(
        session, investigation, alert, reasoning=_BadArgsOnceThenFinalizeProvider()
    )

    assert result.status == "completed"


def test_missing_api_key_fails_fast_instead_of_silent_fallback():
    settings = Settings(llm_provider="openai", openai_api_key=None)
    with pytest.raises(ConfigurationError):
        build_reasoning_provider(settings)
