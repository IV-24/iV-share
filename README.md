# agent-IV

My own AI assistant that runs on my machine, not someone else's cloud. Tell it what you want, it figures out which specialist agents should do the work, and it asks before doing anything that matters.

## The idea

I didn't want my tasks, files, and conversations living on a vendor's server. So iV runs locally: a FastAPI backend, a SQLite database, and a chat UI. It talks to whatever model provider I point it at — Groq, Claude, Mistral, OpenRouter — through one interface, and if one provider is down it tries the next.

The part I'm proudest of: approvals aren't a suggestion in a system prompt. They're enforced by the code. An agent can't act until the runtime says so, retries get deduplicated, and everything lands in an activity log I can inspect.

## How it's organized

One coordinator — I call it the King — breaks work down and hands it to specialist agents (the Lords: Planning, Engineering, Research, Writing, Memory, Finance, Security, Coordination). They fan out to worker agents when things get complicated. The naming is feudal; the structure is just a hierarchy with clear responsibilities, which turns out to be a good way to keep agents from stepping on each other.

```
You
  ↓
Chat UI / terminal
  ↓
The King ──asks──► Approval gate (code, not vibes)
  ↓
Lords (planning, engineering, research, writing, memory, finance, security, coordination)
  ↓
Worker agents
  ↓
Tools + model providers (Groq, Claude, Mistral, OpenRouter, Gemini)
  ↓
Local SQLite — your data never leaves the machine
```

## Run it

```bash
scripts/setup-env.sh    # creates backend/.env with a generated secret
# add a provider key, e.g. GROQ_API_KEY=...
./run.sh
```

Open http://localhost:4024. From your phone: the Tailscale URL `run.sh` prints. API only: `./run.sh --api-only`.

Needs Python 3.10+ and Node 20+ (frontend only).

## What's actually in here

- ~190 Python modules, 75 test files, CI running pytest + smoke tests on every push
- Provider adapters with automatic failover and a log of which providers failed
- A self-audit system: iV reads its own source and writes up findings (see `docs/audits/`)
- Docs for the parts that need explaining: [architecture](docs/ARCHITECTURE.md), [running it](docs/RUNTIME.md), [the agent model](docs/MULTI_AGENT.md), [operating principles](docs/IV_CONSTITUTION.md)

## Honest notes

- This is a personal project, built to run my own life — not a product.
- It was built with heavy AI assistance. I designed and directed it; the agents wrote most of the code. Fitting, given what it is.
- The docs are thorough to a fault. This README is the front door; `docs/` is the basement.

MIT license.
