from core.agent.orchestrator import AgentOrchestrator
from core.agent.roles import AgentRole
from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.context.manager import ContextManager
from core.memory.base import MemoryStore
import pytest

from core.models.base import (
    ModelInfo,
    ModelMessage,
    ModelProvider,
    ModelResponse,
    ModelUnavailableError,
    ToolCall,
)
from core.models.null_provider import NullModelProvider
from core.models.registry import ModelRegistry
from core.permissions.manager import PermissionManager
from core.permissions.scopes import PermissionScope
from core.storage.memory import InMemoryStorage
from core.tools.base import ExecutionPolicy, RiskLevel, ToolDefinition
from core.tools.registry import ToolRegistry


class ScriptedProvider(ModelProvider):
    """Returns each ModelResponse in `script`, in order, one per call to
    generate() — lets tests drive a multi-turn tool-calling exchange
    deterministically, with no real model involved."""

    name = "scripted"

    def __init__(self, script: list[ModelResponse]) -> None:
        self._script = list(script)

    def list_models(self) -> list[ModelInfo]:
        return []

    def generate(self, request):
        return self._script.pop(0)


def make_orchestrator():
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    permissions = PermissionManager(storage, audit)
    approvals = ApprovalManager(storage, audit)
    tools = ToolRegistry(permissions, approvals, audit)
    models = ModelRegistry()
    models.register(NullModelProvider())
    memory = MemoryStore(storage)

    tools.register(ToolDefinition(
        name="create_task",
        description="Creates a task",
        input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
        output_schema={"type": "object"},
        handler=lambda title: {"id": "task-1", "title": title},
        required_permissions=[PermissionScope.DATABASE_WRITE],
        risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))

    orchestrator = AgentOrchestrator(models, tools, memory, ContextManager())
    return orchestrator, permissions, models


def test_handle_message_uses_role_provider_order_with_fallback_to_null():
    orchestrator, _, _ = make_orchestrator()
    role = AgentRole(name="Planning Specialist", description="plans things", model_provider_order=["null"])

    result = orchestrator.handle_message(role, "what's next?")

    assert result.role == "Planning Specialist"
    assert "what's next?" in result.response.text


def test_role_can_only_run_tools_it_was_configured_with():
    orchestrator, permissions, _ = make_orchestrator()
    permissions.grant("Planning Specialist", PermissionScope.DATABASE_WRITE, granted_by="king")
    role_without_access = AgentRole(name="Planning Specialist", description="plans things", tool_names=[])

    result = orchestrator.run_tool(role_without_access, "create_task", {"title": "ship it"})

    assert result.success is False
    assert "not configured with access" in result.error


def test_role_with_tool_access_and_permission_can_run_it():
    orchestrator, permissions, _ = make_orchestrator()
    permissions.grant("Planning Specialist", PermissionScope.DATABASE_WRITE, granted_by="king")
    role_with_access = AgentRole(name="Planning Specialist", description="plans things", tool_names=["create_task"])

    result = orchestrator.run_tool(role_with_access, "create_task", {"title": "ship it"})

    assert result.success is True
    assert result.output["title"] == "ship it"


def test_role_with_tool_access_but_no_permission_is_still_denied():
    orchestrator, _, _ = make_orchestrator()
    role_with_access = AgentRole(name="Planning Specialist", description="plans things", tool_names=["create_task"])

    result = orchestrator.run_tool(role_with_access, "create_task", {"title": "ship it"})

    assert result.success is False
    assert "missing" in result.error.lower()


def test_handle_message_runs_a_requested_tool_and_returns_final_answer():
    orchestrator, permissions, models = make_orchestrator()
    permissions.grant("Engineering Specialist", PermissionScope.DATABASE_WRITE, granted_by="king")
    role = AgentRole(
        name="Engineering Specialist", description="builds things",
        tool_names=["create_task"], model_provider_order=["scripted"],
    )
    models.register(ScriptedProvider([
        ModelResponse(
            text="", model_name="m", provider="scripted",
            tool_calls=[ToolCall(id="call-1", name="create_task", arguments={"title": "ship it"})],
        ),
        ModelResponse(text="Done — task created.", model_name="m", provider="scripted"),
    ]))

    result = orchestrator.handle_message(role, "please create a task")

    assert result.response.text == "Done — task created."


