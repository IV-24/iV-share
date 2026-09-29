# ARCHITECTURE_MAP.md — Phase 0 (Agent-iV)

**Repo:** `IV-24/Agent-iV` @ `a5e2af4` ("Merge pull request #8 from IV-24/claude/agent-iv-parity-review-glzck0")
**Branch:** `audit/baseline`
**Date:** 2026-08-25
**Scale:** 177 Python files. ~8,900 LOC in `core/` + `interfaces/` + `adapters/`, ~1,400 LOC frontend, **7,206 LOC of tests**.
**Method:** first-hand read of the full execution path (API → orchestrator → tools → storage) plus targeted greps. Nothing executed yet — Phase 0 findings are read-verified. Tags are conservative and will be re-tested by execution in Phases 1–7.

> **Scoping note.** The audit brief was originally pointed at `IV-24/betterHalf`, which turned out to be a household chore tracker containing an unrelated assistant also called "iV". That repo's map is at `betterHalf/ARCHITECTURE_MAP.md`. This document supersedes it as the real audit target.

---

## 0. FIRST IMPRESSION, STATED PLAINLY

This is a real agent runtime, not a chat app with agent vocabulary sprinkled on it. `core/` has no imports from `adapters/` or `interfaces/`; the dependency direction is enforced and consistent. The tool registry is a genuine chokepoint. Delegation has depth, fan-out, and wall-clock bounds with a correctly-reasoned `contextvars` implementation. Provider adapters translate to one neutral shape. The code comments are unusually honest — several name the bug the code was written to fix.

Against the three goals, the picture splits cleanly:

- **Goal 1 (localhost)** — plausible. Phase 1 will test it.
- **Goal 2 (callable as a service)** — architecturally close. `/api/chat` is a clean POST with a header secret and an optional `conversation_id`. This is the best-positioned of the three.
- **Goal 3 (every execution recorded with a `run_id`)** — `MISSING`, and not partially. See §5.

The two findings that will matter most are both in §5 and §6: there is no correlation identifier anywhere in the system, and the approval gate has no path from *approved* to *executed*.

---

## 1. REPO TREE

```
Agent-iV/
├── run.py                    125 L  ENTRY. Python-version guard, preflight, uvicorn boot
├── run.sh                    ~230 L ENTRY. venv, deps, API + frontend, health-wait, Tailscale URL
├── pytest.ini                       pythonpath=., testpaths=tests
│
├── core/                    5,329 L  Pure runtime. Imports NOTHING from adapters/ or interfaces/
│   ├── agent/                 834 L  orchestrator(200) delegation(256) roles(214) store(180)
│   │                                 catalog(76) composition_tools(108)
│   ├── tools/                 ~420 L registry(96) standard(203) guardian base — THE chokepoint
│   ├── approvals/             ~200 L manager(148) + base — request/decide/execute lifecycle
│   ├── permissions/           ~340 L manager(101) levels(117) scopes — grants + agency levels
│   ├── models/                ~230 L base(101) registry(92) null_provider — provider-neutral shapes
│   ├── storage/               ~290 L local(168) memory base — SQLite + in-memory, one interface
│   ├── selfaudit/           1,089 L  checks(589) engine(404) findings(96) — iV reviewing iV
│   ├── observability/         247 L  logging(137) redaction(110)
│   ├── audit/                  80 L  log.py — the audit event store
│   ├── memory/ conversations/ projects/ tasks/ reflection/ improvements/ context/
│   ├── haven/ guardian/ safety/ games/ extensions/ environment/ configuration/
│
├── adapters/                1,853 L  Everything that touches the outside world
│   ├── models/                ~780 L gemini(216) openai_compatible(120) claude(118)
│   │                                 groq/mistral/openrouter (~10 L subclasses each)
│   ├── workspace/             ~650 L tools(196) git_ops(137) sandbox(79) shell files github config
│   ├── selfinspect/           458 L  repo(314) tools(144) — read-only access to iV's own source
│   ├── games/ notifications/
│
├── interfaces/              1,756 L
│   ├── api/                 1,303 L  main(615) health(190) runtime(162) schemas(122)
│   │                                 security(117) sleep_cycle(97) selfaudit_runner
│   └── cli/                   453 L  main(169) activity(177) selfaudit(62)
│
├── frontend/                1,395 L  Next.js 16 + React 19. Server-side proxy w/ allowlist
├── backend/                          NO CODE. .env.example, requirements*.txt, prompts/*.md
├── tests/                   7,206 L  69 files
├── supabase/migrations/        68 L  2 SQL files — LEGACY, see §6
├── scripts/                          setup-env.sh, ivctl, macos/ LaunchAgent templates
└── docs/                      19 md  README points at 14 of them
```

