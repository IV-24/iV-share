# The Haven

## What this document is, and isn't

This names a boundary that already exists in the code — `core/permissions`,
`core/approvals`, `core/tools`, `core/environment` — as one concept, so
there's a single, inspectable answer to "what can iV actually do right
now." It does not introduce a new capability, and it is not a claim about
what iV *is* — this project is not attempting to produce or detect
emergent or novel machine intelligence, and nothing here should be read
that way. The framing below is deliberately narrow: safety-by-design,
human-gated agency growth, and an environment that stays inspectable as
it grows. See `docs/MIGRATION_AUDIT.md` for how this fits the broader
architecture.

## The boundary

The Haven is everything a principal (a role, an extension, eventually a
user) can do *inside* iV's own storage and tool surface, and nothing
outside it, unless a specific scope has been granted:

```
principal (a role, e.g. "Engineering Specialist")
  |
  v
core/permissions  -- does this principal hold the scope this needs?
  |
  v
core/tools        -- ToolRegistry.execute() enforces the check above,
  |                  and for REQUIRES_APPROVAL tools, an approved
  |                  ApprovalRequest, before the handler ever runs
  v
core/approvals    -- the gate for anything consequential
  |
  v
the actual effect (a database write, a file, an external call)
```

Nothing crosses a level without an explicit grant. There is no
"technically the process could" backdoor — see `core/permissions`'s own
docstring: nothing gets a capability because the underlying process
happens to have it, only because `PermissionManager` granted it.

`core/environment.discover()` is the read-only edge of the Haven: it can
report what's around it (OS, runtimes, whether known API keys are
configured — never their values) without ever modifying, installing, or
executing anything as a side effect of looking.

## The manifest

`core/haven.build_haven_manifest()` is a read-only snapshot of the
current boundary: every registered tool (with its risk level and
execution policy), every active permission grant (principal, scope, who
granted it, when), and the environment manifest. Building one has no
side effects — it only reports state `core/tools` and `core/permissions`
already track.

Get it via:
- `GET /haven/manifest?secret=...` (interfaces/api)
- typing `/haven` in the CLI REPL (interfaces/cli)

## Agency levels (in progress)

`core/permissions` grants are per-scope and binary. `core/permissions/levels.py`
adds a human-meaningful label — a named bundle of scopes (`observer`,
`researcher`, `builder`, `collaborator`) — derived from what a principal
currently holds, never stored as separate state that could drift out of
sync. Moving a principal to a higher level goes through
`core/approvals`, the same as any other consequential action: **iV does
not decide when its own agency grows. A human does**, every time, no
exceptions carved out for this mechanism.

## The Guardian (in progress)

`core/guardian` gives the Security specialist role (the only default
role holding `permissions.manage`) two things: a way to notice when
another principal's recent activity looks like it's drifting outside its
declared scope (repeated denied actions), and a way to immediately
suspend a principal's grants if something looks wrong. The suspension
itself is not approval-gated — delaying an emergency stop until a human
signs off defeats the point of having one — but it is unconditionally
audited, reversible (re-granting goes through the normal path), and the
Guardian's own actions are logged exactly like anything else's. It has
no special ability to grant itself new scopes; `core/permissions` doesn't
carve out an exception for it.

## What's deliberately not here

No autonomous loop decides to expand its own access. No mechanism claims
to detect or produce novel cognition. The honest scope of this project is
an inspectable, permission-scoped, approval-gated runtime that a human
can grow the agency of over time, and can always fully audit. If that
turns out to be interesting for other reasons, that's a question for
observation and discussion, not something the architecture asserts.
