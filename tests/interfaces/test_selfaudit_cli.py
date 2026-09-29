"""The self-audit CLI is how the first audit of a machine gets run —
before anyone trusts the server to be up — so its contract is worth
pinning: a non-zero exit on CRITICAL findings (usable as a gate), a
markdown report where it was asked for, and clean JSON on stdout with
logs kept off it."""

import json

import pytest

from core.configuration.settings import Settings, StorageConfig, WorkspaceConfig
from interfaces.cli import selfaudit as cli
from interfaces.api.runtime import build_runtime


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    """Point the CLI's runtime at a scratch database and workspace, and
    stop it reading the developer's real backend/.env."""
    monkeypatch.setattr(cli, "load_environment", lambda: None)
    monkeypatch.setenv("IV_LOCAL_DB_PATH", str(tmp_path / "iv-cli.db"))
    monkeypatch.setenv("IV_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setattr(cli, "build_runtime", lambda: build_runtime(Settings(
        storage=StorageConfig(local_db_path=str(tmp_path / "iv-cli.db")),
        workspace=WorkspaceConfig(workspace_root=str(tmp_path / "workspace")),
    )))


def test_no_model_run_exits_zero_and_prints_a_report(capsys):
    exit_code = cli.main(["--no-model"])

    out = capsys.readouterr().out
    assert exit_code == 0  # the real repository has no CRITICAL findings
    assert "# iV Self-Audit Report" in out
    assert "Severity counts" in out


def test_json_output_is_parseable_with_nothing_else_on_stdout(capsys):
    """Logs go to stderr precisely so this holds — the first run of this
    CLI emitted log lines into its own JSON."""
    cli.main(["--no-model", "--json"])

    payload = json.loads(capsys.readouterr().out)

    assert payload["model_review_status"] == "not_attempted"
    assert "counts" in payload and "findings" in payload


def test_report_is_written_to_the_requested_path(tmp_path, capsys):
    destination = tmp_path / "nested" / "audit.md"

    cli.main(["--no-model", "--output", str(destination)])
    capsys.readouterr()

    assert destination.is_file()
    assert "# iV Self-Audit Report" in destination.read_text()


def test_critical_findings_make_the_command_exit_non_zero(monkeypatch, capsys):
    """Exercised with a stubbed result rather than by planting a real
    credential in the repository — the point is the exit contract."""
    from core.selfaudit.engine import SelfAuditResult

    monkeypatch.setattr(cli, "run_api_self_audit", lambda runtime, use_model: SelfAuditResult(
        started_at="t0", finished_at="t1", summary="s", counts={"CRITICAL": 1},
    ))

    assert cli.main(["--no-model"]) == 1
    capsys.readouterr()
