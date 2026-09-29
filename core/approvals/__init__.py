"""Approval lifecycle for consequential actions: requested, approved,
denied, revoked, expired, executed, failed. This is a real gate other
modules (ToolRegistry, ImprovementManager) check before acting — not a
database row nobody reads."""

from core.approvals.base import ApprovalRequest, ApprovalStatus
from core.approvals.manager import ApprovalManager

__all__ = ["ApprovalRequest", "ApprovalStatus", "ApprovalManager"]
