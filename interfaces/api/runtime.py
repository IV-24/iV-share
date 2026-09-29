"""Assembles the full core stack for the API server: local SQLite
storage, permissions, approvals, the standard tool set, every configured
model provider, and the agent orchestrator. This is the one place that
wiring happens — interfaces/api/main.py's routes only ever call into an
already-built ApiRuntime, they never construct core components directly.

Single-user bootstrap: every DEFAULT_ROLES entry is granted its own
declared permission scopes at startup. That's a reasonable default for a
single-owner server whose database only it can reach (see
docs/MIGRATION_AUDIT.md's decision log) — it would NOT be the right
pattern for a multi-tenant or networked-by-default deployment, where
grants should come from an explicit admin action instead.
"""

from dataclasses import dataclass
from pathlib import Path

from adapters.games.chess_tools import register_chess_tools
from adapters.models import register_configured_providers
from adapters.selfinspect.repo import SelfInspector
from adapters.selfinspect.tools import register_self_inspection_tools
from adapters.workspace.config import load_github_token
from adapters.workspace.tools import register_workspace_tools
from core.agent.catalog import AgentCatalog
from core.agent.composition_tools import register_composition_tools
from core.agent.delegation import DelegationBudget, register_delegation_tool
from core.agent.orchestrator import AgentOrchestrator
from core.agent.roles import DEFAULT_ROLES, assert_tool_names_are_registered
from core.agent.store import AgentDefinitionStore
from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.configuration.settings import Settings, load_settings
from core.context.manager import ContextManager
from core.conversations.base import ConversationStore
from core.games.chess import ChessGameStore
from core.improvements.manager import ImprovementManager
from core.memory.base import MemoryStore
from core.models.null_provider import NullModelProvider
from core.models.registry import ModelRegistry
from core.permissions.manager import PermissionManager
from core.projects.base import ProjectStore
from core.runs.base import RunRecorder
from core.storage.base import StorageBackend
from core.storage.local import SqliteStorage
from core.tasks.base import TaskStore
from core.tools.guardian import register_guardian_tools
from core.tools.registry import ToolRegistry
from core.tools.standard import register_standard_tools


INSTALL_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class ApiRuntime:
    storage: StorageBackend
    orchestrator: AgentOrchestrator
    conversations: ConversationStore
    approvals: ApprovalManager
    permissions: PermissionManager
    audit: AuditLog
    memory: MemoryStore
    models: ModelRegistry
    tools: ToolRegistry
    projects: ProjectStore
    tasks: TaskStore
    improvements: ImprovementManager
    games: ChessGameStore
    runs: RunRecorder
    catalog: AgentCatalog | None = None
    agents: AgentDefinitionStore | None = None
    # The per-turn delegation fan-out/depth counters. Held here because
    # only the caller that owns a *turn* (an API route) knows where a turn
    # begins, and that is the one place reset() may be called. Defaulted
    # to None so a hand-built runtime (the API tests build one) stays
    # valid; None simply means "nothing resets the budget", which is only
    # correct for a runtime that never delegates.
    delegation_budget: DelegationBudget | None = None
    # Defaulted so an existing caller constructing an ApiRuntime by hand
    # (the API tests do) keeps working without knowing about
    # self-inspection. None simply means "this runtime cannot read its
    # own source," which is a legitimate configuration.
    inspector: SelfInspector | None = None


def build_runtime(settings: Settings | None = None) -> ApiRuntime:
    settings = settings or load_settings()

    storage = SqliteStorage(settings.storage.local_db_path)
    audit = AuditLog(storage)
    permissions = PermissionManager(storage, audit)
    approvals = ApprovalManager(storage, audit)
    tools = ToolRegistry(permissions, approvals, audit)
    memory = MemoryStore(storage)
    conversations = ConversationStore(storage)
    projects = ProjectStore(storage)
    tasks = TaskStore(storage)
    improvements = ImprovementManager(storage, approvals, audit)
    games = ChessGameStore(storage)
    runs = RunRecorder(storage)
    agents = AgentDefinitionStore(storage, reserved_names=frozenset(
        [key for key in DEFAULT_ROLES] + [role.name for role in DEFAULT_ROLES.values()]
    ))
    catalog = AgentCatalog(agents)

    register_standard_tools(
        tools, projects=projects, tasks=tasks, memory=memory, approvals=approvals, improvements=improvements
    )
    register_guardian_tools(tools, audit=audit, permissions=permissions)
    register_workspace_tools(
        tools, workspace_root=Path(settings.workspace.workspace_root), github_token=load_github_token()
    )
    register_chess_tools(tools, games=games)
    # Read-only introspection of iV's own installation directory. Scoped
    # to self.inspect (see core/permissions/scopes.py) and registered
    # separately from the workspace tools on purpose: the tools that can
    # write are never the tools aimed at iV's own source.
    inspector = register_self_inspection_tools(tools, install_root=INSTALL_ROOT)

    models = ModelRegistry()
    models.register(NullModelProvider())
    register_configured_providers(models, settings.model)

    # Delegation needs the orchestrator, which needs the tool registry
    # that delegation registers into. The getter closes that loop without
    # constraining construction order — see core/agent/delegation.py.
    orchestrator_holder: dict[str, AgentOrchestrator] = {}
    delegation_budget = register_delegation_tool(
        tools, orchestrator_getter=lambda: orchestrator_holder["value"],
        catalog=catalog, audit=audit,
    )
    register_composition_tools(
        tools, store=agents, models=models, permissions=permissions, audit=audit
    )

    # Fails startup rather than a mid-conversation tool-execution error if
    # a role's tool_names ever drifts from what got registered above --
    # exactly the class of bug create_reflection was for the Memory
    # Specialist (see core/agent/roles.py's DEFAULT_ROLES docstring for
    # that one).
    assert_tool_names_are_registered(tools)

    for role in DEFAULT_ROLES.values():
        for scope in role.permission_scopes:
            if not permissions.is_granted(role.name, scope):
                permissions.grant(role.name, scope, granted_by="api-bootstrap")

    orchestrator = AgentOrchestrator(models, tools, memory, ContextManager())
    orchestrator_holder["value"] = orchestrator

    # Composed agents persist across restarts, so their grants have to be
    # restored the same way built-in roles' are — a stored agent whose
    # scopes vanished on reboot would fail in a way that looks like a
    # permission bug rather than a missing bootstrap.
    for definition in agents.list():
        for scope in definition.permission_scopes:
            if not permissions.is_granted(definition.name, scope):
                permissions.grant(definition.name, scope, granted_by="api-bootstrap")

    return ApiRuntime(
        storage=storage, orchestrator=orchestrator, conversations=conversations,
        approvals=approvals, permissions=permissions, audit=audit, memory=memory, models=models,
        tools=tools, projects=projects, tasks=tasks, improvements=improvements, inspector=inspector,
        runs=runs,
        catalog=catalog, agents=agents, delegation_budget=delegation_budget, games=games,
    )
