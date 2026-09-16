from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

EvidenceType = Literal[
    "telemetry", "deployment", "source_change", "runbook", "prior_incident"
]

# ---------------------------------------------------------------------------
# Ingestion request DTOs
# ---------------------------------------------------------------------------


class TelemetryIn(BaseModel):
    service: str
    level: Literal["debug", "info", "warn", "error", "critical"]
    message: str
    host: str
    tags: dict = Field(default_factory=dict)
    timestamp: datetime


class DeploymentIn(BaseModel):
    service: str
    version: str
    commit_sha: str = Field(min_length=7, max_length=40)
    environment: str
    status: Literal["success", "failed", "rolled_back"]
    deployed_at: datetime


class SourceChangeIn(BaseModel):
    repo: str
    commit_sha: str = Field(min_length=7, max_length=40)
    author: str
    message: str
    files_changed: list[str] = Field(default_factory=list)
    diff_stat: int = Field(ge=0)
    timestamp: datetime


class RunbookIn(BaseModel):
    title: str
    service: str
    content: str
    tags: list[str] = Field(default_factory=list)
    updated_at: datetime


class PriorIncidentIn(BaseModel):
    title: str
    service: str
    summary: str
    root_cause: str
    resolution: str
    occurred_at: datetime


class AlertIn(BaseModel):
    service: str
    severity: Literal["low", "medium", "high", "critical"]
    message: str
    fingerprint: str


# ---------------------------------------------------------------------------
# Tool I/O schemas
# ---------------------------------------------------------------------------


class SearchTelemetryInput(BaseModel):
    service: str
    keyword: str | None = None
    since_minutes: int = Field(default=60, ge=1, le=1440)
    limit: int = Field(default=10, ge=1)


class TelemetryEventOut(BaseModel):
    id: int
    service: str
    level: str
    message: str
    host: str
    timestamp: datetime
    tags: dict


class SearchTelemetryOutput(BaseModel):
    results: list[TelemetryEventOut]


class SearchDeploymentsInput(BaseModel):
    service: str
    within_minutes: int = Field(default=120, ge=1, le=10080)
    limit: int = Field(default=10, ge=1)


class DeploymentOut(BaseModel):
    id: int
    service: str
    version: str
    commit_sha: str
    environment: str
    status: str
    deployed_at: datetime


class SearchDeploymentsOutput(BaseModel):
    results: list[DeploymentOut]


class RetrieveRunbookInput(BaseModel):
    service: str
    query: str
    limit: int = Field(default=5, ge=1)


class RunbookOut(BaseModel):
    id: int
    title: str
    service: str
    updated_at: datetime
    stale: bool
    snippet: str


class RetrieveRunbookOutput(BaseModel):
    results: list[RunbookOut]


class SearchPriorIncidentsInput(BaseModel):
    service: str
    query: str
    limit: int = Field(default=5, ge=1)


class PriorIncidentOut(BaseModel):
    id: int
    title: str
    service: str
    root_cause: str
    occurred_at: datetime
    summary_snippet: str


class SearchPriorIncidentsOutput(BaseModel):
    results: list[PriorIncidentOut]


class InspectChangeInput(BaseModel):
    commit_sha: str = Field(min_length=7, max_length=40)


class SourceChangeOut(BaseModel):
    id: int
    repo: str
    commit_sha: str
    author: str
    message: str
    files_changed: list[str]
    diff_stat: int
    timestamp: datetime


class InspectChangeOutput(BaseModel):
    change: SourceChangeOut | None
    correlated_deployment: DeploymentOut | None


# ---------------------------------------------------------------------------
# Diagnosis / investigation schemas
# ---------------------------------------------------------------------------


class EvidenceRef(BaseModel):
    type: EvidenceType
    id: int


class Diagnosis(BaseModel):
    root_cause: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[EvidenceRef]
    suggested_action: str
    reasoning: str


class ToolCallSummary(BaseModel):
    tool_name: str
    status: str
    latency_ms: float


class InvestigationOut(BaseModel):
    id: int
    alert_id: int
    status: str
    diagnosis: Diagnosis | None
    suggested_action: str | None
    postmortem_draft: str | None
    tool_calls: list[ToolCallSummary]


class AlertOut(BaseModel):
    id: int
    service: str
    severity: str
    message: str
    fingerprint: str
    status: str
    investigation_id: int | None = None
