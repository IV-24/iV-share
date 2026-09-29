# AUDIT_FINDINGS.md — Agent-iV

> **STATUS — remediation in progress.** Nine findings have been fixed, each
> with a failing test written first and each verified against its original
> reproduction. Commits `3857d8d`..`7e6175a`. Test suite 510 → 548.
> See §8 for what changed and what is still open. The findings below are
> preserved as written at audit time.

**Repo:** `IV-24/Agent-iV` @ `a5e2af4` · **Audited:** 2026-08-25 · **Auditor artifacts:** `ARCHITECTURE_MAP.md`, `RUNBOOK.md`, `AUDIT_PHASES_2-7.md`, `scripts/smoke_test.sh`, `scripts/external_client_demo.py`, `audit/`

**Evidence standard.** Phase 0 was read-verified. Phases 1–7 were executed against a running iV. Phases 3–7 substituted the provider HTTP call only — real API, orchestrator, tool registry, approval manager, SQLite, via `create_app`'s existing `runtime_factory` seam, no production file modified. Conclusions that depend on a real model's *choices* are tagged `UNVERIFIED` and listed in §7.

---

## 1. EXECUTIVE SUMMARY

Agent-iV is a genuinely well-built agent runtime, and the architecture is not what stands between you and your three goals. It boots from a clean clone in two documented commands, serves an API and a frontend, and passes 510 of its own tests. Delegation is real and autonomous: with live free-tier keys the Coordinator routed 3 of 4 goals to the right specialist, answered the trivial one itself in 0.6 s, and drove 24 real tool calls across two delegations on an open-ended goal. Role scoping, sandbox containment, and self-inspection read-only enforcement all held under direct attack, and no registered tool can grant a permission, so privilege escalation is structurally unavailable to a model. An external application already drives iV over HTTP without importing anything from the repo — goal 2 is closer than goal 1's polish suggests. Against goal 3, however, the system has no run identifier of any kind, so a completed execution cannot be addressed, correlated, or reconstructed; replaying one run by timestamp returned seven tool events for a three-call run. Worse, a failed turn is recorded as `outcome: "success"`; a malformed model response returns HTTP 500 while writing no audit row at all; and a late provider failure throws away a whole turn's completed work — a live 106-second run with 24 tool calls returned nothing but an apology. The approval gate blocks correctly but has no path from approved to executed: across the audit, seven approval rows accumulated and zero ever reached `executed`. Secrets embedded in exception messages are written to the log in cleartext, contradicting an explicit documentation claim. None of these require a rewrite — the two that matter most for your goals are additive changes to code that already exists.

---

## 2. SCORECARD

Scored independently. Do not average them.

