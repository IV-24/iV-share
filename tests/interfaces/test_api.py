"""Exercises interfaces/api/main.py end-to-end via FastAPI's TestClient,
with an in-memory, no-network test runtime (NullModelProvider only, no
real SQLite file, no real provider key) — the same "core needs no
external infra to run" property the CLI tests assert, applied to the HTTP
layer."""

import pytest
from fastapi.testclient import TestClient

from adapters.games.chess_tools import register_chess_tools
from core.agent.catalog import AgentCatalog
from core.agent.composition_tools import register_composition_tools
from core.agent.delegation import register_delegation_tool
from core.agent.orchestrator import AgentOrchestrator
from core.agent.roles import AgentRole, DEFAULT_ROLES
from core.agent.store import AgentDefinitionStore
from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.context.manager import ContextManager
from core.conversations.base import ConversationStore
from core.games.chess import ChessGameStore
from core.improvements.manager import ImprovementManager
from core.memory.base import MemoryStore
from core.models.null_provider import NullModelProvider
from core.models.registry import ModelRegistry
from core.permissions.manager import PermissionManager
from core.permissions.scopes import PermissionScope
from core.projects.base import ProjectStore
from core.runs.base import RunRecorder
from core.storage.memory import InMemoryStorage
from core.tasks.base import TaskStore
from core.tools.guardian import register_guardian_tools
from core.tools.base import ExecutionPolicy, RiskLevel, ToolDefinition
from core.tools.registry import ToolRegistry
from core.tools.standard import register_standard_tools
from interfaces.api.main import _cors_origins, create_app
from interfaces.api.runtime import ApiRuntime

SECRET = "test-secret"


