"""Standard tool set: replaces backend/app/tools/actions.py and
tools/memory.py's direct Supabase calls with ToolDefinitions over core's
own stores. register_standard_tools() is what interfaces/api wires up at
startup — it's the one place these tools' permissions/risk/execution
policy are decided, so reviewing this file is enough to answer "what can
iV actually do and under what conditions," rather than needing to read
every store's implementation.

None of these are destructive or externally consequential (no deletes, no
financial/production actions exist yet), so all of them are LOW risk and
AUTO execution today — that's a fact about the current tool set, not a
policy. Any future tool with a real-world consequential effect
(financial, destructive, production-affecting, external communication)
MUST set execution_policy=REQUIRES_APPROVAL and an appropriate risk_level;
ToolRegistry.execute() is what actually enforces that, not a comment.

Reads are typed and narrow (list_projects, list_tasks_for_project, ...)
rather than one generic "query any table" tool — same "narrow tools over
blanket access" philosophy the old tools/memory.py's ALLOWED_QUERY_TABLES
allowlist was reaching for, taken further: there's no table name for a
model to supply at all, just a fixed set of specific operations.

create_approval and create_improvement require no permission scope at
all — on purpose. They're the escape hatch for a role that can't do
something directly (e.g. Finance, which is deliberately never granted
database.write): requiring a scope to merely *request* something a role
doesn't have would make the request mechanism itself unreachable by the
exact roles it exists for. What actually gates who can call them is
AgentOrchestrator's tool_names check (a role has to be configured with
the tool at all) — recording a request does nothing consequential by
itself; nothing happens until a human approves it.
"""

from core.approvals.manager import ApprovalManager
from core.improvements.manager import ImprovementManager
from core.memory.base import MemoryStore, MemoryType
from core.permissions.scopes import PermissionScope
from core.projects.base import ProjectStore
from core.tasks.base import TaskStore
from core.tools.base import ExecutionPolicy, RiskLevel, ToolDefinition
from core.tools.registry import ToolRegistry


