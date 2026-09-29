import pytest

from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.permissions.levels import (
    AGENCY_LEVELS, BUILDER, OBSERVER, RESEARCHER,
    apply_level_change, current_level, request_level_up,
)
from core.permissions.manager import PermissionManager
from core.storage.memory import InMemoryStorage


def make_deps():
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    permissions = PermissionManager(storage, audit)
    approvals = ApprovalManager(storage, audit)
    return permissions, approvals


def test_current_level_none_with_no_grants():
    permissions, _ = make_deps()
    assert current_level(permissions, "coordinator") is None


def test_current_level_detects_observer():
    permissions, _ = make_deps()
    for scope in OBSERVER.scopes:
        permissions.grant("coordinator", scope, granted_by="owner")
    assert current_level(permissions, "coordinator") == OBSERVER


def test_current_level_reports_highest_matching_level():
    """A principal holding Builder's scopes (a superset of Researcher's
    minus network.request) shouldn't be misreported as Researcher --
    'highest' means the level requiring the most scopes that's still
    fully satisfied."""
    permissions, _ = make_deps()
    for scope in BUILDER.scopes:
        permissions.grant("coordinator", scope, granted_by="owner")
    assert current_level(permissions, "coordinator") == BUILDER


def test_current_level_none_with_partial_scopes():
    permissions, _ = make_deps()
    # Researcher needs database.read AND network.request -- only one
    # granted, so no defined level's full scope set is satisfied.
    permissions.grant("coordinator", "network.request", granted_by="owner")
    assert current_level(permissions, "coordinator") is None


def test_request_level_up_rejects_unknown_level():
    _, approvals = make_deps()
    with pytest.raises(ValueError):
        request_level_up(approvals, principal="coordinator", target_level="godmode", requested_by="owner")


def test_apply_level_change_refused_without_approval():
    permissions, approvals = make_deps()
    request = request_level_up(approvals, principal="coordinator", target_level="researcher", requested_by="owner")

    with pytest.raises(PermissionError):
        apply_level_change(
            permissions, approvals, request.id,
            principal="coordinator", target_level="researcher", granted_by="owner",
        )
    assert current_level(permissions, "coordinator") is None


def test_apply_level_change_grants_scopes_once_approved():
    permissions, approvals = make_deps()
    request = request_level_up(approvals, principal="coordinator", target_level="researcher", requested_by="owner")
    approvals.decide(request.id, approved=True, decided_by="owner")

    level = apply_level_change(
        permissions, approvals, request.id,
        principal="coordinator", target_level="researcher", granted_by="owner",
    )

    assert level == RESEARCHER
    assert current_level(permissions, "coordinator") == RESEARCHER
    assert approvals.get(request.id).status.value == "executed"


def test_apply_level_change_is_additive_not_a_reset():
    permissions, approvals = make_deps()
    permissions.grant("coordinator", "github.read", granted_by="owner")  # unrelated pre-existing grant

    request = request_level_up(approvals, principal="coordinator", target_level="observer", requested_by="owner")
    approvals.decide(request.id, approved=True, decided_by="owner")
    apply_level_change(
        permissions, approvals, request.id,
        principal="coordinator", target_level="observer", granted_by="owner",
    )

    granted_scopes = {g.scope for g in permissions.list_grants("coordinator")}
    assert "github.read" in granted_scopes  # not revoked by the level change
    assert "database.read" in granted_scopes


def test_apply_level_change_rejects_unknown_level():
    permissions, approvals = make_deps()
    with pytest.raises(ValueError):
        apply_level_change(
            permissions, approvals, "fake-id",
            principal="coordinator", target_level="godmode", granted_by="owner",
        )
