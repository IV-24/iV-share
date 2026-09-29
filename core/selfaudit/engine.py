"""Runs iV's self-audit and records the result where iV will see it again.

Order of operations, and why:

1. Deterministic checks run first (core/selfaudit/checks.py). They are
   cheap, they cannot hallucinate, and their output becomes the model's
   starting context — a reviewer who already knows twelve concrete facts
   about the codebase asks better questions than one starting cold.
2. The model layer runs second, as an ordinary AgentOrchestrator turn
   with the "auditor" role. That means it reaches iV's source through the
   same ToolRegistry, permission checks, and audit log as every other
   capability. The audit is not a privileged side channel: if the auditor
   role lacks self.inspect, the audit fails the same way any other
   unauthorized tool call fails.
3. Results are persisted through the stores iV already has — a reflective
   memory holding the summary, a project holding one task per
   CRITICAL/HIGH finding, and an improvement proposal (which opens an
   approval request) for the most severe finding. An audit whose output
   only exists in a chat reply is an audit nobody acts on.

The model layer is optional by construction. With no provider configured
run_self_audit() still returns the deterministic findings and says so in
`model_review_status`, rather than failing or — worse — returning an
empty audit that reads like a clean bill of health.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.agent.orchestrator import AgentOrchestrator
from core.agent.roles import AgentRole
from core.memory.base import MemoryStore, MemoryType
from core.models.base import ModelUnavailableError
from core.projects.base import ProjectStore
from core.selfaudit.checks import RepositoryReader, repository_overview, run_static_checks
from core.selfaudit.findings import (
    Classification,
    Confidence,
    Finding,
    Severity,
    severity_counts,
    sort_findings,
)
from core.tasks.base import TaskStore

logger = logging.getLogger(__name__)

AUDIT_PROJECT_PREFIX = "iV Self-Audit"

SELF_AUDIT_SYSTEM_PROMPT = """You are iV's self-audit: a senior software
engineering review team — a staff engineer, a security engineer, an SRE,
and a test lead — reviewing the codebase that runs you. You are reviewing
your own runtime. Be direct and unsentimental about it.

You have read-only tools for inspecting the repository (self_list_files,
self_read_file, self_search_repository, self_inspect_dependencies,
self_inspect_configuration, self_list_tests, self_read_logs, self_git).
USE THEM. Read the actual implementation of anything you intend to
comment on. A finding you did not read the code for is not a finding.

Review these areas:
  Architecture   - inconsistency, unnecessary complexity, coupling,
                   duplicated functionality, fragile assumptions, poor
                   separation of concerns.
  Reliability    - crashes, unhandled exceptions, race conditions, retry
                   behavior, broken state handling, persistence,
                   startup/shutdown failures.
  Security       - exposed secrets, unsafe endpoints, authentication and
                   authorization gaps, command execution, path traversal,
                   injection, overly broad tool permissions.
  Agent safety   - unrestricted tool access, unsafe autonomous behavior,
                   missing approval boundaries, destructive actions,
                   prompt-injection exposure, model/provider trust
                   boundaries.
  Model harness  - provider failures, fallback behavior, malformed
                   responses, timeouts, token and API errors, secret
                   leakage into logs.
  Frontend       - API failures, state handling, auth, CORS, connection
                   failures, mobile usability.
  Testing        - missing tests, weak tests, untested critical paths,
                   tests that assert nothing meaningful.
  Documentation  - places where documentation disagrees with the code.

Rules:
  - Do NOT manufacture findings. If an area is genuinely fine, say so and
    move on. A short honest audit beats a long padded one.
  - Every finding needs evidence you actually observed: a file path with
    a line number and the relevant source, or the literal output of a
    tool call.
  - You are given a list of findings already established by deterministic
    checks. Do not repeat them. Go deeper: the things a regular
    expression cannot see.

Reply with ONLY a JSON object, no markdown fences and no prose outside it:

