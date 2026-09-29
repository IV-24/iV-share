"""Drives OpenAICompatibleProvider against a real HTTP server speaking
OpenAI's chat-completions wire format.

Every other provider test mocks the SDK client, which verifies iV's
translation of its own types but not that the bytes on the wire are
shaped the way a provider expects. This one runs an actual socket
server, so a change that breaks the request body — a tool schema in the
wrong place, a tool result sent as the wrong role — fails here instead of
at the first real API call. It needs no API key and no network access
beyond loopback, which is what makes it runnable in CI and in a sandbox.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from adapters.models.openai_compatible import OpenAICompatibleProvider
from core.models.base import ModelMessage, ModelRequest, ModelUnavailableError, ToolCall

_received: list[dict] = []
_next_response: dict = {}
_status: int = 200


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server API
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _received.append(json.loads(body))
        payload = json.dumps(_next_response).encode()
        self.send_response(_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture
def server():
    _received.clear()
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/v1"
    httpd.shutdown()
    httpd.server_close()


class LocalProvider(OpenAICompatibleProvider):
    name = "local"


def _completion(message: dict) -> dict:
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": "test-model",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    }


def test_a_real_http_round_trip_returns_the_model_text(server):
    global _next_response, _status
    _status = 200
    _next_response = _completion({"role": "assistant", "content": "I am here."})
    provider = LocalProvider(api_key="not-a-real-key", model="test-model", base_url=server)

    response = provider.generate(ModelRequest(
        messages=[ModelMessage(role="user", content="are you there?")],
        system_prompt="You are iV.",
    ))

    assert response.text == "I am here."
    assert response.provider == "local"
    sent = _received[-1]
    assert sent["messages"][0] == {"role": "system", "content": "You are iV."}
    assert sent["messages"][1] == {"role": "user", "content": "are you there?"}


def test_tool_definitions_reach_the_wire_in_openai_shape(server):
    global _next_response
    _next_response = _completion({"role": "assistant", "content": "ok"})
    provider = LocalProvider(api_key="k", model="test-model", base_url=server)

    provider.generate(ModelRequest(
        messages=[ModelMessage(role="user", content="hi")],
        tools=[{
            "name": "list_projects", "description": "Lists projects.",
            "input_schema": {"type": "object", "properties": {"status": {"type": "string"}}},
        }],
    ))

    tool = _received[-1]["tools"][0]
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "list_projects"
    assert tool["function"]["parameters"]["properties"]["status"]["type"] == "string"
    assert _received[-1]["tool_choice"] == "auto"


def test_a_tool_call_response_is_translated_back_into_core_types(server):
    global _next_response
    _next_response = _completion({
        "role": "assistant", "content": None,
        "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "list_projects", "arguments": '{"status": "active"}'},
        }],
    })
    provider = LocalProvider(api_key="k", model="test-model", base_url=server)

    response = provider.generate(ModelRequest(messages=[ModelMessage(role="user", content="what's open?")]))

    assert response.requests_tool_calls
    assert response.tool_calls[0] == ToolCall(id="call_1", name="list_projects", arguments={"status": "active"})


def test_a_tool_result_turn_is_sent_back_in_the_expected_shape(server):
    """The second half of the loop: iV replays the assistant's tool-call
    turn plus a 'tool' role message carrying the result. Getting either
    wrong is the classic cause of a provider 400 mid-conversation."""
    global _next_response
    _next_response = _completion({"role": "assistant", "content": "You have 2 projects."})
    provider = LocalProvider(api_key="k", model="test-model", base_url=server)

    provider.generate(ModelRequest(messages=[
        ModelMessage(role="user", content="what's open?"),
        ModelMessage(role="assistant", content="", tool_calls=[
            ToolCall(id="call_1", name="list_projects", arguments={"status": "active"})
        ]),
        ModelMessage(role="tool", content='{"success": true}', tool_call_id="call_1"),
    ]))

    sent = _received[-1]["messages"]
    assert sent[1]["tool_calls"][0]["function"]["arguments"] == '{"status": "active"}'
    assert sent[2] == {"role": "tool", "tool_call_id": "call_1", "content": '{"success": true}'}


def test_an_http_error_becomes_ModelUnavailableError_so_fallback_can_run(server):
    global _next_response, _status
    _status = 429
    _next_response = {"error": {"message": "rate limit exceeded"}}
    provider = LocalProvider(api_key="k", model="test-model", base_url=server)

    with pytest.raises(ModelUnavailableError):
        provider.generate(ModelRequest(messages=[ModelMessage(role="user", content="hi")]))

    _status = 200
