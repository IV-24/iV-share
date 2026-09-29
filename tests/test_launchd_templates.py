"""The frontend LaunchAgent failed with `command not found: npm`, on
repeat, every 10 seconds. The cause: the plist ran `/bin/zsh -l -c "npm
run dev"`, and a NON-INTERACTIVE login zsh sources .zprofile and .zlogin
but not .zshrc — which is where nvm, volta, and most node installers put
node on PATH. It worked by hand and failed under launchd, which is the
worst shape of bug to debug remotely.

These tests pin the fix: npm is resolved at install time, in the user's
real shell, and baked into the plist as an absolute path.
"""

import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "scripts" / "macos"
INSTALL = TEMPLATES / "install.sh"


def _rendered(name: str) -> dict:
    raw = (TEMPLATES / f"com.agentiv.{name}.plist.template").read_bytes()
    raw = (raw.replace(b"__REPO_DIR__", b"/Users/example/agent-iv")
              .replace(b"__NPM_BIN__", b"/opt/homebrew/bin/npm")
              .replace(b"__NODE_DIR__", b"/opt/homebrew/bin"))
    return plistlib.loads(raw)


def test_both_templates_are_valid_plists_once_rendered():
    for name in ("backend", "frontend"):
        assert _rendered(name)["Label"] == f"com.agentiv.{name}"


def test_the_frontend_agent_does_not_depend_on_a_login_shell():
    """The regression. A login shell here means depending on .zshrc, which
    launchd's non-interactive shell never reads. Checked against the
    rendered values rather than the file text — the comment explaining
    this fix necessarily mentions zsh."""
    argv = _rendered("frontend")["ProgramArguments"]

    assert not any("zsh" in arg or "bash" in arg or "/sh" in arg for arg in argv), argv
    assert "-l" not in argv


def test_the_frontend_agent_invokes_npm_by_absolute_path():
    argv = _rendered("frontend")["ProgramArguments"]

    assert argv[0] == "/opt/homebrew/bin/npm"
    assert argv[1:] == ["run", "dev"]


def test_the_frontend_agent_puts_nodes_directory_on_path():
    """npm still has to find node once it starts, and launchd's default
    PATH is minimal."""
    path = _rendered("frontend")["EnvironmentVariables"]["PATH"]

    assert path.startswith("/opt/homebrew/bin:")
    assert "/usr/bin" in path


def test_the_backend_agent_invokes_the_venvs_own_python():
    """Same class of problem, already solved for the backend: never rely
    on whatever `python3` launchd happens to resolve."""
    argv = _rendered("backend")["ProgramArguments"]

    assert argv[0].endswith("/backend/venv/bin/python")
    assert argv[1].endswith("/run.py")


def test_install_resolves_npm_and_fails_loudly_without_it():
    source = INSTALL.read_text()

    assert "command -v npm" in source
    assert "__NPM_BIN__" in source and "__NODE_DIR__" in source
    assert "npm not found on PATH" in source


def test_both_agents_restart_themselves_but_not_in_a_tight_loop():
    for name in ("backend", "frontend"):
        rendered = _rendered(name)
        assert rendered["KeepAlive"] is True
        assert rendered["ThrottleInterval"] >= 10, name


def test_logs_go_to_predictable_paths_ivctl_knows_about():
    ivctl = (ROOT / "scripts" / "ivctl").read_text()
    for name in ("backend", "frontend"):
        rendered = _rendered(name)
        for key in ("StandardOutPath", "StandardErrorPath"):
            filename = Path(rendered[key]).name
            assert filename in ivctl, f"{filename} is written but ivctl never reads it"
