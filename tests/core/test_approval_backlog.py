from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.projects.base import ProjectStore
from core.reflection.base import DEFAULT_BACKLOG_PROJECT_NAME, materialize_approved_action_items
from core.storage.memory import InMemoryStorage
from core.tasks.base import TaskStore


def make_deps():
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    approvals = ApprovalManager(storage, audit)
    projects = ProjectStore(storage)
    tasks = TaskStore(storage)
    return approvals, projects, tasks


def test_list_approved_only_returns_approved_not_executed():
    approvals, _, _ = make_deps()
    pending = approvals.request(action_type="a", description="a", requested_by="x")
    approved = approvals.request(action_type="b", description="b", requested_by="x")
    approvals.decide(approved.id, approved=True, decided_by="owner")
    executed = approvals.request(action_type="c", description="c", requested_by="x")
    approvals.decide(executed.id, approved=True, decided_by="owner")
    approvals.mark_executed(executed.id)

    ids = {r.id for r in approvals.list_approved()}
    assert ids == {approved.id}


def test_list_approved_filters_by_requested_by():
    approvals, _, _ = make_deps()
    from_cycle = approvals.request(action_type="a", description="a", requested_by="iV Sleep Cycle")
    approvals.decide(from_cycle.id, approved=True, decided_by="owner")
    from_human = approvals.request(action_type="b", description="b", requested_by="finance")
    approvals.decide(from_human.id, approved=True, decided_by="owner")

    ids = {r.id for r in approvals.list_approved(requested_by="iV Sleep Cycle")}
    assert ids == {from_cycle.id}


def test_materialize_creates_tasks_under_backlog_project():
    approvals, projects, tasks = make_deps()
    request = approvals.request(
        action_type="Add retries", description="Handle 503s better", requested_by="iV Sleep Cycle"
    )
    approvals.decide(request.id, approved=True, decided_by="owner")

    created = materialize_approved_action_items(approvals=approvals, projects=projects, tasks=tasks)

    assert len(created) == 1
    assert created[0].title == "Add retries"
    assert created[0].description == "Handle 503s better"

    [backlog] = projects.list()
    assert backlog.name == DEFAULT_BACKLOG_PROJECT_NAME
    assert created[0].project_id == backlog.id


def test_materialize_marks_requests_executed_so_they_are_not_recreated():
    approvals, projects, tasks = make_deps()
    request = approvals.request(action_type="x", description="y", requested_by="iV Sleep Cycle")
    approvals.decide(request.id, approved=True, decided_by="owner")

    first = materialize_approved_action_items(approvals=approvals, projects=projects, tasks=tasks)
    second = materialize_approved_action_items(approvals=approvals, projects=projects, tasks=tasks)

    assert len(first) == 1
    assert len(second) == 0
    assert approvals.get(request.id).status.value == "executed"


def test_materialize_reuses_existing_backlog_project():
    approvals, projects, tasks = make_deps()
    projects.create(DEFAULT_BACKLOG_PROJECT_NAME)
    request = approvals.request(action_type="x", description="y", requested_by="iV Sleep Cycle")
    approvals.decide(request.id, approved=True, decided_by="owner")

    materialize_approved_action_items(approvals=approvals, projects=projects, tasks=tasks)

    assert len(projects.list()) == 1  # no duplicate backlog project created


def test_materialize_ignores_approvals_from_other_requesters():
    approvals, projects, tasks = make_deps()
    request = approvals.request(action_type="x", description="y", requested_by="finance")
    approvals.decide(request.id, approved=True, decided_by="owner")

    created = materialize_approved_action_items(approvals=approvals, projects=projects, tasks=tasks)

    assert created == []
    assert approvals.get(request.id).status.value == "approved"  # untouched
