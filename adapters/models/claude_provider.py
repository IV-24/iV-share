"""Claude adapter: implements core.models.base.ModelProvider using
Anthropic's Messages API — a different wire shape from the OpenAI-
compatible providers (tool_use/tool_result content blocks instead of a
separate "tool" message role, system prompt as its own top-level param),
so it gets its own small translation layer rather than being squeezed
into adapters/models/openai_compatible.py. Migrated from
backend/app/providers.py's call_claude().

Anthropic requires every tool_result for a given assistant tool_use turn
to arrive together in the next user-role message (multiple content
blocks), not spread across several separate user messages — core's
history represents each tool result as its own ModelMessage(role="tool"),
so _to_anthropic_messages() batches consecutive ones into a single user
turn.
"""

from anthropic import Anthropic

from core.models.base import (
    PROVIDER_REQUEST_TIMEOUT_SECONDS,
    ModelInfo,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUnavailableError,
    ToolCall,
)

CLAUDE_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 4096


class ClaudeProvider(ModelProvider):
    name = "claude"

    def __init__(self, api_key: str, model: str = CLAUDE_MODEL) -> None:
        self._client = Anthropic(api_key=api_key, timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS)
        self._model = model

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(
            name=self._model, provider=self.name, supports_tools=True, cost_tier="paid",
            context_window=200_000,  # standard window for the Claude models this adapter targets
        )]

    def generate(self, request: ModelRequest) -> ModelResponse:
        kwargs = {
            "model": self._model,
            "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
            "messages": _to_anthropic_messages(request.messages),
        }
        if request.system_prompt:
            kwargs["system"] = request.system_prompt
        tools = _to_anthropic_tools(request.tools)
        if tools:
            kwargs["tools"] = tools

        try:
            response = self._client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - any Claude/network failure means "try the next provider"
            raise ModelUnavailableError(str(exc)) from exc

        return _to_model_response(response, self._model, self.name)


def _to_anthropic_tools(core_tools: list[dict] | None) -> list[dict] | None:
    if not core_tools:
        return None
    return [
        {"name": tool["name"], "description": tool["description"], "input_schema": tool["input_schema"]}
        for tool in core_tools
    ]


def _to_anthropic_messages(messages: list[ModelMessage]) -> list[dict]:
    anthropic_messages = []
    index = 0
    while index < len(messages):
        msg = messages[index]

        if msg.role == "assistant":
            content = []
            if msg.tool_calls:
                content.extend(
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                    for call in msg.tool_calls
                )
            if msg.content:
                content.append({"type": "text", "text": msg.content})
            anthropic_messages.append({"role": "assistant", "content": content or [{"type": "text", "text": ""}]})
            index += 1

        elif msg.role == "tool":
            tool_results = []
            while index < len(messages) and messages[index].role == "tool":
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": messages[index].tool_call_id,
                    "content": messages[index].content,
                })
                index += 1
            anthropic_messages.append({"role": "user", "content": tool_results})

        else:
            anthropic_messages.append({"role": "user", "content": msg.content})
            index += 1

    return anthropic_messages


def _to_model_response(response, model_name: str, provider_name: str) -> ModelResponse:
    text = "".join(block.text for block in response.content if block.type == "text")
    tool_calls = [
        ToolCall(id=block.id, name=block.name, arguments=block.input)
        for block in response.content if block.type == "tool_use"
    ]
    return ModelResponse(text=text, model_name=model_name, provider=provider_name, tool_calls=tool_calls, raw=response)
