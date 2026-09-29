# RUNBOOK.md — Phase 1 (Agent-iV)

**What this is:** the setup sequence that actually worked, executed against a genuinely clean clone (no `.env`, no `venv`, no `node_modules`, no `iv.db`) on 2026-08-25. Every command below was run; every output quoted was observed.

**Environment tested:** Linux 6.18, Python 3.11.15, Node v22.22.2, npm 10.9.7.
**Not tested:** macOS. The repo is macOS-first — `scripts/macos/install.sh` (LaunchAgents), `scripts/ivctl`, and Tailscale integration are all untested here and stay `UNVERIFIED`.

---

## THE HEADLINE

> **Can a person clone this and get a working iV on localhost today?**
>
> **Yes — the documented sequence worked verbatim, with zero undocumented steps.**

That is an unusual result and it deserves to be stated plainly. Two commands from the README took a cold clone to a serving API and a serving frontend with no intervention, no missing dependency, no path fix, and no doc correction needed to get there. The health-wait in `run.sh` is real, the preflight in `run.py` is real, and both fire before anything confusing can happen.

One qualifier, and it matters: **iV boots without a provider key but cannot answer anything without one.** See §5.

---

## 1. THE WORKING SEQUENCE

```bash
git clone https://github.com/IV-24/Agent-iV.git
cd Agent-iV

scripts/setup-env.sh          # creates backend/.env, generates API_ACCESS_SECRET
# edit backend/.env — add ONE provider key (see §5). Without it iV boots but cannot reply.

./run.sh                      # venv + deps + API + frontend, with a health gate
```

Then open **http://localhost:4024**.

That is the whole thing. It is exactly what the README says.

### Observed timings (cold clone, this environment)

| Stage | Roughly |
|---|---|
| `scripts/setup-env.sh` | instant |
| venv creation + `pip install -r backend/requirements.txt` | ~1–2 min |
| API boot to first healthy `/health` | ~1 s |
| `npm install` (frontend, cold) | ~1 min |
| `next dev` first compile before the page serves | ~10–40 s (see §4, item 3) |
| `pytest` (full suite) | 102 s |

---

## 2. WHAT STARTS, AND WHERE

| Process | Port | Started by | Notes |
|---|---|---|---|
| uvicorn / `interfaces.api.main:app` | **8024** | `python run.py` (via `run.sh`) | binds `0.0.0.0` by default — deliberate, see §6 |
| `next dev` | **4024** | `run.sh` | proxies to the API server-side; browser never sees the secret |

Both defaults live in `core/configuration/ports.py` (`DEFAULT_API_PORT = 8024`, `DEFAULT_FRONTEND_PORT = 4024`) and are overridable with `IV_PORT` / `FRONTEND_PORT`. `tests/core/test_ports.py` asserts these have not drifted across `run.sh`, the proxy route, and the docs — and that test passes.

**Ctrl+C shuts down both cleanly.** Verified accidentally and then deliberately: `run.sh`'s trap signals the whole process group, so `next dev` does not survive as an orphan holding its port. (This was a bug the project's own self-audit fixed; the fix works.)

---

## 3. EXPECTED STARTUP OUTPUT

A correct cold start looks like this:

```
===> iV — local runtime
===> Branch: main
===> Creating Python virtual environment (Python 3.11.15)...
===> Starting iV API on port 8024...
===> Waiting for /health.
2026-08-25T04:07:59+0000 INFO  iv: iV server binding 0.0.0.0:8024 (dev_mode=False)
Local:     http://localhost:8024, http://127.0.0.1:8024, http://192.0.2.2:8024
Tailscale: unavailable — tailscale CLI not found — remote access over the tailnet is unavailable from here.
2026-08-25T04:07:59+0000 INFO  uvicorn.error: Started server process [370]
2026-08-25T04:08:00+0000 INFO  interfaces.api.main: iV runtime starting (version=2.2.0)
2026-08-25T04:08:00+0000 INFO  interfaces.api.main: iV runtime ready: providers=null tools=36
2026-08-25T04:08:00+0000 INFO  uvicorn.error: Application startup complete.
===> API healthy: {"status":"degraded",...}
===> Installing frontend dependencies...
===> Starting chat frontend on port 4024...
===================== iV is up =====================
```

`providers=null tools=36` is the line worth reading. `tools=36` means the registry loaded. `providers=null` means **no real provider is configured** — see §5.

### Warnings that are safe to ignore

| Message | Why it's fine |
|---|---|
| `Tailscale: unavailable — tailscale CLI not found` | Only affects remote phone access. Localhost is unaffected. |
| `[notice] A new release of pip is available: 24.0 -> 26.2.1` | Cosmetic pip nag. |
| `✓ Generated AGENTS.md and CLAUDE.md for AI agents` | Next.js 16 writes these into `frontend/`. Already gitignored (`.gitignore:258-259`) — the working tree stays clean. |
| `"status":"degraded"` in `/health` | Expected with no provider key. It means "serving, but no model". Not a failure. |

---

## 4. WHERE THE DOCS ARE WRONG OR INCOMPLETE

The setup docs are accurate. These are corrections to *surrounding* claims found while running it.

