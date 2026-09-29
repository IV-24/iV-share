from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExecutionPolicy(str, Enum):
    AUTO = "auto"  # may run immediately once permission checks pass
    REQUIRES_APPROVAL = "requires_approval"  # needs a decided ApprovalRequest first


@dataclass
class ToolDefinition:
    """Everything the agent (and a human reviewing tool access) needs to
    reason about a capability before it runs: what it does, what shape it
    expects, what it needs permission for, how risky it is, and whether it
    needs approval. `handler` is the only infrastructure-specific part —
    everything else here is metadata the runtime enforces on."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    handler: Callable[..., Any]
    required_permissions: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    execution_policy: ExecutionPolicy = ExecutionPolicy.AUTO

    def describe(self) -> dict[str, Any]:
        """Discovery view — what a model or a human sees, with no
        reference to the handler implementation."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "required_permissions": list(self.required_permissions),
            "risk_level": self.risk_level.value,
            "execution_policy": self.execution_policy.value,
        }


@dataclass
class ToolResult:
    tool_name: str
    success: bool
    output: Any = None
    error: str | None = None
    approval_id: str | None = None
