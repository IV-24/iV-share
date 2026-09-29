"""Wires adapters/workspace's git/file/shell primitives into the
ToolRegistry as risk-tiered ToolDefinitions. This is where "what a
repo-editing tool can do" and "how dangerous is that, does it need a
human's sign-off first" get decided -- same role core/tools/standard.py
plays for the database tools.

Risk tiers, and why:
  - Read-only (list/read/status/diff): LOW, AUTO. No side effect exists to
    approve.
  - Local, reversible writes (clone, write file, commit, create branch):
    MEDIUM, AUTO. They only touch the sandboxed workspace on disk --
    nothing external or hard-to-undo yet ('git reset'/deleting the clone
    undoes them). IV_CONSTITUTION.md's "may generate code" allowance is
    what licenses this without a human in the loop for every edit.
  - run_repository_command: CRITICAL, REQUIRES_APPROVAL. Deliberately no
    allowlist of "safe" commands -- the simplest correct default for
    "run anything" is "a human signs off every time," not a blocklist
    that's one bypass away from being wrong.
  - git_push: HIGH, REQUIRES_APPROVAL. The first point any of this leaves
    the sandbox and becomes visible/consequential outside it (a real
    remote, someone else's repo).
  - create_pull_request: HIGH, REQUIRES_APPROVAL. Talks to the real
    GitHub API with a real token -- same externally-visible tier as
    git_push, requires human approval every time.
"""

from pathlib import Path

from adapters.workspace import files, git_ops, github, shell
from core.permissions.scopes import PermissionScope
from core.tools.base import ExecutionPolicy, RiskLevel, ToolDefinition
from core.tools.registry import ToolRegistry


def _result_to_dict(result: git_ops.CommandResult) -> dict:
    return {
        "success": result.success,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "return_code": result.return_code,
    }