1. **Test count.** README says the suite is what `pytest` runs; `CORE_ARCHITECTURE.md` and `MIGRATION_AUDIT.md` say **233 tests**. Actual, observed:
   ```
   510 passed in 102.05s (0:01:42)
   ```
   No failures, no skips, no errors. The docs undercount by more than half — they were written earlier and never updated. `VERIFIED`

2. **`backend/.env.example:11` — "iV runs with none, degraded".** Misleading. The *server* runs with no provider key. The *assistant* cannot answer a single message. Every chat turn returns the no-model apology. See §5. `PARTIAL`

3. **`run.sh` advertises the frontend before it can serve.** It prints the "iV is up" banner and the frontend URL as soon as it has *launched* `next dev`, not when Next has finished compiling. On a cold start the advertised `http://localhost:4024` refuses connections for another 10–40 s. The API is correctly health-gated (`run.sh:147-158`); the frontend is not gated at all. Harmless once you know, confusing on a first run — it looks like the app is broken. `scripts/smoke_test.sh` compensates with its own readiness loop. `UNSOUND` (minor)

4. **`INTERNAL_TRIGGER_SECRET` is empty after `setup-env.sh`.** `scripts/setup-env.sh` generates `API_ACCESS_SECRET` but leaves this one blank. Every `/internal/*` endpoint (sleep-cycle, materialize-backlog, self-audit) therefore returns 403 until you set it by hand. This is a *safe* default — `security.py:31` fails closed when the expected value is unset — but the README's quick start does not mention it, so the nightly Sleep Cycle silently does nothing on a fresh install. `PARTIAL`

5. **Entry point #5 skips preflight.** `interfaces/api/main.py:6` documents `uvicorn interfaces.api.main:app --host 0.0.0.0 --port 8024` as a way to run. That path bypasses `run.py`'s `preflight()` — so it will happily boot with no `API_ACCESS_SECRET` and 403 every request, which is precisely the failure `run.py:23-26` says it exists to prevent. Use `python run.py` or `./run.sh`. `UNSOUND`

---

## 5. ENVIRONMENT VARIABLES — truly required vs optional

### Required

| Var | Why | What happens without it |
|---|---|---|
| `API_ACCESS_SECRET` | Gates `/api/chat`, `/status`, `/approvals/*` | **`run.py` refuses to start** and prints a ready-to-paste `echo ... >> backend/.env` line. Verified. Generated for you by `scripts/setup-env.sh`. |

### Required for iV to actually *do* anything

**At least one of** `GROQ_API_KEY`, `GEMINI_API_KEY`, `MISTRAL_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`.

Without one, this is what every single chat turn returns — observed, HTTP **200**:

```json
{"response":"iV couldn't reach any configured model just now. Check that a provider API key (GEMINI_API_KEY, GROQ_API_KEY, ...) is set, or try again shortly.","model_used":null,"conversation_id":"02ca09dc-..."}
```

Graceful, correct, and exactly what `RUNTIME.md §6` promises — a plain message, not a 500. `VERIFIED`

**Why the `null` provider does not save you.** `interfaces/api/runtime.py:118` registers `NullModelProvider` unconditionally, so it is always present. But **no role lists it**: all nine entries in `DEFAULT_ROLES` set `model_provider_order` to some combination of `gemini`/`mistral`/`groq`/`claude` (`core/agent/roles.py:88,97,117,126,133,146,155,178,191`). The `["null"]` default on the dataclass (`roles.py:44`) is overridden by every one of them. So `generate_with_fallback` walks the role's order, finds nothing registered, and raises `ModelUnavailableError`. The null provider is reachable only by a role that declares no provider order — which no built-in role does.

Consequence for this audit: **Phases 3 and 5 cannot be completed without a real key.** There is no offline path to a completed model turn.

### Optional

| Var | Default | Effect |
|---|---|---|
| `INTERNAL_TRIGGER_SECRET` | *empty* | Required for `/internal/*`. Empty ⇒ those endpoints 403. See §4.4. |
| `IV_HOST` | `0.0.0.0` | Set `127.0.0.1` for a laptop-only install. See §6. |
| `IV_PORT` | `8024` | |
| `FRONTEND_PORT` | `4024` | |
| `IV_DEV` | unset | `1` enables uvicorn auto-reload. Dev only — the reloader can leave two processes on one SQLite file. |
| `IV_LOCAL_DB_PATH` | `<install>/iv.db` | Resolved in `run.py:68` relative to the install dir, not cwd. |
| `IV_WORKSPACE_ROOT` | `<install>/workspace` | Sandbox root for the repo-editing tools. |
| `IV_PROTECTED_BRANCHES` | `main,master,production,release` | Branches iV will never commit to or push. No tool can change this. |
| `IV_SHUTDOWN_GRACE_SECONDS` | `15` | |
| `IV_LOG_LEVEL` / `IV_LOG_FORMAT` | `INFO` / `text` | `json` available. |
| `CORS_ALLOWED_ORIGINS` | localhost:4024 | Only needed for cross-origin browser access. The bundled frontend proxies server-side, so normally leave unset. |
| `GITHUB_TOKEN`, `GMAIL_*`, `NOTIFY_EMAIL` | unset | For `create_pull_request` and email notifications. |

