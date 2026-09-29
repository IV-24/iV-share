"""iV's HTTP API: a thin layer over core. Every route here does request
parsing/response shaping and calls into an ApiRuntime (core.agent,
core.approvals, core.conversations, ...) — no agent logic, storage, or
provider-specific code lives in this file.

Run with: uvicorn interfaces.api.main:app --host 0.0.0.0 --port 8024
(see run.py, or docs/DEVELOPMENT_SETUP.md for the persistent-server setup).

Replaces backend/app/main.py: same /api/chat, /approvals/*, and
/internal/sleep-cycle shape, but backed by core + local SQLite instead of
a hand-rolled Supabase-calling router, with CORS origins read from an env
var instead of a hardcoded Tailscale IP. /haven/manifest and
/internal/materialize-backlog are new — see docs/HAVEN.md.

/api/chat is the only conversational entry point and it always reaches
the Coordinator; sub-agents are reached solely by the Coordinator
delegating to them. Direct addressing of one agent lives at
/internal/agent-chat behind the internal secret, as an operator/debug
affordance the frontend proxy does not expose.
"""

import html
import logging
import os
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import Cookie, Depends, FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse

from adapters.games import chess_engine
from adapters.notifications.email import load_email_config
from core.agent.roles import DEFAULT_ROLES
from core.configuration.ports import DEFAULT_FRONTEND_ORIGINS
from core.configuration.settings import load_settings
from core.conversations.base import derive_title
from core.games.chess import ChessGame
from core.haven.manifest import build_haven_manifest
from core.models.base import ModelMessage, ModelUnavailableError
from core.observability.logging import configure_logging
from core.approvals.execution import ApprovalNotExecutable, execute_approved_request
from core.reflection.base import materialize_approved_action_items
from core.runs.context import reset_current_run_id, set_current_run_id
from interfaces.api.health import build_health, build_status
from interfaces.api.runtime import ApiRuntime, build_runtime
from interfaces.api.schemas import (
    AgentChatRequest,
    ChatRequest,
    ChatResponse,
    ChessGameOut,
    ChessMoveRequest,
    ChessMoveResponse,
    ConversationHistoryResponse,
    ConversationSummary,
    HistoryMessage,
    NewChessGameRequest,
    ProjectOut,
    RunOut,
    RunStep,
    SelfAuditRequest,
    TaskOut,
    TaskStatusUpdateRequest,
)
from interfaces.api.security import (
    APPROVAL_SESSION_COOKIE,
    CHAT_RATE_LIMIT_CAPACITY,
    CHAT_RATE_LIMIT_REFILL_PER_SECOND,
    RateLimiter,
    check_internal_secret,
    check_rate_limit,
    check_secret,
    require_chat_secret,
    resolve_approval_secret,
)
from interfaces.api.selfaudit_runner import run_api_self_audit
from interfaces.api.sleep_cycle import run_sleep_cycle

logger = logging.getLogger(__name__)

API_VERSION = "2.2.0"

# The one agent /api/chat ever speaks to. Sub-agents are reached only by
# the Coordinator delegating to them, never by the user naming one.
COORDINATOR_KEY = "coordinator"


def _lookup_agent(runtime: ApiRuntime, name: str):
    """Resolved through the catalog, not DEFAULT_ROLES, so a composed
    agent can be addressed by name the moment it is created."""
    if runtime.catalog is not None:
        return runtime.catalog.get(name)
    return DEFAULT_ROLES.get(name)


_NO_MODEL_AVAILABLE_MESSAGE = (
    "iV couldn't reach any configured model just now. Check that a provider "
    "API key (GEMINI_API_KEY, GROQ_API_KEY, ...) is set, or try again shortly."
)


