"""Minimal CLI client for iV Core — proof that core needs no web frontend
to be useful. build_runtime() assembles the exact same core components a
future web API would (storage, permissions, approvals, tools, models,
agent orchestrator); run_repl() is the only CLI-specific part, a plain
stdin/stdout loop. Run with:

    python -m interfaces.cli.main [role]

role defaults to "coordinator" and must be a key in core.agent.roles.DEFAULT_ROLES.

This bootstraps by auto-granting the chosen role's own declared
permission scopes to itself at startup — reasonable for a single-user
local CLI talking to a database only it can reach, but NOT the pattern a
multi-tenant or networked interface should follow (there, grants come
from an explicit admin/approval action, not self-assignment at boot).
"""

import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

from adapters.models import register_configured_providers
from adapters.selfinspect.tools import register_self_inspection_tools
from adapters.workspace.config import load_github_token
from adapters.workspace.tools import register_workspace_tools
from core.agent.orchestrator import AgentOrchestrator
from core.agent.roles import DEFAULT_ROLES, AgentRole
from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.configuration.settings import Settings, load_settings
from core.context.manager import ContextManager
from core.conversations.base import ConversationStore
from core.haven.manifest import build_haven_manifest
from core.improvements.manager import ImprovementManager
from core.memory.base import MemoryStore
from core.models.base import ModelMessage
from core.models.null_provider import NullModelProvider
from core.models.registry import ModelRegistry
from core.permissions.manager import PermissionManager
from core.projects.base import ProjectStore
from core.storage.base import StorageBackend
from core.storage.local import SqliteStorage
from core.tasks.base import TaskStore
from core.tools.guardian import register_guardian_tools
from core.tools.registry import ToolRegistry
from core.tools.standard import register_standard_tools

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


@dataclass
class Runtime:
    storage: StorageBackend
    orchestrator: AgentOrchestrator
    conversations: ConversationStore
    role: AgentRole
    tools: ToolRegistry
    permissions: PermissionManager


def build_runtime(settings: Settings | None = None, *, role_name: str = "coordinator") -> Runtime:
    settings = settings or load_settings()
    role = DEFAULT_ROLES.get(role_name)
    if role is None:
        raise ValueError(f"unknown role '{role_name}' — options: {', '.join(DEFAULT_ROLES)}")

    # DEFAULT_ROLES' provider orders assume real API keys are configured.
    # The CLI must still work with none configured, so "null" (always
    # registered below) is appended as a last-resort fallback — a copy,
    # since DEFAULT_ROLES is shared module state other callers rely on.
    if "null" not in role.model_provider_order:
        role = replace(role, model_provider_order=[*role.model_provider_order, "null"])

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

    register_standard_tools(
        tools, projects=projects, tasks=tasks, memory=memory, approvals=approvals, improvements=improvements
    )
    register_guardian_tools(tools, audit=audit, permissions=permissions)
    register_workspace_tools(
        tools, workspace_root=Path(settings.workspace.workspace_root), github_token=load_github_token()
    )
    register_self_inspection_tools(tools, install_root=ROOT_DIR)

    models = ModelRegistry()
    models.register(NullModelProvider())
    register_configured_providers(models, settings.model)

    for scope in role.permission_scopes:
        if not permissions.is_granted(role.name, scope):
            permissions.grant(role.name, scope, granted_by="cli-bootstrap")

    orchestrator = AgentOrchestrator(models, tools, memory, ContextManager())
    return Runtime(
        storage=storage, orchestrator=orchestrator, conversations=conversations, role=role,
        tools=tools, permissions=permissions,
    )


def run_repl(runtime: Runtime) -> None:
    conversation = runtime.conversations.create()
    print(f"iV ({runtime.role.name}) — conversation {conversation.id}. Ctrl-D to exit.")

    while True:
        try:
            message = input("you> ").strip()
        except EOFError:
            print()
            break
        if not message:
            continue

        if message == "/haven":
            manifest = build_haven_manifest(tools=runtime.tools, permissions=runtime.permissions)
            print(f"environment: {manifest.environment}")
            print(f"tools ({len(manifest.tools)}): {[t['name'] for t in manifest.tools]}")
            print(f"grants ({len(manifest.grants)}):")
            for g in manifest.grants:
                print(f"  {g['principal']} -> {g['scope']} (granted by {g['granted_by']} at {g['granted_at']})")
            continue

        history = [
            ModelMessage(role=("assistant" if m.role in ("iv", "assistant") else "user"), content=m.content)
            for m in runtime.conversations.history(conversation.id)
        ]
        result = runtime.orchestrator.handle_message(runtime.role, message, history)

        runtime.conversations.add_message(conversation.id, "user", message)
        runtime.conversations.add_message(
            conversation.id, "iv", result.response.text, model_used=f"{result.response.provider}:{result.response.model_name}"
        )
        print(f"iv> {result.response.text}")


def load_environment() -> None:
    """Side effects (reading .env, defaulting IV_LOCAL_DB_PATH) live in
    their own function, called only from the real CLI entrypoint below —
    not at import time, and not inside build_runtime(), which stays pure
    enough for tests to call directly with an explicit Settings object.
    Same .env location and install-directory-relative database default as
    run.py, so the CLI and the API server agree on where data lives
    regardless of which one you launch, and from where."""
    load_dotenv(ROOT_DIR / "backend" / ".env")
    os.environ.setdefault("IV_LOCAL_DB_PATH", str(ROOT_DIR / "iv.db"))
    os.environ.setdefault("IV_WORKSPACE_ROOT", str(ROOT_DIR / "workspace"))


def main(argv: list[str] | None = None) -> None:
    load_environment()
    argv = argv if argv is not None else sys.argv[1:]
    role_name = argv[0] if argv else "coordinator"
    runtime = build_runtime(role_name=role_name)
    run_repl(runtime)


if __name__ == "__main__":
    main()