`backend/` contains **no Python at all** — it is a vestigial directory holding `.env`, requirements, and prompt markdown. `run.py:58` explains why: "`.env` lives alongside the old `backend/` for continuity with existing setups."

---

## 2. ENTRY POINTS

| # | Command | Process(es) | Port | Notes |
|---|---|---|---|---|
| 1 | `./run.sh` | uvicorn + `next dev` | 8024 + 4024 | venv creation, dep install, health-wait, prints Tailscale URL |
| 2 | `./run.sh --api-only` | uvicorn | 8024 | documented for a Node-less install |
| 3 | `./run.sh --dev` | + auto-reload | | sets `IV_DEV=1` |
| 4 | `python run.py` | uvicorn | 8024 | **refuses to boot without `API_ACCESS_SECRET`** (`run.py:84`) |
| 5 | `uvicorn interfaces.api.main:app` | uvicorn | — | bypasses `run.py`'s preflight *and* its `IV_LOCAL_DB_PATH` / `IV_WORKSPACE_ROOT` defaults |
| 6 | `python -m interfaces.cli.main` | one-shot | — | CLI chat |
| 7 | `python -m interfaces.cli.activity` | one-shot | — | reads the audit log — the "what did iV actually do" tool |
| 8 | `python -m interfaces.cli.selfaudit` | one-shot | — | |
| 9 | `scripts/ivctl` | remote control over SSH | — | |
| 10 | `scripts/macos/install.sh` | 3 LaunchAgents | | backend, frontend, **sleep-cycle** — the only scheduled/background execution |
| 11 | `pytest` | test run | — | |

**Background/scheduled work exists** — the sleep-cycle LaunchAgent (`scripts/macos/com.agentiv.sleepcycle.plist.template`) POSTs `/internal/sleep-cycle` behind `INTERNAL_TRIGGER_SECRET`. That is the only thing in the system that runs without a human present.

`UNSOUND` (minor) — entry #5 is a documented, supported command (`interfaces/api/main.py:6`) that skips `run.py`'s preflight. Started that way with no `API_ACCESS_SECRET`, the server boots healthy and 403s every chat request — precisely the failure `run.py:23-26` says it exists to prevent.

---

## 3. THE REAL REQUEST PATH

Traced by reading call sites, browser → reply.

```
[browser] ChatBox.jsx                 fetch("/api/iv/chat", POST {message, conversation_id?})
   │
[next]  app/api/iv/[...path]/route.js:169   resolve(segments, method) against ALLOWED allowlist
   │    :174  apiSecret() — reads API_ACCESS_SECRET out of backend/.env directly (last-wins, :87)
   │    :177  attaches X-API-Secret header. Secret NEVER reaches the browser bundle.
   │    :199  fetch(http://127.0.0.1:8024/api/chat)
   │
[api]   main.py:264  @app.post("/api/chat", dependencies=[require_chat_secret, enforce_chat_rate_limit])
   │    security.py:47  check_secret → hmac.compare_digest vs os.getenv("API_ACCESS_SECRET") → 403
   │    security.py:113 RateLimiter.allow() — in-process token bucket, 10 burst / 30 per min → 429
   │
   │    main.py:275  role = _lookup_agent(runtime, "coordinator")   ← HARDCODED. A caller
   │                 cannot name a sub-agent on this route. That is the design (main.py:15-19).
   │
[api]   main.py:216  _run_turn(runtime, role, message, conversation_id)
   │    :224  conversations.get(id) or conversations.create(title=derive_title(message))
   │    :228  history → list[ModelMessage]
   │    :232  conversations.add_message(id, "user", message)   ← persisted BEFORE the model call
   │    :239  delegation_budget.reset()   ← per-turn depth/fan-out/wall-clock, contextvar
   │
[core]  orchestrator.py:91  handle_message(role, message, history, relevant_memories=memory.recall())
   │    :100  _context_token_budget(role) → min(context_window of role's providers) − 4000
   │    :101  ContextManager.build(history, memories, max_tokens)
   │    :104  available_tools = [t for t in tools.list_tools() if t["name"] in role.tool_names]
   │
   ├─── LOOP, max 5 iterations (DEFAULT_MAX_TOOL_ITERATIONS) ──────────────────────┐
   │    :108  models.generate_with_fallback(request, role.model_provider_order)
   │          registry.py:70  walks the role's OWN provider order (coordinator:
   │                          gemini→mistral→groq; engineering/auditor/security: claude first)
   │          registry.py:77  catches ONLY ModelUnavailableError → next provider
   │          registry.py:84  logs provider/model/elapsed_ms  ← TO THE LOGGER, NOT TO STORAGE
   │    adapters/models/openai_compatible.py:102-111  try: SDK call; except Exception →
   │                          ModelUnavailableError (30s timeout, PROVIDER_REQUEST_TIMEOUT_SECONDS)
   │    :113-118  ★ OUTSIDE the try — see §7 finding F3
   │
   │    :110  if not response.requests_tool_calls → RETURN AgentTurnResult
   │    :114  _run_tool_calls(role, response.tool_calls)
   │          :151  non-delegate calls run in order
   │          :166  delegate_to_agent calls run concurrently, ≤4 workers,
   │                contextvars.copy_context() per submission so one turn shares one budget
   │          :132  run_tool → REJECTS a tool not in role.tool_names (first enforcement layer)
   │          :137  tools.execute(name, args, principal=role.name, approval_id=None)  ★ ALWAYS None
   │
[core]  tools/registry.py:51  lookup → unknown tool → clean ToolResult error
   │    :55   permission check: every tool.required_permissions vs permissions.is_granted(principal)
   │          → denied → audit "denied" + ToolResult(success=False)
   │    :65   if execution_policy == REQUIRES_APPROVAL and approval_id is None → creates an
   │          ApprovalRequest and returns error="approval_required"   ★ see §6 finding F1
   │    :79   output = tool.handler(**arguments)   ★ raw model args splatted as kwargs, no
   │          validation against input_schema
   │    :88   audit.record(actor=principal, action=f"tool.execute:{name}", resource=name, outcome)
   │
   │  (delegation: delegation.py:188 → orchestrator.handle_message(delegate_role, prompt, [])
   │   — the delegate runs as ITSELF: its own tool_names, its own scopes, its own provider order,
   │   and an EMPTY history. Capability composes; it never sums.)
   └────────────────────────────────────────────────────────────────────────────────┘
   │  (5 iterations exhausted → canned "got stuck in a tool-calling loop", orchestrator.py:120)
   │
[api]   main.py:258  conversations.add_message(id, "iv", text, model_used="provider:model")
   │    :259  audit.record(actor=role.name, action="chat.turn", resource=conversation.id,
   │                       metadata={"model_used": ...})
   │    :262  ChatResponse(response, model_used, conversation_id)
   │
[core]  storage/local.py:58  EVERY write → INSERT INTO records(collection, id, data JSON)
```

