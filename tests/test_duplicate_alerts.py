import asyncio

from sqlalchemy import func, select

from app.models import Alert, Investigation


async def test_duplicate_fingerprint_does_not_create_second_investigation(client, session):
    payload = {
        "service": "checkout",
        "severity": "high",
        "message": "pool exhausted",
        "fingerprint": "dup-1",
    }
    await client.post("/alerts", json=payload)
    await client.post("/alerts", json=payload)

    count = (
        await session.execute(select(func.count()).select_from(Investigation))
    ).scalar_one()
    assert count == 1


async def test_duplicate_alert_returns_200_not_201(client):
    payload = {
        "service": "checkout",
        "severity": "high",
        "message": "pool exhausted",
        "fingerprint": "dup-2",
    }
    first = await client.post("/alerts", json=payload)
    second = await client.post("/alerts", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


async def test_concurrent_duplicate_ingestion_is_handled_by_unique_constraint(client, session):
    payload = {
        "service": "checkout",
        "severity": "high",
        "message": "pool exhausted",
        "fingerprint": "dup-3",
    }
    results = await asyncio.gather(
        *(client.post("/alerts", json=payload) for _ in range(5)),
        return_exceptions=True,
    )

    ok_responses = [r for r in results if not isinstance(r, Exception)]
    assert len(ok_responses) == 5
    for r in ok_responses:
        assert r.status_code in (200, 201)
    assert sum(1 for r in ok_responses if r.status_code == 201) == 1

    count = (
        await session.execute(select(func.count()).select_from(Alert))
    ).scalar_one()
    assert count == 1
