"""Exercises the CLI's runtime assembly and one full turn end-to-end
(storage -> orchestrator -> NullModelProvider), without touching the
interactive input()/print() loop. Proves core is fully usable from a
non-web interface with nothing but a local SQLite path and no API keys."""

import os

from core.configuration.settings import Settings, StorageConfig, WorkspaceConfig
from interfaces.cli.main import ROOT_DIR, build_runtime, load_environment


def test_load_environment_defaults_db_path_to_install_directory(monkeypatch):
    """Regression test: the default database path must anchor to where
    the app is installed (ROOT_DIR), not to whatever directory the
    process happens to be launched from — running the CLI from a
    different cwd used to silently point at the wrong iv.db."""
    monkeypatch.delenv("IV_LOCAL_DB_PATH", raising=False)

    load_environment()

    assert os.environ["IV_LOCAL_DB_PATH"] == str(ROOT_DIR / "iv.db")


def test_load_environment_respects_explicit_override(monkeypatch):
    monkeypatch.setenv("IV_LOCAL_DB_PATH", "/some/explicit/path.db")

    load_environment()

    assert os.environ["IV_LOCAL_DB_PATH"] == "/some/explicit/path.db"


def test_load_environment_defaults_workspace_root_to_install_directory(monkeypatch):
    monkeypatch.delenv("IV_WORKSPACE_ROOT", raising=False)

    load_environment()

    assert os.environ["IV_WORKSPACE_ROOT"] == str(ROOT_DIR / "workspace")


def test_build_runtime_and_handle_one_turn(tmp_path):
    db_path = str(tmp_path / "iv-test.db")
    settings = Settings(
        storage=StorageConfig(local_db_path=db_path),
        workspace=WorkspaceConfig(workspace_root=str(tmp_path / "workspace")),
    )

    runtime = build_runtime(settings, role_name="coordinator")
    try:
        result = runtime.orchestrator.handle_message(runtime.role, "hello iV")
        assert "hello iV" in result.response.text
        assert result.role == "Coordinator"
    finally:
        runtime.storage.close()

    assert os.path.exists(db_path)


def test_unknown_role_raises():
    import pytest
    with pytest.raises(ValueError):
        build_runtime(Settings(), role_name="not-a-real-role")


def test_standard_tools_are_actually_registered(tmp_path):
    """Regression test: build_runtime() used to construct an empty
    ToolRegistry — every role's declared tool_names (create_project,
    create_task, ...) existed only as names nothing backed, so any tool
    call silently failed with 'unknown tool'. register_standard_tools()
    must actually run during CLI startup, not just the API server's."""
    db_path = str(tmp_path / "iv-test.db")
    settings = Settings(
        storage=StorageConfig(local_db_path=db_path),
        workspace=WorkspaceConfig(workspace_root=str(tmp_path / "workspace")),
    )

    runtime = build_runtime(settings, role_name="coordinator")
    try:
        result = runtime.orchestrator.run_tool(runtime.role, "create_project", {"name": "iV Phase 2"})
        assert result.success is True
        assert result.output["name"] == "iV Phase 2"
    finally:
        runtime.storage.close()