---

## 6. SECURITY FACTS YOU SHOULD KNOW BEFORE RUNNING IT

Not hidden — all documented in `RUNTIME.md §10` — but they belong in a runbook because they change how you run it.

- **`IV_HOST` defaults to `0.0.0.0`.** iV listens on every interface out of the box. That is a deliberate choice so a phone on your tailnet can reach it (`run.py:7-13`). On an untrusted network this exposes the port to everyone on it. `IV_HOST=127.0.0.1` for laptop-only.
- **One shared secret, no identity, no rotation.** `API_ACCESS_SECRET` is the only credential. Anyone holding it is fully authorized. There is no per-user auth and no rate limiting on it (there *is* a chat rate limiter — 10 burst / 30 per min — but it is cost control, not access control).
- **The real boundary is your network.** `security.py`'s own docstring says so. Tailscale `serve`, an SSH tunnel, or a reverse proxy with its own auth is what actually keeps strangers out.
- **The secret never reaches the browser.** Verified: the Next.js proxy attaches `X-API-Secret` server-side (`route.js:177`), and the proxy allowlist blocks `/approvals/*` and `/status` (both return 404 through it). Confirmed by live request.

---

## 7. VERIFY IT WORKS

```bash
scripts/smoke_test.sh              # boots the stack, tests it, shuts it down
scripts/smoke_test.sh --no-boot    # test an already-running iV
scripts/smoke_test.sh --api-only   # skip the frontend checks
```

Exit codes are three-valued on purpose:

- **0 — PASS.** Stack up, a real model answered a real goal.
- **2 — DEGRADED.** Stack healthy and the HTTP contract holds, but no provider key, so no model answered. This is what a keyless install returns.
- **1 — FAIL.** Something is genuinely broken.

Observed on this clean clone with no provider key — all eleven checks green, exit 2:

```
==> API
  PASS  GET /health reachable (unauthenticated, as designed)
  PASS  persistence round-trip ok
  ----  no real model provider configured (health reports degraded)
==> auth
  PASS  POST /api/chat without a secret -> 403
  PASS  POST /api/chat with a wrong secret -> 403
==> one real goal through /api/chat
  PASS  response carries a conversation_id (5751d5f5-5569-45c2-aff4-a38068a6a4b1)
  PASS  response carries non-empty text
  ----  no model answered — iV reported no provider was reachable
==> persistence of that turn
  PASS  GET /api/conversations/{id} replays the turn
  PASS  audit_log has a chat.turn row (4 total)
==> frontend
  PASS  frontend serves on http://127.0.0.1:4024
  PASS  proxy forwards /api/iv/health with a server-side secret
  PASS  proxy allowlist blocks /approvals/* (404)

==> DEGRADED: the stack is healthy and the HTTP contract holds,
    but no model provider answered.
```

### Also worth running

```bash
source backend/venv/bin/activate
pytest                                          # 510 passed in ~102s
curl localhost:8024/health                      # unauthenticated
curl "localhost:8024/status?secret=$SECRET"     # providers, tools, risk tiers, protected branches
python -m interfaces.cli.activity               # what iV actually did, from the audit log
```

---

## 8. TROUBLESHOOTING (observed, not theoretical)

| Symptom | Cause | Fix |
|---|---|---|
| `iV cannot start: API_ACCESS_SECRET is not set` | No secret in `backend/.env` | Run `scripts/setup-env.sh`. It prints the exact line otherwise. |
| Every reply is "couldn't reach any configured model" | No provider key | Add one to `backend/.env` and restart. The `null` provider will not cover for you — §5. |
| `http://localhost:4024` refuses connection right after "iV is up" | `next dev` still compiling | Wait 10–40 s. §4.3. |
| `/internal/self-audit` returns 403 | `INTERNAL_TRIGGER_SECRET` empty | Set it in `backend/.env`, restart. §4.4. |
| `/health` says `degraded` | No real provider | Expected. `degraded` ≠ broken. |
| `backend/venv was built with Python X, which is too old` | venv predates a Python upgrade | `rm -rf backend/venv` and re-run. `run.sh:116-121` detects this. |
| Frontend 403 with a "does not match" message | Secret drift between `backend/.env` and `frontend/.env.local` | The proxy names which file it read. Restart the frontend after fixing — it reads env once at startup. |

---

## 9. WHAT PHASE 1 CONCLUDES

`VERIFIED` — **Local runnability is real.** A clean clone reached a serving API and frontend using only the two commands in the README, on a platform the project does not primarily target. The preflight checks, the health gate, the graceful no-provider path, and the clean shutdown all behave as documented. 510 tests pass.

`PARTIAL` — **A keyless install is a hollow one.** iV serves, persists, authenticates, and audits without a provider key, but cannot answer. For goal 1 ("I open a localhost URL, give it a goal, and it does the work"), the stack half is `VERIFIED` and the does-the-work half is blocked on a key.

Phases 3 and 5 require a real provider key and are deferred to a run on the owner's machine.
