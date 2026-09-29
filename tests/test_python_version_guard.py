"""iV requires Python 3.10+ because 48 modules use PEP 604 unions
(`str | None`). On macOS 11/12 the default `python3` is older than that,
so the guard that reports this clearly is load-bearing — without it the
first symptom is a TypeError from inside an import chain, which reads
like a bug in iV rather than a too-old interpreter.

These tests protect two properties that are easy to break by accident:
the floor is stated in one place, and run.py stays parseable by the old
interpreter it needs to print the message to.
"""

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = (3, 10)


def test_run_py_declares_the_same_floor():
    source = (ROOT / "run.py").read_text()

    match = re.search(r"MINIMUM_PYTHON = \((\d+), (\d+)\)", source)

    assert match, "run.py must declare MINIMUM_PYTHON"
    assert (int(match.group(1)), int(match.group(2))) == REQUIRED


def test_run_sh_checks_the_same_floor():
    script = (ROOT / "run.sh").read_text()

    assert "sys.version_info >= (3, 10)" in script
    # And it must check the venv too: upgrading the system interpreter
    # does not migrate an already-built virtualenv.
    assert "backend/venv/bin/python -c" in script


def test_run_py_is_parseable_by_an_old_interpreter():
    """The guard cannot print its message if the file it lives in fails to
    parse first. Nothing in run.py may use syntax newer than 3.8."""
    tree = ast.parse((ROOT / "run.py").read_text(), feature_version=(3, 8))

    assert tree is not None


def test_the_guard_runs_before_any_core_import():
    """Import order matters: a `from core...` above the check would raise
    the TypeError the check exists to prevent. Checked on the AST rather
    than by substring — the explanatory comment above the guard mentions
    core/ and would otherwise match."""
    tree = ast.parse((ROOT / "run.py").read_text())

    guard_line = next(
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and getattr(node.targets[0], "id", "") == "MINIMUM_PYTHON"
    )
    core_imports = [
        node.lineno for node in ast.walk(tree)
        if (isinstance(node, ast.ImportFrom) and (node.module or "").startswith("core"))
        or (isinstance(node, ast.Import) and any(a.name.startswith("core") for a in node.names))
    ]

    assert all(lineno > guard_line for lineno in core_imports), core_imports


def test_the_floor_matches_what_the_source_actually_needs():
    """If every module were rewritten to avoid PEP 604 unions, this floor
    could drop — so assert the constraint still exists rather than
    trusting the comment."""
    offenders = [
        path for path in (ROOT / "core").rglob("*.py")
        if re.search(r":\s*\w+\s*\|\s*None", path.read_text())
    ]

    assert offenders, "no PEP 604 unions found — the 3.10 floor may no longer be needed"


def test_running_run_py_on_this_interpreter_passes_the_guard():
    """This interpreter satisfies the floor, so the guard must not fire.
    Uses --help-style early exit: a missing API_ACCESS_SECRET makes
    preflight exit 1 with its own message, which is still proof the
    version guard was cleared."""
    assert sys.version_info >= REQUIRED

    result = subprocess.run(
        [sys.executable, str(ROOT / "run.py")],
        capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/bin:/bin", "IV_LOCAL_DB_PATH": "/nonexistent-dir/iv.db"},
    )

    assert "needs Python" not in result.stderr
