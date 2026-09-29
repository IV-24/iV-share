"""Git operations via subprocess, confined to the workspace sandbox.
Every call uses an argv list (never shell=True), cwd is always a path
already validated by adapters/workspace/sandbox.py, and every positional
argument that could plausibly start with '-' is checked by
reject_flag_like() first -- there is no code path here that lets a caller
reach an arbitrary shell or smuggle an extra git flag through a value
that's supposed to be a plain URL/branch/remote name.

A non-zero git exit code (e.g. "nothing to commit", "not a valid
repository") is reported back as CommandResult(success=False, ...), not
raised as a Python exception -- that's business-logic data for the caller
(and ultimately the model) to see, not a crash. A commit's author identity
is set via env rather than relying on global git config existing on the
host, matching the rest of iV Core's "no assumed host state" posture.

One thing here is not business-logic data and does raise: a commit or
push that would land on a protected branch (core/safety/branches.py).
That's a policy refusal, not a git outcome — there is no exit code for
it, and reporting it as an ordinary failed command would invite a caller
to retry the same operation expecting a different result.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from adapters.workspace.sandbox import reject_flag_like, resolve_repo_name, resolve_repo_path
from core.safety.branches import require_unprotected_branch

_TIMEOUT_SECONDS = 120


@dataclass
class CommandResult:
    success: bool
    stdout: str
    stderr: str
    return_code: int


def _git_env() -> dict:
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "iV")
    env.setdefault("GIT_AUTHOR_EMAIL", "iv@localhost")
    env.setdefault("GIT_COMMITTER_NAME", "iV")
    env.setdefault("GIT_COMMITTER_EMAIL", "iv@localhost")
    return env


def _run(argv: list[str], *, cwd: Path) -> CommandResult:
    try:
        proc = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, env=_git_env(),
        )
    except subprocess.TimeoutExpired:
        return CommandResult(success=False, stdout="", stderr=f"timed out after {_TIMEOUT_SECONDS}s", return_code=-1)
    return CommandResult(success=proc.returncode == 0, stdout=proc.stdout, stderr=proc.stderr, return_code=proc.returncode)


def clone_repository(workspace_root: Path, url: str, repo_name: str) -> CommandResult:
    reject_flag_like(url, "url")
    resolve_repo_name(repo_name)
    dest = resolve_repo_path(workspace_root, repo_name)
    if dest.exists():
        return CommandResult(success=False, stdout="", stderr=f"'{repo_name}' already exists in workspace", return_code=-1)
    return _run(["git", "clone", url, str(dest)], cwd=dest.parent)


def git_status(workspace_root: Path, repo_name: str) -> CommandResult:
    repo_path = resolve_repo_path(workspace_root, repo_name)
    return _run(["git", "status", "--short", "--branch"], cwd=repo_path)


def git_diff(workspace_root: Path, repo_name: str, staged: bool = False) -> CommandResult:
    repo_path = resolve_repo_path(workspace_root, repo_name)
    argv = ["git", "diff"] + (["--cached"] if staged else [])
    return _run(argv, cwd=repo_path)


def git_add_all(workspace_root: Path, repo_name: str) -> CommandResult:
    repo_path = resolve_repo_path(workspace_root, repo_name)
    return _run(["git", "add", "-A"], cwd=repo_path)


def git_commit(workspace_root: Path, repo_name: str, message: str) -> CommandResult:
    repo_path = resolve_repo_path(workspace_root, repo_name)
    # Protected-branch check happens before anything is staged, so a
    # refused commit leaves the index exactly as it was. See
    # core/safety/branches.py for why the local commit is the right place
    # to stop this rather than the later push.
    require_unprotected_branch(_current_branch_name(workspace_root, repo_name), operation="commit")
    add_result = git_add_all(workspace_root, repo_name)
    if not add_result.success:
        return add_result
    return _run(["git", "commit", "-m", message], cwd=repo_path)


def create_branch(workspace_root: Path, repo_name: str, branch_name: str) -> CommandResult:
    reject_flag_like(branch_name, "branch_name")
    repo_path = resolve_repo_path(workspace_root, repo_name)
    return _run(["git", "checkout", "-b", branch_name], cwd=repo_path)


def checkout_branch(workspace_root: Path, repo_name: str, branch_name: str) -> CommandResult:
    reject_flag_like(branch_name, "branch_name")
    repo_path = resolve_repo_path(workspace_root, repo_name)
    return _run(["git", "checkout", branch_name], cwd=repo_path)


def git_push(workspace_root: Path, repo_name: str, remote: str = "origin", branch: str | None = None) -> CommandResult:
    reject_flag_like(remote, "remote")
    if branch:
        reject_flag_like(branch, "branch")
    repo_path = resolve_repo_path(workspace_root, repo_name)
    require_unprotected_branch(branch or _current_branch_name(workspace_root, repo_name), operation="push")
    argv = ["git", "push", "-u", remote]
    if branch:
        argv.append(branch)
    return _run(argv, cwd=repo_path)


def current_branch(workspace_root: Path, repo_name: str) -> CommandResult:
    repo_path = resolve_repo_path(workspace_root, repo_name)
    return _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)


def _current_branch_name(workspace_root: Path, repo_name: str) -> str | None:
    """The branch name as a plain string, or None if it can't be read
    (not a repo yet, detached HEAD, git missing). See
    core.safety.branches.is_protected_branch for why None is not treated
    as protected."""
    result = current_branch(workspace_root, repo_name)
    if not result.success:
        return None
    name = result.stdout.strip()
    return name or None