### Layers that genuinely hold

`VERIFIED-by-read` — **`core/` imports nothing from `adapters/` or `interfaces/`.** Checked across all 69 core files. The provider-neutral shape (`core/models/base.py`) is real: `ModelMessage`/`ModelRequest`/`ModelResponse`/`ToolCall`, with each adapter translating at its own boundary. Adding an OpenAI-compatible provider genuinely is a ~10-line subclass (`groq_provider.py` and `mistral_provider.py` are each 10–16 lines).

`VERIFIED-by-read` — **`ToolRegistry.execute()` is the only way a tool runs.** No caller anywhere invokes a `handler` directly. Two independent enforcement layers stack: `orchestrator.run_tool` checks `role.tool_names` (line 132), then `registry.execute` checks permission scopes (line 55). A model asking for a tool outside its role gets a clean error back as a tool result, not a crash.

`VERIFIED-by-read` — **delegation does not escalate capability.** `delegation.py:188` calls `handle_message(role, ...)` with the *delegate's* role object. The delegate's `tool_names`, `permission_scopes`, and `model_provider_order` apply. The delegation tool itself declares `required_permissions=[]` because it grants nothing.

---

## 4. WHAT EXISTS — the brief's component checklist

| Component | Status | Location |
|---|---|---|
| API layer | `VERIFIED-by-read` | `interfaces/api/main.py` — 20+ routes |
| Agent runtime | `VERIFIED-by-read` | `core/agent/orchestrator.py` |
| Master agent | `VERIFIED-by-read` (exists) | Coordinator, `core/agent/roles.py:48`. Hardcoded as the only `/api/chat` entry (`main.py:275`) |
| Subagents | `VERIFIED-by-read` (exist) | 8 roles: coordinator, planning, engineering, research, writing, memory, finance, auditor, security |
| Model adapters | `VERIFIED-by-read` | 6 providers: gemini, groq, mistral, claude, openrouter, null |
| Model router | `PARTIAL` | Per-role `model_provider_order` + `generate_with_fallback`. Ordered fallback, not capability/cost routing |
| Tool registry | `VERIFIED` | `core/tools/registry.py` — **36 tools** (confirmed live via `/status`) |
| Memory | `PARTIAL` | `core/memory/base.py`, recency + importance. Injected per turn (`main.py:245`) |
| Persistence | `PARTIAL` | SQLite, **one generic `records` table** — see §6 |
| Approval system | `BROKEN` | Exists and blocks; nothing can un-block it — §6 F1 |
| Permissions | `PARTIAL` | Real scope checks; every role self-granted at boot (`runtime.py:140`) |
| Task system | `VERIFIED-by-read` | `core/tasks/base.py` + REST + tools |
| Project system | `VERIFIED-by-read` | `core/projects/base.py` + REST + tools |
| Telemetry | `PARTIAL` | Audit log exists; **no correlation id** — §5 |
| Config | `VERIFIED-by-read` | `core/configuration/settings.py`, runtime lookup not import-time |
| Auth | `PARTIAL` | Single shared secret, `hmac.compare_digest`. No identity, no multi-user |
| Background workers | `PARTIAL` | Sleep-cycle LaunchAgent only. No queue |
| Tests | `VERIFIED-by-read` (exist) | 69 files, 7,206 LOC. Behavior assessed separately |
| Self-modification | `VERIFIED-by-read` | `adapters/selfinspect` (read-only) + `core/safety/branches.py` |

