"""Grants are explicit and additive: a principal (a role, an extension, a
user) has a scope only if something granted it, and only until something
revokes it. Nothing in core checks `hasattr` or process-level OS
permissions to decide what the model may do — it checks this manager.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.audit.log import AuditLog
from core.storage.base import StorageBackend

COLLECTION = "permission_grants"


@dataclass
class PermissionGrant:
    id: str
    principal: str
    scope: str
    granted_by: str
    granted_at: str
    revoked_at: str | None = None
    reason: str | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "PermissionGrant":
        return cls(
            id=record["id"],
            principal=record["principal"],
            scope=record["scope"],
            granted_by=record["granted_by"],
            granted_at=record["granted_at"],
            revoked_at=record.get("revoked_at"),
            reason=record.get("reason"),
        )


class PermissionManager:
    def __init__(self, storage: StorageBackend, audit: AuditLog | None = None) -> None:
        self._storage = storage
        self._audit = audit

    def grant(self, principal: str, scope: str, *, granted_by: str, reason: str | None = None) -> PermissionGrant:
        stored = self._storage.insert(
            COLLECTION,
            {
                "principal": principal,
                "scope": scope,
                "granted_by": granted_by,
                "granted_at": datetime.now(timezone.utc).isoformat(),
                "revoked_at": None,
                "reason": reason,
            },
        )
        if self._audit:
            self._audit.record(
                actor=granted_by, action="permission.grant", resource=f"{principal}:{scope}"
            )
        return PermissionGrant.from_record(stored)

    def revoke(self, principal: str, scope: str, *, revoked_by: str) -> bool:
        grant = self._active_grant(principal, scope)
        if grant is None:
            return False
        self._storage.update(
            COLLECTION, grant.id, {"revoked_at": datetime.now(timezone.utc).isoformat()}
        )
        if self._audit:
            self._audit.record(
                actor=revoked_by, action="permission.revoke", resource=f"{principal}:{scope}"
            )
        return True

    def is_granted(self, principal: str, scope: str) -> bool:
        return self._active_grant(principal, scope) is not None

    def require(self, principal: str, scope: str) -> None:
        """Raises PermissionError if principal lacks scope. Callers that
        need a hard stop (as opposed to a boolean check) use this."""
        if not self.is_granted(principal, scope):
            raise PermissionError(f"'{principal}' lacks required permission scope '{scope}'")

    def list_grants(self, principal: str | None = None) -> list[PermissionGrant]:
        filters = {"principal": principal} if principal else None
        records = self._storage.query(COLLECTION, filters=filters)
        return [PermissionGrant.from_record(r) for r in records if r.get("revoked_at") is None]

    def _active_grant(self, principal: str, scope: str) -> PermissionGrant | None:
        records = self._storage.query(COLLECTION, filters={"principal": principal, "scope": scope})
        active = [r for r in records if r.get("revoked_at") is None]
        if not active:
            return None
        # Most recent grant wins if somehow more than one is active.
        active.sort(key=lambda r: r["granted_at"], reverse=True)
        return PermissionGrant.from_record(active[0])