| Area | Score | Justification |
|---|---|---|
| **Local runnability** | **4** | Two documented commands took a genuinely clean clone to a serving API and frontend with zero undocumented steps, on a platform the project doesn't even target. |
| **External API** | **4** | A stdlib-only client submitted a goal and retrieved the result with no repo imports; `ChatRequest` has no chat-specific required field — held back only by missing idempotency and no per-run handle. |
| **Master orchestration** | **4** | Live models routed 3 of 4 goals to the right specialist and answered the trivial one directly in 0.6s; one open-ended goal drove 24 real tool calls across two delegations. |
| **Subagent isolation** | **4** | `git_push` was attempted only by Engineering across four role-addressed attempts; finance, research and writing could not reach it, and delegates never inherit the caller's scopes. |
| **Tool architecture** | **4** | 36 tools with declared schemas, risk tiers and scopes behind a single registry chokepoint; docked because arguments are never validated against `input_schema` before being splatted into handlers. |
| **Approval enforcement** | **1** | It blocks reliably, but approving does nothing: the granted request is never consumed, a retry files a new one, and zero of seven approvals ever reached `executed`. |
| **Model abstraction** | **4** | Five personalities served real turns through one neutral shape and fallback walked the role's own provider order; one leak — a non-`ModelUnavailableError` from the adapter kills the run instead of failing over. |
| **Memory** | **2** | Recency-plus-importance recall works and is injected per turn, but there is no project, user, or conversation scoping at all — every memory reaches every turn of every caller. |
| **Persistence** | **3** | Real SQLite with a verified round-trip health probe and a contract suite run against two backends, but everything lives in one untyped `records(collection, id, data)` table with no indexes or foreign keys. |
| **Telemetry** | **1** | Audit events are written inside the runtime, which is the right side of the boundary, but there is no correlation id, no token usage, no latency, no tool results, and failures are mislabelled or absent. |
| **Training-data lineage** | **0** | None of the three layers exists; there is no run record to curate from and no place to attach an outcome signal. |
| **Replayability** | **1** | Messages and the final response come back, but tool events carry no reference to the run and attribution by timestamp demonstrably over-collects under concurrency. |
| **Failure handling** | **2** | Fallback, bad tool args and malformed requests are handled well, but a late provider failure discards a whole turn's accumulated work, malformed model responses are fatal and unrecorded, and empty responses fail silently. |
| **Security** | **3** | Sandbox, self-inspection and role scoping held under attack and the tool surface stopped 2 of 4 live injections that the model itself did not; undercut by cleartext secrets in exception logs and one shared credential. |
| **Tests** | **4** | 510 tests pass and the strongest ones assert on real filesystem and git side effects, but not one makes a real model call and every delegation test scripts the model's decision. |

---

## 3. VERDICT TABLE

| Question | Verdict | Evidence |
|---|---|---|
| Can I run iV on localhost from a clean clone? | **YES** | `scripts/setup-env.sh` then `./run.sh` — API on 8024, frontend on 4024, zero undocumented steps. |
| Can an external app call iV as an agent runtime? | **PARTIAL** | `scripts/external_client_demo.py` did it with stdlib only; blocked from YES by no idempotency and no per-run handle. |
| Does the Master autonomously delegate? | **YES** | Live free-tier models delegated on 3 of 4 goals (Auditor, Security, Planning) and correctly answered the simple one directly. |
| Are subagents isolated? | **YES** | 4 role-addressed push attempts; `git_push` reachable only by Engineering, denied every time. |
| Are tools properly scoped? | **YES** | `orchestrator.py:132` role check plus `registry.py:55` scope check, both observed rejecting real calls. |
| Is approval a real security boundary? | **PARTIAL** | It blocks (denied push left no workspace on disk) but never completes — 7 approvals, 0 executed. |
| Is iV genuinely model-agnostic? | **YES** | gemini forced unavailable → mistral served the turn, HTTP 200, correct answer, via the role's own provider order. |
| Does every execution get a unique run_id? | **NO** | Zero occurrences of `run_id`/`trace_id`/`request_id`/`correlation` in source, docs or tests. |
| Is a complete execution trace persisted? | **NO** | No prompts, no tool results, no token usage, no latency; tool args only on success rows. |
| Can a past run be reconstructed? | **NO** | Replay of a 3-call run returned 7 tool events; no audit row references the conversation. |
| Can runs become curated training data with provenance? | **NO** | No run record exists to be the provenance anchor. |
| Can outcome signals be recorded? | **NO** | The only outcome field says `"success"` on turns that failed. |
| Is this production-safe? | **NO** | Cleartext secrets in exception logs, one shared credential, no caller identity, unscoped memory. |

---

## 4. FINDINGS, RANKED BY IMPACT ON THE THREE GOALS

### P0 — blocks a goal outright, or is a live security hole

---
**P0-1 · No run identifier anywhere in the system**
`MISSING` · impacts **goal 3 fatally, goal 2 seriously**

