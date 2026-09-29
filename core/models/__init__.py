"""Model provider abstraction. core/agent never imports an LLM SDK
directly — it talks to a ModelProvider, and adapters/models/ implements
one per real provider (Gemini, Mistral, Groq, Claude, ...)."""

from core.models.base import ModelInfo, ModelMessage, ModelProvider, ModelRequest, ModelResponse, ModelUnavailableError
from core.models.registry import ModelRegistry

__all__ = [
    "ModelMessage", "ModelRequest", "ModelResponse", "ModelInfo",
    "ModelProvider", "ModelUnavailableError", "ModelRegistry",
]
