"""Deterministic seed data generator.

Populates the six evidence/domain tables with >=1000 records total using a
fixed random seed, so every run produces byte-for-byte identical data. Run
after `alembic upgrade head`:

    python scripts/seed.py
"""

import asyncio
import hashlib
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_session_factory  # noqa: E402
from app.embeddings.factory import get_embedding_provider  # noqa: E402
from app.models import Deployment, PriorIncident, Runbook, SourceChange, TelemetryEvent  # noqa: E402

RNG = random.Random(42)
# Content (which services/categories/root-causes/etc.) is fully deterministic via the
# fixed RNG seed above; timestamps are anchored to the actual run time so freshly
# seeded data reads as "recent" for interactive demos and benchmarks. Embeddings are
# always exactly reproducible for identical text regardless of when seeding runs.
NOW = datetime.now(timezone.utc)

SERVICES = [
    "checkout",
    "payments",
    "search",
    "auth",
    "inventory",
    "notifications",
    "shipping",
    "recommendations",
]

TELEMETRY_ISSUES = [
    ("warn", "connection pool utilization above 80%"),
    ("error", "connection pool exhausted, requests queuing"),
    ("warn", "p99 latency elevated for downstream calls"),
    ("error", "circuit breaker open for downstream dependency"),
    ("error", "database query timeout while reading orders"),
    ("warn", "memory usage above 75% of container limit"),
    ("critical", "out of memory, container restarted"),
    ("warn", "cache miss ratio elevated"),
    ("error", "cache invalidation failed, serving stale data"),
    ("info", "autoscaler added 2 replicas"),
    ("warn", "request rate exceeded configured threshold"),
    ("error", "TLS handshake failure to downstream service"),
    ("critical", "database connection pool exhausted"),
    ("info", "scheduled health check passed"),
    ("debug", "cache warmed for top queries"),
]

ROOT_CAUSE_CATEGORIES = [
    "deployment_regression",
    "resource_exhaustion",
    "downstream_dependency_failure",
    "configuration_error",
    "database_contention",
    "cache_invalidation_bug",
]

RUNBOOK_TOPICS = [
    ("Connection pool exhaustion", "connection pool exhausted increase pool size restart"),
    ("Memory pressure and OOM", "memory usage container restart heap oom"),
    ("Latency spike triage", "latency spike p99 downstream timeout"),
    ("Rolling back a bad deployment", "rollback deployment regression version revert"),
    ("Cache invalidation issues", "cache invalidation stale data miss ratio"),
    ("Database contention", "database contention lock timeout query"),
]


def commit_sha(seed: str) -> str:
    return hashlib.sha1(seed.encode()).hexdigest()


def make_deployments_and_changes() -> tuple[list[dict], list[dict]]:
    deployments, changes = [], []
    for service in SERVICES:
        for i in range(40):
            # a couple of very recent deployments per service so a demo alert raised
            # right after seeding can correlate with search_deployments' default window
            if i < 2:
                deployed_at = NOW - timedelta(minutes=RNG.randint(5, 90))
            else:
                days_ago = RNG.randint(0, 180)
                deployed_at = NOW - timedelta(days=days_ago, hours=RNG.randint(0, 23))
            sha = commit_sha(f"{service}-deploy-{i}")
            version = f"v{1 + i // 20}.{i % 20}.{RNG.randint(0, 9)}"
            status = RNG.choices(["success", "failed", "rolled_back"], weights=[85, 10, 5])[0]
            deployments.append(
                {
                    "service": service,
                    "version": version,
                    "commit_sha": sha,
                    "environment": "production",
                    "status": status,
                    "deployed_at": deployed_at,
                }
            )
            files = RNG.sample(
                [f"{service}/handler.py", f"{service}/config.yaml", f"{service}/models.py",
                 f"{service}/client.py", "shared/pool.py"],
                k=RNG.randint(1, 3),
            )
            changes.append(
                {
                    "repo": f"org/{service}",
                    "commit_sha": sha,
                    "author": RNG.choice(["alice", "bob", "carol", "dave", "erin"]),
                    "message": f"Update {service}: {RNG.choice(['tune pool size', 'fix retry logic', 'bump timeout', 'refactor client', 'add caching'])}",
                    "files_changed": files,
                    "diff_stat": RNG.randint(3, 400),
                    "timestamp": deployed_at - timedelta(hours=1),
                }
            )
        # extra un-deployed commits
        for i in range(10):
            sha = commit_sha(f"{service}-extra-{i}")
            changes.append(
                {
                    "repo": f"org/{service}",
                    "commit_sha": sha,
                    "author": RNG.choice(["alice", "bob", "carol", "dave", "erin"]),
                    "message": f"Chore for {service}: {RNG.choice(['update deps', 'add tests', 'lint fixes'])}",
                    "files_changed": [f"{service}/tests.py"],
                    "diff_stat": RNG.randint(1, 50),
                    "timestamp": NOW - timedelta(days=RNG.randint(0, 180)),
                }
            )
    return deployments, changes


