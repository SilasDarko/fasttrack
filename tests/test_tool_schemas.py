import pytest
from pydantic import BaseModel, ValidationError

from app.schemas import SearchTelemetryInput
from app.tools.base import TOOL_REGISTRY

EXPECTED_TOOL_NAMES = {
    "search_telemetry",
    "search_deployments",
    "retrieve_runbook",
    "search_prior_incidents",
    "inspect_change",
}


def test_registry_has_exactly_five_tools():
    assert len(TOOL_REGISTRY) == 5


def test_registry_tool_names_match_expected_set():
    assert set(TOOL_REGISTRY.keys()) == EXPECTED_TOOL_NAMES


def test_each_tool_openai_schema_is_well_formed():
    for tool in TOOL_REGISTRY.values():
        schema = tool.openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == tool.name
        assert "properties" in schema["function"]["parameters"]


def test_each_tool_has_input_and_output_pydantic_models():
    for tool in TOOL_REGISTRY.values():
        assert issubclass(tool.input_model, BaseModel)
        assert issubclass(tool.output_model, BaseModel)


def test_each_tool_has_non_empty_description():
    for tool in TOOL_REGISTRY.values():
        assert isinstance(tool.description, str) and len(tool.description) > 10


def test_search_telemetry_input_rejects_missing_required_field():
    SearchTelemetryInput(service="checkout")  # service is the only required field
    with pytest.raises(ValidationError):
        SearchTelemetryInput()
