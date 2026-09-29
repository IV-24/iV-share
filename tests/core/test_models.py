import pytest

from core.models.base import ModelMessage, ModelRequest, ModelUnavailableError
from core.models.null_provider import NullModelProvider
from core.models.registry import ModelRegistry


class AlwaysUnavailableProvider:
    name = "flaky"

    def list_models(self):
        return []

    def generate(self, request):
        raise ModelUnavailableError("simulated outage")


def test_null_provider_echoes_last_user_message():
    provider = NullModelProvider()
    request = ModelRequest(messages=[ModelMessage(role="user", content="hello")])
    response = provider.generate(request)
    assert "hello" in response.text
    assert response.provider == "null"


def test_registry_falls_through_unavailable_providers():
    registry = ModelRegistry()
    registry.register(AlwaysUnavailableProvider())
    registry.register(NullModelProvider())

    request = ModelRequest(messages=[ModelMessage(role="user", content="hi")])
    response = registry.generate_with_fallback(request, ["flaky", "null"])

    assert response.provider == "null"


def test_registry_skips_unregistered_providers_in_order():
    registry = ModelRegistry()
    registry.register(NullModelProvider())

    request = ModelRequest(messages=[ModelMessage(role="user", content="hi")])
    response = registry.generate_with_fallback(request, ["not_configured", "null"])

    assert response.provider == "null"


def test_registry_raises_when_every_provider_fails():
    registry = ModelRegistry()
    registry.register(AlwaysUnavailableProvider())

    request = ModelRequest(messages=[ModelMessage(role="user", content="hi")])
    with pytest.raises(ModelUnavailableError):
        registry.generate_with_fallback(request, ["flaky"])


class _ExplodingProvider:
    """A provider whose failure is NOT a ModelUnavailableError -- a bug in
    translation code, an SDK returning an unexpected shape, and so on."""

    name = "exploding"

    def list_models(self):
        return []

    def generate(self, request):
        raise ValueError("unexpected response shape")


def test_fallback_moves_past_a_provider_that_raises_an_unexpected_error():
    """Previously only ModelUnavailableError was caught, so any other
    exception escaped generate_with_fallback, skipped every remaining
    provider, and reached the API as a 500 with no audit row written."""
    registry = ModelRegistry()
    registry.register(_ExplodingProvider())
    registry.register(NullModelProvider())

    response = registry.generate_with_fallback(
        ModelRequest(messages=[ModelMessage(role="user", content="hello")]),
        ["exploding", "null"],
    )

    assert response.provider == "null"


def test_all_providers_failing_unexpectedly_still_raises_model_unavailable():
    registry = ModelRegistry()
    registry.register(_ExplodingProvider())

    with pytest.raises(ModelUnavailableError):
        registry.generate_with_fallback(
            ModelRequest(messages=[ModelMessage(role="user", content="hello")]),
            ["exploding"],
        )


def test_a_failed_provider_attempt_is_reported_on_the_response():
    """On the live run gemini was first in the Coordinator's order and
    /health reported it live, yet every turn was served by mistral: gemini
    was failing on every call and falling through silently, logged only to
    stdout. The operator had no way to see their primary provider was dead."""
    registry = ModelRegistry()
    registry.register(AlwaysUnavailableProvider())
    registry.register(NullModelProvider())

    response = registry.generate_with_fallback(
        ModelRequest(messages=[ModelMessage(role="user", content="hi")]),
        ["flaky", "null"],
    )

    assert response.provider == "null"
    assert [a["provider"] for a in response.failed_attempts] == ["flaky"]
    assert response.failed_attempts[0]["error"]


def test_a_clean_first_attempt_reports_no_failures():
    registry = ModelRegistry()
    registry.register(NullModelProvider())

    response = registry.generate_with_fallback(
        ModelRequest(messages=[ModelMessage(role="user", content="hi")]), ["null"],
    )

    assert response.failed_attempts == []
