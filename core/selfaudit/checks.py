"""Deterministic audit checks: the evidence layer.

The audit has two layers and this is the one that cannot hallucinate. Each
check here reads iV's own source through a RepositoryReader and returns
findings whose `evidence` field is a literal excerpt of what it read —
file, line number, and the text of the line. Nothing in this module asks
a model anything, so every finding it produces is reproducible by running
it again, and a reviewer can verify any of them with one `grep`.

The model layer (core/selfaudit/engine.py) runs *on top of* this: it gets
the deterministic findings as context and adds the judgments a regex
can't make (architecture, coupling, whether a fragile-looking assumption
is actually load-bearing). Splitting it this way is what makes "do not
manufacture findings" enforceable rather than aspirational — if the model
is unavailable, the audit still produces real, evidence-backed output
instead of nothing.

Adding a check: write a function taking (reader) -> list[Finding] and add
it to ALL_CHECKS. Keep the evidence literal; a finding whose evidence
field paraphrases what was found is a finding nobody can check.
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any, Protocol

from core.selfaudit.findings import Classification, Confidence, Finding, Severity


class RepositoryReader(Protocol):
    """Structural interface core/selfaudit needs from a repository
    reader. adapters/selfinspect.SelfInspector satisfies it; core does
    not import that adapter (or any adapter), and a test can pass a
    stub."""

    def search(self, pattern: str, *, relative_dir: str = ".", glob: str = "*", max_matches: int = 200) -> list[dict]: ...
    def read_file(self, relative_path: str, *, max_bytes: int = 200_000) -> dict: ...
    def source_files(self, *, suffixes: tuple[str, ...] = (".py",)) -> list[str]: ...
    def list_tests(self) -> list[str]: ...
    def inspect_dependencies(self) -> dict: ...
    def git(self, command: str) -> dict: ...


def _evidence(matches: list[dict], limit: int = 6) -> str:
    lines = [f"{m['path']}:{m['line']}: {m['text']}" for m in matches[:limit]]
    if len(matches) > limit:
        lines.append(f"... and {len(matches) - limit} more")
    return "\n".join(lines) or "(no matches)"


def _locations(matches: list[dict], limit: int = 4) -> str:
    seen: list[str] = []
    for match in matches:
        if match["path"] not in seen:
            seen.append(match["path"])
        if len(seen) >= limit:
            break
    return ", ".join(seen)


def _safe(reader_call, default):
    """A check that blows up must not take the whole audit with it — a
    missing file or an unreadable directory is a normal condition when
    auditing an install that may not be fully set up."""
    try:
        return reader_call()
    except Exception:  # noqa: BLE001 - deliberate: see docstring
        return default


# --------------------------------------------------------------------------
# Security
# --------------------------------------------------------------------------

# Credential shapes that are unambiguous enough to assert on. Deliberately
# narrow: a check that flags every string containing "key" trains its
# reader to ignore it.
_CREDENTIAL_PATTERNS = [
    (r"sk-[A-Za-z0-9]{20,}", "OpenAI-style secret key"),
    (r"sk-ant-[A-Za-z0-9\-_]{20,}", "Anthropic API key"),
    (r"AIza[0-9A-Za-z\-_]{30,}", "Google API key"),
    (r"gsk_[A-Za-z0-9]{20,}", "Groq API key"),
    (r"ghp_[A-Za-z0-9]{30,}", "GitHub personal access token"),
    (r"github_pat_[A-Za-z0-9_]{30,}", "GitHub fine-grained token"),
    (r"xox[baprs]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key material"),
]


def check_committed_secrets(reader: RepositoryReader) -> list[Finding]:
    findings = []
    for index, (pattern, label) in enumerate(_CREDENTIAL_PATTERNS, start=1):
        matches = _safe(lambda p=pattern: reader.search(p), [])
        # This module contains the patterns themselves; excluding it keeps
        # the check from reporting its own source as a leak.
        matches = [m for m in matches if not m["path"].endswith("core/selfaudit/checks.py")]
        if matches:
            findings.append(Finding(
                id=f"SEC-{index:02d}",
                severity=Severity.CRITICAL,
                category="security/secrets",
                location=_locations(matches),
                problem=f"{label} appears in tracked source",
                evidence=_evidence(matches),
                why_it_matters="A credential in git history is compromised the moment the repository is "
                                "shared, forked, or made public, and rewriting history does not un-share it. "
                                "It must be rotated, not just deleted.",
                recommended_fix="Rotate the credential at the provider, remove it from the working tree, "
                                 "load it from the environment instead, and purge it from git history.",
                confidence=Confidence.HIGH,
                classification=Classification.CONFIRMED_BUG,
            ))
    return findings


def check_secret_in_url(reader: RepositoryReader) -> list[Finding]:
    matches = _safe(lambda: reader.search(r"secret=\{|\?secret=|secret=\$\{"), [])
    # A quote character on the line is what separates a URL being
    # *constructed* from prose or documentation *describing* one. Without
    # this, the check's own explanatory docstring is its top hit.
    matches = [
        m for m in matches
        if not m["path"].startswith(("tests/", "core/selfaudit/", "docs/"))
        and ('"' in m["text"] or "'" in m["text"])
    ]
    if not matches:
        return []
    return [Finding(
        id="SEC-QS-01",
        severity=Severity.MEDIUM,
        category="security/secret-handling",
        location=_locations(matches),
        problem="A shared secret is passed in a URL query string",
        evidence=_evidence(matches),
        why_it_matters="Query strings land in server access logs, browser history, and the Referer "
                        "header of any outbound link on the page — all places a secret in a header or "
                        "POST body would not reach.",
        recommended_fix="Move the secret to a header or a cookie set on first use; if a clickable link "
                         "must carry authorization, use a single-use, expiring token instead of the "
                         "long-lived shared secret.",
        confidence=Confidence.HIGH,
        classification=Classification.POTENTIAL_RISK,
    )]


def check_cors_wildcard(reader: RepositoryReader) -> list[Finding]:
    matches = _safe(lambda: reader.search(r"allow_origins\s*=\s*\[\s*[\"']\*[\"']|Access-Control-Allow-Origin.{0,12}\*"), [])
    matches = [m for m in matches if not m["path"].startswith("core/selfaudit/")]
    if not matches:
        return []
    return [Finding(
        id="SEC-CORS-01",
        severity=Severity.HIGH,
        category="security/cors",
        location=_locations(matches),
        problem="CORS is configured to allow any origin",
        evidence=_evidence(matches),
        why_it_matters="With credentialed requests allowed, any page the browser visits can call this "
                        "API on the user's behalf. On a machine reachable over a VPN, that turns every "
                        "website the owner opens into a client of their agent runtime.",
        recommended_fix="Enumerate the real origins in CORS_ALLOWED_ORIGINS, or use a same-origin proxy "
                         "so cross-origin access is never needed.",
        confidence=Confidence.HIGH,
        classification=Classification.CONFIRMED_BUG,
    )]


def check_shell_execution(reader: RepositoryReader) -> list[Finding]:
    """Parses each module rather than grepping it.

    The first run of this audit reported adapters/workspace/git_ops.py for
    `shell=True` — a sentence in its docstring promising it never does
    that. A check whose top finding is the codebase's own documentation of
    the correct behavior is worse than no check, so detection moved to the
    AST: only a real call expression counts, and comments and docstrings
    are invisible to it by construction.
    """
    dangerous_calls = {
        "os.system": ("os.system() call", Severity.HIGH),
        "eval": ("eval() call", Severity.HIGH),
        "exec": ("exec() call", Severity.HIGH),
    }
    by_kind: dict[str, list[dict]] = {}

    for path in _safe(reader.source_files, []):
        if path.startswith(("tests/", "core/selfaudit/")):
            continue
        content = _safe(lambda p=path: reader.read_file(p)["content"], "")
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        lines = content.splitlines()

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            evidence = {"path": path, "line": node.lineno, "text": _line_at(lines, node.lineno)}

            for keyword in node.keywords:
                if (
                    keyword.arg == "shell"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                ):
                    by_kind.setdefault("subprocess called with shell=True", []).append(evidence)

            name = _callee_name(node.func)
            if name in dangerous_calls:
                by_kind.setdefault(dangerous_calls[name][0], []).append(evidence)

    findings = []
    for index, (label, matches) in enumerate(sorted(by_kind.items()), start=1):
        findings.append(Finding(
            id=f"SEC-EXEC-{index:02d}",
            severity=Severity.HIGH,
            category="security/command-execution",
            location=_locations(matches),
            problem=f"{label} in runtime code",
            evidence=_evidence(matches),
            why_it_matters="Any of these turns a string the model can influence into code or a shell "
                            "command. Argument-injection and command-substitution defenses elsewhere in "
                            "the codebase are bypassed by a single one of these call sites.",
            recommended_fix="Use an argv list with shell=False; parse structured data instead of "
                             "evaluating it.",
            confidence=Confidence.HIGH,
            classification=Classification.POTENTIAL_RISK,
        ))
    return findings


def _callee_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    return ""


def _line_at(lines: list[str], lineno: int) -> str:
    return lines[lineno - 1].strip()[:400] if 0 < lineno <= len(lines) else ""


def check_public_bind(reader: RepositoryReader) -> list[Finding]:
    matches = _safe(lambda: reader.search(r"0\.0\.0\.0"), [])
    matches = [m for m in matches if not m["path"].startswith(("tests/", "core/selfaudit/", "docs/"))]
    if not matches:
        return []
    return [Finding(
        id="SEC-BIND-01",
        severity=Severity.MEDIUM,
        category="security/network-exposure",
        location=_locations(matches),
        problem="The server binds 0.0.0.0 (every interface)",
        evidence=_evidence(matches),
        why_it_matters="Binding every interface exposes the runtime to any network the laptop joins — "
                        "coffee-shop wifi as readily as the private VPN it was meant for. It is the right "
                        "choice only if a shared secret or an external firewall is genuinely enforcing "
                        "access, and the wrong default if either is optional.",
        recommended_fix="Make the bind address configuration (IV_HOST), default it to the loopback or "
                         "the VPN interface, and require an explicit opt-in for the all-interfaces bind.",
        confidence=Confidence.HIGH,
        classification=Classification.POTENTIAL_RISK,
    )]


def check_hardcoded_private_addresses(reader: RepositoryReader) -> list[Finding]:
    """Tailscale hands out addresses in 100.64.0.0/10. One baked into
    source is both a portability bug and a small privacy leak about the
    owner's network."""
    matches = _safe(lambda: reader.search(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"), [])
    # tests/ is excluded because a fixture address is the whole point of a
    # fixture — flagging it is a false positive, and a check that cries
    # wolf gets ignored on the run where it is right.
    matches = [m for m in matches if not m["path"].startswith(("core/selfaudit/", "docs/", "tests/"))]
    if not matches:
        return []
    return [Finding(
        id="NET-01",
        severity=Severity.MEDIUM,
        category="configuration/portability",
        location=_locations(matches),
        problem="A specific Tailscale IP address is hardcoded in source",
        evidence=_evidence(matches),
        why_it_matters="Tailscale addresses change when a node is re-registered, and the value is "
                        "specific to one person's network — anyone else running this gets a client that "
                        "tries to reach a machine that isn't theirs. It also commits a detail of the "
                        "owner's private network to a git repository.",
        recommended_fix="Resolve the address at runtime (a relative URL, or discovery via the tailscale "
                         "CLI) and keep any override in untracked local configuration.",
        confidence=Confidence.HIGH,
        classification=Classification.CONFIRMED_BUG,
    )]


# --------------------------------------------------------------------------
# Reliability / configuration
# --------------------------------------------------------------------------

def check_debug_defaults(reader: RepositoryReader) -> list[Finding]:
    matches = _safe(lambda: reader.search(r"reload\s*=\s*True|debug\s*=\s*True|DEBUG\s*=\s*True", glob="*.py"), [])
    matches = [m for m in matches if not m["path"].startswith(("tests/", "core/selfaudit/"))]
    if not matches:
        return []
    return [Finding(
        id="REL-DEBUG-01",
        severity=Severity.MEDIUM,
        category="reliability/runtime-configuration",
        location=_locations(matches),
        problem="A development-only flag is enabled unconditionally",
        evidence=_evidence(matches),
        why_it_matters="uvicorn's reloader watches the filesystem and restarts the process on any file "
                        "change, which for a long-running service means dropped in-flight requests and a "
                        "second process holding the same SQLite file. debug=True additionally returns "
                        "stack traces to callers.",
        recommended_fix="Gate the flag behind an explicit development environment variable and default "
                         "it off.",
        confidence=Confidence.HIGH,
        classification=Classification.LIKELY_BUG,
    )]


def check_unpinned_dependencies(reader: RepositoryReader) -> list[Finding]:
    manifests = _safe(reader.inspect_dependencies, {})
    requirements = manifests.get("backend/requirements.txt", "")
    unpinned = [
        line.strip() for line in requirements.splitlines()
        if line.strip() and not line.strip().startswith(("#", "-r"))
        and not re.search(r"[=<>~!]=|@", line)
    ]
    if not unpinned:
        return []
    return [Finding(
        id="REL-DEPS-01",
        severity=Severity.MEDIUM,
        category="reliability/dependencies",
        location="backend/requirements.txt",
        problem=f"{len(unpinned)} runtime dependencies have no version constraint",
        evidence="backend/requirements.txt:\n" + "\n".join(f"  {name}" for name in unpinned),
        why_it_matters="run.sh reinstalls requirements on every start, so an upstream release can change "
                        "the running code between two starts of the same commit — a class of failure that "
                        "reproduces on the machine and nowhere else, and that git bisect cannot find.",
        recommended_fix="Pin versions (or generate a lockfile with pip-compile) and update deliberately.",
        confidence=Confidence.HIGH,
        classification=Classification.POTENTIAL_RISK,
    )]


def check_gitignore_coverage(reader: RepositoryReader) -> list[Finding]:
    """Data files that must never be committed. Checked two ways: is the
    pattern in .gitignore at all, and — the check that actually matters —
    is a matching file already tracked by git right now."""
    gitignore = _safe(lambda: reader.read_file(".gitignore")["content"], "")
    tracked_output = _safe(lambda: reader.git("tracked_files"), {"stdout": "", "success": False})
    tracked = set(tracked_output.get("stdout", "").splitlines())

    expectations = [
        ("iv.db", r"^/?iv\.db|\*\.db|^/?\*\.sqlite", "iV's SQLite database (conversations, memories, audit log)"),
        (".env", r"^\.env|/\.env", "provider API keys and shared secrets"),
        ("workspace/", r"^/?workspace/", "sandboxed repo clones"),
    ]
    missing = [
        (name, why) for name, pattern, why in expectations
        if not re.search(pattern, gitignore, re.MULTILINE)
    ]
    already_tracked = sorted(
        path for path in tracked
        if path == "iv.db" or path.endswith("/.env") or path == ".env" or path.endswith(".sqlite")
    )

    findings = []
    if already_tracked:
        findings.append(Finding(
            id="SEC-GIT-01",
            severity=Severity.CRITICAL,
            category="security/secrets",
            location=", ".join(already_tracked),
            problem="A secret or user-data file is tracked by git",
            evidence="git ls-files reported:\n" + "\n".join(f"  {p}" for p in already_tracked),
            why_it_matters="These files hold credentials or the owner's private conversation history. "
                            "Once committed they are in every clone of the repository.",
            recommended_fix="git rm --cached the file, add it to .gitignore, rotate anything it exposed, "
                             "and purge it from history before the repository is shared.",
            confidence=Confidence.HIGH,
            classification=Classification.CONFIRMED_BUG,
        ))
    if missing:
        findings.append(Finding(
            id="SEC-GIT-02",
            severity=Severity.HIGH,
            category="security/secrets",
            location=".gitignore",
            problem="Sensitive runtime artifacts are not covered by .gitignore",
            evidence="No .gitignore rule matches:\n" + "\n".join(f"  {name} — {why}" for name, why in missing),
            why_it_matters="Nothing stops the next `git add -A` from committing them. The file only has "
                            "to be added once for its contents to be permanent.",
            recommended_fix="Add explicit .gitignore entries for each.",
            confidence=Confidence.HIGH,
            classification=Classification.POTENTIAL_RISK,
        ))
    return findings


# --------------------------------------------------------------------------
# Testing / documentation
# --------------------------------------------------------------------------

_TEST_EXEMPT_PREFIXES = ("tests/", "scripts/", "docs/", "frontend/")

# Documents that intentionally reference files that have since been
# deleted, because recording what was removed is what they are for.
HISTORICAL_DOCS = frozenset({"docs/MIGRATION_AUDIT.md"})


def check_untested_modules(reader: RepositoryReader) -> list[Finding]:
    """A module is considered covered if any test file mentions its
    dotted import path. Coarse on purpose: it answers "is anything at all
    exercising this," which is the question worth asking before anyone
    argues about coverage percentages."""
    sources = [
        path for path in _safe(reader.source_files, [])
        if path.endswith(".py")
        and not path.startswith(_TEST_EXEMPT_PREFIXES)
        and not path.endswith("__init__.py")
    ]
    imported = _modules_imported_by(reader, _safe(reader.list_tests, []))

    untested = [path for path in sources if path[:-3].replace("/", ".") not in imported]

    if not untested:
        return []
    return [Finding(
        id="TEST-01",
        severity=Severity.MEDIUM if len(untested) > 2 else Severity.LOW,
        category="testing/coverage",
        location=", ".join(untested[:5]) + (" ..." if len(untested) > 5 else ""),
        problem=f"{len(untested)} runtime module(s) are not imported by any test",
        evidence="No test file references:\n" + "\n".join(f"  {p}" for p in untested[:12]),
        why_it_matters="These modules can break without any test failing. For a system that is meant to "
                        "restart itself unattended, an untested startup or persistence path is a failure "
                        "that surfaces as downtime rather than as a red build.",
        recommended_fix="Add at least one test per module that exercises its main entry point and one "
                         "failure mode.",
        confidence=Confidence.HIGH,
        classification=Classification.IMPROVEMENT,
    )]


def _modules_imported_by(reader: RepositoryReader, test_paths: list[str]) -> set[str]:
    """Every module the tests import, resolved with the AST.

    Substring matching on the dotted path was the obvious first
    implementation and it was wrong: `from adapters.workspace import
    files` never contains the string "adapters.workspace.files", so the
    first self-audit reported four thoroughly-tested workspace modules as
    untested. Both import forms resolve to the same name here."""
    imported: set[str] = set()
    for path in test_paths:
        content = _safe(lambda p=path: reader.read_file(p)["content"], "")
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                imported.add(node.module)
                # `from pkg import mod` — the imported name may itself be
                # a module rather than an attribute; record both readings.
                imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


def check_documentation_drift(reader: RepositoryReader) -> list[Finding]:
    """Documentation that points at files which no longer exist. Only
    paths that look like real repo paths are checked, and only inside
    docs/ and README.md."""
    existing = set(_safe(lambda: reader.source_files(suffixes=(
        ".py", ".js", ".jsx", ".md", ".json", ".sh", ".yml", ".yaml", ".txt", ".ini", ".template", ".css",
    )), []))
    referenced: dict[str, list[str]] = {}
    path_pattern = r"`([a-zA-Z0-9_./-]+\.(?:py|js|jsx|sh|json|md|yml|yaml|ini))`"
    for match in _safe(lambda: reader.search(path_pattern, relative_dir="docs", glob="*.md", max_matches=400), []):
        # A migration record's whole job is to name files that no longer
        # exist. Flagging it as stale documentation inverts its purpose.
        if match["path"] in HISTORICAL_DOCS:
            continue
        for candidate in re.findall(path_pattern, match["text"]):
            candidate = candidate[2:] if candidate.startswith("./") else candidate
            if "/" in candidate:
                referenced.setdefault(candidate, []).append(f"{match['path']}:{match['line']}")

    dangling = {path: where for path, where in referenced.items() if path not in existing}
    if not dangling:
        return []
    listed = sorted(dangling.items())[:10]
    return [Finding(
        id="DOC-01",
        severity=Severity.LOW,
        category="documentation/accuracy",
        location="docs/",
        problem=f"Documentation references {len(dangling)} path(s) that do not exist in the repository",
        evidence="\n".join(f"  {path} (cited at {where[0]})" for path, where in listed),
        why_it_matters="Documentation is the interface a human uses when something is broken at 2am. "
                        "A path that no longer exists costs the reader the time it takes to discover the "
                        "doc is stale, and quietly reduces trust in the parts that are still correct.",
        recommended_fix="Update or remove the stale references; consider a docs link check in CI.",
        confidence=Confidence.MEDIUM,
        classification=Classification.IMPROVEMENT,
    )]


def check_open_todos(reader: RepositoryReader) -> list[Finding]:
    matches = _safe(lambda: reader.search(r"\b(TODO|FIXME|XXX|HACK)\b", glob="*.py"), [])
    matches = [m for m in matches if not m["path"].startswith(("core/selfaudit/", "tests/"))]
    if not matches:
        return []
    return [Finding(
        id="INFO-TODO-01",
        severity=Severity.INFO,
        category="maintenance/tracking",
        location=_locations(matches),
        problem=f"{len(matches)} unresolved TODO/FIXME marker(s) in Python source",
        evidence=_evidence(matches, limit=10),
        why_it_matters="Markers in source are invisible to any planning process. Ones that matter belong "
                        "in the task backlog; ones that don't belong deleted.",
        recommended_fix="Triage each into a task or remove it.",
        confidence=Confidence.HIGH,
        classification=Classification.IMPROVEMENT,
    )]


ALL_CHECKS = [
    check_committed_secrets,
    check_gitignore_coverage,
    check_cors_wildcard,
    check_shell_execution,
    check_secret_in_url,
    check_public_bind,
    check_hardcoded_private_addresses,
    check_debug_defaults,
    check_unpinned_dependencies,
    check_untested_modules,
    check_documentation_drift,
    check_open_todos,
]


def run_static_checks(reader: RepositoryReader, checks=None) -> list[Finding]:
    """Runs every check, isolating failures: one check raising must not
    lose the findings of the eleven that worked."""
    findings: list[Finding] = []
    for check in checks or ALL_CHECKS:
        try:
            findings.extend(check(reader))
        except Exception as exc:  # noqa: BLE001 - see docstring
            findings.append(Finding(
                id=f"AUDIT-ERR-{check.__name__}",
                severity=Severity.INFO,
                category="audit/self",
                location=f"core/selfaudit/checks.py::{check.__name__}",
                problem="An audit check failed to run",
                evidence=f"{type(exc).__name__}: {exc}",
                why_it_matters="A check that cannot run is a blind spot the audit will not report on.",
                recommended_fix="Fix the check; re-run the audit.",
                confidence=Confidence.HIGH,
                classification=Classification.CONFIRMED_BUG,
            ))
    return findings


def findings_as_json(findings: list[Finding]) -> str:
    return json.dumps([f.to_dict() for f in findings], indent=2)


def repository_overview(reader: RepositoryReader) -> dict[str, Any]:
    """Compact inventory handed to the model layer as context, so its
    review starts from what actually exists rather than from a guess."""
    sources = _safe(reader.source_files, [])
    return {
        "python_modules": [p for p in sources if not p.startswith("tests/")],
        "test_files": _safe(reader.list_tests, []),
        "git_status": _safe(lambda: reader.git("status"), {}).get("stdout", ""),
        "git_branch": _safe(lambda: reader.git("branch"), {}).get("stdout", "").strip(),
    }
