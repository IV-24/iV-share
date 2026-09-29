"""Groq adapter: a thin OpenAICompatibleProvider subclass pointed at
Groq's endpoint. All the actual translation logic (message/tool schema
shapes, tool_calls parsing) lives in adapters/models/openai_compatible.py
and is shared with Mistral — see that module's docstring."""

from adapters.models.openai_compatible import OpenAICompatibleProvider

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "llama-3.3-70b-versatile"


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"

    def __init__(self, api_key: str, model: str = GROQ_MODEL) -> None:
        super().__init__(api_key=api_key, model=model, base_url=GROQ_BASE_URL, context_window=128000)
