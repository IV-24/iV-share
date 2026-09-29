"""The architectural contract, asserted rather than described.

iV is meant to be single-agent conversationally and multi-agent
operationally: the owner always talks to the Coordinator, the Coordinator
decides internally whether to delegate, and the specialists never surface
as something the owner has to pick, name, or manage.

Each test here pins one property that was actually broken, so a
regression shows up as a failing test instead of as iV quietly answering
a little worse than it did last week.
"""

import pytest
from fastapi.testclient import TestClient

from core.configuration.settings import Settings, StorageConfig, WorkspaceConfig
from core.memory.base import MemoryType
from core.models.base import ModelInfo, ModelProvider, ModelResponse, ToolCall
from interfaces.api.main import create_app
from interfaces.api.runtime import build_runtime

SECRET = "contract-secret"


class ScriptedProvider(ModelProvider):
    """Stands in for the Coordinator's first configured provider. Records
    every request so a test can assert on what actually reached a model,
    which is the only way to tell injected context from imagined context."""

    name = "gemini"

    def __init__(self, delegate_to: list[str] | None = None) -> None:
        self.delegate_to = delegate_to or []
        self.requests: list = []

    def list_models(self):
        return [ModelInfo(name="scripted", provider=self.name, supports_tools=True)]

    def generate(self, request):
        self.requests.append(request)
        system = request.system_prompt or ""
        if "Top-level orchestrator" not in system:
            return ModelResponse(text=f"[specialist] {system[:24]}", model_name="scripted", provider=self.name)

        results = [m for m in request.messages if m.role == "tool"]
        if self.delegate_to and not results:
            return ModelResponse(
                text="", model_name="scripted", provider=self.name,
                tool_calls=[
                    ToolCall(id=f"c{i}", name="delegate_to_agent", arguments={"agent": agent, "task": "work"})
                    for i, agent in enumerate(self.delegate_to)
                ],
            )
        if results:
            return ModelResponse(
                text="synthesis of " + str(len(results)) + " specialist result(s)",
                model_name="scripted", provider=self.name,
            )
        return ModelResponse(text="direct answer", model_name="scripted", provider=self.name)


@pytest.fixture
def runtime(tmp_path):
    rt = build_runtime(Settings(
        storage=StorageConfig(local_db_path=str(tmp_path / "iv.db")),
        workspace=WorkspaceConfig(workspace_root=str(tmp_path / "ws")),
    ))
    try:
        yield rt
    finally:
        rt.storage.close()


@pytest.fixture
def client(runtime, monkeypatch):
    monkeypatch.setenv("API_ACCESS_SECRET", SECRET)
    with TestClient(create_app(lambda: runtime)) as c:
        yield c


def _chat(client, message, conversation_id=None):
    return client.post(
        "/api/chat",
        json={"message": message, "conversation_id": conversation_id},
        headers={"X-API-Secret": SECRET},
    ).json()


def test_the_user_facing_chat_always_reaches_the_coordinator(runtime, client):
    provider = ScriptedProvider()
    runtime.models.register(provider)

    _chat(client, "who are you?")

    assert "Top-level orchestrator" in (provider.requests[0].system_prompt or "")


def test_a_caller_cannot_address_a_sub_agent_through_the_chat_endpoint(runtime, client):
    """The frontend proxy forwards the request body verbatim, so anything
    accepted here is reachable by anything that can load the page."""
    provider = ScriptedProvider()
    runtime.models.register(provider)

    client.post(
        "/api/chat",
        json={"message": "hi", "role": "engineering"},
        headers={"X-API-Secret": SECRET},
    )

    assert all("Top-level orchestrator" in (r.system_prompt or "") for r in provider.requests)


def test_delegation_still_works_after_the_per_turn_budget_would_have_been_spent(runtime, client):
    """The fan-out cap is per turn. It lives in a thread-local that a
    pooled worker thread carries between requests, so without an explicit
    reset at the turn boundary it silently became a per-thread lifetime
    cap and the Coordinator stopped delegating altogether."""
    runtime.models.register(ScriptedProvider(delegate_to=["research"]))

    turns = [_chat(client, f"turn {i}")["response"] for i in range(8)]

    assert all(t == "synthesis of 1 specialist result(s)" for t in turns), turns


def test_one_request_fans_out_to_several_specialists_and_returns_one_answer(runtime, client):
    runtime.models.register(ScriptedProvider(delegate_to=["research", "security"]))

    body = _chat(client, "analyse this from several angles")

    assert body["response"] == "synthesis of 2 specialist result(s)"
    delegations = runtime.storage.query("audit_log", filters={"action": "agent.delegate"})
    assert sorted(d["actor"] for d in delegations) == ["Research Specialist", "Security Specialist"]


