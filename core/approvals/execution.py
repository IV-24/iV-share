"""Carrying out an action a human has approved.

The gate in ToolRegistry.execute() refuses a REQUIRES_APPROVAL tool unless
it is handed an approval_id that is already approved. Nothing in the
runtime ever handed it one: the orchestrator's two call sites pass no
approval_id, and deciding an approval only moved a database row. So the
gate blocked correctly and never opened, an approved request was never
consumed, and asking again simply filed another one.

This is the missing half. Two properties it must not give up:

  * The action runs as the agent that requested it, never as the human who
    approved it. Approval authorises a specific call; it does not lend that
    call authority it did not already have. Going through
    AgentOrchestrator.run_tool keeps both existing checks in the path -- the
    role's own tool_names, then the permission scopes in the registry -- so
    a scope revoked between request and approval still refuses.

  * Exactly once. ToolRegistry.execute marks the approval executed on
    success, and ApprovalManager.mark_executed only accepts a request in
    `approved`, so a second attempt has nothing left to consume.

`orchestrator` and `catalog` are duck-typed (`.run_tool(...)`, `.get(name)`)
rather than imported, matching core/agent/roles.py's own convention and
keeping core.approvals free of a dependency on core.agent.
"""

from __future__ import annotations

from typing import Any, Protocol

from core.approvals.base import ApprovalRequest, ApprovalStatus


class ApprovalNotExecutable(Exception):
    """The request is not in a state that can be carried out, or names an
    agent that no longer exists. Distinct from the action running and
    failing, which is a ToolResult with success=False."""


class _Orchestrator(Protocol):
    def run_tool(self, role: Any, tool_name: str, arguments: dict, *, approval_id: str | None = None): ...


class _Catalog(Protocol):
    def get(self, name: str) -> Any | None: ...


def execute_approved_request(
    request: ApprovalRequest | None,
    *,
    orchestrator: _Orchestrator,
    catalog: _Catalog,
    approvals: Any,
) -> Any:
    """Runs the call an approved request authorises and returns its
    ToolResult. Raises ApprovalNotExecutable if the request cannot be acted
    on at all."""
    if request is None:
        raise ApprovalNotExecutable("no such approval request")
    if request.status != ApprovalStatus.APPROVED:
        raise ApprovalNotExecutable(
            f"approval '{request.id}' is '{request.status.value}', not approved"
        )

    role = catalog.get(request.requested_by)
    if role is None:
        # A composed agent can be deleted between request and approval.
        # Running its pending action as somebody else would be a quiet
        # privilege transfer, so this refuses instead of substituting.
        raise ApprovalNotExecutable(
            f"the agent that requested this ('{request.requested_by}') no longer exists"
        )

    return orchestrator.run_tool(
        role, request.action_type, dict(request.arguments), approval_id=request.id
    )
