"""The current run's identifier, carried on a contextvar.

Deliberately dependency-free. core/audit imports this to stamp every event
with the run it belongs to, and an audit log that had to import a storage-
backed recorder to do that would be a cycle.

Why a contextvar and not a parameter threaded through every call: an audit
event is written from core/tools, core/approvals, core/permissions and
core/agent, several layers below the API route that knows where a turn
begins. Passing a run id down all of those means every future call site has
to remember to pass it, and the ones that forget produce exactly the
uncorrelated rows this exists to eliminate.

Why a contextvar and not threading.local: core/agent/orchestrator.py runs a
turn's delegate_to_agent calls concurrently on a thread pool, and it copies
the calling context into each worker (contextvars.copy_context()) so the
delegation budget is shared. A run id set here rides that same copy, so a
tool executed by a delegate on a pool thread records the run that caused
it, with nothing further to wire up. That property is the reason this file
is here rather than in core/runs/base.py.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_current_run_id: ContextVar[str | None] = ContextVar("iv_current_run_id", default=None)


def current_run_id() -> str | None:
    """The run this code is executing inside, or None when there isn't one
    (a direct store read, a CLI utility, a test calling core directly)."""
    return _current_run_id.get()


def set_current_run_id(run_id: str | None) -> Token:
    return _current_run_id.set(run_id)


def reset_current_run_id(token: Token) -> None:
    _current_run_id.reset(token)
