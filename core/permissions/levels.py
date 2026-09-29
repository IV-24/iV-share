"""Agency levels: named bundles of permission scopes, so "how much has
this principal been allowed to do so far" has one human-meaningful label
instead of a raw scope list. A level is derived from a principal's
current grants, never stored as separate state — it can't drift out of
sync with what PermissionManager actually holds, because it isn't a
second source of truth, just a read over the first one.

This is orthogonal to core/agent/roles.py's AgentRole: a role is about
job function (which tools does an Engineering Specialist use), a level is
about how much a given principal has been trusted with over time. The
two aren't coupled — nothing requires a role to declare a level, or a
level to be role-specific.

Moving a principal to a higher level goes through core/approvals, same
pattern as core/improvements: propose (request_level_up), then apply
only once approved (apply_level_change). iV does not decide when its own
agency grows — see docs/HAVEN.md.
"""

from dataclasses import dataclass

from core.approvals.manager import ApprovalManager
from core.approvals.base import ApprovalRequest
from core.permissions.manager import PermissionManager
from core.permissions.scopes import PermissionScope


@dataclass(frozen=True)
class AgencyLevel:
    name: str
    description: str
    scopes: frozenset[str]


OBSERVER = AgencyLevel(
    name="observer",
    description="Read-only. No actions.",
    scopes=frozenset({PermissionScope.DATABASE_READ}),
)
RESEARCHER = AgencyLevel(
    name="researcher",
    description="Can read broadly and make outbound requests to gather information.",
    scopes=frozenset({PermissionScope.DATABASE_READ, PermissionScope.NETWORK_REQUEST}),
)
BUILDER = AgencyLevel(
    name="builder",
    description="Can create and run code inside the Haven.",
    scopes=frozenset({
        PermissionScope.DATABASE_READ, PermissionScope.DATABASE_WRITE, PermissionScope.CODE_EXECUTE,
    }),
)
COLLABORATOR = AgencyLevel(
    name="collaborator",
    description="Can coordinate with external repositories in addition to building.",
    scopes=frozenset({
        PermissionScope.DATABASE_READ, PermissionScope.DATABASE_WRITE, PermissionScope.CODE_EXECUTE,
        PermissionScope.GITHUB_READ,
    }),
)

AGENCY_LEVELS: dict[str, AgencyLevel] = {
    level.name: level for level in (OBSERVER, RESEARCHER, BUILDER, COLLABORATOR)
}


def current_level(permissions: PermissionManager, principal: str) -> AgencyLevel | None:
    """The highest defined AgencyLevel whose entire scope set the
    principal currently holds, or None if its grants don't fully satisfy
    any defined level. "Highest" = most scopes required, so a principal
    holding Builder's scopes (a superset of Researcher's) reports as
    Builder, not Researcher."""
    granted = {grant.scope for grant in permissions.list_grants(principal)}
    matches = [level for level in AGENCY_LEVELS.values() if level.scopes <= granted]
    if not matches:
        return None
    return max(matches, key=lambda level: len(level.scopes))


def request_level_up(
    approvals: ApprovalManager, *, principal: str, target_level: str, requested_by: str
) -> ApprovalRequest:
    if target_level not in AGENCY_LEVELS:
        raise ValueError(f"unknown agency level '{target_level}' — options: {', '.join(AGENCY_LEVELS)}")
    return approvals.request(
        action_type="agency_level_change",
        description=f"Raise '{principal}' to agency level '{target_level}' ({AGENCY_LEVELS[target_level].description})",
        requested_by=requested_by,
        risk_level="high",
    )


def apply_level_change(
    permissions: PermissionManager,
    approvals: ApprovalManager,
    approval_id: str,
    *,
    principal: str,
    target_level: str,
    granted_by: str,
) -> AgencyLevel:
    """Raises PermissionError without an approved request for approval_id
    — the same hard gate core.improvements.ImprovementManager.deploy()
    uses. Only grants scopes the target level needs that the principal
    doesn't already have; never revokes anything, so moving between
    levels is additive, not a reset."""
    if target_level not in AGENCY_LEVELS:
        raise ValueError(f"unknown agency level '{target_level}' — options: {', '.join(AGENCY_LEVELS)}")
    if not approvals.is_approved(approval_id):
        raise PermissionError(f"agency level change for '{principal}' has no approved authorization")

    level = AGENCY_LEVELS[target_level]
    for scope in level.scopes:
        if not permissions.is_granted(principal, scope):
            permissions.grant(principal, scope, granted_by=granted_by, reason=f"agency_level:{target_level}")

    approvals.mark_executed(approval_id)
    return level
