import time

from core.approvals.base import ApprovalStatus
from core.approvals.manager import ApprovalManager
from core.storage.memory import InMemoryStorage


def make_manager():
    return ApprovalManager(InMemoryStorage())


def test_new_request_is_pending_and_not_approved():
    approvals = make_manager()
    request = approvals.request(action_type="wire_transfer", description="pay invoice", requested_by="finance")
    assert request.status == ApprovalStatus.REQUESTED
    assert approvals.is_approved(request.id) is False


def test_approve_flips_status():
    approvals = make_manager()
    request = approvals.request(action_type="wire_transfer", description="pay invoice", requested_by="finance")
    approvals.decide(request.id, approved=True, decided_by="owner")
    assert approvals.is_approved(request.id) is True


def test_deny_flips_status_and_stays_unapproved():
    approvals = make_manager()
    request = approvals.request(action_type="wire_transfer", description="pay invoice", requested_by="finance")
    decided = approvals.decide(request.id, approved=False, decided_by="owner")
    assert decided.status == ApprovalStatus.DENIED
    assert approvals.is_approved(request.id) is False


def test_cannot_decide_twice():
    approvals = make_manager()
    request = approvals.request(action_type="wire_transfer", description="pay invoice", requested_by="finance")
    approvals.decide(request.id, approved=True, decided_by="owner")
    try:
        approvals.decide(request.id, approved=True, decided_by="owner")
        assert False, "expected ValueError on re-deciding an already-decided request"
    except ValueError:
        pass


def test_revoke_only_valid_on_approved_request():
    approvals = make_manager()
    request = approvals.request(action_type="wire_transfer", description="pay invoice", requested_by="finance")
    try:
        approvals.revoke(request.id, revoked_by="owner")
        assert False, "expected ValueError revoking a non-approved request"
    except ValueError:
        pass

    approvals.decide(request.id, approved=True, decided_by="owner")
    revoked = approvals.revoke(request.id, revoked_by="owner")
    assert revoked.status == ApprovalStatus.REVOKED
    assert approvals.is_approved(request.id) is False


def test_expired_request_is_not_approved_even_if_never_decided():
    approvals = make_manager()
    request = approvals.request(
        action_type="wire_transfer", description="pay invoice", requested_by="finance",
        expires_in_seconds=0,
    )
    time.sleep(0.01)
    assert approvals.get(request.id).status == ApprovalStatus.EXPIRED
    assert approvals.is_approved(request.id) is False


def test_execute_lifecycle_requires_approval_first():
    approvals = make_manager()
    request = approvals.request(action_type="wire_transfer", description="pay invoice", requested_by="finance")
    try:
        approvals.mark_executed(request.id)
        assert False, "expected ValueError executing an unapproved request"
    except ValueError:
        pass

    approvals.decide(request.id, approved=True, decided_by="owner")
    executed = approvals.mark_executed(request.id)
    assert executed.status == ApprovalStatus.EXECUTED


def test_list_pending_only_shows_requested():
    approvals = make_manager()
    pending = approvals.request(action_type="a", description="a", requested_by="x")
    decided = approvals.request(action_type="b", description="b", requested_by="x")
    approvals.decide(decided.id, approved=True, decided_by="owner")

    ids = {r.id for r in approvals.list_pending()}
    assert ids == {pending.id}
