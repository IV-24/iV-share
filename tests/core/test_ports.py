"""The port defaults live in core/configuration/ports.py, but shell and
JavaScript cannot import a Python module, so run.sh, the frontend proxy
route, and package.json repeat the numbers. This is the test that keeps
the copies honest — a default that drifts between the API bind and the
health check produces a startup banner that reports on something else
entirely, and the symptom ("it says healthy but the phone can't reach
it") points nowhere near the cause.
"""

import json
import re
from pathlib import Path

from core.configuration.ports import (
    DEFAULT_API_BASE_URL,
    DEFAULT_API_PORT,
    DEFAULT_FRONTEND_ORIGINS,
    DEFAULT_FRONTEND_PORT,
)

ROOT = Path(__file__).resolve().parent.parent.parent


def test_defaults_avoid_the_commonly_contended_ports():
    """3000 and 8000 are the two most likely to already be taken on a
    laptop; iV runs all day and must not lose that race."""
    assert DEFAULT_API_PORT not in (3000, 8000, 8080, 5000)
    assert DEFAULT_FRONTEND_PORT not in (3000, 8000, 8080, 5000, 5173)
    assert DEFAULT_API_PORT != DEFAULT_FRONTEND_PORT


def test_run_sh_falls_back_to_the_same_ports():
    script = (ROOT / "run.sh").read_text()

    assert f'IV_PORT="${{IV_PORT:-{DEFAULT_API_PORT}}}"' in script
    assert f'FRONTEND_PORT="${{FRONTEND_PORT:-{DEFAULT_FRONTEND_PORT}}}"' in script


def test_frontend_proxy_defaults_to_the_api_port():
    route = (ROOT / "frontend/app/api/iv/[...path]/route.js").read_text()

    assert f"http://127.0.0.1:{DEFAULT_API_PORT}" in route


def test_package_json_dev_and_start_use_the_frontend_port():
    scripts = json.loads((ROOT / "frontend/package.json").read_text())["scripts"]

    for name in ("dev", "start"):
        assert f"${{FRONTEND_PORT:-{DEFAULT_FRONTEND_PORT}}}" in scripts[name], name


def test_env_examples_point_at_the_api_port():
    frontend_env = (ROOT / "frontend/.env.local.example").read_text()

    assert f"IV_API_URL=http://127.0.0.1:{DEFAULT_API_PORT}" in frontend_env


def test_derived_urls_are_built_from_the_port_constants():
    assert DEFAULT_API_BASE_URL.endswith(f":{DEFAULT_API_PORT}")
    assert DEFAULT_FRONTEND_ORIGINS.count(f":{DEFAULT_FRONTEND_PORT}") == 2


def test_settings_default_base_url_follows_the_constant():
    from core.configuration.settings import load_settings

    assert load_settings(env={}).sleep_cycle.base_url == DEFAULT_API_BASE_URL


def test_cors_default_allows_the_frontend_port(monkeypatch):
    from interfaces.api.main import _cors_origins

    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)

    assert f"http://localhost:{DEFAULT_FRONTEND_PORT}" in _cors_origins()


def test_no_stray_localhost_8000_or_3000_left_in_tracked_source():
    """Catches a doc or script that was missed when the defaults moved."""
    stale = re.compile(r"(?:localhost|127\.0\.0\.1):(?:8000|3000)\b")
    checked = [
        "run.py", "run.sh", "README.md", "docs/RUNTIME.md", "docs/SELF_AUDIT.md",
        "docs/DEVELOPMENT_SETUP.md", "backend/.env.example",
        "frontend/.env.local.example", "scripts/macos/run_sleep_cycle.sh",
    ]
    offenders = [name for name in checked if stale.search((ROOT / name).read_text())]

    assert offenders == []