### 36 registered tools

*(Phase 0 first counted 29 by grepping `ToolDefinition(name=...)`; the 8 self-inspection tools are registered in a loop at `adapters/selfinspect/tools.py:41` and were invisible to that grep. `/status` reports 36 — corrected in Phase 1.)*

`create_project` `list_projects` `create_task` `update_task_status` `list_tasks_for_project` `create_memory` `list_recent_memories` `create_approval` `create_improvement` · `delegate_to_agent` `list_agents` `create_agent` · `flag_denied_action_spikes` `suspend_principal` · `clone_repository` `read_repository_file` `list_repository_files` `write_repository_file` `git_status` `git_diff` `git_commit` `create_branch` `run_repository_command` `git_push` `create_pull_request` · `self_list_files` `self_read_file` `self_search_repository` `self_inspect_dependencies` `self_inspect_configuration` `self_list_tests` `self_read_logs` `self_git` · `start_chess_game` `get_chess_board` `make_chess_move`

**Exactly 3 require approval:** `run_repository_command` (CRITICAL), `git_push` (HIGH), `create_pull_request` (HIGH). Everything else is `AUTO`. Notably `write_repository_file` and `git_commit` are AUTO — deliberate and documented (`roles.py:101`): the sandbox is the boundary, the network is the gate.

---

## 5. TELEMETRY & RUN IDENTITY — the goal-3 finding

`MISSING` — **There is no `run_id`, `trace_id`, `request_id`, or correlation identifier anywhere in Agent-iV.**

```
$ grep -rInE "run_id|trace_id|correlation|request_id" --include='*.py' --include='*.js' \
      --include='*.jsx' --include='*.sql' core adapters interfaces frontend supabase scripts
   (every hit was <span> in JSX or "lifespan" in FastAPI — zero real matches)
$ grep -rInE "run_id|trace_id|correlation|request_id" docs README.md tests
   (no output)
```

Not in source, not in docs, not in tests. This is not a partially-built feature; the concept is absent.

### What that costs, concretely

The audit log (`core/audit/log.py:16`) records `AuditEvent(id, timestamp, actor, action, resource, outcome, metadata)`. During one chat turn it writes:

| Event | actor | action | resource |
|---|---|---|---|
| per tool call | `Engineering Specialist` | `tool.execute:git_commit` | `git_commit` |
| per delegation | `Research Specialist` | `agent.delegate` | `Research Specialist` |
| end of turn | `Coordinator` | `chat.turn` | `<conversation_id>` |

`resource` on a tool event is the **tool name**, not the conversation. So a tool execution carries no reference to the turn, the conversation, or the user message that caused it. The only thing linking them is **timestamp adjacency**. With one user that is usually recoverable; it is not a record, it is an inference. Phase 5 will test how much of a real run can actually be reconstructed.

Additional gaps found by reading:

- `MISSING` — **token usage and cost.** `ModelResponse` (`core/models/base.py:61`) has no usage field. The provider SDK response is kept as `raw`, but nothing extracts usage from it and `raw` is never persisted.
- `PARTIAL` — **latency.** `registry.py:84-88` logs `elapsed_ms` per model call to the Python logger. Never written to storage, never correlated.
- `MISSING` — **prompts passed internally.** The system prompt is `role.description`; the assembled message list is never persisted. `agent.delegate` stores `task[:500]` in metadata (`delegation.py:209`) — the only internal prompt recorded anywhere, truncated.
- `MISSING` — **caller/app identity.** One shared secret, no principal on the request. Every external caller is indistinguishable.
- `PARTIAL` — **failure recording.** `_run_turn` (`main.py:254`) catches a generic exception, logs, and raises HTTP 500 — **before** reaching `audit.record` at line 259. A turn that crashes leaves the user's message persisted, no assistant reply, and **no audit event**. A `ModelUnavailableError` is handled differently (line 251): it produces a fallback text reply and *does* get audited.

