"""adapters/selfinspect must be read-only and escape-proof. These tests
assert both properties directly rather than trusting the module's own
docstring: the confinement checks use real symlinks and real traversal
attempts, and test_module_exposes_no_write_capability inspects the public
surface so a future "small" addition of a write helper fails here."""

import inspect
import os
import subprocess

import pytest

from adapters.selfinspect import repo as repo_module
from adapters.selfinspect.repo import SelfInspectionDenied, SelfInspector


@pytest.fixture
def install(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "thing.py").write_text("def go():\n    return 1  # TODO: real logic\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_thing.py").write_text("from core.thing import go\n")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (tmp_path / "backend" / ".env").write_text("GROQ_API_KEY=gsk_supersecretvalue\n")
    (tmp_path / "iv.db").write_bytes(b"SQLite format 3\x00")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.py").write_text("x = 1\n")
    return tmp_path


def test_read_file_returns_content_and_metadata(install):
    result = SelfInspector(install).read_file("core/thing.py")

    assert "def go()" in result["content"]
    assert result["path"] == "core/thing.py"
    assert result["truncated"] is False


def test_read_file_refuses_env_files(install):
    with pytest.raises(SelfInspectionDenied, match="credentials"):
        SelfInspector(install).read_file("backend/.env")


def test_read_file_refuses_the_database(install):
    with pytest.raises(SelfInspectionDenied):
        SelfInspector(install).read_file("iv.db")


def test_read_file_refuses_absolute_paths(install):
    with pytest.raises(SelfInspectionDenied, match="relative"):
        SelfInspector(install).read_file("/etc/passwd")


def test_read_file_refuses_parent_traversal(install):
    with pytest.raises(SelfInspectionDenied, match="escapes"):
        SelfInspector(install).read_file("../outside.txt")


def test_read_file_refuses_symlink_escape(install, tmp_path):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret")
    os.symlink(outside, install / "link.txt")

    with pytest.raises(SelfInspectionDenied, match="escapes"):
        SelfInspector(install).read_file("link.txt")


def test_listing_skips_dependency_directories(install):
    paths = [entry["path"] for entry in SelfInspector(install).list_files(".", recursive=True)]

    assert "core/thing.py" in paths
    assert not any(path.startswith("node_modules") for path in paths)


def test_search_reports_file_and_line(install):
    matches = SelfInspector(install).search(r"TODO")

    assert matches == [{"path": "core/thing.py", "line": 2, "text": "return 1  # TODO: real logic"}]


def test_search_never_matches_inside_secret_files(install):
    assert SelfInspector(install).search("gsk_supersecretvalue") == []


def test_inspect_configuration_reports_presence_not_values(install, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_supersecretvalue")

    config = SelfInspector(install).inspect_configuration()

    assert config["secrets_configured"]["GROQ_API_KEY"] is True
    assert "gsk_supersecretvalue" not in repr(config)


def test_git_rejects_commands_outside_the_allowlist(install):
    for forbidden in ("commit", "push", "checkout", "clean", "gc"):
        with pytest.raises(SelfInspectionDenied, match="allowlist"):
            SelfInspector(install).git(forbidden)


def test_git_status_runs_against_the_real_repository(tmp_path):
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "a.py").write_text("x = 1\n")

    result = SelfInspector(tmp_path).git("status")

    assert result["success"] is True
    assert "a.py" in result["stdout"]


def test_read_logs_redacts_secrets(install, monkeypatch):
    from core.observability import redaction

    monkeypatch.setattr(redaction, "_registered", set())
    redaction.register_secret("gsk_supersecretvalue")
    (install / ".launchd").mkdir()
    (install / ".launchd" / "backend.log").write_text("starting with key gsk_supersecretvalue\n")

    result = SelfInspector(install).read_logs()

    assert result["exists"] is True
    assert "gsk_supersecretvalue" not in "\n".join(result["lines"])
    assert "REDACTED" in "\n".join(result["lines"])


def test_read_logs_rejects_a_path_as_a_log_name(install):
    with pytest.raises(SelfInspectionDenied):
        SelfInspector(install).read_logs(log_name="../../etc/passwd.log")


def test_module_exposes_no_write_capability():
    """Structural guarantee, asserted rather than documented: nothing in
    this module writes, deletes, or executes anything the caller chose.
    A future helper named like one fails this test on sight."""
    forbidden = ("write", "delete", "remove", "unlink", "mkdir", "chmod", "move", "rename", "exec", "run_command")
    public = [
        name for name, _ in inspect.getmembers(SelfInspector, inspect.isfunction)
        if not name.startswith("_")
    ]
    assert not [name for name in public if any(word in name for word in forbidden)]

    source = inspect.getsource(repo_module)
    for dangerous in ("write_text(", "write_bytes(", "os.remove", "shutil.rmtree", "shell=True"):
        assert dangerous not in source


def test_top_level_workspace_is_hidden_but_adapters_workspace_is_not(install):
    """Regression from iV's first self-audit: excluding any path component
    named 'workspace' hid adapters/workspace/ — the sandboxed repo-editing
    adapter — from iV's view of its own source. Only the top-level
    sandbox clone directory should be hidden."""
    (install / "workspace").mkdir()
    (install / "workspace" / "someones-clone.py").write_text("x = 1\n")
    (install / "adapters" / "workspace").mkdir(parents=True)
    (install / "adapters" / "workspace" / "sandbox.py").write_text("def resolve():\n    pass\n")

    paths = [entry["path"] for entry in SelfInspector(install).list_files(".", recursive=True)]

    assert "adapters/workspace/sandbox.py" in paths
    assert not any(path.startswith("workspace/") for path in paths)
    assert "def resolve" in SelfInspector(install).read_file("adapters/workspace/sandbox.py")["content"]
