from datetime import datetime, timedelta, timezone

from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.conversations.base import ConversationStore
from core.memory.base import MemoryStore, MemoryType
from core.models.base import ModelInfo, ModelProvider, ModelRequest, ModelResponse, ModelUnavailableError
from core.models.registry import ModelRegistry
from core.reflection.base import build_day_log_text, parse_reflection, run_reflection_cycle
from core.storage.memory import InMemoryStorage


class ScriptedReflectionProvider(ModelProvider):
    name = "scripted"

    def __init__(self, text: str) -> None:
        self._text = text

    def list_models(self) -> list[ModelInfo]:
        return []

    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text=self._text, model_name="scripted-model", provider=self.name)


class AlwaysUnavailableProvider(ModelProvider):
    name = "flaky"

    def list_models(self):
        return []

    def generate(self, request):
        raise ModelUnavailableError("simulated outage")


def make_cycle_deps():
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    approvals = ApprovalManager(storage, audit)
    memory = MemoryStore(storage)
    conversations = ConversationStore(storage)
    return storage, audit, approvals, memory, conversations


def test_parse_reflection_handles_plain_json():
    parsed = parse_reflection('{"narrative": "a quiet day", "action_items": []}')
    assert parsed["narrative"] == "a quiet day"


def test_parse_reflection_strips_markdown_fences():
    parsed = parse_reflection('```json\n{"narrative": "fenced", "action_items": []}\n```')
    assert parsed["narrative"] == "fenced"


def test_parse_reflection_falls_back_to_manual_review_on_invalid_json():
    parsed = parse_reflection("not json at all")
    assert parsed["narrative"] == "not json at all"
    assert parsed["action_items"][0]["title"] == "Review raw Sleep Cycle output manually"


def test_build_day_log_text_notes_empty_day():
    text = build_day_log_text([], [])
    assert "No activity recorded today" in text


def test_run_reflection_cycle_writes_memory_and_opens_approvals():
    storage, audit, approvals, memory, conversations = make_cycle_deps()
    conversation = conversations.create()
    conversations.add_message(conversation.id, "user", "hello")

    models = ModelRegistry()
    models.register(ScriptedReflectionProvider(
        '{"narrative": "iV had a productive day.", '
        '"action_items": [{"title": "Add retries", "description": "Handle 503s better"}]}'
    ))

    result = run_reflection_cycle(
        conversations=conversations, audit=audit, memory=memory, approvals=approvals,
        models=models, provider_order=["scripted"],
    )

    assert result.narrative == "iV had a productive day."
    assert len(result.action_items) == 1

    stored_memories = memory.query(memory_type=MemoryType.REFLECTIVE)
    assert stored_memories[0].content == "iV had a productive day."

    approval = approvals.get(result.action_items[0]["id"])
    assert approval.action_type == "Add retries"
    assert approval.status.value == "requested"


def test_run_reflection_cycle_only_reviews_messages_since_cutoff():
    storage, audit, approvals, memory, conversations = make_cycle_deps()
    conversation = conversations.create()

    # Force one message to look like it happened yesterday.
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    storage.insert("messages", {
        "conversation_id": conversation.id, "role": "user", "content": "yesterday's message",
        "model_used": None, "created_at": yesterday,
    })
    conversations.add_message(conversation.id, "user", "today's message")

    models = ModelRegistry()
    provider = ScriptedReflectionProvider('{"narrative": "reviewed", "action_items": []}')
    models.register(provider)

    captured = {}
    original_generate = provider.generate

    def capturing_generate(request):
        captured["day_log"] = request.messages[0].content
        return original_generate(request)

    provider.generate = capturing_generate

    run_reflection_cycle(
        conversations=conversations, audit=audit, memory=memory, approvals=approvals,
        models=models, provider_order=["scripted"],
    )

    assert "today's message" in captured["day_log"]
    assert "yesterday's message" not in captured["day_log"]


def test_run_reflection_cycle_raises_when_every_provider_unavailable():
    import pytest
    storage, audit, approvals, memory, conversations = make_cycle_deps()

    models = ModelRegistry()
    models.register(AlwaysUnavailableProvider())

    with pytest.raises(ModelUnavailableError):
        run_reflection_cycle(
            conversations=conversations, audit=audit, memory=memory, approvals=approvals,
            models=models, provider_order=["flaky"],
        )
