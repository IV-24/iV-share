"""Exercises adapters/workspace/tools.py through the real ToolRegistry --
same shape as tests/core/test_standard_tools.py -- proving the risk/
approval tiers documented in tools.py's module docstring are what's
actually enforced, not just described. Git operations run for real
against tmp_path repos; only the GitHub API call inside create_pull_request
is mocked (patched at the adapters.workspace.github module boundary)."""

import subprocess
from unittest.mock import patch

from adapters.workspace.tools import register_workspace_tools
from core.approvals.manager import ApprovalManager
from core.audit.log import AuditLog
from core.permissions.manager import PermissionManager
from core.permissions.scopes import PermissionScope
from core.storage.memory import InMemoryStorage
from core.tools.registry import ToolRegistry

_ALL_SCOPES = [
    PermissionScope.FILESYSTEM_READ, PermissionScope.FILESYSTEM_WRITE,
    PermissionScope.NETWORK_REQUEST, PermissionScope.CODE_EXECUTE, PermissionScope.GITHUB_WRITE,
]


def make_registry(tmp_path, *, github_token=None):
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    permissions = PermissionManager(storage, audit)
    approvals = ApprovalManager(storage, audit)
    registry = ToolRegistry(permissions, approvals, audit)
    register_workspace_tools(registry, workspace_root=tmp_path / "workspace", github_token=github_token)
    for scope in _ALL_SCOPES:
        permissions.grant("engineering", scope, granted_by="test")
    return registry, permissions, approvals


def _seed_repo(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(source)], check=True, capture_output=True)
    (source / "README.md").write_text("hello\n")
    env = {
        **__import__("os").environ,
        "GIT_AUTHOR_NAME": "seed", "GIT_AUTHOR_EMAIL": "seed@localhost",
        "GIT_COMMITTER_NAME": "seed", "GIT_COMMITTER_EMAIL": "seed@localhost",
    }
    subprocess.run(["git", "add", "-A"], cwd=source, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=source, check=True, capture_output=True, env=env)
    return source


def test_all_workspace_tools_registered_with_documented_risk_tiers(tmp_path):
    registry, _, _ = make_registry(tmp_path)
    by_name = {t["name"]: t for t in registry.list_tools()}

    assert by_name["clone_repository"]["risk_level"] == "medium"
    assert by_name["clone_repository"]["execution_policy"] == "auto"

    for low_read_only in ["read_repository_file", "list_repository_files", "git_status", "git_diff"]:
        assert by_name[low_read_only]["risk_level"] == "low"
        assert by_name[low_read_only]["execution_policy"] == "auto"

    for local_write in ["write_repository_file", "git_commit", "create_branch"]:
        assert by_name[local_write]["risk_level"] == "medium"
        assert by_name[local_write]["execution_policy"] == "auto"

    assert by_name["run_repository_command"]["risk_level"] == "critical"
    assert by_name["run_repository_command"]["execution_policy"] == "requires_approval"

    assert by_name["git_push"]["risk_level"] == "high"
    assert by_name["git_push"]["execution_policy"] == "requires_approval"

    assert by_name["create_pull_request"]["risk_level"] == "high"
    assert by_name["create_pull_request"]["execution_policy"] == "requires_approval"


def test_clone_read_write_commit_flow_runs_without_approval(tmp_path):
    registry, _, _ = make_registry(tmp_path)
    source = _seed_repo(tmp_path)

    cloned = registry.execute(
        "clone_repository", {"url": str(source), "repo_name": "cloned"}, principal="engineering"
    )
    assert cloned.success is True
    assert cloned.output["success"] is True

    listed = registry.execute("list_repository_files", {"repo_name": "cloned"}, principal="engineering")
    assert "README.md" in listed.output["entries"]

    written = registry.execute(
        "write_repository_file",
        {"repo_name": "cloned", "path": "NOTES.md", "content": "notes\n"},
        principal="engineering",
    )
    assert written.success is True

    read_back = registry.execute(
        "read_repository_file", {"repo_name": "cloned", "path": "NOTES.md"}, principal="engineering"
    )
    assert read_back.output["content"] == "notes\n"

    # A fresh clone sits on a protected branch, so the flow iV must
    # follow is branch-then-commit. The refusal is asserted separately in
    # test_git_commit_on_protected_branch_is_refused.
    branched = registry.execute(
        "create_branch", {"repo_name": "cloned", "branch_name": "iv-work"}, principal="engineering"
    )
    assert branched.success is True

    committed = registry.execute(
        "git_commit", {"repo_name": "cloned", "message": "add notes"}, principal="engineering"
    )
    assert committed.success is True
    assert committed.output["success"] is True


