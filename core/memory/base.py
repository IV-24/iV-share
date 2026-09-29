from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.storage.base import StorageBackend

COLLECTION = "memories"


class MemoryType(str, Enum):
    EPISODIC = "episodic"   # specific past interactions/events
    SEMANTIC = "semantic"   # general facts/knowledge distilled over time
    REFLECTIVE = "reflective"  # lessons learned, patterns, improvement ideas


@dataclass
class Memory:
    id: str
    memory_type: MemoryType
    title: str
    content: str
    importance: int
    source: str
    created_at: str

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "Memory":
        return cls(
            id=record["id"],
            memory_type=MemoryType(record["memory_type"]),
            title=record.get("title", ""),
            content=record["content"],
            importance=record.get("importance", 5),
            source=record.get("source", "iv"),
            created_at=record["created_at"],
        )


class MemoryStore:
    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def add(
        self,
        content: str,
        *,
        memory_type: MemoryType = MemoryType.REFLECTIVE,
        title: str = "",
        importance: int = 5,
        source: str = "iv",
    ) -> Memory:
        stored = self._storage.insert(
            COLLECTION,
            {
                "memory_type": memory_type.value,
                "title": title,
                "content": content,
                "importance": importance,
                "source": source,
            },
        )
        return Memory.from_record(stored)

    def get(self, memory_id: str) -> Memory | None:
        record = self._storage.get(COLLECTION, memory_id)
        return Memory.from_record(record) if record else None

    def recall(self, *, limit: int = 8, min_importance: int = 6) -> list[Memory]:
        """The bounded slice of memory that gets injected into an agent's
        prompt each turn: the most recent entries that cleared an
        importance bar, never the whole store. Both bounds matter — the
        prompt has to stay a fixed size as the database grows, and a
        low-signal note from months ago should not crowd out the current
        conversation.

        Recency-plus-importance is the honest heuristic available without
        an embedding index; genuine relevance ranking against the current
        message is the natural next step, and it belongs here rather than
        at the call site so every caller inherits it at once.
        """
        return self.query(min_importance=min_importance, limit=limit)

    def query(
        self,
        *,
        memory_type: MemoryType | None = None,
        min_importance: int | None = None,
        limit: int = 20,
    ) -> list[Memory]:
        filters = {"memory_type": memory_type.value} if memory_type else None
        records = self._storage.query(
            COLLECTION, filters=filters, order_by="created_at", descending=True, limit=None
        )
        memories = [Memory.from_record(r) for r in records]
        if min_importance is not None:
            memories = [m for m in memories if m.importance >= min_importance]
        return memories[:limit]
