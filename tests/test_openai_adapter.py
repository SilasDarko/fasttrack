"""Tests the OpenAI reasoning adapter's request/response mapping using a fake
transport (no live API calls, no network access, no API key required)."""

import json
from types import SimpleNamespace

import pytest

from app.errors import LLMOutputInvalidError
from app.reasoning.base import FinalizeAction, InvestigationContext, ToolCallAction
from app.reasoning.openai_provider import OpenAIReasoningProvider


def _make_provider() -> OpenAIReasoningProvider:
    return OpenAIReasoningProvider(api_key="test-key", model="gpt-4o-mini", tool_schemas=[])


def _context() -> InvestigationContext:
    return InvestigationContext.model_validate(
        {"alert": {"service": "checkout", "severity": "high", "message": "pool exhausted"}}
    )


def _fake_response(*, tool_call=None, content=None):
    message = SimpleNamespace(tool_calls=[tool_call] if tool_call else None, content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


async def test_adapter_maps_successful_tool_call_response(monkeypatch):
    provider = _make_provider()
    tool_call = SimpleNamespace(
        function=SimpleNamespace(
            name="search_telemetry", arguments=json.dumps({"service": "checkout"})
        )
    )

    async def fake_create(**kwargs):
        return _fake_response(tool_call=tool_call)

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

    action = await provider.decide_next_action(_context())

    assert isinstance(action, ToolCallAction)
    assert action.tool_name == "search_telemetry"
    assert action.arguments == {"service": "checkout"}


async def test_adapter_maps_malformed_tool_call_arguments_to_typed_error(monkeypatch):
    provider = _make_provider()
    tool_call = SimpleNamespace(
        function=SimpleNamespace(name="search_telemetry", arguments="{not valid json")
    )

    async def fake_create(**kwargs):
        return _fake_response(tool_call=tool_call)

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

    with pytest.raises(LLMOutputInvalidError):
        await provider.decide_next_action(_context())


async def test_adapter_maps_successful_finalize_response(monkeypatch):
    provider = _make_provider()
    payload = {
        "root_cause": "deployment regression",
        "confidence": 0.8,
        "evidence_refs": [{"type": "deployment", "id": 1}],
        "suggested_action": "roll back",
        "reasoning": "correlated deployment",
    }

    async def fake_create(**kwargs):
        return _fake_response(content=json.dumps(payload))

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

    action = await provider.decide_next_action(_context())

    assert isinstance(action, FinalizeAction)
    assert action.diagnosis.root_cause == "deployment regression"
