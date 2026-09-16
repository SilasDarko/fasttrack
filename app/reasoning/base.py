from typing import Literal, Protocol

from pydantic import BaseModel

from app.schemas import Diagnosis


class AlertSnapshot(BaseModel):
    service: str
    severity: str
    message: str


class ToolCallRecord(BaseModel):
    tool_name: str
    input: dict
    output: dict | None
    status: Literal["success", "error"]
    error_code: str | None = None


class InvestigationContext(BaseModel):
    alert: AlertSnapshot
    history: list[ToolCallRecord] = []


class ToolCallAction(BaseModel):
    kind: Literal["tool_call"] = "tool_call"
    tool_name: str
    arguments: dict


class FinalizeAction(BaseModel):
    kind: Literal["finalize"] = "finalize"
    diagnosis: Diagnosis


AgentAction = ToolCallAction | FinalizeAction


class ReasoningProvider(Protocol):
    async def decide_next_action(self, context: InvestigationContext) -> AgentAction: ...
