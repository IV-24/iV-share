from pydantic import BaseModel


class ChatRequest(BaseModel):
    """The user-facing chat request. Deliberately has no `role` field:
    /api/chat always reaches the Coordinator, which decides internally
    whether to delegate. Letting a caller name a sub-agent here made the
    Master Agent bypassable by anything that could reach the frontend
    proxy (which forwards the body verbatim), which is the one property
    this architecture is supposed to guarantee. Operator-level direct
    addressing still exists — see AgentChatRequest and
    POST /internal/agent-chat, gated on the internal secret."""

    message: str
    conversation_id: str | None = None
    # Optional dedupe handle. A caller that retries a timed-out request has
    # no other way to say "this is the same submission" -- and without one,
    # the retry does the work a second time, side effects included.
    idempotency_key: str | None = None


class AgentChatRequest(ChatRequest):
    """Operator/debug only: addresses one agent directly, bypassing the
    Coordinator. Reachable solely through /internal/agent-chat, which the
    frontend proxy does not expose."""

    role: str = "coordinator"  # a key in core.agent.roles.DEFAULT_ROLES, or a composed agent


class ChatResponse(BaseModel):
    response: str
    model_used: str | None = None
    conversation_id: str
    # The handle for this one execution. A conversation_id identifies a
    # thread and is reused across turns; a caller needing to refer to the
    # work it just asked for -- to correlate it, retrieve its trace, or
    # decide later whether it was any good -- needs an id for the turn.
    run_id: str | None = None


class HistoryMessage(BaseModel):
    role: str
    content: str
    model_used: str | None = None
    created_at: str


class ConversationHistoryResponse(BaseModel):
    conversation_id: str
    title: str
    messages: list[HistoryMessage]


class SelfAuditRequest(BaseModel):
    """use_model=False runs only the deterministic checks — useful when
    no provider is configured, or to get a fast, reproducible pass."""

    use_model: bool = True


class ProjectOut(BaseModel):
    """Read shape for the project sidebar. A direct read of the owner's
    own project/task data through the same chat secret every other
    frontend request already uses — not an agent action, so it bypasses
    the orchestrator/tool layer entirely rather than routing through a
    role that would need database.read granted to some principal."""

    id: str
    name: str
    description: str
    status: str
    priority: int
    created_at: str


class TaskOut(BaseModel):
    id: str
    project_id: str
    title: str
    description: str
    status: str
    priority: int
    assigned_role: str | None = None
    created_at: str


class TaskStatusUpdateRequest(BaseModel):
    status: str


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: str


class ChessGameOut(BaseModel):
    """Read shape for a chess game. `turn` is derived from the FEN at
    response time rather than stored -- it's always implied by whose move
    the position is, so storing it separately would just be one more
    place for it to drift out of sync with the actual board."""

    id: str
    human_color: str
    fen: str
    turn: str
    moves: list[str]
    status: str
    result: str | None = None
    conversation_id: str | None = None


class NewChessGameRequest(BaseModel):
    human_color: str = "white"


class ChessMoveRequest(BaseModel):
    move: str


class ChessMoveResponse(BaseModel):
    """After the owner's move is applied, iV's reply (if the game didn't
    just end and it's now iV's turn) runs synchronously in the same
    request -- see interfaces/api/main.py's chess_move route. iv_reply is
    None when the game ended on the owner's move, or when a model turn
    ran but produced no committed move (e.g. no provider configured)."""

    game: ChessGameOut
    iv_reply: str | None = None
    model_used: str | None = None
    conversation_id: str


class RunStep(BaseModel):
    at: str
    actor: str
    action: str
    resource: str
    outcome: str
    metadata: dict = {}


class RunOut(BaseModel):
    """One execution, reconstructed. `steps` are the audit events raised
    during it, selected by run_id rather than by a timestamp window -- so
    this returns exactly this run's steps even when others overlapped it."""

    id: str
    goal: str
    status: str
    started_at: str
    ended_at: str | None = None
    caller: str = "owner"
    conversation_id: str | None = None
    parent_run_id: str | None = None
    model_used: str | None = None
    error: str | None = None
    completed_tool_calls: list[str] = []
    failed_provider_attempts: list[dict] = []
    steps: list[RunStep] = []
