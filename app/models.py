from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.db import Base

_DIM = get_settings().embedding_dimensions


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Evidence / domain tables
# ---------------------------------------------------------------------------


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    level: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    host: Mapped[str] = mapped_column(String(128))
    tags: Mapped[dict] = mapped_column(JSON, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(_DIM))


class Deployment(Base):
    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(64))
    commit_sha: Mapped[str] = mapped_column(String(40), index=True)
    environment: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    deployed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(_DIM))


class SourceChange(Base):
    __tablename__ = "source_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo: Mapped[str] = mapped_column(String(128))
    commit_sha: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    author: Mapped[str] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text)
    files_changed: Mapped[list] = mapped_column(JSON, default=list)
    diff_stat: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(_DIM))


class Runbook(Base):
    __tablename__ = "runbooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    service: Mapped[str] = mapped_column(String(128), index=True)
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    embedding: Mapped[list[float]] = mapped_column(Vector(_DIM))


class PriorIncident(Base):
    __tablename__ = "prior_incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    service: Mapped[str] = mapped_column(String(128), index=True)
    summary: Mapped[str] = mapped_column(Text)
    root_cause: Mapped[str] = mapped_column(String(64), index=True)
    resolution: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    embedding: Mapped[list[float]] = mapped_column(Vector(_DIM))


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    status: Mapped[str] = mapped_column(String(32), default="open")

    investigations: Mapped[list["Investigation"]] = relationship(back_populates="alert")


# ---------------------------------------------------------------------------
# Workflow / state tables
# ---------------------------------------------------------------------------


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    diagnosis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    suggested_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    postmortem_draft: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    alert: Mapped["Alert"] = relationship(back_populates="investigations")
    tool_calls: Mapped[list["ToolExecutionLog"]] = relationship(back_populates="investigation")
    approvals: Mapped[list["Approval"]] = relationship(back_populates="investigation")


class ToolExecutionLog(Base):
    __tablename__ = "tool_execution_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("investigations.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(64), index=True)
    input_json: Mapped[dict] = mapped_column(JSON)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    investigation: Mapped["Investigation"] = relationship(back_populates="tool_calls")


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("investigations.id"), index=True)
    action: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(16))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    investigation: Mapped["Investigation"] = relationship(back_populates="approvals")
