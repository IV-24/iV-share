"""In-memory StorageBackend. No I/O, no dependencies — used by tests and
by any caller that wants core running with zero persistence at all."""

import uuid
from datetime import datetime, timezone
from typing import Any

from core.storage.base import StorageBackend


class InMemoryStorage(StorageBackend):
    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, Any]]] = {}

    def _table(self, collection: str) -> dict[str, dict[str, Any]]:
        return self._collections.setdefault(collection, {})

    def insert(self, collection: str, record: dict[str, Any]) -> dict[str, Any]:
        stored = dict(record)
        stored.setdefault("id", str(uuid.uuid4()))
        stored.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        self._table(collection)[stored["id"]] = stored
        return dict(stored)

    def get(self, collection: str, record_id: str) -> dict[str, Any] | None:
        record = self._table(collection).get(record_id)
        return dict(record) if record is not None else None

    def update(self, collection: str, record_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        table = self._table(collection)
        if record_id not in table:
            return None
        table[record_id].update(fields)
        return dict(table[record_id])

    def query(
        self,
        collection: str,
        *,
        filters: dict[str, Any] | None = None,
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = list(self._table(collection).values())
        if filters:
            rows = [r for r in rows if all(r.get(k) == v for k, v in filters.items())]
        if order_by:
            # (value is None, value) puts every record missing this field
            # at one end without ever comparing None to a real value --
            # `rows.sort(key=lambda r: r.get(order_by))` raises TypeError
            # the moment one record has the field and another doesn't.
            rows.sort(key=lambda r: (r.get(order_by) is None, r.get(order_by)), reverse=descending)  # type: ignore[arg-type, return-value]
        if limit is not None:
            rows = rows[:limit]
        return [dict(r) for r in rows]

    def delete(self, collection: str, record_id: str) -> bool:
        return self._table(collection).pop(record_id, None) is not None
