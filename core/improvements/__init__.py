"""Self-improvement lifecycle: observe -> propose -> isolated change ->
test -> approval -> deploy. deploy() is hard-gated on an approved
ApprovalRequest — iV cannot silently modify its own core systems."""

from core.improvements.base import ImprovementProposal, ImprovementStatus
from core.improvements.manager import ImprovementManager

__all__ = ["ImprovementProposal", "ImprovementStatus", "ImprovementManager"]
