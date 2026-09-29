"""The audit's own correctness matters more than most code here, because
its output is what a human will act on. Two properties get the most
attention: a check must produce evidence that actually points at the
thing it claims (not a paraphrase), and a failure anywhere in the audit
must degrade rather than erase — a broken check, an unavailable model, or
a failing store must never turn a real finding into silence."""

import pytest

from core.selfaudit.checks import (
    check_committed_secrets,
    check_debug_defaults,
    check_shell_execution,
    check_untested_modules,
    run_static_checks,
)
from core.selfaudit.engine import parse_model_findings, render_markdown_report, run_self_audit
from core.selfaudit.findings import Classification, Confidence, Finding, Severity, severity_counts, sort_findings


class FakeReader:
    """Stands in for adapters/selfinspect.SelfInspector. core/selfaudit
    depends on the structural interface, not the adapter, which is what
    makes this substitution possible at all."""

    def __init__(self, files: dict[str, str], *, git_outputs: dict | None = None):
        self._files = files
        self._git = git_outputs or {}

    def search(self, pattern, *, relative_dir=".", glob="*", max_matches=200):
        import re

        compiled = re.compile(pattern)
        matches = []
        for path, content in self._files.items():
            if glob == "*.py" and not path.endswith(".py"):
                continue
            for lineno, line in enumerate(content.splitlines(), start=1):
                if compiled.search(line):
                    matches.append({"path": path, "line": lineno, "text": line.strip()})
        return matches[:max_matches]

    def read_file(self, relative_path, *, max_bytes=200_000):
        if relative_path not in self._files:
            raise FileNotFoundError(relative_path)
        return {"path": relative_path, "content": self._files[relative_path], "truncated": False}

    def source_files(self, *, suffixes=(".py",)):
        return [p for p in sorted(self._files) if p.endswith(tuple(suffixes))]

    def list_tests(self):
        return [p for p in sorted(self._files) if p.startswith("tests/")]

    def inspect_dependencies(self):
        return {k: v for k, v in self._files.items() if "requirements" in k or "package.json" in k}

    def git(self, command):
        return self._git.get(command, {"command": command, "success": True, "stdout": "", "stderr": ""})


def test_committed_secret_is_reported_with_the_literal_line():
    reader = FakeReader({"config.py": 'KEY = "ghp_' + "a" * 36 + '"\n'})

    findings = check_committed_secrets(reader)

    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL
    assert findings[0].classification is Classification.CONFIRMED_BUG
    assert "config.py:1" in findings[0].evidence


def test_clean_repository_produces_no_secret_findings():
    """The check must be quiet when there is nothing to say — a check that
    always fires is one nobody reads."""
    reader = FakeReader({"config.py": "KEY = os.getenv('GITHUB_TOKEN')\n"})

    assert check_committed_secrets(reader) == []


def test_shell_execution_check_ignores_explicit_shell_false():
    reader = FakeReader({
        "argv_only.py": "subprocess.run(argv, shell=False)\n",
        "risky.py": "subprocess.run(cmd, shell=True)\n",
    })

    findings = check_shell_execution(reader)

    assert len(findings) == 1
    assert "risky.py:1" in findings[0].evidence
    assert "argv_only.py" not in findings[0].evidence


def test_shell_execution_check_ignores_documentation_of_the_safe_behavior():
    """Regression from iV's first self-audit: the highest-severity finding
    of that run was git_ops.py's own docstring promising it never uses
    shell=True. Prose is not a call site."""
    reader = FakeReader({"git_ops.py": (
        '"""Every call uses an argv list (never shell=True), so no shell\n'
        'is ever involved."""\n'
        "# also not a call: shell=True\n"
        "subprocess.run(argv, shell=False)\n"
    )})

    assert check_shell_execution(reader) == []


def test_shell_execution_check_finds_a_multiline_call():
    reader = FakeReader({"risky.py": "subprocess.run(\n    argv,\n    shell=True,\n)\n"})

    findings = check_shell_execution(reader)

    assert len(findings) == 1
    assert "risky.py:1" in findings[0].evidence


def test_eval_and_exec_are_detected_as_calls_not_substrings():
    reader = FakeReader({
        "risky.py": "eval(user_input)\n",
        "innocent.py": "value = self.evaluate(x)\nname = 'executive'\n",
    })

    findings = check_shell_execution(reader)

    assert len(findings) == 1
    assert "eval()" in findings[0].problem
    assert "innocent.py" not in findings[0].evidence


def test_debug_default_check_finds_hardcoded_reload():
    reader = FakeReader({"run.py": "uvicorn.run(app, reload=True)\n"})

    findings = check_debug_defaults(reader)

    assert findings[0].severity is Severity.MEDIUM
    assert findings[0].classification is Classification.LIKELY_BUG


def test_untested_modules_check_counts_only_unreferenced_modules():
    reader = FakeReader({
        "core/covered.py": "x = 1\n",
        "core/uncovered.py": "y = 2\n",
        "tests/test_covered.py": "from core.covered import x\n",
    })

    findings = check_untested_modules(reader)

    assert "core/uncovered.py" in findings[0].evidence
    assert "core/covered.py" not in findings[0].evidence


def test_a_failing_check_becomes_a_finding_instead_of_losing_the_run():
    def exploding_check(reader):
        raise RuntimeError("check is broken")

    def working_check(reader):
        return [Finding(
            id="X", severity=Severity.LOW, category="c", location="l",
            problem="p", evidence="e", why_it_matters="w", recommended_fix="f",
        )]

    findings = run_static_checks(FakeReader({}), checks=[exploding_check, working_check])

    ids = {f.id for f in findings}
    assert "X" in ids
    assert any(i.startswith("AUDIT-ERR") for i in ids)