- **Problem.** Nothing identifies an execution. `AuditEvent` (`core/audit/log.py:16`) has `actor, action, resource, outcome, metadata` and no correlation field. A tool event's `resource` is the *tool name*; a `chat.turn` event's is the conversation. Nothing links them.
- **Evidence.** `grep -rInE "run_id|trace_id|correlation|request_id" core adapters interfaces frontend docs tests` → zero real matches. Confirmed empirically: one turn produced 1 conversation row, 2 messages, 1 audit row, no identifier.
- **Reproduction.** `scripts/smoke_test.sh`, then inspect `audit_log`.
- **Impact.** You cannot address a run, correlate its steps, or hand its id to another system. Goal 3 has no anchor to hang anything on.
- **Fix.** Add `run_id` to `AuditEvent` and carry it on a `contextvars.ContextVar` set once per turn in `_run_turn`. **iV already runs exactly this pattern** — `DelegationBudget` (`core/agent/delegation.py:99`) is a per-turn contextvar reset at `main.py:239-240`. Ride the same rail. Return `run_id` in `ChatResponse`.
- **Dependencies.** None. **Fix now.**

---
**P0-2 · A failed turn is recorded as a success**
`BROKEN` · impacts **goal 3 fatally**

- **Problem.** `main.py:251` catches `ModelUnavailableError`, substitutes apology text, then falls through to `audit.record(...)` at `:259`, which defaults to `outcome="success"`.
- **Evidence.** With no provider reachable: `{"actor":"Coordinator","action":"chat.turn","outcome":"success","metadata":{"model_used":null}}` — for a turn that produced no answer.
- **Impact.** "iV produced this" and "iV produced this *and it worked*" are indistinguishable. That distinction is the entire value of a training set.
- **Fix.** Pass `outcome="error"` on the `ModelUnavailableError` branch; add a `status` field to the run record from P0-1. Two lines.
- **Fix now.**

---
**P0-3 · A malformed model response returns 500 and writes no audit row**
`BROKEN` · impacts **goals 1, 2 and 3**

- **Problem.** `adapters/models/openai_compatible.py:113-118` reads `completion.choices[0]` and `json.loads(call.function.arguments)` **outside** the `try` that converts errors to `ModelUnavailableError`. `registry.py:77` catches only `ModelUnavailableError`, so anything else skips fallback and propagates to `main.py:291`'s generic handler — which raises **before** the `audit.record` on `:259`.
- **Evidence.** Injected: `HTTP 500`, `model_used=None` (mistral and groq never tried), `persistence delta: {'conversations': 1, 'messages': 1}` — user message stored, no reply, **no audit row**. Reproduced twice.
- **Impact.** Executions vanish. A caller sees an opaque 500; the record does not exist.
- **Fix.** Move response parsing inside the `try` (or wrap it), and have `generate_with_fallback` catch `Exception` while re-raising a distinguished internal error class.
- **Fix now.**

---
**P0-4 · Secrets appear in cleartext in exception logs**
`BROKEN` · **live security hole**, contradicts an explicit doc claim

- **Problem.** `SecretRedactingFilter` (`core/observability/redaction.py:108`) redacts `record.exc_text`, but `exc_text` is `None` when filters run — it is populated lazily by the formatter afterwards, from the live `exc_info` tuple. So tracebacks are never scrubbed.
- **Evidence.** Production `configure_logging()`, single root handler:
  ```
  ValueError: provider rejected key gsk_LEAKYKEY_9876543210_abcdef
  RuntimeError: inner: SUPERSECRET-abc123-do-not-leak
  ```
  Control case (secret as a format arg) redacted correctly.
- **Reachable path.** `openai_compatible.py:111` does `raise ModelUnavailableError(str(exc)) from exc`, carrying an SDK message that routinely embeds request context; 8 `logger.exception` sites format the full chained traceback, including `main.py:292` on the request path. Keys land in `.launchd/backend.log`.
- **Doc contradiction.** `RUNTIME.md:432` — "Secrets are redacted from logs by value… scrubbed on the way out." False for the module's own stated threat model (`redaction.py:4-7`).
- **Fix.** In the filter, when `record.exc_info` is set and `exc_text` is not, format it, redact, and assign. ~3 lines. Add a regression test.
- **Fix now.**

