"""Shows what iV actually did, from the audit log:

    python -m interfaces.cli.activity [--since 2026-08-19] [--limit 50]

This exists because of a specific failure mode. A model can say "I've got
agents working on that in the background" as easily as it can say
anything else, and the sentence is indistinguishable from a true one.
iV's runtime has no background execution at all — no worker, no queue, no
thread; AgentOrchestrator.handle_message runs inside one HTTP request and
stops when it returns. So a claim like that is always confabulation, and
the only way to tell what happened is to look at the record rather than
the transcript.

Every tool invocation passes through ToolRegistry.execute(), which writes
an AuditEvent whether it succeeded, was denied, or errored. That log is
the ground truth: if an action is not in it, iV did not take it.

Deliberately read-only, and deliberately not built on build_runtime() —
that bootstraps permission grants on startup, which would write to the
database this command is supposed to be inspecting.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.configuration.settings import load_settings
from core.conversations.base import MESSAGES_COLLECTION
from core.improvements.base import ImprovementProposal
from core.improvements.manager import COLLECTION as IMPROVEMENTS_COLLECTION
from core.projects.base import ProjectStore
from core.storage.local import SqliteStorage
from core.tasks.base import TaskStore

TOOL_ACTION_PREFIX = "tool.execute:"


def _parse_since(value: str | None) -> str:
    if not value:
        return (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise SystemExit(f"--since must be an ISO date like 2026-08-19, got {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _heading(text: str) -> str:
    return f"\n{text}\n{'-' * len(text)}"


def build_report(storage, *, since_iso: str, limit: int) -> str:
    audit = AuditLog(storage)
    projects = ProjectStore(storage)
    tasks = TaskStore(storage)
    approvals = ApprovalManager(storage, audit)

    events = audit.since(since_iso)
    tool_events = [e for e in events if e.action.startswith(TOOL_ACTION_PREFIX)]
    chat_turns = [e for e in events if e.action == "chat.turn"]

    out = [f"iV activity since {since_iso}"]

    out.append(_heading("Real actions taken (tool executions)"))
    if not tool_events:
        out.append("  None. iV has not executed a single tool in this window.")
        out.append("  Anything it described doing was text, not action.")
    else:
        by_tool = Counter(
            (e.action[len(TOOL_ACTION_PREFIX):], e.outcome) for e in tool_events
        )
        for (tool, outcome), count in sorted(by_tool.items()):
            out.append(f"  {count:>4}x  {tool:<28} {outcome}")
        out.append("")
        # audit.since() returns oldest-first; a "most recent" list has to
        # reverse it, or it shows the start of the window instead of the end.
        out.append(f"  Most recent {min(limit, len(tool_events))}, newest first:")
        for event in sorted(tool_events, key=lambda e: e.timestamp, reverse=True)[:limit]:
            tool = event.action[len(TOOL_ACTION_PREFIX):]
            detail = ""
            if event.outcome != "success":
                detail = f"  <- {event.metadata.get('error') or event.metadata.get('reason') or ''}"
            out.append(f"    {event.timestamp}  {event.actor:<24} {tool}{detail}")

    out.append(_heading("Conversation turns"))
    out.append(f"  {len(chat_turns)} message(s) answered by a model.")
    models = Counter(e.metadata.get("model_used") or "(none reached)" for e in chat_turns)
    for model, count in models.most_common():
        out.append(f"    {count:>4}x  {model}")

    out.append(_heading("Projects and tasks"))
    all_projects = projects.list()
    if not all_projects:
        out.append("  No projects exist.")
    for project in all_projects:
        project_tasks = tasks.list_for_project(project.id)
        out.append(f"  {project.name}  [{project.status}]  ({len(project_tasks)} task(s))")
        for task in project_tasks:
            out.append(f"      - [{task.status:<11}] {task.title}")

    # The distinction that answers "did the work happen?": a task's status
    # is a value iV wrote into a row. Only update_task_status changes one
    # after creation, and that call is audited. No audit entry means the
    # status is whatever it was set to at creation — a label, not a
    # record of work performed.
    status_updates = [
        e for e in tool_events if e.action == f"{TOOL_ACTION_PREFIX}update_task_status"
    ]
    out.append("")
    out.append(f"  Task status changes actually performed: {len(status_updates)}")
    if not status_updates:
        out.append("    No task status was ever updated. Any task not in its")
        out.append("    creation-time status was not moved by iV.")

    out.append(_heading("Waiting on you (pending approvals)"))
    pending = approvals.list_pending()
    if not pending:
        out.append("  Nothing pending.")
    for request in pending:
        out.append(f"  [{request.risk_level:<8}] {request.action_type}: {request.description[:90]}")

    out.append(_heading("Improvement proposals"))
    # Read the collection directly: ImprovementManager has no list method,
    # and this command must not be the reason one gets added to a
    # write-capable manager just to satisfy a read-only report.
    proposals = [
        ImprovementProposal.from_record(r)
        for r in storage.query(IMPROVEMENTS_COLLECTION, order_by="created_at", descending=True)
    ]
    if not proposals:
        out.append("  None.")
    for proposal in proposals[:limit]:
        out.append(f"  [{proposal.status.value:<10}] {proposal.problem_identified[:90]}")

    out.append(_heading("What iV cannot do"))
    out.append("  This runtime has no background execution: no worker, no queue,")
    out.append("  no scheduled agent. iV acts only while answering a request, and")
    out.append("  stops when the reply is sent. If it said work was continuing in")
    out.append("  the background, that was not true — check the list above instead.")
    out.append("  (The one exception is the Sleep Cycle, which runs only if you")
    out.append("  installed its LaunchAgent; it appears here as its own audit entries.)")

    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show what iV actually did, from its audit log.")
    parser.add_argument("--since", help="ISO date, e.g. 2026-08-19. Defaults to 7 days ago.")
    parser.add_argument("--limit", type=int, default=30, help="Max rows per detailed list.")
    parser.add_argument("--json", action="store_true", help="Raw audit events as JSON.")
    args = parser.parse_args(argv)

    from interfaces.cli.main import load_environment

    load_environment()
    since_iso = _parse_since(args.since)
    storage = SqliteStorage(load_settings().storage.local_db_path)
    try:
        if args.json:
            events = AuditLog(storage).since(since_iso)
            print(json.dumps([e.__dict__ for e in events], indent=2, default=str))
        else:
            print(build_report(storage, since_iso=since_iso, limit=args.limit))
    finally:
        storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
