from unittest.mock import MagicMock, patch

from adapters.notifications.email import EmailConfig
from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.agent.orchestrator import AgentOrchestrator
from core.context.manager import ContextManager
from core.conversations.base import ConversationStore
from core.games.chess import ChessGameStore
from core.improvements.manager import ImprovementManager
from core.memory.base import MemoryStore
from core.models.base import ModelInfo, ModelProvider, ModelRequest, ModelResponse, ModelUnavailableError
from core.models.registry import ModelRegistry
from core.permissions.manager import PermissionManager
from core.projects.base import ProjectStore
from core.runs.base import RunRecorder
from core.storage.memory import InMemoryStorage
from core.tasks.base import TaskStore
from core.tools.registry import ToolRegistry
from interfaces.api.runtime import ApiRuntime
from interfaces.api.sleep_cycle import run_sleep_cycle


class ScriptedProvider(ModelProvider):
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


def make_runtime(provider: ModelProvider) -> ApiRuntime:
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    permissions = PermissionManager(storage, audit)
    approvals = ApprovalManager(storage, audit)
    tools = ToolRegistry(permissions, approvals, audit)
    memory = MemoryStore(storage)
    conversations = ConversationStore(storage)
    projects = ProjectStore(storage)
    tasks = TaskStore(storage)
    improvements = ImprovementManager(storage, approvals, audit)
    games = ChessGameStore(storage)
    models = ModelRegistry()
    models.register(provider)
    orchestrator = AgentOrchestrator(models, tools, memory, ContextManager())
    return ApiRuntime(
        runs=RunRecorder(storage),
        storage=storage, orchestrator=orchestrator, conversations=conversations,
        approvals=approvals, permissions=permissions, audit=audit, memory=memory, models=models,
        tools=tools, projects=projects, tasks=tasks, improvements=improvements, games=games,
    )


def test_run_sleep_cycle_success_sends_email_when_configured():
    runtime = make_runtime(ScriptedProvider(
        '{"narrative": "quiet day", "action_items": [{"title": "x", "description": "y"}]}'
    ))
    email_config = EmailConfig(address="iv@example.com", app_password="secret", notify_to="owner@example.com")

    with patch("interfaces.api.sleep_cycle.send_email") as fake_send:
        result = run_sleep_cycle(
            runtime, provider_order=["scripted"], email_config=email_config,
            dashboard_base_url="http://localhost:8000", api_access_secret="s3cr3t",
        )

    assert result == {"status": "ok", "action_items": 1}
    fake_send.assert_called_once()
    assert "owner@example.com" == email_config.notify_to  # sanity on fixture
    sent_html = fake_send.call_args.kwargs["html_body"]
    assert "quiet day" in sent_html
    assert "http://localhost:8000/approvals/pending?secret=s3cr3t" in sent_html


def test_run_sleep_cycle_skips_email_when_not_configured():
    runtime = make_runtime(ScriptedProvider('{"narrative": "quiet day", "action_items": []}'))

    with patch("interfaces.api.sleep_cycle.send_email") as fake_send:
        result = run_sleep_cycle(
            runtime, provider_order=["scripted"], email_config=EmailConfig(),
            dashboard_base_url="http://localhost:8000", api_access_secret=None,
        )

    assert result == {"status": "ok", "action_items": 0}
    fake_send.assert_not_called()


def test_run_sleep_cycle_sends_failure_email_when_every_provider_fails():
    runtime = make_runtime(AlwaysUnavailableProvider())
    email_config = EmailConfig(address="iv@example.com", app_password="secret", notify_to="owner@example.com")

    with patch("interfaces.api.sleep_cycle.send_email") as fake_send:
        result = run_sleep_cycle(
            runtime, provider_order=["flaky"], email_config=email_config,
            dashboard_base_url="http://localhost:8000", api_access_secret=None,
        )

    assert result["status"] == "error"
    fake_send.assert_called_once()
    assert fake_send.call_args.kwargs["subject"] == "iV Sleep Cycle — FAILED"
