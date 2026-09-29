# iV's Self-Audit

The question this exists to answer: **can iV understand and critique the
system that created it?**

---

## How it is built

Two layers, deliberately separated.

**Layer 1 — deterministic checks** (`core/selfaudit/checks.py`). Twelve
checks that read iV's own source and return findings whose evidence is a
literal excerpt: file, line number, and the text of the line. No model is
involved, so nothing here can be invented, and any finding can be verified
with one `grep`. This layer runs with no API key and no network.

**Layer 2 — model review** (`core/selfaudit/engine.py`). An ordinary
`AgentOrchestrator` turn using the `auditor` role, which reaches iV's
source through the same `ToolRegistry`, the same permission checks, and
the same audit log as every other capability — the audit is not a
privileged side channel. It receives layer 1's findings as context and is
told not to repeat them, so its job is the judgment a regular expression
cannot make: architecture, coupling, whether a fragile-looking assumption
is load-bearing.

Layer 2 is optional by construction. With no provider configured the audit
still produces layer 1's findings and reports `model_review_status:
not_attempted` — never an empty result that reads like a clean bill of
health.

## Running it

```bash
python -m interfaces.cli.selfaudit --no-model --output reports/self-audit.md
python -m interfaces.cli.selfaudit --output reports/self-audit.md   # with model review
python -m interfaces.cli.selfaudit --no-model --json > audit.json   # machine-readable

curl -X POST localhost:8024/internal/self-audit \
     -H "X-Internal-Secret: $INTERNAL_TRIGGER_SECRET" \
     -H 'Content-Type: application/json' -d '{"use_model": true}'
```

The CLI exits non-zero if anything CRITICAL is found, so it works as a
pre-deploy gate. Logs go to stderr; `--json` and the report own stdout.

## What a finding contains

`ID`, `Severity` (CRITICAL/HIGH/MEDIUM/LOW/INFO), `Category`, `Location`,
`Problem`, `Evidence`, `Why it matters`, `Recommended fix`, `Confidence`,
and `Classification` (CONFIRMED BUG / LIKELY BUG / POTENTIAL RISK /
IMPROVEMENT).

Severity and confidence are separate on purpose. "CRITICAL, low
confidence" and "LOW, confirmed" are different pieces of work, and a
single blended number loses that distinction.

## Where results go

Findings are written into iV's existing mechanisms, not a side file:

- **Memory** — one reflective entry (`source="self_audit"`, importance 9)
  holding the summary and every finding's one-line form.
- **Project + tasks** — a project named `iV Self-Audit — <date>` with one
  task per CRITICAL/HIGH finding, assigned to the Engineering Specialist,
  carrying the evidence and the recommended fix.
- **Improvement proposal** — the most severe finding becomes an
  `ImprovementProposal`, which opens an approval request. A human decides.

Persistence is best-effort: a storage failure is logged and the findings
are still returned. An audit that already ran is not thrown away because a
write failed.

## What iV can and cannot do while auditing itself

The `auditor` role holds `self.inspect`, `database.read`, and
`database.write`. Its tools are the eight read-only inspection tools plus
four that record findings (`create_memory`, `create_project`,
`create_task`, `create_improvement`).

It has no write access to iV's source — not an approval-gated one, none at
all. `adapters/selfinspect` contains no write, delete, or execute
function, and a test asserts their absence so a future addition fails
loudly. `.env` files, key material, and `iv.db` are refused by name.
Git access is a fixed allowlist of seven read-only subcommands.

See `docs/RUNTIME.md` §9 for the full boundary.

## Adding a check

Write a function taking a `RepositoryReader` and returning
`list[Finding]`, then add it to `ALL_CHECKS`. Two rules learned from the
first run:

1. **Keep the evidence literal.** A finding whose evidence paraphrases
   what was found is a finding nobody can check.
2. **Precision beats recall.** The first run's highest-severity finding
   was `git_ops.py`'s own docstring promising it never uses `shell=True`
   — a check whose top hit is the codebase documenting correct behavior
   trains its reader to ignore it. That check now parses the AST. The
   untested-modules check made the same mistake in reverse, missing `from
   pkg import module` and reporting four well-tested modules as untested;
   it now resolves imports with the AST too.

Both corrections came out of running the audit for real. That is the
intended way this improves.

## First run

`docs/audits/` holds the first audit's report, kept as a record.
Subsequent runs write to `reports/` (git-ignored).
