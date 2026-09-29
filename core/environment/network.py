"""Where iV is reachable from, discovered rather than configured.

The prototype hardcoded one Tailscale IP in two places (the frontend's
next.config.js and its API fallback URL). That address belongs to one
machine on one person's tailnet: it changes when a node is re-registered,
it is wrong for anyone else, and it is a detail of a private network
sitting in a public git history.

So this module asks the machine instead. `tailscale ip -4` and
`tailscale status --json` are read-only queries against a CLI that may
not be installed — every function here degrades to "unknown" rather than
raising, because a laptop with no VPN is a perfectly valid way to run iV
locally.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass, field

from core.configuration.ports import DEFAULT_API_PORT

TAILSCALE_TIMEOUT_SECONDS = 5


@dataclass
class NetworkAccess:
    port: int
    hostname: str
    local_urls: list[str] = field(default_factory=list)
    tailscale_available: bool = False
    tailscale_ipv4: str | None = None
    tailscale_dns_name: str | None = None
    tailscale_urls: list[str] = field(default_factory=list)
    note: str = ""


def _tailscale_binary() -> str | None:
    # The Homebrew/macOS app install puts the CLI outside the default
    # PATH a LaunchAgent inherits, so check the known location too.
    found = shutil.which("tailscale")
    if found:
        return found
    for candidate in ("/Applications/Tailscale.app/Contents/MacOS/Tailscale", "/usr/local/bin/tailscale"):
        if os.path.exists(candidate):
            return candidate
    return None


def _run_tailscale(binary: str, args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            [binary, *args], capture_output=True, text=True,
            timeout=TAILSCALE_TIMEOUT_SECONDS, shell=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def local_ipv4() -> str | None:
    """The address of the interface that would be used to reach the
    outside world, i.e. the one another device on the LAN can reach. No
    packet is actually sent — connecting a UDP socket only sets a route."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # TEST-NET-1: reserved, never routed
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def discover_access(port: int = DEFAULT_API_PORT) -> NetworkAccess:
    hostname = socket.gethostname()
    access = NetworkAccess(port=port, hostname=hostname)

    access.local_urls = [f"http://localhost:{port}", f"http://127.0.0.1:{port}"]
    lan_ip = local_ipv4()
    if lan_ip and not lan_ip.startswith("127."):
        access.local_urls.append(f"http://{lan_ip}:{port}")

    binary = _tailscale_binary()
    if binary is None:
        access.note = "tailscale CLI not found — remote access over the tailnet is unavailable from here."
        return access

    ipv4 = _run_tailscale(binary, ["ip", "-4"])
    if ipv4:
        access.tailscale_available = True
        access.tailscale_ipv4 = ipv4.splitlines()[0].strip()
        access.tailscale_urls.append(f"http://{access.tailscale_ipv4}:{port}")

    status_json = _run_tailscale(binary, ["status", "--json"])
    if status_json:
        try:
            status = json.loads(status_json)
            dns_name = (status.get("Self") or {}).get("DNSName", "").rstrip(".")
            if dns_name:
                access.tailscale_available = True
                access.tailscale_dns_name = dns_name
                access.tailscale_urls.append(f"http://{dns_name}:{port}")
        except (json.JSONDecodeError, AttributeError):
            pass

    if not access.tailscale_available:
        access.note = "tailscale is installed but not connected (run `tailscale up`)."
    return access


def describe(access: NetworkAccess) -> str:
    lines = [f"Local:     {', '.join(access.local_urls)}"]
    if access.tailscale_urls:
        lines.append(f"Tailscale: {', '.join(access.tailscale_urls)}")
    else:
        lines.append(f"Tailscale: unavailable — {access.note}")
    return "\n".join(lines)
