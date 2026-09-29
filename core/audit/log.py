"""Audit log: a security system that only exists in prompts isn't a
security system, and neither is one that only exists in log lines nobody
can query. Every permission grant/revoke, approval decision, and tool
execution records an AuditEvent here."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.runs.context import current_run_id
from core.storage.base import StorageBackend

COLLECTION = "audit_log"


@dataclass
class AuditEvent:
    id: str
    timestamp: str
    actor: str
    action: str
    resource: str
    outcome: str  # "success" | "partial" | "denied" | "error"
    metadata: dict[str, Any] = field(default_factory=dict)
    # The execution this event belongs to. None for events written outside
    # a run -- a CLI utility, a direct store call, the permission grants
    # made at startup. Everything raised during a turn carries one, which
    # is what makes a run reconstructable without inferring from
    # timestamps.
    run_id: str | None = None

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "AuditEvent":
        return cls(
            id=record["id"],
            timestamp=record["timestamp"],
            actor=record["actor"],
            action=record["action"],
            resource=record["resource"],
            outcome=record["outcome"],
            metadata=record.get("metadata", {}),
            run_id=record.get("run_id"),
        )


class AuditLog:
    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def record(
        self,
        *,
        actor: str,
        action: str,
        resource: str,
        outcome: str = "success",
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        stored = self._storage.insert(
            COLLECTION,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "actor": actor,
                "action": action,
                "resource": resource,
                "outcome": outcome,
                "metadata": metadata or {},
                # Read from the contextvar rather than taken as a
                # parameter: this is called from core/tools,
                # core/approvals, core/permissions and core/agent, and a
                # run id threaded through all of them is a run id some
                # future call site forgets to pass.
                "run_id": current_run_id(),
            },
        )
        return AuditEvent.from_record(stored)

    def for_actor(self, actor: str, limit: int = 100) -> list[AuditEvent]:
        records = self._storage.query(
            COLLECTION, filters={"actor": actor}, order_by="timestamp", descending=True, limit=limit
        )
        return [AuditEvent.from_record(r) for r in records]

    def for_resource(self, resource: str, limit: int = 100) -> list[AuditEvent]:
        records = self._storage.query(
            COLLECTION, filters={"resource": resource}, order_by="timestamp", descending=True, limit=limit
        )
        return [AuditEvent.from_record(r) for r in records]

    def for_run(self, run_id: str) -> list[AuditEvent]:
        """Every event raised during one execution, oldest first. This is
        the replay primitive: before run_id existed, the nearest thing was
        selecting on a timestamp window, which returns another run's events
        too whenever two overlap."""
        records = self._storage.query(COLLECTION, filters={"run_id": run_id}, order_by="timestamp")
        return [AuditEvent.from_record(r) for r in records]

    def since(self, since_iso: str) -> list[AuditEvent]:
        """Every event with timestamp >= since_iso, e.g. for a nightly
        reflection cycle reviewing a whole day's activity."""
        records = self._storage.query(COLLECTION, order_by="timestamp")
        return [AuditEvent.from_record(r) for r in records if r.get("timestamp", "") >= since_iso]
