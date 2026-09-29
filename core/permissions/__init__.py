"""Least-privilege permission scopes: requested, explicitly granted,
inspectable, revocable, auditable. The model never gets a capability
merely because the underlying process technically has it."""

from core.permissions.levels import AGENCY_LEVELS, AgencyLevel, apply_level_change, current_level, request_level_up
from core.permissions.manager import PermissionManager
from core.permissions.scopes import ALL_SCOPES, PermissionScope

__all__ = [
    "PermissionManager", "PermissionScope", "ALL_SCOPES",
    "AgencyLevel", "AGENCY_LEVELS", "current_level", "request_level_up", "apply_level_change",
]
