from core.memory.base import MemoryStore, MemoryType
from core.storage.memory import InMemoryStorage


def test_add_and_get():
    store = MemoryStore(InMemoryStorage())
    memory = store.add("iV learned X", memory_type=MemoryType.REFLECTIVE, importance=8)
    fetched = store.get(memory.id)
    assert fetched.content == "iV learned X"
    assert fetched.memory_type == MemoryType.REFLECTIVE


def test_query_filters_by_type():
    store = MemoryStore(InMemoryStorage())
    store.add("episode one", memory_type=MemoryType.EPISODIC)
    store.add("fact one", memory_type=MemoryType.SEMANTIC)

    episodic = store.query(memory_type=MemoryType.EPISODIC)
    assert len(episodic) == 1
    assert episodic[0].content == "episode one"


def test_query_filters_by_importance():
    store = MemoryStore(InMemoryStorage())
    store.add("minor note", importance=2)
    store.add("major insight", importance=9)

    important = store.query(min_importance=5)
    assert [m.content for m in important] == ["major insight"]


def test_query_respects_limit():
    store = MemoryStore(InMemoryStorage())
    for i in range(5):
        store.add(f"memory {i}")
    assert len(store.query(limit=3)) == 3
