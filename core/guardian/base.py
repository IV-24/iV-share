"""Guardian functions: anomaly detection over the audit trail, and a way
to immediately revoke a principal's access if something looks wrong.

suspend_principal() is deliberately NOT gated by an ApprovalRequest —
requiring a human to approve an emergency stop before it takes effect
defeats the purpose of having one. What keeps this from being a
back-door escalation: it only *revokes*, it can't grant; every scope it
touches goes through the exact same PermissionManager.revoke() any other
caller would use; and the action is unconditionally, specifically
audited as a suspension so it's never quietly indistinguishable from
routine permission cleanup. Re-granting anything it revoked goes through
the normal core.permissions.grant() path — this module adds no shortcut
for that either.
"""

from dataclasses import dataclass

from core.audit.log import AuditEvent, AuditLog
from core.permissions.manager import PermissionManager


@dataclass
class AnomalyFlag:
    principal: str
    reason: str
    evidence: list[AuditEvent]


def find_denied_action_spikes(
    audit: AuditLog, principal: str, *, threshold: int = 5, lookback: int = 200
) -> AnomalyFlag | None:
    """Flags a principal with an unusually high number of recent
    'denied' outcomes in its own audit history — a pattern consistent
    with a role repeatedly attempting something outside its granted
    scope, whether that's a bug, a bad prompt, or something worth a
    closer look. lookback bounds how much history each check scans."""
    events = audit.for_actor(principal, limit=lookback)
    denied = [e for e in events if e.outcome == "denied"]
    if len(denied) >= threshold:
        return AnomalyFlag(
            principal=principal,
            reason=f"{len(denied)} denied action(s) in the last {len(events)} audit events",
            evidence=denied[:threshold],
        )
    return None


def suspend_principal(
    permissions: PermissionManager, audit: AuditLog, *, principal: str, suspended_by: str, reason: str
) -> list[str]:
    """Revokes every currently active scope held by principal. Returns
    the list of scopes actually revoked. A hard stop, not a graceful
    downgrade — restoring access afterward is a deliberate, separate
    core.permissions.grant() call, not something this function offers a
    shortcut back to."""
    revoked = []
    for grant in permissions.list_grants(principal):
        if permissions.revoke(principal, grant.scope, revoked_by=suspended_by):
            revoked.append(grant.scope)

    audit.record(
        actor=suspended_by,
        action="guardian.suspend",
        resource=principal,
        metadata={"reason": reason, "revoked_scopes": revoked},
    )
    return revoked
