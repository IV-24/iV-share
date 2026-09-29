"""iV's default ports, in one place.

Deliberately not 3000 or 8000. Those are the two most contended ports on
a developer's laptop — 3000 is every Next/React/Rails dev server, 8000 is
Django and `python -m http.server` — and iV is meant to run all day as a
background service. A service that loses a coin flip with whatever else
you started that morning is not a service.

8024/4024 read as "IV-24" and are outside every common framework default,
so nothing else is likely to claim them.

Both remain configurable (IV_PORT, FRONTEND_PORT); these are only the
values used when nothing says otherwise. They live here rather than as
literals at each site because there are five of them across the Python
surface — the API bind, the CORS allowance, the Sleep Cycle's link base,
network discovery, and the startup banner — and a default that drifts
between two of those produces a health check that politely reports on
something else entirely.

run.sh and the frontend's proxy route cannot import this module (shell and
JavaScript), so they repeat the numbers with a comment pointing here.
tests/core/test_ports.py asserts they have not drifted apart.
"""

DEFAULT_API_PORT = 8024
DEFAULT_FRONTEND_PORT = 4024

DEFAULT_API_BASE_URL = f"http://localhost:{DEFAULT_API_PORT}"
DEFAULT_FRONTEND_ORIGINS = (
    f"http://localhost:{DEFAULT_FRONTEND_PORT},http://127.0.0.1:{DEFAULT_FRONTEND_PORT}"
)
