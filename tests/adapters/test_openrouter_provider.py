"""OPENROUTER_API_KEY was read by settings but registered by nothing, so a
key set in the environment did nothing at all. These tests pin the fix:
the adapter exists, and the registry actually wires it up."""

from adapters.models import register_configured_providers
from adapters.models.openrouter_provider import DEFAULT_OPENROUTER_MODEL, OpenRouterProvider
from core.configuration.settings import ModelConfig
from core.models.registry import ModelRegistry


def test_provider_reports_its_configured_model():
    provider = OpenRouterProvider(api_key="not-a-real-key")

    models = provider.list_models()

    assert provider.name == "openrouter"
    assert models[0].name == DEFAULT_OPENROUTER_MODEL
    assert models[0].supports_tools is True


def test_model_can_be_overridden_by_environment(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "some-org/some-model")

    assert OpenRouterProvider(api_key="k").list_models()[0].name == "some-org/some-model"


def test_a_configured_key_actually_registers_the_provider():
    registry = ModelRegistry()

    register_configured_providers(registry, ModelConfig(openrouter_api_key="not-a-real-key"))

    assert registry.get("openrouter") is not None
    assert "openrouter" in registry.provider_names()


def test_no_key_registers_nothing():
    registry = ModelRegistry()

    register_configured_providers(registry, ModelConfig())

    assert registry.provider_names() == []
