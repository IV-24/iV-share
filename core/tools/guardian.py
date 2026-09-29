"""Guardian tools: exposes core/guardian's anomaly-detection and
suspension functions through the same ToolRegistry every other
capability goes through, gated on permissions.manage — the one scope
only the Security specialist role holds by default (see
core/agent/roles.py). suspend_principal is AUTO, not REQUIRES_APPROVAL:
see core/guardian/base.py's docstring for why an emergency stop isn't
approval-gated. It is unconditionally audited either way.
"""

from core.audit.log import AuditLog
from core.guardian.base import find_denied_action_spikes, suspend_principal
from core.permissions.manager import PermissionManager
from core.permissions.scopes import PermissionScope
from core.tools.base import ExecutionPolicy, RiskLevel, ToolDefinition
from core.tools.registry import ToolRegistry


def register_guardian_tools(registry: ToolRegistry, *, audit: AuditLog, permissions: PermissionManager) -> None:
    def flag_denied_action_spikes(principal: str, threshold: int = 5):
        flag = find_denied_action_spikes(audit, principal, threshold=threshold)
        if flag is None:
            return {"flagged": False, "principal": principal}
        return {"flagged": True, "principal": flag.principal, "reason": flag.reason}

    def suspend(principal: str, reason: str):
        revoked = suspend_principal(permissions, audit, principal=principal, suspended_by="guardian", reason=reason)
        return {"principal": principal, "revoked_scopes": revoked}

    registry.register(ToolDefinition(
        name="flag_denied_action_spikes",
        description="Checks whether a principal has an unusually high number of recent denied actions "
                     "in its audit history — a pattern consistent with drifting outside its declared scope.",
        input_schema={"type": "object", "properties": {
            "principal": {"type": "string"}, "threshold": {"type": "integer"},
        }, "required": ["principal"]},
        output_schema={"type": "object"}, handler=flag_denied_action_spikes,
        required_permissions=[PermissionScope.PERMISSIONS_MANAGE], risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="suspend_principal",
        description="Immediately revokes every permission scope a principal currently holds. An "
                     "emergency stop, not approval-gated — restoring access afterward is a separate, "
                     "deliberate grant.",
        input_schema={"type": "object", "properties": {
            "principal": {"type": "string"}, "reason": {"type": "string"},
        }, "required": ["principal", "reason"]},
        output_schema={"type": "object"}, handler=suspend,
        required_permissions=[PermissionScope.PERMISSIONS_MANAGE], risk_level=RiskLevel.HIGH,
        execution_policy=ExecutionPolicy.AUTO,
    ))
