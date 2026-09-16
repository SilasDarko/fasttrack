from app.pipeline.investigation import run_investigation
from app.reasoning.base import InvestigationContext, ToolCallAction
from tests.conftest import (
    make_alert,
    make_deployment,
    make_investigation,
    make_prior_incident,
    make_runbook,
    make_source_change,
    make_telemetry,
)


class _NeverFinalizeProvider:
    async def decide_next_action(self, context: InvestigationContext) -> ToolCallAction:
        return ToolCallAction(tool_name="search_telemetry", arguments={"service": "checkout"})


async def test_end_to_end_diagnosis_cites_evidence(session):
    alert = await make_alert(session, service="checkout", message="pool exhausted",
                              fingerprint="fp-inv-1")
    investigation = await make_investigation(session, alert)
    await make_telemetry(session, service="checkout", level="error", minutes_ago=5)
    sha = "c" * 40
    await make_deployment(session, service="checkout", commit_sha=sha, minutes_ago=20)
    await make_source_change(session, commit_sha=sha, service="checkout")
    await make_runbook(session, service="checkout")
    await make_prior_incident(session, service="checkout")
    await session.commit()

    result = await run_investigation(session, investigation, alert)

    assert result.status == "completed"
    assert result.diagnosis["evidence_refs"]
    assert any(ref["type"] == "deployment" for ref in result.diagnosis["evidence_refs"])


async def test_agent_loop_is_bounded_and_fails_gracefully(session):
    alert = await make_alert(session, fingerprint="fp-inv-2")
    investigation = await make_investigation(session, alert)
    await session.commit()

    result = await run_investigation(session, investigation, alert, reasoning=_NeverFinalizeProvider())

    assert result.status == "failed"
    assert result.diagnosis["error"] == "agent_loop_exhausted"


async def test_conflicting_prior_incidents_lowers_confidence(session):
    alert = await make_alert(session, service="checkout", message="pool exhausted",
                              fingerprint="fp-inv-3")
    investigation = await make_investigation(session, alert)
    await make_prior_incident(session, service="checkout", root_cause="resource_exhaustion")
    await make_prior_incident(session, service="checkout", root_cause="configuration_error")
    await session.commit()

    result = await run_investigation(session, investigation, alert)

    assert result.status == "completed"
    assert "conflicting" in result.diagnosis["reasoning"].lower()
    assert result.diagnosis["confidence"] <= 0.4


async def test_missing_deployment_correlation_falls_back_gracefully(session):
    alert = await make_alert(session, service="checkout", message="pool exhausted",
                              fingerprint="fp-inv-4")
    investigation = await make_investigation(session, alert)
    await make_telemetry(session, service="checkout", level="error", minutes_ago=5)
    await session.commit()

    result = await run_investigation(session, investigation, alert)

    assert result.status == "completed"
    assert not any(ref["type"] == "deployment" for ref in result.diagnosis["evidence_refs"])
