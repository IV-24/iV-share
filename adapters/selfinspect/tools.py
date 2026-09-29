"""Registers adapters/selfinspect's read-only introspection as
ToolDefinitions.

Every tool here is LOW risk and AUTO execution, which is the correct
tier and not a shortcut: none of them has a side effect to approve.
There is no self_write_file, no self_run_command, and no
self_git_commit — not registered as REQUIRES_APPROVAL, not registered at
all. The first self-inspection experiment answers "can iV understand the
system that created it," and the honest way to bound that experiment is
to give it no way to change that system, rather than a way it is asked
not to use.

All of them require the self.inspect scope (see
core/permissions/scopes.py), so a role that can read cloned repos does
not automatically gain the ability to read iV's own source.
"""

from __future__ import annotations

from pathlib import Path

from adapters.selfinspect.repo import SelfInspector
from core.permissions.scopes import PermissionScope
from core.tools.base import ExecutionPolicy, RiskLevel, ToolDefinition
from core.tools.registry import ToolRegistry

SELF_INSPECTION_TOOL_NAMES = [
    "self_list_files",
    "self_read_file",
    "self_search_repository",
    "self_inspect_dependencies",
    "self_inspect_configuration",
    "self_list_tests",
    "self_read_logs",
    "self_git",
]


def _low_read_only(name: str, description: str, input_schema: dict, handler) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        input_schema=input_schema,
        output_schema={"type": "object"},
        handler=handler,
        required_permissions=[PermissionScope.SELF_INSPECT],
        risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    )


def register_self_inspection_tools(registry: ToolRegistry, *, install_root: Path | str) -> SelfInspector:
    inspector = SelfInspector(install_root)

    def self_list_files(path: str = ".", recursive: bool = False):
        return {"root": str(inspector.root), "entries": inspector.list_files(path, recursive=recursive)}

    def self_read_file(path: str, max_bytes: int = 200_000):
        return inspector.read_file(path, max_bytes=max_bytes)

    def self_search_repository(pattern: str, path: str = ".", glob: str = "*", max_matches: int = 200):
        return {"pattern": pattern, "matches": inspector.search(
            pattern, relative_dir=path, glob=glob, max_matches=max_matches
        )}

    def self_inspect_dependencies():
        return {"manifests": inspector.inspect_dependencies()}

    def self_inspect_configuration():
        return inspector.inspect_configuration()

    def self_list_tests():
        return {"tests": inspector.list_tests()}

    def self_read_logs(log_name: str = "backend.log", lines: int = 200):
        return inspector.read_logs(log_name=log_name, lines=lines)

    def self_git(command: str = "status"):
        return inspector.git(command)

    registry.register(_low_read_only(
        "self_list_files",
        "Lists files and directories inside iV's own installation directory. Read-only; "
        "dependency, build, and secret directories are excluded.",
        {"type": "object", "properties": {
            "path": {"type": "string", "description": "Directory relative to the iV install root."},
            "recursive": {"type": "boolean"},
        }},
        self_list_files,
    ))
    registry.register(_low_read_only(
        "self_read_file",
        "Reads one of iV's own source/config/documentation files as text. Refuses .env files, "
        "key material, and the SQLite database.",
        {"type": "object", "properties": {
            "path": {"type": "string"}, "max_bytes": {"type": "integer"},
        }, "required": ["path"]},
        self_read_file,
    ))
    registry.register(_low_read_only(
        "self_search_repository",
        "Regex-searches iV's own source for a pattern, returning file, line number, and matching line.",
        {"type": "object", "properties": {
            "pattern": {"type": "string"}, "path": {"type": "string"},
            "glob": {"type": "string"}, "max_matches": {"type": "integer"},
        }, "required": ["pattern"]},
        self_search_repository,
    ))
    registry.register(_low_read_only(
        "self_inspect_dependencies",
        "Returns iV's dependency manifests (Python requirements, frontend package.json, pytest config).",
        {"type": "object", "properties": {}},
        self_inspect_dependencies,
    ))
    registry.register(_low_read_only(
        "self_inspect_configuration",
        "Reports which configuration variables are set (names and non-secret values only — never "
        "the value of an API key or secret) plus the install root.",
        {"type": "object", "properties": {}},
        self_inspect_configuration,
    ))
    registry.register(_low_read_only(
        "self_list_tests",
        "Lists iV's own test files, for reasoning about coverage gaps.",
        {"type": "object", "properties": {}},
        self_list_tests,
    ))
    registry.register(_low_read_only(
        "self_read_logs",
        "Reads the tail of one of iV's runtime log files, with secrets redacted.",
        {"type": "object", "properties": {
            "log_name": {"type": "string"}, "lines": {"type": "integer"},
        }},
        self_read_logs,
    ))
    registry.register(_low_read_only(
        "self_git",
        "Runs one read-only git command against iV's own repository. Allowed: status, diff, "
        "diff_staged, log, branch, remotes, tracked_files.",
        {"type": "object", "properties": {"command": {"type": "string"}}},
        self_git,
    ))

    return inspector