---

---
**P0-5 · A late provider failure discards the entire turn's completed work**
`BROKEN` · impacts **goals 1 and 3** · **found on live models, not reproducible with the simulator**

- **Problem.** `orchestrator.py:108` calls `generate_with_fallback` inside the tool loop. When every provider is exhausted mid-run it raises `ModelUnavailableError`, which propagates *out of the loop*, discarding the local `messages` list — every tool call and result accumulated so far. `main.py:288` catches it and replaces the whole turn with the generic no-model apology.
- **Evidence (live, real keys).** One open-ended goal ran for **106.6 seconds**, delegated to the Auditor, and fired **24 tool executions** — `self_list_files`, seven `self_read_file`, five `self_search_repository`, two `delegate_to_agent`. The user received:
  ```
  HTTP 200 in 106.6s   model_used=None
  reply: "iV couldn't reach any configured model just now. Check that a provider
          API key (GEMINI_API_KEY, GROQ_API_KEY, ...) is set, or try again shortly."
  ```
  The tools genuinely ran — their side effects and audit rows persist. Only the synthesis was lost, and with it any report of the work.
- **Impact.** The longer and more valuable the run, the more likely it is to hit a free-tier limit late and be thrown away whole. Combined with P0-2, this run is also recorded as `outcome: "success"`. A 106-second, 24-tool execution is exactly the kind of run you would want as training data; it is stored as a success that produced nothing.
- **Fix.** Catch `ModelUnavailableError` **inside** the loop and return a partial `AgentTurnResult` summarising completed tool calls, with a truthful status. The accumulated `messages` list already holds everything needed.
- **Fix now.** This is the single biggest gap between "iV did the work" and "iV can tell you it did the work".


### P1 — blocks iV becoming a real runtime

---
**P1-1 · Approving a consequential action never executes it**
`BROKEN`

- **Evidence.** Approval `8aff9d98` granted (HTTP 303, `status=approved`); identical retry filed a **new** request `9350046a`; final table: 7 rows, **0 in `executed`**.
- **Cause.** `orchestrator.py:151` and `:168` are the only production callers reaching `ToolRegistry.execute()` for a model tool call, and neither passes `approval_id`. `/approvals/decide` (`main.py:594`) flips the row and redirects. `materialize_approved_action_items` filters `requested_by="iV Sleep Cycle"`, so tool approvals never match. Arguments survive only inside a prose string (`registry.py:69`).
- **Impact.** `git_push`, `create_pull_request`, `run_repository_command` can never run. Approval is enforced and unreachable.
- **Fix.** Add `arguments` (structured) and `principal` to `ApprovalRequest`; add a resume path — an endpoint or a post-decision hook that calls `orchestrator.run_tool(role, action_type, arguments, approval_id=...)`. Model the guarantee on the protected-branch guard, which is enforced at the operation rather than the dispatcher and held under direct bypass.
- **Fix now** (it is the difference between a gate and a dead end).

---
**P1-2 · No idempotency; retries duplicate side effects**
`BROKEN` · impacts **goal 2**

- **Evidence.** The same goal submitted twice: 2 conversations, **2 projects, 4 tasks**. Visible in `external_client_demo.py` output as three identically-named projects.
- **Fix.** Optional `idempotency_key` on `ChatRequest`; store it on the run record from P0-1 and return the prior result on a repeat. **Fix now.**

---
**P1-3 · Memory is globally scoped**
`UNSOUND` · impacts **goal 2**

- **Evidence.** `MemoryStore.recall(*, limit=8, min_importance=6)` — no project, user, conversation or tenant parameter exists. Two memories written, both returned to any caller.
- **Impact.** Correct for one owner; the moment a second application calls iV, every caller's memories are injected into every other caller's turns.
- **Fix.** Add an optional `scope` field to `Memory` and a `scope=` filter on `recall`; default to the current behaviour. **Defer** until a second consumer exists — but before any multi-app use.

