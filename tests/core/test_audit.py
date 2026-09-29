from core.audit.log import AuditLog
from core.storage.memory import InMemoryStorage


def test_record_and_for_actor():
    audit = AuditLog(InMemoryStorage())
    audit.record(actor="coordinator", action="tool.execute:create_task", resource="task-1")

    events = audit.for_actor("coordinator")
    assert events[0].action == "tool.execute:create_task"


def test_since_excludes_earlier_events():
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    storage.insert("audit_log", {
        "timestamp": "2020-01-01T00:00:00+00:00", "actor": "x", "action": "old",
        "resource": "r", "outcome": "success", "metadata": {},
    })
    audit.record(actor="x", action="new", resource="r")

    recent = audit.since("2025-01-01T00:00:00+00:00")
    assert [e.action for e in recent] == ["new"]