def _cors_origins() -> list[str]:
    raw = os.getenv("CORS_ALLOWED_ORIGINS", DEFAULT_FRONTEND_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def create_app(runtime_factory: Callable[[], ApiRuntime] | None = None) -> FastAPI:
    """runtime_factory defaults to build_runtime() (real local SQLite +
    whatever providers are configured via env). Tests pass a factory that
    returns an in-memory-backed runtime instead, so importing/constructing
    the app never touches disk or the network on its own."""
    runtime_factory = runtime_factory or build_runtime

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Startup builds the runtime once and logs what came up; shutdown
        closes the storage handle. Both halves log, because the two
        failure modes an operator actually hits are "it never finished
        starting" and "it died without flushing" — and a silent lifespan
        makes them look identical in the log."""
        configure_logging()
        logger.info("iV runtime starting (version=%s)", API_VERSION)
        runtime = runtime_factory()
        app.state.runtime = runtime
        app.state.chat_rate_limiter = RateLimiter(
            capacity=CHAT_RATE_LIMIT_CAPACITY, refill_per_second=CHAT_RATE_LIMIT_REFILL_PER_SECOND
        )
        logger.info(
            "iV runtime ready: providers=%s tools=%d",
            ",".join(runtime.models.provider_names()) or "none",
            len(runtime.tools.list_tools()),
        )
        try:
            yield
        finally:
            logger.info("iV runtime shutting down; closing storage")
            try:
                runtime.storage.close()
            except Exception:  # noqa: BLE001 - shutdown must not raise past the server
                logger.exception("error closing storage during shutdown")
            logger.info("iV runtime stopped")

    app = FastAPI(title="iV Core API", version=API_VERSION, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_origin_regex=os.getenv("CORS_ALLOWED_ORIGIN_REGEX"),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_runtime(request: Request) -> ApiRuntime:
        return request.app.state.runtime

    def enforce_chat_rate_limit(request: Request) -> None:
        check_rate_limit(request.app.state.chat_rate_limiter)

    @app.get("/")
    def home():
        return {"status": "iV online", "mode": "local", "version": API_VERSION}

    @app.get("/health")
    def health(runtime: ApiRuntime = Depends(get_runtime)):
        """Unauthenticated on purpose: this is what a supervisor, a
        LaunchAgent wrapper, or the frontend's connection indicator polls,
        and requiring the shared secret would mean distributing it to
        every one of them. It exposes no secret value and no user data —
        only whether components are working. See docs/RUNTIME.md's
        security assumptions."""
        return build_health(runtime, version=API_VERSION)

    @app.get("/status")
    def status(secret: str | None = Query(default=None), runtime: ApiRuntime = Depends(get_runtime)):
        """Authenticated, unlike /health: this reports which providers are
        provisioned, which tools exist and at what risk tier, and which
        credentials are configured (as booleans). None of that is a
        secret, but together it is a map of the system, and a map is worth
        withholding from anything that can't already talk to iV."""
        check_secret(secret)
        return build_status(runtime, version=API_VERSION)

    @app.get(
        "/api/conversations/{conversation_id}",
        response_model=ConversationHistoryResponse,
        dependencies=[Depends(require_chat_secret)],
    )
    def conversation_history(conversation_id: str, runtime: ApiRuntime = Depends(get_runtime)):
        """Lets the frontend restore a conversation after a reload — the
        messages were always persisted, there was simply no way to read
        them back over HTTP."""
        conversation = runtime.conversations.get(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="no such conversation")
        return ConversationHistoryResponse(
            conversation_id=conversation.id,
            title=conversation.title,
            messages=[
                HistoryMessage(
                    role=message.role, content=message.content,
                    model_used=message.model_used, created_at=message.created_at,
                )
                for message in runtime.conversations.history(conversation.id)
            ],
        )

    @app.get("/haven/manifest")
    def haven_manifest(secret: str | None = Query(default=None), runtime: ApiRuntime = Depends(get_runtime)):
        check_secret(secret)
        return build_haven_manifest(tools=runtime.tools, permissions=runtime.permissions)

    @app.get("/api/conversations", response_model=list[ConversationSummary], dependencies=[Depends(require_chat_secret)])
    def list_conversations(runtime: ApiRuntime = Depends(get_runtime)):
        """Recent conversations for a history list in the sidebar. A
        direct store read, same as GET /api/conversations/{id} below --
        not an agent action, so it never touches the orchestrator."""
        return [
            ConversationSummary(id=c.id, title=c.title, created_at=c.created_at)
            for c in runtime.conversations.list_recent()
        ]

    @app.get("/api/runs", response_model=list[RunOut], dependencies=[Depends(require_chat_secret)])
    def list_runs(limit: int = Query(default=50, ge=1, le=500), runtime: ApiRuntime = Depends(get_runtime)):
        """Recent executions, newest first, without their steps -- an index
        to pick a run out of, not a trace dump."""
        return [RunOut(**vars(run)) for run in runtime.runs.list_recent(limit=limit)]

    @app.get("/api/runs/{run_id}", response_model=RunOut, dependencies=[Depends(require_chat_secret)])
    def get_run(run_id: str, runtime: ApiRuntime = Depends(get_runtime)):
        """One execution with its steps. This is what makes a past run
        reconstructable: the steps are selected by run_id, so two runs that
        overlapped in time do not contaminate each other's traces."""
        run = runtime.runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="no such run")
        return RunOut(
            **vars(run),
            steps=[
                RunStep(
                    at=event.timestamp, actor=event.actor, action=event.action,
                    resource=event.resource, outcome=event.outcome, metadata=event.metadata,
                )
                for event in runtime.audit.for_run(run_id)
            ],
        )

    @app.get("/api/projects", response_model=list[ProjectOut], dependencies=[Depends(require_chat_secret)])
    def list_projects(status: str | None = Query(default=None), runtime: ApiRuntime = Depends(get_runtime)):
        """Backs the project sidebar. Reads runtime.projects directly --
        the same store the create_project/list_projects *tools* already
        read and write -- rather than going through the agent/tool layer,
        because this is the owner looking at their own data, not an agent
        deciding to act on it."""
        return [ProjectOut(**vars(p)) for p in runtime.projects.list(status=status)]

    @app.get("/api/projects/{project_id}", response_model=ProjectOut, dependencies=[Depends(require_chat_secret)])
    def get_project(project_id: str, runtime: ApiRuntime = Depends(get_runtime)):
        project = runtime.projects.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="no such project")
        return ProjectOut(**vars(project))

    @app.get("/api/tasks", response_model=list[TaskOut], dependencies=[Depends(require_chat_secret)])
    def list_tasks(project_id: str = Query(...), runtime: ApiRuntime = Depends(get_runtime)):
        return [TaskOut(**vars(t)) for t in runtime.tasks.list_for_project(project_id)]

    @app.post("/api/tasks/{task_id}/status", response_model=TaskOut, dependencies=[Depends(require_chat_secret)])
    def update_task_status(
        task_id: str, body: TaskStatusUpdateRequest, runtime: ApiRuntime = Depends(get_runtime)
    ):
        """The same update_task_status the Planning/Engineering roles
        already call as a tool (core/tools/standard.py), at the same LOW
        risk / AUTO execution tier -- the owner moving their own task
        forward from the sidebar needs no more gating than an agent doing
        the identical thing already has."""
        task = runtime.tasks.update_status(task_id, body.status)
        if task is None:
            raise HTTPException(status_code=404, detail="no such task")
        return TaskOut(**vars(task))

    def _replay_completed_run(runtime: ApiRuntime, idempotency_key: str) -> ChatResponse | None:
        """A run already recorded under this key means the work was done.
        Returning its result is the point of the key: a caller retrying a
        request that timed out must not cause the side effects twice.

        A run still in flight is deliberately NOT replayed -- it has no
        answer yet, and returning an empty one would be worse than letting
        the caller wait or retry."""
        existing = runtime.runs.find_by_idempotency_key(idempotency_key)
        if existing is None or existing.status == "running":
            return None
        reply = ""
        if existing.conversation_id:
            history = runtime.conversations.history(existing.conversation_id)
            reply = next((m.content for m in reversed(history) if m.role in ("iv", "assistant")), "")
        return ChatResponse(
            response=reply, model_used=existing.model_used,
            conversation_id=existing.conversation_id or "", run_id=existing.id,
        )

    def _run_turn(
        runtime: ApiRuntime, role, message: str, conversation_id: str | None,
        idempotency_key: str | None = None,
    ) -> ChatResponse:
        """One user-visible turn, whichever agent serves it.

        Ordering matters here. The user's message is persisted *before*
        the model is called, so a provider or runtime failure loses the
        reply but never the question — previously an exception left the
        turn unrecorded and the message gone on the next reload.
        """
        if idempotency_key:
            replayed = _replay_completed_run(runtime, idempotency_key)
            if replayed is not None:
                return replayed

        conversation = runtime.conversations.get(conversation_id) if conversation_id else None
        if conversation is None:
            conversation = runtime.conversations.create(title=derive_title(message))

        history = [
            ModelMessage(role=("assistant" if m.role in ("iv", "assistant") else "user"), content=m.content)
            for m in runtime.conversations.history(conversation.id)
        ]
        runtime.conversations.add_message(conversation.id, "user", message)

        # Fan-out/depth counters are per turn, and they live in a
        # thread-local that a pooled worker thread carries between
        # requests. Without this reset the "6 delegations per turn" cap
        # silently became "6 delegations per worker thread, ever", after
        # which the Coordinator stopped being able to delegate at all.
        if runtime.delegation_budget is not None:
            runtime.delegation_budget.reset()

        # Opened before the model is called, for the same reason the user's
        # message is persisted first: a run that dies should still be
        # findable. Everything below executes inside this scope, so every
        # audit event core writes -- including ones raised by a delegate on
        # a pool thread, which inherits the context -- carries this id.
        run = runtime.runs.start(
            goal=message, conversation_id=conversation.id, idempotency_key=idempotency_key,
        )
        run_token = set_current_run_id(run.id)

        # A turn that produced no answer must not be recorded as one that
        # did. This used to fall through to audit.record()'s default
        # outcome of "success" on every path, so a run that failed and a
        # run that worked were indistinguishable afterwards -- which makes
        # the audit log useless for deciding which runs are worth keeping.
        outcome = "success"
        error_detail: str | None = None
        completed_tool_calls: list[str] = []
        provider_failures: list[dict] = []
        try:
            result = runtime.orchestrator.handle_message(
                role, message, history,
                relevant_memories=runtime.memory.recall(),
            )
            response_text = result.response.text
            model_used = (
                f"{result.response.provider}:{result.response.model_name}" if result.response.provider else None
            )
            # A turn that ran out of providers partway, or exhausted its
            # tool-call budget, produced real state changes but no final
            # answer. Recording either as a plain success would put runs
            # that half-happened in the same bucket as runs that worked.
            completed_tool_calls = list(result.completed_tool_calls)
            provider_failures = list(result.failed_provider_attempts)
            if result.status != "ok":
                outcome = "partial" if result.status == "partial" else "error"
                error_detail = f"turn ended {result.status}"
        except ModelUnavailableError as exc:
            response_text = _NO_MODEL_AVAILABLE_MESSAGE
            model_used = None
            outcome = "error"
            error_detail = str(exc)
        except Exception as exc:
            logger.exception("chat turn failed (agent=%s)", role.name)
            # Audited before raising: previously this path returned a 500
            # from above the audit call, so an execution that crashed left
            # the user's message stored with no record that a turn had even
            # been attempted.
            runtime.audit.record(
                actor=role.name, action="chat.turn", resource=conversation.id, outcome="error",
                metadata={"model_used": None, "error": type(exc).__name__},
            )
            runtime.runs.finish(run.id, status="error", error=type(exc).__name__)
            reset_current_run_id(run_token)
            raise HTTPException(status_code=500, detail="iV hit an internal error handling that message.")

        runtime.conversations.add_message(conversation.id, "iv", response_text, model_used=model_used)
        metadata: dict[str, object] = {"model_used": model_used}
        if error_detail is not None:
            metadata["error"] = error_detail
        if completed_tool_calls:
            metadata["completed_tool_calls"] = completed_tool_calls
        runtime.audit.record(
            actor=role.name, action="chat.turn", resource=conversation.id,
            outcome=outcome, metadata=metadata,
        )
        if provider_failures:
            metadata["failed_provider_attempts"] = provider_failures
        runtime.runs.finish(
            run.id, status=("ok" if outcome == "success" else outcome),
            model_used=model_used, error=error_detail,
            completed_tool_calls=completed_tool_calls,
            failed_provider_attempts=provider_failures,
        )
        reset_current_run_id(run_token)
        return ChatResponse(
            response=response_text, model_used=model_used,
            conversation_id=conversation.id, run_id=run.id,
        )

    @app.post(
        "/api/chat", response_model=ChatResponse,
        dependencies=[Depends(require_chat_secret), Depends(enforce_chat_rate_limit)],
    )
    def chat(chat_request: ChatRequest, runtime: ApiRuntime = Depends(get_runtime)):
        """The single user-facing conversational entry point. It always
        reaches the Coordinator — there is no way for a caller to name a
        sub-agent here. Whether Research, Engineering or Security does
        any of the work is the Coordinator's decision, made internally
        via delegate_to_agent, and the user sees one synthesized reply
        from iV either way."""
        role = _lookup_agent(runtime, COORDINATOR_KEY)
        if role is None:  # pragma: no cover - a build without the built-in roles
            raise HTTPException(status_code=500, detail="the coordinator role is not configured")
        return _run_turn(
            runtime, role, chat_request.message, chat_request.conversation_id,
            idempotency_key=chat_request.idempotency_key,
        )

    def _chess_game_out(game: ChessGame) -> ChessGameOut:
        return ChessGameOut(
            id=game.id, human_color=game.human_color, fen=game.fen,
            turn=chess_engine.side_to_move(game.fen), moves=game.moves,
            status=game.status, result=game.result, conversation_id=game.conversation_id,
        )

    def _run_iv_chess_turn(runtime: ApiRuntime, game: ChessGame) -> ChatResponse | None:
        """Prompts the Coordinator to take its turn in `game`, through the
        exact same turn machinery as a normal chat message (_run_turn) —
        so iV's move commentary is logged, audited, and readable later
        the same way any other reply is. Returns None if the game is
        already over or it isn't iV's move (nothing to do); otherwise the
        turn's ChatResponse, whether or not the model actually committed
        a move via make_chess_move — a model with no provider configured
        or one that only talks without moving still gets its reply shown,
        just with the board unchanged."""
        if game.status != "active" or chess_engine.side_to_move(game.fen) == game.human_color:
            return None
        role = _lookup_agent(runtime, COORDINATOR_KEY)
        if role is None:  # pragma: no cover - a build without the built-in roles
            return None
        prompt = (
            f"It's your move in chess game {game.id}. Call get_chess_board with this game_id to "
            "see the current position and every legal move, then play one with make_chess_move. "
            "For anything beyond a simple opening move, delegate to planning or research for "
            "candidate-move analysis first, then commit the move yourself."
        )
        return _run_turn(runtime, role, prompt, game.conversation_id)

    @app.post("/api/chess/games", response_model=ChessMoveResponse, dependencies=[Depends(require_chat_secret)])
    def create_chess_game(body: NewChessGameRequest, runtime: ApiRuntime = Depends(get_runtime)):
        """Starts a new game. A direct write of the owner's own data, same
        trust level as update_task_status below — not an agent action, so
        it bypasses the tool/orchestrator layer. (The Coordinator can also
        start a game itself via the start_chess_game tool, e.g. from a
        typed "let's play chess" in normal chat; this is the deterministic
        path the UI's "New game" button uses instead of depending on a
        model call to get the same effect.)"""
        human_color = body.human_color.strip().lower()
        if human_color not in ("white", "black"):
            raise HTTPException(status_code=400, detail="human_color must be 'white' or 'black'")
        conversation = runtime.conversations.create(
            title=f"Chess — you play {human_color}"
        )
        game = runtime.games.create(
            human_color=human_color, fen=chess_engine.new_game_fen(), conversation_id=conversation.id
        )
        turn_result = _run_iv_chess_turn(runtime, game)
        refreshed = runtime.games.get(game.id) or game
        return ChessMoveResponse(
            game=_chess_game_out(refreshed),
            iv_reply=turn_result.response if turn_result else None,
            model_used=turn_result.model_used if turn_result else None,
            conversation_id=conversation.id,
        )

    @app.get("/api/chess/games", response_model=list[ChessGameOut], dependencies=[Depends(require_chat_secret)])
    def list_chess_games(status: str | None = Query(default=None), runtime: ApiRuntime = Depends(get_runtime)):
        return [_chess_game_out(g) for g in runtime.games.list(status=status)]

    @app.get(
        "/api/chess/games/{game_id}", response_model=ChessGameOut, dependencies=[Depends(require_chat_secret)]
    )
    def get_chess_game(game_id: str, runtime: ApiRuntime = Depends(get_runtime)):
        game = runtime.games.get(game_id)
        if game is None:
            raise HTTPException(status_code=404, detail="no such chess game")
        return _chess_game_out(game)

    @app.post(
        "/api/chess/games/{game_id}/move",
        response_model=ChessMoveResponse,
        dependencies=[Depends(require_chat_secret), Depends(enforce_chat_rate_limit)],
    )
    def make_chess_move_route(
        game_id: str, body: ChessMoveRequest, runtime: ApiRuntime = Depends(get_runtime)
    ):
        """Applies the owner's own move directly (same "the owner acting
        on their own data needs no more gating than an agent doing the
        identical thing already has" reasoning as update_task_status),
        then — if the game continues and it's now iV's turn — runs iV's
        reply synchronously in this same request via _run_iv_chess_turn,
        so playing a move and seeing iV's answering move is one round
        trip instead of a separate "ask iV to move" step."""
        game = runtime.games.get(game_id)
        if game is None:
            raise HTTPException(status_code=404, detail="no such chess game")
        if game.status != "active":
            raise HTTPException(
                status_code=400, detail=f"this game already ended ({game.status}, result {game.result})"
            )
        if chess_engine.side_to_move(game.fen) != game.human_color:
            raise HTTPException(status_code=400, detail="it isn't your turn — waiting on iV's move")

        try:
            outcome = chess_engine.apply_move(game.fen, body.move)
        except chess_engine.IllegalMoveError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": str(exc), "legal_moves": exc.legal_moves},
            )

        game = runtime.games.record_move(
            game_id, fen=outcome["fen"], san=outcome["san"], status=outcome["status"], result=outcome["result"]
        )
        if game is None:  # pragma: no cover - the game was confirmed to exist a few lines up
            raise HTTPException(status_code=404, detail="no such chess game")
        turn_result = _run_iv_chess_turn(runtime, game)
        refreshed = runtime.games.get(game_id) or game
        return ChessMoveResponse(
            game=_chess_game_out(refreshed),
            iv_reply=turn_result.response if turn_result else None,
            model_used=turn_result.model_used if turn_result else None,
            conversation_id=game.conversation_id,
        )

    @app.post("/internal/agent-chat", response_model=ChatResponse)
    def agent_chat(
        chat_request: AgentChatRequest,
        x_internal_secret: str | None = Header(default=None),
        runtime: ApiRuntime = Depends(get_runtime),
    ):
        """Operator/debug path for talking to one agent directly, which
        /api/chat deliberately no longer allows. Gated on the internal
        secret rather than the chat secret, and not exposed by the
        frontend proxy's allowlist: addressing a specialist straight is
        an operator action, not a chat feature."""
        check_internal_secret(x_internal_secret)
        role = _lookup_agent(runtime, chat_request.role)
        if role is None:
            raise HTTPException(status_code=400, detail=f"unknown agent '{chat_request.role}'")
        return _run_turn(runtime, role, chat_request.message, chat_request.conversation_id)

    @app.post("/internal/sleep-cycle")
    def trigger_sleep_cycle(
        x_internal_secret: str | None = Header(default=None), runtime: ApiRuntime = Depends(get_runtime)
    ):
        check_internal_secret(x_internal_secret)
        settings = load_settings()
        return run_sleep_cycle(
            runtime,
            email_config=load_email_config(),
            dashboard_base_url=settings.sleep_cycle.base_url,
            api_access_secret=settings.api_access_secret,
        )

    @app.post("/internal/materialize-backlog")
    def trigger_materialize_backlog(
        x_internal_secret: str | None = Header(default=None), runtime: ApiRuntime = Depends(get_runtime)
    ):
        check_internal_secret(x_internal_secret)
        created = materialize_approved_action_items(
            approvals=runtime.approvals, projects=runtime.projects, tasks=runtime.tasks
        )
        return {"status": "ok", "tasks_created": len(created)}

    @app.get("/api/agents", dependencies=[Depends(require_chat_secret)])
    def list_agents(runtime: ApiRuntime = Depends(get_runtime)):
        """The roster: every agent the coordinator can route work to,
        built-in and composed, with the models and tools each holds.
        Behind the chat secret rather than open like /health — it is a map
        of what iV can do, which is worth withholding from anything that
        cannot already talk to it."""
        if runtime.catalog is None:
            return {"agents": []}
        return {"agents": runtime.catalog.list()}

    @app.post("/internal/self-audit")
    def trigger_self_audit(
        audit_request: SelfAuditRequest | None = None,
        x_internal_secret: str | None = Header(default=None),
        runtime: ApiRuntime = Depends(get_runtime),
    ):
        """Runs iV's self-audit: deterministic checks over its own source,
        plus a model review through the read-only auditor role when a
        provider is configured. Gated on the internal secret rather than
        the chat secret — it is an operator action, not a chat feature,
        and it writes to projects/tasks/approvals.

        Synchronous: the model review makes several tool-calling round
        trips and can take minutes. That is a known limitation recorded
        in docs/RUNTIME.md rather than a hidden one; a background job
        runner is the right fix and is out of scope for this first
        deployment."""
        check_internal_secret(x_internal_secret)
        if runtime.inspector is None:
            raise HTTPException(status_code=503, detail="self-inspection is not available in this runtime")
        options = audit_request or SelfAuditRequest()
        result = run_api_self_audit(runtime, use_model=options.use_model)
        runtime.audit.record(
            actor="Auditor", action="selfaudit.run", resource="agent-iv",
            metadata={"counts": result.counts, "model_review": result.model_review_status},
        )
        return result.to_dict()

    @app.get("/approvals/pending", response_class=HTMLResponse)
    def approvals_pending(
        secret: str | None = Query(default=None),
        iv_approvals_session: str | None = Cookie(default=None),
        runtime: ApiRuntime = Depends(get_runtime),
    ):
        """An emailed approval link necessarily carries the secret in its
        URL. On that first visit the secret is moved into an HttpOnly
        cookie and the browser is redirected to the same page without the
        query string — so it appears once in the logs and history instead
        of on every subsequent request and decision. See
        interfaces/api/security.resolve_approval_secret."""
        resolved, from_query = resolve_approval_secret(secret, iv_approvals_session)
        if from_query:
            redirect = RedirectResponse(url="/approvals/pending", status_code=303)
            redirect.set_cookie(
                APPROVAL_SESSION_COOKIE, resolved,
                httponly=True, samesite="lax", max_age=60 * 60 * 12, path="/approvals",
            )
            return redirect

        pending = runtime.approvals.list_pending()

        items_html = "".join(
            f"""
            <div style="background: #222; padding: 15px; margin-bottom: 10px; border-radius: 6px; border: 1px solid #444;">
                <p style="margin: 0 0 10px 0; color: #fff;"><strong>{html.escape(r.action_type)}</strong></p>
                <p style="margin: 0 0 10px 0; color: #ccc;">{html.escape(r.description)}</p>
                <p style="margin: 0 0 10px 0; font-size: 0.85rem; color: #888;">Requested by {html.escape(r.requested_by)} — {html.escape(r.created_at)} — risk: {html.escape(r.risk_level)}</p>
                <form method="post" action="/approvals/decide" style="display:inline">
                    <input type="hidden" name="id" value="{html.escape(r.id)}">
                    <input type="hidden" name="decision" value="approved">
                    <button type="submit" style="background: #28a745; color: white; border: none; padding: 10px 16px; min-height: 44px; border-radius: 4px; margin-right: 10px; margin-bottom: 8px; cursor: pointer; font-size: 1rem;">Approve</button>
                </form>
                <form method="post" action="/approvals/decide" style="display:inline">
                    <input type="hidden" name="id" value="{html.escape(r.id)}">
                    <input type="hidden" name="decision" value="denied">
                    <button type="submit" style="background: #dc3545; color: white; border: none; padding: 10px 16px; min-height: 44px; border-radius: 4px; margin-bottom: 8px; cursor: pointer; font-size: 1rem;">Deny</button>
                </form>
            </div>
            """
            for r in pending
        ) or "<p style='color: #888;'>No pending approvals right now.</p>"

        bulk_html = "" if not pending else f"""
        <div style="margin-bottom: 20px;">
            <form method="post" action="/approvals/decide-all" style="display:inline">
                <input type="hidden" name="decision" value="approved">
                <button type="submit" style="background: #28a745; color: white; border: none; padding: 10px 18px; min-height: 44px; border-radius: 4px; margin-right: 10px; margin-bottom: 8px; cursor: pointer; font-size: 1rem;">Approve All</button>
            </form>
            <form method="post" action="/approvals/decide-all" style="display:inline">
                <input type="hidden" name="decision" value="denied">
                <button type="submit" style="background: #dc3545; color: white; border: none; padding: 10px 18px; min-height: 44px; border-radius: 4px; margin-bottom: 8px; cursor: pointer; font-size: 1rem;">Deny All</button>
            </form>
        </div>
        """

        body = f"""
        <html>
        <head><title>iV Pending Approvals</title><meta name="viewport" content="width=device-width, initial-scale=1"></head>
        <body style="background: #121212; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px; max-width: 600px; margin: auto;">
            <h2>iV Pending Approvals</h2>
            {bulk_html}
            {items_html}
        </body>
        </html>
        """
        return HTMLResponse(content=body)

    def _carry_out(runtime: ApiRuntime, approval_id: str) -> None:
        """Approving is the point at which the action happens. Deciding
        used to only move a row, so an approved request was never consumed
        and asking again just filed another one -- across the audit, seven
        approval rows accumulated and none ever reached `executed`.

        Failures are logged, not raised: the decision itself succeeded and
        is recorded, and the tool layer has already written its own audit
        entry for whatever went wrong. Turning that into a 500 would lose
        the decision the owner just made."""
        try:
            result = execute_approved_request(
                runtime.approvals.get(approval_id), orchestrator=runtime.orchestrator,
                catalog=runtime.catalog, approvals=runtime.approvals,
            )
        except ApprovalNotExecutable as exc:
            logger.warning("approved request %s could not be carried out: %s", approval_id, exc)
            return
        except Exception:  # noqa: BLE001 - a failed action must not lose the decision
            logger.exception("approved request %s raised while executing", approval_id)
            return
        if not result.success:
            logger.warning("approved request %s ran and failed: %s", approval_id, result.error)

    @app.post("/approvals/decide")
    def approvals_decide(
        id: str = Form(...), decision: str = Form(...), secret: str | None = Form(default=None),
        iv_approvals_session: str | None = Cookie(default=None),
        runtime: ApiRuntime = Depends(get_runtime),
    ):
        # The form field is still accepted so an existing bookmark or a
        # scripted caller keeps working; the browser path uses the cookie.
        resolve_approval_secret(secret, iv_approvals_session)
        if decision not in ("approved", "denied"):
            raise HTTPException(status_code=400, detail="decision must be 'approved' or 'denied'")
        try:
            runtime.approvals.decide(id, approved=(decision == "approved"), decided_by="owner")
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if decision == "approved":
            _carry_out(runtime, id)
        return RedirectResponse(url="/approvals/pending", status_code=303)

    @app.post("/approvals/decide-all")
    def approvals_decide_all(
        decision: str = Form(...), secret: str | None = Form(default=None),
        iv_approvals_session: str | None = Cookie(default=None),
        runtime: ApiRuntime = Depends(get_runtime),
    ):
        resolve_approval_secret(secret, iv_approvals_session)
        if decision not in ("approved", "denied"):
            raise HTTPException(status_code=400, detail="decision must be 'approved' or 'denied'")
        for request_row in runtime.approvals.list_pending():
            runtime.approvals.decide(request_row.id, approved=(decision == "approved"), decided_by="owner")
            if decision == "approved":
                _carry_out(runtime, request_row.id)
        return RedirectResponse(url="/approvals/pending", status_code=303)

    return app


app = create_app()
