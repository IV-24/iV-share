"""Exercises adapters/workspace/git_ops.py against real git subprocesses
(no mocking) -- git is available in this environment, and these
operations are simple enough to trust a real git binary over recreating
its behavior with a fake. Only adapters/workspace/github.py's actual
network call to the GitHub API gets mocked, in test_github.py."""

import os
import subprocess

import pytest

from adapters.workspace import git_ops
from adapters.workspace.sandbox import SandboxViolation
from core.safety.branches import ProtectedBranchViolation

_SEED_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "seed", "GIT_AUTHOR_EMAIL": "seed@localhost",
    "GIT_COMMITTER_NAME": "seed", "GIT_COMMITTER_EMAIL": "seed@localhost",
}


def _init_repo(path, *, initial_branch="main"):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", initial_branch, str(path)], check=True, capture_output=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=path, check=True, capture_output=True, env=_SEED_ENV)
    return path


def test_clone_repository_succeeds(tmp_path):
    source = _init_repo(tmp_path / "source")
    workspace_root = tmp_path / "workspace"

    result = git_ops.clone_repository(workspace_root, str(source), "cloned")

    assert result.success is True
    assert (workspace_root / "cloned" / "README.md").exists()


def test_clone_repository_rejects_url_shaped_like_a_flag(tmp_path):
    with pytest.raises(SandboxViolation):
        git_ops.clone_repository(tmp_path / "workspace", "--upload-pack=touch pwned", "cloned")


def test_clone_repository_refuses_to_overwrite_existing_dir(tmp_path):
    source = _init_repo(tmp_path / "source")
    workspace_root = tmp_path / "workspace"
    git_ops.clone_repository(workspace_root, str(source), "cloned")

    result = git_ops.clone_repository(workspace_root, str(source), "cloned")

    assert result.success is False
    assert "already exists" in result.stderr


def test_git_status_reports_clean_then_dirty(tmp_path):
    source = _init_repo(tmp_path / "source")
    workspace_root = tmp_path / "workspace"
    git_ops.clone_repository(workspace_root, str(source), "cloned")

    clean = git_ops.git_status(workspace_root, "cloned")
    assert clean.success is True
    assert clean.stdout.strip() != "" or clean.return_code == 0

    (workspace_root / "cloned" / "new_file.txt").write_text("data")
    dirty = git_ops.git_status(workspace_root, "cloned")
    assert "new_file.txt" in dirty.stdout


def test_git_commit_stages_and_commits(tmp_path):
    source = _init_repo(tmp_path / "source")
    workspace_root = tmp_path / "workspace"
    git_ops.clone_repository(workspace_root, str(source), "cloned")
    git_ops.create_branch(workspace_root, "cloned", "iv-work")
    (workspace_root / "cloned" / "new_file.txt").write_text("data")

    result = git_ops.git_commit(workspace_root, "cloned", "add new_file.txt")

    assert result.success is True
    status_after = git_ops.git_status(workspace_root, "cloned")
    assert status_after.stdout.strip() == "" or "new_file.txt" not in status_after.stdout


def test_git_commit_refuses_on_a_protected_branch(tmp_path):
    """The clone lands on 'main', which is protected by default. The
    refusal happens before anything is staged, so the working tree is
    left exactly as the caller had it."""
    source = _init_repo(tmp_path / "source")
    workspace_root = tmp_path / "workspace"
    git_ops.clone_repository(workspace_root, str(source), "cloned")
    (workspace_root / "cloned" / "new_file.txt").write_text("data")

    with pytest.raises(ProtectedBranchViolation, match="protected branch"):
        git_ops.git_commit(workspace_root, "cloned", "sneak onto main")

    staged = git_ops.git_diff(workspace_root, "cloned", staged=True)
    assert staged.stdout.strip() == ""
    still_untracked = git_ops.git_status(workspace_root, "cloned")
    assert "new_file.txt" in still_untracked.stdout


def test_protected_branches_are_configurable(tmp_path, monkeypatch):
    """An operator can widen or narrow the list; iV cannot. Narrowing it
    to something else makes 'main' committable again, which is what
    proves the guard is reading configuration rather than a constant."""
    monkeypatch.setenv("IV_PROTECTED_BRANCHES", "release")
    source = _init_repo(tmp_path / "source")
    workspace_root = tmp_path / "workspace"
    git_ops.clone_repository(workspace_root, str(source), "cloned")
    (workspace_root / "cloned" / "new_file.txt").write_text("data")

    assert git_ops.git_commit(workspace_root, "cloned", "allowed now").success is True


