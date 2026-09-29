from core.audit.log import AuditLog
from core.guardian.base import find_denied_action_spikes, suspend_principal
from core.permissions.manager import PermissionManager
from core.storage.memory import InMemoryStorage


def make_deps():
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    permissions = PermissionManager(storage, audit)
    return audit, permissions


def test_find_denied_action_spikes_none_when_quiet():
    audit, _ = make_deps()
    audit.record(actor="engineering", action="tool.execute:create_task", resource="task-1", outcome="success")
    assert find_denied_action_spikes(audit, "engineering") is None


def test_find_denied_action_spikes_flags_repeated_denials():
    audit, _ = make_deps()
    for _ in range(5):
        audit.record(actor="engineering", action="tool.execute:delete_prod", resource="x", outcome="denied")

    flag = find_denied_action_spikes(audit, "engineering", threshold=5)

    assert flag is not None
    assert flag.principal == "engineering"
    assert len(flag.evidence) == 5


def test_find_denied_action_spikes_respects_threshold():
    audit, _ = make_deps()
    for _ in range(4):
        audit.record(actor="engineering", action="x", resource="y", outcome="denied")
    assert find_denied_action_spikes(audit, "engineering", threshold=5) is None


def test_suspend_principal_revokes_every_active_scope():
    audit, permissions = make_deps()
    permissions.grant("engineering", "database.write", granted_by="owner")
    permissions.grant("engineering", "code.execute", granted_by="owner")

    revoked = suspend_principal(permissions, audit, principal="engineering", suspended_by="security", reason="anomaly")

    assert set(revoked) == {"database.write", "code.execute"}
    assert permissions.list_grants("engineering") == []


def test_suspend_principal_is_audited():
    audit, permissions = make_deps()
    permissions.grant("engineering", "database.write", granted_by="owner")

    suspend_principal(permissions, audit, principal="engineering", suspended_by="security", reason="anomaly detected")

    events = audit.for_actor("security")
    suspend_events = [e for e in events if e.action == "guardian.suspend"]
    assert len(suspend_events) == 1
    assert suspend_events[0].metadata["reason"] == "anomaly detected"
    assert suspend_events[0].metadata["revoked_scopes"] == ["database.write"]


def test_suspend_principal_does_not_grant_anything():
    """The Guardian's suspend action must never be able to touch a scope
    it doesn't already have the standard PermissionManager.grant() path
    for -- suspend_principal only ever calls revoke()."""
    audit, permissions = make_deps()
    suspend_principal(permissions, audit, principal="engineering", suspended_by="security", reason="test")
    assert permissions.list_grants("engineering") == []
    assert permissions.is_granted("engineering", "permissions.manage") is False
