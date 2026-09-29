"""Persisted agent definitions: an agent you compose at runtime by pairing
a system prompt with a model and a set of tools.

core/agent/roles.py already proved the shape — an AgentRole is pure
configuration, and the runtime is identical for every one of them. This
module makes that configuration something you can create without editing
code, which is what turns "nine fixed specialists" into "assign an agent
to the job".

Two safety properties, both structural rather than advisory:

  * **Scopes are derived, never supplied.** A custom agent's permission
    scopes are computed from what its tools actually require, by reading
    the ToolRegistry. There is no field to put `permissions.manage` in,
    so a composed agent cannot be given authority its tools do not need.
    This is the difference between composing capability and escalating
    privilege.
  * **Tools come from an allowlist.** COMPOSABLE_TOOLS is the set a
    composed agent may draw on — reads, planning, memory, and the
    request-something-from-a-human escape hatches. Anything that writes
    to a repository, runs a command, pushes, or manages permissions is
    absent: those stay with the built-in roles a human edited into the
    source. A first version of user-composable agents should not be able
    to assemble one that can run arbitrary commands.

Built-in roles (DEFAULT_ROLES) are not stored here and cannot be
overwritten from here; see core/agent/catalog.py for how the two sets are
merged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.agent.roles import AgentRole
from core.storage.base import StorageBackend

COLLECTION = "agent_definitions"

# What a composed agent may be given. Deliberately read-and-propose only:
# everything here either reads state, records a plan, or asks a human for
# something. Nothing here changes the world on its own.
COMPOSABLE_TOOLS = frozenset({
    "list_projects", "list_tasks_for_project", "create_project", "create_task",
    "update_task_status", "create_memory", "list_recent_memories",
    "create_approval", "create_improvement",
    "self_list_files", "self_read_file", "self_search_repository",
    "self_inspect_dependencies", "self_list_tests", "self_git",
})

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{1,48}$")
MAX_PROMPT_CHARS = 8000


class AgentDefinitionError(ValueError):
    """Raised for a definition that is malformed, or that asks for a tool
    outside COMPOSABLE_TOOLS."""


@dataclass
class AgentDefinition:
    id: str
    name: str
    description: str  # the system prompt this agent runs with
    model_provider_order: list[str]
    tool_names: list[str]
    permission_scopes: list[str]
    created_at: str
    created_by: str = "owner"

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "AgentDefinition":
        return cls(
            id=record["id"],
            name=record["name"],
            description=record.get("description", ""),
            model_provider_order=list(record.get("model_provider_order", [])),
            tool_names=list(record.get("tool_names", [])),
            permission_scopes=list(record.get("permission_scopes", [])),
            created_at=record["created_at"],
            created_by=record.get("created_by", "owner"),
        )

    def to_role(self) -> AgentRole:
        """An AgentRole, so AgentOrchestrator cannot tell a composed agent
        from a built-in one — the runtime stays identical, which is the
        property that made roles-as-data worth having."""
        return AgentRole(
            name=self.name,
            description=self.description,
            tool_names=list(self.tool_names),
            permission_scopes=list(self.permission_scopes),
            model_provider_order=list(self.model_provider_order),
        )


def scopes_required_by(tool_names: list[str], tools) -> list[str]:
    """The union of permission scopes the named tools declare. Reading the
    requirement off the ToolDefinition rather than accepting it as input
    is what makes over-privileging impossible: an agent ends up with
    exactly what its tools need."""
    scopes: set[str] = set()
    for name in tool_names:
        definition = tools.get(name)
        if definition is not None:
            scopes.update(definition.required_permissions)
    return sorted(scopes)


class AgentDefinitionStore:
    def __init__(self, storage: StorageBackend, *, reserved_names: frozenset[str] = frozenset()) -> None:
        self._storage = storage
        # Built-in role names, so a composed agent cannot shadow one and
        # quietly replace a specialist a human configured in source.
        self._reserved = {name.lower() for name in reserved_names}

    def create(
        self,
        *,
        name: str,
        description: str,
        model_provider_order: list[str],
        tool_names: list[str] | None = None,
        tools=None,
        created_by: str = "owner",
    ) -> AgentDefinition:
        name = (name or "").strip()
        if not _NAME_PATTERN.match(name):
            raise AgentDefinitionError(
                "agent name must be 2-49 characters of letters, digits, spaces, '-' or '_'"
            )
        if name.lower() in self._reserved:
            raise AgentDefinitionError(f"'{name}' is a built-in role name and cannot be redefined")
        if self.get(name) is not None:
            raise AgentDefinitionError(f"an agent named '{name}' already exists")

        description = (description or "").strip()
        if not description:
            raise AgentDefinitionError("an agent needs a system prompt (description)")
        if len(description) > MAX_PROMPT_CHARS:
            raise AgentDefinitionError(f"system prompt exceeds {MAX_PROMPT_CHARS} characters")

        if not model_provider_order:
            raise AgentDefinitionError("an agent needs at least one model provider")

        requested = list(dict.fromkeys(tool_names or []))
        forbidden = [t for t in requested if t not in COMPOSABLE_TOOLS]
        if forbidden:
            raise AgentDefinitionError(
                f"these tools cannot be assigned to a composed agent: {', '.join(sorted(forbidden))}. "
                f"Composable tools are: {', '.join(sorted(COMPOSABLE_TOOLS))}"
            )

        stored = self._storage.insert(COLLECTION, {
            "name": name,
            "description": description,
            "model_provider_order": list(model_provider_order),
            "tool_names": requested,
            # Derived, not accepted from the caller. See module docstring.
            "permission_scopes": scopes_required_by(requested, tools) if tools else [],
            "created_by": created_by,
        })
        return AgentDefinition.from_record(stored)

    def get(self, name: str) -> AgentDefinition | None:
        wanted = (name or "").strip().lower()
        for record in self._storage.query(COLLECTION):
            if record.get("name", "").lower() == wanted:
                return AgentDefinition.from_record(record)
        return None

    def list(self) -> list[AgentDefinition]:
        records = self._storage.query(COLLECTION, order_by="created_at")
        return [AgentDefinition.from_record(r) for r in records]

    def delete(self, name: str) -> bool:
        definition = self.get(name)
        return self._storage.delete(COLLECTION, definition.id) if definition else False
