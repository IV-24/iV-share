"""Mistral adapter: a thin OpenAICompatibleProvider subclass pointed at
Mistral's endpoint. See adapters/models/openai_compatible.py for the
shared translation logic this and GroqProvider both use.

mistral-small-latest is the fast general default per the prototype's
original MODEL_STRATEGY.md intent (fast, coding-capable, cost
optimization) — swap to codestral-latest for a code-specific role later
if that split is wanted, same as backend/app/providers.py noted."""

from adapters.models.openai_compatible import OpenAICompatibleProvider

MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
MISTRAL_MODEL = "mistral-small-latest"


class MistralProvider(OpenAICompatibleProvider):
    name = "mistral"

    def __init__(self, api_key: str, model: str = MISTRAL_MODEL) -> None:
        super().__init__(api_key=api_key, model=model, base_url=MISTRAL_BASE_URL, context_window=128000)
