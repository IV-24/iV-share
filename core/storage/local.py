"""SQLite-backed StorageBackend: a real, persistent, zero-external-service
storage option. Proves core doesn't need Supabase (or any network
service) to be useful — point this at a file path and iV runs fully
locally. Equality filters and ordering push down into SQL via SQLite's
JSON1 functions (see query()) rather than fetching a whole collection
into Python, since callers only depend on StorageBackend's interface and
never need to know a query ran as SQL instead of a Python list
comprehension.

check_same_thread=False + a lock around every operation: a sync FastAPI
endpoint runs in a worker thread from Starlette's threadpool, a different
thread than whichever one opened this connection, and plain sqlite3
connections aren't safe to share across threads without one. The lock
serializes access rather than trying to hand out one connection per
thread — simplest correct thing for a single local file that's never
meant to see meaningful write concurrency.
"""

import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from core.storage.base import StorageBackend

# Every order_by/filter key that reaches SQL is interpolated into the
# query text (SQLite has no way to bind a JSON path's field name as a
# parameter), so it must be validated as a plain identifier first --
# defense in depth, since every current caller passes a hardcoded field
# name literal, never anything from external input.
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


class SqliteStorage(StorageBackend):
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS records (
                    collection TEXT NOT NULL,
                    id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY (collection, id)
                )
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def insert(self, collection: str, record: dict[str, Any]) -> dict[str, Any]:
        stored = dict(record)
        stored.setdefault("id", str(uuid.uuid4()))
        stored.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        with self._lock:
            self._conn.execute(
                "INSERT INTO records (collection, id, data) VALUES (?, ?, ?)",
                (collection, stored["id"], json.dumps(stored)),
            )
            self._conn.commit()
        return dict(stored)

    def get(self, collection: str, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM records WHERE collection = ? AND id = ?",
                (collection, record_id),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def update(self, collection: str, record_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM records WHERE collection = ? AND id = ?",
                (collection, record_id),
            ).fetchone()
            if row is None:
                return None
            existing = json.loads(row[0])
            existing.update(fields)
            self._conn.execute(
                "UPDATE records SET data = ? WHERE collection = ? AND id = ?",
                (json.dumps(existing), collection, record_id),
            )
            self._conn.commit()
        return dict(existing)

    def query(
        self,
        collection: str,
        *,
        filters: dict[str, Any] | None = None,
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Pushes equality filters and ordering into SQL via SQLite's
        JSON1 functions rather than fetching a whole collection into
        Python and filtering there -- the previous approach, which meant
        every call scanned every record ever written to that collection,
        including a chat turn's own conversation-history lookup running
        that scan twice (once in the route, once when persisting).

        One exception, kept in Python for correctness rather than speed:
        a filter value of None. StorageBackend's contract is `r.get(k) ==
        v`, which treats a field that is absent identically to one
        explicitly set to null -- SQL's `= NULL` never matches either
        one (SQL's three-valued logic), so pushing that case into SQL
        would silently return zero rows instead of the same set as
        before. No current caller filters on None, but the fallback
        exists so a future one gets a right answer, not a fast wrong one.
        """
        where_clauses = ["collection = ?"]
        params: list[Any] = [collection]
        fallback_filters: dict[str, Any] = {}

        for key, value in (filters or {}).items():
            if value is None or not _SAFE_IDENTIFIER.match(key):
                fallback_filters[key] = value
                continue
            where_clauses.append(f"json_extract(data, '$.{key}') = ?")
            params.append(value)

        sql = f"SELECT data FROM records WHERE {' AND '.join(where_clauses)}"  # noqa: S608 - identifiers are validated above, values are all bound params

        if order_by:
            if not _SAFE_IDENTIFIER.match(order_by):
                raise ValueError(f"invalid order_by field name: {order_by!r}")
            # NULLS LAST in both directions -- a record missing the field
            # entirely trails behind every record that has it, ascending
            # or descending, matching InMemoryStorage's (value is None,
            # value) sort key rather than SQLite's own default (NULLS
            # FIRST ascending, NULLS LAST descending), which would put
            # missing-field records in a different place per backend.
            direction = "DESC" if descending else "ASC"
            sql += f" ORDER BY json_extract(data, '$.{order_by}') {direction} NULLS LAST"

        # LIMIT only pushes into SQL when nothing still needs Python-side
        # filtering afterward -- capping before the fallback filter runs
        # would cut rows that hadn't been checked against it yet.
        if limit is not None and not fallback_filters:
            sql += " LIMIT ?"
            params.append(limit)

        with self._lock:
            rows = [json.loads(r[0]) for r in self._conn.execute(sql, params).fetchall()]

        if fallback_filters:
            rows = [r for r in rows if all(r.get(k) == v for k, v in fallback_filters.items())]
            if limit is not None:
                rows = rows[:limit]

        return rows

    def delete(self, collection: str, record_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM records WHERE collection = ? AND id = ?", (collection, record_id)
            )
            self._conn.commit()
            return cur.rowcount > 0
