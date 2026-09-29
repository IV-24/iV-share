"""OpenRouter adapter: another thin OpenAICompatibleProvider subclass —
see adapters/models/openai_compatible.py for the shared translation
logic.

OPENROUTER_API_KEY was already part of ModelConfig before this adapter
existed, so a key set in the environment was silently ignored: settings
read it, nothing registered a provider for it, and a role listing
"openrouter" in its fallback order simply skipped that entry. Config that
is read but never acted on is worse than no config, because it looks
configured.

The default model is deliberately a free-tier one — OpenRouter's value
here is reaching a model none of the other four providers offer, not
becoming the default spend path.
"""

import os

from adapters.models.openai_compatible import OpenAICompatibleProvider

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"

    def __init__(self, api_key: str, model: str | None = None) -> None:
        super().__init__(
            api_key=api_key,
            model=model or os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL),
            base_url=OPENROUTER_BASE_URL,
            context_window=128000,  # the default free llama-3.3-70b model's native window
        )
