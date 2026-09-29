"""These tests are the load-bearing ones for the whole security model:
a tool with the wrong permissions must not run, and a tool requiring
approval must not run without one. A permission/approval system that only
exists in prompts is not a security system — these assert it's real."""

from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.permissions.manager import PermissionManager
from core.permissions.scopes import PermissionScope
from core.storage.memory import InMemoryStorage
from core.tools.base import ExecutionPolicy, RiskLevel, ToolDefinition
from core.tools.registry import ToolRegistry


def make_registry():
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    permissions = PermissionManager(storage, audit)
    approvals = ApprovalManager(storage, audit)
    registry = ToolRegistry(permissions, approvals, audit)
    return registry, permissions, approvals, audit


def read_only_tool_calls():
    calls = []

    def handler(query: str):
        calls.append(query)
        return {"rows": []}

    tool = ToolDefinition(
        name="query_table",
        description="Read-only table query",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        output_schema={"type": "object"},
        handler=handler,
        required_permissions=[PermissionScope.DATABASE_READ],
        risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    )
    return tool, calls


def destructive_tool_calls():
    calls = []

    def handler(target: str):
        calls.append(target)
        return {"deleted": target}

    tool = ToolDefinition(
        name="delete_record",
        description="Deletes a record permanently",
        input_schema={"type": "object", "properties": {"target": {"type": "string"}}},
        output_schema={"type": "object"},
        handler=handler,
        required_permissions=[PermissionScope.DATABASE_WRITE],
        risk_level=RiskLevel.CRITICAL,
        execution_policy=ExecutionPolicy.REQUIRES_APPROVAL,
    )
    return tool, calls


def test_execute_without_permission_is_denied_and_does_not_run_handler():
    registry, _, _, _ = make_registry()
    tool, calls = read_only_tool_calls()
    registry.register(tool)

    result = registry.execute("query_table", {"query": "select *"}, principal="research")

    assert result.success is False
    assert "missing" in result.error.lower()
    assert calls == []  # the handler must never have run


def test_execute_with_permission_runs_handler():
    registry, permissions, _, _ = make_registry()
    tool, calls = read_only_tool_calls()
    registry.register(tool)
    permissions.grant("research", PermissionScope.DATABASE_READ, granted_by="king")

    result = registry.execute("query_table", {"query": "select *"}, principal="research")

    assert result.success is True
    assert calls == ["select *"]


def test_requires_approval_tool_blocks_without_approval_even_with_permission():
    registry, permissions, approvals, _ = make_registry()
    tool, calls = destructive_tool_calls()
    registry.register(tool)
    permissions.grant("engineering", PermissionScope.DATABASE_WRITE, granted_by="king")

    result = registry.execute("delete_record", {"target": "prod-row-1"}, principal="engineering")

    assert result.success is False
    assert result.error == "approval_required"
    assert result.approval_id is not None
    assert calls == []  # the handler must never have run
    assert approvals.get(result.approval_id).status.value == "requested"


def test_requires_approval_tool_runs_once_approved():
    registry, permissions, approvals, _ = make_registry()
    tool, calls = destructive_tool_calls()
    registry.register(tool)
    permissions.grant("engineering", PermissionScope.DATABASE_WRITE, granted_by="king")

    first = registry.execute("delete_record", {"target": "prod-row-1"}, principal="engineering")
    approvals.decide(first.approval_id, approved=True, decided_by="owner")

    second = registry.execute(
        "delete_record", {"target": "prod-row-1"}, principal="engineering", approval_id=first.approval_id
    )

    assert second.success is True
    assert calls == ["prod-row-1"]
    assert approvals.get(first.approval_id).status.value == "executed"


def test_requires_approval_tool_still_blocked_if_approval_was_denied():
    registry, permissions, approvals, _ = make_registry()
    tool, calls = destructive_tool_calls()
    registry.register(tool)
    permissions.grant("engineering", PermissionScope.DATABASE_WRITE, granted_by="king")

    first = registry.execute("delete_record", {"target": "prod-row-1"}, principal="engineering")
    approvals.decide(first.approval_id, approved=False, decided_by="owner")

    second = registry.execute(
        "delete_record", {"target": "prod-row-1"}, principal="engineering", approval_id=first.approval_id
    )

    assert second.success is False
    assert calls == []


def test_unknown_tool_fails_cleanly():
    registry, _, _, _ = make_registry()
    result = registry.execute("nonexistent", {}, principal="engineering")
    assert result.success is False
    assert "unknown tool" in result.error


def test_handler_exception_is_captured_not_raised():
    registry, permissions, _, _ = make_registry()

    def boom(**_kwargs):
        raise RuntimeError("simulated failure")

    tool = ToolDefinition(
        name="flaky",
        description="always fails",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object"},
        handler=boom,
        required_permissions=[],
        risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    )
    registry.register(tool)

    result = registry.execute("flaky", {}, principal="engineering")
    assert result.success is False
    assert "simulated failure" in result.error


def test_list_tools_exposes_metadata_for_discovery():
    registry, _, _, _ = make_registry()
    tool, _ = destructive_tool_calls()
    registry.register(tool)

    [description] = registry.list_tools()
    assert description["name"] == "delete_record"
    assert description["risk_level"] == "critical"
    assert description["execution_policy"] == "requires_approval"
    assert description["required_permissions"] == [PermissionScope.DATABASE_WRITE]


def test_calling_an_unknown_tool_is_audited():
    """registry.execute returned early for an unregistered tool before
    reaching _log, so a model repeatedly inventing tool names left no trace
    at all -- the one signal that a prompt or a role's tool list is wrong."""
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    registry = ToolRegistry(PermissionManager(storage, audit), ApprovalManager(storage, audit), audit)

    result = registry.execute("no_such_tool", {}, principal="Coordinator")

    assert result.success is False
    events = audit.for_actor("Coordinator")
    assert [e.action for e in events] == ["tool.execute:no_such_tool"]
    assert events[0].outcome == "error"
