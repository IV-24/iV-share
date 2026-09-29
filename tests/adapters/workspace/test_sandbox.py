import pytest

from adapters.workspace.sandbox import (
    SandboxViolation,
    reject_flag_like,
    resolve_file_path,
    resolve_repo_name,
    resolve_repo_path,
)


def test_resolve_repo_name_rejects_slashes_and_traversal():
    for bad in ["../escape", "a/b", "a\\b", "", ".", ".."]:
        with pytest.raises(SandboxViolation):
            resolve_repo_name(bad)


def test_resolve_repo_name_rejects_flag_like_values():
    with pytest.raises(SandboxViolation):
        resolve_repo_name("--upload-pack=touch pwned")


def test_reject_flag_like_allows_normal_values():
    reject_flag_like("main", "branch_name")
    reject_flag_like("https://github.com/x/y.git", "url")


def test_resolve_repo_path_stays_within_workspace_root(tmp_path):
    resolved = resolve_repo_path(tmp_path, "my-repo")
    assert resolved == (tmp_path / "my-repo").resolve()


def test_resolve_repo_path_rejects_traversal_via_relative_segments(tmp_path):
    with pytest.raises(SandboxViolation):
        resolve_repo_path(tmp_path, "..")


def test_resolve_file_path_requires_existing_repo(tmp_path):
    with pytest.raises(SandboxViolation):
        resolve_file_path(tmp_path, "not-cloned-yet", "README.md")


def test_resolve_file_path_rejects_absolute_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(SandboxViolation):
        resolve_file_path(tmp_path, "repo", "/etc/passwd")


def test_resolve_file_path_rejects_traversal_out_of_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "secret.txt").write_text("outside the repo")

    with pytest.raises(SandboxViolation):
        resolve_file_path(tmp_path, "repo", "../secret.txt")


def test_resolve_file_path_allows_nested_paths_inside_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("print('hi')")

    resolved = resolve_file_path(tmp_path, "repo", "src/main.py")
    assert resolved == (repo / "src" / "main.py").resolve()


def test_resolve_file_path_rejects_symlink_escape(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope")

    link = repo / "escape"
    link.symlink_to(outside)

    with pytest.raises(SandboxViolation):
        resolve_file_path(tmp_path, "repo", "escape/secret.txt")
