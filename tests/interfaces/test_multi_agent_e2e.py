"""The feature end to end, against the real build_runtime(): a coordinator
composing an agent and then delegating to it, with each turn served by a
different provider. Scripted models rather than real ones — the point is
that the orchestration wiring holds, not that any particular vendor
replies well."""

import pytest

from core.agent.roles import DEFAULT_ROLES, AgentRole
from core.configuration.settings import Settings, StorageConfig, WorkspaceConfig
from core.models.base import ModelInfo, ModelProvider, ModelResponse, ToolCall
from interfaces.api.runtime import build_runtime


class ScriptedProvider(ModelProvider):
    def __init__(self, name, responses):
        self.name = name
        self._responses = list(responses)
        self.calls = 0

    def list_models(self):
        return [ModelInfo(name=f"{self.name}-1", provider=self.name, supports_tools=True)]

    def generate(self, request):
        self.calls += 1
        if not self._responses:
            return ModelResponse(text=f"[{self.name}]", model_name=f"{self.name}-1", provider=self.name)
        return self._responses.pop(0)


def _call(provider, tool, args, call_id="c"):
    return ModelResponse(
        text="", model_name=f"{provider}-1", provider=provider,
        tool_calls=[ToolCall(id=call_id, name=tool, arguments=args)],
    )


def _text(provider, body):
    return ModelResponse(text=body, model_name=f"{provider}-1", provider=provider)


@pytest.fixture
def runtime(tmp_path):
    rt = build_runtime(Settings(
        storage=StorageConfig(local_db_path=str(tmp_path / "iv.db")),
        workspace=WorkspaceConfig(workspace_root=str(tmp_path / "ws")),
    ))
    try:
        yield rt
    finally:
        rt.storage.close()


def test_coordinator_composes_an_agent_then_delegates_to_it(runtime):
    """The whole requested capability in one turn: build a specialist out
    of a prompt and a model, then route work to it."""
    coordinator_model = ScriptedProvider("gemini", [
        _call("gemini", "create_agent", {
            "name": "Grant Writer",
            "system_prompt": "You write funding applications in plain language.",
            "model_provider_order": ["groq"],
            "tool_names": ["create_memory"],
        }, "c1"),
        _call("gemini", "delegate_to_agent", {
            "agent": "Grant Writer", "task": "Draft an opening paragraph.",
        }, "c2"),
        _text("gemini", "I had the Grant Writer draft it; here it is."),
    ])
    grant_writer_model = ScriptedProvider("groq", [_text("groq", "Dear funder, ...")])

    runtime.models.register(coordinator_model)
    runtime.models.register(grant_writer_model)

    coordinator = AgentRole(
        name="Coordinator",
        description=DEFAULT_ROLES["coordinator"].description,
        tool_names=DEFAULT_ROLES["coordinator"].tool_names,
        permission_scopes=DEFAULT_ROLES["coordinator"].permission_scopes,
        model_provider_order=["gemini"],
    )

    result = runtime.orchestrator.handle_message(
        coordinator, "Write me a grant opening", [], max_tool_iterations=6
    )

    assert result.response.text == "I had the Grant Writer draft it; here it is."
    assert grant_writer_model.calls == 1, "the composed agent must have run on its own provider"

    # It persisted, with only the scope its one tool needs.
    definition = runtime.agents.get("Grant Writer")
    assert definition is not None
    assert definition.model_provider_order == ["groq"]
    assert definition.permission_scopes == ["database.write"]

    # And both steps are in the audit log, so the activity report can
    # show that this actually happened.
    actions = [e.action for e in runtime.audit.since("2000-01-01")]
    assert "agent.create" in actions
    assert "agent.delegate" in actions


def test_a_composed_agent_survives_a_restart_with_its_grants(runtime, tmp_path):
    runtime.tools.execute("create_agent", {
        "name": "Librarian", "system_prompt": "You organize notes.",
        "tool_names": ["list_recent_memories"],
    }, principal="Coordinator")
    runtime.storage.close()

    reopened = build_runtime(Settings(
        storage=StorageConfig(local_db_path=str(tmp_path / "iv.db")),
        workspace=WorkspaceConfig(workspace_root=str(tmp_path / "ws")),
    ))
    try:
        assert reopened.catalog.get("Librarian") is not None
        grants = {g.scope for g in reopened.permissions.list_grants("Librarian")}
        assert grants == {"database.read"}, "grants must be restored at bootstrap, not lost on restart"
    finally:
        reopened.storage.close()


def test_the_coordinator_is_configured_to_delegate(runtime):
    """A prompt that describes delegation without the tool attached would
    produce a coordinator that talks about specialists it cannot reach —
    the exact gap this feature closes."""
    coordinator = DEFAULT_ROLES["coordinator"]
    registered = {t["name"] for t in runtime.tools.list_tools()}

    assert "delegate_to_agent" in coordinator.tool_names
    assert "delegate_to_agent" in registered
    assert "list_agents" in coordinator.tool_names
    assert "create_agent" in coordinator.tool_names
    assert "background" in coordinator.description.lower(), (
        "the prompt must tell the coordinator not to claim background work"
    )
