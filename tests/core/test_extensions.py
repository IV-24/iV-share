import pytest

from core.extensions.manifest import ExtensionManifest
from core.extensions.registry import ExtensionRegistry
from core.tools.base import RiskLevel


def test_manifest_requires_core_fields():
    with pytest.raises(ValueError):
        ExtensionManifest.from_dict({"name": "missing id and entry point"})


def test_manifest_parses_valid_dict():
    manifest = ExtensionManifest.from_dict({
        "id": "weather-tool",
        "name": "Weather Tool",
        "version": "0.1.0",
        "entry_point": "weather_tool.main:register",
        "permissions": ["network.request"],
        "risk_level": "medium",
    })
    assert manifest.id == "weather-tool"
    assert manifest.risk_level == RiskLevel.MEDIUM
    assert manifest.permissions == ["network.request"]


def test_manifest_rejects_invalid_risk_level():
    with pytest.raises(ValueError):
        ExtensionManifest.from_dict({
            "id": "x", "name": "x", "version": "0.1.0", "entry_point": "x:main",
            "risk_level": "extremely-dangerous",
        })


def test_registry_register_and_list():
    registry = ExtensionRegistry()
    manifest = ExtensionManifest.from_dict({
        "id": "weather-tool", "name": "Weather Tool", "version": "0.1.0", "entry_point": "weather:main",
    })
    registry.register(manifest)

    assert registry.get("weather-tool") is manifest
    assert registry.list_extensions() == [manifest]


def test_registry_rejects_duplicate_id():
    registry = ExtensionRegistry()
    manifest = ExtensionManifest.from_dict({
        "id": "weather-tool", "name": "Weather Tool", "version": "0.1.0", "entry_point": "weather:main",
    })
    registry.register(manifest)
    with pytest.raises(ValueError):
        registry.register(manifest)
