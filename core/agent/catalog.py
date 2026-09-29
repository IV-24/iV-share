"""One lookup over both kinds of agent: the built-in roles compiled into
core/agent/roles.py, and the ones composed at runtime and persisted by
core/agent/store.py.

Built-ins win on a name collision, and the store refuses to create a
colliding name in the first place — two defences for the same property,
because "a composed agent silently replaced the Security specialist"
is the kind of failure that is invisible until it matters.

The catalog is read-through rather than cached: a composed agent created
during a conversation is available on the next turn without restarting
the server, which is the whole point of composing one.
"""

from __future__ import annotations

from core.agent.roles import DEFAULT_ROLES, AgentRole
from core.agent.store import AgentDefinitionStore


class AgentCatalog:
    def __init__(self, store: AgentDefinitionStore | None = None) -> None:
        self._store = store

    def get(self, name: str) -> AgentRole | None:
        """Accepts either the role key ('engineering') or the display name
        ('Engineering Specialist'), because a model asked to delegate will
        use whichever of the two it saw."""
        if not name:
            return None
        wanted = name.strip().lower()

        role = DEFAULT_ROLES.get(wanted)
        if role is not None:
            return role
        for candidate in DEFAULT_ROLES.values():
            if candidate.name.lower() == wanted:
                return candidate

        if self._store is not None:
            definition = self._store.get(name)
            if definition is not None:
                return definition.to_role()
        return None

    def list(self) -> list[dict]:
        """Discovery view: what a model (or a human) sees when choosing who
        to hand a task to. No prompts are included — a full system prompt
        per agent would crowd out the conversation it is meant to inform."""
        entries = [
            {
                "key": key,
                "name": role.name,
                "summary": role.description.split(".")[0][:160],
                "models": list(role.model_provider_order),
                "tools": list(role.tool_names),
                "builtin": True,
            }
            for key, role in DEFAULT_ROLES.items()
        ]
        if self._store is not None:
            entries.extend(
                {
                    "key": definition.name,
                    "name": definition.name,
                    "summary": definition.description.split(".")[0][:160],
                    "models": list(definition.model_provider_order),
                    "tools": list(definition.tool_names),
                    "builtin": False,
                }
                for definition in self._store.list()
            )
        return entries

    def names(self) -> list[str]:
        return [entry["key"] for entry in self.list()]
