"""Same contract test suite runs against every StorageBackend
implementation — proves core's storage interface is genuinely
implementation-agnostic, not just true for one backend by accident."""

import os

import pytest

from core.storage.local import SqliteStorage
from core.storage.memory import InMemoryStorage


@pytest.fixture(params=["memory", "sqlite"])
def storage(request, tmp_path):
    if request.param == "memory":
        yield InMemoryStorage()
    else:
        db_path = str(tmp_path / "test.db")
        backend = SqliteStorage(db_path)
        yield backend
        backend.close()
        if os.path.exists(db_path):
            os.remove(db_path)


def test_insert_assigns_id_and_created_at(storage):
    record = storage.insert("widgets", {"name": "sprocket"})
    assert record["id"]
    assert record["created_at"]
    assert record["name"] == "sprocket"


def test_get_roundtrip(storage):
    inserted = storage.insert("widgets", {"name": "sprocket"})
    fetched = storage.get("widgets", inserted["id"])
    assert fetched == inserted


def test_get_missing_returns_none(storage):
    assert storage.get("widgets", "does-not-exist") is None


def test_update_merges_fields(storage):
    inserted = storage.insert("widgets", {"name": "sprocket", "count": 1})
    updated = storage.update("widgets", inserted["id"], {"count": 2})
    assert updated["count"] == 2
    assert updated["name"] == "sprocket"


def test_update_missing_returns_none(storage):
    assert storage.update("widgets", "does-not-exist", {"count": 2}) is None


def test_query_filters_by_equality(storage):
    storage.insert("widgets", {"name": "a", "kind": "x"})
    storage.insert("widgets", {"name": "b", "kind": "y"})
    storage.insert("widgets", {"name": "c", "kind": "x"})

    results = storage.query("widgets", filters={"kind": "x"})
    assert {r["name"] for r in results} == {"a", "c"}


def test_query_limit(storage):
    for i in range(5):
        storage.insert("widgets", {"name": f"w{i}"})
    assert len(storage.query("widgets", limit=2)) == 2


def test_delete(storage):
    inserted = storage.insert("widgets", {"name": "sprocket"})
    assert storage.delete("widgets", inserted["id"]) is True
    assert storage.get("widgets", inserted["id"]) is None
    assert storage.delete("widgets", inserted["id"]) is False


def test_close_is_always_safe_to_call(storage):
    """Every backend must support close() uniformly, even ones with
    nothing to release (InMemoryStorage) — a caller managing a
    StorageBackend's lifecycle should never need to know which concrete
    backend it holds."""
    storage.close()


def test_sqlite_storage_usable_from_a_different_thread(tmp_path):
    """Regression test: a sync FastAPI endpoint runs in a worker thread
    different from whichever thread opened the connection during app
    startup. A plain sqlite3.connect() without check_same_thread=False
    (and a lock around access) raises ProgrammingError the moment a
    request thread touches it — this reproduces that scenario directly."""
    import threading

    db_path = str(tmp_path / "cross_thread.db")
    storage = SqliteStorage(db_path)
    try:
        errors = []

        def insert_from_worker_thread():
            try:
                storage.insert("widgets", {"name": "from-worker-thread"})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=insert_from_worker_thread)
        thread.start()
        thread.join()

        assert errors == []
        assert storage.query("widgets", filters={"name": "from-worker-thread"})
    finally:
        storage.close()
