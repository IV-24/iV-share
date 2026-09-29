from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.haven.manifest import build_haven_manifest
from core.permissions.manager import PermissionManager
from core.permissions.scopes import PermissionScope
from core.storage.memory import InMemoryStorage
from core.tools.base import ExecutionPolicy, RiskLevel, ToolDefinition
from core.tools.registry import ToolRegistry


def test_build_haven_manifest_aggregates_environment_tools_and_grants(tmp_path):
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    permissions = PermissionManager(storage, audit)
    approvals = ApprovalManager(storage, audit)
    tools = ToolRegistry(permissions, approvals, audit)

    tools.register(ToolDefinition(
        name="create_task", description="Creates a task",
        input_schema={"type": "object", "properties": {}}, output_schema={"type": "object"},
        handler=lambda: None, required_permissions=[PermissionScope.DATABASE_WRITE],
        risk_level=RiskLevel.LOW, execution_policy=ExecutionPolicy.AUTO,
    ))
    permissions.grant("coordinator", PermissionScope.DATABASE_WRITE, granted_by="owner")

    manifest = build_haven_manifest(tools=tools, permissions=permissions, working_directory=str(tmp_path))

    assert manifest.environment.working_directory == str(tmp_path)
    assert [t["name"] for t in manifest.tools] == ["create_task"]
    assert manifest.grants == [{
        "principal": "coordinator", "scope": PermissionScope.DATABASE_WRITE,
        "granted_by": "owner", "granted_at": manifest.grants[0]["granted_at"],
    }]


def test_build_haven_manifest_never_mutates_anything():
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    permissions = PermissionManager(storage, audit)
    approvals = ApprovalManager(storage, audit)
    tools = ToolRegistry(permissions, approvals, audit)

    build_haven_manifest(tools=tools, permissions=permissions)

    assert permissions.list_grants() == []
    assert tools.list_tools() == []
