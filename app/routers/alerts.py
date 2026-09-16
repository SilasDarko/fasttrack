from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.pipeline.ingestion import ingest_alert
from app.schemas import AlertIn, AlertOut

router = APIRouter(tags=["alerts"])


@router.post("/alerts", response_model=AlertOut)
async def post_alert(
    data: AlertIn, response: Response, session: AsyncSession = Depends(get_session)
):
    alert, created, investigation_id = await ingest_alert(session, data)
    await session.commit()
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return AlertOut(
        id=alert.id,
        service=alert.service,
        severity=alert.severity,
        message=alert.message,
        fingerprint=alert.fingerprint,
        status=alert.status,
        investigation_id=investigation_id,
    )
