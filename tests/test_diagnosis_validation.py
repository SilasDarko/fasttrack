import pytest

from app.errors import DiagnosisInvalidError
from app.pipeline.investigation import run_investigation
from app.pipeline.diagnosis_validation import validate_diagnosis
from app.reasoning.base import FinalizeAction, InvestigationContext, ToolCallAction, ToolCallRecord
from app.schemas import Diagnosis, EvidenceRef
from tests.conftest import make_alert, make_investigation, make_telemetry


def _diagnosis(evidence_refs):
    return Diagnosis(
        root_cause="test",
        confidence=0.5,
        evidence_refs=evidence_refs,
        suggested_action="test",
        reasoning="test",
    )


async def test_valid_evidence_refs_pass_validation(session):
    event = await make_telemetry(session)
    await session.commit()

    history = [
        ToolCallRecord(
            tool_name="search_telemetry",
            input={"service": "checkout"},
            output={"results": [{"id": event.id, "service": "checkout", "level": "error",
                                  "message": "x", "host": "h", "timestamp": "2026-01-01T00:00:00Z",
                                  "tags": {}}]},
            status="success",
        )
    ]
    diagnosis = _diagnosis([EvidenceRef(type="telemetry", id=event.id)])
    await validate_diagnosis(session, diagnosis, history)  # should not raise


async def test_evidence_ref_never_returned_by_a_tool_is_rejected(session):
    event = await make_telemetry(session)
    await session.commit()

    diagnosis = _diagnosis([EvidenceRef(type="telemetry", id=event.id)])
    with pytest.raises(DiagnosisInvalidError):
        await validate_diagnosis(session, diagnosis, history=[])


async def test_evidence_ref_to_nonexistent_db_row_is_rejected(session):
    history = [
        ToolCallRecord(
            tool_name="search_telemetry",
            input={"service": "checkout"},
            output={"results": [{"id": 999999, "service": "checkout", "level": "error",
                                  "message": "x", "host": "h", "timestamp": "2026-01-01T00:00:00Z",
                                  "tags": {}}]},
            status="success",
        )
    ]
    diagnosis = _diagnosis([EvidenceRef(type="telemetry", id=999999)])
    with pytest.raises(DiagnosisInvalidError):
        await validate_diagnosis(session, diagnosis, history)


async def test_one_retry_attempt_before_failing_investigation(session):
    event = await make_telemetry(session)
    await session.commit()

    bad = FinalizeAction(diagnosis=_diagnosis([EvidenceRef(type="telemetry", id=999999)]))
    good = FinalizeAction(diagnosis=_diagnosis([EvidenceRef(type="telemetry", id=event.id)]))

    class _FlakyProvider:
        def __init__(self):
            self.calls = 0

        async def decide_next_action(self, context: InvestigationContext):
            self.calls += 1
            if self.calls == 1:
                return ToolCallAction(
                    tool_name="search_telemetry", arguments={"service": "checkout"}
                )
            if self.calls == 2:
                return bad
            return good

    alert = await make_alert(session, fingerprint="fp-diag-1")
    investigation = await make_investigation(session, alert)
    await session.commit()

    result = await run_investigation(session, investigation, alert, reasoning=_FlakyProvider())

    assert result.status == "completed"


async def test_diagnosis_invalid_after_retry_marks_investigation_failed(session):
    bad = FinalizeAction(diagnosis=_diagnosis([EvidenceRef(type="telemetry", id=999999)]))

    class _AlwaysBadProvider:
        async def decide_next_action(self, context: InvestigationContext):
            return bad

    alert = await make_alert(session, fingerprint="fp-diag-2")
    investigation = await make_investigation(session, alert)
    await session.commit()

    result = await run_investigation(session, investigation, alert, reasoning=_AlwaysBadProvider())

    assert result.status == "failed"
    assert result.diagnosis["error"] == "diagnosis_invalid"
