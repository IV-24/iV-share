"""run.py sets an install-directory-relative IV_LOCAL_DB_PATH default as
a module-level side effect (matching its pre-existing load_dotenv()
pattern) — importing it must not start the server (that's guarded by
`if __name__ == "__main__":`), so this is safe to exercise directly."""

import importlib.util
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
RUN_PY = ROOT_DIR / "run.py"


def _import_run_py():
    spec = importlib.util.spec_from_file_location("iv_run_module_under_test", RUN_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_py_defaults_db_path_to_install_directory(monkeypatch):
    monkeypatch.delenv("IV_LOCAL_DB_PATH", raising=False)

    _import_run_py()

    assert os.environ["IV_LOCAL_DB_PATH"] == str(ROOT_DIR / "iv.db")


def test_run_py_respects_explicit_override(monkeypatch):
    monkeypatch.setenv("IV_LOCAL_DB_PATH", "/some/explicit/path.db")

    _import_run_py()

    assert os.environ["IV_LOCAL_DB_PATH"] == "/some/explicit/path.db"


def test_run_py_defaults_workspace_root_to_install_directory(monkeypatch):
    monkeypatch.delenv("IV_WORKSPACE_ROOT", raising=False)

    _import_run_py()

    assert os.environ["IV_WORKSPACE_ROOT"] == str(ROOT_DIR / "workspace")


def test_run_py_respects_explicit_workspace_root_override(monkeypatch):
    monkeypatch.setenv("IV_WORKSPACE_ROOT", "/some/explicit/workspace")

    _import_run_py()

    assert os.environ["IV_WORKSPACE_ROOT"] == "/some/explicit/workspace"
