"""Read-only introspection of iV's own installation directory.

This is a different problem from adapters/workspace, and that's why it's
a different module rather than "just clone yourself into the workspace."
The workspace sandbox exists so iV can *edit* arbitrary repos it was
pointed at; this exists so iV can *read* the one repo it is running from.
Reusing the editing sandbox for that would mean the tools that can write
are also the tools aimed at iV's own source — the exact combination the
first self-inspection experiment is supposed to avoid.

So the guarantees here are structural, not conventional:

  * There is no write, delete, move, or execute function in this module.
    Not "one that requires approval" — none at all. A model cannot reach
    a capability that has no implementation.
  * Every path resolves through _resolve(), which confines results to the
    install root and rejects absolute paths, '..' traversal, and symlinks
    that point outside it.
  * Secret-bearing and irrelevant paths are refused by name (.env files,
    the SQLite database, .git internals, virtualenvs, node_modules). The
    audit's job is to critique source code, and none of those are source
    code — .env in particular would hand a model the very API keys the
    rest of the system works to keep out of its context.
  * Git access is limited to a fixed allowlist of read-only subcommands
    (status, diff, log, branch, ...) run with argv lists, never a shell.

Everything returned is a plain dict/str, so a tool wrapper can hand it
straight to a model.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

MAX_READ_BYTES = 200_000
MAX_SEARCH_MATCHES = 200
MAX_LISTING_ENTRIES = 500
GIT_TIMEOUT_SECONDS = 30

# Directory names never descended into or read from. Not a security
# boundary on their own (the path confinement is) — they're the
# difference between an inventory of iV's source and 40,000 files of
# vendored dependencies.
EXCLUDED_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", "venv", ".venv", ".next",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", "build",
    ".egg-info",
})

# Excluded only as a TOP-LEVEL directory, matched against the first path
# component rather than any of them. iV's first self-audit caught the bug
# this fixes: 'workspace' was in EXCLUDED_DIRS, which correctly hid the
# sandboxed clone root at ./workspace/ but also hid adapters/workspace/ —
# the repo-editing adapter, one of the most security-relevant modules in
# the codebase — from iV's view of itself. Same shape of mistake for
# .launchd, whose logs read_logs() reaches deliberately.
# 'reports' is here because the self-audit writes its own report there:
# without this, a later run reads the previous run's evidence excerpts as
# if they were source, and re-reports its own findings back to itself.
ROOT_ONLY_EXCLUDED_DIRS = frozenset({"workspace", ".launchd", "reports"})

# Glob patterns whose *contents* are never returned, even though the file
# may be listed. Everything here either holds credentials or holds user
# data that a code review has no reason to read.
SECRET_PATH_PATTERNS = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx",
    "id_rsa*", "id_ed25519*", "*.sqlite", "*.sqlite3", "*.db",
    "credentials.json", "service-account*.json",
)

# Read-only git subcommands. `git` has plenty of read verbs that can
# still write (gc, fetch, checkout); an allowlist of the handful actually
# useful for a review is safer than trying to enumerate the dangerous ones.
ALLOWED_GIT_COMMANDS: dict[str, list[str]] = {
    "status": ["status", "--short", "--branch"],
    "diff": ["diff"],
    "diff_staged": ["diff", "--cached"],
    "log": ["log", "--oneline", "-20"],
    "branch": ["rev-parse", "--abbrev-ref", "HEAD"],
    "remotes": ["remote", "-v"],
    "tracked_files": ["ls-files"],
}

TEXT_SUFFIXES = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".md", ".txt", ".yml",
    ".yaml", ".toml", ".ini", ".cfg", ".sh", ".css", ".html", ".sql",
    ".plist", ".template", ".example",
})


class SelfInspectionDenied(PermissionError):
    """Raised when a requested path escapes the install root or names a
    file this module refuses to read (a secret store, the database)."""


@dataclass(frozen=True)
class FileEntry:
    path: str
    is_dir: bool
    size_bytes: int


def _is_secret_path(relative_path: str) -> bool:
    name = Path(relative_path).name
    return any(fnmatch.fnmatch(name, pattern) for pattern in SECRET_PATH_PATTERNS)


def _has_excluded_component(relative_path: str) -> bool:
    parts = Path(relative_path).parts
    if parts and parts[0] in ROOT_ONLY_EXCLUDED_DIRS:
        return True
    return any(part in EXCLUDED_DIRS or part.endswith(".egg-info") for part in parts)


class SelfInspector:
    """Bound to one root directory at construction. The root is supplied
    by the entrypoint (which knows where iV is installed) rather than
    discovered here, so a test can point one at a fixture tree and get
    identical behavior to the real thing."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).expanduser().resolve()

    @property
    def root(self) -> Path:
        return self._root

    # ---- path safety -------------------------------------------------

    def _resolve(self, relative_path: str, *, for_read: bool) -> Path:
        candidate = Path(relative_path or ".")
        if candidate.is_absolute():
            raise SelfInspectionDenied(f"path must be relative to the install root: {relative_path!r}")

        resolved = (self._root / candidate).resolve()
        try:
            inside = resolved.relative_to(self._root)
        except ValueError:
            raise SelfInspectionDenied(f"path escapes the install root: {relative_path!r}") from None

        as_posix = inside.as_posix()
        if as_posix != "." and _has_excluded_component(as_posix):
            raise SelfInspectionDenied(f"path is inside an excluded directory: {as_posix}")
        if for_read and _is_secret_path(as_posix):
            raise SelfInspectionDenied(
                f"refusing to read '{as_posix}': it may contain credentials or user data"
            )
        return resolved

    def _relative(self, path: Path) -> str:
        return path.relative_to(self._root).as_posix()

    # ---- reads -------------------------------------------------------

    def list_files(self, relative_dir: str = ".", *, recursive: bool = False) -> list[dict]:
        directory = self._resolve(relative_dir, for_read=False)
        if not directory.is_dir():
            raise NotADirectoryError(f"'{relative_dir}' is not a directory in the iV install")

        entries: list[FileEntry] = []
        walker = directory.rglob("*") if recursive else directory.iterdir()
        for child in sorted(walker):
            rel = self._relative(child)
            if _has_excluded_component(rel):
                continue
            entries.append(
                FileEntry(path=rel, is_dir=child.is_dir(), size_bytes=child.stat().st_size if child.is_file() else 0)
            )
            if len(entries) >= MAX_LISTING_ENTRIES:
                break
        return [{"path": e.path, "is_dir": e.is_dir, "size_bytes": e.size_bytes} for e in entries]

    def read_file(self, relative_path: str, *, max_bytes: int = MAX_READ_BYTES) -> dict:
        path = self._resolve(relative_path, for_read=True)
        if not path.is_file():
            raise FileNotFoundError(f"no file at '{relative_path}' in the iV install")
        raw = path.read_bytes()
        truncated = len(raw) > max_bytes
        text = raw[:max_bytes].decode("utf-8", errors="replace")
        return {
            "path": self._relative(path),
            "content": text,
            "truncated": truncated,
            "size_bytes": len(raw),
            "line_count": text.count("\n") + 1,
        }

    def search(self, pattern: str, *, relative_dir: str = ".", glob: str = "*", max_matches: int = MAX_SEARCH_MATCHES) -> list[dict]:
        """Regex search across text files, returning file/line/text for
        each hit. Implemented in Python rather than shelling out to grep
        or ripgrep — this module deliberately has no path that runs a
        command chosen by anything other than its own allowlist."""
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid search pattern: {exc}") from exc

        directory = self._resolve(relative_dir, for_read=False)
        matches: list[dict] = []
        for path in sorted(directory.rglob(glob)):
            if not path.is_file():
                continue
            rel = self._relative(path)
            if _has_excluded_component(rel) or _is_secret_path(rel):
                continue
            if path.suffix and path.suffix not in TEXT_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    matches.append({"path": rel, "line": lineno, "text": line.strip()[:400]})
                    if len(matches) >= max_matches:
                        return matches
        return matches

    def source_files(self, *, suffixes: tuple[str, ...] = (".py",)) -> list[str]:
        """Every non-excluded source file of the given types, relative to
        the root. The inventory the deterministic audit checks iterate."""
        found = []
        for path in sorted(self._root.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            rel = self._relative(path)
            if _has_excluded_component(rel) or _is_secret_path(rel):
                continue
            found.append(rel)
        return found

    # ---- configuration / manifests ------------------------------------

    def inspect_dependencies(self) -> dict:
        """Dependency manifests, read as text. Names and version pins are
        exactly what a review needs and contain nothing sensitive."""
        manifests = {}
        for candidate in (
            "backend/requirements.txt", "backend/requirements-dev.txt",
            "frontend/package.json", "pyproject.toml", "pytest.ini",
        ):
            path = self._root / candidate
            if path.is_file():
                manifests[candidate] = path.read_text(encoding="utf-8", errors="replace")[:MAX_READ_BYTES]
        return manifests

    def inspect_configuration(self) -> dict:
        """Which known configuration variables are SET — never their
        values. Same rule core/environment/discovery.py follows: a
        manifest a human can safely read is one that reports presence,
        not content."""
        from core.observability.redaction import SECRET_ENV_VARS

        non_secret = [
            "IV_LOCAL_DB_PATH", "IV_WORKSPACE_ROOT", "IV_STORAGE_BACKEND", "IV_LOG_LEVEL",
            "IV_LOG_FORMAT", "IV_HOST", "IV_PORT", "IV_PROTECTED_BRANCHES",
            "CORS_ALLOWED_ORIGINS", "CORS_ALLOWED_ORIGIN_REGEX", "AGENT_IV_BASE_URL",
        ]
        return {
            "secrets_configured": {name: bool(os.getenv(name)) for name in SECRET_ENV_VARS},
            "settings": {name: os.getenv(name) for name in non_secret if os.getenv(name)},
            "install_root": str(self._root),
        }

    def list_tests(self) -> list[str]:
        return [p for p in self.source_files() if p.startswith("tests/") and Path(p).name.startswith("test_")]

    def read_logs(self, *, lines: int = 200, log_name: str = "backend.log") -> dict:
        """Tail of a LaunchAgent log, if one exists. The log directory is
        the one place under an excluded directory (.launchd) this module
        will read, and only for files matching *.log — a runtime that
        can't see its own crash output can't audit its own reliability.
        Contents still pass through redaction before they leave here."""
        from core.observability.redaction import redact

        if "/" in log_name or "\\" in log_name or not log_name.endswith(".log"):
            raise SelfInspectionDenied(f"invalid log name: {log_name!r}")
        path = (self._root / ".launchd" / log_name).resolve()
        try:
            path.relative_to(self._root / ".launchd")
        except ValueError:
            raise SelfInspectionDenied(f"log path escapes the log directory: {log_name!r}") from None
        if not path.is_file():
            return {"log": log_name, "exists": False, "lines": []}
        tail = path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        return {"log": log_name, "exists": True, "lines": [redact(line) for line in tail]}

    # ---- git ----------------------------------------------------------

    def git(self, command: str) -> dict:
        argv = ALLOWED_GIT_COMMANDS.get(command)
        if argv is None:
            raise SelfInspectionDenied(
                f"git command '{command}' is not in the read-only allowlist: "
                f"{', '.join(sorted(ALLOWED_GIT_COMMANDS))}"
            )
        try:
            proc = subprocess.run(
                ["git", *argv], cwd=self._root, capture_output=True, text=True,
                timeout=GIT_TIMEOUT_SECONDS, shell=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {"command": command, "success": False, "stdout": "", "stderr": str(exc)}
        return {
            "command": command,
            "success": proc.returncode == 0,
            "stdout": proc.stdout[:MAX_READ_BYTES],
            "stderr": proc.stderr[:20_000],
        }
