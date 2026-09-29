"""Assembles a bounded context window from conversation history and
relevant memory for a given request — separate from long-term memory
storage itself."""

from core.context.manager import ContextManager

__all__ = ["ContextManager"]
