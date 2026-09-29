from core.audit.log import AuditLog
from core.permissions.manager import PermissionManager
from core.permissions.scopes import PermissionScope
from core.storage.memory import InMemoryStorage


def make_manager():
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    return PermissionManager(storage, audit), audit


def test_ungranted_scope_is_denied():
    permissions, _ = make_manager()
    assert permissions.is_granted("engineering", PermissionScope.DATABASE_WRITE) is False


def test_grant_then_check():
    permissions, _ = make_manager()
    permissions.grant("engineering", PermissionScope.DATABASE_WRITE, granted_by="king")
    assert permissions.is_granted("engineering", PermissionScope.DATABASE_WRITE) is True


def test_revoke_removes_access():
    permissions, _ = make_manager()
    permissions.grant("engineering", PermissionScope.DATABASE_WRITE, granted_by="king")
    permissions.revoke("engineering", PermissionScope.DATABASE_WRITE, revoked_by="king")
    assert permissions.is_granted("engineering", PermissionScope.DATABASE_WRITE) is False


def test_revoke_nonexistent_grant_returns_false():
    permissions, _ = make_manager()
    assert permissions.revoke("engineering", PermissionScope.DATABASE_WRITE, revoked_by="king") is False


def test_require_raises_when_missing():
    permissions, _ = make_manager()
    try:
        permissions.require("engineering", PermissionScope.PRODUCTION_DEPLOY)
        assert False, "expected PermissionError"
    except PermissionError:
        pass


def test_require_passes_when_granted():
    permissions, _ = make_manager()
    permissions.grant("engineering", PermissionScope.CODE_EXECUTE, granted_by="king")
    permissions.require("engineering", PermissionScope.CODE_EXECUTE)  # must not raise


def test_grants_and_revokes_are_audited():
    permissions, audit = make_manager()
    permissions.grant("engineering", PermissionScope.CODE_EXECUTE, granted_by="king")
    permissions.revoke("engineering", PermissionScope.CODE_EXECUTE, revoked_by="king")
    actions = [e.action for e in audit.for_actor("king")]
    assert "permission.grant" in actions
    assert "permission.revoke" in actions


def test_list_grants_excludes_revoked():
    permissions, _ = make_manager()
    permissions.grant("engineering", PermissionScope.CODE_EXECUTE, granted_by="king")
    permissions.grant("engineering", PermissionScope.DATABASE_READ, granted_by="king")
    permissions.revoke("engineering", PermissionScope.CODE_EXECUTE, revoked_by="king")

    active_scopes = {g.scope for g in permissions.list_grants("engineering")}
    assert active_scopes == {PermissionScope.DATABASE_READ}


def test_only_security_role_should_hold_permissions_manage_by_convention():
    """Not an enforced runtime rule (PermissionManager is generic), but
    documents the intended default-roles convention: only 'security'
    is defined with PERMISSIONS_MANAGE in core/agent/roles.py."""
    from core.agent.roles import DEFAULT_ROLES

    holders = [
        name for name, role in DEFAULT_ROLES.items()
        if PermissionScope.PERMISSIONS_MANAGE in role.permission_scopes
    ]
    assert holders == ["security"]