{
  "summary": "2-4 sentences: the honest state of this system.",
  "findings": [
    {
      "severity": "CRITICAL|HIGH|MEDIUM|LOW|INFO",
      "category": "architecture|reliability|security|agent-safety|model-harness|frontend|testing|documentation",
      "location": "path/to/file.py:123",
      "problem": "One sentence.",
      "evidence": "What you actually read or ran, quoted.",
      "why_it_matters": "The consequence if this is not fixed.",
      "recommended_fix": "The specific change.",
      "confidence": "high|medium|low",
      "classification": "CONFIRMED BUG|LIKELY BUG|POTENTIAL RISK|IMPROVEMENT"
    }
  ]
}"""


@dataclass
class SelfAuditResult:
    started_at: str
    finished_at: str
    findings: list[Finding] = field(default_factory=list)
    summary: str = ""
    model_review_status: str = "not_attempted"  # "ok" | "unavailable" | "unparseable" | "not_attempted"
    model_used: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    project_id: str | None = None
    memory_id: str | None = None
    task_ids: list[str] = field(default_factory=list)
    improvement_id: str | None = None
    approval_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": self.summary,
            "counts": self.counts,
            "model_review_status": self.model_review_status,
            "model_used": self.model_used,
            "project_id": self.project_id,
            "memory_id": self.memory_id,
            "task_ids": list(self.task_ids),
            "improvement_id": self.improvement_id,
            "approval_id": self.approval_id,
            "findings": [f.to_dict() for f in self.findings],
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_model_findings(raw_text: str) -> tuple[str, list[Finding], str]:
    """Returns (summary, findings, status). Tolerates markdown fences and
    leading prose — models add both despite instructions, and losing a
    whole review to a stray ``` would be a poor trade. Anything that still
    won't parse is reported as unparseable rather than silently dropped,
    so a broken review is visible as a broken review."""
    cleaned = (raw_text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    else:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            cleaned = cleaned[start : end + 1]

    try:
        payload = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return "", [], "unparseable"
    if not isinstance(payload, dict):
        return "", [], "unparseable"

    findings = []
    for index, item in enumerate(payload.get("findings", []) or [], start=1):
        if not isinstance(item, dict):
            continue
        findings.append(Finding(
            id=f"MODEL-{index:02d}",
            severity=_coerce(Severity, item.get("severity"), Severity.MEDIUM),
            category=str(item.get("category", "unspecified")),
            location=str(item.get("location", "unspecified")),
            problem=str(item.get("problem", "")).strip() or "(no problem statement returned)",
            evidence=str(item.get("evidence", "")).strip() or "(no evidence returned)",
            why_it_matters=str(item.get("why_it_matters", "")).strip(),
            recommended_fix=str(item.get("recommended_fix", "")).strip(),
            confidence=_coerce(Confidence, item.get("confidence"), Confidence.LOW),
            classification=_coerce(Classification, item.get("classification"), Classification.POTENTIAL_RISK),
            source="model",
        ))
    return str(payload.get("summary", "")).strip(), findings, "ok"


def _coerce(enum_cls, value, default):
    if value is None:
        return default
    try:
        return enum_cls(str(value).strip().upper() if enum_cls is Severity else str(value).strip())
    except ValueError:
        pass
    try:
        return enum_cls(str(value).strip().lower())
    except ValueError:
        return default


def _static_summary(findings: list[Finding], counts: dict[str, int]) -> str:
    if not findings:
        return "Deterministic checks found no issues. No model review was available for this run."
    worst = ", ".join(f"{count} {severity}" for severity, count in counts.items() if count)
    return (
        f"Deterministic self-audit completed: {len(findings)} finding(s) ({worst}). "
        "Every finding below is reproducible from the repository — no model review contributed to this run."
    )


def run_self_audit(
    *,
    reader: RepositoryReader,
    orchestrator: AgentOrchestrator | None = None,
    role: AgentRole | None = None,
    memory: MemoryStore | None = None,
    projects: ProjectStore | None = None,
    tasks: TaskStore | None = None,
    improvements=None,
    use_model: bool = True,
    max_tool_iterations: int = 12,
) -> SelfAuditResult:
    started_at = _now()
    findings = run_static_checks(reader)
    summary = ""
    status = "not_attempted"
    model_used = None

    if use_model and orchestrator is not None and role is not None:
        summary, model_findings, status, model_used = _run_model_review(
            orchestrator, role, reader, findings, max_tool_iterations
        )
        findings.extend(model_findings)

    findings = sort_findings(findings)
    counts = severity_counts(findings)
    if not summary:
        summary = _static_summary(findings, counts)

    result = SelfAuditResult(
        started_at=started_at,
        finished_at=_now(),
        findings=findings,
        summary=summary,
        model_review_status=status,
        model_used=model_used,
        counts=counts,
    )
    _persist(result, memory=memory, projects=projects, tasks=tasks, improvements=improvements)
    return result


def _run_model_review(
    orchestrator: AgentOrchestrator,
    role: AgentRole,
    reader: RepositoryReader,
    static_findings: list[Finding],
    max_tool_iterations: int,
) -> tuple[str, list[Finding], str, str | None]:
    overview = repository_overview(reader)
    already_found = "\n".join(
        f"- [{f.severity.value}] {f.location}: {f.problem}" for f in static_findings
    ) or "(none)"

    prompt = (
        "Audit the iV codebase you are running inside.\n\n"
        f"Current branch: {overview.get('git_branch') or 'unknown'}\n"
        f"Python modules ({len(overview.get('python_modules', []))}):\n"
        + "\n".join(f"  {p}" for p in overview.get("python_modules", [])[:120])
        + f"\n\nTest files ({len(overview.get('test_files', []))}):\n"
        + "\n".join(f"  {p}" for p in overview.get("test_files", [])[:60])
        + "\n\nFindings already established by deterministic checks (do NOT repeat these):\n"
        + already_found
        + "\n\nRead the code with your tools, then reply with the JSON object described in your instructions."
    )

    audit_role = role
    if role.description != SELF_AUDIT_SYSTEM_PROMPT:
        from dataclasses import replace

        audit_role = replace(role, description=SELF_AUDIT_SYSTEM_PROMPT)

    try:
        turn = orchestrator.handle_message(
            audit_role, prompt, [], max_tool_iterations=max_tool_iterations
        )
    except ModelUnavailableError as exc:
        logger.warning("self-audit model review unavailable: %s", exc)
        return "", [], "unavailable", None
    except Exception:  # noqa: BLE001 - a failed review must not lose the static findings
        logger.exception("self-audit model review failed")
        return "", [], "unavailable", None

    summary, findings, status = parse_model_findings(turn.response.text)
    model_used = (
        f"{turn.response.provider}:{turn.response.model_name}" if turn.response.provider else None
    )
    if status == "unparseable":
        logger.warning("self-audit model review returned unparseable output")
    return summary, findings, status, model_used


def _persist(result: SelfAuditResult, *, memory, projects, tasks, improvements) -> None:
    """Best-effort by design: a storage failure must not discard an audit
    that already ran. Whatever succeeded is recorded on the result, and
    the caller still gets the findings either way."""
    title = f"{AUDIT_PROJECT_PREFIX} — {result.started_at[:10]}"

    if memory is not None:
        try:
            entry = memory.add(
                _memory_body(result),
                memory_type=MemoryType.REFLECTIVE,
                title=title,
                importance=9,
                source="self_audit",
            )
            result.memory_id = entry.id
        except Exception:  # noqa: BLE001
            logger.exception("self-audit: failed to record memory")

    actionable = [f for f in result.findings if f.severity in (Severity.CRITICAL, Severity.HIGH)]
    if projects is not None and tasks is not None and actionable:
        try:
            project = projects.create(
                title,
                description=result.summary,
                status="active",
                priority=1,
            )
            result.project_id = project.id
            for finding in actionable:
                task = tasks.create(
                    project.id,
                    f"[{finding.severity.value}] {finding.problem}",
                    description=(
                        f"Location: {finding.location}\n\n"
                        f"Evidence:\n{finding.evidence}\n\n"
                        f"Why it matters: {finding.why_it_matters}\n\n"
                        f"Recommended fix: {finding.recommended_fix}\n\n"
                        f"Classification: {finding.classification.value} "
                        f"(confidence: {finding.confidence.value}, source: {finding.source})"
                    ),
                    priority=1 if finding.severity is Severity.CRITICAL else 2,
                    assigned_role="Engineering Specialist",
                )
                result.task_ids.append(task.id)
        except Exception:  # noqa: BLE001
            logger.exception("self-audit: failed to record project/tasks")

    if improvements is not None and actionable:
        try:
            worst = actionable[0]
            proposal = improvements.propose(
                agent_name="Auditor",
                problem_identified=f"[{worst.severity.value}] {worst.problem} ({worst.location})",
                proposed_change=worst.recommended_fix,
                affected_files=[part.strip() for part in worst.location.split(",") if part.strip()],
            )
            result.improvement_id = proposal.id
            result.approval_id = proposal.approval_id
        except Exception:  # noqa: BLE001
            logger.exception("self-audit: failed to record improvement proposal")


def _memory_body(result: SelfAuditResult) -> str:
    lines = [result.summary, "", "Findings:"]
    for finding in result.findings:
        lines.append(f"- [{finding.severity.value}] {finding.location}: {finding.problem}")
    return "\n".join(lines)


def render_markdown_report(result: SelfAuditResult) -> str:
    counts = result.counts or severity_counts(result.findings)
    lines = [
        "# iV Self-Audit Report",
        "",
        f"- **Started:** {result.started_at}",
        f"- **Finished:** {result.finished_at}",
        f"- **Model review:** {result.model_review_status}"
        + (f" ({result.model_used})" if result.model_used else ""),
        "",
        "## Severity counts",
        "",
        "| Severity | Count |",
        "| --- | --- |",
    ]
    lines += [f"| {severity} | {count} |" for severity, count in counts.items()]
    lines += ["", "## Summary", "", result.summary, "", "## Findings", ""]
    if not result.findings:
        lines.append("_No findings._")
    for finding in result.findings:
        lines.append(finding.to_markdown())
    return "\n".join(lines) + "\n"
