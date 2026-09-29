"""Approving a consequential action must make it happen, exactly once, with
no more authority than the agent that asked for it.

The audit found the gate blocking correctly and never opening: no
production call site passed an approval_id, so a granted approval was never
consumed and the identical retry filed a new request. Seven approval rows
accumulated across the audit and none ever reached `executed`.
"""

import pytest

from core.agent.catalog import AgentCatalog
from core.agent.orchestrator import AgentOrchestrator
from core.agent.roles import AgentRole
from core.approvals.base import ApprovalStatus
from core.approvals.execution import ApprovalNotExecutable, execute_approved_request
from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.context.manager import ContextManager
from core.memory.base import MemoryStore
from core.models.registry import ModelRegistry
from core.permissions.manager import PermissionManager
from core.permissions.scopes import PermissionScope
from core.storage.memory import InMemoryStorage
from core.tools.base import ExecutionPolicy, RiskLevel, ToolDefinition
from core.tools.registry import ToolRegistry

ROLE = AgentRole(
    name="Engineering Specialist", description="builds things",
    tool_names=["dangerous_push"], permission_scopes=[PermissionScope.GITHUB_WRITE],
)


class _Catalog(AgentCatalog):
    def get(self, name):
        return ROLE if name in (ROLE.name, "engineering") else None


def _harness():
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    permissions = PermissionManager(storage, audit)
    approvals = ApprovalManager(storage, audit)
    tools = ToolRegistry(permissions, approvals, audit)
    calls: list[dict] = []

    tools.register(ToolDefinition(
        name="dangerous_push", description="pushes",
        input_schema={"type": "object"}, output_schema={"type": "object"},
        handler=lambda **kwargs: calls.append(kwargs) or {"pushed": True},
        required_permissions=[PermissionScope.GITHUB_WRITE],
        risk_level=RiskLevel.HIGH, execution_policy=ExecutionPolicy.REQUIRES_APPROVAL,
    ))
    permissions.grant(ROLE.name, PermissionScope.GITHUB_WRITE, granted_by="test")
    orchestrator = AgentOrchestrator(ModelRegistry(), tools, MemoryStore(storage), ContextManager())
    return orchestrator, approvals, permissions, calls


def _request_it(orchestrator):
    return orchestrator.run_tool(ROLE, "dangerous_push", {"repo": "target", "branch": "main"})


def test_the_request_stores_the_arguments_structurally():
    """They lived only inside a display string built with repr(), so even a
    consumer that existed would have had to parse it back."""
    orchestrator, approvals, _, _ = _harness()

    result = _request_it(orchestrator)

    stored = approvals.get(result.approval_id)
    assert stored.arguments == {"repo": "target", "branch": "main"}
    assert stored.requested_by == ROLE.name


def test_approving_then_executing_runs_the_action_once():
    orchestrator, approvals, _, calls = _harness()
    approval_id = _request_it(orchestrator).approval_id
    assert calls == [], "nothing may run before the decision"

    approvals.decide(approval_id, approved=True, decided_by="owner")
    result = execute_approved_request(
        approvals.get(approval_id), orchestrator=orchestrator, catalog=_Catalog(), approvals=approvals
    )

    assert result.success is True
    assert calls == [{"repo": "target", "branch": "main"}]
    assert approvals.get(approval_id).status == ApprovalStatus.EXECUTED


def test_the_same_approval_cannot_be_executed_twice():
    orchestrator, approvals, _, calls = _harness()
    approval_id = _request_it(orchestrator).approval_id
    approvals.decide(approval_id, approved=True, decided_by="owner")
    execute_approved_request(
        approvals.get(approval_id), orchestrator=orchestrator, catalog=_Catalog(), approvals=approvals
    )

    with pytest.raises(ApprovalNotExecutable):
        execute_approved_request(
            approvals.get(approval_id), orchestrator=orchestrator, catalog=_Catalog(), approvals=approvals
        )

    assert len(calls) == 1


def test_a_denied_request_is_not_executable():
    orchestrator, approvals, _, calls = _harness()
    approval_id = _request_it(orchestrator).approval_id
    approvals.decide(approval_id, approved=False, decided_by="owner")

    with pytest.raises(ApprovalNotExecutable):
        execute_approved_request(
            approvals.get(approval_id), orchestrator=orchestrator, catalog=_Catalog(), approvals=approvals
        )

    assert calls == []


def test_execution_runs_as_the_requesting_agent_not_the_approver():
    """Approving must not lend the action authority it did not have. If the
    requesting role's scope is revoked between request and approval, the
    execution has to fail the permission check like any other call."""
    orchestrator, approvals, permissions, calls = _harness()
    approval_id = _request_it(orchestrator).approval_id
    approvals.decide(approval_id, approved=True, decided_by="owner")
    permissions.revoke(ROLE.name, PermissionScope.GITHUB_WRITE, revoked_by="owner")

    result = execute_approved_request(
        approvals.get(approval_id), orchestrator=orchestrator, catalog=_Catalog(), approvals=approvals
    )

    assert result.success is False
    assert "missing required permission" in result.error
    assert calls == []


def test_an_unknown_requesting_agent_is_not_executable():
    orchestrator, approvals, _, calls = _harness()
    approval_id = _request_it(orchestrator).approval_id
    approvals.decide(approval_id, approved=True, decided_by="owner")

    class Empty(AgentCatalog):
        def get(self, name):
            return None

    with pytest.raises(ApprovalNotExecutable):
        execute_approved_request(
            approvals.get(approval_id), orchestrator=orchestrator, catalog=Empty(), approvals=approvals
        )
    assert calls == []