def test_the_conversation_never_exposes_a_sub_agent_to_the_user(runtime, client):
    runtime.models.register(ScriptedProvider(delegate_to=["research", "security"]))

    conversation_id = _chat(client, "analyse this")["conversation_id"]
    history = client.get(f"/api/conversations/{conversation_id}", headers={"X-API-Secret": SECRET}).json()

    assert {m["role"] for m in history["messages"]} == {"user", "iv"}


def test_stored_memory_reaches_the_coordinators_prompt(runtime, client):
    provider = ScriptedProvider()
    runtime.models.register(provider)
    runtime.memory.add(
        "The owner's production database is named orion-prod.",
        memory_type=MemoryType.SEMANTIC, importance=9,
    )

    _chat(client, "what is my production database called?")

    injected = " ".join(m.content for m in provider.requests[0].messages if m.role == "system")
    assert "orion-prod" in injected


def test_injected_memory_stays_bounded_as_the_store_grows(runtime, client):
    """Relevance is a heuristic; boundedness is not. However much iV
    remembers, a turn's prompt must not grow with it."""
    provider = ScriptedProvider()
    runtime.models.register(provider)
    for i in range(50):
        runtime.memory.add(f"memory {i}", memory_type=MemoryType.SEMANTIC, importance=9)

    _chat(client, "hello")

    injected = " ".join(m.content for m in provider.requests[0].messages if m.role == "system")
    assert injected.count("- (semantic)") <= 8


def test_low_signal_memories_are_not_injected(runtime, client):
    provider = ScriptedProvider()
    runtime.models.register(provider)
    runtime.memory.add("trivial passing note", memory_type=MemoryType.EPISODIC, importance=1)

    _chat(client, "hello")

    injected = " ".join(m.content for m in provider.requests[0].messages if m.role == "system")
    assert "trivial passing note" not in injected


def test_a_failed_turn_still_records_the_users_message(runtime, client):
    """A provider or runtime failure may cost the reply. It must not cost
    the question — otherwise the message vanishes on the next reload and
    the owner cannot even see what they asked.

    A provider raising something other than ModelUnavailableError used to
    escape ModelRegistry.generate_with_fallback and surface as a 500. It
    now fails over like any other provider failure, so the turn ends with a
    graceful reply instead; the property this test exists for — the
    question survives — is unchanged and is what is asserted below."""
    class Exploding(ModelProvider):
        name = "gemini"

        def list_models(self):
            return [ModelInfo(name="boom", provider=self.name)]

        def generate(self, request):
            raise RuntimeError("provider exploded")

    runtime.models.register(ScriptedProvider())
    conversation_id = _chat(client, "first message")["conversation_id"]
    runtime.models.register(Exploding())

    response = client.post(
        "/api/chat",
        json={"message": "this must not vanish", "conversation_id": conversation_id},
        headers={"X-API-Secret": SECRET},
    )

    assert response.status_code == 200
    contents = [m.content for m in runtime.conversations.history(conversation_id)]
    assert "this must not vanish" in contents


def test_a_specialist_does_not_inherit_the_coordinators_turn(runtime, client):
    """Capability composes, it never sums: the delegate runs with its own
    tools, not the caller's."""
    provider = ScriptedProvider(delegate_to=["research"])
    runtime.models.register(provider)

    _chat(client, "delegate please")

    specialist = next(r for r in provider.requests if "Top-level orchestrator" not in (r.system_prompt or ""))
    offered = {t["name"] for t in (specialist.tools or [])}
    assert "delegate_to_agent" not in offered
    assert "write_repository_file" not in offered


def _chat_turn_rows(runtime):
    return [e for e in runtime.audit.for_resource_action("chat.turn")] if hasattr(
        runtime.audit, "for_resource_action") else [
        e for e in runtime.audit.since("1970-01-01T00:00:00+00:00") if e.action == "chat.turn"
    ]


def test_a_turn_no_provider_could_serve_is_audited_as_a_failure(runtime, client):
    """A turn that produced no answer must not be recorded as a success.
    Telling a successful run from a failed one is the whole basis for
    deciding which runs are worth keeping."""
    # No provider registered under the Coordinator's order at all.
    response = client.post(
        "/api/chat", json={"message": "this cannot be served"},
        headers={"X-API-Secret": SECRET},
    )

    assert response.status_code == 200  # the caller still gets a civil reply
    rows = _chat_turn_rows(runtime)
    assert rows, "the turn must be audited even though it failed"
    assert rows[-1].outcome == "error"
    assert rows[-1].metadata.get("error")


def test_a_turn_a_provider_served_is_audited_as_a_success(runtime, client):
    runtime.models.register(ScriptedProvider())

    client.post("/api/chat", json={"message": "hello"}, headers={"X-API-Secret": SECRET})

    rows = _chat_turn_rows(runtime)
    assert rows[-1].outcome == "success"
    assert rows[-1].metadata.get("error") is None


