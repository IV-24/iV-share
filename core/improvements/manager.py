"""Improvement proposals are just records until deploy() — and deploy()
refuses to flip status to DEPLOYED unless the linked ApprovalRequest is
actually approved. This is the code-level enforcement of "iV must never
silently modify production/main code," not just a documented rule."""

from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.improvements.base import ImprovementProposal, ImprovementStatus
from core.storage.base import StorageBackend

COLLECTION = "improvements"


class ImprovementManager:
    def __init__(self, storage: StorageBackend, approvals: ApprovalManager, audit: AuditLog | None = None) -> None:
        self._storage = storage
        self._approvals = approvals
        self._audit = audit

    def propose(
        self,
        *,
        agent_name: str,
        problem_identified: str,
        proposed_change: str,
        affected_files: list[str] | None = None,
        risk_level: str = "high",
    ) -> ImprovementProposal:
        approval = self._approvals.request(
            action_type="self_improvement_deploy",
            description=f"{agent_name}: {proposed_change}",
            requested_by=agent_name,
            risk_level=risk_level,
        )
        stored = self._storage.insert(
            COLLECTION,
            {
                "agent_name": agent_name,
                "problem_identified": problem_identified,
                "proposed_change": proposed_change,
                "affected_files": affected_files or [],
                "status": ImprovementStatus.PROPOSED.value,
                "approval_id": approval.id,
            },
        )
        return ImprovementProposal.from_record(stored)

    def mark_testing(self, proposal_id: str) -> ImprovementProposal:
        return self._set_status(proposal_id, ImprovementStatus.TESTING)

    def deploy(self, proposal_id: str) -> ImprovementProposal:
        proposal = self._require(proposal_id)
        if proposal.approval_id is None or not self._approvals.is_approved(proposal.approval_id):
            raise PermissionError(
                f"improvement '{proposal_id}' has no approved authorization — refusing to deploy"
            )
        updated = self._set_status(proposal_id, ImprovementStatus.DEPLOYED)
        self._approvals.mark_executed(proposal.approval_id)
        if self._audit:
            self._audit.record(actor=proposal.agent_name, action="improvement.deploy", resource=proposal_id)
        return updated

    def reject(self, proposal_id: str) -> ImprovementProposal:
        return self._set_status(proposal_id, ImprovementStatus.REJECTED)

    def get(self, proposal_id: str) -> ImprovementProposal | None:
        record = self._storage.get(COLLECTION, proposal_id)
        return ImprovementProposal.from_record(record) if record else None

    def _require(self, proposal_id: str) -> ImprovementProposal:
        proposal = self.get(proposal_id)
        if proposal is None:
            raise KeyError(f"no improvement proposal with id '{proposal_id}'")
        return proposal

    def _set_status(self, proposal_id: str, status: ImprovementStatus) -> ImprovementProposal:
        stored = self._storage.update(COLLECTION, proposal_id, {"status": status.value})
        if stored is None:
            raise KeyError(f"no improvement proposal with id '{proposal_id}'")
        return ImprovementProposal.from_record(stored)
