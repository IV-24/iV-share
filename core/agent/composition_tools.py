"""Tools for composing a new agent at runtime: a system prompt paired with
a model and a set of tools.

create_agent requires no permission scope, for the same reason
create_approval and create_improvement do not (see
core/tools/standard.py): defining an agent is not itself a consequential
act. The composed agent's scopes are derived from its tools, capped by
COMPOSABLE_TOOLS, so the most that can be composed is something that
reads, plans, and asks — never something that writes to a repository or
runs a command. Those capabilities remain with the built-in roles a human
put in the source.

What stops this being an escalation path in a subtler way: the composed
agent's permission grants still have to exist. Composing an agent that
declares database.write does not grant database.write to it — the grant
is made explicitly at bootstrap, and an agent whose grants are missing
gets a clean denial from ToolRegistry like anything else.
"""

from __future__ import annotations

from core.agent.store import COMPOSABLE_TOOLS, AgentDefinitionError, AgentDefinitionStore
from core.audit.log import AuditLog
from core.models.registry import ModelRegistry
from core.permissions.manager import PermissionManager
from core.tools.base import ExecutionPolicy, RiskLevel, ToolDefinition
from core.tools.registry import ToolRegistry


def register_composition_tools(
    registry: ToolRegistry,
    *,
    store: AgentDefinitionStore,
    models: ModelRegistry,
    permissions: PermissionManager,
    audit: AuditLog | None = None,
) -> None:
    def create_agent(
        name: str,
        system_prompt: str,
        model_provider_order: list[str] | None = None,
        tool_names: list[str] | None = None,
    ):
        available = models.provider_names()
        requested_models = [m for m in (model_provider_order or []) if m in available]
        if not requested_models:
            # Falling back to every configured provider beats refusing:
            # the useful failure mode for "you asked for a model that
            # isn't set up" is "used the ones that are", not "no agent".
            requested_models = [m for m in available if m != "null"] or available

        try:
            definition = store.create(
                name=name,
                description=system_prompt,
                model_provider_order=requested_models,
                tool_names=tool_names,
                tools=registry,
                created_by="iv",
            )
        except AgentDefinitionError as exc:
            return {"created": False, "error": str(exc)}

        # Grant the composed agent exactly the scopes its own tools
        # declare — the set derived in the store, nothing wider.
        for scope in definition.permission_scopes:
            if not permissions.is_granted(definition.name, scope):
                permissions.grant(
                    definition.name, scope, granted_by="agent-composition",
                    reason=f"tools: {', '.join(definition.tool_names)}",
                )
        if audit is not None:
            audit.record(
                actor="iv", action="agent.create", resource=definition.name,
                metadata={"tools": definition.tool_names, "models": definition.model_provider_order},
            )
        return {
            "created": True,
            "name": definition.name,
            "models": definition.model_provider_order,
            "tools": definition.tool_names,
            "scopes": definition.permission_scopes,
        }

    registry.register(ToolDefinition(
        name="create_agent",
        description=(
            "Composes a new agent from a system prompt, a model preference, and a set of "
            "tools, so it can be delegated to afterwards. Tools are limited to read, "
            "planning, memory, and request-approval capabilities — a composed agent cannot "
            "be given repository writes, command execution, or permission management. "
            f"Assignable tools: {', '.join(sorted(COMPOSABLE_TOOLS))}."
        ),
        input_schema={"type": "object", "properties": {
            "name": {"type": "string"},
            "system_prompt": {"type": "string", "description": "How this agent should behave."},
            "model_provider_order": {
                "type": "array", "items": {"type": "string"},
                "description": "Preferred providers in order, e.g. ['claude','groq'].",
            },
            "tool_names": {"type": "array", "items": {"type": "string"}},
        }, "required": ["name", "system_prompt"]},
        output_schema={"type": "object"},
        handler=create_agent,
        required_permissions=[],
        risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))
