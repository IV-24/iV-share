"""The frontend proxy resolves the API secret without needing it copied
into frontend/.env.local. That lookup is easy to break silently — it
depends on filesystem layout rather than on anything the Python suite
otherwise touches — and when it breaks the only symptom is a 403 during
manual use. So it gets driven for real: the actual route module, executed
by node, against fixture trees.

Skipped when node is unavailable; the Python runtime cannot exercise a
JavaScript module, and a fake would test the fake.
"""

import json
import shutil
import subprocess
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
ROUTE = ROOT / "frontend/app/api/iv/[...path]/route.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

PROBE = """
const mod = await import("./route.js");
const req = new Request("http://localhost/api/iv/chat", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ message: "probe" }),
});
const res = await mod.POST(req, { params: Promise.resolve({ path: ["chat"] }) });
console.log(JSON.stringify({ status: res.status, body: await res.json() }));
"""


class _RejectingHandler(BaseHTTPRequestHandler):
    """Stands in for the API with a different secret: always 403. Using a
    stub rather than the real server keeps these tests independent of
    whatever happens to be listening on the developer's machine — an
    earlier version of this file passed or failed depending on that."""

    def do_POST(self):  # noqa: N802 - http.server API
        self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        body = b'{"detail":"Invalid or missing API secret."}'
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def rejecting_api():
    httpd = HTTPServer(("127.0.0.1", 0), _RejectingHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


def _run_probe(directory: Path, api_url: str) -> dict:
    (directory / "route.js").write_text(ROUTE.read_text())
    (directory / "probe.mjs").write_text(textwrap.dedent(PROBE))
    result = subprocess.run(
        ["node", "probe.mjs"], cwd=directory, capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "IV_API_URL": api_url},
    )
    assert result.stdout, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_missing_secret_reports_every_path_it_checked(tmp_path, rejecting_api):
    """The failure that prompted this: a 403 saying a secret was missing,
    with no way to tell where the app had looked for it."""
    isolated = tmp_path / "nested" / "app" / "api" / "iv"
    isolated.mkdir(parents=True)

    result = _run_probe(isolated, rejecting_api)

    assert result["status"] == 403
    assert "could not find an API secret" in result["body"]["detail"]
    assert result["body"]["looked_in"], "the diagnostic must name the paths it tried"
    assert all("backend/.env" in entry for entry in result["body"]["looked_in"])


def test_secret_is_found_from_the_module_location_not_just_cwd(tmp_path, rejecting_api):
    """Resolution must not depend on where the server was launched from.
    Here backend/.env sits above the app tree and the process runs from
    inside it — the distinguishing signal is which 403 comes back: the
    "found but rejected" one names the file it read."""
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / ".env").write_text("GROQ_API_KEY=x\nAPI_ACCESS_SECRET=the-real-secret\n")
    app_dir = repo / "frontend" / "app" / "api" / "iv"
    app_dir.mkdir(parents=True)

    result = _run_probe(app_dir, rejecting_api)

    assert result["status"] == 403
    assert "does not match" in result["body"]["detail"]
    assert str(repo / "backend" / ".env") in result["body"]["detail"]


def test_quoted_and_exported_secret_lines_are_parsed(tmp_path, rejecting_api):
    """`export API_ACCESS_SECRET="value"` is a shape people really write
    in a .env; failing to parse it would look identical to a missing key."""
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / ".env").write_text('export API_ACCESS_SECRET="quoted-secret-value"\n')
    app_dir = repo / "frontend" / "app" / "api" / "iv"
    app_dir.mkdir(parents=True)

    result = _run_probe(app_dir, rejecting_api)

    # Parsed successfully = it reports the file as the source, rather
    # than falling through to "could not find an API secret".
    assert "does not match" in result["body"]["detail"], result["body"]


def test_an_empty_secret_is_treated_as_missing(tmp_path, rejecting_api):
    """`API_ACCESS_SECRET=` with no value must not be sent as a valid
    empty credential — that would produce the confusing 403 again."""
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / ".env").write_text("API_ACCESS_SECRET=\n")
    app_dir = repo / "frontend" / "app" / "api" / "iv"
    app_dir.mkdir(parents=True)

    result = _run_probe(app_dir, rejecting_api)

    assert result["status"] == 403
    assert any("empty" in entry for entry in result["body"]["looked_in"])


def test_a_duplicated_key_resolves_the_same_way_python_dotenv_does(tmp_path, rejecting_api):
    """`cp .env.example .env` leaves an empty API_ACCESS_SECRET= near the
    top; appending the real value below it is what the setup instructions
    used to tell people to do. python-dotenv takes the last occurrence,
    so the backend worked — and this reader took the first, so the
    frontend reported no secret at all. Two components must not disagree
    about the same file."""
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / ".env").write_text(
        "API_ACCESS_SECRET=\nGROQ_API_KEY=abc\nAPI_ACCESS_SECRET=the-real-one\n"
    )
    app_dir = repo / "frontend" / "app" / "api" / "iv"
    app_dir.mkdir(parents=True)

    result = _run_probe(app_dir, rejecting_api)

    # Reached the upstream with a secret in hand, rather than giving up.
    assert "does not match" in result["body"]["detail"], result["body"]


def test_a_trailing_empty_duplicate_resolves_to_empty_like_dotenv(tmp_path, rejecting_api):
    """The inverse case. dotenv resolves a real value followed by an empty
    re-declaration to empty, so this reader must too: preferring the
    earlier non-empty value would put the two back out of step in the
    other direction. (The backend would refuse to start on such a file,
    which is the correct outcome — the point is that both readers see the
    same thing.)"""
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / ".env").write_text("API_ACCESS_SECRET=earlier-value\nAPI_ACCESS_SECRET=\n")
    app_dir = repo / "frontend" / "app" / "api" / "iv"
    app_dir.mkdir(parents=True)

    result = _run_probe(app_dir, rejecting_api)

    assert "could not find an API secret" in result["body"]["detail"], result["body"]
    assert any("empty" in entry for entry in result["body"]["looked_in"])
