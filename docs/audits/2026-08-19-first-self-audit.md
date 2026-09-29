# iV Self-Audit Report

- **Started:** 2026-08-19T08:57:00.816242+00:00
- **Finished:** 2026-08-19T08:57:10.892999+00:00
- **Model review:** not_attempted

## Severity counts

| Severity | Count |
| --- | --- |
| CRITICAL | 0 |
| HIGH | 0 |
| MEDIUM | 3 |
| LOW | 0 |
| INFO | 0 |

## Summary

Deterministic self-audit completed: 3 finding(s) (3 MEDIUM). Every finding below is reproducible from the repository — no model review contributed to this run.

## Findings

### SEC-BIND-01 — The server binds 0.0.0.0 (every interface)

- **Severity:** MEDIUM
- **Classification:** POTENTIAL RISK
- **Confidence:** high
- **Category:** security/network-exposure
- **Location:** `interfaces/api/main.py, run.py`
- **Source:** static

**Evidence**

```
interfaces/api/main.py:6: Run with: uvicorn interfaces.api.main:app --host 0.0.0.0 --port 8000
run.py:7: IV_HOST   default 0.0.0.0. iV is meant to be reached from another
run.py:87: host = os.getenv("IV_HOST", "0.0.0.0")
```

**Why it matters** — Binding every interface exposes the runtime to any network the laptop joins — coffee-shop wifi as readily as the private VPN it was meant for. It is the right choice only if a shared secret or an external firewall is genuinely enforcing access, and the wrong default if either is optional.

**Recommended fix** — Make the bind address configuration (IV_HOST), default it to the loopback or the VPN interface, and require an explicit opt-in for the all-interfaces bind.

### SEC-QS-01 — A shared secret is passed in a URL query string

- **Severity:** MEDIUM
- **Classification:** POTENTIAL RISK
- **Confidence:** high
- **Category:** security/secret-handling
- **Location:** `interfaces/api/sleep_cycle.py`
- **Source:** static

**Evidence**

```
interfaces/api/sleep_cycle.py:71: dashboard_url = f"{dashboard_base_url}/approvals/pending?secret={quote(api_access_secret or '')}"
```

**Why it matters** — Query strings land in server access logs, browser history, and the Referer header of any outbound link on the page — all places a secret in a header or POST body would not reach.

**Recommended fix** — Move the secret to a header or a cookie set on first use; if a clickable link must carry authorization, use a single-use, expiring token instead of the long-lived shared secret.

### TEST-01 — 3 runtime module(s) are not imported by any test

- **Severity:** MEDIUM
- **Classification:** IMPROVEMENT
- **Confidence:** high
- **Category:** testing/coverage
- **Location:** `core/storage/base.py, interfaces/api/schemas.py, run.py`
- **Source:** static

**Evidence**

```
No test file references:
  core/storage/base.py
  interfaces/api/schemas.py
  run.py
```

**Why it matters** — These modules can break without any test failing. For a system that is meant to restart itself unattended, an untested startup or persistence path is a failure that surfaces as downtime rather than as a red build.

**Recommended fix** — Add at least one test per module that exercises its main entry point and one failure mode.


---

## Operator notes on this run

Added by the engineer who ran it. The report above is iV's machine
output, unedited; this section records disposition.

### Model review did not run

`model_review_status: not_attempted` — no provider API key was configured
in the environment where this ran, so layer 2 (architecture, coupling,
agent safety, and the other judgment categories) produced nothing. Only
the deterministic layer contributed. Re-run with a provider key configured
to get the full audit; the command is in `docs/SELF_AUDIT.md`.

### Disposition of the remaining findings

| ID | Disposition |
| --- | --- |
| `SEC-BIND-01` | **Accepted, documented.** Binding `0.0.0.0` is what makes the phone-over-Tailscale case work. The mitigations are the private network, `API_ACCESS_SECRET`, and `IV_HOST=127.0.0.1` for anyone who does not need remote access. See `docs/RUNTIME.md` §8. |
| `SEC-QS-01` | **Partially fixed; residual accepted.** The browser flow now moves the secret into an `HttpOnly` cookie on first use and redirects to a clean URL, so it no longer appears in any subsequent request. The one remaining occurrence is the emailed approval link, which has nowhere else to carry it. A single-use expiring token is the real fix and is listed as future work. |
| `TEST-01` | **Check limitation, not a gap.** The three modules named are `core/storage/base.py` (an abstract interface — both implementations are now covered by a shared contract suite in `tests/core/test_storage_contract.py`, which imports the implementations rather than the base), `interfaces/api/schemas.py` (Pydantic models exercised through every API test), and `run.py` (the entrypoint script). |

### Findings this run's predecessors produced, already fixed

The audit was run several times while being built, and each run found real
defects that were fixed before this report. Recording them because they
are the actual evidence that the mechanism works:

1. **`adapters/workspace/` was invisible to iV.** `workspace` sat in the
   self-inspection exclusion list to hide the sandboxed clone directory,
   and matched *any* path component of that name — hiding the repo-editing
   adapter, one of the most security-relevant modules in the codebase,
   from iV's view of itself. Fixed with a root-only exclusion set;
   regression test in `tests/adapters/selfinspect/test_repo.py`.
2. **The audit's highest-severity finding was a docstring.** `SEC-EXEC-01`
   reported `git_ops.py` for `shell=True` — a sentence promising it never
   does that. The check now parses the AST, so comments and prose are
   invisible to it by construction.
3. **Four well-tested modules reported as untested.** The coverage check
   matched dotted paths as substrings and never saw `from
   adapters.workspace import files`. It now resolves imports with the AST.
4. **`--json` output was unparseable.** Log lines shared stdout with the
   JSON. Logs moved to stderr for the CLI.
5. **`./run.sh` orphaned the frontend on Ctrl+C.** Killing the launching
   subshell left `next dev` alive holding port 3000. Fixed with a
   process-group signal; verified by watching both processes exit.

Items 1–3 are corrections to the audit itself, which is the expected way
this improves: the checks get sharper by being run against a real
codebase and having their false positives and false negatives fixed.
