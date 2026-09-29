"""Records validated extension manifests for later review. Deliberately
does not load, import, or execute anything — installation/execution is
future work that should go through the same permission+approval gates as
everything else, once it exists.

Not wired into interfaces/api/runtime.build_runtime() yet, and nothing
currently imports this module outside its own test — it exists ahead of
its first real caller because the intent for it is specific: this is the
landing spot for a Gmail / Google Drive / Calendar integration, each
declaring its own ExtensionManifest (capabilities, required permission
scopes, risk_level) the same way adapters/workspace and
adapters/notifications/email.py declare their own tool sets and
credentials today. Building that integration -- OAuth, the actual Gmail/
Drive/Calendar API calls, the ToolDefinitions those calls back into --
is real, separate feature work, not a fix to bundle in here; this module
is the contract that work would register against, not the work itself.
"""

from core.extensions.manifest import ExtensionManifest


class ExtensionRegistry:
    def __init__(self) -> None:
        self._extensions: dict[str, ExtensionManifest] = {}

    def register(self, manifest: ExtensionManifest) -> None:
        if manifest.id in self._extensions:
            raise ValueError(f"extension '{manifest.id}' is already registered")
        self._extensions[manifest.id] = manifest

    def get(self, extension_id: str) -> ExtensionManifest | None:
        return self._extensions.get(extension_id)

    def list_extensions(self) -> list[ExtensionManifest]:
        return list(self._extensions.values())
