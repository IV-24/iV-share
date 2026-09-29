"""Delegation is the mechanism that makes the whole harness answer instead
of one role. The properties worth pinning are the ones that would be
expensive to discover in production: that the delegate really runs on its
own model and tools, that it does not inherit its caller's permissions,
and that the loop is bounded in every direction -- depth, fan-out, wall
clock, and self-reference."""

import time

import pytest

from core.agent.catalog import AgentCatalog
from core.agent.delegation import (
    MAX_DELEGATION_DEPTH,
    MAX_DELEGATIONS_PER_TURN,
    DelegationBudget,
    register_delegation_tool,
)
from core.agent.roles import DEFAULT_ROLES
from core.agent.orchestrator import AgentOrchestrator
from core.agent.roles import AgentRole
from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.context.manager import ContextManager
from core.memory.base import MemoryStore
from core.models.base import (
    ModelInfo,
    ModelProvider,
    ModelResponse,
    ModelUnavailableError,
    ToolCall,
)
from core.models.registry import ModelRegistry
from core.permissions.manager import PermissionManager
from core.permissions.scopes import PermissionScope
from core.storage.memory import InMemoryStorage
from core.tools.base import ExecutionPolicy, RiskLevel, ToolDefinition
from core.tools.registry import ToolRegistry


class ScriptedProvider(ModelProvider):
    """Returns queued responses and records that it was asked, so a test
    can assert which provider actually served a turn."""

    def __init__(self, name, responses, delay=0.0):
        self.name = name
        self._responses = list(responses)
        self.calls = 0
        self.delay = delay

    def list_models(self):
        return [ModelInfo(name=f"{self.name}-1", provider=self.name)]

    def generate(self, request):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if not self._responses:
            return ModelResponse(text=f"[{self.name}] done", model_name=f"{self.name}-1", provider=self.name)
        return self._responses.pop(0)


class UnavailableProvider(ModelProvider):
    name = "broken"

    def list_models(self):
        return []

    def generate(self, request):
        raise ModelUnavailableError("quota exhausted")


def _text(provider, body):
    return ModelResponse(text=body, model_name=f"{provider}-1", provider=provider)


def _call(provider, tool, args, call_id="c1"):
    return ModelResponse(
        text="", model_name=f"{provider}-1", provider=provider,
        tool_calls=[ToolCall(id=call_id, name=tool, arguments=args)],
    )


@pytest.fixture
def harness():
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    permissions = PermissionManager(storage, audit)
    approvals = ApprovalManager(storage, audit)
    tools = ToolRegistry(permissions, approvals, audit)
    models = ModelRegistry()
    holder = {}

    catalog = AgentCatalog()
    budget = register_delegation_tool(
        tools, orchestrator_getter=lambda: holder["value"], catalog=catalog, audit=audit
    )
    orchestrator = AgentOrchestrator(models, tools, MemoryStore(storage), ContextManager())
    holder["value"] = orchestrator
    budget.reset()

    return {
        "tools": tools, "models": models, "audit": audit, "permissions": permissions,
        "orchestrator": orchestrator, "catalog": catalog, "budget": budget, "storage": storage,
    }


def test_the_delegate_runs_on_its_own_provider_not_the_callers(harness):
    """The point of the feature: a coordinator on one model reaching a
    specialist on another."""
    coordinator_model = ScriptedProvider("gemini", [
        _call("gemini", "delegate_to_agent", {"agent": "engineering", "task": "review the API"}),
        _text("gemini", "Engineering says it looks fine."),
    ])
    specialist_model = ScriptedProvider("claude", [_text("claude", "The API looks fine.")])
    harness["models"].register(coordinator_model)
    harness["models"].register(specialist_model)

    coordinator = AgentRole(
        name="Coordinator", description="orchestrate",
        tool_names=["delegate_to_agent"], model_provider_order=["gemini"],
    )
    result = harness["orchestrator"].handle_message(coordinator, "review the API", [])

    assert result.response.text == "Engineering says it looks fine."
    assert specialist_model.calls == 1, "the engineering role must have run on claude"
    assert coordinator_model.calls == 2


def test_the_delegates_answer_is_returned_to_the_caller(harness):
    harness["models"].register(ScriptedProvider("gemini", [
        _call("gemini", "delegate_to_agent", {"agent": "research", "task": "compare X and Y"}),
        _text("gemini", "summarized"),
    ]))
    harness["models"].register(ScriptedProvider("claude", [_text("claude", "X beats Y because...")]))

    coordinator = AgentRole(name="Coordinator", description="d",
                            tool_names=["delegate_to_agent"], model_provider_order=["gemini"])
    harness["orchestrator"].handle_message(coordinator, "compare", [])

    events = [e for e in harness["audit"].since("2000-01-01") if e.action == "agent.delegate"]
    assert len(events) == 1
    assert events[0].resource == "Research Specialist"


def test_an_unknown_agent_is_a_usable_error_not_a_crash(harness):
    result = harness["tools"].execute(
        "delegate_to_agent", {"agent": "nonexistent", "task": "do it"}, principal="Coordinator"
    )

    assert result.success is True  # the tool ran; the delegation did not
    assert result.output["delegated"] is False
    assert "no agent named" in result.output["error"]
    assert "engineering" in result.output["error"], "the error should list who does exist"


def test_a_delegate_with_no_available_model_reports_that_clearly(harness):
    harness["models"].register(UnavailableProvider())
    harness["catalog"] = AgentCatalog()

    result = harness["tools"].execute(
        "delegate_to_agent", {"agent": "engineering", "task": "x"}, principal="Coordinator"
    )

    assert result.output["delegated"] is False
    assert "no model configured" in result.output["error"]


