from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import DiagnosisInvalidError
from app.models import Deployment, PriorIncident, Runbook, SourceChange, TelemetryEvent
from app.reasoning.base import ToolCallRecord
from app.schemas import Diagnosis, EvidenceRef

_EVIDENCE_MODEL = {
    "telemetry": TelemetryEvent,
    "deployment": Deployment,
    "source_change": SourceChange,
    "runbook": Runbook,
    "prior_incident": PriorIncident,
}


def collect_seen_evidence(history: list[ToolCallRecord]) -> set[tuple[str, int]]:
    """Every (type, id) pair actually returned by a successful tool call."""
    seen: set[tuple[str, int]] = set()
    for record in history:
        if record.status != "success" or not record.output:
            continue
        output = record.output
        if record.tool_name == "search_telemetry":
            seen.update(("telemetry", item["id"]) for item in output.get("results", []))
        elif record.tool_name == "search_deployments":
            seen.update(("deployment", item["id"]) for item in output.get("results", []))
        elif record.tool_name == "inspect_change":
            if output.get("change"):
                seen.add(("source_change", output["change"]["id"]))
            if output.get("correlated_deployment"):
                seen.add(("deployment", output["correlated_deployment"]["id"]))
        elif record.tool_name == "retrieve_runbook":
            seen.update(("runbook", item["id"]) for item in output.get("results", []))
        elif record.tool_name == "search_prior_incidents":
            seen.update(("prior_incident", item["id"]) for item in output.get("results", []))
    return seen


async def _exists_in_db(session: AsyncSession, ref: EvidenceRef) -> bool:
    model = _EVIDENCE_MODEL[ref.type]
    stmt = select(exists().where(model.id == ref.id))
    return bool((await session.execute(stmt)).scalar())


async def validate_diagnosis(
    session: AsyncSession, diagnosis: Diagnosis, history: list[ToolCallRecord]
) -> None:
    """Rejects any evidence_ref that wasn't actually returned by a tool call in this
    investigation (anti-hallucination), and, as defense in depth, any that no longer
    resolves to a real database row."""
    seen = collect_seen_evidence(history)

    for ref in diagnosis.evidence_refs:
        if (ref.type, ref.id) not in seen:
            raise DiagnosisInvalidError(
                f"evidence_ref {ref.type}:{ref.id} was not returned by any tool call "
                "in this investigation",
                detail={"type": ref.type, "id": ref.id},
            )

    for ref in diagnosis.evidence_refs:
        if not await _exists_in_db(session, ref):
            raise DiagnosisInvalidError(
                f"evidence_ref {ref.type}:{ref.id} does not exist",
                detail={"type": ref.type, "id": ref.id},
            )