def test_handle_message_feeds_back_tool_access_denial_instead_of_crashing():
    orchestrator, _, models = make_orchestrator()
    role = AgentRole(
        name="Writing Specialist", description="writes things",
        tool_names=[],  # no tool access at all
        model_provider_order=["scripted"],
    )
    models.register(ScriptedProvider([
        ModelResponse(
            text="", model_name="m", provider="scripted",
            tool_calls=[ToolCall(id="call-1", name="create_task", arguments={"title": "ship it"})],
        ),
        ModelResponse(text="I couldn't do that, so here's a summary instead.", model_name="m", provider="scripted"),
    ]))

    result = orchestrator.handle_message(role, "please create a task")

    assert result.response.text == "I couldn't do that, so here's a summary instead."


def test_handle_message_gives_up_after_max_tool_iterations():
    orchestrator, permissions, models = make_orchestrator()
    permissions.grant("Engineering Specialist", PermissionScope.DATABASE_WRITE, granted_by="king")
    role = AgentRole(
        name="Engineering Specialist", description="builds things",
        tool_names=["create_task"], model_provider_order=["scripted"],
    )
    always_calls_tool = ModelResponse(
        text="", model_name="m", provider="scripted",
        tool_calls=[ToolCall(id="call-1", name="create_task", arguments={"title": "ship it"})],
    )
    models.register(ScriptedProvider([always_calls_tool, always_calls_tool]))

    result = orchestrator.handle_message(role, "please create a task", max_tool_iterations=2)

    assert "stuck in a tool-calling loop" in result.response.text


class _DiesAfterScript(ModelProvider):
    """Plays a script, then becomes unavailable — a free-tier provider
    hitting its rate limit partway through a long agentic turn."""

    name = "dying"

    def __init__(self, script):
        self._script = list(script)

    def list_models(self):
        return []

    def generate(self, request):
        if not self._script:
            raise ModelUnavailableError("simulated 429 rate limit")
        return self._script.pop(0)


def test_a_provider_dying_mid_turn_returns_the_work_already_done():
    """A live run spent 106 seconds and 24 tool calls, then lost every one
    of them because ModelUnavailableError propagated out of the loop and
    discarded the accumulated messages. The tools had really run; only the
    report of them was thrown away."""
    orchestrator, permissions, models = make_orchestrator()
    permissions.grant("Planning Specialist", PermissionScope.DATABASE_WRITE, granted_by="test")
    models.register(_DiesAfterScript([
        ModelResponse(
            text=None, model_name="m", provider="dying",
            tool_calls=[ToolCall(id="c1", name="create_task", arguments={"title": "ship it"})],
        ),
    ]))
    role = AgentRole(
        name="Planning Specialist", description="plans things",
        tool_names=["create_task"], model_provider_order=["dying"],
    )

    result = orchestrator.handle_message(role, "make me a task")

    assert result.status == "partial"
    assert "create_task" in result.response.text
    assert result.completed_tool_calls == ["create_task"]


def test_a_provider_unavailable_before_any_work_still_raises():
    """Nothing was accomplished, so there is nothing to report — the
    caller's existing no-model handling is the right path."""
    orchestrator, _, models = make_orchestrator()
    models.register(_DiesAfterScript([]))
    role = AgentRole(
        name="Planning Specialist", description="plans things",
        tool_names=["create_task"], model_provider_order=["dying"],
    )

    with pytest.raises(ModelUnavailableError):
        orchestrator.handle_message(role, "make me a task")


def test_a_completed_turn_reports_ok_status():
    orchestrator, _, _ = make_orchestrator()
    role = AgentRole(name="Planning Specialist", description="plans things", model_provider_order=["null"])

    assert orchestrator.handle_message(role, "hello").status == "ok"


def test_a_tool_call_outside_the_roles_list_is_audited():
    """The common shape of a hallucinated tool call: a name the role was
    never given. run_tool rejects it before ToolRegistry.execute is
    reached, so auditing only inside the registry left this path silent."""
    orchestrator, _, _ = make_orchestrator()
    role = AgentRole(name="Planning Specialist", description="plans things", tool_names=[])

    result = orchestrator.run_tool(role, "definitely_not_registered", {})

    assert result.success is False
    events = orchestrator._tools._audit.for_actor("Planning Specialist")
    assert [e.action for e in events] == ["tool.execute:definitely_not_registered"]
    assert events[0].outcome == "denied"
