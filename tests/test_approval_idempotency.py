from app.pipeline.investigation import run_investigation
from tests.conftest import make_alert, make_investigation, make_telemetry


async def _completed_investigation(session, fingerprint):
    alert = await make_alert(session, service="checkout", message="pool exhausted",
                              fingerprint=fingerprint)
    investigation = await make_investigation(session, alert)
    await make_telemetry(session, service="checkout")
    await session.commit()
    result = await run_investigation(session, investigation, alert)
    await session.commit()
    return alert, result


async def test_approve_transitions_status_and_generates_postmortem(session, client):
    alert, investigation = await _completed_investigation(session, "fp-appr-1")

    resp = await client.post(f"/investigations/{investigation.id}/approve")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["postmortem_draft"] is not None
    assert "Postmortem" in body["postmortem_draft"]


async def test_reject_transitions_status_without_postmortem(session, client):
    alert, investigation = await _completed_investigation(session, "fp-appr-2")

    resp = await client.post(f"/investigations/{investigation.id}/reject")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["postmortem_draft"] is None


async def test_re_approving_is_idempotent(session, client):
    alert, investigation = await _completed_investigation(session, "fp-appr-3")

    first = await client.post(f"/investigations/{investigation.id}/approve")
    second = await client.post(f"/investigations/{investigation.id}/approve")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["postmortem_draft"] == second.json()["postmortem_draft"]


async def test_approve_unknown_or_unready_investigation_returns_structured_errors(client, session):
    not_found = await client.post("/investigations/999999/approve")
    assert not_found.status_code == 404
    assert not_found.json()["error_code"] == "not_found"

    alert = await make_alert(session, fingerprint="fp-appr-4")
    investigation = await make_investigation(session, alert, status="pending")
    await session.commit()

    not_ready = await client.post(f"/investigations/{investigation.id}/approve")
    assert not_ready.status_code == 409
    assert not_ready.json()["error_code"] == "conflict"