---
**P1-4 · Execution trace is not persisted**
`MISSING` · impacts **goal 3**

- **Evidence.** Not stored: system prompt, assembled context, tool results, token usage, latency, cost, failed provider attempts. Tool arguments only on `success` rows (6 of 7). Latency is logged to stdout at `registry.py:84` and discarded.
- **Fix.** With P0-1's run record in place, write a `run_steps` row per model call and per tool call. **Fix in Phase 3 of the roadmap.**

---
**P1-5 · Sync-only API with no submit/poll**
`UNSOUND` · impacts **goal 2**

- **Evidence.** Every `/api/chat` call blocks for the whole run. Delegation permits a 90s wall-clock budget per turn (`delegation.py:73`) plus a 30s provider timeout each.
- **Impact.** A long agentic task holds an HTTP connection; a caller cannot poll, cancel, or reconnect.
- **Fix.** `POST /api/runs` returning `{run_id, status: "running"}` plus `GET /api/runs/{id}` — natural once P0-1 exists. **Defer to roadmap Phase 1.**

---

### P2 — address before expanding

- **P2-1 · Tool arguments never validated.** `registry.py:79` does `tool.handler(**arguments)` with raw model output; `input_schema` is advertised but never enforced. A bad key becomes a `TypeError` caught at `:80`. Add jsonschema validation before dispatch.
- **P2-2 · Unknown tool calls are not audited.** `registry.py:53` returns early for an unregistered tool **before** `_log`. A model repeatedly hallucinating tools is invisible. One line.
- **P2-3 · Empty model response fails silently.** Injected empty text → HTTP 200 with `response: ""`, persisted as an empty assistant message, no error. Treat as a failed turn.
- **P2-4 · Nonexistent `conversation_id` silently creates a new conversation.** `main.py:224-226`. A caller believing it is appending is starting fresh. Return 404.
- **P2-5 · No request body size limit.** A 40,000-char message was accepted and stored whole.
- **P2-6 · Every role self-grants its scopes at boot.** `runtime.py:140-155`. Documented as a single-user choice; it means `is_granted` can only fail for a scope a role never declared. Revisit before multi-tenant.
- **P2-7 · The documented `uvicorn` invocation skips preflight.** `main.py:6` — boots healthy with no `API_ACCESS_SECRET` and 403s everything, the exact failure `run.py:23-26` exists to prevent.
- **P2-9 · Silent provider degradation.** Live `/health` reported `gemini, groq, mistral, openrouter` all live, yet **every** turn was served by `mistral:mistral-small-latest` — gemini is first in the Coordinator's order (`roles.py:88`) and evidently failed on every call, falling through silently. The failure is logged to stdout at `registry.py:78` and never surfaced or persisted, so an operator would believe gemini is serving traffic. Surface failed attempts on `/status` and on the run record.
- **P2-8 · One shared secret, no caller identity.** Every external app is indistinguishable in the audit log — which also means training data cannot be attributed to a source.

### P3 — improvement

- **P3-1 · Docs contradict code in four places.** `IV_CONSTITUTION.md` and `ARCHITECTURE.md` describe delegation as unbuilt (it is built); `MULTI_AGENT.md:95` says delegations run sequentially (they run concurrently, `orchestrator.py:166`); `RUNTIME.md §10.3` says the proxy allowlists three paths (it is seven-plus, including a POST write); `DATA_MODEL.md` describes a Supabase schema with a `Users` table that does not exist.
- **P3-2 · Test count claims are stale.** Docs say 233; actual is **510 passing**.
- **P3-3 · `run.sh` advertises the frontend before it can serve.** Cold start refuses for 10–40s after "iV is up".
- **P3-4 · `setup-env.sh` leaves `INTERNAL_TRIGGER_SECRET` empty**, so the Sleep Cycle silently does nothing on a fresh install.
- **P3-5 · `core/permissions/levels.py` is unreachable.** Fully implemented and tested; no caller outside tests.
- **P3-6 · `supabase/migrations/` is dead residue.**
- **P3-7 · `resolve_repo_path` permits a symlinked repo name.** `resolve_file_path` correctly refuses symlink escapes; the repo-level resolver does not check the target. Low severity, worth closing.