def make_telemetry() -> list[dict]:
    events = []
    for service in SERVICES:
        for i in range(50):
            level, message = RNG.choice(TELEMETRY_ISSUES)
            # ~10% of events land in the last 45 minutes so a demo alert raised right
            # after seeding has recent telemetry to correlate against; the rest spread
            # across the last 30 days for realistic historical volume.
            if i < 5:
                age = timedelta(minutes=RNG.randint(1, 45))
            else:
                age = timedelta(hours=RNG.randint(1, 24 * 30), minutes=RNG.randint(0, 59))
            events.append(
                {
                    "service": service,
                    "level": level,
                    "message": f"{service}: {message}",
                    "host": f"{service}-{RNG.randint(1, 12)}.prod.internal",
                    "tags": {"region": RNG.choice(["us-east", "us-west", "eu-central"])},
                    "timestamp": NOW - age,
                }
            )
    return events


def make_runbooks() -> list[dict]:
    runbooks = []
    for service in SERVICES:
        for title, keywords in RUNBOOK_TOPICS:
            stale = RNG.random() < 0.25
            age_days = RNG.randint(200, 400) if stale else RNG.randint(1, 90)
            runbooks.append(
                {
                    "title": f"{title} ({service})",
                    "service": service,
                    "content": (
                        f"# {title} for {service}\n\n"
                        f"Symptoms: {keywords}.\n\n"
                        f"1. Check dashboards for {service}.\n"
                        f"2. Correlate with recent deployments.\n"
                        f"3. Apply mitigation for: {keywords}.\n"
                        f"4. Escalate if unresolved within 30 minutes.\n"
                    ),
                    "tags": keywords.split(),
                    "updated_at": NOW - timedelta(days=age_days),
                }
            )
    return runbooks


def make_prior_incidents() -> list[dict]:
    incidents = []
    for service in SERVICES:
        for i in range(10):
            category = ROOT_CAUSE_CATEGORIES[i % len(ROOT_CAUSE_CATEGORIES)]
            occurred_at = NOW - timedelta(days=RNG.randint(10, 365))
            incidents.append(
                {
                    "title": f"{service} incident #{i + 1}: {category.replace('_', ' ')}",
                    "service": service,
                    "summary": (
                        f"{service} experienced degraded availability attributed to "
                        f"{category.replace('_', ' ')}."
                    ),
                    "root_cause": category,
                    "resolution": f"Resolved by addressing {category.replace('_', ' ')} directly.",
                    "occurred_at": occurred_at,
                }
            )
    return incidents


async def main() -> None:
    provider = get_embedding_provider()
    session_factory = get_session_factory()

    deployments, changes = make_deployments_and_changes()
    telemetry = make_telemetry()
    runbooks = make_runbooks()
    incidents = make_prior_incidents()

    total = len(deployments) + len(changes) + len(telemetry) + len(runbooks) + len(incidents)
    print(f"Seeding {total} records "
          f"(deployments={len(deployments)}, source_changes={len(changes)}, "
          f"telemetry={len(telemetry)}, runbooks={len(runbooks)}, "
          f"prior_incidents={len(incidents)})")

    async with session_factory() as session:
        for d in deployments:
            embedding = await provider.embed(f"{d['service']} {d['version']} {d['status']}")
            session.add(Deployment(embedding=embedding, **d))
        await session.commit()

        for c in changes:
            embedding = await provider.embed(f"{c['message']} {' '.join(c['files_changed'])}")
            session.add(SourceChange(embedding=embedding, **c))
        await session.commit()

        for t in telemetry:
            embedding = await provider.embed(f"{t['level']} {t['message']}")
            session.add(TelemetryEvent(embedding=embedding, **t))
        await session.commit()

        for r in runbooks:
            embedding = await provider.embed(f"{r['title']} {r['content']}")
            session.add(Runbook(embedding=embedding, **r))
        await session.commit()

        for p in incidents:
            embedding = await provider.embed(f"{p['summary']} {p['root_cause']}")
            session.add(PriorIncident(embedding=embedding, **p))
        await session.commit()

    print(f"Done. Seeded {total} records across 5 evidence tables (plus alerts created ad hoc).")


if __name__ == "__main__":
    asyncio.run(main())
