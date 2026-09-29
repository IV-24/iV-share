"""Health and runtime-status reporting.

Two endpoints with deliberately different jobs:

  /health  — is this process serving, and are its dependencies actually
             usable? Cheap enough to poll, and it *checks* rather than
             assumes: persistence is verified with a real round-trip
             against the storage backend, not by testing whether an
             object exists in memory. A health check that only reports
             "the object was constructed" goes green while the disk is
             full.

  /status  — the fuller picture a human wants when something is wrong:
             which providers are provisioned, which tools are registered
             and at what risk tier, which branches are protected, where
             the database lives.

Neither ever returns a credential. build_health()/build_status() report
which secrets are *configured* as booleans, and provider information
comes from ModelRegistry.describe(), which holds no key. That rule is the
reason this file exists at all instead of dumping Settings.
"""

from __future__ import annotations

import logging
import os
import platform
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from core.observability.redaction import SECRET_ENV_VARS
from core.safety.branches import protected_branches

logger = logging.getLogger(__name__)

_PROBE_COLLECTION = "_healthcheck"
_STARTED_AT = time.time()

# Providers that prove nothing about real model access: "null" always
# works because it never leaves the process.
_SYNTHETIC_PROVIDERS = frozenset({"null"})


def uptime_seconds() -> float:
    return round(time.time() - _STARTED_AT, 3)


def check_persistence(storage) -> dict[str, Any]:
    """Round-trips a record through the storage backend. Writes and
    deletes its own probe row in a dedicated collection, so a failing
    disk, a locked database, or a read-only filesystem shows up here
    rather than on the first user message."""
    probe_id = f"probe-{int(time.time() * 1000)}"
    try:
        storage.insert(_PROBE_COLLECTION, {"id": probe_id, "ok": True})
        read_back = storage.get(_PROBE_COLLECTION, probe_id)
        storage.delete(_PROBE_COLLECTION, probe_id)
    except Exception as exc:  # noqa: BLE001 - the point of a health check is to report this
        logger.warning("persistence health probe failed: %s", type(exc).__name__)
        return {"ok": False, "error": type(exc).__name__, "backend": type(storage).__name__}
    if not read_back or read_back.get("id") != probe_id:
        return {"ok": False, "error": "round_trip_mismatch", "backend": type(storage).__name__}
    return {"ok": True, "backend": type(storage).__name__}


def check_model_harness(models) -> dict[str, Any]:
    """Reports whether any *real* provider is provisioned. The null
    provider is registered unconditionally so the rest of the runtime can
    work without keys; counting it as a healthy model harness would make
    this check permanently, uselessly green."""
    try:
        described = models.describe()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__, "providers": []}

    names = [entry["provider"] for entry in described]
    real = [name for name in names if name not in _SYNTHETIC_PROVIDERS]
    broken = [entry["provider"] for entry in described if not entry.get("available", False)]
    return {
        "ok": bool(real) and not broken,
        "providers": names,
        "live_providers": real,
        "unavailable_providers": broken,
        "detail": (
            "no real model provider is configured — set a provider API key"
            if not real
            else ("one or more providers failed discovery" if broken else "ok")
        ),
    }


def configured_secrets() -> dict[str, bool]:
    """Which credentials are set — as booleans, never values."""
    return {name: bool(os.getenv(name)) for name in SECRET_ENV_VARS}


def check_delegation_activity(audit) -> dict[str, Any]:
    """Whether delegation is actually happening, not just configured.

    Delegation is prompt-driven (core/agent/delegation.py's module
    docstring): whether the Coordinator ever calls delegate_to_agent
    depends on its system prompt and whichever model is currently serving
    it, not on anything this codebase can force. A silent collapse to
    "answers everything alone" -- which is exactly what happened before
    this audit, when the fan-out budget never reset -- looks identical to
    "the owner just hasn't asked anything worth delegating" from the
    outside. This surfaces the raw count so that distinction is a number
    to look at instead of a guess.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    try:
        recent = [e for e in audit.since(since) if e.action == "agent.delegate"]
    except Exception as exc:  # noqa: BLE001 - a broken audit log must not break /status
        return {"ok": False, "error": type(exc).__name__}
    return {
        "ok": True,
        "delegations_last_24h": len(recent),
        "by_agent_last_24h": {
            agent: sum(1 for e in recent if e.actor == agent) for agent in sorted({e.actor for e in recent})
        },
    }


def build_health(runtime, *, version: str) -> dict[str, Any]:
    persistence = check_persistence(runtime.storage)
    harness = check_model_harness(runtime.models)
    runtime_ok = all(
        getattr(runtime, attr, None) is not None
        for attr in ("orchestrator", "conversations", "tools", "approvals", "permissions")
    )

    # The model harness is reported but does NOT gate overall health: a
    # server with no provider key is correctly running and can still
    # serve /approvals, the CLI, and a self-audit's deterministic layer.
    # Conflating "degraded" with "down" would have a supervisor restart a
    # process that is behaving exactly as configured.
    status = "ok" if (persistence["ok"] and runtime_ok) else "error"
    if status == "ok" and not harness["ok"]:
        status = "degraded"

    return {
        "status": status,
        "version": version,
        "uptime_seconds": uptime_seconds(),
        "checks": {
            "server": {"ok": True},
            "runtime": {"ok": runtime_ok},
            "persistence": persistence,
            "model_harness": harness,
        },
    }


def build_status(runtime, *, version: str) -> dict[str, Any]:
    tools = runtime.tools.list_tools()
    return {
        "version": version,
        "uptime_seconds": uptime_seconds(),
        "host": {
            "platform": platform.system(),
            "python": platform.python_version(),
        },
        "persistence": {
            **check_persistence(runtime.storage),
            "database_path": os.getenv("IV_LOCAL_DB_PATH", "iv.db"),
        },
        "model_harness": {
            **check_model_harness(runtime.models),
            "provisioned": runtime.models.describe(),
        },
        "tools": {
            "count": len(tools),
            "requiring_approval": sorted(
                t["name"] for t in tools if t["execution_policy"] == "requires_approval"
            ),
            "by_risk": {
                level: sorted(t["name"] for t in tools if t["risk_level"] == level)
                for level in ("critical", "high", "medium", "low")
            },
        },
        "safety": {
            "protected_branches": sorted(protected_branches()),
            "self_inspection": "read-only",
        },
        "secrets_configured": configured_secrets(),
        "approvals_pending": len(runtime.approvals.list_pending()),
        "delegation_activity": check_delegation_activity(runtime.audit),
    }
