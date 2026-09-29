"""ToolRegistry is the enforcement chokepoint: register() is the only way
a tool becomes callable, and execute() is the only way it runs. A caller
can't get from "the model wants to run X" to "X ran" without passing
through the permission check and, for REQUIRES_APPROVAL tools, an
approved ApprovalRequest. This is what makes the approval system real
instead of a database row nobody reads.
"""

from typing import Any

from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.permissions.manager import PermissionManager
from core.tools.base import ExecutionPolicy, ToolDefinition, ToolResult


class ToolRegistry:
    def __init__(
        self,
        permissions: PermissionManager,
        approvals: ApprovalManager,
        audit: AuditLog | None = None,
    ) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._permissions = permissions
        self._approvals = approvals
        self._audit = audit

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(self) -> list[dict[str, Any]]:
        """Discovery: what exists, what it does, what it needs, how risky
        it is, whether it needs approval — everything an agent (or a
        human auditing tool access) should be able to see up front."""
        return [tool.describe() for tool in self._tools.values()]

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        principal: str,
        approval_id: str | None = None,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            # Audited like any other refusal. Returning early from above
            # the logging left a model inventing tool names invisible --
            # and a run of those is the clearest signal that a prompt or a
            # role's tool_names has drifted from what is registered.
            self._log(principal, name, outcome="error", metadata={"reason": "unknown_tool"})
            return ToolResult(tool_name=name, success=False, error=f"unknown tool '{name}'")

        missing = [
            scope for scope in tool.required_permissions if not self._permissions.is_granted(principal, scope)
        ]
        if missing:
            self._log(principal, name, outcome="denied", metadata={"missing_permissions": missing})
            return ToolResult(
                tool_name=name, success=False,
                error=f"'{principal}' is missing required permission(s): {', '.join(missing)}",
            )

        if tool.execution_policy == ExecutionPolicy.REQUIRES_APPROVAL:
            if approval_id is None or not self._approvals.is_approved(approval_id):
                created = self._approvals.request(
                    action_type=name,
                    description=f"{principal} requested to run '{name}' with arguments {arguments!r}",
                    requested_by=principal,
                    risk_level=tool.risk_level.value,
                    arguments=arguments,
                )
                self._log(principal, name, outcome="denied", metadata={"reason": "approval_required"})
                return ToolResult(
                    tool_name=name, success=False, error="approval_required", approval_id=created.id
                )

        try:
            output = tool.handler(**arguments)
        except Exception as exc:  # noqa: BLE001 - a failing tool must not crash the caller
            if approval_id:
                self._approvals.mark_failed(approval_id, str(exc))
            self._log(principal, name, outcome="error", metadata={"error": str(exc)})
            return ToolResult(tool_name=name, success=False, error=str(exc), approval_id=approval_id)

        if approval_id:
            self._approvals.mark_executed(approval_id)
        self._log(principal, name, outcome="success", metadata={"arguments": arguments})
        return ToolResult(tool_name=name, success=True, output=output, approval_id=approval_id)

    def record_refusal(self, principal: str, tool_name: str, *, reason: str) -> None:
        """For a caller that refuses a tool call before execute() is
        reached -- AgentOrchestrator.run_tool rejecting a tool outside the
        role's own tool_names, which is the shape a hallucinated tool name
        usually takes. The audit sink for tool events lives here, so the
        refusal is recorded here rather than giving another component its
        own second path to the log."""
        self._log(principal, tool_name, outcome="denied", metadata={"reason": reason})

    def _log(self, principal: str, tool_name: str, *, outcome: str, metadata: dict[str, Any]) -> None:
        if self._audit:
            self._audit.record(
                actor=principal, action=f"tool.execute:{tool_name}", resource=tool_name,
                outcome=outcome, metadata=metadata,
            )
