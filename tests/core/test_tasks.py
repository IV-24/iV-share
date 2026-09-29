from core.projects.base import ProjectStore
from core.storage.memory import InMemoryStorage
from core.tasks.base import TaskStore


def test_create_and_get():
    storage = InMemoryStorage()
    projects = ProjectStore(storage)
    tasks = TaskStore(storage)

    project = projects.create("iV Phase 1")
    task = tasks.create(project.id, "Write core interfaces", assigned_role="Engineering Specialist")

    fetched = tasks.get(task.id)
    assert fetched.title == "Write core interfaces"
    assert fetched.project_id == project.id
    assert fetched.assigned_role == "Engineering Specialist"
    assert fetched.status == "pending"


def test_update_status():
    storage = InMemoryStorage()
    tasks = TaskStore(storage)
    task = tasks.create("project-1", "Do the thing")
    updated = tasks.update_status(task.id, "done")
    assert updated.status == "done"


def test_list_for_project_scoped():
    storage = InMemoryStorage()
    tasks = TaskStore(storage)
    tasks.create("project-1", "task a")
    tasks.create("project-2", "task b")

    project_1_tasks = tasks.list_for_project("project-1")
    assert [t.title for t in project_1_tasks] == ["task a"]
