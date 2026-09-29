# Multi-agent: delegation and composed agents

## The gap this closes

Before this, `/api/chat` picked exactly one role and that role's model
answered. The Coordinator's prompt described specialists, but it had no
way to reach one — so it did the only thing a language model can do with
a capability it lacks: describe using it. That is the mechanism behind
"I've got agents working on that in the background." The prompt implied a
team; the runtime had a single model and no way to call anyone.

Now the Coordinator can actually route work, each specialist runs on its
own model with its own tools, and every hand-off is recorded.

## How it works

```
        you
         │
    ┌────▼─────────────────────────────┐
    │ Coordinator (gemini → mistral …) │
    │  tools: delegate_to_agent,       │
    │         list_agents, create_agent│
    └────┬─────────────────────────────┘
         │ delegate_to_agent("engineering", task)
         │
    ┌────▼──────────────────┐   each delegate runs as ITSELF:
    │ Engineering           │   its own provider order, its own
    │ (claude → gemini …)   │   tools, its own permission scopes,
    │ repo tools, approvals │   its own approval gates
    └───────────────────────┘
```

Delegation is an ordinary tool, which is the point: it inherits the
role's `tool_names` check, permission scopes, approval gating, and one
audit entry per call. A delegation that happened shows up in
`python -m interfaces.cli.activity`.

**A delegate never inherits its caller's permissions.** A Coordinator
holding `database.write` and delegating to an agent without it does not
lend it — the delegate's own grants are what `ToolRegistry.execute()`
checks. Delegation composes capability; it never sums it.

### Bounds

| Bound | Value | Why |
| --- | --- | --- |
| Depth | `MAX_DELEGATION_DEPTH = 2` | A delegate may delegate, but the chain terminates. |
| Fan-out | `MAX_DELEGATIONS_PER_TURN = 6` | A confused coordinator cannot poll every specialist about the same question, on your API budget. |
| Tool iterations | `max_tool_iterations` | Already bounded the loop before delegation existed. |

Depth and fan-out are tracked per thread, since a sync FastAPI endpoint
runs in a worker thread and two conversations must not share a counter.

## Composed agents

An agent is a system prompt, a model preference, and a set of tools —
`core/agent/roles.py` already treated that as data, so composing one at
runtime needed persistence, not new machinery. A composed agent is an
`AgentRole` like any other; `AgentOrchestrator` cannot tell the
difference.

Create one in conversation:

> "Make me an agent called Grant Writer that drafts funding applications
> in plain language, using Claude, and can save notes to memory."

Or over HTTP — `GET /api/agents` lists the roster (built-in and composed,
with each one's models and tools).

### Why composing an agent is not an escalation path

Two structural properties, not policies:

1. **Scopes are derived, never supplied.** A composed agent's permission
   scopes are computed from what its tools declare, by reading the
   `ToolRegistry`. There is no field to put `permissions.manage` in.
2. **Tools come from an allowlist.** `COMPOSABLE_TOOLS` covers reads,
   planning, memory, and the ask-a-human escape hatches. Repository
   writes, command execution, pushing, and permission management are
   absent — those stay with built-in roles a human edited into source.

So the most you can compose is something that reads, plans, and asks.
Built-in names are also reserved, so a composed agent cannot shadow the
Security specialist.

## Interpreting "use all the models together"

This routes **by role**: the right specialist on the right model, results
synthesized by the Coordinator. It does not currently run several models
on the *same* question and merge the answers (an ensemble). That is a
different feature with a different cost profile — every question priced
at N models — and worth doing deliberately, if at all.

Delegations also run **sequentially**. Two independent specialists could
run concurrently; nothing in the design prevents it, but shared SQLite
writes and per-provider rate limits both need thought first.

## Verifying it happened

```bash
python -m interfaces.cli.activity
```

`agent.delegate` and `agent.create` appear as their own entries, with the
model each delegate used. If the Coordinator says it consulted a
specialist and no `agent.delegate` entry exists, it didn't.