def register_workspace_tools(
    registry: ToolRegistry, *, workspace_root: Path, github_token: str | None = None
) -> None:
    def clone_repository(url: str, repo_name: str):
        return _result_to_dict(git_ops.clone_repository(workspace_root, url, repo_name))

    def read_repository_file(repo_name: str, path: str):
        return {"content": files.read_file(workspace_root, repo_name, path)}

    def list_repository_files(repo_name: str, path: str = "."):
        return {"entries": files.list_files(workspace_root, repo_name, path)}

    def write_repository_file(repo_name: str, path: str, content: str):
        files.write_file(workspace_root, repo_name, path, content)
        return {"written": path}

    def git_status(repo_name: str):
        return _result_to_dict(git_ops.git_status(workspace_root, repo_name))

    def git_diff(repo_name: str, staged: bool = False):
        return _result_to_dict(git_ops.git_diff(workspace_root, repo_name, staged=staged))

    def git_commit(repo_name: str, message: str):
        return _result_to_dict(git_ops.git_commit(workspace_root, repo_name, message))

    def create_branch(repo_name: str, branch_name: str):
        return _result_to_dict(git_ops.create_branch(workspace_root, repo_name, branch_name))

    def run_repository_command(repo_name: str, command: str):
        return _result_to_dict(shell.run_command(workspace_root, repo_name, command))

    def git_push(repo_name: str, remote: str = "origin", branch: str | None = None):
        return _result_to_dict(git_ops.git_push(workspace_root, repo_name, remote=remote, branch=branch))

    def create_pull_request(owner: str, repo: str, title: str, head: str, base: str, body: str = ""):
        return github.create_pull_request(
            token=github_token, owner=owner, repo=repo, title=title, head=head, base=base, body=body,
        )

    registry.register(ToolDefinition(
        name="clone_repository",
        description="Clones a git repository into the sandboxed workspace under the given repo_name.",
        input_schema={"type": "object", "properties": {
            "url": {"type": "string"}, "repo_name": {"type": "string"},
        }, "required": ["url", "repo_name"]},
        output_schema={"type": "object"}, handler=clone_repository,
        required_permissions=[PermissionScope.NETWORK_REQUEST, PermissionScope.FILESYSTEM_WRITE],
        risk_level=RiskLevel.MEDIUM, execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="read_repository_file",
        description="Reads a text file from a cloned repository in the workspace.",
        input_schema={"type": "object", "properties": {
            "repo_name": {"type": "string"}, "path": {"type": "string"},
        }, "required": ["repo_name", "path"]},
        output_schema={"type": "object"}, handler=read_repository_file,
        required_permissions=[PermissionScope.FILESYSTEM_READ],
        risk_level=RiskLevel.LOW, execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="list_repository_files",
        description="Lists files and directories at a path inside a cloned repository.",
        input_schema={"type": "object", "properties": {
            "repo_name": {"type": "string"}, "path": {"type": "string"},
        }, "required": ["repo_name"]},
        output_schema={"type": "object"}, handler=list_repository_files,
        required_permissions=[PermissionScope.FILESYSTEM_READ],
        risk_level=RiskLevel.LOW, execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="write_repository_file",
        description="Writes (creates or overwrites) a text file inside a cloned repository.",
        input_schema={"type": "object", "properties": {
            "repo_name": {"type": "string"}, "path": {"type": "string"}, "content": {"type": "string"},
        }, "required": ["repo_name", "path", "content"]},
        output_schema={"type": "object"}, handler=write_repository_file,
        required_permissions=[PermissionScope.FILESYSTEM_WRITE],
        risk_level=RiskLevel.MEDIUM, execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="git_status",
        description="Runs 'git status' (short, with branch info) in a cloned repository.",
        input_schema={"type": "object", "properties": {"repo_name": {"type": "string"}}, "required": ["repo_name"]},
        output_schema={"type": "object"}, handler=git_status,
        required_permissions=[PermissionScope.FILESYSTEM_READ],
        risk_level=RiskLevel.LOW, execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="git_diff",
        description="Shows the working-tree (or staged, if staged=true) diff in a cloned repository.",
        input_schema={"type": "object", "properties": {
            "repo_name": {"type": "string"}, "staged": {"type": "boolean"},
        }, "required": ["repo_name"]},
        output_schema={"type": "object"}, handler=git_diff,
        required_permissions=[PermissionScope.FILESYSTEM_READ],
        risk_level=RiskLevel.LOW, execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="git_commit",
        description="Stages all changes and commits them in a cloned repository. Local only -- does not push.",
        input_schema={"type": "object", "properties": {
            "repo_name": {"type": "string"}, "message": {"type": "string"},
        }, "required": ["repo_name", "message"]},
        output_schema={"type": "object"}, handler=git_commit,
        required_permissions=[PermissionScope.FILESYSTEM_WRITE],
        risk_level=RiskLevel.MEDIUM, execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="create_branch",
        description="Creates and checks out a new local branch in a cloned repository.",
        input_schema={"type": "object", "properties": {
            "repo_name": {"type": "string"}, "branch_name": {"type": "string"},
        }, "required": ["repo_name", "branch_name"]},
        output_schema={"type": "object"}, handler=create_branch,
        required_permissions=[PermissionScope.FILESYSTEM_WRITE],
        risk_level=RiskLevel.MEDIUM, execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="run_repository_command",
        description="Runs a shell command inside a cloned repository's directory (e.g. a test suite "
                     "or linter). High-risk escape hatch with no allowlist of pre-approved commands -- "
                     "requires human approval every time.",
        input_schema={"type": "object", "properties": {
            "repo_name": {"type": "string"}, "command": {"type": "string"},
        }, "required": ["repo_name", "command"]},
        output_schema={"type": "object"}, handler=run_repository_command,
        required_permissions=[PermissionScope.CODE_EXECUTE],
        risk_level=RiskLevel.CRITICAL, execution_policy=ExecutionPolicy.REQUIRES_APPROVAL,
    ))
    registry.register(ToolDefinition(
        name="git_push",
        description="Pushes the current branch to a remote. Leaves the sandbox and becomes externally "
                     "visible (a real remote, someone else's repo) -- requires human approval every time.",
        input_schema={"type": "object", "properties": {
            "repo_name": {"type": "string"}, "remote": {"type": "string"}, "branch": {"type": "string"},
        }, "required": ["repo_name"]},
        output_schema={"type": "object"}, handler=git_push,
        required_permissions=[PermissionScope.GITHUB_WRITE, PermissionScope.NETWORK_REQUEST],
        risk_level=RiskLevel.HIGH, execution_policy=ExecutionPolicy.REQUIRES_APPROVAL,
    ))
    registry.register(ToolDefinition(
        name="create_pull_request",
        description="Opens a pull request on GitHub from an already-pushed branch. Requires a "
                     "configured GitHub token -- leaves the sandbox and is externally visible, "
                     "so it requires human approval every time.",
        input_schema={"type": "object", "properties": {
            "owner": {"type": "string"}, "repo": {"type": "string"}, "title": {"type": "string"},
            "head": {"type": "string"}, "base": {"type": "string"}, "body": {"type": "string"},
        }, "required": ["owner", "repo", "title", "head", "base"]},
        output_schema={"type": "object"}, handler=create_pull_request,
        required_permissions=[PermissionScope.GITHUB_WRITE, PermissionScope.NETWORK_REQUEST],
        risk_level=RiskLevel.HIGH, execution_policy=ExecutionPolicy.REQUIRES_APPROVAL,
    ))
