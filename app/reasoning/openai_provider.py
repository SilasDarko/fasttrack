import json

from openai import AsyncOpenAI
from pydantic import ValidationError

from app.errors import LLMOutputInvalidError, LLMUnavailableError
from app.reasoning.base import AgentAction, FinalizeAction, InvestigationContext, ToolCallAction
from app.schemas import Diagnosis

_SYSTEM_PROMPT = (
    "You are an incident-analysis agent. You may call the provided read-only "
    "diagnostic tools to gather evidence about the alert. Once you have enough "
    "evidence, respond with a final JSON object (no tool call) matching this "
    "schema exactly: "
    '{"root_cause": str, "confidence": float 0-1, '
    '"evidence_refs": [{"type": one of telemetry|deployment|source_change|runbook|prior_incident, '
    '"id": int}], "suggested_action": str, "reasoning": str}. '
    "Only cite evidence_refs for records actually returned by a tool call you made "
    "in this conversation -- never invent an id."
)


class OpenAIReasoningProvider:
    def __init__(self, api_key: str, model: str, tool_schemas: list[dict]) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._tool_schemas = tool_schemas

    async def decide_next_action(self, context: InvestigationContext) -> AgentAction:
        messages = self._build_messages(context)
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=self._tool_schemas,
                tool_choice="auto",
            )
        except Exception as exc:  # openai SDK raises various typed errors
            raise LLMUnavailableError(f"OpenAI chat completion failed: {exc}") from exc

        return self._parse_response(response)

    def _parse_response(self, response) -> AgentAction:
        choice = response.choices[0]
        tool_calls = choice.message.tool_calls or []

        if tool_calls:
            call = tool_calls[0]
            try:
                arguments = json.loads(call.function.arguments)
            except json.JSONDecodeError as exc:
                raise LLMOutputInvalidError(
                    f"Malformed tool-call arguments from OpenAI: {exc}"
                ) from exc
            return ToolCallAction(tool_name=call.function.name, arguments=arguments)

        content = choice.message.content or ""
        try:
            payload = json.loads(content)
            diagnosis = Diagnosis.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LLMOutputInvalidError(f"Malformed diagnosis payload from OpenAI: {exc}") from exc
        return FinalizeAction(diagnosis=diagnosis)

    def _build_messages(self, context: InvestigationContext) -> list[dict]:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Alert on service={context.alert.service} "
                    f"severity={context.alert.severity}: {context.alert.message}"
                ),
            },
        ]
        for record in context.history:
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{record.tool_name}",
                            "type": "function",
                            "function": {
                                "name": record.tool_name,
                                "arguments": json.dumps(record.input),
                            },
                        }
                    ],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": f"call_{record.tool_name}",
                    "content": json.dumps(record.output if record.status == "success" else {
                        "error": record.error_code
                    }),
                }
            )
        return messages
