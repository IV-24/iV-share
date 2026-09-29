from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ApprovalStatus(str, Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    DENIED = "denied"
    REVOKED = "revoked"
    EXPIRED = "expired"
    EXECUTED = "executed"
    FAILED = "failed"


@dataclass
class ApprovalRequest:
    id: str
    action_type: str
    description: str
    requested_by: str
    risk_level: str
    status: ApprovalStatus
    created_at: str
    decided_by: str | None = None
    decided_at: str | None = None
    expires_at: str | None = None
    executed_at: str | None = None
    error: str | None = None
    # The call this request authorises, kept structurally. It used to exist
    # only inside `description`, folded into prose by repr(), so nothing
    # could act on an approval without parsing a Python literal back out of
    # a sentence -- which is part of why nothing ever did.
    arguments: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "ApprovalRequest":
        return cls(
            id=record["id"],
            action_type=record["action_type"],
            description=record["description"],
            requested_by=record["requested_by"],
            risk_level=record.get("risk_level", "high"),
            status=ApprovalStatus(record["status"]),
            created_at=record["created_at"],
            decided_by=record.get("decided_by"),
            decided_at=record.get("decided_at"),
            expires_at=record.get("expires_at"),
            executed_at=record.get("executed_at"),
            error=record.get("error"),
            arguments=record.get("arguments") or {},
        )
