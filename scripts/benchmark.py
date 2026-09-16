"""Benchmarks p50/p95 latency for three backend paths, using the deterministic
(no-network) provider so results measure FastTrack's own code, not OpenAI's:

  - retrieval:          the retrieve_runbook tool called directly in-process
                         (real Postgres + pgvector + embedding, no HTTP/ASGI)
  - incident-analysis:  POST /investigations/{id}/run over real HTTP, running
                         the full bounded agent loop against seeded data
  - ingestion:           POST /telemetry over real HTTP

Requires the API to be reachable at --base-url (default http://localhost:8000)
and the database to already be seeded (`python scripts/seed.py`). Run:

    python scripts/benchmark.py
"""

import argparse
import asyncio
import statistics
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_session_factory  # noqa: E402
from app.models import Alert, Investigation  # noqa: E402
from app.tools.executor import execute_tool  # noqa: E402


def _percentiles(samples_ms: list[float]) -> dict:
    ordered = sorted(samples_ms)
    return {
        "n": len(ordered),
        "p50_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[int(len(ordered) * 0.95) - 1], 2),
        "max_ms": round(ordered[-1], 2),
    }


async def bench_retrieval(n: int, warmup: int) -> list[float]:
    factory = get_session_factory()
    async with factory() as session:
        alert = Alert(service="checkout", severity="high", message="pool exhausted",
                       fingerprint=f"bench-retrieval-{uuid.uuid4()}")
        session.add(alert)
        await session.flush()
        investigation = Investigation(alert_id=alert.id, status="pending")
        session.add(investigation)
        await session.flush()

        for _ in range(warmup):
            await execute_tool(session, investigation.id, "retrieve_runbook",
                                {"service": "checkout", "query": "pool exhausted"})

        samples = []
        for _ in range(n):
            start = time.perf_counter()
            await execute_tool(session, investigation.id, "retrieve_runbook",
                                {"service": "checkout", "query": "pool exhausted"})
            samples.append((time.perf_counter() - start) * 1000)
        await session.commit()
    return samples


async def bench_ingestion(client: httpx.AsyncClient, n: int, warmup: int) -> list[float]:
    def payload():
        return {
            "service": "checkout",
            "level": "info",
            "message": "benchmark telemetry event",
            "host": "bench-host",
            "timestamp": "2026-01-01T00:00:00Z",
        }

    for _ in range(warmup):
        await client.post("/telemetry", json=payload())

    samples = []
    for _ in range(n):
        start = time.perf_counter()
        resp = await client.post("/telemetry", json=payload())
        samples.append((time.perf_counter() - start) * 1000)
        resp.raise_for_status()
    return samples


async def bench_incident_analysis(client: httpx.AsyncClient, n: int, warmup: int) -> list[float]:
    async def run_once() -> float:
        alert_resp = await client.post(
            "/alerts",
            json={
                "service": "checkout",
                "severity": "high",
                "message": "connection pool exhausted",
                "fingerprint": f"bench-analysis-{uuid.uuid4()}",
            },
        )
        alert_resp.raise_for_status()
        investigation_id = alert_resp.json()["investigation_id"]

        start = time.perf_counter()
        run_resp = await client.post(f"/investigations/{investigation_id}/run")
        elapsed = (time.perf_counter() - start) * 1000
        run_resp.raise_for_status()
        return elapsed

    for _ in range(warmup):
        await run_once()

    return [await run_once() for _ in range(n)]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    args = parser.parse_args()

    results = {}
    results["retrieval"] = _percentiles(await bench_retrieval(args.n, args.warmup))

    async with httpx.AsyncClient(base_url=args.base_url, timeout=30.0) as client:
        results["ingestion"] = _percentiles(await bench_ingestion(client, args.n, args.warmup))
        results["incident_analysis"] = _percentiles(
            await bench_incident_analysis(client, args.n, args.warmup)
        )

    print(f"{'path':<20} {'n':>5} {'p50_ms':>10} {'p95_ms':>10} {'max_ms':>10}")
    for name, stats in results.items():
        print(f"{name:<20} {stats['n']:>5} {stats['p50_ms']:>10} {stats['p95_ms']:>10} {stats['max_ms']:>10}")


if __name__ == "__main__":
    asyncio.run(main())
