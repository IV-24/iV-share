"""Provider-neutral request/response shapes. Any concrete provider
(Gemini's typed Content objects, OpenAI-compatible chat completions,
Anthropic's Messages API, ...) translates to/from this shape at its own
adapter boundary — core/agent only ever sees ModelMessage/ModelRequest/
ModelResponse.

Tool calling is part of this shape, not bolted on separately: a
ModelResponse can carry `tool_calls` instead of (or alongside empty)
`text`, and a ModelMessage can represent either an assistant turn that
requested tool calls or a "tool" role turn carrying one tool's result.
Every provider that supports native tool calling (all four the prototype
used do) has some version of this same request/result loop; this is the
one shape every adapter translates to/from, and it's what lets
core/agent/orchestrator.py run one tool-calling loop instead of each
provider integration reimplementing its own (which is what
backend/app/providers.py did before this).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# Every provider adapter's SDK client is constructed with this as its
# request timeout. Without an explicit value each SDK falls back to its
# own default (600s for both the OpenAI and Anthropic Python clients),
# which means a single hung provider call can hold a Starlette worker
# thread -- and, chained through delegation, the Coordinator's whole
# turn -- for ten minutes with nothing in the response to explain why.
# 30s is generous for a chat completion; ModelRegistry.generate_with_fallback()
# already logs elapsed_ms on every call, so this can be tightened later
# from real data instead of a guess.
PROVIDER_REQUEST_TIMEOUT_SECONDS = 30.0


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelMessage:
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str = ""
    # Set on an "assistant" message that requested tool calls instead of
    # (or in addition to) plain text.
    tool_calls: list[ToolCall] | None = None
    # Set on a "tool" message: which ToolCall.id this result answers.
    tool_call_id: str | None = None


@dataclass
class ModelRequest:
    messages: list[ModelMessage]
    system_prompt: str | None = None
    tools: list[dict[str, Any]] | None = None
    max_tokens: int | None = None


@dataclass
class ModelResponse:
    text: str
    model_name: str
    provider: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None
    # Providers tried before this one answered, each {provider, error}.
    # Fallback used to be entirely silent: a provider failing on every
    # single call still left the system healthy-looking, because the only
    # trace was a stdout warning nobody reads and /health reports a
    # provider as live if it is merely configured. Carrying the attempts
    # on the response lets the run record and /status say which provider
    # actually served, and which quietly did not.
    failed_attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def requests_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class ModelInfo:
    name: str
    provider: str
    context_window: int | None = None
    supports_tools: bool = False
    cost_tier: str = "unknown"  # "free" | "low" | "medium" | "high" | "unknown"


class ModelUnavailableError(Exception):
    """Raised by a provider when it can't serve this request right now
    (quota exhausted, model retired, transient outage, ...). Callers using
    ModelRegistry.generate_with_fallback() catch this and move to the next
    provider in the fallback order; it is not a programming error."""


class ModelProvider(ABC):
    name: str

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """Models this provider can currently serve, for capability
        discovery/routing decisions."""

    @abstractmethod
    def generate(self, request: ModelRequest) -> ModelResponse:
        """Raises ModelUnavailableError if this provider can't serve the
        request right now; any other exception is a genuine error."""