def test_parse_model_findings_tolerates_markdown_fences():
    raw = '```json\n{"summary": "s", "findings": [{"severity": "HIGH", "problem": "p"}]}\n```'

    summary, findings, status = parse_model_findings(raw)

    assert status == "ok"
    assert summary == "s"
    assert findings[0].severity is Severity.HIGH
    assert findings[0].source == "model"


def test_parse_model_findings_reports_unparseable_output():
    summary, findings, status = parse_model_findings("I could not comply.")

    assert status == "unparseable"
    assert findings == []


def test_parse_model_findings_defaults_unknown_severity_conservatively():
    summary, findings, _ = parse_model_findings('{"findings": [{"severity": "APOCALYPTIC", "problem": "p"}]}')

    assert findings[0].severity is Severity.MEDIUM
    assert findings[0].confidence is Confidence.LOW


def test_sorting_and_counting_order_by_severity():
    findings = [
        Finding(id="a", severity=Severity.LOW, category="c", location="l", problem="p", evidence="e",
                why_it_matters="w", recommended_fix="f"),
        Finding(id="b", severity=Severity.CRITICAL, category="c", location="l", problem="p", evidence="e",
                why_it_matters="w", recommended_fix="f"),
    ]

    assert [f.id for f in sort_findings(findings)] == ["b", "a"]
    assert severity_counts(findings)["CRITICAL"] == 1


class RecordingStores:
    def __init__(self):
        self.memories, self.projects, self.tasks, self.proposals = [], [], [], []

    # memory
    def add(self, content, *, memory_type=None, title="", importance=5, source=""):
        self.memories.append((title, content))
        return type("M", (), {"id": f"mem-{len(self.memories)}"})()

    # projects
    def create(self, name, description="", status="active", priority=5):
        self.projects.append(name)
        return type("P", (), {"id": "proj-1"})()

    def propose(self, *, agent_name, problem_identified, proposed_change, affected_files=None):
        self.proposals.append(problem_identified)
        return type("I", (), {"id": "imp-1", "approval_id": "appr-1"})()


class RecordingTasks:
    def __init__(self, sink):
        self._sink = sink

    def create(self, project_id, title, *, description="", priority=5, status="pending", assigned_role=None):
        self._sink.tasks.append(title)
        return type("T", (), {"id": f"task-{len(self._sink.tasks)}"})()


def test_audit_without_a_model_still_produces_and_persists_findings():
    """The no-provider case is the one this deployment actually hits, so
    it is tested as a first-class path: real findings, recorded, with the
    absence of a model review stated rather than implied."""
    reader = FakeReader({"run.py": "uvicorn.run(app, reload=True)\n"})
    stores = RecordingStores()

    result = run_self_audit(
        reader=reader, use_model=False, memory=stores, projects=stores,
        tasks=RecordingTasks(stores), improvements=stores,
    )

    assert result.model_review_status == "not_attempted"
    assert result.counts["MEDIUM"] >= 1
    assert result.memory_id == "mem-1"
    assert "no model review contributed" in result.summary


def test_high_severity_findings_become_tasks_and_an_improvement_proposal():
    reader = FakeReader({"config.py": 'KEY = "ghp_' + "b" * 36 + '"\n'})
    stores = RecordingStores()

    result = run_self_audit(
        reader=reader, use_model=False, memory=stores, projects=stores,
        tasks=RecordingTasks(stores), improvements=stores,
    )

    assert result.project_id == "proj-1"
    assert any("CRITICAL" in title for title in stores.tasks)
    assert result.approval_id == "appr-1"


def test_persistence_failure_does_not_discard_the_audit():
    class BrokenStore:
        def add(self, *a, **k):
            raise RuntimeError("disk full")

        def create(self, *a, **k):
            raise RuntimeError("disk full")

        def propose(self, *a, **k):
            raise RuntimeError("disk full")

    reader = FakeReader({"run.py": "uvicorn.run(app, reload=True)\n"})

    result = run_self_audit(
        reader=reader, use_model=False, memory=BrokenStore(), projects=BrokenStore(),
        tasks=BrokenStore(), improvements=BrokenStore(),
    )

    assert result.findings
    assert result.memory_id is None


def test_markdown_report_contains_every_finding_field():
    reader = FakeReader({"run.py": "uvicorn.run(app, reload=True)\n"})
    result = run_self_audit(reader=reader, use_model=False)

    report = render_markdown_report(result)

    for heading in ("Severity counts", "Summary", "Findings", "Evidence", "Why it matters", "Recommended fix"):
        assert heading in report


def test_untested_check_understands_from_package_import_module():
    """Regression from iV's first self-audit: substring matching on the
    dotted path reported four well-tested modules as untested, because
    `from adapters.workspace import files` never spells out
    'adapters.workspace.files'."""
    reader = FakeReader({
        "adapters/workspace/files.py": "def read_file():\n    pass\n",
        "adapters/workspace/shell.py": "def run_command():\n    pass\n",
        "tests/test_files.py": "from adapters.workspace import files\n",
    })

    findings = check_untested_modules(reader)

    assert "adapters/workspace/files.py" not in findings[0].evidence
    assert "adapters/workspace/shell.py" in findings[0].evidence


def test_secret_in_url_check_ignores_prose_about_the_practice():
    reader = FakeReader({
        "notes.py": "# /approvals/pending?secret=... ends up in access logs\n",
        "links.py": 'url = f"{base}/approvals/pending?secret={value}"\n',
    })

    from core.selfaudit.checks import check_secret_in_url

    findings = check_secret_in_url(reader)

    assert len(findings) == 1
    assert "links.py:1" in findings[0].evidence
    assert "notes.py" not in findings[0].evidence
