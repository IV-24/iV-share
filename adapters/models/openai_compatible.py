"""Shared translation layer for any OpenAI-compatible chat completions
API (Groq, Mistral, OpenRouter, ...): core's tool schemas -> OpenAI's
function-calling format and back, core.models.base.ModelMessage history
-> OpenAI's messages array and back. One adapter per provider only needs
to supply base_url/api_key/model; this is where the actual translation
logic lives, so adding a fifth OpenAI-compatible provider is a ~10-line
subclass, not a new integration from scratch — mirrors
backend/app/providers.py's call_openai_compatible() being shared by Groq
and Mistral before this migration, now as a real base class instead of
one function two callers both went through.
"""

import json

from openai import OpenAI

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


def to_openai_tools(core_tools: list[dict] | None) -> list[dict] | None:
    if not core_tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }
        for tool in core_tools
    ]


def to_openai_messages(system_prompt: str | None, messages: list[ModelMessage]) -> list[dict]:
    openai_messages = []
    if system_prompt:
        openai_messages.append({"role": "system", "content": system_prompt})

    for msg in messages:
        if msg.role == "tool":
            openai_messages.append({"role": "tool", "tool_call_id": msg.tool_call_id, "content": msg.content})
        elif msg.role == "assistant" and msg.tool_calls:
            openai_messages.append({
                "role": "assistant",
                "content": msg.content or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                    }
                    for call in msg.tool_calls
                ],
            })
        else:
            role = "assistant" if msg.role == "assistant" else "user"
            openai_messages.append({"role": role, "content": msg.content})

    return openai_messages


def from_openai_tool_calls(message) -> list[ToolCall]:
    if not getattr(message, "tool_calls", None):
        return []
    return [
        ToolCall(id=call.id, name=call.function.name, arguments=json.loads(call.function.arguments or "{}"))
        for call in message.tool_calls
    ]


class OpenAICompatibleProvider(ModelProvider):
    """Base class for any ModelProvider that speaks OpenAI's chat
    completions wire format. Subclasses set `name` (a class attribute,
    per ModelProvider) and pass their own base_url/default model."""

    def __init__(self, api_key: str, model: str, base_url: str, context_window: int | None = None) -> None:
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS)
        self._model = model
        self._context_window = context_window

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(
            name=self._model, provider=self.name, supports_tools=True, cost_tier="free",
            context_window=self._context_window,
        )]

    def generate(self, request: ModelRequest) -> ModelResponse:
        messages = to_openai_messages(request.system_prompt, request.messages)
        tools = to_openai_tools(request.tools)

        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=request.max_tokens,
                tools=tools,
                tool_choice="auto" if tools else None,
            )
            message = completion.choices[0].message
            tool_calls = from_openai_tool_calls(message)
            text = message.content or ""
        except Exception as exc:  # noqa: BLE001 - any failure here means "try the next provider"
            raise ModelUnavailableError(str(exc)) from exc

        # Reading the response is inside the try above, not after it. An
        # empty choices list (IndexError) and invalid tool-call JSON
        # (JSONDecodeError, see from_openai_tool_calls) are both things a
        # provider really does, and neither is a ModelUnavailableError --
        # so raised from out here they skipped ModelRegistry's fallback
        # entirely and took the whole turn down as a 500, before anything
        # was written to the audit log.
        return ModelResponse(
            text=text,
            model_name=self._model,
            provider=self.name,
            tool_calls=tool_calls,
            raw=completion,
        )
