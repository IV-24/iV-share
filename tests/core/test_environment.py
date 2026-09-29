from core.environment.discovery import discover


def test_discover_is_read_only_and_returns_a_manifest(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    manifest = discover(working_directory=str(tmp_path))

    assert manifest.os_name
    assert manifest.python_version
    assert manifest.working_directory == str(tmp_path)
    assert manifest.is_git_repository is False  # tmp_path has no .git
    assert "GEMINI_API_KEY" not in manifest.configured_env_var_names


def test_discover_reports_configured_env_var_names_without_values(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-value")
    manifest = discover(working_directory=str(tmp_path))

    assert "GEMINI_API_KEY" in manifest.configured_env_var_names
    manifest_str = str(manifest)
    assert "super-secret-value" not in manifest_str


def test_discover_detects_git_repository(tmp_path):
    (tmp_path / ".git").mkdir()
    manifest = discover(working_directory=str(tmp_path))
    assert manifest.is_git_repository is True
