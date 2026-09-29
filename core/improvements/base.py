from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ImprovementStatus(str, Enum):
    PROPOSED = "proposed"
    TESTING = "testing"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEPLOYED = "deployed"
    FAILED = "failed"


@dataclass
class ImprovementProposal:
    id: str
    agent_name: str
    problem_identified: str
    proposed_change: str
    status: ImprovementStatus
    created_at: str
    affected_files: list[str] = field(default_factory=list)
    approval_id: str | None = None

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "ImprovementProposal":
        return cls(
            id=record["id"],
            agent_name=record["agent_name"],
            problem_identified=record["problem_identified"],
            proposed_change=record["proposed_change"],
            status=ImprovementStatus(record["status"]),
            created_at=record["created_at"],
            affected_files=record.get("affected_files", []),
            approval_id=record.get("approval_id"),
        )
