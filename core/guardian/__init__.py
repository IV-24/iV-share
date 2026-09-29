"""The Guardian: not a worker role, a watcher. Its job is noticing when
another principal's recent activity looks like it's drifting outside its
declared boundary, and having a fast way to stop it — not accomplishing
tasks itself. Every function here is a plain read/write over
core.audit and core.permissions; there is no special-case bypass of
either. See docs/HAVEN.md."""

from core.guardian.base import AnomalyFlag, find_denied_action_spikes, suspend_principal

__all__ = ["AnomalyFlag", "find_denied_action_spikes", "suspend_principal"]
