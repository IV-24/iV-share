from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.permissions.manager import PermissionManager
from core.permissions.scopes import PermissionScope
from core.storage.memory import InMemoryStorage
from core.tools.guardian import register_guardian_tools
from core.tools.registry import ToolRegistry


def make_registry():
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    permissions = PermissionManager(storage, audit)
    approvals = ApprovalManager(storage, audit)
    registry = ToolRegistry(permissions, approvals, audit)
    register_guardian_tools(registry, audit=audit, permissions=permissions)
    return registry, permissions, audit


def test_guardian_tools_require_permissions_manage():
    registry, _, _ = make_registry()
    result = registry.execute("flag_denied_action_spikes", {"principal": "engineering"}, principal="security")
    assert result.success is False
    assert "missing" in result.error.lower()


def test_flag_denied_action_spikes_via_tool_registry():
    registry, permissions, audit = make_registry()
    permissions.grant("security", PermissionScope.PERMISSIONS_MANAGE, granted_by="owner")
    for _ in range(5):
        audit.record(actor="engineering", action="x", resource="y", outcome="denied")

    result = registry.execute(
        "flag_denied_action_spikes", {"principal": "engineering"}, principal="security"
    )

    assert result.success is True
    assert result.output["flagged"] is True


def test_suspend_principal_via_tool_registry_runs_immediately_without_approval():
    """Guardian suspension is AUTO, not REQUIRES_APPROVAL -- an emergency
    stop that had to wait for a human sign-off wouldn't be one."""
    registry, permissions, _ = make_registry()
    permissions.grant("security", PermissionScope.PERMISSIONS_MANAGE, granted_by="owner")
    permissions.grant("engineering", "database.write", granted_by="owner")

    result = registry.execute(
        "suspend_principal", {"principal": "engineering", "reason": "acting outside scope"}, principal="security"
    )

    assert result.success is True
    assert result.approval_id is None  # no approval was created or needed
    assert permissions.is_granted("engineering", "database.write") is False
