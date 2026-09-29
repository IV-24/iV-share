# iV Runtime — running it, reaching it, and what it is allowed to do

This is the operational document: what the system actually is today, the
exact commands to run it, how to reach it from a phone, and where the
safety boundary sits. `docs/CORE_ARCHITECTURE.md` explains *why* core is
shaped the way it is; this explains *how the thing runs*.

---

## 1. Runtime architecture (what actually exists)

```
                     YOUR LAPTOP
                          │
   ┌──────────────────────┴──────────────────────┐
   │                                             │
┌──▼───────────────────────┐        ┌────────────▼─────────────┐
│  Next.js frontend :4024  │        │  iV API (uvicorn) :8024  │
│                          │        │  interfaces/api/main.py  │
│  components/ChatBox.jsx  │        │                          │
│           │              │        │  /health   /status       │
│           ▼              │        │  /api/chat               │
│  app/api/iv/[...path]    │───────▶│  /api/conversations/{id} │
│  server-side proxy       │  HTTP  │  /approvals/*            │
│  (holds IV_API_SECRET)   │  loop  │  /internal/self-audit    │
└──────────────────────────┘        └────────────┬─────────────┘
            ▲                                    │
            │ same-origin                        ▼
            │ relative URLs         ┌──────────────────────────┐
            │                       │  interfaces/api/runtime  │
            │                       │  builds the core stack   │
            │                       └────────────┬─────────────┘
            │                                    │
            │            ┌───────────────────────┼───────────────────────┐
            │            ▼                       ▼                       ▼
            │  ┌──────────────────┐  ┌───────────────────┐  ┌──────────────────┐
            │  │ AgentOrchestrator│  │  ToolRegistry     │  │  SqliteStorage   │
            │  │ core/agent       │  │  permissions +    │  │  iv.db           │
            │  │ tool-call loop   │  │  approvals gate   │  │  (single file)   │
            │  └────────┬─────────┘  └─────────┬─────────┘  └──────────────────┘
            │           │                      │
            │           ▼                      ├── standard tools (projects/tasks/memory)
            │  ┌──────────────────┐            ├── guardian tools (security role only)
            │  │  ModelRegistry   │            ├── workspace tools (sandboxed clones, WRITE)
            │  │  provider-neutral│            └── self-inspection tools (own source, READ-ONLY)
            │  │  + fallback order│
            │  └────────┬─────────┘
            │           │
            │           ├── gemini    adapters/models/gemini_provider.py
            │           ├── groq      ─┐
            │           ├── mistral    ├ OpenAICompatibleProvider
            │           ├── openrouter ─┘
            │           ├── claude    adapters/models/claude_provider.py
            │           └── null      no key, no network (always registered)
            │
   ┌────────┴────────┐
   │  Tailscale VPN  │
   └────────┬────────┘
            │
        YOUR iPHONE  →  http://<laptop>.<tailnet>.ts.net:4024
```

Process boundaries: **two** processes — uvicorn (API) and Next.js
(frontend). They talk over loopback. Nothing else is required; there is no
database server, no queue, no external service.

Data flow for one message:

```
phone → /api/iv/chat (Next server, adds X-API-Secret)
      → POST /api/chat (FastAPI, validates secret)
      → ConversationStore.history()          (SQLite)
      → AgentOrchestrator.handle_message()
          → ModelRegistry.generate_with_fallback(role.model_provider_order)
          → [if the model asks for a tool] ToolRegistry.execute()
              → permission check → approval check → handler
          → loop until a text answer or max_tool_iterations
      → ConversationStore.add_message() ×2   (SQLite)
      → AuditLog.record()                    (SQLite)
      → response back down the same path
```

---

## 2. Starting iV

**One command:**

```bash
./run.sh
```

That creates/syncs the Python venv, starts the API, waits for `/health`
to report ready, starts the frontend, and prints every URL the service is
reachable on — including the Tailscale one, discovered at runtime. Ctrl+C
stops both cleanly.

Variants:

```bash
./run.sh --dev        # uvicorn auto-reload (editing code)
./run.sh --api-only   # no frontend
python run.py         # API only, no venv/dependency management
python -m interfaces.cli.main [role]   # REPL against the same core stack
```

**Persistent service (survives logout and reboot), macOS:**

```bash
./run.sh              # once, so the venv and node_modules exist
scripts/macos/install.sh
```

- Status: `launchctl list | grep agentiv`
- Logs: `.launchd/backend.log`, `.launchd/backend.error.log`
- Stop: `scripts/macos/uninstall.sh`

