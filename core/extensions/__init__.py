"""Extension architecture (manifest + registry), not a marketplace.
Extensions are treated as potentially untrusted: registering one only
validates and records its manifest — it never loads or executes code."""

from core.extensions.manifest import ExtensionManifest
from core.extensions.registry import ExtensionRegistry

__all__ = ["ExtensionManifest", "ExtensionRegistry"]
