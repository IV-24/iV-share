"""Sandboxed file read/write/list for repos under the workspace root. All
paths route through adapters/workspace/sandbox.py's resolve_file_path(),
so a model-supplied relative_path can never resolve outside the repo it
names -- see sandbox.py's docstring for how that's enforced.
"""

from pathlib import Path

from adapters.workspace.sandbox import resolve_file_path, resolve_repo_path

_MAX_READ_BYTES = 200_000


def read_file(workspace_root: Path, repo_name: str, relative_path: str) -> str:
    path = resolve_file_path(workspace_root, repo_name, relative_path)
    if not path.is_file():
        raise FileNotFoundError(f"no file at '{relative_path}' in repo '{repo_name}'")
    data = path.read_bytes()[:_MAX_READ_BYTES]
    return data.decode("utf-8", errors="replace")


def write_file(workspace_root: Path, repo_name: str, relative_path: str, content: str) -> None:
    path = resolve_file_path(workspace_root, repo_name, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def list_files(workspace_root: Path, repo_name: str, relative_dir: str = ".") -> list[str]:
    dir_path = resolve_file_path(workspace_root, repo_name, relative_dir)
    if not dir_path.is_dir():
        raise NotADirectoryError(f"'{relative_dir}' is not a directory in repo '{repo_name}'")

    repo_path = resolve_repo_path(workspace_root, repo_name)
    entries = []
    for child in sorted(dir_path.iterdir()):
        if child.name == ".git":
            continue
        suffix = "/" if child.is_dir() else ""
        entries.append(str(child.relative_to(repo_path)) + suffix)
    return entries
