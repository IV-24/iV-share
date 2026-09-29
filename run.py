"""iV's server entrypoint: `python run.py`.

Everything configurable here is configuration, with defaults chosen for
the one deployment this exists for — a laptop running iV for its owner,
reachable from their phone over a private VPN:

  IV_HOST   default 0.0.0.0. iV is meant to be reached from another
            device (a phone over Tailscale), and binding only loopback
            would make that impossible. That is a real exposure decision,
            not an accident: the boundary keeping strangers off the port
            is the VPN plus API_ACCESS_SECRET, and it is documented as
            such in docs/RUNTIME.md. Set IV_HOST=127.0.0.1 for a
            laptop-only install and nothing else has to change.
  IV_PORT   default 8024 (see core/configuration/ports.py for why not
            8000 — it is the most contended port on a dev laptop).
  IV_DEV    unset by default. Setting it to 1 enables uvicorn's
            auto-reloader, which is right for editing code and wrong for
            a service: the reloader watches the filesystem, restarts on
            any write, and can leave two processes holding the same
            SQLite file. The previous version of this file had reload
            hardcoded on.

Startup refuses to proceed without API_ACCESS_SECRET rather than booting
a runtime whose /api/chat rejects every request with a 403 — an
unconfigured server that looks healthy and answers nothing is the more
expensive failure.
"""

import os
import secrets
import sys

# Checked before importing anything from core/: 48 modules there use PEP
# 604 unions (`str | None`), which a pre-3.10 interpreter cannot even
# parse. Without this the failure is a TypeError from inside some import
# chain, which reads like a code bug rather than "your Python is too old".
# This file itself is deliberately kept parseable by older interpreters so
# the message can actually be printed.
MINIMUM_PYTHON = (3, 10)
if sys.version_info < MINIMUM_PYTHON:
    sys.stderr.write(
        "\niV needs Python %d.%d or newer; this is %d.%d.%d.\n"
        "macOS 11/12 ship an older python3 as the default.\n"
        "Install from https://www.python.org/downloads/macos/, or run with a\n"
        "newer interpreter directly:  /usr/local/bin/python3.11 run.py\n\n"
        % (MINIMUM_PYTHON + sys.version_info[:3])
    )
    raise SystemExit(1)

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from dotenv import load_dotenv  # noqa: E402 - must follow the sys.path insert above

# .env lives alongside the old backend/ for continuity with existing
# setups — it's just environment variables at this point, not something
# specific to backend/app anymore.
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

# iV's local database lives at the install directory, not wherever this
# process happens to be launched from — matters for a LaunchAgent, a
# cron job, or just running `python /path/to/Agent-iV/run.py` from
# somewhere else. setdefault so an explicit IV_LOCAL_DB_PATH (shell env
# or backend/.env, already loaded above) always wins over this default.
os.environ.setdefault("IV_LOCAL_DB_PATH", os.path.join(ROOT_DIR, "iv.db"))

# Same reasoning for where adapters/workspace's sandboxed git/file/shell
# tools are confined to on disk.
os.environ.setdefault("IV_WORKSPACE_ROOT", os.path.join(ROOT_DIR, "workspace"))


def _fail(message: str) -> None:
    print(f"\niV cannot start: {message}\n", file=sys.stderr)
    sys.exit(1)


def preflight() -> None:
    """Configuration problems that would otherwise surface as a confusing
    runtime symptom, checked once at startup while there is still a
    terminal to read the message on."""
    if not os.getenv("API_ACCESS_SECRET"):
        _fail(
            "API_ACCESS_SECRET is not set. /api/chat and /approvals/* would reject\n"
            "every request. Add it to backend/.env, for example:\n\n"
            f"    echo 'API_ACCESS_SECRET={secrets.token_urlsafe(32)}' >> backend/.env\n"
        )

    db_dir = os.path.dirname(os.path.abspath(os.environ["IV_LOCAL_DB_PATH"])) or "."
    if not os.access(db_dir, os.W_OK):
        _fail(f"the database directory '{db_dir}' is not writable (IV_LOCAL_DB_PATH).")


def main() -> None:
    import uvicorn

    from core.configuration.ports import DEFAULT_API_PORT
    from core.environment.network import describe, discover_access
    from core.observability.logging import configure_logging, uvicorn_log_config

    preflight()
    logger = configure_logging()

    host = os.getenv("IV_HOST", "0.0.0.0")
    port = int(os.getenv("IV_PORT", str(DEFAULT_API_PORT)))
    dev_mode = os.getenv("IV_DEV", "").lower() in ("1", "true", "yes")

    logger.info("iV server binding %s:%d (dev_mode=%s)", host, port, dev_mode)
    print(describe(discover_access(port)), flush=True)

    uvicorn.run(
        "interfaces.api.main:app",
        host=host,
        port=port,
        reload=dev_mode,
        log_config=uvicorn_log_config(),
        # uvicorn installs SIGINT/SIGTERM handlers that run the app's
        # lifespan shutdown (closing the SQLite handle) before exiting.
        # The grace period bounds how long a slow in-flight model call can
        # hold up a restart.
        timeout_graceful_shutdown=int(os.getenv("IV_SHUTDOWN_GRACE_SECONDS", "15")),
    )


if __name__ == "__main__":
    main()
