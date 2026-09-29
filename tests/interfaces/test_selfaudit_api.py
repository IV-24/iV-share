"""End-to-end self-audit through the HTTP trigger and the real runtime:
the auditor role reaching iV's actual source through the real
ToolRegistry, with permission enforcement in the path rather than around
it."""

import pytest
from fastapi.testclient import TestClient

from core.agent.roles import DEFAULT_ROLES
from core.configuration.settings import Settings, StorageConfig, WorkspaceConfig
from core.permissions.scopes import PermissionScope
from interfaces.api.main import create_app
from interfaces.api.runtime import build_runtime
from interfaces.api.selfaudit_runner import run_api_self_audit

INTERNAL_SECRET = "internal-test-secret"


@pytest.fixture
def real_runtime(tmp_path):
    runtime = build_runtime(Settings(
        storage=StorageConfig(local_db_path=str(tmp_path / "iv-test.db")),
        workspace=WorkspaceConfig(workspace_root=str(tmp_path / "workspace")),
    ))
    try:
        yield runtime
    finally:
        runtime.storage.close()


def test_runtime_registers_read_only_self_inspection_tools(real_runtime):
    names = {tool["name"] for tool in real_runtime.tools.list_tools()}

    assert {"self_read_file", "self_search_repository", "self_git"} <= names
    # The corresponding write capabilities must not exist anywhere in the
    # registry — not as approval-gated tools, not at all.
    assert not {"self_write_file", "self_run_command", "self_git_commit"} & names


def test_self_inspection_tools_are_denied_without_the_scope(real_runtime):
    result = real_runtime.tools.execute(
        "self_read_file", {"path": "README.md"}, principal="Nobody"
    )

    assert result.success is False
    assert PermissionScope.SELF_INSPECT in result.error


def test_auditor_can_read_ivs_own_source_through_the_registry(real_runtime):
    role = DEFAULT_ROLES["auditor"]
    for scope in role.permission_scopes:
        real_runtime.permissions.grant(role.name, scope, granted_by="test")

    result = real_runtime.tools.execute(
        "self_read_file", {"path": "core/agent/orchestrator.py"}, principal=role.name
    )

    assert result.success is True
    assert "class AgentOrchestrator" in result.output["content"]


def test_auditor_role_tool_names_match_the_registered_tools(real_runtime):
    """core/agent/roles.py lists the self-inspection tool names literally
    (core must not import adapters). This is the test that keeps the two
    lists from drifting apart silently."""
    from adapters.selfinspect.tools import SELF_INSPECTION_TOOL_NAMES

    registered = {tool["name"] for tool in real_runtime.tools.list_tools()}
    auditor_tools = set(DEFAULT_ROLES["auditor"].tool_names)

    assert set(SELF_INSPECTION_TOOL_NAMES) <= auditor_tools
    assert auditor_tools <= registered


def test_self_audit_over_the_real_repository_produces_evidence_backed_findings(real_runtime):
    result = run_api_self_audit(real_runtime, use_model=False)

    assert result.findings, "the deterministic layer must produce findings against a real repository"
    for finding in result.findings:
        assert finding.evidence.strip()
        assert finding.location.strip()
        assert finding.recommended_fix.strip()


def test_self_audit_records_its_findings_in_ivs_own_stores(real_runtime):
    result = run_api_self_audit(real_runtime, use_model=False)

    memories = real_runtime.memory.query(limit=10)
    assert any(m.source == "self_audit" for m in memories)
    if result.project_id:
        assert real_runtime.tasks.list_for_project(result.project_id)


def test_http_trigger_requires_the_internal_secret(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_TRIGGER_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("IV_LOCAL_DB_PATH", str(tmp_path / "iv-http.db"))
    monkeypatch.setenv("IV_WORKSPACE_ROOT", str(tmp_path / "workspace"))

    app = create_app()
    with TestClient(app) as client:
        assert client.post("/internal/self-audit").status_code == 403

        response = client.post(
            "/internal/self-audit",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            json={"use_model": False},
        )

    assert response.status_code == 200
    body = response.json()
    assert "counts" in body and "findings" in body
    assert body["model_review_status"] == "not_attempted"
