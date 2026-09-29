import pytest

from adapters.workspace import files
from adapters.workspace.sandbox import SandboxViolation


def _make_repo(tmp_path):
    repo = tmp_path / "workspace" / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("print('hi')\n")
    (repo / ".git").mkdir()  # list_files must skip this
    return tmp_path / "workspace"


def test_read_file_returns_content(tmp_path):
    workspace_root = _make_repo(tmp_path)
    content = files.read_file(workspace_root, "repo", "src/main.py")
    assert content == "print('hi')\n"


def test_read_file_missing_raises(tmp_path):
    workspace_root = _make_repo(tmp_path)
    with pytest.raises(FileNotFoundError):
        files.read_file(workspace_root, "repo", "src/does_not_exist.py")


def test_read_file_escaping_repo_is_blocked(tmp_path):
    workspace_root = _make_repo(tmp_path)
    (tmp_path / "workspace" / "secret.txt").write_text("nope")
    with pytest.raises(SandboxViolation):
        files.read_file(workspace_root, "repo", "../secret.txt")


def test_write_file_creates_and_overwrites(tmp_path):
    workspace_root = _make_repo(tmp_path)
    files.write_file(workspace_root, "repo", "src/new_module.py", "x = 1\n")
    assert files.read_file(workspace_root, "repo", "src/new_module.py") == "x = 1\n"

    files.write_file(workspace_root, "repo", "src/new_module.py", "x = 2\n")
    assert files.read_file(workspace_root, "repo", "src/new_module.py") == "x = 2\n"


def test_write_file_creates_intermediate_directories(tmp_path):
    workspace_root = _make_repo(tmp_path)
    files.write_file(workspace_root, "repo", "docs/nested/note.md", "hello\n")
    assert files.read_file(workspace_root, "repo", "docs/nested/note.md") == "hello\n"


def test_write_file_escaping_repo_is_blocked(tmp_path):
    workspace_root = _make_repo(tmp_path)
    with pytest.raises(SandboxViolation):
        files.write_file(workspace_root, "repo", "../escape.txt", "nope")


def test_list_files_excludes_git_dir(tmp_path):
    workspace_root = _make_repo(tmp_path)
    entries = files.list_files(workspace_root, "repo")
    assert "src/" in entries
    assert ".git/" not in entries
    assert ".git" not in entries


def test_list_files_nested_directory(tmp_path):
    workspace_root = _make_repo(tmp_path)
    entries = files.list_files(workspace_root, "repo", "src")
    assert entries == ["src/main.py"]


def test_list_files_non_directory_raises(tmp_path):
    workspace_root = _make_repo(tmp_path)
    with pytest.raises(NotADirectoryError):
        files.list_files(workspace_root, "repo", "src/main.py")
