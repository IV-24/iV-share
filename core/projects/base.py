from dataclasses import dataclass
from typing import Any

from core.storage.base import StorageBackend

COLLECTION = "projects"


@dataclass
class Project:
    id: str
    name: str
    description: str
    status: str
    priority: int
    created_at: str

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "Project":
        return cls(
            id=record["id"],
            name=record["name"],
            description=record.get("description", ""),
            status=record.get("status", "active"),
            priority=record.get("priority", 5),
            created_at=record["created_at"],
        )


class ProjectStore:
    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def create(self, name: str, *, description: str = "", status: str = "active", priority: int = 5) -> Project:
        stored = self._storage.insert(
            COLLECTION, {"name": name, "description": description, "status": status, "priority": priority}
        )
        return Project.from_record(stored)

    def get(self, project_id: str) -> Project | None:
        record = self._storage.get(COLLECTION, project_id)
        return Project.from_record(record) if record else None

    def update_status(self, project_id: str, status: str) -> Project | None:
        stored = self._storage.update(COLLECTION, project_id, {"status": status})
        return Project.from_record(stored) if stored else None

    def list(self, *, status: str | None = None) -> list[Project]:
        filters = {"status": status} if status else None
        records = self._storage.query(COLLECTION, filters=filters, order_by="created_at", descending=True)
        return [Project.from_record(r) for r in records]
