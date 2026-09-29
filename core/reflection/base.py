"""Pure core logic for the nightly reflection cycle: gather today's
conversations + audit activity, ask a model to reflect on it, write a
reflective memory, and open one ApprovalRequest per proposed action item.
No email, no HTTP trigger, no scheduler — those are infrastructure
concerns (see the package docstring). run_reflection_cycle() only depends
on core abstractions, so it's testable with InMemoryStorage + a scripted
ModelProvider, same as everything else in core.

Migrated from backend/app/sleep_cycle.py, generalized from a hardcoded
Gemini-then-Mistral-then-Groq waterfall to any provider_order understood
by ModelRegistry.generate_with_fallback().
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.conversations.base import ConversationStore
from core.memory.base import MemoryStore, MemoryType
from core.models.base import ModelMessage, ModelRequest
from core.models.registry import ModelRegistry
from core.projects.base import ProjectStore
from core.tasks.base import Task, TaskStore

DEFAULT_PROVIDER_ORDER = ["gemini", "mistral", "groq"]
DEFAULT_BACKLOG_PROJECT_NAME = "iV Improvements"

REFLECTION_SYSTEM_PROMPT = """You are iV's Sleep Cycle — a nightly reflection
process. You are reviewing one day's worth of conversations and activity,
considering the perspective of every specialist role at once (Planning,
Engineering, Research, Writing, Memory, Finance, Security), plus the
Coordinator's cross-cutting view.

Review the raw day log provided and produce ONLY a JSON object (no
markdown fences, no commentary outside the JSON) with this exact shape:

{
  "narrative": "A few paragraphs of honest reflection: gaps, flaws,
    friction points, and trends noticed across the day, from a
    cross-role perspective.",
  "action_items": [
    {"title": "Short title", "description": "One or two sentences on
      the specific proposed change and why."}
  ]
}

Be specific and honest — vague praise is not useful here. If nothing
significant happened, say so plainly and keep action_items empty rather
than inventing busywork."""


@dataclass
class ReflectionResult:
    narrative: str
    action_items: list[dict] = field(default_factory=list)  # [{"id", "title", "description"}]


def build_day_log_text(messages, events) -> str:
    lines = ["=== Today's Messages ==="]
    for m in messages:
        lines.append(f"[{m.created_at}] {m.role}: {m.content}")

    lines.append("\n=== Today's Activity Log ===")
    for e in events:
        lines.append(f"[{e.timestamp}] {e.actor} -- {e.action} ({e.outcome}) on {e.resource}")

    if not messages and not events:
        lines.append("(No activity recorded today.)")

    return "\n".join(lines)


def parse_reflection(raw_text: str) -> dict:
    """Models sometimes wrap JSON in markdown fences despite
    instructions not to — strip those defensively. If parsing still
    fails, don't lose the run: wrap the raw text as a single manual-
    review action item instead of raising."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return {
            "narrative": raw_text,
            "action_items": [
                {
                    "title": "Review raw Sleep Cycle output manually",
                    "description": "The model's response wasn't valid JSON — "
                                    "see the narrative field above for the raw text.",
                }
            ],
        }


def start_of_today() -> datetime:
    return datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)


def run_reflection_cycle(
    *,
    conversations: ConversationStore,
    audit: AuditLog,
    memory: MemoryStore,
    approvals: ApprovalManager,
    models: ModelRegistry,
    provider_order: list[str] | None = None,
    since: datetime | None = None,
) -> ReflectionResult:
    """Raises ModelUnavailableError if every provider in provider_order
    fails — deliberately not caught here, since 'no model could review
    today' is exactly the kind of failure a caller (the HTTP trigger,
    which can send a failure notification) needs to see, not something
    this pure function should paper over."""
    since = since or start_of_today()
    since_iso = since.astimezone(timezone.utc).isoformat()

    messages = conversations.messages_since(since_iso)
    events = audit.since(since_iso)
    day_log_text = build_day_log_text(messages, events)

    request = ModelRequest(
        messages=[ModelMessage(role="user", content=day_log_text)],
        system_prompt=REFLECTION_SYSTEM_PROMPT,
    )
    response = models.generate_with_fallback(request, provider_order or DEFAULT_PROVIDER_ORDER)
    reflection = parse_reflection(response.text)

    narrative = reflection.get("narrative", "(no narrative returned)")
    raw_action_items = reflection.get("action_items", [])

    memory.add(narrative, memory_type=MemoryType.REFLECTIVE, importance=8, source="sleep_cycle")

    action_items = []
    for item in raw_action_items:
        title = item.get("title", "Untitled")
        description = item.get("description", "")
        approval = approvals.request(action_type=title, description=description, requested_by="iV Sleep Cycle")
        action_items.append({"id": approval.id, "title": title, "description": description})

    return ReflectionResult(narrative=narrative, action_items=action_items)


def materialize_approved_action_items(
    *,
    approvals: ApprovalManager,
    projects: ProjectStore,
    tasks: TaskStore,
    project_name: str = DEFAULT_BACKLOG_PROJECT_NAME,
    requested_by: str = "iV Sleep Cycle",
) -> list[Task]:
    """Turns action items a human has already approved (via
    /approvals/decide or the CLI, at any point after the reflection that
    proposed them) into real Task rows under one backlog project, instead
    of leaving "approved" as a status buried in the approvals table with
    nothing downstream reading it. Each request materialized this way is
    marked executed — ApprovalManager's existing terminal state for "this
    approved thing was actually carried out" — so calling this again
    only picks up newly-approved items, never re-creates the same task.

    This does not itself decide anything: every item it touches was
    already approved by a human through the normal approval flow. It's
    the "make the approval count for something" step, not a new
    authority.
    """
    project = _find_or_create_backlog_project(projects, project_name)

    created = []
    for request in approvals.list_approved(requested_by=requested_by):
        task = tasks.create(project.id, request.action_type, description=request.description)
        approvals.mark_executed(request.id)
        created.append(task)
    return created


def _find_or_create_backlog_project(projects: ProjectStore, name: str):
    for project in projects.list():
        if project.name == name:
            return project
    return projects.create(name, description="Backlog of approved Sleep Cycle proposals.")