def build_test_runtime() -> ApiRuntime:
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

    agents = AgentDefinitionStore(storage, reserved_names=frozenset(
        [key for key in DEFAULT_ROLES] + [role.name for role in DEFAULT_ROLES.values()]
    ))
    catalog = AgentCatalog(agents)

    register_standard_tools(
        tools, projects=projects, tasks=tasks, memory=memory, approvals=approvals, improvements=improvements
    )
    register_guardian_tools(tools, audit=audit, permissions=permissions)
    register_chess_tools(tools, games=games)

    models = ModelRegistry()
    models.register(NullModelProvider())

    # Mirrors interfaces/api/runtime.py: the test runtime is only useful
    # as a stand-in if it has the same tool surface the real one does.
    orchestrator_holder = {}
    register_delegation_tool(
        tools, orchestrator_getter=lambda: orchestrator_holder["value"],
        catalog=catalog, audit=audit,
    )
    register_composition_tools(
        tools, store=agents, models=models, permissions=permissions, audit=audit
    )

    for role in DEFAULT_ROLES.values():
        for scope in role.permission_scopes:
            permissions.grant(role.name, scope, granted_by="test-bootstrap")

    orchestrator = AgentOrchestrator(models, tools, memory, ContextManager())
    orchestrator_holder["value"] = orchestrator
    return ApiRuntime(
        runs=RunRecorder(storage),
        storage=storage, orchestrator=orchestrator, conversations=conversations,
        approvals=approvals, permissions=permissions, audit=audit, memory=memory, models=models,
        tools=tools, projects=projects, tasks=tasks, improvements=improvements, games=games,
        catalog=catalog, agents=agents,
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("API_ACCESS_SECRET", SECRET)
    # DEFAULT_ROLES' real provider orders (gemini/mistral/groq/claude)
    # aren't registered in the test runtime — swap "coordinator" for a
    # test double that resolves against NullModelProvider, same fix the
    # CLI's build_runtime applies for its own zero-config offline mode.
    monkeypatch.setitem(
        DEFAULT_ROLES, "coordinator",
        AgentRole(
            name="Coordinator", description="test coordinator",
            tool_names=["create_project"], permission_scopes=[PermissionScope.DATABASE_WRITE],
            model_provider_order=["null"],
        ),
    )
    app = create_app(runtime_factory=build_test_runtime)
    with TestClient(app) as c:
        yield c


def test_home():
    app = create_app(runtime_factory=build_test_runtime)
    with TestClient(app) as c:
        response = c.get("/")
        assert response.status_code == 200
        assert response.json()["status"] == "iV online"


def test_chat_requires_secret(client):
    response = client.post("/api/chat", json={"message": "hi"})
    assert response.status_code == 403


def test_chat_with_valid_secret_gets_a_response(client):
    response = client.post(
        "/api/chat", json={"message": "hello iV"}, headers={"X-API-Secret": SECRET}
    )
    assert response.status_code == 200
    body = response.json()
    assert "hello iV" in body["response"]
    assert body["model_used"] == "null:null-echo"
    assert body["conversation_id"]


def test_chat_rate_limit_returns_429_once_the_burst_is_spent(client):
    """/api/chat can fan out to MAX_DELEGATIONS_PER_TURN specialist calls
    per request (core/agent/delegation.py) -- a retry loop hitting this
    endpoint is a paid-API-call amplifier, not just noise. The limiter is
    in-process (interfaces/api/security.RateLimiter), so a fresh test
    client -- a fresh app, a fresh limiter -- starts with a full bucket."""
    from interfaces.api.security import CHAT_RATE_LIMIT_CAPACITY

    for _ in range(CHAT_RATE_LIMIT_CAPACITY):
        response = client.post("/api/chat", json={"message": "hi"}, headers={"X-API-Secret": SECRET})
        assert response.status_code == 200

    response = client.post("/api/chat", json={"message": "hi"}, headers={"X-API-Secret": SECRET})

    assert response.status_code == 429


def test_chat_reuses_conversation_history(client):
    first = client.post(
        "/api/chat", json={"message": "first message"}, headers={"X-API-Secret": SECRET}
    ).json()

    second = client.post(
        "/api/chat",
        json={"message": "second message", "conversation_id": first["conversation_id"]},
        headers={"X-API-Secret": SECRET},
    ).json()

    assert second["conversation_id"] == first["conversation_id"]


def test_chat_ignores_a_caller_supplied_role(client):
    """/api/chat has no role field any more. A caller naming a sub-agent
    is not an error, it is simply ignored — the Coordinator serves every
    user-facing turn. This is the property that keeps the Master Agent
    unbypassable by anything that can reach the frontend proxy."""
    response = client.post(
        "/api/chat", json={"message": "hi", "role": "engineering"}, headers={"X-API-Secret": SECRET}
    )
    assert response.status_code == 200


def test_approvals_pending_requires_secret(client):
    assert client.get("/approvals/pending").status_code == 403


def test_approvals_pending_empty(client):
    response = client.get("/approvals/pending", params={"secret": SECRET})
    assert response.status_code == 200
    assert "No pending approvals" in response.text


def test_approvals_decide_flow(client):
    runtime: ApiRuntime = client.app.state.runtime
    request = runtime.approvals.request(action_type="wire_transfer", description="pay invoice", requested_by="finance")

    pending_page = client.get("/approvals/pending", params={"secret": SECRET})
    assert "wire_transfer" in pending_page.text

    decide = client.post(
        "/approvals/decide", data={"id": request.id, "decision": "approved", "secret": SECRET},
        follow_redirects=False,
    )
    assert decide.status_code == 303
    assert runtime.approvals.is_approved(request.id) is True


def test_approvals_decide_all_denies_everything(client):
    runtime: ApiRuntime = client.app.state.runtime
    a = runtime.approvals.request(action_type="a", description="a", requested_by="x")
    b = runtime.approvals.request(action_type="b", description="b", requested_by="x")

    client.post("/approvals/decide-all", data={"decision": "denied", "secret": SECRET}, follow_redirects=False)

    assert runtime.approvals.get(a.id).status.value == "denied"
    assert runtime.approvals.get(b.id).status.value == "denied"


def test_cors_origins_default():
    # The literal port lives in core/configuration/ports.py; asserting the
    # constant rather than a number keeps this test from being the thing
    # that has to be remembered when the default moves.
    from core.configuration.ports import DEFAULT_FRONTEND_PORT

    assert f"http://localhost:{DEFAULT_FRONTEND_PORT}" in _cors_origins()


def test_cors_origins_from_env(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://example.com, https://foo.bar")
    assert _cors_origins() == ["https://example.com", "https://foo.bar"]


def test_sleep_cycle_requires_internal_secret(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_TRIGGER_SECRET", "trigger-secret")
    response = client.post("/internal/sleep-cycle")
    assert response.status_code == 403


def test_sleep_cycle_runs_with_correct_secret(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_TRIGGER_SECRET", "trigger-secret")
    response = client.post("/internal/sleep-cycle", headers={"x-internal-secret": "trigger-secret"})
    assert response.status_code == 200
    # The test runtime only registers NullModelProvider under "null", not
    # any of the reflection cycle's default provider names (gemini/
    # mistral/groq) -- so this correctly reports "no model available"
    # rather than crashing, exactly the behavior a real deployment with
    # no provider configured should see.
    assert response.json()["status"] == "error"


def test_haven_manifest_requires_secret(client):
    assert client.get("/haven/manifest").status_code == 403


def test_haven_manifest_reports_tools_and_grants(client):
    response = client.get("/haven/manifest", params={"secret": SECRET})
    assert response.status_code == 200
    body = response.json()
    assert "create_project" in body["tools"] or any(t["name"] == "create_project" for t in body["tools"])
    assert any(g["principal"] == "Coordinator" for g in body["grants"])
    assert "os_name" in body["environment"]


def test_materialize_backlog_requires_internal_secret(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_TRIGGER_SECRET", "trigger-secret")
    response = client.post("/internal/materialize-backlog")
    assert response.status_code == 403


def test_materialize_backlog_turns_approved_items_into_tasks(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_TRIGGER_SECRET", "trigger-secret")
    runtime: ApiRuntime = client.app.state.runtime
    request = runtime.approvals.request(
        action_type="Add retries", description="Handle 503s better", requested_by="iV Sleep Cycle"
    )
    runtime.approvals.decide(request.id, approved=True, decided_by="owner")

    response = client.post("/internal/materialize-backlog", headers={"x-internal-secret": "trigger-secret"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "tasks_created": 1}
    assert runtime.approvals.get(request.id).status.value == "executed"


def test_a_new_conversation_is_titled_from_the_first_message(client):
    """Nothing ever updated the "New conversation" default before this --
    every conversation in the history list looked the same."""
    response = client.post(
        "/api/chat", json={"message": "Summarize the architecture of this project"},
        headers={"X-API-Secret": SECRET},
    )
    conversation_id = response.json()["conversation_id"]

    history = client.get(f"/api/conversations/{conversation_id}", headers={"X-API-Secret": SECRET}).json()

    assert history["title"] == "Summarize the architecture of this project"


def test_conversation_history_round_trips(client):
    """Persistence was always real; the missing piece was a way to read a
    conversation back, which is what makes a page reload non-destructive."""
    first = client.post("/api/chat", json={"message": "hello"},
                        headers={"X-API-Secret": SECRET})
    conversation_id = first.json()["conversation_id"]

    response = client.get(f"/api/conversations/{conversation_id}", headers={"X-API-Secret": SECRET})

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == conversation_id
    assert [m["role"] for m in body["messages"]] == ["user", "iv"]
    assert body["messages"][0]["content"] == "hello"


def test_conversation_history_requires_the_secret(client):
    assert client.get("/api/conversations/whatever").status_code == 403


def test_conversation_history_404s_for_an_unknown_id(client):
    response = client.get("/api/conversations/does-not-exist", headers={"X-API-Secret": SECRET})

    assert response.status_code == 404


def test_conversations_list_backs_a_recent_conversations_sidebar(client):
    first = client.post("/api/chat", json={"message": "first chat"}, headers={"X-API-Secret": SECRET}).json()
    second = client.post("/api/chat", json={"message": "second chat"}, headers={"X-API-Secret": SECRET}).json()

    response = client.get("/api/conversations", headers={"X-API-Secret": SECRET})

    assert response.status_code == 200
    ids = {c["id"] for c in response.json()}
    assert {first["conversation_id"], second["conversation_id"]} <= ids


def test_conversations_list_requires_the_secret(client):
    assert client.get("/api/conversations").status_code == 403


def test_projects_and_tasks_round_trip_through_the_rest_routes(client, monkeypatch):
    """Backs the project sidebar: direct reads of the owner's own data,
    same trust level as GET /api/conversations/{id} -- gated on the chat
    secret, never routed through the agent/tool layer."""
    from interfaces.api.runtime import ApiRuntime

    runtime: ApiRuntime = client.app.state.runtime
    project = runtime.projects.create("Kitchen remodel", status="active")
    task = runtime.tasks.create(project.id, "Pick tile", status="pending")

    listed = client.get("/api/projects?status=active", headers={"X-API-Secret": SECRET}).json()
    assert any(p["id"] == project.id for p in listed)

    single = client.get(f"/api/projects/{project.id}", headers={"X-API-Secret": SECRET}).json()
    assert single["name"] == "Kitchen remodel"

    assert client.get("/api/projects/no-such-project", headers={"X-API-Secret": SECRET}).status_code == 404

    tasks = client.get(f"/api/tasks?project_id={project.id}", headers={"X-API-Secret": SECRET}).json()
    assert tasks == [{
        "id": task.id, "project_id": project.id, "title": "Pick tile", "description": "",
        "status": "pending", "priority": 5, "assigned_role": None, "created_at": task.created_at,
    }]

    updated = client.post(
        f"/api/tasks/{task.id}/status", json={"status": "in_progress"}, headers={"X-API-Secret": SECRET}
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "in_progress"

    assert client.post(
        "/api/tasks/no-such-task/status", json={"status": "done"}, headers={"X-API-Secret": SECRET}
    ).status_code == 404


def test_projects_and_tasks_routes_require_the_secret(client):
    assert client.get("/api/projects").status_code == 403
    assert client.get("/api/tasks?project_id=x").status_code == 403
    assert client.post("/api/tasks/x/status", json={"status": "done"}).status_code == 403


def test_approvals_link_moves_the_secret_from_the_url_into_a_cookie(client):
    """iV's first self-audit flagged the secret travelling in the query
    string (SEC-QS-01). The emailed link still carries it once; after that
    it lives in an HttpOnly cookie and no URL contains it."""
    response = client.get(f"/approvals/pending?secret={SECRET}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/approvals/pending"
    cookie = response.cookies.get("iv_approvals_session")
    assert cookie == SECRET
    assert "httponly" in response.headers["set-cookie"].lower()


def test_approvals_page_renders_from_the_cookie_alone(client):
    client.get(f"/approvals/pending?secret={SECRET}")  # establishes the cookie

    page = client.get("/approvals/pending")

    assert page.status_code == 200
    assert "iV Pending Approvals" in page.text
    # The rendered forms must no longer embed the secret anywhere.
    assert SECRET not in page.text


def test_approvals_page_rejects_a_request_with_neither_cookie_nor_query(client):
    client.cookies.clear()

    assert client.get("/approvals/pending").status_code == 403


def test_decide_redirects_without_putting_the_secret_in_the_url(client):
    client.get(f"/approvals/pending?secret={SECRET}")
    runtime = client.app.state.runtime
    request = runtime.approvals.request(
        action_type="test.action", description="d", requested_by="tester"
    )

    response = client.post(
        "/approvals/decide", data={"id": request.id, "decision": "approved"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/approvals/pending"
    assert SECRET not in response.headers["location"]
    assert runtime.approvals.get(request.id).status.value == "approved"


def test_agents_endpoint_lists_the_roster(client):
    response = client.get("/api/agents", headers={"X-API-Secret": SECRET})

    assert response.status_code == 200
    agents = response.json()["agents"]
    keys = {a["key"] for a in agents}
    assert {"coordinator", "engineering", "auditor"} <= keys
    for agent in agents:
        assert agent["models"], f"{agent['key']} must declare a model preference"
        assert "builtin" in agent


def test_agents_endpoint_requires_the_secret(client):
    assert client.get("/api/agents").status_code == 403


def test_agent_chat_rejects_an_unknown_agent(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_TRIGGER_SECRET", "internal-secret")
    response = client.post(
        "/internal/agent-chat", json={"message": "hi", "role": "no-such-agent"},
        headers={"X-Internal-Secret": "internal-secret"},
    )

    assert response.status_code == 400
    assert "unknown agent" in response.json()["detail"]


def test_agent_chat_requires_the_internal_secret(client, monkeypatch):
    """Direct sub-agent addressing is an operator action. The chat secret
    -- which the frontend proxy holds and attaches to every page request
    -- must not be enough to reach it."""
    monkeypatch.setenv("INTERNAL_TRIGGER_SECRET", "internal-secret")
    assert client.post(
        "/internal/agent-chat", json={"message": "hi", "role": "engineering"},
        headers={"X-API-Secret": SECRET},
    ).status_code == 403


def test_agent_chat_reaches_the_named_agent(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_TRIGGER_SECRET", "internal-secret")
    response = client.post(
        "/internal/agent-chat", json={"message": "hi", "role": "coordinator"},
        headers={"X-Internal-Secret": "internal-secret"},
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------
# Chess
#
# The test coordinator swapped in by the `client` fixture has no chess
# tools and runs on NullModelProvider, which echoes text rather than
# calling tools -- so these exercise the HTTP layer (secret gating,
# request/response shapes, turn-order and legality enforcement, which are
# all checked *before* any model runs) rather than "does iV actually play
# a move," which is exercised without any model involved at all in
# tests/core/test_chess_tools.py.
# ---------------------------------------------------------------------


def test_create_chess_game_requires_secret(client):
    assert client.post("/api/chess/games", json={}).status_code == 403


def test_create_chess_game_defaults_to_human_white(client):
    response = client.post("/api/chess/games", json={}, headers={"X-API-Secret": SECRET})
    assert response.status_code == 200
    body = response.json()
    assert body["game"]["human_color"] == "white"
    assert body["game"]["turn"] == "white"
    assert body["game"]["status"] == "active"
    assert body["game"]["moves"] == []
    # It's the human's move first, so iV has nothing to say yet.
    assert body["iv_reply"] is None
    assert body["conversation_id"]


def test_create_chess_game_rejects_invalid_color(client):
    response = client.post(
        "/api/chess/games", json={"human_color": "purple"}, headers={"X-API-Secret": SECRET}
    )
    assert response.status_code == 400


def test_create_chess_game_as_black_gives_ivs_turn_a_reply(client):
    """human_color=black means it's iV's move immediately -- the create
    route should run iV's turn in the same request, so iv_reply is not
    None even though the null test model never actually calls a tool to
    move (see this section's docstring)."""
    response = client.post(
        "/api/chess/games", json={"human_color": "black"}, headers={"X-API-Secret": SECRET}
    )
    body = response.json()
    assert body["game"]["human_color"] == "black"
    assert body["game"]["turn"] == "white"
    assert body["iv_reply"] is not None


def test_list_and_get_chess_game(client):
    created = client.post("/api/chess/games", json={}, headers={"X-API-Secret": SECRET}).json()
    game_id = created["game"]["id"]

    listed = client.get("/api/chess/games", headers={"X-API-Secret": SECRET})
    assert listed.status_code == 200
    assert any(g["id"] == game_id for g in listed.json())

    fetched = client.get(f"/api/chess/games/{game_id}", headers={"X-API-Secret": SECRET})
    assert fetched.status_code == 200
    assert fetched.json()["id"] == game_id


def test_get_chess_game_missing_is_404(client):
    assert client.get("/api/chess/games/nope", headers={"X-API-Secret": SECRET}).status_code == 404


def test_move_requires_secret(client):
    created = client.post("/api/chess/games", json={}, headers={"X-API-Secret": SECRET}).json()
    game_id = created["game"]["id"]
    assert client.post(f"/api/chess/games/{game_id}/move", json={"move": "e4"}).status_code == 403


def test_move_missing_game_is_404(client):
    response = client.post(
        "/api/chess/games/nope/move", json={"move": "e4"}, headers={"X-API-Secret": SECRET}
    )
    assert response.status_code == 404


def test_legal_move_updates_the_game_and_flips_the_turn(client):
    created = client.post("/api/chess/games", json={}, headers={"X-API-Secret": SECRET}).json()
    game_id = created["game"]["id"]

    response = client.post(
        f"/api/chess/games/{game_id}/move", json={"move": "e4"}, headers={"X-API-Secret": SECRET}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["game"]["moves"] == ["e4"]
    assert body["game"]["turn"] == "black"
    assert body["game"]["status"] == "active"
    # It's now iV's turn, so its turn ran in the same request.
    assert body["iv_reply"] is not None


def test_illegal_move_is_rejected_with_legal_moves_listed(client):
    created = client.post("/api/chess/games", json={}, headers={"X-API-Secret": SECRET}).json()
    game_id = created["game"]["id"]

    response = client.post(
        f"/api/chess/games/{game_id}/move", json={"move": "e5"}, headers={"X-API-Secret": SECRET}
    )
    assert response.status_code == 400
    assert "e4" in response.json()["detail"]["legal_moves"]


def test_move_out_of_turn_is_rejected(client):
    """human_color defaults to white, so trying to move as if it were
    black's turn (nothing has been played yet) must be refused."""
    created = client.post(
        "/api/chess/games", json={"human_color": "black"}, headers={"X-API-Secret": SECRET}
    ).json()
    game_id = created["game"]["id"]

    response = client.post(
        f"/api/chess/games/{game_id}/move", json={"move": "e5"}, headers={"X-API-Secret": SECRET}
    )
    assert response.status_code == 400
    assert "turn" in response.json()["detail"]


def test_move_on_finished_game_is_rejected(client):
    runtime: ApiRuntime = client.app.state.runtime
    created = client.post("/api/chess/games", json={}, headers={"X-API-Secret": SECRET}).json()
    game_id = created["game"]["id"]
    runtime.games.record_move(game_id, fen=created["game"]["fen"], san="e4", status="checkmate", result="1-0")

    response = client.post(
        f"/api/chess/games/{game_id}/move", json={"move": "e4"}, headers={"X-API-Secret": SECRET}
    )
    assert response.status_code == 400
    assert "already ended" in response.json()["detail"]


def test_approving_a_request_carries_the_action_out(monkeypatch):
    """The audit's decisive finding: approving changed a database row and
    nothing else, so every consequential action was permanently blocked
    rather than governed.

    The role is registered in DEFAULT_ROLES rather than built here on the
    spot, because execution resolves the requesting agent through the
    catalog rather than trusting whatever object filed the request -- an
    approval names an agent, and the agent's own tool list and scopes are
    what decide, not the caller's say-so."""
    ran: list[dict] = []
    monkeypatch.setenv("API_ACCESS_SECRET", SECRET)
    monkeypatch.setitem(
        DEFAULT_ROLES, "coordinator",
        AgentRole(
            name="Coordinator", description="test coordinator",
            tool_names=["create_project", "guarded_action"],
            permission_scopes=[PermissionScope.DATABASE_WRITE],
            model_provider_order=["null"],
        ),
    )
    app = create_app(runtime_factory=build_test_runtime)
    with TestClient(app) as client:
        runtime = client.app.state.runtime
        runtime.tools.register(ToolDefinition(
            name="guarded_action", description="does something consequential",
            input_schema={"type": "object"}, output_schema={"type": "object"},
            handler=lambda **kw: ran.append(kw) or {"ok": True},
            required_permissions=[PermissionScope.DATABASE_WRITE],
            risk_level=RiskLevel.HIGH, execution_policy=ExecutionPolicy.REQUIRES_APPROVAL,
        ))
        runtime.permissions.grant("Coordinator", PermissionScope.DATABASE_WRITE, granted_by="test")

        blocked = runtime.orchestrator.run_tool(
            DEFAULT_ROLES["coordinator"], "guarded_action", {"target": "prod"}
        )
        assert blocked.error == "approval_required"
        assert ran == [], "nothing may run before the decision"

        response = client.post(
            "/approvals/decide",
            data={"id": blocked.approval_id, "decision": "approved", "secret": SECRET},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert ran == [{"target": "prod"}], "approving must actually carry the action out"
        assert runtime.approvals.get(blocked.approval_id).status.value == "executed"


def test_denying_a_request_leaves_the_action_unrun(monkeypatch):
    ran: list[dict] = []
    monkeypatch.setenv("API_ACCESS_SECRET", SECRET)
    monkeypatch.setitem(
        DEFAULT_ROLES, "coordinator",
        AgentRole(
            name="Coordinator", description="test coordinator",
            tool_names=["guarded_action"], permission_scopes=[PermissionScope.DATABASE_WRITE],
            model_provider_order=["null"],
        ),
    )
    app = create_app(runtime_factory=build_test_runtime)
    with TestClient(app) as client:
        runtime = client.app.state.runtime
        runtime.tools.register(ToolDefinition(
            name="guarded_action", description="does something consequential",
            input_schema={"type": "object"}, output_schema={"type": "object"},
            handler=lambda **kw: ran.append(kw) or {"ok": True},
            required_permissions=[PermissionScope.DATABASE_WRITE],
            risk_level=RiskLevel.HIGH, execution_policy=ExecutionPolicy.REQUIRES_APPROVAL,
        ))
        runtime.permissions.grant("Coordinator", PermissionScope.DATABASE_WRITE, granted_by="test")
        blocked = runtime.orchestrator.run_tool(
            DEFAULT_ROLES["coordinator"], "guarded_action", {"target": "prod"}
        )

        client.post(
            "/approvals/decide",
            data={"id": blocked.approval_id, "decision": "denied", "secret": SECRET},
            follow_redirects=False,
        )

        assert ran == []
        assert runtime.approvals.get(blocked.approval_id).status.value == "denied"
