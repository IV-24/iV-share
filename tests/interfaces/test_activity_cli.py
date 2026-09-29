"""The activity report answers one question: did iV actually do the
things it said it did? Its value depends entirely on not overstating —
so these tests pin the honest cases, especially the empty one, where a
report that looked reassuring would be worse than no report at all.
"""

import pytest

from core.audit.log import AuditLog
from core.projects.base import ProjectStore
from core.storage.memory import InMemoryStorage
from core.tasks.base import TaskStore
from interfaces.cli.activity import build_report

SINCE = "2000-01-01T00:00:00+00:00"


@pytest.fixture
def storage():
    return InMemoryStorage()


def test_an_empty_log_says_plainly_that_nothing_happened(storage):
    report = build_report(storage, since_iso=SINCE, limit=10)

    assert "has not executed a single tool" in report
    assert "was text, not action" in report


def test_tool_executions_are_counted_by_outcome(storage):
    audit = AuditLog(storage)
    audit.record(actor="Coordinator", action="tool.execute:create_task",
                 resource="create_task", outcome="success")
    audit.record(actor="Coordinator", action="tool.execute:create_task",
                 resource="create_task", outcome="success")
    audit.record(actor="Coordinator", action="tool.execute:git_push",
                 resource="git_push", outcome="denied", metadata={"reason": "approval_required"})

    report = build_report(storage, since_iso=SINCE, limit=10)

    assert "2x  create_task" in report
    assert "1x  git_push" in report
    assert "approval_required" in report


def test_a_task_left_in_its_creation_status_is_called_out(storage):
    """The heart of it: iV can create a task already marked in_progress
    without doing anything. Only update_task_status moves one afterwards,
    and that call is audited — so its absence is the evidence."""
    projects, tasks = ProjectStore(storage), TaskStore(storage)
    project = projects.create("Launch")
    tasks.create(project.id, "Looks busy", status="in_progress")

    report = build_report(storage, since_iso=SINCE, limit=10)

    assert "Task status changes actually performed: 0" in report
    assert "No task status was ever updated" in report


def test_real_status_updates_are_reported_as_such(storage):
    projects, tasks = ProjectStore(storage), TaskStore(storage)
    project = projects.create("Launch")
    tasks.create(project.id, "Real work", status="pending")
    AuditLog(storage).record(actor="Coordinator", action="tool.execute:update_task_status",
                             resource="update_task_status", outcome="success")

    report = build_report(storage, since_iso=SINCE, limit=10)

    assert "Task status changes actually performed: 1" in report
    assert "No task status was ever updated" not in report


def test_the_detailed_list_shows_the_newest_events_first(storage):
    audit = AuditLog(storage)
    for name in ("first_tool", "second_tool", "third_tool"):
        audit.record(actor="Coordinator", action=f"tool.execute:{name}",
                     resource=name, outcome="success")

    report = build_report(storage, since_iso=SINCE, limit=2)

    detail = report.split("newest first:")[1]
    assert "third_tool" in detail
    assert "first_tool" not in detail, "a 'most recent' list must not show the oldest entries"


def test_the_report_states_that_no_background_execution_exists(storage):
    """The claim this whole command exists to check. If background
    execution is ever added, this assertion should fail and force the
    wording to be corrected rather than silently becoming a lie."""
    report = build_report(storage, since_iso=SINCE, limit=10)

    assert "no background execution" in report


def test_model_usage_is_attributed_per_turn(storage):
    audit = AuditLog(storage)
    audit.record(actor="Coordinator", action="chat.turn", resource="c1",
                 metadata={"model_used": "groq:llama-3.3-70b-versatile"})
    audit.record(actor="Coordinator", action="chat.turn", resource="c1",
                 metadata={"model_used": None})

    report = build_report(storage, since_iso=SINCE, limit=10)

    assert "2 message(s) answered by a model" in report
    assert "groq:llama-3.3-70b-versatile" in report
    assert "(none reached)" in report
