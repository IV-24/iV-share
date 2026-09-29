from dataclasses import dataclass
from typing import Any

from core.storage.base import StorageBackend

COLLECTION = "tasks"


@dataclass
class Task:
    id: str
    project_id: str
    title: str
    description: str
    status: str
    priority: int
    assigned_role: str | None
    created_at: str

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "Task":
        return cls(
            id=record["id"],
            project_id=record["project_id"],
            title=record["title"],
            description=record.get("description", ""),
            status=record.get("status", "pending"),
            priority=record.get("priority", 5),
            assigned_role=record.get("assigned_role"),
            created_at=record["created_at"],
        )


class TaskStore:
    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def create(
        self,
        project_id: str,
        title: str,
        *,
        description: str = "",
        priority: int = 5,
        status: str = "pending",
        assigned_role: str | None = None,
    ) -> Task:
        stored = self._storage.insert(
            COLLECTION,
            {
                "project_id": project_id, "title": title, "description": description,
                "priority": priority, "status": status, "assigned_role": assigned_role,
            },
        )
        return Task.from_record(stored)

    def get(self, task_id: str) -> Task | None:
        record = self._storage.get(COLLECTION, task_id)
        return Task.from_record(record) if record else None

    def update_status(self, task_id: str, status: str) -> Task | None:
        stored = self._storage.update(COLLECTION, task_id, {"status": status})
        return Task.from_record(stored) if stored else None

    def list_for_project(self, project_id: str) -> list[Task]:
        records = self._storage.query(
            COLLECTION, filters={"project_id": project_id}, order_by="created_at"
        )
        return [Task.from_record(r) for r in records]