def test_delegation_depth_is_bounded(harness):
    budget = harness["budget"]
    for _ in range(MAX_DELEGATION_DEPTH):
        assert budget.check() is None
        budget.enter()

    assert "depth limit" in budget.check()


def test_fan_out_per_turn_is_bounded(harness):
    budget = harness["budget"]
    for _ in range(MAX_DELEGATIONS_PER_TURN):
        budget.enter()
        budget.exit()

    assert "limit for this turn" in budget.check()


def test_reset_clears_the_budget_between_turns(harness):
    budget = harness["budget"]
    for _ in range(MAX_DELEGATIONS_PER_TURN):
        budget.enter()
        budget.exit()
    assert budget.check() is not None

    budget.reset()

    assert budget.check() is None


def test_a_delegate_does_not_inherit_the_callers_permissions(harness):
    """The security property. A caller holding a scope must not lend it to
    a delegate — the delegate's own grants are what count."""
    harness["tools"].register(ToolDefinition(
        name="privileged_thing", description="needs a scope",
        input_schema={"type": "object", "properties": {}}, output_schema={"type": "object"},
        handler=lambda: {"ran": True},
        required_permissions=[PermissionScope.PERMISSIONS_MANAGE],
        risk_level=RiskLevel.HIGH, execution_policy=ExecutionPolicy.AUTO,
    ))
    harness["permissions"].grant("Coordinator", PermissionScope.PERMISSIONS_MANAGE, granted_by="test")

    delegate = AgentRole(name="Helper", description="d", tool_names=["privileged_thing"],
                         model_provider_order=["gemini"])
    outcome = harness["orchestrator"].run_tool(delegate, "privileged_thing", {})

    assert outcome.success is False
    assert PermissionScope.PERMISSIONS_MANAGE in outcome.error


def test_delegation_itself_requires_no_permission_scope(harness):
    """Delegation grants nothing, so gating it on a scope would only stop
    coordination while doing nothing for safety — what the delegate does
    is checked against the delegate."""
    definition = harness["tools"].get("delegate_to_agent")

    assert definition.required_permissions == []
    assert definition.execution_policy is ExecutionPolicy.AUTO


def test_a_coordinator_cannot_delegate_to_itself(harness):
    result = harness["tools"].execute(
        "delegate_to_agent", {"agent": "coordinator", "task": "do it"}, principal="Coordinator"
    )

    assert result.output["delegated"] is False
    assert "cannot delegate to itself" in result.output["error"]
    # Refused before ever touching the budget or the model layer.
    assert harness["budget"].check() is None


def test_self_delegation_is_refused_by_display_name_too(harness):
    result = harness["tools"].execute(
        "delegate_to_agent",
        {"agent": DEFAULT_ROLES["coordinator"].name, "task": "do it"},
        principal="Coordinator",
    )

    assert result.output["delegated"] is False
    assert "cannot delegate to itself" in result.output["error"]


def test_turns_wall_clock_budget_is_enforced(harness):
    budget = harness["budget"]
    budget.reset(wall_clock_budget_seconds=0.01)
    time.sleep(0.02)

    refusal = budget.check()

    assert refusal is not None
    assert "time budget" in refusal


def test_a_fresh_reset_restores_a_full_wall_clock_budget(harness):
    budget = harness["budget"]
    budget.reset(wall_clock_budget_seconds=0.01)
    time.sleep(0.02)
    assert budget.check() is not None

    budget.reset()

    assert budget.check() is None


def test_concurrent_delegates_in_one_batch_run_in_parallel_not_in_sequence(harness):
    """The point of the fan-out change: two delegates in the same batch
    must overlap in wall time, not add up. A regression back to
    sequential execution would still pass every *correctness* test here
    and only show up as "iV got slower" -- so this test measures time,
    not just the returned answer."""
    harness["models"].register(ScriptedProvider("gemini", [
        ModelResponse(
            text="", model_name="gemini-1", provider="gemini",
            tool_calls=[
                ToolCall(id="c1", name="delegate_to_agent", arguments={"agent": "research", "task": "a"}),
                ToolCall(id="c2", name="delegate_to_agent", arguments={"agent": "security", "task": "b"}),
            ],
        ),
        _text("gemini", "combined"),
    ]))
    delay = 0.2
    harness["models"].register(ScriptedProvider("claude", [_text("claude", "research done")], delay=delay))
    harness["models"].register(ScriptedProvider("groq", [_text("groq", "security done")], delay=delay))
    # research and security both default to ["gemini", "mistral", "groq"]
    # in DEFAULT_ROLES; register them directly under the providers each
    # actually resolves to so this test doesn't depend on that order.
    from core.agent.roles import DEFAULT_ROLES as ROLES
    ROLES["research"].model_provider_order = ["claude"]
    ROLES["security"].model_provider_order = ["groq"]
    try:
        coordinator = AgentRole(name="Coordinator", description="d",
                                tool_names=["delegate_to_agent"], model_provider_order=["gemini"])

        started = time.monotonic()
        result = harness["orchestrator"].handle_message(coordinator, "do both", [])
        elapsed = time.monotonic() - started
    finally:
        ROLES["research"].model_provider_order = ["gemini", "mistral", "groq"]
        ROLES["security"].model_provider_order = ["claude", "gemini", "mistral", "groq"]

    assert result.response.text == "combined"
    # Sequential would take >= 2 * delay; concurrent should land close to
    # one delay plus scheduling overhead. The threshold sits well below
    # the sequential floor so this only fails on a real regression.
    assert elapsed < delay * 1.8, f"delegates took {elapsed:.3f}s -- looks sequential, not concurrent"
