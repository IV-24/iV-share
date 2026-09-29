"""Protected-branch policy: iV never commits to or pushes a branch a
human has declared off-limits.

Approval gating alone does not cover this. `git_commit` and
`write_repository_file` are AUTO by design (they only touch a sandboxed
clone, and reverting them is cheap), which means nothing in the approval
path would notice that the clone happened to be sitting on `main`. The
cost of that is not the local commit — it's that the local commit is the
thing a later, approved `git_push` pushes. Blocking the write at the
point it lands on a protected branch is the cheap place to stop it.

The list is configuration, not a constant: IV_PROTECTED_BRANCHES
(comma-separated) overrides the default. It is read fresh on each call so
an operator can widen it without restarting the server, and there is
deliberately no tool that lets iV change it — a runtime that can edit its
own guardrails does not have guardrails.
"""

from __future__ import annotations

import os

DEFAULT_PROTECTED_BRANCHES = ("main", "master", "production", "release")


class ProtectedBranchViolation(PermissionError):
    """Raised when a write/commit/push would land on a protected branch."""


def protected_branches(env: dict[str, str] | None = None) -> frozenset[str]:
    source = env if env is not None else os.environ
    raw = source.get("IV_PROTECTED_BRANCHES")
    if raw is None:
        return frozenset(DEFAULT_PROTECTED_BRANCHES)
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


def is_protected_branch(branch: str | None, env: dict[str, str] | None = None) -> bool:
    """An unknown branch (None/empty — detached HEAD, or a repo whose
    branch couldn't be read) is treated as NOT protected: the caller that
    couldn't determine a branch is in no position to claim the write is
    safe *or* unsafe, and failing closed here would break every
    legitimate operation on a fresh clone whose HEAD read failed for an
    unrelated reason. Callers that need certainty resolve the branch
    first and pass a real value."""
    if not branch:
        return False
    return branch.strip() in protected_branches(env)


def require_unprotected_branch(branch: str | None, *, operation: str, env: dict[str, str] | None = None) -> None:
    if is_protected_branch(branch, env):
        raise ProtectedBranchViolation(
            f"refusing to {operation}: '{branch}' is a protected branch. "
            f"Create a working branch first (create_branch), then retry. "
            f"Protected branches are set by IV_PROTECTED_BRANCHES and are not "
            f"something iV can change."
        )
