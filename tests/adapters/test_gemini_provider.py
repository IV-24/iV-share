"""Unit tests for the Gemini adapter's translation logic only — no real
network call. The provider's genai.Client is real (constructing it does
no I/O), but client.models.list()/generate_content() are replaced with
fakes so these tests need no GEMINI_API_KEY and no network access."""

from unittest.mock import MagicMock

from google.genai import types

from adapters.models.gemini_provider import GeminiProvider
from core.models.base import ModelMessage, ModelRequest, ToolCall


def _fake_model(name: str, supported_actions=("generateContent",)):
    model = MagicMock()
    model.name = f"models/{name}"
    model.supported_actions = list(supported_actions)
    return model


def _provider_with_models(*names: str) -> GeminiProvider:
    provider = GeminiProvider(api_key="fake-key")
    provider._client.models.list = MagicMock(return_value=[_fake_model(n) for n in names])
    return provider


def test_resolve_candidates_filters_to_available_and_supported():
    provider = _provider_with_models("gemini-3.6-flash", "gemini-2.5-flash-lite")
    candidates = provider._resolve_candidates()
    assert candidates == ["gemini-3.6-flash", "gemini-2.5-flash-lite"]


def test_resolve_candidates_excludes_models_without_generate_content():
    provider = _provider_with_models("gemini-3.6-flash")
    provider._client.models.list = MagicMock(return_value=[_fake_model("gemini-3.6-flash", supported_actions=("embedContent",))])
    assert provider._resolve_candidates() == []


def test_generate_returns_text_response():
    provider = _provider_with_models("gemini-3.6-flash")
    fake_response = MagicMock(text="hello there", function_calls=[])
    provider._client.models.generate_content = MagicMock(return_value=fake_response)

    response = provider.generate(ModelRequest(messages=[ModelMessage(role="user", content="hi")]))

    assert response.text == "hello there"
    assert response.provider == "gemini"
    assert response.model_name == "gemini-3.6-flash"
    assert response.tool_calls == []


def test_generate_parses_function_calls():
    provider = _provider_with_models("gemini-3.6-flash")
    # MagicMock's own constructor reserves the `name` kwarg for the mock's
    # repr, so it has to be set as an attribute afterward, not passed in.
    fake_call = MagicMock(id="call-1", args={"title": "ship it"})
    fake_call.name = "create_task"
    fake_response = MagicMock(text="", function_calls=[fake_call])
    provider._client.models.generate_content = MagicMock(return_value=fake_response)

    response = provider.generate(ModelRequest(messages=[ModelMessage(role="user", content="create a task")]))

    assert response.tool_calls == [ToolCall(id="call-1", name="create_task", arguments={"title": "ship it"})]


def test_generate_synthesizes_id_when_function_call_has_none():
    provider = _provider_with_models("gemini-3.6-flash")
    fake_call = MagicMock(id=None, args={"title": "ship it"})
    fake_call.name = "create_task"
    fake_response = MagicMock(text="", function_calls=[fake_call])
    provider._client.models.generate_content = MagicMock(return_value=fake_response)

    response = provider.generate(ModelRequest(messages=[ModelMessage(role="user", content="create a task")]))

    assert response.tool_calls[0].id  # non-empty, synthesized


def test_generate_passes_system_instruction_and_tools():
    provider = _provider_with_models("gemini-3.6-flash")
    fake_response = MagicMock(text="ok", function_calls=[])
    provider._client.models.generate_content = MagicMock(return_value=fake_response)

    provider.generate(ModelRequest(
        messages=[ModelMessage(role="user", content="hi")],
        system_prompt="be terse",
        tools=[{
            "name": "create_task", "description": "Creates a task",
            "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
        }],
    ))

    call_kwargs = provider._client.models.generate_content.call_args.kwargs
    config = call_kwargs["config"]
    assert config.system_instruction == "be terse"
    assert config.tools[0].function_declarations[0].name == "create_task"


def test_generate_round_trips_tool_call_and_result_history():
    provider = _provider_with_models("gemini-3.6-flash")
    fake_response = MagicMock(text="Done.", function_calls=[])
    provider._client.models.generate_content = MagicMock(return_value=fake_response)

    history = [
        ModelMessage(role="user", content="create a task"),
        ModelMessage(
            role="assistant", content="",
            tool_calls=[ToolCall(id="call-1", name="create_task", arguments={"title": "ship it"})],
        ),
        ModelMessage(role="tool", content='{"success": true, "output": {"id": "task-1"}}', tool_call_id="call-1"),
    ]
    provider.generate(ModelRequest(messages=history))

    contents = provider._client.models.generate_content.call_args.kwargs["contents"]
    assert contents[1].role == "model"
    assert contents[1].parts[0].function_call.name == "create_task"
    assert contents[2].role == "tool"
    assert contents[2].parts[0].function_response.name == "create_task"
    assert contents[2].parts[0].function_response.response == {"success": True, "output": {"id": "task-1"}}


def test_generate_marks_candidate_exhausted_on_404_and_tries_next():
    provider = _provider_with_models("gemini-3.6-flash", "gemini-2.5-flash-lite")

    from google.genai import errors as genai_errors
    not_found = genai_errors.APIError(404, {"error": {"message": "NOT_FOUND: model retired", "status": "NOT_FOUND"}})

    fake_response = MagicMock(text="fallback worked", function_calls=[])
    provider._client.models.generate_content = MagicMock(side_effect=[not_found, fake_response])

    response = provider.generate(ModelRequest(messages=[ModelMessage(role="user", content="hi")]))

    assert response.text == "fallback worked"
    assert response.model_name == "gemini-2.5-flash-lite"
    assert "gemini-3.6-flash" in provider._exhausted
