"""ApprovalManager owns the requested -> approved/denied -> executed/failed
lifecycle (plus revoked/expired as terminal states reachable from
approved-but-not-yet-executed). Every transition is audited. Nothing here
executes anything — that stays the caller's job, gated by is_approved().
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from core.approvals.base import ApprovalRequest, ApprovalStatus
from core.audit.log import AuditLog
from core.storage.base import StorageBackend

COLLECTION = "approvals"


class ApprovalManager:
    def __init__(self, storage: StorageBackend, audit: AuditLog | None = None) -> None:
        self._storage = storage
        self._audit = audit

    def request(
        self,
        *,
        action_type: str,
        description: str,
        requested_by: str,
        risk_level: str = "high",
        expires_in_seconds: int | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        expires_at = None
        if expires_in_seconds is not None:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
            ).isoformat()

        stored = self._storage.insert(
            COLLECTION,
            {
                "action_type": action_type,
                "description": description,
                "requested_by": requested_by,
                "risk_level": risk_level,
                "status": ApprovalStatus.REQUESTED.value,
                "expires_at": expires_at,
                "arguments": arguments or {},
            },
        )
        if self._audit:
            self._audit.record(actor=requested_by, action="approval.request", resource=stored["id"])
        return ApprovalRequest.from_record(stored)

    def get(self, approval_id: str) -> ApprovalRequest | None:
        record = self._storage.get(COLLECTION, approval_id)
        if record is None:
            return None
        return self._resolve_expiry(ApprovalRequest.from_record(record))

    def decide(self, approval_id: str, *, approved: bool, decided_by: str) -> ApprovalRequest:
        request = self._require(approval_id)
        if request.status != ApprovalStatus.REQUESTED:
            raise ValueError(f"approval '{approval_id}' is '{request.status.value}', not decidable")

        new_status = ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
        stored = self._storage.update(
            COLLECTION,
            approval_id,
            {
                "status": new_status.value,
                "decided_by": decided_by,
                "decided_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        if self._audit:
            self._audit.record(
                actor=decided_by, action=f"approval.{new_status.value}", resource=approval_id
            )
        return ApprovalRequest.from_record(stored)  # type: ignore[arg-type]

    def revoke(self, approval_id: str, *, revoked_by: str) -> ApprovalRequest:
        request = self._require(approval_id)
        if request.status != ApprovalStatus.APPROVED:
            raise ValueError(f"only an approved request can be revoked (got '{request.status.value}')")
        stored = self._storage.update(COLLECTION, approval_id, {"status": ApprovalStatus.REVOKED.value})
        if self._audit:
            self._audit.record(actor=revoked_by, action="approval.revoke", resource=approval_id)
        return ApprovalRequest.from_record(stored)  # type: ignore[arg-type]

    def is_approved(self, approval_id: str) -> bool:
        request = self.get(approval_id)
        return request is not None and request.status == ApprovalStatus.APPROVED

    def mark_executed(self, approval_id: str) -> ApprovalRequest:
        request = self._require(approval_id)
        if request.status != ApprovalStatus.APPROVED:
            raise ValueError("cannot execute an approval that isn't approved")
        stored = self._storage.update(
            COLLECTION,
            approval_id,
            {"status": ApprovalStatus.EXECUTED.value, "executed_at": datetime.now(timezone.utc).isoformat()},
        )
        if self._audit:
            self._audit.record(actor="system", action="approval.executed", resource=approval_id)
        return ApprovalRequest.from_record(stored)  # type: ignore[arg-type]

    def mark_failed(self, approval_id: str, error: str) -> ApprovalRequest:
        stored = self._storage.update(
            COLLECTION, approval_id, {"status": ApprovalStatus.FAILED.value, "error": error}
        )
        if self._audit:
            self._audit.record(
                actor="system", action="approval.failed", resource=approval_id, outcome="error",
                metadata={"error": error},
            )
        return ApprovalRequest.from_record(stored)  # type: ignore[arg-type]

    def list_pending(self) -> list[ApprovalRequest]:
        records = self._storage.query(COLLECTION, filters={"status": ApprovalStatus.REQUESTED.value})
        resolved = [self._resolve_expiry(ApprovalRequest.from_record(r)) for r in records]
        return [r for r in resolved if r.status == ApprovalStatus.REQUESTED]

    def list_approved(self, *, requested_by: str | None = None) -> list[ApprovalRequest]:
        """Approved-but-not-yet-executed requests — the backlog of things
        a human has signed off on that nothing has acted on yet.
        Optionally scoped to who originally requested them (e.g. only
        the Sleep Cycle's own proposals)."""
        filters: dict[str, str] = {"status": ApprovalStatus.APPROVED.value}
        if requested_by is not None:
            filters["requested_by"] = requested_by
        records = self._storage.query(COLLECTION, filters=filters)
        return [ApprovalRequest.from_record(r) for r in records]

    def _require(self, approval_id: str) -> ApprovalRequest:
        request = self.get(approval_id)
        if request is None:
            raise KeyError(f"no approval request with id '{approval_id}'")
        return request

    def _resolve_expiry(self, request: ApprovalRequest) -> ApprovalRequest:
        """Lazily flips REQUESTED -> EXPIRED once past expires_at, so
        nothing needs a background sweeper for expiry to be honest."""
        if (
            request.status == ApprovalStatus.REQUESTED
            and request.expires_at is not None
            and datetime.fromisoformat(request.expires_at) < datetime.now(timezone.utc)
        ):
            stored = self._storage.update(COLLECTION, request.id, {"status": ApprovalStatus.EXPIRED.value})
            return ApprovalRequest.from_record(stored)  # type: ignore[arg-type]
        return request