`UNSOUND` — telemetry is written *inside* the runtime (`registry.execute`, `manager`s), which is the right side of the boundary and good news for goal 2. But `chat.turn` — the only event that names a conversation — is written in `interfaces/api/main.py`, the HTTP layer. A future non-HTTP caller (the CLI already qualifies) gets tool events with no turn-level anchor at all.

---

## 6. THE APPROVAL GATE — traced end to end

`PARTIAL` — **The gate blocks correctly and can be unlocked. But nothing in the running system ever unlocks it.**

This is a two-part finding, and the parts point opposite ways. An independent read of the test suite initially contradicted my reading of the code; verifying both by hand resolved it, and the resolution is sharper than either half.

### The primitive is sound, and genuinely proven

`tests/interfaces/test_workspace_e2e.py:130-162` is the strongest test in the repo. It clones a real git repo, calls `run_repository_command` with `touch UNAPPROVED.txt`, and asserts on the **real filesystem**:

```python
denied = runtime.orchestrator.run_tool(role, "run_repository_command", {...})
assert denied.error == "approval_required"
assert not (tmp_path / "workspace" / "target" / "UNAPPROVED.txt").exists()   # :153

runtime.approvals.decide(denied.approval_id, approved=True, decided_by="owner")
approved = runtime.orchestrator.run_tool(role, "run_repository_command", {...},
                                         approval_id=denied.approval_id)      # :156-159
assert (tmp_path / "workspace" / "target" / "UNAPPROVED.txt").exists()        # :162
```

So `ToolRegistry.execute` really does refuse to run the handler, and really does run it once given an approved `approval_id`. That half is `VERIFIED-by-read` and well-tested.

### But the production path never supplies `approval_id`

Note line 158 above: **the test passes `approval_id` itself**, calling `orchestrator.run_tool(...)` directly. That is an operator affordance, not the agent loop. Exhaustive grep of every call site:

| Caller | Passes `approval_id`? |
|---|---|
| `orchestrator._run_tool_calls:151` (sequential batch) | **No** — defaults to `None` |
| `orchestrator._run_tool_calls:168` (concurrent delegates) | **No** — defaults to `None` |
| `tests/**` | Yes, by hand |

Those two lines are the *only* callers in `core/`, `adapters/`, or `interfaces/`. So for a model-initiated call, `registry.py:66`'s condition `approval_id is None or not is_approved(...)` is **always true**.

And nothing re-issues the call after a decision:

- `POST /approvals/decide` (`main.py:594`) calls `approvals.decide(...)` and returns a `303` redirect. It re-executes nothing.
- `materialize_approved_action_items` (`core/reflection/base.py:175`) filters on `requested_by="iV Sleep Cycle"` and creates **Task rows**, not tool calls. A tool-gate approval carries `requested_by=<role name>` (`registry.py:70`), so it is never picked up.
- `core/improvements/manager.py:53` closes the loop correctly — but for improvement proposals, a different mechanism.
- `core/permissions/levels.py:108` closes it correctly too — but `levels.py` **has no caller outside tests**. Scaffolding.

### What this means in practice

A human approving `git_push` in the approvals UI changes a database row and nothing else. If the user then asks iV to try again, the model issues a fresh tool call with `approval_id=None`, which creates a **second** ApprovalRequest. Repeated attempts accumulate pending rows. The only way to actually execute the approved action is to open a Python REPL and call `orchestrator.run_tool(role, name, args, approval_id=...)` by hand.

Compounding it: `ApprovalRequest` (`core/approvals/base.py:17`) has no structured argument field. `registry.py:69` folds the arguments into a display string — `f"...with arguments {arguments!r}"`. Even a future consumer would have to parse a Python repr to know what to run.

So the brief's question — *"is approval enforced or advisory?"* — has a third answer here. It is **enforced and unreachable**. Phase 4 will confirm by execution.

`UNSOUND` — the docs describe only the first half. CORE_ARCHITECTURE.md: *"`ToolRegistry.execute()` will create an `ApprovalRequest` the first time it's called and refuse to run the handler until that request is approved."* True as far as it goes; it implies a completion that does not exist.

## 7. PERSISTENCE SHAPE — the goal-3 structural constraint

`UNSOUND` — **the entire database is one table.**

```sql
CREATE TABLE IF NOT EXISTS records (
    collection TEXT NOT NULL,
    id TEXT NOT NULL,
    data TEXT NOT NULL,          -- the whole record as a JSON blob
    PRIMARY KEY (collection, id)
)
```
`core/storage/local.py:44`

