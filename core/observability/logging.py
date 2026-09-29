"""One logging setup for every iV entrypoint (API server, CLI, scheduled
jobs), so a line written by core/agent looks the same as one written by a
provider adapter and both are readable in `.launchd/backend.log`.

Two formats, chosen by IV_LOG_FORMAT:
  - "text" (default): human-readable, timestamped, level + logger name.
  - "json": one JSON object per line, for anything that ships logs
    somewhere structured later.

Both go through SecretRedactingFilter (see core/observability/redaction.py)
— that's the part that matters, and it's attached to the *handler*, not
to individual loggers, so a library logging under its own name can't
bypass it.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

from core.observability.redaction import SecretRedactingFilter, register_all_from_env

DEFAULT_LEVEL = "INFO"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info or record.exc_text:
            # Prefer exc_text: SecretRedactingFilter has already formatted
            # and scrubbed it. Re-deriving from exc_info here would rebuild
            # the raw traceback and undo that.
            payload["exception"] = record.exc_text or self.formatException(record.exc_info)
        for key, value in getattr(record, "iv_extra", {}).items():
            payload[key] = value
        return json.dumps(payload, default=str)


def _formatter() -> logging.Formatter:
    if os.getenv("IV_LOG_FORMAT", "text").lower() == "json":
        return JsonFormatter()
    return logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def configure_logging(level: str | None = None, *, stream=None) -> logging.Logger:
    """Idempotent: replaces iV's own handler rather than stacking a new
    one on every call, so a reload (or a test calling this twice) doesn't
    produce duplicated lines. Registers configured secrets first so the
    very first log line is already covered by redaction.

    `stream` defaults to stdout, which is where a long-running service's
    logs belong (the LaunchAgent captures it to .launchd/backend.log, and
    that is the file iV's own self_read_logs tool reads). A CLI passes
    sys.stderr instead, so that `--json` output on stdout stays
    machine-parseable — a lesson from the first self-audit run, whose JSON
    was unusable because log lines shared the stream with it."""
    register_all_from_env()

    resolved = (level or os.getenv("IV_LOG_LEVEL") or DEFAULT_LEVEL).upper()
    root = logging.getLogger()
    root.setLevel(resolved)

    # Under `python run.py`, uvicorn applies uvicorn_log_config() before
    # the app's lifespan runs, and that config already installs a
    # redacting handler on the root logger. Adding a second one here is
    # how every line ended up printed twice; detect the existing one and
    # leave it alone. The check is for the filter, not for our marker
    # attribute, precisely because the handler dictConfig built is not
    # the one this function creates.
    for existing in list(root.handlers):
        if any(isinstance(f, SecretRedactingFilter) for f in existing.filters):
            return logging.getLogger("iv")
        if getattr(existing, "_iv_handler", False):
            root.removeHandler(existing)

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(_formatter())
    handler.addFilter(SecretRedactingFilter())
    handler._iv_handler = True  # noqa: SLF001 - marker for the idempotency check above
    root.addHandler(handler)

    return logging.getLogger("iv")


def uvicorn_log_config(level: str | None = None) -> dict:
    """uvicorn installs its own handlers on uvicorn/uvicorn.access unless
    given a dictConfig. Routing them through the same redacting handler
    matters more than cosmetics: an access log line carries the full
    request URL, and /approvals/pending?secret=... puts a real secret in
    exactly that position."""
    resolved = (level or os.getenv("IV_LOG_LEVEL") or DEFAULT_LEVEL).upper()
    json_mode = os.getenv("IV_LOG_FORMAT", "text").lower() == "json"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"redact": {"()": "core.observability.redaction.SecretRedactingFilter"}},
        "formatters": {
            "iv": (
                {"()": "core.observability.logging.JsonFormatter"}
                if json_mode
                else {
                    "format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                    "datefmt": "%Y-%m-%dT%H:%M:%S%z",
                }
            )
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "iv",
                "filters": ["redact"],
            }
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": resolved, "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": resolved, "propagate": False},
            "uvicorn.access": {"handlers": ["default"], "level": resolved, "propagate": False},
        },
        "root": {"handlers": ["default"], "level": resolved},
    }


def redact(text: str) -> str:
    """Re-exported so callers scrubbing a string they're about to return
    (not log) don't need to import the redaction module directly."""
    from core.observability.redaction import redact as _redact

    return _redact(text)
