from core.conversations.base import ConversationStore, derive_title
from core.storage.memory import InMemoryStorage


def test_create_and_get():
    store = ConversationStore(InMemoryStorage())
    conversation = store.create("first chat")
    assert store.get(conversation.id).title == "first chat"


def test_default_title():
    store = ConversationStore(InMemoryStorage())
    conversation = store.create()
    assert conversation.title == "New conversation"


def test_add_message_and_history_ordered():
    store = ConversationStore(InMemoryStorage())
    conversation = store.create()
    store.add_message(conversation.id, "user", "hello")
    store.add_message(conversation.id, "iv", "hi there", model_used="groq:llama-3.3-70b")

    history = store.history(conversation.id)
    assert [m.role for m in history] == ["user", "iv"]
    assert history[1].model_used == "groq:llama-3.3-70b"


def test_history_scoped_to_conversation():
    store = ConversationStore(InMemoryStorage())
    a = store.create()
    b = store.create()
    store.add_message(a.id, "user", "in a")
    store.add_message(b.id, "user", "in b")

    assert [m.content for m in store.history(a.id)] == ["in a"]


def test_list_recent():
    store = ConversationStore(InMemoryStorage())
    for i in range(3):
        store.create(f"chat {i}")
    assert len(store.list_recent(limit=2)) == 2


def test_messages_since_spans_all_conversations():
    storage = InMemoryStorage()
    store = ConversationStore(storage)
    a = store.create()
    b = store.create()
    store.add_message(a.id, "user", "in a")
    store.add_message(b.id, "user", "in b")

    since = "2000-01-01T00:00:00+00:00"
    contents = {m.content for m in store.messages_since(since)}
    assert contents == {"in a", "in b"}


def test_messages_since_excludes_earlier_messages():
    storage = InMemoryStorage()
    store = ConversationStore(storage)
    conversation = store.create()
    storage.insert("messages", {
        "conversation_id": conversation.id, "role": "user", "content": "old",
        "model_used": None, "created_at": "2020-01-01T00:00:00+00:00",
    })
    store.add_message(conversation.id, "user", "new")

    recent = store.messages_since("2025-01-01T00:00:00+00:00")
    assert [m.content for m in recent] == ["new"]


def test_derive_title_collapses_whitespace_and_caps_length():
    assert derive_title("hello   iV,\n who are you?") == "hello iV, who are you?"

    long_message = "explain " + "x" * 100
    title = derive_title(long_message, max_chars=20)
    assert len(title) == 20
    assert title.endswith("…")


def test_derive_title_falls_back_for_an_empty_message():
    assert derive_title("   ") == "New conversation"