Every entity — conversations, messages, audit events, approvals, permission grants, projects, tasks, memories, improvements, agent definitions, chess games — is a JSON blob in this one table, discriminated by `collection`. Queries push equality filters through `json_extract` (`local.py:128`). There are **no indexes** beyond the composite primary key, no foreign keys, no typed columns, no joins.

For a single owner's laptop this is a defensible trade and the module argues for it well. For goal 3 it is the central design question: the three-layer lineage the brief asks for (`Raw Execution → Curated Example → Training Dataset`, provenance intact) has no relational substrate to hang off. Phase 6 will address whether that is fixable additively or needs a real schema.

`MISSING` (legacy) — `supabase/migrations/*.sql` (2 files, 68 LOC) are dead: nothing in the codebase reads Supabase. `docs/MIGRATION_AUDIT.md` records the Supabase→SQLite migration; `scripts/migrate_supabase_to_sqlite.py` is the one-shot tool. The `supabase/` directory is residue.

---

## 8. NOTED FOR LATER PHASES (recorded, not fixed)

- **F3 · `UNVERIFIED`, high-confidence — a malformed model response crashes the turn with no fallback and no audit record.** `adapters/models/openai_compatible.py:113-118` runs *outside* the `try` that converts errors to `ModelUnavailableError`. `completion.choices[0]` on an empty list → `IndexError`; `json.loads(call.function.arguments)` on malformed tool-call JSON (`from_openai_tool_calls:77`) → `JSONDecodeError`. Neither is a `ModelUnavailableError`, so `generate_with_fallback` (`registry.py:77`) does **not** catch it and does **not** try the next provider. It propagates to `_run_turn`'s generic handler (`main.py:254`) → HTTP 500, before the `audit.record` on line 259. Phase 7 will inject this.
- **`UNVERIFIED` — tool arguments are never validated.** `registry.py:79` does `tool.handler(**arguments)` with raw model output. `input_schema` is sent to the model and used for discovery, but never enforced. An unexpected key → `TypeError` → caught at line 80 → surfaced as a tool error. Safe-ish, but the schema is advisory.
- **`UNSOUND` — every role self-grants at boot.** `interfaces/api/runtime.py:140-143` grants each `DEFAULT_ROLES` entry its own declared scopes at startup, and lines 152-155 do the same for composed agents. Documented as a deliberate single-user choice. It means `permissions.is_granted` can only ever fail for a scope a role never declared — the check is real, but nothing was ever withheld.
- **`UNVERIFIED` — `create_agent` composes new agents at runtime** (`core/agent/composition_tools.py`), restricted to `COMPOSABLE_TOOLS` which excludes `delegate_to_agent` (`core/agent/store.py`). The Coordinator has `create_agent`. Phase 3/4 will test whether a composed agent can be given tools its creator lacks.
- **`UNVERIFIED` — self-modification surface.** `adapters/selfinspect` is read-only over `INSTALL_ROOT` and scoped to `self.inspect`. Separately, `adapters/workspace` can write/commit inside `IV_WORKSPACE_ROOT`. `core/safety/branches.py` + `IV_PROTECTED_BRANCHES` guard main/master/production/release. Whether iV's own install dir can be reached by a *write* tool is a Phase 7 question.
- **`UNSOUND` (minor) — `IV_HOST` defaults to `0.0.0.0`.** Deliberate and documented (`run.py:7-13`) for the phone-over-Tailscale case. The boundary is the VPN plus one shared secret.
- **`UNSOUND` (minor) — the rate limiter is per-process, in-memory.** `security.py:70`. Correct for the documented single-uvicorn deployment; silently wrong under `--workers`.
- **`PARTIAL` — the frontend proxy is a strict allowlist** (`route.js:125`): chat, health, conversations, projects, tasks, chess only. Approval-decision and `/internal/*` endpoints are deliberately unreachable from the page.

---

## 9. WHAT PHASE 0 CONCLUDES

`VERIFIED-by-read` — Agent-iV is a genuine, carefully-layered agent runtime with real enforcement chokepoints, real provider neutrality, and bounded multi-agent delegation. The architecture is not the problem.

Against the three goals:

1. **Local runnability** — untested. Phase 1.
2. **Callable as a runtime** — the closest of the three. One clean POST endpoint, header auth, optional conversation id. The coupling risk is `conversation_id` and the absence of any submit/poll pattern for long runs.
3. **Every execution recorded** — `MISSING` at the identifier level. The audit log is real and written inside the runtime, which is the right foundation; what it lacks is the one field that turns a stream of events into a run. This is the smallest high-value change in the repo, and it is additive.

---

## 10. DOCS vs CODE — the required diff

19 markdown files, 14 linked from the README. The corpus splits cleanly into **current and accurate**, **stale**, and **wrong in a way that matters**. Every row below was verified against code by hand.

