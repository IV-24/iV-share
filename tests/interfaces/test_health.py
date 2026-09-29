"""/health is what a supervisor and the frontend poll, so the tests focus
on it telling the truth: a real persistence round-trip (not a liveness
guess), 'degraded' rather than 'error' when only a model provider is
missing, and never a secret value in either endpoint's body."""

import pytest
from fastapi.testclient import TestClient

from core.models.base import ModelInfo, ModelProvider, ModelRequest, ModelResponse
from core.models.null_provider import NullModelProvider
from interfaces.api.health import build_health, build_status, check_model_harness, check_persistence
from interfaces.api.main import create_app
from tests.interfaces.test_api import SECRET, build_test_runtime


class BrokenStorage:
    def insert(self, *a, **k):
        raise OSError("disk full")

    def get(self, *a, **k):
        return None

    def delete(self, *a, **k):
        return False


class FakeProvider(ModelProvider):
    name = "fake"

    def list_models(self):
        return [ModelInfo(name="fake-1", provider="fake", supports_tools=True)]

    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="", model_name="fake-1", provider="fake")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("API_ACCESS_SECRET", SECRET)
    app = create_app(runtime_factory=build_test_runtime)
    with TestClient(app) as test_client:
        yield test_client


def test_health_is_reachable_without_a_secret(client):
    assert client.get("/health").status_code == 200


def test_health_reports_degraded_when_only_the_null_provider_is_registered(client):
    body = client.get("/health").json()

    # The test runtime registers only NullModelProvider, which proves
    # nothing about real model access — so the server is up but degraded.
    assert body["status"] == "degraded"
    assert body["checks"]["persistence"]["ok"] is True
    assert body["checks"]["model_harness"]["ok"] is False
    assert body["checks"]["model_harness"]["live_providers"] == []


def test_health_reports_ok_once_a_real_provider_is_registered(client):
    client.app.state.runtime.models.register(FakeProvider())

    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert "fake" in body["checks"]["model_harness"]["live_providers"]


def test_persistence_check_round_trips_and_cleans_up(client):
    storage = client.app.state.runtime.storage

    assert check_persistence(storage)["ok"] is True
    assert storage.query("_healthcheck") == []


def test_persistence_check_reports_a_failing_backend():
    result = check_persistence(BrokenStorage())

    assert result["ok"] is False
    assert result["error"] == "OSError"


def test_model_harness_check_discounts_the_null_provider():
    from core.models.registry import ModelRegistry

    registry = ModelRegistry()
    registry.register(NullModelProvider())

    result = check_model_harness(registry)

    assert result["ok"] is False
    assert "no real model provider" in result["detail"]


def test_health_never_contains_a_secret_value(client, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_this_must_never_appear")

    body = client.get("/health").text

    assert "gsk_this_must_never_appear" not in body


def test_status_requires_the_secret(client):
    assert client.get("/status").status_code == 403
    assert client.get(f"/status?secret={SECRET}").status_code == 200


def test_status_reports_capabilities_without_secret_values(client, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_this_must_never_appear")

    response = client.get(f"/status?secret={SECRET}")
    body = response.json()

    assert "gsk_this_must_never_appear" not in response.text
    assert body["secrets_configured"]["GROQ_API_KEY"] is True
    assert body["tools"]["count"] > 0
    assert "main" in body["safety"]["protected_branches"]


def test_build_health_flags_an_incomplete_runtime():
    runtime = build_test_runtime()
    runtime.orchestrator = None

    assert build_health(runtime, version="test")["status"] == "error"


def test_build_status_lists_tools_requiring_approval():
    runtime = build_test_runtime()

    status = build_status(runtime, version="test")

    assert isinstance(status["tools"]["requiring_approval"], list)
    assert status["persistence"]["ok"] is True


def test_status_reports_delegation_activity():
    """Delegation is prompt-driven (core/agent/delegation.py), so nothing
    forces the Coordinator to ever call it -- a silent collapse to
    "answers everything alone" is otherwise indistinguishable from "the
    owner hasn't asked anything worth delegating" from the outside. This
    is the number that tells them apart."""
    runtime = build_test_runtime()
    runtime.audit.record(actor="Research Specialist", action="agent.delegate", resource="Research Specialist")
    runtime.audit.record(actor="Security Specialist", action="agent.delegate", resource="Security Specialist")
    runtime.audit.record(actor="Research Specialist", action="agent.delegate", resource="Research Specialist")
    runtime.audit.record(actor="Coordinator", action="chat.turn", resource="some-conversation")

    status = build_status(runtime, version="test")

    activity = status["delegation_activity"]
    assert activity["ok"] is True
    assert activity["delegations_last_24h"] == 3
    assert activity["by_agent_last_24h"] == {"Research Specialist": 2, "Security Specialist": 1}
