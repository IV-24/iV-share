from dataclasses import dataclass, field
from typing import Any

from core.tools.base import RiskLevel

REQUIRED_FIELDS = ("id", "name", "version", "entry_point")


@dataclass
class ExtensionManifest:
    id: str
    name: str
    version: str
    entry_point: str
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.MEDIUM

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExtensionManifest":
        missing = [f for f in REQUIRED_FIELDS if not data.get(f)]
        if missing:
            raise ValueError(f"extension manifest missing required field(s): {', '.join(missing)}")

        risk_raw = data.get("risk_level", RiskLevel.MEDIUM.value)
        try:
            risk_level = RiskLevel(risk_raw)
        except ValueError as exc:
            raise ValueError(f"invalid risk_level '{risk_raw}'") from exc

        return cls(
            id=data["id"],
            name=data["name"],
            version=data["version"],
            entry_point=data["entry_point"],
            description=data.get("description", ""),
            capabilities=list(data.get("capabilities", [])),
            permissions=list(data.get("permissions", [])),
            dependencies=list(data.get("dependencies", [])),
            risk_level=risk_level,
        )