### Verified accurate

| Claim | Source | Check |
|---|---|---|
| Ports 8024 / 4024, defined once in `core/configuration/ports.py` | RUNTIME.md §4 | `ports.py:25-26` — `DEFAULT_API_PORT = 8024`, `DEFAULT_FRONTEND_PORT = 4024`. ✓ |
| `MAX_DELEGATION_DEPTH = 2`, `MAX_DELEGATIONS_PER_TURN = 6` | MULTI_AGENT.md | `delegation.py:71-72`. ✓ (docs omit `TURN_WALL_CLOCK_BUDGET_SECONDS = 90.0`, `:73`) |
| `max_tool_iterations` default 5 | MIGRATION_AUDIT.md §11 | `orchestrator.py:48`. ✓ |
| "A delegate never inherits its caller's permissions" | MULTI_AGENT.md | `delegation.py:188` passes the delegate's own role. ✓ |
| `ToolRegistry.execute()` is the only path to running a tool | CORE_ARCH, HAVEN, ARCHITECTURE | No handler is invoked anywhere else. ✓ |
| Exactly 3 tools REQUIRES_APPROVAL, at the stated tiers | RUNTIME.md §9, MIGRATION_AUDIT §14 | `workspace/tools.py:171,182,195`. ✓ |
| Engineering is the only default role with repo-editing tools | MIGRATION_AUDIT §14 | `roles.py:105-110`. ✓ |
| `hmac.compare_digest` for secret comparison | CORE_ARCH | `security.py:31`. ✓ |
| Secret never reaches the browser bundle | RUNTIME.md §10.3 | `route.js:177` attaches it server-side. ✓ |
| "Five model providers behind one provider-neutral interface" | ARCHITECTURE.md | groq, gemini, mistral, claude, openrouter (`adapters/models/__init__.py:16-30`) + `null` in core. ✓ |
| Python 3.10+ hard floor, checked before any `core` import | README, RUNTIME §11 | `run.py:39-48`, above the `core` imports. ✓ |
| Server refuses to start without `API_ACCESS_SECRET` | RUNTIME.md §5 | `run.py:84`. ✓ — but only via `run.py`; `uvicorn interfaces.api.main:app` skips it |

### Stale — describes a system that has moved on

| # | Claim | Reality | Tag |
|---|---|---|---|
| D1 | **"the hierarchy above describes the *target* architecture. The current running system implements the King as a single-model reasoning loop... without separately-executing Lords, Merchants, Doctors, Guards, or Serfs."** — IV_CONSTITUTION.md "Development Status"; ARCHITECTURE.md says the same for Phase 1 | **False.** `core/agent/delegation.py` (256 LOC) is fully built and wired at `runtime.py:125`. Delegates run as separate roles on their own provider order with their own tools. MULTI_AGENT.md describes it correctly. Two docs describe delegation as not-yet-built; one describes it as built; the code agrees with the third. | `BROKEN` (as a claim) |
| D2 | "Delegations also run **sequentially**. Two independent specialists could run concurrently; nothing in the design prevents it, but shared SQLite writes and per-provider rate limits both need thought first." — MULTI_AGENT.md:95 | **False, and understated in the safe direction.** `orchestrator.py:153-172` runs `delegate_to_agent` calls **concurrently** through a `ThreadPoolExecutor` bounded by `MAX_CONCURRENT_DELEGATES = 4`, with `contextvars.copy_context()` per submission. The orchestrator's own docstring (`:20-27`) documents this. The doc was not updated when the feature landed. | `BROKEN` (as a claim) |
| D3 | Frontend proxy "exposes an allowlist of **three** API paths — chat, health, and conversation history — and nothing else." — RUNTIME.md §10.3 | **Undercounts.** `route.js:125-165` allows chat, health, conversations (list + by id), projects (list + by id), tasks (list), `tasks/{id}/status` (**POST, a write**), and chess games (list/create/get/move). The allowlist *design* is correct and real; the count is wrong and it omits that a write path is exposed. | `PARTIAL` |
| D4 | `DATA_MODEL.md` — schema with `Users` table (id, preferences, permissions), `Conversations(user_input, iV_response, agents_involved)` | **Supabase-era fiction.** Storage is one `records(collection, id, data)` table (`local.py:44`). No `Users` concept exists; `ConversationStore` is explicitly single-user with no `profile_id`. MIGRATION_AUDIT.md §6 flagged this doc for rewrite "once migration lands"; it was never rewritten. | `BROKEN` |
| D5 | `API_ARCHITECTURE.md` + `MODEL_STRATEGY.md` — "Initial Model Providers: Gemini and Mistral"; response shape `{response, model_used, timestamp}` | Pre-dates Groq, Claude, and OpenRouter. Response is `ChatResponse(response, model_used, conversation_id)` — `schemas.py`, no `timestamp`. Both docs describe an architecture that was superseded. | `BROKEN` |
| D6 | ARCHITECTURE.md Target diagram terminates in "Knowledge Base (Supabase)" | Supabase was removed entirely (MIGRATION_AUDIT §10.2). `supabase/migrations/` is dead residue. | `BROKEN` |
| D7 | `AGENT_ROLES.md` uses CEO/Lords/Guards naming; `IV_CONSTITUTION.md` lists a "Coordination Lord" | `roles.py:8-19` documents the retirement of exactly this vocabulary. The two docs describe different 8-role rosters, neither matching `DEFAULT_ROLES`. | `PARTIAL` |
| D8 | Self-audit finding SEC-BIND-01 quotes `interfaces/api/main.py:6` as saying `--port 8000` | **Already fixed.** That line now reads `--port 8024`. The 2026-08-19 audit report is a historical record; at least one of its three findings no longer reproduces. | resolved |
| D9 | DEVELOPMENT_SETUP.md: "All four adapters exist now" (Gemini, Groq, Mistral, Claude) | Five. OpenRouter (`openrouter_provider.py`, registered at `__init__.py:28`) is omitted. | `PARTIAL` |
| D10 | HAVEN.md marks "Agency levels (in progress)" and "The Guardian (in progress)" | Guardian is built and reachable (`core/tools/guardian.py`, Security role). **Agency levels are built and unreachable** — `core/permissions/levels.py` has no non-test caller. "In progress" turns out to be accurate for one and misleading for the other. | `PARTIAL` |