Two things LaunchAgents will not do for you: they start at **login**, not
at boot (enable automatic login if you want it up with nobody at the
keyboard), and they cannot keep a sleeping Mac on the network (System
Settings → Battery → "Prevent automatic sleeping when the display is
off").

---

## 3. Controlling iV remotely (from your phone)

`scripts/ivctl` is the control surface, built for typing over SSH:

```bash
ivctl status         # both halves: running? healthy? managed how?
ivctl restart        # the one you actually want
ivctl stop
ivctl start
ivctl logs -f
ivctl health

ivctl restart api    # backend only
ivctl restart web    # frontend only
```

**iV is two processes** — the API on 8024 and the chat frontend on 4024 —
and the page you open on a phone is the frontend. Commands act on both
unless you name one, because restarting only the API leaves the half you
can actually see running old code. `stop` and `restart` take the frontend
down first: it proxies to the API, so the other order leaves a window
where the page loads and every request 502s.

### Setting it up

1. **Enable SSH on the Mac** — System Settings → General → Sharing →
   **Remote Login** on.
2. **Connect over Tailscale**, not the public internet. In Termius, host
   = the laptop's Tailscale IP or MagicDNS name, user = your macOS
   username. Key-based auth beats a password.
3. Optionally add a shortcut so it is one word:
   ```bash
   echo 'alias ivctl="~/dev/agent-iv/scripts/ivctl"' >> ~/.zshrc
   ```

### Two things that make remote restart different

**A process started over SSH dies when the session ends.** Running
`./run.sh` from Termius gives you an iV that lasts until you close the
app. `ivctl start` detaches the process (setsid/nohup, stdio redirected
away from the terminal) so it survives disconnection.

**If the LaunchAgent is installed, launchd owns the process** — killing
it directly just makes launchd start a new one. `ivctl` detects which
mode you are in and uses the matching mechanism (`launchctl kickstart -k`
vs. signal-and-respawn), so the same command works either way.

For a laptop that should always be running iV, install the LaunchAgent
(§2) and remote restart becomes a single reliable verb.

### Killing a server started in another terminal

That is `ivctl stop`, and it is safe to run from anywhere — including
while the original Terminal window is still open (it will show the
shutdown and return to a prompt). It sends SIGTERM first so the FastAPI
lifespan closes the SQLite handle, waits 15 seconds, and only then
escalates to SIGKILL.

Note that `./run.sh` starts both processes and ties them to that
terminal. If you started iV that way and then run `ivctl stop` over SSH,
`run.sh` itself notices its children exiting and returns to a prompt.

### After a `git pull`, restart before testing

Pulling changes the files on disk; it does not change what is running.
Python holds the old code in memory until the process restarts, and the
Next.js server reads its env once at startup. So:

```bash
cd ~/dev/agent-iv && git pull && scripts/ivctl restart
```

Then reload the page. `ivctl status` confirms both halves came back
before you go looking for a bug that is really a stale process.

It identifies the server by verifying each candidate process's actual
command, not by pattern-matching command lines: `pgrep -f run.py` also
matches an editor with the file open and the shell running the script
itself, and a stop command that swept those up would be worse than none.

## 4. Access

| What | URL |
| --- | --- |
| Chat frontend | `http://localhost:4024` |
| API | `http://localhost:8024` |
| Health | `http://localhost:8024/health` |
| Runtime status | `http://localhost:8024/status?secret=$API_ACCESS_SECRET` |
| Approvals | `http://localhost:8024/approvals/pending?secret=$API_ACCESS_SECRET` |
| From another device | `http://<tailscale-name-or-ip>:4024` |

**Why 8024 and 4024, not 8000 and 3000:** those two are the most
contended ports on a developer laptop — 3000 is every Next/React/Rails
dev server, 8000 is Django and `python -m http.server`. iV runs all day
as a background service and should not lose a coin flip with whatever
else you started that morning. Override with `IV_PORT` and
`FRONTEND_PORT` if these collide with something too; the defaults are
defined once in `core/configuration/ports.py`.

```bash
IV_PORT=9100 FRONTEND_PORT=9101 ./run.sh
```

`./run.sh` prints the real values for the last row. To find them yourself:

```bash
tailscale ip -4                       # e.g. 100.x.y.z
tailscale status --json | grep DNSName   # e.g. my-laptop.tailnet-1234.ts.net
```

No Tailscale address is stored in this repository. The frontend talks to
the API through its own origin using relative URLs, so the page works
unchanged on localhost, on the LAN address, and on the tailnet address.

### iPhone setup

1. Install Tailscale on the phone and sign into the same tailnet.
2. Make sure the laptop is up (`tailscale status` lists it) and awake.
3. Set `IV_ALLOWED_DEV_ORIGINS` in `frontend/.env.local` to the laptop's
   Tailscale IP and/or MagicDNS name — `next dev` rejects cross-origin
   requests from hosts it was not told about. (Not needed for a
   production build: `npm run build && npm run start`.)
4. Open `http://<that address>:4024` on the phone. Add to Home Screen for
   an app-like launcher.

---

## 5. Configuration

Every variable is read at runtime through `core/configuration/settings.py`
(or an adapter's own config module). Nothing is hardcoded, and no secret
is committed. Copy `backend/.env.example` → `backend/.env` and
`frontend/.env.local.example` → `frontend/.env.local`.

**Required**

| Variable | Purpose |
| --- | --- |
| `API_ACCESS_SECRET` | Gates `/api/chat`, `/status`, `/approvals/*`. The server refuses to start without it. |
| `IV_API_SECRET` (frontend) | **Optional.** Leave unset on a same-machine install: the frontend reads `API_ACCESS_SECRET` from `backend/.env` directly, so there is one source of truth. Set it only when the frontend runs where `backend/.env` does not exist. Server-side only — never reaches the browser. |
| `IV_API_URL` (frontend) | Where the Next.js server reaches the API. Defaults to `http://127.0.0.1:8024`. |

**Model providers** — set whichever you have; iV runs (degraded) with none.

`GEMINI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `ANTHROPIC_API_KEY`,
`OPENROUTER_API_KEY` (+ optional `OPENROUTER_MODEL`).

**Server / storage / safety**

| Variable | Default | Notes |
| --- | --- | --- |
| `IV_HOST` | `0.0.0.0` | Set `127.0.0.1` for a laptop-only install. |
| `IV_PORT` | `8024` | Frontend port is `FRONTEND_PORT`, default `4024`. |
| `IV_DEV` | unset | `1` enables uvicorn's reloader. Development only. |
| `IV_SHUTDOWN_GRACE_SECONDS` | `15` | |
| `IV_LOG_LEVEL` / `IV_LOG_FORMAT` | `INFO` / `text` | `json` for structured logs. |
| `IV_LOCAL_DB_PATH` | `<install-dir>/iv.db` | |
| `IV_WORKSPACE_ROOT` | `<install-dir>/workspace` | Sandbox for repo-editing tools. |
| `IV_PROTECTED_BRANCHES` | `main,master,production,release` | iV cannot change this. |
| `INTERNAL_TRIGGER_SECRET` | — | Required for `/internal/sleep-cycle` and `/internal/self-audit`. |
| `CORS_ALLOWED_ORIGINS` | localhost:4024 | Only needed for a browser calling the API cross-origin; the bundled frontend does not. |
| `IV_ALLOWED_DEV_ORIGINS` (frontend) | unset | Hosts allowed to reach `next dev` cross-origin. |

Optional: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `NOTIFY_EMAIL`,
`GITHUB_TOKEN`, `AGENT_IV_BASE_URL`.

---

## 6. Model provisioning

`adapters/models/register_configured_providers()` registers one
`ModelProvider` per configured API key and skips the rest — a missing key
is "not configured", never an error. Each role in `core/agent/roles.py`
declares a `model_provider_order`; `ModelRegistry.generate_with_fallback`
walks it, skipping unregistered providers and moving on when one raises
`ModelUnavailableError`. If a role's whole order is unavailable, `/api/chat`
returns a plain "couldn't reach any configured model" message rather than
a 500.

Check what is provisioned:

```bash
curl "localhost:8024/status?secret=$API_ACCESS_SECRET" | jq .model_harness
```

`/health` reports `degraded` when only the `null` provider is registered —
the server is running correctly, it just has no real model.

Adding a provider is one file plus one `if` in
`adapters/models/__init__.py`. Providers speaking OpenAI's wire format
subclass `OpenAICompatibleProvider` and need about ten lines. Core is
untouched either way; no agent code knows a provider's name beyond the
string in a role's fallback order.

---

## 7. Self-inspection and the self-audit

iV can read its own source through eight read-only tools
(`adapters/selfinspect/`), gated on the `self.inspect` permission scope
and held by exactly one role, `auditor`.

Run the audit:

```bash
# Deterministic checks only — no provider key needed, fully reproducible
python -m interfaces.cli.selfaudit --no-model --output reports/self-audit.md

# With the model review layer, once a provider key is configured
python -m interfaces.cli.selfaudit --output reports/self-audit.md

# Over HTTP (operator action; needs INTERNAL_TRIGGER_SECRET)
curl -X POST localhost:8024/internal/self-audit \
     -H "X-Internal-Secret: $INTERNAL_TRIGGER_SECRET" \
     -H 'Content-Type: application/json' -d '{"use_model": true}'
```

Results land in iV's own stores: a reflective memory holding the summary,
a project named `iV Self-Audit — <date>` with one task per CRITICAL/HIGH
finding, and an improvement proposal (which opens an approval request) for
the most severe one. See `docs/SELF_AUDIT.md`.

---

## 8. Did iV actually do that?

Worth knowing before you trust a transcript: **this runtime has no
background execution.** No worker, no job queue, no scheduled agent, no
thread. `AgentOrchestrator.handle_message` runs inside a single HTTP
request and stops when the reply is sent. Between your messages, iV is
not running.

A model can nonetheless say "I've got agents working on that in the
background" — it is a fluent, plausible sentence, and nothing in the
architecture makes it true. Treat any claim of ongoing or background work
as confabulation.

What *is* trustworthy is the audit log. Every tool invocation goes
through `ToolRegistry.execute()`, which records an `AuditEvent` whether it
succeeded, was denied, or errored. If an action is not in that log, it did
not happen.

```bash
python -m interfaces.cli.activity                 # last 7 days
python -m interfaces.cli.activity --since 2026-08-19
python -m interfaces.cli.activity --json          # raw events
```

The report separates two things that are easy to confuse:

- **Tool executions** — real actions, with their outcome.
- **Task status** — a value written into a row. iV can create a task
  already marked `in_progress` without doing anything at all. Only
  `update_task_status` moves one afterwards, and that call is audited, so
  the report counts those separately.

The one thing that does run unattended is the Sleep Cycle, and only if
you installed its LaunchAgent (see §2) — it appears in the same log under
its own entries.

## 9. Safety boundary

**iV may, without asking:** read its own source; read, write, and commit
inside a sandboxed clone under `IV_WORKSPACE_ROOT`; create projects,
tasks, memories, reflections; propose improvements and approvals.

**iV must get a human's approval for:** running any command in a repo
(`run_repository_command`, CRITICAL), pushing to a remote (`git_push`),
opening a pull request (`create_pull_request`).

**iV cannot do at all:**

| Boundary | How it is enforced |
| --- | --- |
| Commit or push to `main` (or any protected branch) | `core/safety/branches.py`, checked inside `git_ops` before staging. Not a prompt instruction. |
| Write to its own installation directory | `adapters/selfinspect` has no write, delete, or execute function. A test asserts the absence. |
| Read `.env`, key material, or `iv.db` | Refused by name in `adapters/selfinspect`, even from inside the install root. |
| Run an arbitrary git subcommand against itself | Fixed allowlist of seven read-only commands. |
| Change its own guardrails | `IV_PROTECTED_BRANCHES` is environment configuration; no tool reads or writes it on iV's behalf. |
| Grant itself permissions | `PermissionManager` grants come from bootstrap or an approved `apply_level_change`; every grant is audited. |
| Escape the workspace sandbox | `adapters/workspace/sandbox.py` — absolute paths, `..`, and symlink escapes all rejected. |

`suspend_principal` (revoking every scope a principal holds) is
deliberately *not* approval-gated: an emergency stop that needs a
signature is not an emergency stop. It can only revoke, never grant.

---

## 10. Security assumptions

Stated plainly, because the design depends on them:

1. **Tailscale (or an equivalent private network) is the real access
   boundary.** The API binds `0.0.0.0` so a phone can reach it. Anything
   that can route to port 8024 can attempt requests.
2. **`API_ACCESS_SECRET` is the second layer, not the first.** It is a
   single shared secret with no rotation, no per-device identity, and no
   rate limiting. It stops something that merely routes to the box; it is
   not built to withstand a determined attacker already on the network.
3. **The frontend holds no credential.** The secret lives in the Next.js
   server process and is injected server-side; it is not in the browser
   bundle. The proxy exposes an allowlist of three API paths — chat,
   health, and conversation history — and nothing else.
4. **Approval links are the one place a secret appears in a URL.** The
   emailed link has nowhere else to put it; on first use the secret moves
   into an `HttpOnly` cookie and the browser is redirected to a clean URL.
5. **Secrets are redacted from logs by value.** Registered at startup and
   scrubbed on the way out, including uvicorn's access log — see
   `core/observability/redaction.py`.
6. **Anything the model reads is untrusted input.** A repository iV clones
   can contain text aimed at iV. What limits the damage is the tool
   surface, not the model's judgment: the destructive capabilities need
   human approval, and the self-inspection surface has no write path.

If you expose this beyond a private network, the honest minimum is: real
per-user authentication, rate limiting, HTTPS, and a review of every
`REQUIRES_APPROVAL` tier.

---

## 11. Troubleshooting

**`iV needs Python 3.10 or newer`** — 48 modules use the `str | None`
type syntax introduced in 3.10. macOS 11 and 12 ship 3.8/3.9 as the
default `python3`. Install a newer one from
https://www.python.org/downloads/macos/ (supports macOS 11+), or point at
an interpreter you already have: `PYTHON_BIN=/usr/local/bin/python3.11
./run.sh`. If `backend/venv` was already built by the old interpreter,
`rm -rf backend/venv` first — upgrading the system Python does not
migrate an existing virtualenv.

**`./run.sh: Permission denied`** — the executable bit is missing. It is
committed (`100755`), so a `git clone` has it; a GitHub **ZIP download**
strips it, and unpacks to a folder named `<repo>-<branch>` such as
`agent-iv-main`. Either `chmod +x run.sh` or run it as `bash run.sh`.

If the folder is named `...-main`, check you have the code you expect
before debugging further — `grep -c api-only run.sh` returns 0 on a
revision that predates the flag. Cloning the branch you want avoids both
problems at once.

**A key defined twice in `backend/.env`** — `cp backend/.env.example
backend/.env` leaves `API_ACCESS_SECRET=` empty near the top of the file.
If you then *append* a real value rather than filling that line in, the
key is defined twice. Everything reading the file resolves a duplicate to
the **last** occurrence (python-dotenv's rule, which the frontend proxy
matches), so this usually still works — but it is worth cleaning up:
`grep -n '^API_ACCESS_SECRET=' backend/.env` should print exactly one
line. Delete the empty one with
`sed -i '' '/^API_ACCESS_SECRET=$/d' backend/.env`.

**Frontend LaunchAgent logs `command not found: npm`, repeatedly** — the
agent cannot find node. launchd does not run a login shell, and even a
login shell would not source `.zshrc`, which is where nvm, volta, and
most node installers put node on PATH. `scripts/macos/install.sh`
resolves npm's absolute path at install time and bakes it into the plist,
so the fix is to re-run it from a terminal where `npm -v` works:

```bash
scripts/macos/install.sh
scripts/ivctl status
```

The repeating log lines are launchd's `KeepAlive` retrying every
`ThrottleInterval` (10s), not ten separate faults.

**`iV cannot start: API_ACCESS_SECRET is not set`** — deliberate. Add it to
`backend/.env`; the error prints a ready-to-paste command.

**`/health` says `degraded`** — the server is fine; no real model provider
is configured. Add a provider key and restart. `curl
"localhost:8024/status?secret=..."` shows which are registered.

**Chat replies "couldn't reach any configured model"** — every provider in
that role's fallback order is missing or failing. Check the log for
`model provider unavailable: provider=...`; the reason is logged with the
key redacted.

**Frontend shows "Lost the connection to iV"** — the page's own origin is
unreachable: laptop asleep, Next.js stopped, or the phone dropped off the
tailnet. `tailscale status` on both ends.

**Phone shows a cross-origin/blocked-request error from Next** — set
`IV_ALLOWED_DEV_ORIGINS` in `frontend/.env.local` to the address the phone
is using, or run a production build instead of `next dev`.

**502 "iV API unreachable" from the frontend** — Next.js is up but the API
is not. Check `IV_API_URL` and `curl localhost:8024/health`.

**403 / "Invalid or missing API secret"** — the frontend's secret does not
match the backend's. On a same-machine install the fix is usually to
*delete* `IV_API_SECRET` from `frontend/.env.local` entirely and restart:
with it unset the frontend reads `API_ACCESS_SECRET` from `backend/.env`,
and the two cannot drift. The proxy's 403 message names which source it
used. Next.js reads env files once at startup, so editing one without
restarting the frontend changes nothing.

**"refusing to commit: 'main' is a protected branch"** — working as
intended. iV must `create_branch` first. See §7.

**Port already in use on restart** — the previous uvicorn is still
running. `pkill -f run.py`, or `launchctl unload` the LaunchAgent.

**Logs** — foreground: stdout. LaunchAgent: `.launchd/backend.log` and
`.launchd/backend.error.log`. `IV_LOG_LEVEL=DEBUG` for more,
`IV_LOG_FORMAT=json` for structured lines.
