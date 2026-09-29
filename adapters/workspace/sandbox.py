"""Path-safety core for all repo-editing tools: every filesystem or git
operation goes through resolve_repo_path()/resolve_file_path(), which
confines resolution to a configured workspace root and rejects anything
that would escape it (absolute paths, '..' traversal, symlink escapes).
This is the one file the rest of adapters/workspace trusts to keep "the
model asked to read/write X" from ever reaching a path outside the
sandbox.
"""

from pathlib import Path


class SandboxViolation(ValueError):
    """Raised when a requested repo name or path would resolve outside
    the workspace root, or is shaped like a command-line flag where a
    plain name/path was expected."""


def ensure_workspace_root(root: Path) -> Path:
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def reject_flag_like(value: str, field_name: str) -> None:
    """Guards against git argument injection: a value that starts with
    '-' can be misread by git as an option instead of the positional
    argument it's meant to be (e.g. a clone URL of '--upload-pack=...').
    Every value that ends up as a bare positional argument to git or as a
    workspace directory name must pass through this first."""
    if value.startswith("-"):
        raise SandboxViolation(f"{field_name} must not start with '-': {value!r}")


def resolve_repo_name(repo_name: str) -> str:
    """A repo_name is a single path segment -- no slashes, no '..', not
    empty, not flag-like -- since it names a direct child directory of
    the workspace root, never a nested or escaping path."""
    if not repo_name or "/" in repo_name or "\\" in repo_name or repo_name in (".", ".."):
        raise SandboxViolation(f"invalid repo name: {repo_name!r}")
    reject_flag_like(repo_name, "repo_name")
    return repo_name


def resolve_repo_path(workspace_root: Path, repo_name: str) -> Path:
    """Resolves the on-disk path for a repo directory under the
    workspace root. Does not require the directory to already exist --
    callers cloning a new repo need the target path before it's created."""
    root = ensure_workspace_root(workspace_root)
    resolve_repo_name(repo_name)
    repo_path = (root / repo_name).resolve()
    _require_within(repo_path, root)
    return repo_path


def resolve_file_path(workspace_root: Path, repo_name: str, relative_path: str) -> Path:
    """Resolves a path to a file or directory *inside* an already-cloned
    repo. Rejects absolute paths up front; the resolve() + relative_to()
    check afterward catches '..' traversal and symlink escapes alike,
    since both the string form and the resolved real path have to land
    inside repo_path."""
    repo_path = resolve_repo_path(workspace_root, repo_name)
    if not repo_path.is_dir():
        raise SandboxViolation(f"repo '{repo_name}' does not exist in workspace")

    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise SandboxViolation(f"path must be relative: {relative_path!r}")

    resolved = (repo_path / candidate).resolve()
    _require_within(resolved, repo_path)
    return resolved


def _require_within(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError:
        raise SandboxViolation(f"path '{path}' escapes sandbox root '{root}'") from None
