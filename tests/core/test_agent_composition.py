"""Composing an agent must add capability without adding privilege. The
tests that matter here are the refusals: a composed agent that could
assign itself repository writes, or declare a scope its tools do not
need, would turn a convenience feature into an escalation path."""

import pytest

from core.agent.catalog import AgentCatalog
from core.agent.composition_tools import register_composition_tools
from core.agent.roles import DEFAULT_ROLES
from core.agent.store import (
    COMPOSABLE_TOOLS,
    AgentDefinitionError,
    AgentDefinitionStore,
    scopes_required_by,
)
from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.memory.base import MemoryStore
from core.models.base import ModelInfo, ModelProvider, ModelResponse
from core.models.registry import ModelRegistry
from core.permissions.manager import PermissionManager
from core.permissions.scopes import PermissionScope
from core.projects.base import ProjectStore
from core.storage.memory import InMemoryStorage
from core.tasks.base import TaskStore
from core.tools.registry import ToolRegistry
from core.tools.standard import register_standard_tools
from core.improvements.manager import ImprovementManager


class FakeProvider(ModelProvider):
    def __init__(self, name):
        self.name = name

    def list_models(self):
        return [ModelInfo(name=f"{self.name}-1", provider=self.name)]

    def generate(self, request):
        return ModelResponse(text="ok", model_name=f"{self.name}-1", provider=self.name)


RESERVED = frozenset([k for k in DEFAULT_ROLES] + [r.name for r in DEFAULT_ROLES.values()])


@pytest.fixture
def harness():
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    permissions = PermissionManager(storage, audit)
    approvals = ApprovalManager(storage, audit)
    tools = ToolRegistry(permissions, approvals, audit)
    register_standard_tools(
        tools, projects=ProjectStore(storage), tasks=TaskStore(storage),
        memory=MemoryStore(storage), approvals=approvals,
        improvements=ImprovementManager(storage, approvals, audit),
    )
    models = ModelRegistry()
    models.register(FakeProvider("groq"))
    models.register(FakeProvider("claude"))
    store = AgentDefinitionStore(storage, reserved_names=RESERVED)
    register_composition_tools(tools, store=store, models=models, permissions=permissions, audit=audit)
    return {"tools": tools, "store": store, "permissions": permissions, "audit": audit}


def _create(harness, **kwargs):
    args = {"name": "Grant Writer", "system_prompt": "You write funding applications."}
    args.update(kwargs)
    return harness["tools"].execute("create_agent", args, principal="Coordinator").output


def test_a_composed_agent_becomes_delegatable(harness):
    result = _create(harness, model_provider_order=["claude"], tool_names=["create_memory"])

    assert result["created"] is True
    catalog = AgentCatalog(harness["store"])
    role = catalog.get("Grant Writer")
    assert role is not None
    assert role.description == "You write funding applications."
    assert role.model_provider_order == ["claude"]


def test_scopes_are_derived_from_tools_not_supplied(harness):
    """There is no field for scopes, and the derived set is exactly what
    the chosen tools declare — the property that makes over-privileging
    structurally impossible rather than merely discouraged."""
    result = _create(harness, tool_names=["create_memory", "list_projects"])

    assert set(result["scopes"]) == {PermissionScope.DATABASE_WRITE, PermissionScope.DATABASE_READ}


def test_a_toolless_agent_gets_no_scopes_at_all(harness):
    result = _create(harness, name="Pure Prompt", tool_names=[])

    assert result["scopes"] == []


def test_dangerous_tools_cannot_be_assigned(harness):
    for forbidden in ("run_repository_command", "git_push", "write_repository_file",
                      "suspend_principal", "create_pull_request"):
        result = _create(harness, name=f"Sneaky {forbidden[:6]}", tool_names=[forbidden])

        assert result["created"] is False, forbidden
        assert forbidden in result["error"]


def test_builtin_role_names_cannot_be_shadowed(harness):
    for name in ("engineering", "Security Specialist", "Coordinator"):
        result = _create(harness, name=name)

        assert result["created"] is False
        assert "built-in" in result["error"]


def test_duplicate_names_are_refused(harness):
    _create(harness, name="Analyst")

    assert _create(harness, name="Analyst")["created"] is False


def test_an_unconfigured_model_falls_back_to_what_is_available(harness):
    """Asking for a provider that is not set up should not lose the agent;
    the useful behavior is to use the providers that do exist."""
    result = _create(harness, name="Optimist", model_provider_order=["gpt-9000"])

    assert result["created"] is True
    assert set(result["models"]) <= {"groq", "claude"}


def test_a_blank_prompt_is_refused(harness):
    assert _create(harness, name="Empty", system_prompt="   ")["created"] is False


def test_composition_grants_only_the_derived_scopes(harness):
    _create(harness, name="Librarian", tool_names=["list_recent_memories"])

    grants = {g.scope for g in harness["permissions"].list_grants("Librarian")}
    assert grants == {PermissionScope.DATABASE_READ}
    assert PermissionScope.DATABASE_WRITE not in grants


def test_composition_is_audited(harness):
    _create(harness, name="Recorded")

    events = [e for e in harness["audit"].since("2000-01-01") if e.action == "agent.create"]
    assert [e.resource for e in events] == ["Recorded"]


def test_every_composable_tool_actually_exists(harness):
    """A typo in COMPOSABLE_TOOLS would silently offer a tool that can
    never be assigned, or worse, derive no scopes for a real one."""
    registered = {t["name"] for t in harness["tools"].list_tools()}
    missing = [name for name in COMPOSABLE_TOOLS if name not in registered]

    # Self-inspection tools are registered by an adapter this fixture
    # does not wire, so only assert the core ones resolve.
    assert [m for m in missing if not m.startswith("self_")] == []


def test_scopes_required_by_ignores_unknown_tools(harness):
    assert scopes_required_by(["not_a_tool"], harness["tools"]) == []


def test_store_rejects_a_malformed_name(harness):
    with pytest.raises(AgentDefinitionError):
        harness["store"].create(
            name="!!", description="x", model_provider_order=["groq"], tools=harness["tools"]
        )
