from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import ConflictError, NotFoundError
from app.models import Alert, Approval, Investigation, ToolExecutionLog
from app.pipeline.investigation import run_investigation
from app.pipeline.postmortem import generate_postmortem
from app.schemas import Diagnosis, InvestigationOut, ToolCallSummary

router = APIRouter(prefix="/investigations", tags=["investigations"])


async def _load(session: AsyncSession, investigation_id: int) -> tuple[Investigation, Alert]:
    investigation = await session.get(Investigation, investigation_id)
    if investigation is None:
        raise NotFoundError(f"Investigation {investigation_id} not found")
    alert = await session.get(Alert, investigation.alert_id)
    return investigation, alert


async def _to_out(session: AsyncSession, investigation: Investigation) -> InvestigationOut:
    rows = (
        await session.execute(
            select(ToolExecutionLog)
            .where(ToolExecutionLog.investigation_id == investigation.id)
            .order_by(ToolExecutionLog.id)
        )
    ).scalars().all()
    diagnosis = None
    if investigation.diagnosis and "error" not in investigation.diagnosis:
        diagnosis = Diagnosis.model_validate(investigation.diagnosis)
    return InvestigationOut(
        id=investigation.id,
        alert_id=investigation.alert_id,
        status=investigation.status,
        diagnosis=diagnosis,
        suggested_action=investigation.suggested_action,
        postmortem_draft=investigation.postmortem_draft,
        tool_calls=[
            ToolCallSummary(tool_name=r.tool_name, status=r.status, latency_ms=r.latency_ms)
            for r in rows
        ],
    )


@router.get("/{investigation_id}", response_model=InvestigationOut)
async def get_investigation(investigation_id: int, session: AsyncSession = Depends(get_session)):
    investigation, _ = await _load(session, investigation_id)
    return await _to_out(session, investigation)


@router.post("/{investigation_id}/run", response_model=InvestigationOut)
async def post_run(investigation_id: int, session: AsyncSession = Depends(get_session)):
    investigation, alert = await _load(session, investigation_id)
    investigation = await run_investigation(session, investigation, alert)
    await session.commit()
    return await _to_out(session, investigation)


async def _decide(
    session: AsyncSession, investigation_id: int, decision: str
) -> InvestigationOut:
    investigation, alert = await _load(session, investigation_id)

    existing = (
        await session.execute(
            select(Approval).where(
                Approval.investigation_id == investigation_id,
                Approval.action == "suggested_action",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return await _to_out(session, investigation)

    if investigation.status != "completed":
        raise ConflictError(
            f"Investigation {investigation_id} has no diagnosis to {decision}",
            detail={"status": investigation.status},
        )

    session.add(
        Approval(investigation_id=investigation_id, action="suggested_action", decision=decision)
    )
    if decision == "approved":
        investigation.postmortem_draft = generate_postmortem(alert, investigation)
        investigation.status = "approved"
    else:
        investigation.status = "rejected"

    await session.flush()
    await session.commit()
    return await _to_out(session, investigation)


@router.post("/{investigation_id}/approve", response_model=InvestigationOut)
async def post_approve(investigation_id: int, session: AsyncSession = Depends(get_session)):
    return await _decide(session, investigation_id, "approved")


@router.post("/{investigation_id}/reject", response_model=InvestigationOut)
async def post_reject(investigation_id: int, session: AsyncSession = Depends(get_session)):
    return await _decide(session, investigation_id, "rejected")
