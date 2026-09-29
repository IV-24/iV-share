# agent-IV Architecture v0.2

*Revision note: v0.2 replaces the flat multi-agent diagram from v0.1 with the feudal hierarchy defined in `IV_CONSTITUTION.md`, and adds an honest "Current vs. Target" section so this document stops drifting from what's actually deployed.*

---

## Core Concept

agent-IV is a model-agnostic, hierarchically-organized digital executive assistant. A single sovereign coordinator (the King) delegates work down through Lords, Merchants, Doctors, Guards, and Serfs as project complexity demands — see `IV_CONSTITUTION.md` for the full definition of each tier.

---

## High-Level Architecture (Target)

```
Owner
  ↓
iV Interface (web/mobile, terminal)
  ↓
The King  ──────────────► Approval System (Guards)
  ↓
Lords (Planning, Engineering, Research, Writing,
       Memory, Finance, Security, Coordination)
  ↓
Merchants / Doctors / Guards / Serfs
  ↓
Tools + External APIs (GitHub, Supabase, web, etc.)
  ↓
Memory System
  ↓
Knowledge Base (Supabase)
```

---

## Current vs. Target

This section exists specifically so architecture docs don't silently diverge from the deployed code again, as happened between v0.1 and the actual repo.

**Currently implemented (as of this revision):**

See `docs/RUNTIME.md` for the operational picture; this list is the
architectural summary.

* `interfaces/api` (FastAPI) over `core/`: `/api/chat`, `/api/conversations/{id}`, `/health`, `/status`, `/approvals/*`, `/haven/manifest`, `/internal/sleep-cycle`, `/internal/materialize-backlog`, `/internal/self-audit`
* Eight roles (`core/agent/roles.py`) sharing one runtime — `AgentOrchestrator` runs a real tool-calling loop; roles differ only in configuration (tools, scopes, provider order), not in code paths
* Five model providers behind one provider-neutral interface (`core/models/`), selected per role with fallback; a missing API key means "skip this provider", not an error
* Persistent local SQLite (`core/storage/local.py`) for conversations, messages, memories, projects, tasks, approvals, improvements, reflections, permission grants, and the audit log — no hosted database
* Approval enforcement is real and in code: `ToolRegistry.execute()` is the only path to running a tool, and it blocks a `REQUIRES_APPROVAL` tool until a human has decided
* Repo-editing tools sandboxed to `IV_WORKSPACE_ROOT` (clone/read/write/commit auto; run-command, push, and pull-request require approval), plus protected-branch refusal (`core/safety/branches.py`)
* Read-only self-inspection of iV's own source (`adapters/selfinspect/`) and a two-layer self-audit (`core/selfaudit/`) — see `docs/SELF_AUDIT.md`
* GitHub integration limited to opening a pull request, approval-gated

**Target for Phase 1 (this build):**

* King gains a broader toolset including writes (`create_task`, `create_project`, `create_approval`, `update_task_status`)
* Actions marked consequential per the Constitution create a pending-approval record rather than executing directly
* GitHub tools (read + write, repo-scoped) so the King can see and act on its own repository
* A basic tool-creation capability: the King can draft a new tool (as a proposed code change + test), submit it through the Self-Improvement System, and — once approved — it becomes available to itself or a Lord
* Still single-model at this phase; the hierarchy is reasoned about internally by one model rather than dispatched to separate running agents. Real per-tier model dispatch (see `MODEL_STRATEGY.md`) is a later phase.

---

## Core Components

### The King

Central coordinating intelligence. Understands the owner's goals, delegates internally, maintains alignment with the Constitution, and is the only tier with standing authority to escalate to the owner.

### Lords

Domain rulers, each owning a defined responsibility area (Planning, Engineering, Research, Writing, Memory, Finance, Security, Coordination). Lords persist across a project's lifetime and may raise lower tiers as needed. In Phase 1, Lords are represented as internal reasoning modes within the single King model call, not separate processes.

### Merchants, Doctors, Guards, Serfs

Task-scoped or project-scoped workers beneath a Lord — see `IV_CONSTITUTION.md` for full definitions. Not yet independently implemented; represented in Phase 1 as tool calls and prompt-level role-taking within the King's single reasoning pass.

---

## Memory Layers

**Working Memory** — Current conversation context.

**Episodic Memory** — Past interactions and events.

**Semantic Memory** — Facts and knowledge.

**Reflective Memory** — Lessons learned and improvements.

Currently, the `memories` table stores episodic/semantic/reflective content by `type`, and a separate `reflections` table exists alongside it — the exact division of labor between these two tables should be finalized during Phase 1 memory-tooling work rather than left ambiguous.

---

## Development Safety

The King may:

* Create proposals
* Generate code
* Create test branches
* Run evaluations
* Propose new tools for itself or a Lord

The King may not:

* Modify production without approval
* Expand scope without approval
* Execute restricted actions independently
* Grant a lower tier authority the King itself was not delegated

---

## Hosting & Access

* Backend runs persistently on the owner's Big Sur MacBook Pro (always-on host), not a cloud provider.
* Remote access (mobile and otherwise) is via Tailscale, chosen over ngrok for its always-on mesh VPN behavior and device-level authentication, avoiding session-based tunnel resets.
* Cloud dependencies are limited to API usage (model providers) and Supabase (structured storage beyond flat files/SQLite); both are chosen to stay within free tiers.

---

## Related Documents

* `IV_CONSTITUTION.md` — governing principles and full hierarchy definition
* `MODEL_STRATEGY.md` — model routing philosophy and current lineup
* `DATA_MODEL.md` — Supabase schema
* `AGENT_ROLES.md` — per-tier responsibility detail
* `ROADMAP.md` — build sequence
