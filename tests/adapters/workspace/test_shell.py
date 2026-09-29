from adapters.workspace import shell


def _make_repo(tmp_path):
    repo = tmp_path / "workspace" / "repo"
    repo.mkdir(parents=True)
    (repo / "marker.txt").write_text("present\n")
    return tmp_path / "workspace"


def test_run_command_executes_inside_repo_cwd(tmp_path):
    workspace_root = _make_repo(tmp_path)
    result = shell.run_command(workspace_root, "repo", "ls")
    assert result.success is True
    assert "marker.txt" in result.stdout


def test_run_command_captures_nonzero_exit(tmp_path):
    workspace_root = _make_repo(tmp_path)
    result = shell.run_command(workspace_root, "repo", "cat does_not_exist.txt")
    assert result.success is False
    assert result.return_code != 0


def test_run_command_missing_repo_reports_error_not_crash(tmp_path):
    workspace_root = tmp_path / "workspace"
    result = shell.run_command(workspace_root, "never-cloned", "ls")
    assert result.success is False
    assert "does not exist" in result.stderr


def test_run_command_empty_string_reports_error(tmp_path):
    workspace_root = _make_repo(tmp_path)
    result = shell.run_command(workspace_root, "repo", "   ")
    assert result.success is False
    assert "empty command" in result.stderr


def test_run_command_does_not_use_a_shell(tmp_path):
    """Shell metacharacters must not be interpreted -- ';' here should be
    passed as a literal argument to `echo`, not treated as a command
    separator. If this ever runs through shell=True, stdout would show
    'injected' on its own; it must not."""
    workspace_root = _make_repo(tmp_path)
    result = shell.run_command(workspace_root, "repo", "echo hello ; echo injected")
    assert "hello" in result.stdout
    assert result.stdout.strip() != "injected"


def test_run_command_unknown_binary_reports_error_not_crash(tmp_path):
    workspace_root = _make_repo(tmp_path)
    result = shell.run_command(workspace_root, "repo", "definitely-not-a-real-binary-xyz")
    assert result.success is False
