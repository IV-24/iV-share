"""ModelProvider implementations for real providers. Each one is the only
place that provider's SDK/API shape is allowed to appear — core/agent
only ever sees core.models.base's provider-neutral types."""

from core.configuration.settings import ModelConfig
from core.models.registry import ModelRegistry


def register_configured_providers(models: ModelRegistry, model_config: ModelConfig) -> None:
    """Registers a ModelProvider for every provider with a configured API
    key, skipping the rest. Shared by interfaces/api and interfaces/cli so
    both agree on which providers are available from the same config,
    without duplicating the same four if-blocks in each entrypoint.
    Imports are lazy so importing this module doesn't require every
    provider's SDK to be installed if you only use one."""
    if model_config.groq_api_key:
        from adapters.models.groq_provider import GroqProvider
        models.register(GroqProvider(api_key=model_config.groq_api_key))
    if model_config.gemini_api_key:
        from adapters.models.gemini_provider import GeminiProvider
        models.register(GeminiProvider(api_key=model_config.gemini_api_key))
    if model_config.mistral_api_key:
        from adapters.models.mistral_provider import MistralProvider
        models.register(MistralProvider(api_key=model_config.mistral_api_key))
    if model_config.anthropic_api_key:
        from adapters.models.claude_provider import ClaudeProvider
        models.register(ClaudeProvider(api_key=model_config.anthropic_api_key))
    if model_config.openrouter_api_key:
        from adapters.models.openrouter_provider import OpenRouterProvider
        models.register(OpenRouterProvider(api_key=model_config.openrouter_api_key))
