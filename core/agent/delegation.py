"""Delegation: one agent handing a task to another, each running on its
own configured model with its own tools.

This is what makes "the whole harness answers, each model in its role"
real rather than described. Before it, /api/chat picked exactly one role
and that role's provider order was the only model involved; the
Coordinator could talk *about* specialists but never reach one. Now
delegation is an ordinary tool, which means it inherits everything tools
already get: the role's tool_names check, permission scopes, approval
gating, and an audit entry per call. A delegation that happened is a
delegation you can see in `python -m interfaces.cli.activity`.

Four bounds, because "an agent can invoke an agent" is the shape that
runs away:

  * **Depth.** A delegate may itself delegate, but only MAX_DELEGATION_DEPTH
    levels down.
  * **Fan-out.** A single turn may delegate at most MAX_DELEGATIONS_PER_TURN
    times, so a confused coordinator cannot spend the afternoon (and the
    API budget) asking every specialist the same question.
  * **Wall clock.** A turn has TURN_WALL_CLOCK_BUDGET_SECONDS total,
    counted from the moment `reset()` starts it. Depth and fan-out bound
    *how many* delegations happen; this bounds *how long the owner waits*
    regardless of how few of them there were -- one slow provider call is
    enough to blow past a reasonable reply time even at a fan-out of one.
  * **Capability.** The delegate runs as itself, not as its caller. A
    Coordinator with no repository tools delegating to Engineering does
    not thereby gain repository tools — the Engineering role's own
    tool_names and scopes apply, and its own approvals still gate it.
    Delegation composes capability; it never sums it.

State lives in a contextvars.ContextVar, not threading.local, and that
choice is deliberate rather than cosmetic. A sync FastAPI endpoint runs on
a pooled worker thread, so two different requests (two different turns)
must never share a budget -- thread-local achieved that, but so does a
contextvar, since each request's route handler runs in its own context by
default. What thread-local could not do is let a single turn's several
delegate_to_agent calls run *concurrently* (core/agent/orchestrator.py's
_run_tool_calls) while still sharing one budget: a plain worker thread
spawned to run one of those calls starts with a fresh, empty contextvars
Context unless the caller explicitly copies its own context into that
thread first. The orchestrator does exactly that
(contextvars.copy_context() before submitting to its thread pool), which
is what makes the same _TurnState instance -- and hence the same
depth/count/deadline -- visible to every concurrent delegate in one turn,
while two unrelated turns on two unrelated worker threads still start
from nothing and never see each other's state. The lock in
DelegationBudget guards the one thing a contextvar copy does not: mutating
that shared _TurnState safely once more than one thread can reach it at
once.
"""

from __future__ import annotations

import logging
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass

from core.agent.catalog import AgentCatalog
from core.agent.roles import DEFAULT_ROLES
from core.audit.log import AuditLog
from core.models.base import ModelUnavailableError
from core.permissions.scopes import PermissionScope
from core.tools.base import ExecutionPolicy, RiskLevel, ToolDefinition
from core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

MAX_DELEGATION_DEPTH = 2
MAX_DELEGATIONS_PER_TURN = 6
TURN_WALL_CLOCK_BUDGET_SECONDS = 90.0
DELEGATE_TOOL_NAME = "delegate_to_agent"

# The Coordinator is the only built-in role configured with this tool
# (composed agents are restricted to COMPOSABLE_TOOLS, which excludes it
# entirely -- see core/agent/store.py), so "no self-delegation" reduces
# today to "the Coordinator is never a valid delegate target." If a
# second delegating role is ever added, this needs to become "the calling
# role may not delegate to itself" instead of a fixed name.
_COORDINATOR_ROLE_NAME = DEFAULT_ROLES["coordinator"].name


@dataclass
class _TurnState:
    depth: int = 0
    count: int = 0
    deadline: float | None = None  # time.monotonic() value; None = no limit