def test_git_commit_with_nothing_to_commit_fails_cleanly(tmp_path):
    source = _init_repo(tmp_path / "source")
    workspace_root = tmp_path / "workspace"
    git_ops.clone_repository(workspace_root, str(source), "cloned")
    git_ops.create_branch(workspace_root, "cloned", "iv-work")

    result = git_ops.git_commit(workspace_root, "cloned", "nothing changed")

    assert result.success is False


def test_git_diff_shows_uncommitted_changes(tmp_path):
    source = _init_repo(tmp_path / "source")
    workspace_root = tmp_path / "workspace"
    git_ops.clone_repository(workspace_root, str(source), "cloned")
    (workspace_root / "cloned" / "README.md").write_text("changed\n")

    result = git_ops.git_diff(workspace_root, "cloned")

    assert result.success is True
    assert "README.md" in result.stdout


def test_create_branch_and_current_branch(tmp_path):
    source = _init_repo(tmp_path / "source")
    workspace_root = tmp_path / "workspace"
    git_ops.clone_repository(workspace_root, str(source), "cloned")

    created = git_ops.create_branch(workspace_root, "cloned", "feature-x")
    assert created.success is True

    current = git_ops.current_branch(workspace_root, "cloned")
    assert current.stdout.strip() == "feature-x"


def test_create_branch_rejects_flag_like_branch_name(tmp_path):
    source = _init_repo(tmp_path / "source")
    workspace_root = tmp_path / "workspace"
    git_ops.clone_repository(workspace_root, str(source), "cloned")

    with pytest.raises(SandboxViolation):
        git_ops.create_branch(workspace_root, "cloned", "--orphan")


def test_checkout_branch_switches_back(tmp_path):
    source = _init_repo(tmp_path / "source")
    workspace_root = tmp_path / "workspace"
    git_ops.clone_repository(workspace_root, str(source), "cloned")
    git_ops.create_branch(workspace_root, "cloned", "feature-x")

    result = git_ops.checkout_branch(workspace_root, "cloned", "main")

    assert result.success is True
    assert git_ops.current_branch(workspace_root, "cloned").stdout.strip() == "main"


def test_git_push_to_local_remote(tmp_path):
    """Real end-to-end push: a bare repo stands in for GitHub, clone it,
    commit a change, push, then verify the bare repo actually received
    the new commit -- not just that git exited 0."""
    bare_remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_remote)], check=True, capture_output=True)

    # The remote is seeded with raw git rather than through git_ops:
    # seeding 'main' is a human setting up a fixture, not iV committing,
    # and the protected-branch guard exists precisely to stop the latter.
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(bare_remote), str(seed)], check=True, capture_output=True)
    (seed / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=seed, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=seed, check=True, capture_output=True, env=_SEED_ENV)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=seed, check=True, capture_output=True)

    workspace_root = tmp_path / "workspace"
    clone_result = git_ops.clone_repository(workspace_root, str(bare_remote), "cloned")
    assert clone_result.success is True
    assert git_ops.create_branch(workspace_root, "cloned", "iv-work").success is True
    (workspace_root / "cloned" / "new_file.txt").write_text("pushed content")
    assert git_ops.git_commit(workspace_root, "cloned", "add new_file.txt").success is True

    push = git_ops.git_push(workspace_root, "cloned", remote="origin", branch="iv-work")

    assert push.success is True

    verify_root = tmp_path / "verify-workspace"
    verify = git_ops.clone_repository(verify_root, str(bare_remote), "cloned")
    assert verify.success is True
    assert git_ops.checkout_branch(verify_root, "cloned", "iv-work").success is True
    assert (verify_root / "cloned" / "new_file.txt").exists()
    # main must be untouched by everything above.
    assert git_ops.checkout_branch(verify_root, "cloned", "main").success is True
    assert not (verify_root / "cloned" / "new_file.txt").exists()


def test_git_push_refuses_a_protected_branch(tmp_path):
    source = _init_repo(tmp_path / "source")
    workspace_root = tmp_path / "workspace"
    git_ops.clone_repository(workspace_root, str(source), "cloned")

    with pytest.raises(ProtectedBranchViolation, match="protected branch"):
        git_ops.git_push(workspace_root, "cloned", remote="origin", branch="main")


def test_git_push_rejects_flag_like_remote(tmp_path):
    source = _init_repo(tmp_path / "source")
    workspace_root = tmp_path / "workspace"
    git_ops.clone_repository(workspace_root, str(source), "cloned")

    with pytest.raises(SandboxViolation):
        git_ops.git_push(workspace_root, "cloned", remote="--force")
