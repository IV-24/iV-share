# iV Development Setup

## Purpose

Defines the technical environment used to develop agent-IV.

---

# Hosting

Nothing is hosted. iV runs as two local processes on the owner's laptop —
the API (uvicorn) and the chat frontend (Next.js) — reached remotely over
Tailscale rather than published to the internet. See `docs/RUNTIME.md`.

Repository:
GitHub

---

# Database

Provider:
A local SQLite file iV owns and operates directly — `core/storage/local.py`'s
`SqliteStorage`, no hosted database, no Supabase. Point `IV_LOCAL_DB_PATH`
at a file (default `iv.db` at the repo root) and iV runs fully on your
machine. See `docs/MIGRATION_AUDIT.md` for why (and how existing Supabase
data gets migrated once via `scripts/migrate_supabase_to_sqlite.py`).

Purpose:
- Store conversations
- Store memories
- Store projects / tasks
- Store approvals / improvements

---

# AI Providers

Routed per-role by `core/agent/roles.py`'s `model_provider_order` (see
MODEL_STRATEGY.md for the philosophy) rather than one fixed order — each
provider still needs its key set for a role to actually route to it;
missing keys just mean `ModelRegistry` skips it and falls through to the
next name in that role's order.

- Gemini — general reasoning, research, large context (`GEMINI_API_KEY`) — adapter: `adapters/models/gemini_provider.py`
- Groq — fastest, most generous free tier, quick replies (`GROQ_API_KEY`) — adapter: `adapters/models/groq_provider.py`
- Mistral — fast/cost-efficient middle tier (`MISTRAL_API_KEY`) — adapter: `adapters/models/mistral_provider.py`
- Claude — engineering/coding talent (`ANTHROPIC_API_KEY`) — adapter: `adapters/models/claude_provider.py`

All four adapters exist now — set whichever keys you have. If a role's
entire provider order has no key configured, `/api/chat` returns a clear
"no model available" message rather than crashing — see
`interfaces/api/main.py`.

Future:
Local and specialized models

---

# Environment Variables

`docs/RUNTIME.md` §4 is the authoritative table, and
`backend/.env.example` / `frontend/.env.local.example` are the copyable
templates. Two things worth calling out here because they changed:

- `API_ACCESS_SECRET` is now **required** — the server refuses to start
  without it rather than booting a runtime that 403s every request.
- The frontend's secret is `IV_API_SECRET`, a **server-side** variable.
  It replaced `NEXT_PUBLIC_API_SECRET`, which Next.js inlined into the
  client bundle where anyone loading the page could read it. The browser
  now talks to a same-origin proxy route (`frontend/app/api/iv/`) and
  never sees a credential.

---

# Running it

`docs/RUNTIME.md` is the operational document — startup commands, every
environment variable, remote access over Tailscale, the safety boundary,
and troubleshooting. The short version:

```bash
cp backend/.env.example backend/.env          # API_ACCESS_SECRET is required
cp frontend/.env.local.example frontend/.env.local
./run.sh                                      # API + frontend, prints every URL
```

Other entrypoints: `./run.sh --dev` (auto-reload), `./run.sh --api-only`,
`python run.py` (API only), `python -m interfaces.cli.main [role]` (REPL),
`python -m interfaces.cli.selfaudit --no-model` (self-audit).

For a service that survives logout and reboot on macOS, see
`docs/RUNTIME.md` §2 and `scripts/macos/install.sh`.

If you have a real `iv.db` already (e.g. migrated from a prior Supabase
setup via `scripts/migrate_supabase_to_sqlite.py`), place it at the repo
root before starting, or point `IV_LOCAL_DB_PATH` at wherever it lives.

## Scheduling the Sleep Cycle (optional)

`scripts/macos/install.sh` doesn't install this automatically — it needs
`INTERNAL_TRIGGER_SECRET` set in `backend/.env` first. Once you've set
that secret:

```bash
mkdir -p ~/Library/LaunchAgents .launchd
sed "s#__REPO_DIR__#$(pwd)#g" scripts/macos/com.agentiv.sleepcycle.plist.template \
    > ~/Library/LaunchAgents/com.agentiv.sleepcycle.plist
launchctl load -w ~/Library/LaunchAgents/com.agentiv.sleepcycle.plist
```

Runs daily at 11:30pm, calling `scripts/macos/run_sleep_cycle.sh`, which
triggers `/internal/sleep-cycle` and then `/internal/materialize-backlog`
— the second call turns whatever's been approved into real `Task` rows
under an "iV Improvements" project. Requires `com.agentiv.backend` to
already be running. Logs: `.launchd/sleepcycle.log`.

# Development Principles

1. Security first
2. Human approval for consequential actions
3. Modular design
4. Replaceable components
5. Test before deployment

---

# Development Workflow

main
|
Production

develop
|
Testing

feature branches
|
Experiments and improvements