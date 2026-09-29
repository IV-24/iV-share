"""Shared-secret gates for iV's externally-reachable endpoints (chat,
approvals, the internal sleep-cycle trigger). This is one cheap layer,
not the whole security model — the real access control is
core.permissions/core.approvals underneath it. Whatever network boundary
you put in front of this server (Tailscale, an SSH tunnel, a reverse
proxy with its own auth, ...) is what actually keeps strangers off the
box; this just stops anything that merely routes to it from acting
without also knowing the relevant value.

hmac.compare_digest is used instead of `==` so comparing a secret takes
constant time regardless of where the first mismatching character is —
backend/app/security.py's plain `!=` comparison didn't have this
property. Low real-world severity given the network boundary above, but
free to fix.
"""

import hmac
import os
import threading
import time

from fastapi import Header, HTTPException

# Name of the cookie the approvals UI uses after its first authenticated
# request, so the secret stops travelling in the URL. See
# APPROVAL_SESSION_COOKIE's use in interfaces/api/main.py.
APPROVAL_SESSION_COOKIE = "iv_approvals_session"


def _check(provided: str | None, expected: str | None, *, what: str) -> None:
    if not expected or not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail=f"Invalid or missing {what}.")


def check_secret(provided: str | None) -> None:
    # Read fresh each call rather than caching at import time — keeps
    # this correctly testable regardless of module import order, and
    # means a config reload doesn't need a process restart to take effect.
    _check(provided, os.getenv("API_ACCESS_SECRET"), what="API secret")


def check_internal_secret(provided: str | None) -> None:
    _check(provided, os.getenv("INTERNAL_TRIGGER_SECRET"), what="internal secret")


def require_chat_secret(x_api_secret: str | None = Header(default=None)) -> None:
    check_secret(x_api_secret)


def resolve_approval_secret(query_secret: str | None, cookie_secret: str | None) -> tuple[str, bool]:
    """Accepts the secret from either the query string (the emailed link,
    which has nowhere else to put it) or the session cookie set on that
    first visit. Returns (secret, came_from_query); the caller uses the
    flag to decide whether to set the cookie and redirect to a clean URL.

    Why bother: iV's first self-audit flagged (SEC-QS-01) that a secret in
    a query string is recorded in server access logs, browser history, and
    the Referer header of any outbound link on the page. A cookie is not
    stronger cryptographically — it is the same secret — but it stops the
    value from being written down in three extra places on every
    subsequent request."""
    if query_secret:
        check_secret(query_secret)
        return query_secret, True
    check_secret(cookie_secret)
    return cookie_secret or "", False


class RateLimiter:
    """A plain in-process token bucket, not a distributed one -- correct
    for exactly the deployment iV documents itself as (docs/RUNTIME.md):
    a single uvicorn process, no --workers, run for one owner. It resets
    on every restart and does not coordinate across processes; if that
    ever changes, this needs to move to shared storage (or a real limiter
    library) instead.

    The threat this closes isn't an external attacker -- the shared
    secret and network boundary are what stop those (see this module's
    top-level docstring) -- it's cost amplification from this process's
    own client: one /api/chat call can already fan out to
    MAX_DELEGATIONS_PER_TURN specialist calls (core/agent/delegation.py),
    so an accidental retry loop in a browser tab is a paid-API incident,
    not just a noisy one.
    """

    def __init__(self, *, capacity: float, refill_per_second: float) -> None:
        self._capacity = capacity
        self._refill_per_second = refill_per_second
        self._tokens = capacity
        self._last_check = time.monotonic()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_check
            self._last_check = now
            self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_second)
            if self._tokens < 1:
                return False
            self._tokens -= 1
            return True


# 30 requests/minute steady state with a burst of 10 -- generous for one
# owner's normal back-and-forth, low enough that a runaway loop hits the
# ceiling in seconds rather than emptying a wallet unattended.
CHAT_RATE_LIMIT_CAPACITY = 10
CHAT_RATE_LIMIT_REFILL_PER_SECOND = 30 / 60


def check_rate_limit(limiter: RateLimiter) -> None:
    if not limiter.allow():
        raise HTTPException(
            status_code=429,
            detail="iV is getting messages faster than its rate limit allows. Wait a few seconds and try again.",
        )
