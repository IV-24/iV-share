# iV Core Architecture

This document describes `core/` — the portable agent runtime — and how it
relates to the rest of the repository. See `docs/MIGRATION_AUDIT.md` for
the audit that motivated this design and the migration's history, and
`docs/HAVEN.md` for the permission/tool/approval boundary as one named
concept (agency levels, the Guardian role) rather than three separate
modules.
`backend/app` (the original Supabase-backed FastAPI app) has been fully
replaced and removed — `interfaces/api/` is what actually runs now.

---

## Design rule

**`core/` never imports Supabase, GitHub, Vercel, Tailscale, a specific
LLM SDK, or anything OS-specific.** Every file under `core/` uses only the
Python standard library and other `core/` modules. Infrastructure lives in
`adapters/` (implementations of core's interfaces for a specific
provider/database) and `interfaces/` (how something external — an HTTP
API, a CLI — talks to core). Dependencies flow one way:

```
adapters/  ──┐
interfaces/ ─┼──►  core/
tests/      ──┘
```

You can verify this holds at any time:

```bash
grep -rn "^import \|^from " core/ | grep -v "^core/.*from core\."
# should only show stdlib imports (dataclasses, enum, abc, typing, os, ...)
```

---

## Module map

| Module | Responsibility |
|---|---|
| `core/storage` | `StorageBackend` interface (`insert`/`get`/`update`/`query`/`delete` over named collections) plus two concrete backends: `InMemoryStorage` (tests, zero I/O) and `SqliteStorage` (the one storage backend iV actually runs on — a local file it owns and operates fully; no hosted database, no Supabase). |
| `core/models` | `ModelProvider` interface + `ModelRegistry`. `ModelMessage`/`ModelRequest`/`ModelResponse` are the provider-neutral shapes every adapter translates to/from. `ModelRegistry.generate_with_fallback()` walks a provider order and catches `ModelUnavailableError`, same reliability shape as the old per-call-site fallback loops, now in one place. `NullModelProvider` is a zero-dependency provider for tests/offline use. |
| `core/memory` | `MemoryStore`, distinguishing episodic / semantic / reflective memory, built on `StorageBackend`. |
| `core/conversations` | `ConversationStore` — conversation + message history. Single-user (no `profile_id`/`user_id`) by design; see `docs/MIGRATION_AUDIT.md` §10.5. `messages_since()` spans every conversation for a given cutoff, used by `core/reflection`. |
| `core/projects` / `core/tasks` | `ProjectStore` / `TaskStore` — real-world objective tracking and the tasks under them. |
| `core/reflection` | `run_reflection_cycle()` — the nightly Sleep Cycle's actual logic: gather a day's messages (`ConversationStore.messages_since()`) and activity (`AuditLog.since()`), ask a model to reflect, write a reflective memory, open one `ApprovalRequest` per proposed action item. No email, no HTTP trigger, no scheduler — purely core, testable with a scripted `ModelProvider` like everything else. `materialize_approved_action_items()` is the follow-up step: once a human has approved a proposal (same day or any later day, via `/approvals/decide`), it becomes a real `Task` under an "iV Improvements" project — `ApprovalManager.list_approved()` finds approved-but-not-yet-`mark_executed()`'d requests, so calling it again never double-creates. See `interfaces/api/sleep_cycle.py` for the infrastructure side. |
| `core/haven` | `build_haven_manifest()` — a read-only snapshot of `core/environment` + `core/tools.list_tools()` + `core/permissions.list_grants()` in one place. No new capability, just naming the boundary those three already enforce as one inspectable thing. See `docs/HAVEN.md`. |
| `core/guardian` | `find_denied_action_spikes()` (flags a principal with an unusual number of recent denied actions) and `suspend_principal()` (immediately revokes every scope a principal holds — an emergency stop, not approval-gated, but unconditionally audited). Exposed as tools in `core/tools/guardian.py`, reachable only by the Security specialist role (the only default role holding `permissions.manage`). |
| `core/context` | `ContextManager` assembles a bounded message window (+ relevant memories) for a request — separate from long-term memory storage. |
| `core/tools` | `ToolDefinition` (name, description, input/output schema, required permissions, risk level, execution policy) + `ToolRegistry`. `ToolRegistry.execute()` is the **only** way a tool runs — it checks permissions, and for `REQUIRES_APPROVAL` tools, checks for an approved `ApprovalRequest`, before ever calling the handler. `core/tools/standard.py`'s `register_standard_tools()` wires the actual tool set (projects, tasks, memory, approvals, improvements) — replaces `backend/app/tools/actions.py` + `tools/memory.py`. |
| `core/permissions` | `PermissionScope` (e.g. `database.write`, `financial.execute`) + `PermissionManager`: explicit grant/revoke, `is_granted()`/`require()` checks, every mutation audited. Nothing gets a capability because the process happens to have it — only because something granted the scope. `core/permissions/levels.py` adds `AgencyLevel` — a named bundle of scopes (`observer`/`researcher`/`builder`/`collaborator`) derived from a principal's current grants, never stored separately. Raising a principal's level goes through `core/approvals` exactly like anything else consequential — nothing self-grants a higher level. |
| `core/approvals` | `ApprovalRequest` lifecycle: `requested → approved/denied → executed/failed`, plus `revoked`/`expired`. This is a real gate `ToolRegistry` and `ImprovementManager` check — not a database row nobody reads. |
| `core/agent` | `AgentRole` (system prompt + `tool_names` + `permission_scopes` + `model_provider_order`) and `AgentOrchestrator`. The old feudal hierarchy (King/Lords/Merchants/Doctors/Guards/Serfs) is retired — `core/agent/roles.py` now uses Coordinator/Specialists/Gatherers/Investigators/Workers (full mapping in that file's docstring). Roles are data, not separate processes — the orchestrator is identical for every role. `AgentOrchestrator.handle_message()` runs the actual tool-calling loop: if a model response carries `tool_calls`, it executes each through `ToolRegistry` (permission/approval-checked) and feeds the results back, repeating until a final text answer or `max_tool_iterations` is hit. |
| `core/environment` | `discover()` — read-only: OS, Python version, whether cwd looks like a git repo, which known env var *names* are set (never values). Produces an `EnvironmentManifest` meant to be safe to show a human, unconditionally. |
| `core/extensions` | `ExtensionManifest` (id, version, capabilities, permissions, dependencies, risk, entry point) + `ExtensionRegistry`. Registering an extension validates and records its manifest only — nothing here loads or executes extension code. |
| `core/improvements` | `ImprovementProposal` lifecycle. `propose()` immediately opens a linked `ApprovalRequest`; `deploy()` raises `PermissionError` unless that request is approved. This is the code-level enforcement of "iV must never silently modify production/main code." |
| `core/audit` | `AuditLog.record()` — append-only. `PermissionManager`, `ApprovalManager`, and `ToolRegistry` all write here on every mutation/execution. |
| `core/configuration` | `load_settings()` reads env vars into grouped, typed config (`ModelConfig`, `StorageConfig`, `WorkspaceConfig`, ...). `StorageConfig` has no provider-specific fields — just a backend name and a local file path — since local SQLite is the only backend core knows about. `WorkspaceConfig` is just as thin: a `workspace_root` path, no GitHub credential (that's `adapters/workspace/config.py`'s job, same reasoning as email's `GMAIL_APP_PASSWORD` living in its own adapter). An unset model key is `None`, not an error — it's up to the adapter/entrypoint that actually needs it to decide that's a problem. |

## Adapters (infrastructure-specific, implement core's interfaces)

| Path | What it does |
|---|---|
| `adapters/models/openai_compatible.py` | Shared translation layer (message/tool schema shapes, `tool_calls` parsing) for any OpenAI-compatible chat completions API. `OpenAICompatibleProvider` is a base class — Groq and Mistral are ~10-line subclasses of it. |
| `adapters/models/groq_provider.py` / `mistral_provider.py` | `OpenAICompatibleProvider` subclasses pointed at Groq's / Mistral's endpoints. |
| `adapters/models/gemini_provider.py` | `ModelProvider` using google-genai's manual (non-automatic) function calling — `FunctionDeclaration`/`Tool` objects built from core's tool schemas, not Python callables handed to the SDK, since execution has to go through `ToolRegistry`. Field names (`parameters_json_schema`, `FunctionCall.id/args/name`, `Part.from_function_response`, role `"tool"`) were checked against the actual SDK source, not assumed. |
| `adapters/models/claude_provider.py` | `ModelProvider` using Anthropic's Messages API — `tool_use`/`tool_result` content blocks instead of a separate tool message role, so it has its own translation layer rather than sharing `openai_compatible.py`. Batches consecutive tool results into one user turn, since Anthropic requires all of a turn's results to arrive together. |
| `adapters/models/__init__.py`'s `register_configured_providers()` | Registers a provider for every configured API key, shared by `interfaces/api` and `interfaces/cli` so both agree on availability from one place instead of duplicating four if-blocks each. |
| `adapters/notifications/email.py` | Gmail SMTP notifications for the Sleep Cycle. `EmailConfig` + `send_email()` — a caller never touches `smtplib` directly. No-ops (doesn't raise) when unconfigured, same as the original. |
| `adapters/workspace/` | Repo-editing tools: `sandbox.py` (path-safety trust boundary — every path is confined to a configurable workspace root, rejecting absolute paths, `..` traversal, symlink escapes, and git-flag-shaped values), `git_ops.py` (clone/status/diff/commit/branch/push via subprocess argv lists, never `shell=True`), `files.py` (sandboxed read/write/list), `shell.py` (arbitrary command execution, sandboxed to a repo's directory, no real shell involved), `github.py` (PR creation via the GitHub REST API over `urllib`, stdlib only). `tools.py`'s `register_workspace_tools()` wires all of it into `ToolRegistry` with risk tiers — see its module docstring for the reasoning per tool. Only the Engineering role has these tools by default. |

## Interfaces (thin, replaceable ways to talk to core)

| Path | What it does |
|---|---|
| `interfaces/cli/main.py` | A REPL that assembles the full core stack with zero web layer — proof the API surface is UI-agnostic, not just a design claim. Run with `python -m interfaces.cli.main [role]`. Falls back to `NullModelProvider` when no provider API keys are configured, so it always works offline. Type `/haven` in the REPL for the current manifest. |
| `interfaces/api/main.py` | `POST /api/chat`, `GET /haven/manifest`, `GET /approvals/pending`, `POST /approvals/decide`, `POST /approvals/decide-all`, `POST /internal/sleep-cycle`, `POST /internal/materialize-backlog` — all backed by `core` + local SQLite. `create_app(runtime_factory=...)` is a factory, not a bare module-level app-with-side-effects: importing the module does no I/O, constructing the default `app` does no I/O either — the actual `ApiRuntime` (SQLite connection, providers, tool registry) is only built when the app's lifespan actually starts (real serving, or a test using `TestClient` as a context manager). |
| `interfaces/api/runtime.py` | Where the `ApiRuntime` wiring happens — storage, permissions, approvals, tools, every configured model provider, the orchestrator. |
| `interfaces/api/security.py` | Constant-time secret checks (`hmac.compare_digest`) for both `API_ACCESS_SECRET` and `INTERNAL_TRIGGER_SECRET`. |
| `interfaces/api/sleep_cycle.py` | Wires `core.reflection.run_reflection_cycle()` to email notification — builds the notification HTML, decides when to send it, sends a failure email if the cycle itself fails. The only non-core part of the Sleep Cycle. |

`run.py` launches `interfaces.api.main:app`. `backend/app` (the original
Supabase-backed FastAPI app) has been deleted — every route and
capability it had is now in `interfaces/api` + `core`, and nothing in the
repository imports it anymore. `backend/requirements.txt` no longer lists
`supabase`; `scripts/migrate_supabase_to_sqlite.py` (still useful if you
ever need to re-export from a live Supabase project) installs it
separately, on demand.

---

## How to extend it

**Add a tool.** Define a `ToolDefinition` (schema, `required_permissions`,
`risk_level`, `execution_policy`) and `registry.register(tool)`. If the
tool does anything consequential, set `execution_policy=REQUIRES_APPROVAL`
— `ToolRegistry.execute()` will create an `ApprovalRequest` the first time
it's called and refuse to run the handler until that request is approved.

**Add a specialized agent.** Add an `AgentRole` to
`core/agent/roles.py` (or register one at runtime) with its own
`tool_names`, `permission_scopes`, and `model_provider_order`. No runtime
code changes — `AgentOrchestrator` and `ToolRegistry` are role-agnostic.

**Add a model provider.** Implement `core.models.base.ModelProvider`
(`list_models()`, `generate()`) in `adapters/models/`, translating that
provider's native request/response shape to/from `ModelRequest`/
`ModelResponse`. If it speaks the OpenAI chat-completions wire format
(most do), subclass `adapters/models/openai_compatible.py`'s
`OpenAICompatibleProvider` instead of starting from scratch — see
`groq_provider.py`/`mistral_provider.py` for a ~10-line example. Register
it in `adapters/models/__init__.py`'s `register_configured_providers()`
so both `interfaces/api` and `interfaces/cli` pick it up automatically
once its API key is configured, and add its name to any
`AgentRole.model_provider_order` that should use it.

**Add a storage backend.** Implement `core.storage.base.StorageBackend`'s
five methods in `adapters/storage/`. Every other `core/` module (memory,
permissions, approvals, audit, improvements) already works against the
interface, not a specific backend — swapping `SqliteStorage` for a new
adapter requires no changes anywhere else.

**Add an extension.** Write an `ExtensionManifest` describing identity,
version, capabilities, permissions, dependencies, and risk. Register it
with `ExtensionRegistry` for review. Loading/executing extension code —
and scanning it for risk before that's allowed — is intentionally not
built yet (see `docs/MIGRATION_AUDIT.md` §"Extensions").

---

## Security model in practice

Permission and approval enforcement live in code, not just in prompt
text — `tests/core/test_tools.py` asserts this directly:

- A tool call without the required permission scope **never calls the
  handler** (`test_execute_without_permission_is_denied_and_does_not_run_handler`).
- A `REQUIRES_APPROVAL` tool call **never calls the handler** without an
  approved `ApprovalRequest`, even if the caller has every permission
  scope it needs (`test_requires_approval_tool_blocks_without_approval_even_with_permission`).
- Denying the approval keeps it blocked permanently, not just until retry
  (`test_requires_approval_tool_still_blocked_if_approval_was_denied`).
- `ImprovementManager.deploy()` raises `PermissionError` without an
  approved request (`test_deploy_without_approval_is_refused`) — the
  self-improvement "never silently modify production" rule, enforced.

---

## Running the tests

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt
.venv/bin/pytest
```

`core/`'s own tests need nothing beyond stdlib + pytest. The adapter tests
(`tests/adapters/`) need `openai`/`google-genai`/`anthropic` (already in
`backend/requirements.txt`) but no real API key or network access — all
mock their client. `tests/interfaces/test_api.py` needs `httpx` (for
FastAPI's `TestClient`) and `python-multipart` (FastAPI requires it for
any `Form(...)` field, which `/approvals/decide` uses) — both are in
`backend/requirements-dev.txt`. `tests/adapters/workspace/` and
`tests/interfaces/test_workspace_e2e.py` run real `git` subprocesses
against `tmp_path` fixtures (git is assumed present, same as any other
dev-machine requirement) — only `adapters/workspace/github.py`'s actual
network call to the GitHub API is mocked. 233 tests pass as of the
repo-editing tools addition (see `adapters/workspace/`).
