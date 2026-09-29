# iV Migration Audit

Full-repository audit performed before building `core/`, the portable
agent runtime. See `docs/CORE_ARCHITECTURE.md` for what was actually
built as a result. This document is the durable record of the audit
findings and plan.

---

## 1. Current Architecture

```
run.py / run.sh
  -> backend/app/main.py (FastAPI, single file of routes)
      -> router.py: ask_iv() -- the entire "agent" -- one function
          -> talents.py: keyword classifier picks a provider order
          -> providers.py: Gemini (native SDK) / Mistral & Groq (OpenAI-compatible) / Claude (Anthropic SDK)
          -> tools/{memory,actions,tool_schemas}.py: 11 functions, direct Supabase calls
          -> personality.py: loads prompts/iv_core.md + prompts/<provider>_core.md
          -> db.py: raw Supabase client, conversations/messages only
      -> sleep_cycle.py: standalone nightly job, its own model-fallback + email logic
frontend/: Next.js 16 app, one ChatBox component, calls /api/chat directly
supabase/: 2 migrations (schema drift patches, not a full schema)
docs/: 8 markdown files describing a "feudal hierarchy" (King/Lords/Merchants/Doctors/Guards/Serfs) vision
```

One FastAPI process, one Python package, one Supabase project. No
separation between orchestration, model calls, tool execution, storage,
and permissions — all inline in `router.py` and `tools/*.py`.

## 2. What Works / Was Preserved (as design intent, not copied code)

- **Talent-based provider routing** (`talents.py`) — classify task type,
  pick provider order, fall through on failure. Generalized into
  `AgentRole.model_provider_order` + `ModelRegistry.generate_with_fallback()`.
- **Multi-provider fallback loop** — real handling of unavailable/exhausted
  providers. Now one implementation (`ModelRegistry`) instead of duplicated
  in `router.py` and `sleep_cycle.py`.
- **`model_resolver.py`'s** defensive "ask what's actually available"
  pattern — good instinct, belongs inside a `ModelProvider.list_models()`
  implementation per adapter.
- **Tool allowlisting** in `tools/memory.py` — correct instinct, now
  formalized as `ToolDefinition.required_permissions` + `PermissionManager`
  checked on every single tool, not just reads.
- **Approval data model** (`approvals` table) — the concept was right; it
  just never blocked anything. `ApprovalManager` + `ToolRegistry` now make
  it a real gate.
- **Sleep cycle's** "only ever write memories + approval proposals, never
  execute" discipline — matches `core/improvements`'s `deploy()` gate.
- **Docs** (`IV_CONSTITUTION.md`, `DATA_MODEL.md`, `AGENT_ROLES.md`) — the
  target vision was clear even where code hadn't caught up; role
  definitions in `core/agent/roles.py` are transcribed from these.

## 3. Problems / Technical Debt Found

**Architecture:** no separation of concerns — `router.py`'s `ask_iv()` did
classification, model calling, tool dispatch, history formatting, and
activity logging in one function. Tools talked to Supabase directly with
three separate client instances (`db.py`, `tools/memory.py`,
`tools/actions.py`). No agent/role abstraction in code — the "King/Lords"
hierarchy was pure prompt text.

**Security (most serious gap):** no permission system existed in code —
every tool function executed unconditionally the moment the model called
it. `create_approval()` inserted a row; nothing ever checked approval
status before acting, so the approval system only recorded, never
blocked. Shared-secret comparison (`security.py`) wasn't constant-time.

**Provider/DB coupling:** Gemini structurally special-cased throughout
`router.py` versus a shared path for OpenAI-compatible providers.
Supabase imported directly in 4 files, no single seam to swap it.
`DEFAULT_PROFILE_ID` — a hardcoded single user, despite `DATA_MODEL.md`
describing real per-user permissions.

**Machine/OS coupling:** Tailscale IPs hardcoded in `main.py`'s CORS list
and `ChatBox.jsx`'s API base fallback. `scripts/macos/*.plist` assumes a
single always-on macOS host.

**Testing:** zero automated tests existed anywhere in the repository.

