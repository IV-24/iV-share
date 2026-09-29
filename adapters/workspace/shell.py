"""Sandboxed arbitrary-command execution for a cloned repo. This is the
highest-risk workspace primitive -- unlike git_ops (a fixed, known set of
git subcommands) or files (path-confined reads/writes), run_command() lets
the model run anything on disk within the repo's working directory.
adapters/workspace/tools.py registers it CRITICAL risk + REQUIRES_APPROVAL
for exactly that reason; the sandboxing here only confines *where* it
runs (cwd is always inside the workspace, never a shell), not *what* it
runs.

command is parsed with shlex.split rather than passed to a shell -- no
pipes, redirects, or command substitution reach a real shell -- but that's
a floor, not a ceiling: the parsed argv can still run anything the git
process would be able to (rm -rf ., curl, etc). That risk is exactly why
this tool requires human approval every time rather than trying to
allowlist "safe" commands.
"""

import shlex
import subprocess
from pathlib import Path

from adapters.workspace.git_ops import CommandResult
from adapters.workspace.sandbox import resolve_repo_path

_TIMEOUT_SECONDS = 120
_MAX_OUTPUT_CHARS = 20_000


def run_command(workspace_root: Path, repo_name: str, command: str) -> CommandResult:
    repo_path = resolve_repo_path(workspace_root, repo_name)
    if not repo_path.is_dir():
        return CommandResult(
            success=False, stdout="", stderr=f"repo '{repo_name}' does not exist in workspace", return_code=-1
        )

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return CommandResult(success=False, stdout="", stderr=f"could not parse command: {exc}", return_code=-1)
    if not argv:
        return CommandResult(success=False, stdout="", stderr="empty command", return_code=-1)

    try:
        proc = subprocess.run(
            argv, cwd=repo_path, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, shell=False,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(success=False, stdout="", stderr=f"timed out after {_TIMEOUT_SECONDS}s", return_code=-1)
    except OSError as exc:
        return CommandResult(success=False, stdout="", stderr=str(exc), return_code=-1)

    return CommandResult(
        success=proc.returncode == 0,
        stdout=proc.stdout[:_MAX_OUTPUT_CHARS],
        stderr=proc.stderr[:_MAX_OUTPUT_CHARS],
        return_code=proc.returncode,
    )
