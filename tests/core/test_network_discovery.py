"""Access URLs are discovered, never hardcoded — the previous frontend
carried one person's Tailscale IP in two files. These tests pin the
degradation behavior, since 'tailscale is not installed' is a normal
state for a laptop-only install and must not raise."""

import json

from core.environment import network
from core.environment.network import NetworkAccess, describe, discover_access


def test_discovery_without_tailscale_reports_local_urls_only(monkeypatch):
    monkeypatch.setattr(network, "_tailscale_binary", lambda: None)

    access = discover_access(port=8000)

    assert any("localhost:8000" in url for url in access.local_urls)
    assert access.tailscale_available is False
    assert access.tailscale_urls == []
    assert "not found" in access.note


def test_discovery_uses_the_tailscale_cli_when_present(monkeypatch):
    monkeypatch.setattr(network, "_tailscale_binary", lambda: "/usr/bin/tailscale")
    monkeypatch.setattr(network, "_run_tailscale", lambda binary, args: (
        "100.101.102.103" if args[:2] == ["ip", "-4"]
        else json.dumps({"Self": {"DNSName": "laptop.tail1234.ts.net."}})
    ))

    access = discover_access(port=3000)

    assert access.tailscale_available is True
    assert access.tailscale_ipv4 == "100.101.102.103"
    assert "http://laptop.tail1234.ts.net:3000" in access.tailscale_urls


def test_installed_but_disconnected_tailscale_is_reported_as_such(monkeypatch):
    monkeypatch.setattr(network, "_tailscale_binary", lambda: "/usr/bin/tailscale")
    monkeypatch.setattr(network, "_run_tailscale", lambda binary, args: None)

    access = discover_access()

    assert access.tailscale_available is False
    assert "tailscale up" in access.note


def test_describe_is_readable_in_both_states():
    with_vpn = NetworkAccess(
        port=8000, hostname="h", local_urls=["http://localhost:8000"],
        tailscale_urls=["http://laptop.ts.net:8000"], tailscale_available=True,
    )
    without = NetworkAccess(port=8000, hostname="h", local_urls=["http://localhost:8000"], note="not installed")

    assert "laptop.ts.net" in describe(with_vpn)
    assert "unavailable" in describe(without)
