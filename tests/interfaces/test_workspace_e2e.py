"""Full-stack smoke test for the repo-editing capability: build_runtime()
(the exact same assembly interfaces/api/main.py uses at startup) wired to
a real SQLite file and a real workspace root on disk, driving the
Engineering role through AgentOrchestrator with a scripted multi-turn
tool-calling exchange against a real local git repository. Only the model
responses are scripted -- every clone/read/write/commit/status/approval
step below is the real handler running against real files and a real git
subprocess, the same code path a real provider's tool calls would hit.
"""

import subprocess
from dataclasses import replace

from core.agent.roles import DEFAULT_ROLES
from core.configuration.settings import Settings, StorageConfig, WorkspaceConfig
from core.models.base import ModelInfo, ModelProvider, ModelResponse, ToolCall
from interfaces.api.runtime import build_runtime


class ScriptedProvider(ModelProvider):
    """Returns each ModelResponse in `script`, in order, one per call to
    generate() -- same pattern tests/core/test_agent_orchestrator.py uses,
    applied here against the real ApiRuntime instead of a bare-bones
    orchestrator."""

    name = "scripted"

    def __init__(self, script: list[ModelResponse]) -> None:
        self._script = list(script)

    def list_models(self) -> list[ModelInfo]:
        return []

    def generate(self, request):
        return self._script.pop(0)


def _seed_source_repo(tmp_path):
    source = tmp_path / "source-repo"
    source.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(source)], check=True, capture_output=True)
    (source / "README.md").write_text("original content\n")
    env = {
        **__import__("os").environ,
        "GIT_AUTHOR_NAME": "seed", "GIT_AUTHOR_EMAIL": "seed@localhost",
        "GIT_COMMITTER_NAME": "seed", "GIT_COMMITTER_EMAIL": "seed@localhost",
    }
    subprocess.run(["git", "add", "-A"], cwd=source, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=source, check=True, capture_output=True, env=env)
    return source


def _scripted_engineering_role():
    """A copy of the real Engineering role (same tool_names/permission_
    scopes registered in core/agent/roles.py) with its provider order
    swapped to the deterministic scripted one -- everything else about
    how the role reaches these tools is exactly what a real deployment
    would enforce."""
    return replace(DEFAULT_ROLES["engineering"], model_provider_order=["scripted"])


def test_engineering_role_clones_edits_and_commits_a_real_repo(tmp_path):
    source = _seed_source_repo(tmp_path)
    settings = Settings(
        storage=StorageConfig(local_db_path=str(tmp_path / "iv-test.db")),
        workspace=WorkspaceConfig(workspace_root=str(tmp_path / "workspace")),
    )
    runtime = build_runtime(settings)
    try:
        runtime.models.register(ScriptedProvider([
            ModelResponse(
                text="", model_name="m", provider="scripted",
                tool_calls=[ToolCall(
                    id="call-1", name="clone_repository",
                    arguments={"url": str(source), "repo_name": "target"},
                )],
            ),
            ModelResponse(
                text="", model_name="m", provider="scripted",
                tool_calls=[ToolCall(
                    id="call-2", name="write_repository_file",
                    arguments={"repo_name": "target", "path": "NOTES.md", "content": "iV was here\n"},
                )],
            ),
            ModelResponse(
                text="", model_name="m", provider="scripted",
                tool_calls=[ToolCall(
                    id="call-3", name="create_branch",
                    arguments={"repo_name": "target", "branch_name": "iv-notes"},
                )],
            ),
            ModelResponse(
                text="", model_name="m", provider="scripted",
                tool_calls=[ToolCall(
                    id="call-4", name="git_commit",
                    arguments={"repo_name": "target", "message": "add NOTES.md"},
                )],
            ),
            ModelResponse(
                text="", model_name="m", provider="scripted",
                tool_calls=[ToolCall(id="call-5", name="git_status", arguments={"repo_name": "target"})],
            ),
            ModelResponse(text="Cloned the repo, added NOTES.md, and committed it.", model_name="m", provider="scripted"),
        ]))
        role = _scripted_engineering_role()

        result = runtime.orchestrator.handle_message(
            role, "clone the repo, add a note, and commit it", max_tool_iterations=6
        )

        assert result.response.text == "Cloned the repo, added NOTES.md, and committed it."

        cloned_repo = tmp_path / "workspace" / "target"
        assert (cloned_repo / "NOTES.md").read_text() == "iV was here\n"
        log = subprocess.run(
            ["git", "log", "--oneline", "-1"], cwd=cloned_repo, capture_output=True, text=True, check=True
        )
        assert "add NOTES.md" in log.stdout
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cloned_repo,
            capture_output=True, text=True, check=True,
        )
        # The commit landed on the working branch iV created, never on the
        # clone's default branch — see core/safety/branches.py.
        assert branch.stdout.strip() == "iv-notes"
    finally:
        runtime.storage.close()


def test_engineering_role_run_command_is_blocked_until_approved(tmp_path):
    """The CRITICAL-risk escape hatch must actually stop and wait for a
    human, end to end: the model's tool call comes back denied with
    approval_required, the command has NOT run, and only after a human
    decision does re-submitting with the approval_id let it through."""
    source = _seed_source_repo(tmp_path)
    settings = Settings(
        storage=StorageConfig(local_db_path=str(tmp_path / "iv-test.db")),
        workspace=WorkspaceConfig(workspace_root=str(tmp_path / "workspace")),
    )
    runtime = build_runtime(settings)
    try:
        role = _scripted_engineering_role()
        cloned = runtime.orchestrator.run_tool(
            role, "clone_repository", {"url": str(source), "repo_name": "target"}
        )
        assert cloned.success is True

        denied = runtime.orchestrator.run_tool(
            role, "run_repository_command", {"repo_name": "target", "command": "touch UNAPPROVED.txt"}
        )
        assert denied.success is False
        assert denied.error == "approval_required"
        assert not (tmp_path / "workspace" / "target" / "UNAPPROVED.txt").exists()

        runtime.approvals.decide(denied.approval_id, approved=True, decided_by="owner")
        approved = runtime.orchestrator.run_tool(
            role, "run_repository_command", {"repo_name": "target", "command": "touch UNAPPROVED.txt"},
            approval_id=denied.approval_id,
        )

        assert approved.success is True
        assert (tmp_path / "workspace" / "target" / "UNAPPROVED.txt").exists()
    finally:
        runtime.storage.close()


def test_finance_role_cannot_reach_workspace_tools_even_though_they_are_registered(tmp_path):
    """The permission boundary between roles is real, not just a naming
    convention: Finance is never configured with any workspace tool
    names, so even though register_workspace_tools() ran during startup
    and the tools exist in the registry, Finance can't call them."""
    settings = Settings(
        storage=StorageConfig(local_db_path=str(tmp_path / "iv-test.db")),
        workspace=WorkspaceConfig(workspace_root=str(tmp_path / "workspace")),
    )
    runtime = build_runtime(settings)
    try:
        assert any(t["name"] == "clone_repository" for t in runtime.tools.list_tools())

        result = runtime.orchestrator.run_tool(
            DEFAULT_ROLES["finance"], "clone_repository", {"url": "https://example.com/x.git", "repo_name": "x"}
        )

        assert result.success is False
        assert "not configured with access" in result.error
    finally:
        runtime.storage.close()
