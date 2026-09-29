"""Mirrors test_groq_provider.py — both are thin OpenAICompatibleProvider
subclasses, so both need the same coverage to prove the shared base
class works correctly for each, not just one of them by accident."""

from unittest.mock import MagicMock

import pytest

from adapters.models.mistral_provider import MistralProvider
from core.models.base import ModelMessage, ModelRequest, ModelUnavailableError, ToolCall


def _mock_completion(content: str | None = None, tool_calls=None):
    completion = MagicMock()
    completion.choices = [MagicMock(message=MagicMock(content=content, tool_calls=tool_calls))]
    return completion


def test_generate_translates_request_and_returns_response():
    provider = MistralProvider(api_key="fake-key")
    provider._client.chat.completions.create = MagicMock(return_value=_mock_completion(content="42"))

    response = provider.generate(ModelRequest(
        messages=[ModelMessage(role="user", content="what's the answer?")], system_prompt="be terse",
    ))

    assert response.text == "42"
    assert response.provider == "mistral"
    assert response.tool_calls == []


def test_generate_wraps_api_errors_as_model_unavailable():
    provider = MistralProvider(api_key="fake-key")
    provider._client.chat.completions.create = MagicMock(side_effect=RuntimeError("connection refused"))

    with pytest.raises(ModelUnavailableError):
        provider.generate(ModelRequest(messages=[ModelMessage(role="user", content="hi")]))


def test_generate_parses_tool_calls_from_response():
    provider = MistralProvider(api_key="fake-key")
    fake_tool_call = MagicMock(id="call-1")
    fake_tool_call.function.name = "create_task"
    fake_tool_call.function.arguments = '{"title": "ship it"}'
    provider._client.chat.completions.create = MagicMock(
        return_value=_mock_completion(content=None, tool_calls=[fake_tool_call])
    )

    response = provider.generate(ModelRequest(messages=[ModelMessage(role="user", content="create a task")]))

    assert response.tool_calls == [ToolCall(id="call-1", name="create_task", arguments={"title": "ship it"})]


def test_generate_translates_core_tool_schema_to_openai_function_schema():
    provider = MistralProvider(api_key="fake-key")
    provider._client.chat.completions.create = MagicMock(return_value=_mock_completion(content="ok"))

    provider.generate(ModelRequest(
        messages=[ModelMessage(role="user", content="create a task")],
        tools=[{
            "name": "create_task", "description": "Creates a task",
            "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
        }],
    ))

    call_kwargs = provider._client.chat.completions.create.call_args.kwargs
    assert call_kwargs["tools"][0]["function"]["name"] == "create_task"
    assert call_kwargs["tool_choice"] == "auto"