---

## 5. TARGET ARCHITECTURE — the smallest change set

**No rewrite. The current architecture reaches all three goals additively**, and I can point at why: `core/` already has no dependency on `adapters/` or `interfaces/`; the tool registry is already the only path a tool runs through; the provider abstraction already survived five personalities and a forced failover. What is missing is a *run* concept, and the runtime already has the exact machinery to carry one.

### The one new idea: `run_id` on the rail that already exists

`core/agent/delegation.py:99` keeps per-turn state in a `contextvars.ContextVar`, reset once per turn at `main.py:239-240`, and `orchestrator.py:168` already does `contextvars.copy_context()` so that state survives into concurrent delegate threads. **A `run_id` set on the same rail is automatically correct for delegation, concurrency, and nesting** — the hard part is already solved and tested.

```
NEW      core/runs/base.py          RunRecorder + RunContext (contextvar), start/finish/fail
NEW      core/runs/lineage.py       Curation + OutcomeSignal + DatasetRow (Phase 4)
EDIT     core/audit/log.py          AuditEvent gains run_id, read from the contextvar
EDIT     interfaces/api/main.py     _run_turn opens a run, closes it with a real status
EDIT     interfaces/api/schemas.py  ChatResponse gains run_id; ChatRequest gains idempotency_key
EDIT     core/models/registry.py    record provider attempt, latency, usage onto the run
EDIT     core/tools/registry.py     record tool args AND results onto the run; log unknown tools
EDIT     core/approvals/base.py     ApprovalRequest gains arguments + principal (structured)
NEW      interfaces/api/runs.py     POST /api/runs, GET /api/runs/{id}  (async surface)
```

### Phase 6 answer — the three layers, and outcome without mutation

**Assessment: the schema does not currently support the three layers, and it does not prevent them either.** Everything is a JSON blob in one `records(collection, id, data)` table with no foreign keys or indexes — so lineage integrity would be by convention rather than enforced, and queries are scans. At one household's scale that is acceptable; it is the thing to revisit if the dataset grows.

Today **all three layers are absent**, not collapsed: there is no raw execution record to curate from, so there is nothing to collapse.

```
runs                 run_id (pk) · parent_run_id · idempotency_key · caller · goal
                     · started_at · ended_at · status(ok|failed|blocked) · error
                     · model · provider · provider_attempts[] · tokens_in/out · latency_ms

run_steps            step_id (pk) · run_id (fk) · seq · kind(model_call|tool_call|delegation)
                     · role · provider · system_prompt · messages_json
                     · tool_name · tool_args · tool_result · outcome · latency_ms

outcome_signals      signal_id (pk) · run_id (fk) · kind(human_feedback|verification
                     |correction|rejection) · value · rationale · created_at · created_by
                     APPEND-ONLY. Never touches runs or run_steps.

curations            curation_id (pk) · run_id (fk) · decision(include|exclude)
                     · rationale · curated_by · created_at

dataset_rows         row_id (pk) · dataset_id · curation_id (fk) · format
                     · content_hash · created_at
```

Provenance chain: `dataset_row → curation → run → run_steps + messages`. Every training row traces to the exact execution that produced it.

**Outcome signal without mutating raw history** is achievable and the codebase already leans the right way: `AuditLog` exposes only `record()` and readers — no update method. Keeping `outcome_signals` a separate append-only collection preserves that property. A correction becomes a *new signal row*, never an edit to the run. This is the piece that turns "iV said this" into "iV said this and it demonstrably worked", and it is the whole reason the dataset is worth building.

