"""Read-only environment discovery. Produces a reviewable manifest —
never modifies, installs, deletes, executes, or grants anything."""

from core.environment.discovery import EnvironmentManifest, discover

__all__ = ["EnvironmentManifest", "discover"]
