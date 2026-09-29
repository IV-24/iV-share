"""Builds a HavenManifest: a read-only snapshot of the boundary iV is
currently operating inside. Assembling one has no side effects and
grants nothing — it only reports state that core/environment,
core/tools, and core/permissions already track separately.
"""

from dataclasses import dataclass, field
from typing import Any

from core.environment.discovery import EnvironmentManifest, discover
from core.permissions.manager import PermissionManager
from core.tools.registry import ToolRegistry


@dataclass
class HavenManifest:
    environment: EnvironmentManifest
    tools: list[dict[str, Any]] = field(default_factory=list)
    grants: list[dict[str, Any]] = field(default_factory=list)


def build_haven_manifest(
    *, tools: ToolRegistry, permissions: PermissionManager, working_directory: str | None = None
) -> HavenManifest:
    grants = permissions.list_grants()
    return HavenManifest(
        environment=discover(working_directory=working_directory),
        tools=tools.list_tools(),
        grants=[
            {"principal": g.principal, "scope": g.scope, "granted_by": g.granted_by, "granted_at": g.granted_at}
            for g in grants
        ],
    )
