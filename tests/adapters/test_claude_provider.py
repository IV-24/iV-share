"""Unit tests for the Claude adapter's translation logic only — no real
network call. anthropic.Anthropic() does no I/O at construction, so the
provider is real; client.messages.create() is replaced with a fake."""

from unittest.mock import MagicMock

from adapters.models.claude_provider import ClaudeProvider
from core.models.base import ModelMessage, ModelRequest, ModelUnavailableError, ToolCall
import pytest


def _text_block(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _tool_use_block(id_: str, name: str, input_: dict):
    block = MagicMock()
    block.type = "tool_use"
    block.id = id_
    block.name = name
    block.input = input_
    return block


def _fake_response(*blocks):
    response = MagicMock()
    response.content = list(blocks)
    return response


def test_generate_returns_text_response():
    provider = ClaudeProvider(api_key="fake-key")
    provider._client.messages.create = MagicMock(return_value=_fake_response(_text_block("hello there")))

    response = provider.generate(ModelRequest(messages=[ModelMessage(role="user", content="hi")]))

    assert response.text == "hello there"
    assert response.provider == "claude"
    assert response.tool_calls == []


def test_generate_wraps_api_errors_as_model_unavailable():
    provider = ClaudeProvider(api_key="fake-key")
    provider._client.messages.create = MagicMock(side_effect=RuntimeError("connection refused"))

    with pytest.raises(ModelUnavailableError):
        provider.generate(ModelRequest(messages=[ModelMessage(role="user", content="hi")]))


def test_generate_parses_tool_use_blocks():
    provider = ClaudeProvider(api_key="fake-key")
    provider._client.messages.create = MagicMock(
        return_value=_fake_response(_tool_use_block("call-1", "create_task", {"title": "ship it"}))
    )

    response = provider.generate(ModelRequest(messages=[ModelMessage(role="user", content="create a task")]))

    assert response.tool_calls == [ToolCall(id="call-1", name="create_task", arguments={"title": "ship it"})]
    assert response.text == ""


def test_generate_passes_system_prompt_and_tools():
    provider = ClaudeProvider(api_key="fake-key")
    provider._client.messages.create = MagicMock(return_value=_fake_response(_text_block("ok")))

    provider.generate(ModelRequest(
        messages=[ModelMessage(role="user", content="hi")],
        system_prompt="be terse",
        tools=[{
            "name": "create_task", "description": "Creates a task",
            "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
        }],
    ))

    call_kwargs = provider._client.messages.create.call_args.kwargs
    assert call_kwargs["system"] == "be terse"
    assert call_kwargs["tools"] == [{
        "name": "create_task", "description": "Creates a task",
        "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
    }]


def test_generate_batches_consecutive_tool_results_into_one_user_turn():
    """Anthropic requires every tool_result for a given assistant
    tool_use turn to arrive together in the next user message, not
    spread across multiple separate user messages."""
    provider = ClaudeProvider(api_key="fake-key")
    provider._client.messages.create = MagicMock(return_value=_fake_response(_text_block("done")))

    history = [
        ModelMessage(role="user", content="create two tasks"),
        ModelMessage(
            role="assistant", content="",
            tool_calls=[
                ToolCall(id="call-1", name="create_task", arguments={"title": "a"}),
                ToolCall(id="call-2", name="create_task", arguments={"title": "b"}),
            ],
        ),
        ModelMessage(role="tool", content='{"success": true, "output": {"id": "1"}}', tool_call_id="call-1"),
        ModelMessage(role="tool", content='{"success": true, "output": {"id": "2"}}', tool_call_id="call-2"),
    ]
    provider.generate(ModelRequest(messages=history))

    sent = provider._client.messages.create.call_args.kwargs["messages"]
    assert sent[1]["role"] == "assistant"
    assert [b["type"] for b in sent[1]["content"]] == ["tool_use", "tool_use"]
    assert sent[2]["role"] == "user"
    assert [b["tool_use_id"] for b in sent[2]["content"]] == ["call-1", "call-2"]