def test_git_commit_on_protected_branch_is_refused(tmp_path):
    """The protected-branch guard surfaces through the tool layer as an
    ordinary failed ToolResult, not an exception escaping the registry —
    the model gets a message it can act on ("create a branch first")."""
    registry, _, _ = make_registry(tmp_path)
    source = _seed_repo(tmp_path)
    registry.execute("clone_repository", {"url": str(source), "repo_name": "cloned"}, principal="engineering")
    registry.execute(
        "write_repository_file",
        {"repo_name": "cloned", "path": "NOTES.md", "content": "notes\n"},
        principal="engineering",
    )

    committed = registry.execute(
        "git_commit", {"repo_name": "cloned", "message": "straight onto main"}, principal="engineering"
    )

    assert committed.success is False
    assert "protected branch" in committed.error


def test_run_repository_command_requires_approval_before_executing(tmp_path):
    registry, _, approvals = make_registry(tmp_path)
    source = _seed_repo(tmp_path)
    registry.execute("clone_repository", {"url": str(source), "repo_name": "cloned"}, principal="engineering")

    first = registry.execute(
        "run_repository_command", {"repo_name": "cloned", "command": "ls"}, principal="engineering"
    )
    assert first.success is False
    assert first.error == "approval_required"
    approval_id = first.approval_id
    assert approvals.get(approval_id).status.value == "requested"

    approvals.decide(approval_id, approved=True, decided_by="owner")
    second = registry.execute(
        "run_repository_command", {"repo_name": "cloned", "command": "ls"},
        principal="engineering", approval_id=approval_id,
    )
    assert second.success is True
    assert "README.md" in second.output["stdout"]


def test_git_push_requires_approval_before_executing(tmp_path):
    registry, _, approvals = make_registry(tmp_path)
    bare_remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_remote)], check=True, capture_output=True)
    registry.execute("clone_repository", {"url": str(bare_remote), "repo_name": "cloned"}, principal="engineering")
    registry.execute(
        "write_repository_file", {"repo_name": "cloned", "path": "f.txt", "content": "x"}, principal="engineering"
    )
    registry.execute("create_branch", {"repo_name": "cloned", "branch_name": "iv-work"}, principal="engineering")
    registry.execute("git_commit", {"repo_name": "cloned", "message": "add f"}, principal="engineering")

    first = registry.execute(
        "git_push", {"repo_name": "cloned", "branch": "iv-work"}, principal="engineering"
    )
    assert first.success is False
    assert first.error == "approval_required"

    approvals.decide(first.approval_id, approved=True, decided_by="owner")
    second = registry.execute(
        "git_push", {"repo_name": "cloned", "branch": "iv-work"},
        principal="engineering", approval_id=first.approval_id,
    )
    assert second.success is True
    assert second.output["success"] is True


def test_create_pull_request_requires_approval_and_calls_github(tmp_path):
    registry, _, approvals = make_registry(tmp_path, github_token="ghp_fake")

    first = registry.execute(
        "create_pull_request",
        {"owner": "iv-24", "repo": "agent-iv", "title": "t", "head": "feature", "base": "main"},
        principal="engineering",
    )
    assert first.success is False
    assert first.error == "approval_required"

    approvals.decide(first.approval_id, approved=True, decided_by="owner")
    with patch("adapters.workspace.github.create_pull_request") as fake_create:
        fake_create.return_value = {"number": 1, "url": "https://github.com/iv-24/agent-iv/pull/1", "state": "open"}
        second = registry.execute(
            "create_pull_request",
            {"owner": "iv-24", "repo": "agent-iv", "title": "t", "head": "feature", "base": "main"},
            principal="engineering", approval_id=first.approval_id,
        )

    assert second.success is True
    assert second.output["number"] == 1
    fake_create.assert_called_once()


def test_missing_permission_is_denied_before_any_git_operation_runs(tmp_path):
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    permissions = PermissionManager(storage, audit)
    approvals = ApprovalManager(storage, audit)
    registry = ToolRegistry(permissions, approvals, audit)
    register_workspace_tools(registry, workspace_root=tmp_path / "workspace")

    result = registry.execute(
        "clone_repository", {"url": "https://example.com/x.git", "repo_name": "x"}, principal="engineering"
    )

    assert result.success is False
    assert "missing required permission" in result.error
