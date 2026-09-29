from dataclasses import dataclass
from typing import Any

from core.storage.base import StorageBackend

CONVERSATIONS_COLLECTION = "conversations"
MESSAGES_COLLECTION = "messages"


@dataclass
class Conversation:
    id: str
    title: str
    created_at: str

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "Conversation":
        return cls(id=record["id"], title=record.get("title", "New conversation"), created_at=record["created_at"])


@dataclass
class Message:
    id: str
    conversation_id: str
    role: str
    content: str
    model_used: str | None
    created_at: str

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "Message":
        return cls(
            id=record["id"],
            conversation_id=record["conversation_id"],
            role=record["role"],
            content=record["content"],
            model_used=record.get("model_used"),
            created_at=record["created_at"],
        )


def derive_title(message: str, *, max_chars: int = 60) -> str:
    """The first user message, collapsed to one line and capped -- used
    to name a conversation the moment it's created instead of leaving
    every conversation titled "New conversation" forever (nothing ever
    updated the default). Pure and separately testable because naming a
    conversation from its opening line is a rule worth pinning on its
    own, not just as a side effect of the chat route."""
    collapsed = " ".join(message.split())
    if not collapsed:
        return "New conversation"
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 1].rstrip() + "…"


class ConversationStore:
    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def create(self, title: str = "New conversation") -> Conversation:
        stored = self._storage.insert(CONVERSATIONS_COLLECTION, {"title": title})
        return Conversation.from_record(stored)

    def get(self, conversation_id: str) -> Conversation | None:
        record = self._storage.get(CONVERSATIONS_COLLECTION, conversation_id)
        return Conversation.from_record(record) if record else None

    def list_recent(self, limit: int = 20) -> list[Conversation]:
        records = self._storage.query(
            CONVERSATIONS_COLLECTION, order_by="created_at", descending=True, limit=limit
        )
        return [Conversation.from_record(r) for r in records]

    def add_message(
        self, conversation_id: str, role: str, content: str, *, model_used: str | None = None
    ) -> Message:
        stored = self._storage.insert(
            MESSAGES_COLLECTION,
            {"conversation_id": conversation_id, "role": role, "content": content, "model_used": model_used},
        )
        return Message.from_record(stored)

    def history(self, conversation_id: str) -> list[Message]:
        records = self._storage.query(
            MESSAGES_COLLECTION, filters={"conversation_id": conversation_id}, order_by="created_at"
        )
        return [Message.from_record(r) for r in records]

    def messages_since(self, since_iso: str) -> list[Message]:
        """All messages across every conversation with created_at >=
        since_iso, e.g. for a nightly reflection cycle reviewing the
        whole day rather than one conversation. StorageBackend's generic
        query() only does equality filters, so the date comparison
        happens here in Python — fine at local/single-user scale."""
        records = self._storage.query(MESSAGES_COLLECTION, order_by="created_at")
        return [Message.from_record(r) for r in records if r.get("created_at", "") >= since_iso]