**Docs vs. reality drift:** `ARCHITECTURE.md`'s own "Current vs. Target"
section documents this; `DATA_MODEL.md`'s schema didn't match the live
Supabase schema (per `tools/actions.py`'s own header comment).

**Dead/inconsistent:** stale branch list in `README.md`;
`.github/scripts/merge-all-branches.sh` is a one-off, not a repeatable
workflow; `frontend/src/components/ConversationItem.jsx` lives in a
different directory than the other three frontend components.

## 4. Target Architecture

```
core/            portable runtime, zero infra imports at module load time
  storage/         StorageBackend interface -- Supabase becomes ONE adapter
  models/           ModelProvider interface + registry/fallback
  memory/           episodic / semantic / reflective, on top of storage
  tools/            ToolDefinition + ToolRegistry -- no unrestricted execution
  permissions/      scopes, grant/revoke/inspect/audit, enforced at execution time
  approvals/        requested -> approved/denied -> executed/failed, revoked, expired
  agent/            role/capability profiles -- "King/Lords" become AgentRole configs
  context/          bounded context assembly from history + memory
  environment/       read-only discovery -> reviewable manifest
  extensions/        manifest schema + registry -- architecture only, no marketplace
  improvements/      proposal lifecycle gated on approval before deploy
  audit/             append-only log every consequential action writes to
  configuration/      env-driven, secrets separated from user/system/model config

adapters/         infra-specific, depend on core, never the reverse
  storage/           Supabase adapter (future), SqliteStorage lives in core/storage
                      today since it has zero external deps
  models/            groq_provider.py (built), gemini/mistral/claude (future)

interfaces/       thin, replaceable
  api/               FastAPI app -- becomes a consumer of core, not where logic lives
```

**Current -> Target map:**

| Current | Target |
|---|---|
| `backend/app/router.py` (`ask_iv`) | `core/agent/orchestrator.py` + `interfaces/api/` |
| `backend/app/providers.py` | `adapters/models/*_provider.py` implementing `core/models/base.py` |
| `backend/app/model_resolver.py` | folded into each adapter's capability discovery |
| `backend/app/talents.py` | `AgentRole.model_provider_order` / future routing policy |
| `backend/app/db.py` + tool-level Supabase clients | `adapters/storage/supabase_adapter.py`, one client |
| `tools/actions.py` / `tools/memory.py` / `tools/tool_schemas.py` | `core/tools/registry.py` + per-tool `ToolDefinition`s |
| `main.py`'s `/approvals/*` HTML routes | thin `interfaces/api/` routes calling `core/approvals/manager.py` |
| `sleep_cycle.py` | consumer of `core/improvements` + `core/memory`; scheduling stays external |
| prompts (`iv_core.md`, `agent_roles.md`) | referenced as data by `core/agent/roles.py`, not hardcoded per provider |
| `DEFAULT_PROFILE_ID` | `core/configuration` + real profile handling in storage |

## 5. Files Created (this pass)

`core/**` (13 subpackages), `adapters/__init__.py`,
`adapters/models/groq_provider.py`, `tests/core/**` (9 test modules, 67
tests), `tests/adapters/test_groq_provider.py`, `pytest.ini`,
`backend/requirements-dev.txt`, `docs/CORE_ARCHITECTURE.md`, this file.

## 6. Files Recommended for Later Modification (Phase 4 — Migration, not done in this pass)

`backend/app/main.py` (thin route layer over `core`), `frontend/components/ChatBox.jsx`
(drop hardcoded Tailscale IP fallback), `docs/ARCHITECTURE.md` /
`DATA_MODEL.md` / `API_ARCHITECTURE.md` (rewrite once migration lands),
`README.md` (drop stale branch list).

## 7. Files Recommended for Removal (only once superseded — not done in this pass)

`.github/scripts/merge-all-branches.sh` (one-off, already served its
purpose); `backend/app/db.py` + the raw Supabase clients in
`tools/memory.py`/`tools/actions.py` (once a Supabase `StorageBackend`
adapter exists and `interfaces/api` uses it); `frontend/src/components/ConversationItem.jsx`
if confirmed unused.

## 8. Risks / Security Concerns

1. No enforced approval gate was the single biggest risk in the prior
   code — now addressed structurally in `core/tools/registry.py` and
   `core/improvements/manager.py`, but `backend/app`'s live tools still
   don't go through it until migration.
2. No permission scoping existed — addressed by `core/permissions`.
3. Non-constant-time secret comparison in `backend/app/security.py` —
   still present, not yet fixed (low real risk given Tailscale is the
   actual network boundary, per that file's own comment, but worth a
   trivial fix during migration).
4. Machine-specific assumptions (Tailscale IPs, macOS LaunchAgents) still
   block "install into a new environment" until migration reaches
   `interfaces/api`.
5. Zero test coverage on `backend/app` remains true — `core/` now has 67
   tests, `backend/app` has none until it's migrated or tested directly.

## 9. Implemented Immediately (this session)

`core/` interfaces for storage, models, memory, tools, permissions,
approvals, agent roles/orchestration, context, environment, extensions,
improvements, audit, and configuration — all additive, `backend/app`
untouched. A local `SqliteStorage` backend and `InMemoryStorage` prove
storage works with zero external services. A `GroqProvider` adapter
proves the model abstraction against a real (mocked-in-tests) provider.
Real permission and approval enforcement, with tests proving prohibited
actions are actually blocked, not just documented.

## 10. Decisions (locked)

1. **Migration aggressiveness: incremental.** `backend/app` will be
   migrated onto `core` piece by piece, not cut over in one pass.
2. **Supabase: dropped entirely.** iV owns and operates a local SQLite
   file (`core/storage/local.py`'s `SqliteStorage`) as its only storage
   backend going forward. `core/configuration/settings.py` no longer has
   any Supabase-specific fields (removed `supabase_url`/`supabase_key`
   from `StorageConfig`) — core doesn't reference a hosted-database
   provider at all anymore, not even optionally. The collection shapes
   (`conversations`, `messages`, `projects`, `tasks`, `approvals`,
   `memories`, `improvements`) carry forward what was learned running
   against the live Supabase schema, now implemented as `core/conversations`,
   `core/projects`, `core/tasks` (new this pass — see below), and the
   existing `core/memory`/`core/approvals`/`core/improvements`.
   **Not yet done:** the actual `backend/app` cutover (removing the
   `supabase` package, rewriting `db.py`/`tools/*.py`/`router.py`/`main.py`/
   `sleep_cycle.py` to use `core` + `SqliteStorage`) — that's next, and
   depends on whether existing Supabase data needs exporting first (see
   open question below).
3. **Frontend: keep Next.js, rework it into a thin API client + PWA; add
   a CLI as a second interface.** Recommendation given and acted on:
   Next.js stays (already invested, modern, nothing wrong with the
   framework itself), reworked to consume a versioned API instead of a
   hardcoded Tailscale IP with a secret embedded in the JS bundle, plus a
   PWA manifest so it installs to the iPhone home screen without the UI
   layer depending on Tailscale (the backend still needs a secure tunnel
   of some kind for remote reachability — that's a deployment concern,
   separate from frontend framework choice). A minimal CLI
   (`interfaces/cli/main.py`, built this pass) is the concrete proof the
   API is UI-agnostic, not just a claim — it runs the full
   storage -> permissions -> tools -> agent orchestration -> model stack
   with zero web layer involved.
4. **"Feudal hierarchy" naming: retired.** `core/agent/roles.py` now uses
   Coordinator (was King), Specialists (was Lords), Gatherers (was
   Merchants), Investigators (was Doctors), Workers (was Serfs). Guards
   don't get a role name at all — that function is now literally
   `core/permissions` + `core/approvals` in code. Full mapping and
   rationale in `core/agent/roles.py`'s module docstring.
5. **Multi-user: stays single-user for now.** `core/conversations`,
   `core/projects`, and `core/tasks` (new this pass) deliberately carry no
   `profile_id`/`user_id` field — simpler schema, consistent with staying
   single-user until there's a working single-user model to extend.
   `core/permissions`/`core/approvals` are already principal-scoped by
   string, so multi-user scoping later is additive (a `principal` becomes
   a real user id instead of a role name) rather than a rearchitecture.

**Resolved:** yes — the live Supabase project ("Agent-iV",
`qpeefbcqwjxvdwwvucib`) had real data: 37 conversations, 272 messages, 8
projects. Everything else in the live schema (`tasks`, `approvals`,
`memories`, `improvements`, `reflections`, `agent_activity`) was empty at
migration time. `scripts/migrate_supabase_to_sqlite.py` (added this pass)
exports those three tables and imports them into a local `SqliteStorage`
file via `core/conversations`/`core/projects`, preserving original ids
and timestamps and dropping `profile_id` (single-user, per decision 5).
It was run once against the live project; the resulting `iv.db` — real
conversation and project history, readable back through
`ConversationStore`/`ProjectStore` — was handed to the owner directly
rather than committed (personal conversation content doesn't belong in
git; `*.db` is now gitignored). The script itself is safe to commit
(no personal data, just logic) and can be re-run any time the live
Supabase project changes before the backend cutover is complete.

## 11. Backend Cutover — Part 1 (this pass)

With all 5 decisions locked, the actual `backend/app` cutover began.
What exists now:

- **Provider-neutral tool calling** was a real gap the earlier passes
  left open: `core/agent/orchestrator.py` sent tool metadata to a model
  but never processed a `tool_calls` response. `core/models/base.py` now
  carries `ToolCall` and tool-call/tool-result message shapes, and
  `AgentOrchestrator.handle_message()` runs the actual loop — call the
  model, execute any requested tools through `ToolRegistry` (permission +
  approval enforcement included), feed results back, repeat until a final
  answer or `max_tool_iterations` (default 5, matching the old per-provider
  cap) is hit.
- **`adapters/models/groq_provider.py`** now does real tool-calling
  (schema translation both directions), not just single-shot text.
- **`adapters/models/gemini_provider.py`** is new: google-genai's manual
  function calling, migrated from `providers.py` + `model_resolver.py`.
  Verified against the actual `google-genai` SDK's real types
  (`FunctionDeclaration.parameters_json_schema`, `FunctionCall.id/args/name`,
  `Part.from_function_response`, role `"tool"` for function-response
  `Content`) rather than assumed from memory — the exact field names were
  checked against the SDK's source before writing the adapter.
- **`core/tools/standard.py`** replaces `backend/app/tools/actions.py` +
  `tools/memory.py`: `register_standard_tools()` wires `ToolDefinition`s
  for projects/tasks/memory/approvals/improvements against core's own
  stores. All are `LOW` risk / `AUTO` execution today because nothing in
  the current tool set is actually destructive or externally consequential
  — a fact about today's tools, not a policy; any future consequential
  tool must set `REQUIRES_APPROVAL`, and `ToolRegistry` is what actually
  enforces that.
- **`interfaces/api/`** replaces `backend/app/main.py`: same route shape
  (`POST /api/chat`, `GET /approvals/pending`, `POST /approvals/decide`,
  `POST /approvals/decide-all`), backed by `core` + local SQLite instead
  of raw Supabase calls. CORS origins come from `CORS_ALLOWED_ORIGINS`
  (env), not a hardcoded Tailscale IP. The secret check uses
  `hmac.compare_digest` instead of `backend/app/security.py`'s plain `!=`.
- **`run.py`** now launches `interfaces.api.main:app` instead of
  `backend.app.main:app`, and no longer `chdir`s into `backend/` — iV's
  default local database path (`iv.db`) now resolves at the repo root,
  matching where the migrated data (§10.2) should be placed.
- Two real bugs were found and fixed while wiring this up, not just
  theorized: `StorageBackend` had no `close()` in its interface at all
  (`InMemoryStorage` didn't have one, so the API's shutdown path crashed);
  and `SqliteStorage` used a bare `sqlite3.connect()`, which is not safe
  to share across threads — FastAPI runs sync endpoints in a worker
  thread pool, so the very first real request through `uvicorn` (not
  `TestClient`, which happened to not reproduce it) crashed with
  `sqlite3.ProgrammingError`. Fixed with `check_same_thread=False` plus a
  lock around every operation, and covered by a regression test that
  actually spawns a thread rather than trusting the fix by inspection.
- `python-multipart` was missing from `requirements.txt` — FastAPI's
  `Form(...)` (used by `/approvals/decide`) requires it, and the original
  `backend/app/main.py` had the exact same latent gap. Added.

**Not done in this pass** (next steps, roughly in order):

1. Migrate `backend/app/sleep_cycle.py` onto `core/improvements` +
   `core/memory` and wire a `/internal/sleep-cycle`-equivalent trigger
   into `interfaces/api`. — **done, see §12.**
2. Build `adapters/models/mistral_provider.py` and `claude_provider.py`
   (their names already work in `model_provider_order` — `ModelRegistry`
   just skips them unregistered). — **done, see §12.**
3. Once the Sleep Cycle is migrated, `backend/app` and the `supabase`
   dependency can actually be deleted — not before, per "don't delete
   working functionality until its replacement exists." — **done, see §12.**
4. Frontend rework (decision 3) — `interfaces/api` is ready to be a
   client's backend now; the Next.js rework itself hasn't started.
5. `scripts/macos/*.plist.template` still reference `run.py` (unchanged
   invocation) so they keep working as-is, but haven't been re-verified
   against the new server on an actual Mac yet.

113 tests pass (up from 99 at the end of the previous pass).

## 12. Backend Cutover — Part 2 (complete: reaches a testing-ready state)

Everything from Part 1's "not done" list except the frontend rework and
an on-Mac LaunchAgent re-verification is now done.

- **`adapters/models/openai_compatible.py`** is new: the translation
  logic that used to live only inside `groq_provider.py` is now a shared
  `OpenAICompatibleProvider` base class. `groq_provider.py` was refactored
  to a ~10-line subclass; `mistral_provider.py` is a second ~10-line
  subclass of the same base — no duplicated translation logic between them,
  mirroring how `backend/app/providers.py`'s `call_openai_compatible()`
  served both before this migration.
- **`adapters/models/claude_provider.py`** is new: Anthropic's Messages
  API, real tool-calling via `tool_use`/`tool_result` content blocks.
  Anthropic requires every tool_result for one assistant turn to arrive
  together in the next user message — `_to_anthropic_messages()` batches
  consecutive `ModelMessage(role="tool")` entries into one turn rather
  than emitting each as its own message, which would be invalid.
- **`adapters/models/__init__.py`'s `register_configured_providers()`**
  replaces duplicated per-provider if-blocks in `interfaces/api/runtime.py`
  and `interfaces/cli/main.py` with one shared function both call.
- **The CLI's tool registry was silently empty** — `interfaces/cli/main.py`'s
  `build_runtime()` never called `register_standard_tools()`, so every
  role's declared `tool_names` (create_project, create_task, ...) existed
  as names nothing backed; any tool call would have failed with "unknown
  tool". Found and fixed while wiring Mistral/Claude registration in, with
  a regression test that actually calls a tool through the CLI runtime
  rather than just checking the registry's contents.
- **`core/reflection/base.py`** is new: `run_reflection_cycle()`, the
  Sleep Cycle's actual logic, generalized from a hardcoded
  Gemini-then-Mistral-then-Groq waterfall to any `provider_order`.
  Required two small core additions: `ConversationStore.messages_since()`
  and `AuditLog.since()`, since the generic `StorageBackend.query()`
  interface only does equality filters and a "since a timestamp" query
  needed its own method.
- **`adapters/notifications/email.py`** is new: Gmail SMTP, isolated the
  same way Supabase was — a caller only ever sees `EmailConfig` and
  `send_email()`, never `smtplib`. `interfaces/api/sleep_cycle.py` is the
  glue that decides what the notification says and when to send it,
  including a failure-notice email if the cycle itself fails — same
  behavior as the original, provider-agnostic instead of hardcoded.
- **`POST /internal/sleep-cycle`** is wired into `interfaces/api/main.py`,
  gated by `INTERNAL_TRIGGER_SECRET` via the same constant-time check
  pattern as `API_ACCESS_SECRET` (`interfaces/api/security.py` was
  generalized to support both).
- **`backend/app` is deleted.** Every route and capability it had —
  `/api/chat`, `/approvals/*`, the Sleep Cycle, all four model providers,
  the full tool set — now lives in `core` + `adapters` + `interfaces/api`,
  verified end to end against a real `uvicorn` process at every step, not
  just unit tests. `supabase` is removed from `backend/requirements.txt`;
  `scripts/migrate_supabase_to_sqlite.py` still works (it imports
  `supabase` lazily, only inside the live-export code path) but now needs
  `pip install supabase` separately if that path is used.
  `backend/prompts/*.md` (the old per-provider personality files) are
  left in place, unreferenced — their content was folded into
  `core/agent/roles.py`'s role descriptions, not copied verbatim; worth a
  look if you want to tune tone further, otherwise safe to delete later.

**Still not done:**

1. Frontend rework (decision 3) — the existing Next.js app should work
   unchanged against `interfaces/api` (same request/response shape), but
   hasn't been reworked into the PWA + versioned-API-client design
   discussed, and a CORS/Tailscale round-trip against it hasn't been
   verified in this environment (no browser/Tailscale access here).
2. `scripts/macos/*.plist.template` haven't been re-verified on an actual
   Mac — the invocation (`run.py` via the venv's own python) is unchanged
   from before, so they should keep working, but "should" isn't "verified."
3. `supabase/migrations/*.sql` and `.github/scripts/merge-all-branches.sh`
   are now purely historical (flagged in the original audit as dead/
   one-off) — left as-is, low-risk either way, candidates for a later
   cleanup pass.

149 tests pass (up from 113 at the end of Part 1). Every real bug found
this pass (the CLI's empty tool registry, the earlier thread-safety and
`close()` gaps) was caught by actually exercising the code — running a
live `uvicorn` process, calling a tool through the CLI runtime — not by
code review alone; this remains the standard for anything touching
runtime wiring.

## 13. Haven / Agency Levels / Guardian (safety architecture, staged per docs/HAVEN.md)

Following a review of external ideation about iV's long-term direction —
a proposal to frame the project around detecting or producing emergent
machine intelligence — the genuinely buildable parts of that framing
(a named boundary, graduated trust, a watcher role, human-gated agency
growth) were extracted and built. The speculative parts (emergence
detection, "continuity of cognition," anything framed as approaching
superintelligence) were explicitly declined — see `docs/HAVEN.md`'s
opening section for why, in the project's own words rather than
paraphrased here.

- **`core/haven`**: `build_haven_manifest()` aggregates `core/environment`
  + `core/tools.list_tools()` + `core/permissions.list_grants()` into one
  read-only snapshot. No new capability — naming and exposing (via
  `GET /haven/manifest` and the CLI's `/haven` command) what already
  existed across three modules.
- **`core/permissions/levels.py`**: `AgencyLevel` bundles (`observer`,
  `researcher`, `builder`, `collaborator`), derived from a principal's
  live grants rather than stored separately. `request_level_up()` /
  `apply_level_change()` mirror `core.improvements`'s propose/deploy
  gate — a level change requires an approved `ApprovalRequest` before
  any scope is granted, additive only (never revokes on a level change).
- **`core/guardian`**: `find_denied_action_spikes()` (read-only anomaly
  check over `AuditLog`) and `suspend_principal()` (immediate, full
  revoke of a principal's grants). Exposed as tools
  (`core/tools/guardian.py`) reachable only via `permissions.manage` —
  the Security specialist role. `suspend_principal` is deliberately
  `AUTO`, not `REQUIRES_APPROVAL`: gating an emergency stop behind human
  sign-off defeats the point of having one. What keeps it from being an
  escalation path: it only calls `PermissionManager.revoke()` in a loop
  (never grants), and every call is unconditionally, specifically
  audited as `guardian.suspend`.
- **`core/approvals.list_approved()`** and
  **`core/reflection.materialize_approved_action_items()`**: the "first
  launch" idea, scoped honestly — once a human approves a Sleep Cycle
  proposal (that day or any later day), it becomes a real `Task` under
  an auto-created "iV Improvements" project instead of sitting as a
  status flip in the approvals table nothing reads. Wired to
  `POST /internal/materialize-backlog` and an optional daily
  `scripts/macos/com.agentiv.sleepcycle.plist.template` that runs it
  right after the reflection cycle. This is *not* an autonomous work
  loop — nothing picks the task up and starts executing it without
  further human action; it only makes an approval visible as backlog.
- **Found while wiring this in**: `core/agent/roles.py`'s `coordinator`,
  `research`, and `security` roles referenced `list_tables`/`query_table`
  — tool names from the original prototype that `core/tools/standard.py`
  never carried forward (replaced by typed tools like `list_projects`).
  These roles' declared tool access had silently been dead for the
  entire backend-cutover pass; fixed to reference real registered tools.

179 tests pass (up from 149). `docs/HAVEN.md` is the canonical framing
document for this section — read it before extending any of the above,
since it states explicitly what this architecture is and isn't claiming.

## 14. Repo-editing tools (`adapters/workspace/`)

Following the first successful real-world test cycle (Haven, Sleep Cycle,
and the approval catch-22 fix all verified against a real deployment),
the next capability requested was repo editing — the concrete first test
target being "have iV take on a new repo and complete a task." This adds
a new adapter package rather than extending `core/tools/standard.py`,
since git/filesystem/subprocess/GitHub-API access is exactly the kind of
infrastructure-specific capability `core/` is not supposed to import (see
this doc's own "Design rule").

- **`adapters/workspace/sandbox.py`** is the trust boundary every other
  module here routes through: `resolve_repo_path()`/`resolve_file_path()`
  confine all resolution to a configurable workspace root, rejecting
  absolute paths, `..` traversal, and symlink escapes (checked via
  `resolve()` + `relative_to()`, which catches a symlink planted inside a
  repo pointing back out, not just a literal `..` in the input string).
  `reject_flag_like()` additionally rejects any value that starts with
  `-` before it reaches git as a positional argument (a clone URL like
  `--upload-pack=...` is a real argument-injection vector otherwise).
- **`adapters/workspace/git_ops.py`** / **`files.py`** / **`shell.py`**:
  git via subprocess argv lists (never `shell=True`), sandboxed file
  read/write/list, and a general command runner for a repo's directory —
  `shell.py`'s `run_command()` uses `shlex.split()` so no real shell (no
  pipes, redirects, or substitution) is ever invoked, but the resulting
  argv can still run anything git could, which is exactly why it's gated
  the way it is (below).
- **`adapters/workspace/github.py`**: PR creation via the GitHub REST API
  using `urllib` — stdlib only, no new dependency for one API call.
  Raises rather than silently no-opping when no token is configured,
  unlike email's "skip send if unconfigured" — a PR request with no way
  to authenticate is a configuration error the caller should see.
- **Risk tiers** (`adapters/workspace/tools.py`, reasoning duplicated in
  its own module docstring since that's what a human auditing tool access
  will actually read): read-only tools (list/read/status/diff) are LOW +
  AUTO; clone/write/commit/branch are MEDIUM + AUTO (local, reversible,
  confined to the sandbox — the same "may generate code" allowance
  `IV_CONSTITUTION.md` already grants); `run_repository_command` is
  CRITICAL + REQUIRES_APPROVAL with deliberately no command allowlist
  (the simplest correct default for "run anything" is a human sign-off
  every time, not a blocklist one bypass away from wrong); `git_push` and
  `create_pull_request` are HIGH + REQUIRES_APPROVAL — the first points
  any of this leaves the sandbox and becomes externally visible.
- **Engineering is the only default role** given these tool names and the
  new scopes (`filesystem.read`/`write`, `network.request`,
  `github.write`, alongside its existing `database.*`/`code.execute`/
  `github.read`) — verified with a real regression test
  (`test_finance_role_cannot_reach_workspace_tools_even_though_they_are_registered`)
  that registering these tools globally doesn't leak access to a role
  that was never configured with them.
- **`IV_WORKSPACE_ROOT`** anchors to the install directory the same way
  `IV_LOCAL_DB_PATH` does (`run.py`, `interfaces/cli/main.py`'s
  `load_environment()`), for the same reason — a LaunchAgent, a cron job,
  or a process launched from an arbitrary cwd must all agree on where
  cloned repos live. `GITHUB_TOKEN` (adapter-specific, like
  `GMAIL_APP_PASSWORD`) lives in `adapters/workspace/config.py`, not
  `core/configuration`.
- **Tested here**: real `git` subprocesses against `tmp_path` fixtures
  for every sandbox/git_ops/files/shell/tools path, including a real
  local bare repo standing in for a GitHub remote to prove `git_push`
  actually delivers a commit, not just exits 0
  (`tests/adapters/workspace/test_git_ops.py::test_git_push_to_local_remote`).
  A full-stack smoke test
  (`tests/interfaces/test_workspace_e2e.py`) drives the real
  `build_runtime()` assembly through a scripted multi-turn tool-calling
  exchange — clone → write → commit → status — against a real repo on
  disk, and separately proves the CRITICAL-risk command tool actually
  blocks until a human approves it (the file doesn't exist until after
  the approval + re-submit). Only `adapters/workspace/github.py`'s
  network call is mocked.
- **Not testable in this environment** (see handoff instructions given to
  the user directly): a real GitHub API call with a real token and a
  real repo (`create_pull_request` against the actual GitHub REST API,
  not a local bare repo); `git_push`/`create_branch` against an actual
  GitHub remote requiring real host git credentials (SSH key or stored
  credential helper); and — the actual point of this feature — a real
  provider (Claude/Gemini/Mistral/Groq) autonomously choosing to call
  these tools in response to an open-ended instruction like "go fix X in
  repo Y," rather than a scripted response driving them deterministically.

233 tests pass (up from 179).
