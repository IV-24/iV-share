"""Persistence abstraction. core depends on StorageBackend; concrete
backends (local SQLite, Supabase, ...) implement it."""

from core.storage.base import StorageBackend
from core.storage.memory import InMemoryStorage
from core.storage.local import SqliteStorage

__all__ = ["StorageBackend", "InMemoryStorage", "SqliteStorage"]
