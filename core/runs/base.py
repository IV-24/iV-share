"""A Run: one execution of a goal, from the request that asked for it to
the answer or failure it ended in.

This is the record the audit log's individual events hang off. Before it,
iV had no identifier for an execution at all: a tool event's `resource` was
the tool's own name, so the only thing linking a tool call to the turn that
caused it was that they happened at a similar time. Reconstructing a past
run meant inferring from clock proximity, which silently over-collects the
moment two runs overlap -- and overlapping runs are the normal case for an
API with more than one caller.

A Run is deliberately thin. It holds what identifies and bounds an
execution; the steps within it stay in the audit log, now stamped with
run_id. Splitting it that way means nothing that already writes an audit
event has to change, and a run can be assembled by querying for its id.

Status vocabulary matches AgentTurnResult's, plus "running" for a run in
flight and "error" for one that produced no answer:

    running | ok | partial | error
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.runs.context import current_run_id, reset_current_run_id, set_current_run_id
from core.storage.base import StorageBackend

COLLECTION = "runs"

__all__ = [
    "COLLECTION",
    "Run",
    "RunRecorder",
    "current_run_id",
    "reset_current_run_id",
    "set_current_run_id",
]


@dataclass
class Run:
    id: str
    goal: str
    status: str
    started_at: str
    caller: str = "owner"
    conversation_id: str | None = None
    parent_run_id: str | None = None
    idempotency_key: str | None = None
    ended_at: str | None = None
    model_used: str | None = None
    error: str | None = None
    completed_tool_calls: list[str] = field(default_factory=list)
    # Providers that failed before one answered. Without this a provider
    # dead on every call is invisible: fallback hides it, and /health
    # reports a provider as live when it is merely configured.
    failed_provider_attempts: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "Run":
        return cls(
            id=record["id"],
            goal=record.get("goal", ""),
            status=record.get("status", "running"),
            started_at=record.get("started_at", record.get("created_at", "")),
            caller=record.get("caller", "owner"),
            conversation_id=record.get("conversation_id"),
            parent_run_id=record.get("parent_run_id"),
            idempotency_key=record.get("idempotency_key"),
            ended_at=record.get("ended_at"),
            model_used=record.get("model_used"),
            error=record.get("error"),
            completed_tool_calls=record.get("completed_tool_calls") or [],
            failed_provider_attempts=record.get("failed_provider_attempts") or [],
        )


class RunRecorder:
    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def start(
        self,
        *,
        goal: str,
        caller: str = "owner",
        conversation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Run:
        """Opens a run in "running". Deliberately written before the model
        is called, for the same reason the user's message is: a run that
        crashes should still be findable afterwards, and one that only
        appears once it succeeds cannot record its own failure."""
        stored = self._storage.insert(
            COLLECTION,
            {
                "goal": goal,
                "caller": caller,
                "conversation_id": conversation_id,
                "parent_run_id": current_run_id(),
                "idempotency_key": idempotency_key,
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "ended_at": None,
                "model_used": None,
                "error": None,
                "completed_tool_calls": [],
                "failed_provider_attempts": [],
            },
        )
        return Run.from_record(stored)

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        model_used: str | None = None,
        error: str | None = None,
        completed_tool_calls: list[str] | None = None,
        failed_provider_attempts: list[dict[str, Any]] | None = None,
    ) -> Run | None:
        updated = self._storage.update(
            COLLECTION,
            run_id,
            {
                "status": status,
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "model_used": model_used,
                "error": error,
                "completed_tool_calls": list(completed_tool_calls or []),
                "failed_provider_attempts": list(failed_provider_attempts or []),
            },
        )
        return Run.from_record(updated) if updated else None

    def get(self, run_id: str) -> Run | None:
        record = self._storage.get(COLLECTION, run_id)
        return Run.from_record(record) if record else None

    def list_recent(self, limit: int = 50) -> list[Run]:
        records = self._storage.query(
            COLLECTION, order_by="started_at", descending=True, limit=limit
        )
        return [Run.from_record(r) for r in records]

    def find_by_idempotency_key(self, key: str) -> Run | None:
        """Used to make a repeated submission return the original run
        rather than doing the work twice."""
        records = self._storage.query(COLLECTION, filters={"idempotency_key": key})
        if not records:
            return None
        records.sort(key=lambda r: r.get("started_at", ""))
        return Run.from_record(records[0])
