"""Nightly reflection cycle, per IV_CONSTITUTION.md's Sleep Cycle
section: review a day's activity, propose memories and action items, and
never execute anything consequential on its own — every proposed action
item becomes an ApprovalRequest, same as any other consequential action.
Scheduling (when this runs) and notification (how a human finds out) are
NOT this module's job — see interfaces/api/sleep_cycle.py for the HTTP
trigger + email glue, and adapters/notifications/ for the email adapter
itself. This module only knows about core."""

from core.reflection.base import (
    DEFAULT_BACKLOG_PROJECT_NAME,
    DEFAULT_PROVIDER_ORDER,
    ReflectionResult,
    materialize_approved_action_items,
    run_reflection_cycle,
)

__all__ = [
    "ReflectionResult", "run_reflection_cycle", "DEFAULT_PROVIDER_ORDER",
    "materialize_approved_action_items", "DEFAULT_BACKLOG_PROJECT_NAME",
]
