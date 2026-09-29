"""One contract, run against every StorageBackend implementation.

core/storage/base.py defines the interface that domain modules
(permissions, approvals, memory, audit, ...) are written against, and
nothing verified that its two implementations actually agree. iV's own
audit flagged the module as untested; the useful fix is not a test of the
abstract class but a shared contract both backends must satisfy, so a
future adapter can be added to the parametrize list and inherit the whole
suite.
"""

import pytest

from core.storage.local import SqliteStorage
from core.storage.memory import InMemoryStorage


@pytest.fixture(params=["memory", "sqlite"])
def storage(request, tmp_path):
    backend = (
        InMemoryStorage() if request.param == "memory"
        else SqliteStorage(str(tmp_path / "contract.db"))
    )
    yield backend
    backend.close()


def test_insert_assigns_an_id_and_timestamp(storage):
    stored = storage.insert("things", {"name": "a"})

    assert stored["id"]
    assert stored["created_at"]
    assert stored["name"] == "a"


def test_insert_respects_a_caller_supplied_id(storage):
    stored = storage.insert("things", {"id": "chosen", "name": "a"})

    assert stored["id"] == "chosen"
    assert storage.get("things", "chosen")["name"] == "a"


def test_get_returns_none_for_a_missing_record(storage):
    assert storage.get("things", "nope") is None


def test_collections_are_isolated_from_each_other(storage):
    storage.insert("a", {"id": "same-id", "value": 1})
    storage.insert("b", {"id": "same-id", "value": 2})

    assert storage.get("a", "same-id")["value"] == 1
    assert storage.get("b", "same-id")["value"] == 2


def test_update_merges_fields_and_returns_the_new_record(storage):
    stored = storage.insert("things", {"name": "a", "keep": "yes"})

    updated = storage.update("things", stored["id"], {"name": "b"})

    assert updated["name"] == "b"
    assert updated["keep"] == "yes"


def test_update_returns_none_for_a_missing_record(storage):
    assert storage.update("things", "nope", {"name": "b"}) is None


def test_query_filters_are_anded_together(storage):
    storage.insert("things", {"kind": "x", "state": "open"})
    storage.insert("things", {"kind": "x", "state": "closed"})
    storage.insert("things", {"kind": "y", "state": "open"})

    results = storage.query("things", filters={"kind": "x", "state": "open"})

    assert len(results) == 1
    assert results[0]["kind"] == "x" and results[0]["state"] == "open"


def test_query_orders_and_limits(storage):
    for value in ("b", "c", "a"):
        storage.insert("things", {"name": value})

    ascending = [r["name"] for r in storage.query("things", order_by="name")]
    descending = [r["name"] for r in storage.query("things", order_by="name", descending=True)]

    assert ascending == ["a", "b", "c"]
    assert descending == ["c", "b", "a"]
    assert [r["name"] for r in storage.query("things", order_by="name", limit=2)] == ["a", "b"]


def test_query_on_an_unknown_collection_is_empty_not_an_error(storage):
    assert storage.query("never-written") == []


def test_query_order_by_does_not_crash_when_some_records_lack_the_field(storage):
    """Sorting on a field only some records have used to raise TypeError
    ('<' not supported between NoneType and the field's real type) the
    moment one record had it and another didn't -- a live trap for the
    next collection that isn't perfectly uniform, not merely a
    theoretical one."""
    storage.insert("things", {"name": "no-score"})
    storage.insert("things", {"name": "has-score", "score": 5})
    storage.insert("things", {"name": "also-scored", "score": 1})

    ascending = storage.query("things", order_by="score")

    assert [r["name"] for r in ascending] == ["also-scored", "has-score", "no-score"]


def test_query_filters_treat_a_missing_field_as_matching_none(storage):
    """A filter value of None must match a record missing the field
    entirely, same as dict.get(key) == None would -- this is the one
    filter case SqliteStorage still runs in Python rather than pushing
    into SQL (SQL's `= NULL` never matches, even a genuine null)."""
    storage.insert("things", {"name": "no-status"})
    storage.insert("things", {"name": "has-status", "status": "done"})

    results = storage.query("things", filters={"status": None})

    assert [r["name"] for r in results] == ["no-status"]


def test_delete_reports_whether_the_record_existed(storage):
    stored = storage.insert("things", {"name": "a"})

    assert storage.delete("things", stored["id"]) is True
    assert storage.delete("things", stored["id"]) is False
    assert storage.get("things", stored["id"]) is None


def test_returned_records_are_copies_not_live_references(storage):
    """Mutating what a backend handed back must not silently rewrite what
    is stored — the in-memory backend is the one that could plausibly get
    this wrong, and callers must not have to know which backend they hold."""
    stored = storage.insert("things", {"name": "a"})
    stored["name"] = "mutated"

    assert storage.get("things", stored["id"])["name"] == "a"


def test_sqlite_data_survives_reopening_the_file(tmp_path):
    """The property the whole local-first design rests on, and the one
    difference from InMemoryStorage worth asserting explicitly."""
    path = str(tmp_path / "persist.db")
    first = SqliteStorage(path)
    stored = first.insert("things", {"name": "durable"})
    first.close()

    second = SqliteStorage(path)
    try:
        assert second.get("things", stored["id"])["name"] == "durable"
    finally:
        second.close()