def test_a_partial_turn_is_audited_as_partial_and_reports_what_ran(runtime, client):
    """The failure mode a live run hit: 106 seconds and 24 tool calls, then
    a provider limit, and the owner was told nothing had happened."""
    class DiesAfterOneToolCall(ModelProvider):
        name = "gemini"

        def __init__(self):
            self._served = False

        def list_models(self):
            return [ModelInfo(name="m", provider=self.name)]

        def generate(self, request):
            if self._served:
                raise ModelUnavailableError("simulated rate limit")
            self._served = True
            return ModelResponse(
                text=None, model_name="m", provider=self.name,
                tool_calls=[ToolCall(id="c1", name="create_project",
                                     arguments={"name": "real work", "description": "d"})],
            )

    runtime.models.register(DiesAfterOneToolCall())

    body = _chat(client, "create a project for me")

    assert "create_project" in body["response"]
    rows = _chat_turn_rows(runtime)
    assert rows[-1].outcome == "partial"
    assert rows[-1].metadata["completed_tool_calls"] == ["create_project"]
    # and the side effect really is on disk, which is the point
    assert [p.name for p in runtime.projects.list()] == ["real work"]


def test_a_turn_gets_a_run_id_that_every_audit_row_carries(runtime, client):
    """Goal 3 rests on this: an execution needs one identifier that its
    steps can be correlated by. Before this, tool rows recorded the tool
    name as their resource and nothing tied them to the turn -- so
    reconstructing a run meant inferring from timestamp adjacency, which
    over-collects the moment two runs overlap."""
    # Delegating puts tool calls on the concurrent pool in
    # AgentOrchestrator._run_tool_calls, which is the case the contextvar
    # exists for: a worker thread starts with an empty context unless the
    # caller copies its own in, and copy_context() carries the run id with it.
    runtime.models.register(ScriptedProvider(delegate_to=["research", "planning"]))

    body = _chat(client, "look into two things at once")

    run_id = body.get("run_id")
    assert run_id, "the response must hand the caller a run identifier"

    events = runtime.audit.for_run(run_id)
    actions = [e.action for e in events]
    assert "chat.turn" in actions
    delegations = [a for a in actions if a == "tool.execute:delegate_to_agent"]
    assert len(delegations) == 2, actions
    assert all(e.run_id == run_id for e in events)
    # and nothing leaked out of the run: no event from this turn is unstamped
    assert not [e for e in runtime.audit.since("1970-01-01T00:00:00+00:00")
                if e.run_id is None and e.action.startswith("tool.execute:")]


def test_the_run_record_says_how_the_turn_ended(runtime, client):
    runtime.models.register(ScriptedProvider())

    run_id = _chat(client, "create a project called outcome test")["run_id"]

    run = runtime.runs.get(run_id)
    assert run is not None
    assert run.status == "ok"
    assert run.goal == "create a project called outcome test"
    assert run.ended_at is not None
    assert run.model_used


def test_a_failed_turn_still_gets_a_run_record(runtime, client):
    """A run that failed is exactly the one you need to be able to find."""
    body = _chat(client, "nothing can serve this")

    run = runtime.runs.get(body["run_id"])
    assert run.status == "error"
    assert run.error


def test_the_same_idempotency_key_returns_the_original_run(runtime, client):
    """Submitting the same goal twice created two conversations, two
    projects and four tasks. Any caller with retry logic hits this, so an
    external app cannot safely retry a timeout without a key to dedupe on."""
    runtime.models.register(ScriptedProvider())

    first = client.post(
        "/api/chat",
        json={"message": "create a project", "idempotency_key": "order-42"},
        headers={"X-API-Secret": SECRET},
    ).json()
    second = client.post(
        "/api/chat",
        json={"message": "create a project", "idempotency_key": "order-42"},
        headers={"X-API-Secret": SECRET},
    ).json()

    assert second["run_id"] == first["run_id"]
    assert second["conversation_id"] == first["conversation_id"]
    assert second["response"] == first["response"]
    assert len(runtime.runs.list_recent()) == 1


def test_a_different_idempotency_key_runs_again(runtime, client):
    runtime.models.register(ScriptedProvider())

    first = client.post(
        "/api/chat", json={"message": "go", "idempotency_key": "a"},
        headers={"X-API-Secret": SECRET},
    ).json()
    second = client.post(
        "/api/chat", json={"message": "go", "idempotency_key": "b"},
        headers={"X-API-Secret": SECRET},
    ).json()

    assert first["run_id"] != second["run_id"]


def test_no_idempotency_key_keeps_the_previous_behaviour(runtime, client):
    runtime.models.register(ScriptedProvider())

    first = _chat(client, "go")
    second = _chat(client, "go")

    assert first["run_id"] != second["run_id"]
