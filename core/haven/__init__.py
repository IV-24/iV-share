"""The Haven: the name for the boundary iV already operates inside —
core/permissions (what's granted), core/tools (what's registered and
under what risk/execution policy), core/environment (what machine/runtime
iV is running on). Nothing here is a new capability; core/haven only
aggregates what those modules already track into one inspectable
snapshot, read-only, so "what can iV actually do right now" has one
answer instead of three places to check. See docs/HAVEN.md."""

from core.haven.manifest import HavenManifest, build_haven_manifest

__all__ = ["HavenManifest", "build_haven_manifest"]