---

## 6. ROADMAP

Estimates assume one engineer familiar with the codebase. iV's own test suite is the safety net.

### Phase 0 — Critical fixes · **6–9 h**
Files: `core/observability/redaction.py`, `adapters/models/openai_compatible.py`, `core/models/registry.py`, `interfaces/api/main.py`
- P0-4 secret leak in exception logs (~3 lines + regression test)
- P0-3 move response parsing inside the try; broaden fallback catching
- P0-2 correct outcome on the `ModelUnavailableError` branch
- P2-2 audit unknown tool calls
**Done when:** a secret in a chained traceback is redacted; a malformed provider response fails over instead of 500-ing; a failed turn is not labelled success. Tests required: one per fix.

### Phase 1 — Reliable local + API runtime · **14–20 h**
Files: `core/runs/base.py` (new), `core/audit/log.py`, `interfaces/api/{main,schemas,runs}.py`
- P0-1 `run_id` on the delegation contextvar rail; `run_id` in `ChatResponse`
- P1-2 `idempotency_key`
- P1-5 `POST /api/runs` + `GET /api/runs/{id}`
- P2-4 404 on unknown `conversation_id`; P2-5 body size limit
- P3-3/P3-4 runbook fixes: frontend readiness gate, generate `INTERNAL_TRIGGER_SECRET`
**Done when:** `scripts/smoke_test.sh` exits 0 with a real key and asserts a `run_id` round-trips; a duplicate submission returns the original run.

### Phase 2 — Agent and tool boundaries · **10–14 h**
Files: `core/approvals/{base,manager}.py`, `core/tools/registry.py`, `interfaces/api/main.py`
- P1-1 structured `arguments`/`principal` on `ApprovalRequest` + a resume path
- P2-1 jsonschema validation before handler dispatch
- P2-3 treat an empty model response as a failed turn
**Done when:** the Phase 4 test inverts — approve `git_push`, and it executes exactly once and moves to `executed`.

### Phase 3 — Telemetry · **12–16 h**
Files: `core/runs/base.py`, `core/models/registry.py`, `core/tools/registry.py`
- P1-4 `run_steps`: per model call and tool call, with args, results, provider attempts, latency, token usage (extract from the SDK response already held as `ModelResponse.raw`)
**Done when:** the replay test reconstructs a run exactly — no timestamp inference — and returns 3 events for a 3-call run under concurrent load.

### Phase 4 — Training pipeline · **16–22 h**
Files: `core/runs/lineage.py` (new), `interfaces/api/runs.py`, `interfaces/cli/`
- `outcome_signals`, `curations`, `dataset_rows`; an export command
**Done when:** a dataset row exports with a provenance chain back to its run, and recording a correction adds a row without mutating any run.

**Total: 58–81 h.** Phases 0 and 1 alone (20–29 h) get you to a runtime you can build against.

---

## 7. THE FINAL QUESTION

> *If I built another application tomorrow and wanted to call iV as an autonomous agent over an API, with every execution recorded so successful runs could become training data — what stops me today?*

**Nothing stops you calling it.** An external app already submits a goal and gets a result back over HTTP with no repo imports. What stops the *recording* half is that iV has no concept of a run: no id to address, no trace to reconstruct, and an outcome field that says "success" when the turn failed. You would be able to drive it and unable to learn from it.

### Blockers, in order

1. **No `run_id`** — nothing to correlate steps to, or hand to your app. (P0-1)
2. **Failed turns recorded as successes** — you cannot select the runs that worked. (P0-2)
3. **Some failures recorded not at all** — a malformed model response 500s and writes nothing. (P0-3)
4. **Secrets in exception logs** — fix before anything else touches this box. (P0-4)
5. **No execution trace** — no prompts, tool results, tokens, or latency to train on. (P1-4)
6. **No idempotency** — your retry logic will duplicate real side effects. (P1-2)
7. **Approval never completes** — every consequential action is permanently blocked. (P1-1)
8. **Sync-only** — a long run holds an HTTP connection with no poll or cancel. (P1-5)
9. **Global memory** — your app's memories would leak into every other caller's context. (P1-3)
10. **No caller identity** — one shared secret, so training data cannot be attributed. (P2-8)
11. **No lineage tables** — nothing to curate into, no place to attach an outcome signal. (Phase 4)

