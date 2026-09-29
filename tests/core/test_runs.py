"""A run is the identifier an execution is addressed by. These tests cover
the two properties that make it worth having: every event raised during a
turn carries it, and two overlapping turns never share one."""

import threading

from core.audit.log import AuditLog
from core.runs.base import RunRecorder
from core.runs.context import current_run_id, reset_current_run_id, set_current_run_id
from core.storage.memory import InMemoryStorage


def _recorder():
    storage = InMemoryStorage()
    return RunRecorder(storage), AuditLog(storage), storage


def test_no_run_id_outside_a_run():
    assert current_run_id() is None


def test_audit_events_are_stamped_with_the_current_run():
    runs, audit, _ = _recorder()
    run = runs.start(goal="do the thing")
    token = set_current_run_id(run.id)
    try:
        audit.record(actor="Coordinator", action="tool.execute:create_task", resource="create_task")
    finally:
        reset_current_run_id(token)

    events = audit.for_run(run.id)
    assert [e.action for e in events] == ["tool.execute:create_task"]
    assert events[0].run_id == run.id


def test_events_outside_a_run_are_not_stamped_and_do_not_leak_into_one():
    runs, audit, _ = _recorder()
    run = runs.start(goal="scoped")
    audit.record(actor="api-bootstrap", action="permission.grant", resource="x:y")

    assert audit.for_run(run.id) == []


def test_two_concurrent_runs_do_not_share_an_id():
    """A sync FastAPI endpoint runs on a pooled worker thread, so two
    requests can be in flight on two threads at once. If they shared this
    state their events would be attributed to each other."""
    runs, audit, _ = _recorder()
    seen: dict[str, str] = {}
    barrier = threading.Barrier(2)

    def turn(goal: str) -> None:
        run = runs.start(goal=goal)
        token = set_current_run_id(run.id)
        try:
            barrier.wait(timeout=5)  # guarantee the two overlap
            audit.record(actor="Coordinator", action=f"tool.execute:{goal}", resource=goal)
            seen[goal] = current_run_id()
        finally:
            reset_current_run_id(token)

    threads = [threading.Thread(target=turn, args=(g,)) for g in ("alpha", "beta")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert seen["alpha"] != seen["beta"]
    assert [e.action for e in audit.for_run(seen["alpha"])] == ["tool.execute:alpha"]
    assert [e.action for e in audit.for_run(seen["beta"])] == ["tool.execute:beta"]


def test_a_run_records_how_it_started_and_ended():
    runs, _, _ = _recorder()
    run = runs.start(goal="ship it", conversation_id="conv-1")
    assert run.status == "running"
    assert run.ended_at is None

    runs.finish(run.id, status="ok", model_used="gemini:flash", completed_tool_calls=["create_task"])

    stored = runs.get(run.id)
    assert stored.status == "ok"
    assert stored.ended_at is not None
    assert stored.model_used == "gemini:flash"
    assert stored.completed_tool_calls == ["create_task"]
    assert stored.conversation_id == "conv-1"


def test_a_nested_run_records_its_parent():
    runs, _, _ = _recorder()
    outer = runs.start(goal="outer")
    token = set_current_run_id(outer.id)
    try:
        inner = runs.start(goal="inner")
    finally:
        reset_current_run_id(token)

    assert inner.parent_run_id == outer.id
    assert outer.parent_run_id is None


def test_find_by_idempotency_key_returns_the_first_run():
    runs, _, _ = _recorder()
    first = runs.start(goal="charge the card", idempotency_key="key-1")
    runs.start(goal="charge the card", idempotency_key="key-1")

    assert runs.find_by_idempotency_key("key-1").id == first.id
    assert runs.find_by_idempotency_key("absent") is None
