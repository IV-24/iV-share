from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.improvements.manager import ImprovementManager
from core.memory.base import MemoryStore
from core.permissions.manager import PermissionManager
from core.permissions.scopes import PermissionScope
from core.projects.base import ProjectStore
from core.storage.memory import InMemoryStorage
from core.tasks.base import TaskStore
from core.tools.registry import ToolRegistry
from core.tools.standard import register_standard_tools


def make_registry():
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    permissions = PermissionManager(storage, audit)
    approvals = ApprovalManager(storage, audit)
    registry = ToolRegistry(permissions, approvals, audit)

    register_standard_tools(
        registry,
        projects=ProjectStore(storage),
        tasks=TaskStore(storage),
        memory=MemoryStore(storage),
        approvals=approvals,
        improvements=ImprovementManager(storage, approvals, audit),
    )
    return registry, permissions, approvals


def test_all_standard_tools_registered_with_low_risk_and_auto_policy():
    registry, _, _ = make_registry()
    names = {t["name"] for t in registry.list_tools()}
    assert names == {
        "create_project", "list_projects", "create_task", "update_task_status",
        "list_tasks_for_project", "create_memory", "list_recent_memories",
        "create_approval", "create_improvement",
    }
    for tool in registry.list_tools():
        assert tool["risk_level"] == "low"
        assert tool["execution_policy"] == "auto"


def test_create_project_and_task_flow_requires_database_write():
    registry, permissions, _ = make_registry()

    denied = registry.execute("create_project", {"name": "iV Phase 2"}, principal="coordinator")
    assert denied.success is False

    permissions.grant("coordinator", PermissionScope.DATABASE_WRITE, granted_by="owner")
    created = registry.execute("create_project", {"name": "iV Phase 2"}, principal="coordinator")
    assert created.success is True
    project_id = created.output["id"]

    task = registry.execute(
        "create_task", {"project_id": project_id, "title": "Write tests"}, principal="coordinator"
    )
    assert task.success is True
    assert task.output["project_id"] == project_id

    listed = registry.execute("list_tasks_for_project", {"project_id": project_id}, principal="coordinator")
    assert listed.success is False  # only DATABASE_WRITE was granted, not DATABASE_READ

    permissions.grant("coordinator", PermissionScope.DATABASE_READ, granted_by="owner")
    listed = registry.execute("list_tasks_for_project", {"project_id": project_id}, principal="coordinator")
    assert listed.success is True
    assert len(listed.output) == 1


def test_create_approval_records_request_without_executing_anything():
    registry, _, approvals = make_registry()

    result = registry.execute(
        "create_approval",
        {"action_type": "wire_transfer", "description": "pay invoice #42", "requested_by": "finance"},
        principal="finance",
    )

    assert result.success is True
    assert approvals.get(result.output["id"]).status.value == "requested"


def test_create_approval_reachable_by_a_principal_with_no_write_access():
    """Regression test: create_approval is the escape hatch for a role
    that can't do something directly (Finance is deliberately never
    granted database.write). It used to itself require database.write,
    which made it unreachable by exactly the roles it exists for --
    caught via live testing with the real Finance role, not by a unit
    test, because every earlier test happened to grant database.write
    first. This one specifically grants only database.read (Finance's
    actual default) to make sure that regression can't come back."""
    registry, permissions, approvals = make_registry()
    permissions.grant("finance", PermissionScope.DATABASE_READ, granted_by="owner")

    result = registry.execute(
        "create_approval",
        {"action_type": "system_access", "description": "request read access to finance systems"},
        principal="finance",
    )

    assert result.success is True
    assert approvals.get(result.output["id"]).status.value == "requested"


def test_create_improvement_opens_a_linked_approval():
    registry, _, approvals = make_registry()

    result = registry.execute(
        "create_improvement",
        {
            "agent_name": "Engineering Specialist",
            "problem_identified": "No retry on 503s",
            "proposed_change": "Add backoff",
        },
        principal="engineering",
    )

    assert result.success is True
    assert approvals.get(result.output["approval_id"]).status.value == "requested"
