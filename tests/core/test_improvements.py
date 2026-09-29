"""These tests assert the constitutional rule 'iV must never silently
modify production/main code' is enforced in code, not just documented."""

import pytest

from core.approvals.manager import ApprovalManager
from core.improvements.base import ImprovementStatus
from core.improvements.manager import ImprovementManager
from core.storage.memory import InMemoryStorage


def make_managers():
    storage = InMemoryStorage()
    approvals = ApprovalManager(storage)
    improvements = ImprovementManager(storage, approvals)
    return improvements, approvals


def test_propose_creates_a_linked_pending_approval():
    improvements, approvals = make_managers()
    proposal = improvements.propose(
        agent_name="Engineering Lord",
        problem_identified="No retry on transient 503s",
        proposed_change="Add exponential backoff to providers.py",
    )
    assert proposal.status == ImprovementStatus.PROPOSED
    assert approvals.get(proposal.approval_id).status.value == "requested"


def test_deploy_without_approval_is_refused():
    improvements, _ = make_managers()
    proposal = improvements.propose(
        agent_name="Engineering Lord",
        problem_identified="x",
        proposed_change="y",
    )
    with pytest.raises(PermissionError):
        improvements.deploy(proposal.id)
    assert improvements.get(proposal.id).status == ImprovementStatus.PROPOSED


def test_deploy_succeeds_once_approved():
    improvements, approvals = make_managers()
    proposal = improvements.propose(
        agent_name="Engineering Lord",
        problem_identified="x",
        proposed_change="y",
    )
    approvals.decide(proposal.approval_id, approved=True, decided_by="owner")

    deployed = improvements.deploy(proposal.id)

    assert deployed.status == ImprovementStatus.DEPLOYED
    assert approvals.get(proposal.approval_id).status.value == "executed"


def test_deploy_refused_if_approval_was_denied():
    improvements, approvals = make_managers()
    proposal = improvements.propose(
        agent_name="Engineering Lord",
        problem_identified="x",
        proposed_change="y",
    )
    approvals.decide(proposal.approval_id, approved=False, decided_by="owner")

    with pytest.raises(PermissionError):
        improvements.deploy(proposal.id)
