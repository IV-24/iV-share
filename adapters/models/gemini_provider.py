"""Gemini adapter: implements core.models.base.ModelProvider using the
unified google-genai SDK's manual (non-automatic) function calling —
FunctionDeclaration/Tool objects built from core's tool schemas, not
Python callables handed to the SDK, since tool execution has to go
through core.tools.ToolRegistry (permission + approval enforcement), not
whatever the SDK would call on iV's behalf.

Candidate resolution is migrated from backend/app/model_resolver.py: ask
the API what this key can actually see (client.models.list()), walk
PREFERRED_MODELS in priority order, and only use candidates that are both
available and support generateContent. Unlike the original, this happens
lazily (first generate() call) and is cached per instance, not at import
time — keeps constructing a GeminiProvider free of network calls, which
matters for tests and for not blocking process startup on the network.

Per-candidate fallback (429/404 -> mark exhausted for this process;
503/UNAVAILABLE -> skip for this call only) is migrated from
backend/app/router.py's _try_gemini(). If every candidate fails, generate()
raises ModelUnavailableError so ModelRegistry.generate_with_fallback()
moves on to the next provider in the role's order (mistral, groq, ...).
"""

import json
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

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

# Priority order: most capable/current first, most-likely-to-still-be-free
# last. Update as Google ships new models — this list is the only place
# a Gemini model name should ever be typed.
PREFERRED_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash-lite",
]


class GeminiProvider(ModelProvider):
    name = "gemini"

    def __init__(self, api_key: str, preferred_models: list[str] | None = None) -> None:
        self._client = genai.Client(
            api_key=api_key,
            # HttpOptions.timeout is milliseconds, unlike every other
            # provider's client here -- see PROVIDER_REQUEST_TIMEOUT_SECONDS.
            http_options=types.HttpOptions(timeout=int(PROVIDER_REQUEST_TIMEOUT_SECONDS * 1000)),
        )
        self._preferred_models = preferred_models or PREFERRED_MODELS
        self._resolved_candidates: list[str] | None = None
        self._exhausted: set[str] = set()

    def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                name=candidate, provider=self.name, supports_tools=True, cost_tier="free",
                # The flash family in PREFERRED_MODELS has shared a 1M-token
                # input window across generations; update alongside
                # PREFERRED_MODELS if that ever changes for a listed model.
                context_window=1_048_576,
            )
            for candidate in self._resolve_candidates()
        ]

    def generate(self, request: ModelRequest) -> ModelResponse:
        candidates = [c for c in self._resolve_candidates() if c not in self._exhausted]
        if not candidates:
            raise ModelUnavailableError("no Gemini candidate model is available for this API key")

        contents = _to_gemini_contents(request.messages)
        tools = _to_gemini_tools(request.tools)
        config_kwargs: dict[str, Any] = {}
        if request.system_prompt:
            config_kwargs["system_instruction"] = request.system_prompt
        if tools:
            config_kwargs["tools"] = tools
        if request.max_tokens:
            config_kwargs["max_output_tokens"] = request.max_tokens

        skip_this_call: set[str] = set()
        last_error: Exception | None = None

        for candidate in candidates:
            if candidate in skip_this_call:
                continue
            try:
                response = self._client.models.generate_content(
                    model=candidate, contents=contents, config=types.GenerateContentConfig(**config_kwargs)
                )
            except genai_errors.APIError as exc:
                code = getattr(exc, "code", None)
                message = str(exc)
                if code in (429, 404) or "RESOURCE_EXHAUSTED" in message or "NOT_FOUND" in message:
                    self._exhausted.add(candidate)
                    last_error = exc
                    continue
                if code == 503 or "UNAVAILABLE" in message:
                    skip_this_call.add(candidate)
                    last_error = exc
                    continue
                raise ModelUnavailableError(message) from exc
            except Exception as exc:  # noqa: BLE001 - any other failure means "try the next provider"
                raise ModelUnavailableError(str(exc)) from exc

            return _to_model_response(response, candidate, self.name)

        raise ModelUnavailableError(f"every Gemini candidate failed; last error: {last_error}")

    def _resolve_candidates(self) -> list[str]:
        if self._resolved_candidates is not None:
            return self._resolved_candidates

        available = {}
        for model in self._client.models.list():
            short_name = model.name.replace("models/", "")
            available[short_name] = model

        working = []
        for candidate in self._preferred_models:
            info = available.get(candidate)
            if info is None:
                continue
            supported = getattr(info, "supported_actions", None) or []
            if supported and "generateContent" not in supported:
                continue
            working.append(candidate)

        self._resolved_candidates = working
        return working


def _to_gemini_tools(core_tools: list[dict] | None) -> list["types.Tool"] | None:
    if not core_tools:
        return None
    declarations = [
        types.FunctionDeclaration(
            name=tool["name"], description=tool["description"], parameters_json_schema=tool["input_schema"]
        )
        for tool in core_tools
    ]
    return [types.Tool(function_declarations=declarations)]


def _to_gemini_contents(messages: list[ModelMessage]) -> list["types.Content"]:
    contents = []
    for index, msg in enumerate(messages):
        if msg.role == "assistant":
            parts = []
            if msg.tool_calls:
                parts.extend(
                    types.Part(function_call=types.FunctionCall(name=call.name, args=call.arguments, id=call.id))
                    for call in msg.tool_calls
                )
            if msg.content:
                parts.append(types.Part.from_text(text=msg.content))
            if not parts:
                parts = [types.Part.from_text(text="")]
            contents.append(types.Content(role="model", parts=parts))
        elif msg.role == "tool":
            function_name = _lookup_tool_call_name(messages, index)
            contents.append(
                types.Content(
                    role="tool",
                    parts=[types.Part.from_function_response(name=function_name, response=_parse_tool_result(msg.content))],
                )
            )
        else:
            # "user" and any other role (e.g. ContextManager's "system"
            # memory preamble) fold into a user turn — Gemini has no
            # generic mid-conversation system role; system_instruction is
            # a single top-level config value set once, separately.
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=msg.content)]))
    return contents


def _lookup_tool_call_name(messages: list[ModelMessage], tool_message_index: int) -> str:
    target_id = messages[tool_message_index].tool_call_id
    for msg in reversed(messages[:tool_message_index]):
        if msg.role == "assistant" and msg.tool_calls:
            for call in msg.tool_calls:
                if call.id == target_id:
                    return call.name
    return "unknown_function"


def _parse_tool_result(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return {"result": content}
    return payload if isinstance(payload, dict) else {"result": payload}


def _to_model_response(response, model_name: str, provider_name: str) -> ModelResponse:
    function_calls = getattr(response, "function_calls", None) or []
    tool_calls = [
        ToolCall(id=(fc.id or f"{model_name}-call-{i}"), name=fc.name, arguments=dict(fc.args or {}))
        for i, fc in enumerate(function_calls)
    ]
    try:
        text = response.text or ""
    except Exception:  # noqa: BLE001 - some SDK versions raise accessing .text with no text parts
        text = ""
    return ModelResponse(text=text, model_name=model_name, provider=provider_name, tool_calls=tool_calls, raw=response)