---

## 8. REMEDIATION LOG

Fixed, in dependency order. Every entry was reproduced before the change and
re-verified against that same reproduction afterwards.

| # | Finding | Commit | Verified by |
|---|---|---|---|
| P0-4 | Secrets in exception tracebacks | `3857d8d` | 0 cleartext occurrences in both log formats, where there were 2 |
| P0-3 | Malformed model response fatal | `76babe3` | HTTP 200 + failover to mistral + audit row, where there was a 500 and no row |
| P0-2 | Failed turn recorded as success | `db08445` | `outcome: "error"` with a reason on a turn no provider served |
| P0-5 | Late provider death discards the turn | `e65d964` | partial result naming every completed tool; the created project is on disk |
| P2-2 | Unknown tool calls unaudited | `e29c402`, `fde35bd` | both paths audited — unregistered, and outside the role's list |
| P0-1 | No run identifier | `4b60618` | two identical goals back to back each return exactly their own 3 steps |
| P1-2 | No idempotency | `efc7c01` | three submissions under one key → one run, one set of side effects |
| P1-1 | Approving never executed | `4016d06` | approving runs the action; args now stored structurally |
| P2-9 | Silent provider degradation | `7e6175a` | a provider dead on every call is named on the run beside the one that served |

### Verdicts that change

| Question | Audit | Now |
|---|---|---|
| Does every execution get a unique run_id? | NO | **YES** |
| Can a past run be reconstructed? | NO | **PARTIAL** — steps, timings, status, provider failures; prompts and tool results still absent (P1-4) |
| Is approval a real security boundary? | PARTIAL | **YES** — blocks, and now completes |

Scores that move: Telemetry 1 → 3, Replayability 1 → 3, Approval enforcement
1 → 4, Failure handling 2 → 4, Security 3 → 4, External API 4 → 5.
Training-data lineage stays **0** — the raw layer now exists, but curation,
outcome signals and dataset rows do not.

### Two things worth recording about the work itself

**A green test was guarding the secret leak.** `test_filter_redacts_exception_text`
passed by pre-setting `record.exc_text`, which is the one state production
never reaches — filters run before any formatter, and `exc_text` is
populated lazily afterwards. The test asserted on a condition the runtime
cannot produce, which is why the bug survived a suite that covered it.

**iV's own self-audit caught a regression I introduced.** A test fixture
carrying an Anthropic-style key prefix tripped SEC-02 as a hardcoded
credential; the pattern requires 20+ characters after that prefix and the
fixture was the first string in the file long enough to reach it. The
fixture was renamed rather than the check loosened.

It then caught the same thing a second time, in this document: quoting the
offending literal here re-tripped the check, because SEC-02 scans tracked
markdown as well as source -- which is correct, since a real key pasted
into a doc is a real leak. Described rather than quoted, for that reason.

### Still open

- **P1-4** execution trace: prompts, tool arguments and results, token usage, latency, cost. The run record is the place for it; `ModelResponse.raw` already holds usage nobody extracts.
- **P1-3** memory is globally scoped — fine for one owner, a cross-caller leak the moment iV serves a second consumer.
- **P1-5** sync-only; no submit/poll. `GET /api/runs/{id}` is now the retrieval half, so this is a smaller change than it was.
- **P2-1** tool arguments still unvalidated against `input_schema`.
- **P2-3** an empty model response still returns 200 with an empty reply.
- **P2-4/5/6/7/8**, **P3-x** as filed.
- **Phase 4** curation, outcome signals, dataset rows.
