"""ivctl kills processes, so the test that matters most is the one
asserting it kills the *right* one. `pgrep -f run.py` matches any command
line containing that string — an editor with the file open, the shell
running the script itself — and a stop command that swept those up would
be worse than no stop command.
"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
IVCTL = ROOT / "scripts" / "ivctl"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("pgrep") is None,
    reason="needs bash and pgrep",
)


def _run(*args, env=None):
    return subprocess.run(
        ["bash", str(IVCTL), *args], capture_output=True, text=True, timeout=90,
        cwd=ROOT, env={**os.environ, **(env or {})},
    )


def test_it_is_executable_and_committed_that_way():
    """A ZIP download strips the bit, but a clone must not have to chmod
    the one script you reach for when you cannot get to the machine."""
    listing = subprocess.run(
        ["git", "ls-files", "-s", "scripts/ivctl"], cwd=ROOT, capture_output=True, text=True
    ).stdout
    assert listing.startswith("100755"), listing or "scripts/ivctl is not tracked"


def test_usage_is_shown_for_an_unknown_command():
    result = _run("bogus")

    assert result.returncode == 2
    assert "usage: ivctl" in result.stderr


def test_an_unknown_target_is_rejected_rather_than_ignored():
    """`ivctl restart backend` (instead of `api`) must not silently act on
    both — on a phone you cannot see what it did."""
    result = _run("restart", "backend")

    assert result.returncode == 2
    assert "unknown target" in result.stderr


def test_status_reports_cleanly_when_nothing_is_running():
    result = _run("status", env={"IV_PORT": "59999", "FRONTEND_PORT": "59998"})

    assert result.returncode == 0
    assert "not running" in result.stdout or "running (pid" in result.stdout
    assert "Managed by:" in result.stdout


def test_status_covers_both_halves():
    """The frontend is what you load on a phone. A status that reported
    only the API would say "healthy" while the page was down."""
    result = _run("status", env={"IV_PORT": "59999", "FRONTEND_PORT": "59998"})

    assert "API (port 59999)" in result.stdout
    assert "Frontend (port 59998)" in result.stdout
    assert "this is the page you open on your phone" in result.stdout or "no response on port 59998" in result.stdout


def test_stop_takes_the_frontend_down_before_the_api():
    """Ordering matters: the frontend proxies to the API, so stopping the
    API first leaves a window where the page loads and every request
    502s."""
    source = IVCTL.read_text()
    stop_block = source[source.index("    stop)"):source.index("    restart)")]

    assert stop_block.index("web_stop") < stop_block.index("cmd_stop")


def test_a_lookalike_process_is_never_targeted():
    """The safety property. A process whose command line contains run.py
    but which is not a python server must be invisible to ivctl."""
    decoy = subprocess.Popen(
        ["bash", "-c", 'exec -a "vim /home/user/run.py" sleep 30'],
    )
    try:
        time.sleep(1)
        # pgrep alone sees it...
        seen_by_pgrep = subprocess.run(
            ["pgrep", "-f", r"run\.py"], capture_output=True, text=True
        ).stdout.split()
        assert seen_by_pgrep, "the decoy should be visible to a naive pgrep"

        # ...ivctl must not.
        result = _run("status", env={"IV_PORT": "59999"})
        reported = re.search(r"running \(pid ([\d ]+)\)", result.stdout)
        reported_pids = reported.group(1).split() if reported else []

        assert str(decoy.pid) not in reported_pids
    finally:
        decoy.terminate()
        decoy.wait(timeout=10)


def test_stop_is_a_no_op_when_nothing_is_running():
    """Idempotence matters on a phone: you cannot see whether the last
    command landed, so running it twice must be harmless."""
    result = _run("stop", env={"IV_PORT": "59999"})

    assert result.returncode == 0


def test_it_prefers_sigterm_over_sigkill():
    """SIGTERM lets the FastAPI lifespan close the SQLite handle; SIGKILL
    skips that. The order is the whole reason stop exists rather than
    telling people to use kill -9."""
    source = IVCTL.read_text()
    term_at = source.index("kill -TERM")
    kill_at = source.index("kill -KILL")

    assert term_at < kill_at
    assert "last resort" in source


def test_start_detaches_from_the_calling_shell():
    """Started over SSH, the server has to outlive the session. Assert the
    mechanism is present rather than dropping a real server into the test
    run — setsid/nohup plus a redirect away from the terminal."""
    source = IVCTL.read_text()

    assert "setsid" in source and "nohup" in source
    assert "< /dev/null" in source


def test_status_finds_a_real_server_even_under_a_narrow_terminal(tmp_path):
    """The regression this pins: a live, /health-responding backend
    reported as "not running" over an SSH session with a narrow terminal
    (Termius on a phone, easily). BSD ps (macOS) and GNU ps alike
    truncate `ps -o command=` to $COLUMNS when it isn't wide enough to
    hold the full command line — a real install path like
    /Users/you/dev/agent-iv/backend/venv/bin/python plus
    /Users/you/dev/agent-iv/run.py exceeds a narrow terminal's width
    easily, silently dropping the "run.py" suffix the match depends on.

    Reproduced directly here: a real long-lived process, launched under a
    path deliberately padded past 40 columns, found by ivctl's own
    backend_pids() with COLUMNS forced down to 40.
    """
    # Padded past the COLUMNS=40 this test forces below, so the bug (pre-fix)
    # is reliably triggered rather than accidentally fitting anyway.
    long_dir = tmp_path
    for segment in ("padding-so-the-full-command-line", "clearly-exceeds", "forty-columns-of-terminal-width"):
        long_dir = long_dir / segment
    long_dir.mkdir(parents=True)
    fake_run_py = long_dir / "run.py"
    fake_run_py.write_text("import time\ntime.sleep(30)\n")

    proc = subprocess.Popen([sys.executable, str(fake_run_py)])
    try:
        time.sleep(0.5)
        result = subprocess.run(
            ["bash", "-c", f'source "{IVCTL}"; backend_pids'],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "COLUMNS": "40", "IV_PORT": "59999"},
        )
        found_pids = result.stdout.split()

        assert str(proc.pid) in found_pids, (
            f"backend_pids() missed pid {proc.pid} under COLUMNS=40 "
            f"(found: {found_pids!r}, stderr: {result.stderr!r}) — "
            f"the ps call is truncating the command line again"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_both_pid_finders_use_unlimited_width_ps():
    """Direct pin on the fix itself, independent of the functional test
    above passing for some other reason: both backend_pids() and
    frontend_pids() must call `ps -ww`, not bare `ps -o command=`."""
    source = IVCTL.read_text()

    assert source.count("ps -ww -o command=") == 2, (
        "expected both backend_pids() and frontend_pids() to use "
        "`ps -ww` (unlimited width) — found a bare `ps -o command=` "
        "somewhere, which macOS's BSD ps truncates to terminal width"
    )