### The honest parts, which are worth saying

RUNTIME.md §8 states flatly: *"This runtime has no background execution. No worker, no job queue, no scheduled agent, no thread... Treat any claim of ongoing or background work as confabulation."* Code agrees — the sole exception (the Sleep Cycle LaunchAgent) is named in the same section. RUNTIME.md §10 lists what the security model does *not* do — no per-user auth, no rotation, no rate limiting on the secret, "not built to withstand a determined attacker already on the network" — and closes with the honest minimum for public exposure. HAVEN.md explicitly disclaims any emergence/novel-cognition framing. `MIGRATION_AUDIT.md §14` lists what is **not testable in this environment**, including "a real provider autonomously choosing to call these tools... rather than a scripted response."

That last admission is the repo's own statement of the gap Phase 3 exists to close.

---

## 11. TESTS — what 7,206 lines actually establish

`PARTIAL` — 63 test files, no `conftest.py` (all fixtures are per-file), CI (`.github/workflows/core-tests.yml`) runs the whole suite with a bare `pytest`, no markers or exclusions.

What is genuinely, unfakeably tested:

- **Real git subprocesses** against real repos — `tests/adapters/workspace/test_git_ops.py` (15 tests) clones, commits, branches, and verifies a real push by re-cloning the bare remote.
- **Real filesystem sandbox escapes** — `test_sandbox.py`, `selfinspect/test_repo.py` (symlink escape, traversal, flag injection, secret refusal).
- **The approval gate blocking a real side effect** — `test_workspace_e2e.py:153` asserts the file does not exist. See §6.
- **The real frontend proxy under real `node`** — `test_frontend_proxy_secret.py` executes `route.js`.
- **Storage contract run against both backends** — `test_storage_contract.py` parametrizes `["memory", "sqlite"]`.

What is faked, and matters:

- `MISSING` — **No test makes a real model API call.** No `skipif` on any provider key exists in the suite. Every provider adapter test replaces the SDK method with `MagicMock`. `test_openai_compatible_live.py`, despite the name, points at a loopback `http.server`.
- `UNVERIFIED` — **Every delegation test fakes the model.** `test_delegation.py:41`, `test_multi_agent_e2e.py:15`, `test_master_agent_contract.py:25` all use a `ScriptedProvider` returning hand-built `ToolCall` objects. `test_master_agent_contract.py` decides whether to emit a delegation by string-matching `"Top-level orchestrator"` in the system prompt — that is test-authored routing logic, not a model's decision.

Per the brief's rule #4, this puts **"the Master autonomously delegates"** at `UNVERIFIED` going into Phase 3. Everything *downstream* of the model's choice — budgets, permission checks, audit, capability non-inheritance — is real code under test and can be treated as sound. What is unproven is that a real free-tier model, given an open-ended goal, chooses to delegate at all. The repo says so itself (MIGRATION_AUDIT §14). That is the single most valuable thing Phase 3 can establish, and it costs live model calls.

- `MISSING` — **zero tests reference `run_id`, `trace_id`, `request_id`, or `correlation`**, consistent with §5.
