from core.projects.base import ProjectStore
from core.storage.memory import InMemoryStorage


def test_create_and_get():
    store = ProjectStore(InMemoryStorage())
    project = store.create("iV Phase 1", description="build the core", priority=8)
    fetched = store.get(project.id)
    assert fetched.name == "iV Phase 1"
    assert fetched.priority == 8
    assert fetched.status == "active"


def test_update_status():
    store = ProjectStore(InMemoryStorage())
    project = store.create("iV Phase 1")
    updated = store.update_status(project.id, "archived")
    assert updated.status == "archived"


def test_update_status_missing_returns_none():
    store = ProjectStore(InMemoryStorage())
    assert store.update_status("does-not-exist", "archived") is None


def test_list_filters_by_status():
    store = ProjectStore(InMemoryStorage())
    store.create("active one", status="active")
    store.create("archived one", status="archived")

    active = store.list(status="active")
    assert [p.name for p in active] == ["active one"]
