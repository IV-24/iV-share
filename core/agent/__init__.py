"""Agent roles and orchestration. The original 'King/Lords' roster is
retired in favor of conventional naming (Coordinator/Specialists/
Gatherers/Investigators/Workers — see core/agent/roles.py's module
docstring for the full mapping and rationale). A role is a policy object
— system prompt + tool-access profile + permission scopes + model
provider preference — not a separate running application. Adding a new
specialized agent means adding a role definition, not touching the
runtime."""

from core.agent.orchestrator import AgentOrchestrator
from core.agent.roles import DEFAULT_ROLES, AgentRole

__all__ = ["AgentRole", "DEFAULT_ROLES", "AgentOrchestrator"]