class DelegationBudget:
    """Per-turn depth, fan-out, and wall-clock counters, safe to read and
    mutate from several threads at once when one turn fans out to
    multiple delegates concurrently. See the module docstring for why
    this is a contextvar with a lock rather than threading.local."""

    def __init__(self) -> None:
        self._var: ContextVar[_TurnState] = ContextVar("delegation_turn_state")
        self._lock = threading.Lock()

    @property
    def state(self) -> _TurnState:
        try:
            return self._var.get()
        except LookupError:
            state = _TurnState()
            self._var.set(state)
            return state

    def reset(self, *, wall_clock_budget_seconds: float = TURN_WALL_CLOCK_BUDGET_SECONDS) -> None:
        """Called once at the start of a turn (the API route, not the
        orchestrator -- see interfaces/api/main.py._run_turn), so a fresh
        turn always starts with depth 0, count 0, and a full wall-clock
        budget, regardless of what the worker thread serving it did on a
        previous request."""
        deadline = (time.monotonic() + wall_clock_budget_seconds) if wall_clock_budget_seconds else None
        self._var.set(_TurnState(deadline=deadline))

    def check(self) -> str | None:
        """Returns a refusal reason, or None if a delegation may proceed."""
        with self._lock:
            state = self.state
            if state.depth >= MAX_DELEGATION_DEPTH:
                return (
                    f"delegation depth limit reached ({MAX_DELEGATION_DEPTH}); "
                    "answer directly rather than delegating further"
                )
            if state.count >= MAX_DELEGATIONS_PER_TURN:
                return (
                    f"delegation limit for this turn reached ({MAX_DELEGATIONS_PER_TURN}); "
                    "summarize what you have and answer"
                )
            if state.deadline is not None and time.monotonic() > state.deadline:
                return (
                    f"this turn's time budget ({TURN_WALL_CLOCK_BUDGET_SECONDS:.0f}s) is spent; "
                    "summarize what you have and answer rather than delegating further"
                )
        return None

    def enter(self) -> None:
        with self._lock:
            state = self.state
            state.depth += 1
            state.count += 1

    def exit(self) -> None:
        with self._lock:
            self.state.depth -= 1


def register_delegation_tool(
    registry: ToolRegistry,
    *,
    orchestrator_getter,
    catalog: AgentCatalog,
    audit: AuditLog | None = None,
    budget: DelegationBudget | None = None,
) -> DelegationBudget:
    """orchestrator_getter is a callable rather than the orchestrator
    itself: the orchestrator needs the tool registry to be built, and this
    tool needs the orchestrator, so one of the two references has to be
    late-bound. A getter keeps the cycle out of construction order."""
    budget = budget or DelegationBudget()

    def delegate_to_agent(agent: str, task: str, context: str = ""):
        refusal = budget.check()
        if refusal:
            return {"agent": agent, "delegated": False, "error": refusal}

        role = catalog.get(agent)
        if role is None:
            return {
                "agent": agent,
                "delegated": False,
                "error": f"no agent named '{agent}'. Available: {', '.join(catalog.names())}",
            }
        if role.name == _COORDINATOR_ROLE_NAME:
            return {
                "agent": role.name, "delegated": False,
                "error": "the Coordinator cannot delegate to itself; answer directly instead",
            }

        prompt = f"{task}\n\nContext from the coordinator:\n{context}" if context else task

        budget.enter()
        try:
            result = orchestrator_getter().handle_message(role, prompt, [])
            answer = result.response.text
            model_used = (
                f"{result.response.provider}:{result.response.model_name}"
                if result.response.provider else None
            )
        except ModelUnavailableError as exc:
            logger.warning("delegation to %s failed: no model available (%s)", role.name, exc)
            return {
                "agent": role.name, "delegated": False,
                "error": f"no model configured for {role.name} is available right now",
            }
        except Exception as exc:  # noqa: BLE001 - a failed delegate must not kill the caller's turn
            logger.exception("delegation to %s raised", role.name)
            return {"agent": role.name, "delegated": False, "error": str(exc)}
        finally:
            budget.exit()

        if audit is not None:
            audit.record(
                actor=role.name, action="agent.delegate", resource=role.name,
                metadata={"task": task[:500], "model_used": model_used},
            )
        return {
            "agent": role.name,
            "delegated": True,
            "model_used": model_used,
            "response": answer,
        }

    registry.register(ToolDefinition(
        name=DELEGATE_TOOL_NAME,
        description=(
            "Hands a task to another agent, which answers using its own model and its own "
            "tools, and returns that agent's reply. Use this instead of guessing at a "
            "specialist's domain: delegate engineering work to engineering, research to "
            "research, and so on. The delegate does not inherit your permissions. You cannot "
            "delegate to yourself (the coordinator)."
        ),
        input_schema={"type": "object", "properties": {
            "agent": {"type": "string", "description": "Agent key or name, e.g. 'engineering'."},
            "task": {"type": "string", "description": "What that agent should do."},
            "context": {"type": "string", "description": "Anything it needs that it cannot see."},
        }, "required": ["agent", "task"]},
        output_schema={"type": "object"},
        handler=delegate_to_agent,
        # No scope of its own: delegation grants nothing. Whatever the
        # delegate does is checked against the delegate's own scopes when
        # it calls its own tools.
        required_permissions=[],
        risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))

    def list_agents():
        return {"agents": catalog.list()}

    registry.register(ToolDefinition(
        name="list_agents",
        description="Lists every agent available to delegate to, with the models and tools each has.",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object"},
        handler=list_agents,
        required_permissions=[PermissionScope.DATABASE_READ],
        risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))

    return budget