def register_standard_tools(
    registry: ToolRegistry,
    *,
    projects: ProjectStore,
    tasks: TaskStore,
    memory: MemoryStore,
    approvals: ApprovalManager,
    improvements: ImprovementManager,
) -> None:
    def _project_to_dict(project):
        return {
            "id": project.id, "name": project.name, "description": project.description,
            "status": project.status, "priority": project.priority, "created_at": project.created_at,
        }

    def _task_to_dict(task):
        return {
            "id": task.id, "project_id": task.project_id, "title": task.title,
            "description": task.description, "status": task.status, "priority": task.priority,
            "assigned_role": task.assigned_role, "created_at": task.created_at,
        }

    def _memory_to_dict(m):
        return {
            "id": m.id, "memory_type": m.memory_type.value, "title": m.title,
            "content": m.content, "importance": m.importance, "source": m.source, "created_at": m.created_at,
        }

    def create_project(name: str, description: str = "", status: str = "active", priority: int = 5):
        return _project_to_dict(projects.create(name, description=description, status=status, priority=priority))

    def list_projects(status: str | None = None):
        return [_project_to_dict(p) for p in projects.list(status=status)]

    def create_task(project_id: str, title: str, description: str = "", priority: int = 5,
                     status: str = "pending", assigned_role: str | None = None):
        return _task_to_dict(tasks.create(
            project_id, title, description=description, priority=priority, status=status, assigned_role=assigned_role
        ))

    def update_task_status(task_id: str, status: str):
        task = tasks.update_status(task_id, status)
        return _task_to_dict(task) if task else {"error": f"no task with id '{task_id}'"}

    def list_tasks_for_project(project_id: str):
        return [_task_to_dict(t) for t in tasks.list_for_project(project_id)]

    def create_memory(content: str, memory_type: str = "reflective", title: str = "",
                       importance: int = 5, source: str = "iv"):
        return _memory_to_dict(memory.add(
            content, memory_type=MemoryType(memory_type), title=title, importance=importance, source=source
        ))

    def list_recent_memories(memory_type: str | None = None, limit: int = 20):
        mt = MemoryType(memory_type) if memory_type else None
        return [_memory_to_dict(m) for m in memory.query(memory_type=mt, limit=limit)]

    def create_approval(action_type: str, description: str, requested_by: str = "iv", risk_level: str = "high"):
        """Records a pending approval request for a consequential action
        this role can't perform directly. Does NOT execute anything — a
        human decides via the approval interface."""
        request = approvals.request(
            action_type=action_type, description=description, requested_by=requested_by, risk_level=risk_level
        )
        return {"id": request.id, "status": request.status.value}

    def create_improvement(agent_name: str, problem_identified: str, proposed_change: str,
                            affected_files: list[str] | None = None):
        proposal = improvements.propose(
            agent_name=agent_name, problem_identified=problem_identified,
            proposed_change=proposed_change, affected_files=affected_files,
        )
        return {"id": proposal.id, "status": proposal.status.value, "approval_id": proposal.approval_id}

    registry.register(ToolDefinition(
        name="create_project", description="Creates a new project to track a real-world objective.",
        input_schema={"type": "object", "properties": {
            "name": {"type": "string"}, "description": {"type": "string"},
            "status": {"type": "string"}, "priority": {"type": "integer"},
        }, "required": ["name"]},
        output_schema={"type": "object"}, handler=create_project,
        required_permissions=[PermissionScope.DATABASE_WRITE], risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="list_projects", description="Lists projects, optionally filtered by status.",
        input_schema={"type": "object", "properties": {"status": {"type": "string"}}},
        output_schema={"type": "array"}, handler=list_projects,
        required_permissions=[PermissionScope.DATABASE_READ], risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="create_task", description="Creates a task under an existing project.",
        input_schema={"type": "object", "properties": {
            "project_id": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"},
            "priority": {"type": "integer"}, "status": {"type": "string"}, "assigned_role": {"type": "string"},
        }, "required": ["project_id", "title"]},
        output_schema={"type": "object"}, handler=create_task,
        required_permissions=[PermissionScope.DATABASE_WRITE], risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="update_task_status", description="Updates the status of an existing task.",
        input_schema={"type": "object", "properties": {
            "task_id": {"type": "string"}, "status": {"type": "string"},
        }, "required": ["task_id", "status"]},
        output_schema={"type": "object"}, handler=update_task_status,
        required_permissions=[PermissionScope.DATABASE_WRITE], risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="list_tasks_for_project", description="Lists tasks under a given project.",
        input_schema={"type": "object", "properties": {"project_id": {"type": "string"}}, "required": ["project_id"]},
        output_schema={"type": "array"}, handler=list_tasks_for_project,
        required_permissions=[PermissionScope.DATABASE_READ], risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="create_memory", description="Stores a long-term memory entry (episodic, semantic, or reflective).",
        input_schema={"type": "object", "properties": {
            "content": {"type": "string"}, "memory_type": {"type": "string"}, "title": {"type": "string"},
            "importance": {"type": "integer"}, "source": {"type": "string"},
        }, "required": ["content"]},
        output_schema={"type": "object"}, handler=create_memory,
        required_permissions=[PermissionScope.DATABASE_WRITE], risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="list_recent_memories", description="Lists recent memories, optionally filtered by type.",
        input_schema={"type": "object", "properties": {
            "memory_type": {"type": "string"}, "limit": {"type": "integer"},
        }},
        output_schema={"type": "array"}, handler=list_recent_memories,
        required_permissions=[PermissionScope.DATABASE_READ], risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="create_approval",
        description="Records a pending approval request for a consequential action iV cannot perform "
                     "directly (not for self-improvement proposals — use create_improvement for those). "
                     "Does not execute the action itself.",
        input_schema={"type": "object", "properties": {
            "action_type": {"type": "string"}, "description": {"type": "string"},
            "requested_by": {"type": "string"}, "risk_level": {"type": "string"},
        }, "required": ["action_type", "description"]},
        output_schema={"type": "object"}, handler=create_approval,
        required_permissions=[], risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="create_improvement",
        description="Records a proposed self-improvement pending human approval before it can be deployed.",
        input_schema={"type": "object", "properties": {
            "agent_name": {"type": "string"}, "problem_identified": {"type": "string"},
            "proposed_change": {"type": "string"}, "affected_files": {"type": "array", "items": {"type": "string"}},
        }, "required": ["agent_name", "problem_identified", "proposed_change"]},
        output_schema={"type": "object"}, handler=create_improvement,
        required_permissions=[], risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))
