"""Storage interface every persistence adapter must implement.

Deliberately generic (collection + record dict), mirroring the
collection-of-rows shape every current backend already uses informally
(Supabase's .table(name), a local SQLite table, an in-memory dict). Domain
modules (permissions, approvals, memory, audit, ...) layer typed
convenience methods on top of a StorageBackend; they never assume which
concrete backend is behind it.
"""

from abc import ABC, abstractmethod
from typing import Any


class StorageBackend(ABC):
    @abstractmethod
    def insert(self, collection: str, record: dict[str, Any]) -> dict[str, Any]:
        """Inserts record into collection, returning the stored record
        (with an `id` assigned if the caller didn't supply one)."""

    @abstractmethod
    def get(self, collection: str, record_id: str) -> dict[str, Any] | None:
        """Returns the record with this id, or None if not found."""

    @abstractmethod
    def update(self, collection: str, record_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        """Merges fields into the existing record. Returns the updated
        record, or None if record_id doesn't exist."""

    @abstractmethod
    def query(
        self,
        collection: str,
        *,
        filters: dict[str, Any] | None = None,
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Returns records matching an equality filter on every key/value
        in `filters` (AND'd together), optionally ordered and capped."""

    @abstractmethod
    def delete(self, collection: str, record_id: str) -> bool:
        """Deletes the record. Returns True if it existed."""

    def close(self) -> None:
        """Releases any held resources (a file handle, a connection, ...).
        Default no-op — backends with nothing to release (InMemoryStorage)
        don't need to override it, but any caller managing a
        StorageBackend's lifecycle (interfaces/api's app shutdown, a CLI
        session ending, ...) can always call this uniformly regardless of
        which concrete backend is behind it."""
