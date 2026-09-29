"""Unit tests for the adapter's translation logic only — no real network
call. Proves ModelRequest -> OpenAI-shaped messages/tools translation and
OpenAI tool_calls -> core.models.base.ToolCall translation both work,
without needing a live GROQ_API_KEY."""

from unittest.mock import MagicMock

import pytest

from adapters.models.groq_provider import GroqProvider
from core.models.base import ModelMessage, ModelRequest, ModelUnavailableError, ToolCall


def _mock_completion(content: str | None = None, tool_calls=None):
    completion = MagicMock()
    completion.choices = [MagicMock(message=MagicMock(content=content, tool_calls=tool_calls))]
    return completion


def test_generate_translates_request_and_returns_response():
    provider = GroqProvider(api_key="fake-key")
    provider._client.chat.completions.create = MagicMock(return_value=_mock_completion(content="42"))

    request = ModelRequest(
        messages=[ModelMessage(role="user", content="what's the answer?")],
        system_prompt="be terse",
    )
    response = provider.generate(request)

    assert response.text == "42"
    assert response.provider == "groq"
    assert response.tool_calls == []
    call_kwargs = provider._client.chat.completions.create.call_args.kwargs
    assert call_kwargs["messages"][0] == {"role": "system", "content": "be terse"}
    assert call_kwargs["messages"][1] == {"role": "user", "content": "what's the answer?"}


def test_generate_wraps_api_errors_as_model_unavailable():
    provider = GroqProvider(api_key="fake-key")
    provider._client.chat.completions.create = MagicMock(side_effect=RuntimeError("connection refused"))

    request = ModelRequest(messages=[ModelMessage(role="user", content="hi")])

    with pytest.raises(ModelUnavailableError):
        provider.generate(request)


def test_generate_translates_core_tool_schema_to_openai_function_schema():
    provider = GroqProvider(api_key="fake-key")
    provider._client.chat.completions.create = MagicMock(return_value=_mock_completion(content="ok"))

    request = ModelRequest(
        messages=[ModelMessage(role="user", content="create a task")],
        tools=[{
            "name": "create_task", "description": "Creates a task",
            "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
            "output_schema": {"type": "object"}, "required_permissions": ["database.write"],
            "risk_level": "low", "execution_policy": "auto",
        }],
    )
    provider.generate(request)

    call_kwargs = provider._client.chat.completions.create.call_args.kwargs
    assert call_kwargs["tools"] == [{
        "type": "function",
        "function": {
            "name": "create_task", "description": "Creates a task",
            "parameters": {"type": "object", "properties": {"title": {"type": "string"}}},
        },
    }]
    assert call_kwargs["tool_choice"] == "auto"


def test_generate_parses_tool_calls_from_response():
    provider = GroqProvider(api_key="fake-key")
    fake_tool_call = MagicMock(id="call-1")
    fake_tool_call.function.name = "create_task"
    fake_tool_call.function.arguments = '{"title": "ship it"}'
    provider._client.chat.completions.create = MagicMock(
        return_value=_mock_completion(content=None, tool_calls=[fake_tool_call])
    )

    response = provider.generate(ModelRequest(messages=[ModelMessage(role="user", content="create a task")]))

    assert response.tool_calls == [ToolCall(id="call-1", name="create_task", arguments={"title": "ship it"})]


def test_generate_serializes_tool_call_and_tool_result_messages_in_history():
    provider = GroqProvider(api_key="fake-key")
    provider._client.chat.completions.create = MagicMock(return_value=_mock_completion(content="done"))

    history = [
        ModelMessage(role="user", content="create a task"),
        ModelMessage(
            role="assistant", content="",
            tool_calls=[ToolCall(id="call-1", name="create_task", arguments={"title": "ship it"})],
        ),
        ModelMessage(role="tool", content='{"success": true}', tool_call_id="call-1"),
    ]
    provider.generate(ModelRequest(messages=history))

    sent = provider._client.chat.completions.create.call_args.kwargs["messages"]
    assert sent[1]["tool_calls"][0]["id"] == "call-1"
    assert sent[1]["tool_calls"][0]["function"]["arguments"] == '{"title": "ship it"}'
    assert sent[2] == {"role": "tool", "tool_call_id": "call-1", "content": '{"success": true}'}


def test_generate_wraps_an_empty_choices_list_as_model_unavailable():
    """A response with no choices used to raise IndexError from outside the
    try block, which ModelRegistry.generate_with_fallback does not catch --
    so one malformed response killed the whole turn instead of failing over."""
    provider = GroqProvider(api_key="fake-key")
    empty = MagicMock()
    empty.choices = []
    provider._client.chat.completions.create = MagicMock(return_value=empty)

    with pytest.raises(ModelUnavailableError):
        provider.generate(ModelRequest(messages=[ModelMessage(role="user", content="hi")]))


def test_generate_wraps_malformed_tool_call_arguments_as_model_unavailable():
    """json.loads on the model's tool-call arguments sat outside the try
    too, so a model emitting invalid JSON raised JSONDecodeError and took
    the turn down with it."""
    provider = GroqProvider(api_key="fake-key")
    bad_call = MagicMock()
    bad_call.id = "call_1"
    bad_call.function = MagicMock(name="fn", arguments="{not valid json")
    bad_call.function.name = "create_task"
    provider._client.chat.completions.create = MagicMock(
        return_value=_mock_completion(tool_calls=[bad_call])
    )

    with pytest.raises(ModelUnavailableError):
        provider.generate(ModelRequest(messages=[ModelMessage(role="user", content="hi")]))
