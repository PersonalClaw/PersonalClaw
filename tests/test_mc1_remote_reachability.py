"""MOBILE-COMPANION S1 — remote-access reachability: tailnet detection + doctor probe.

The contract: a reader on cell data reaches their dashboard via a tailnet, ``doctor``
detects the tailscale interface and points at the phone-ready tokenized URL, and warns
when the bind host exposes the dashboard beyond loopback without auth. These tests pin:

* the tailnet-detection helper's 100.64.0.0/10 CGNAT boundaries (the authoritative
  "on a tailnet now" signal), injectable so no real network is touched;
* the doctor ``remote.reachability`` probe's three outcomes (tailnet ok / exposed-without-
  auth warning / local-only ok) — and, critically, that the probe never mints or prints a
  live token.
"""

from __future__ import annotations

import pytest

from personalclaw.dashboard import origin
from personalclaw.dashboard.origin import auth_is_off, tailnet_ip, tailscale_cli_present
from personalclaw.resilience import doctor
from personalclaw.resilience.doctor import DoctorContext

# ── helper: tailnet CGNAT membership (100.64.0.0/10) ─────────────────────────


def test_tailnet_ip_detects_injected_tailnet_address():
    assert tailnet_ip(["100.101.102.103"]) == "100.101.102.103"


def test_tailnet_ip_empty_when_absent():
    assert tailnet_ip(["192.168.1.5", "127.0.0.1", "::1"]) == ""


def test_tailnet_ip_picks_the_tailnet_addr_from_a_mixed_list():
    addrs = ["127.0.0.1", "192.168.1.5", "100.64.10.20", "fe80::1"]
    assert tailnet_ip(addrs) == "100.64.10.20"


@pytest.mark.parametrize(
    "addr,expected",
    [
        ("100.64.0.0", True),  # first address in the /10
        ("100.63.255.255", False),  # one below the range
        ("100.127.255.255", True),  # last address in the /10
        ("100.128.0.0", False),  # one above the range
    ],
)
def test_tailnet_cgnat_boundaries(addr: str, expected: bool):
    """100.64.0.0/10 spans 100.64.0.0 – 100.127.255.255. Off-by-one at either edge
    would mis-classify a public 100.x host as a tailnet peer, or vice versa."""
    assert bool(tailnet_ip([addr])) is expected


def test_tailnet_ip_ignores_garbage_and_zone_ids():
    assert tailnet_ip(["not-an-ip", "100.65.1.1%en0"]) == "100.65.1.1"


def test_tailscale_cli_present_is_patchable(monkeypatch):
    monkeypatch.setattr(origin.shutil, "which", lambda name: None)
    assert tailscale_cli_present() is False
    monkeypatch.setattr(origin.shutil, "which", lambda name: "/usr/bin/tailscale")
    assert tailscale_cli_present() is True


# ── helper: auth-off detection ───────────────────────────────────────────────


def test_auth_is_off_true_for_none_mode(monkeypatch):
    monkeypatch.delenv("PERSONALCLAW_DEV_NO_AUTH", raising=False)
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")
    assert auth_is_off() is True


def test_auth_is_off_false_for_default_local_token(monkeypatch):
    monkeypatch.delenv("PERSONALCLAW_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("PERSONALCLAW_AUTH_MODE", raising=False)
    assert auth_is_off() is False


def test_auth_is_off_true_for_dev_no_auth_flag(monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_DEV_NO_AUTH", "1")
    monkeypatch.delenv("PERSONALCLAW_AUTH_MODE", raising=False)
    assert auth_is_off() is True


# ── the doctor probe: three outcomes ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_probe_tailnet_present_is_ok_and_carries_phone_url(monkeypatch):
    """tailnet fixture: injected 100.x address → ok, evidence carries the phone base
    URL, detail names the tailnet."""
    monkeypatch.setattr(origin, "tailnet_ip", lambda addresses=None: "100.101.102.103")
    monkeypatch.setattr(origin, "resolve_bind_host", lambda auth_cfg=None: "127.0.0.1")
    monkeypatch.setattr(origin, "auth_is_off", lambda auth_cfg=None: False)
    monkeypatch.setattr(origin, "tailscale_cli_present", lambda: True)

    res = await doctor._probe_remote_reachability(DoctorContext(port=10000))

    assert res.ok is True
    assert res.evidence["phone_url"] == "http://100.101.102.103:10000"
    assert res.evidence["tailnet_ip"] == "100.101.102.103"
    assert "tailnet" in res.detail
    assert "100.101.102.103" in res.detail


@pytest.mark.asyncio
async def test_probe_does_NOT_mint_or_print_a_live_token(monkeypatch):
    """A read-only health probe must not generate a secret. It points at
    `personalclaw token` for the signed-in link; the evidence is only the base URL
    plus that hint — never a token value or a `?token=` query string."""
    monkeypatch.setattr(origin, "tailnet_ip", lambda addresses=None: "100.101.102.103")
    monkeypatch.setattr(origin, "resolve_bind_host", lambda auth_cfg=None: "127.0.0.1")
    monkeypatch.setattr(origin, "auth_is_off", lambda auth_cfg=None: False)
    monkeypatch.setattr(origin, "tailscale_cli_present", lambda: False)

    res = await doctor._probe_remote_reachability(DoctorContext(port=10000))

    assert "?token=" not in res.detail
    assert "token=" not in res.evidence.get("phone_url", "")
    assert res.evidence.get("token_hint") == "personalclaw token"
    # No evidence value looks like a minted token URL.
    for value in res.evidence.values():
        assert "token=" not in str(value)


@pytest.mark.asyncio
async def test_probe_misconfig_non_loopback_no_auth_warns(monkeypatch):
    """misconfig fixture: non-loopback bind + auth off → not ok, detail references
    the exposure and points at remote-access.md."""
    monkeypatch.setattr(origin, "tailnet_ip", lambda addresses=None: "")
    monkeypatch.setattr(origin, "resolve_bind_host", lambda auth_cfg=None: "0.0.0.0")
    monkeypatch.setattr(origin, "auth_is_off", lambda auth_cfg=None: True)
    monkeypatch.setattr(origin, "tailscale_cli_present", lambda: False)

    res = await doctor._probe_remote_reachability(DoctorContext(port=10000))

    assert res.ok is False
    assert "beyond loopback" in res.detail
    assert "auth OFF" in res.detail
    assert res.evidence["guide"] == "docs/guides/remote-access.md"


@pytest.mark.asyncio
async def test_probe_local_no_tailnet_is_ok_local_only(monkeypatch):
    """local-no-tailnet fixture: loopback bind, no tailnet → ok with a 'local-only'
    note (informational, not an issue)."""
    monkeypatch.setattr(origin, "tailnet_ip", lambda addresses=None: "")
    monkeypatch.setattr(origin, "resolve_bind_host", lambda auth_cfg=None: "127.0.0.1")
    monkeypatch.setattr(origin, "auth_is_off", lambda auth_cfg=None: False)
    monkeypatch.setattr(origin, "tailscale_cli_present", lambda: False)

    res = await doctor._probe_remote_reachability(DoctorContext(port=10000))

    assert res.ok is True
    assert "local-only" in res.detail


@pytest.mark.asyncio
async def test_probe_non_loopback_with_auth_on_is_not_a_warning(monkeypatch):
    """A non-loopback bind is fine as long as auth is ON (the default local_token
    mode). Only auth-OFF exposure is the misconfiguration — token auth is the real
    boundary, so this must NOT fail."""
    monkeypatch.setattr(origin, "tailnet_ip", lambda addresses=None: "")
    monkeypatch.setattr(origin, "resolve_bind_host", lambda auth_cfg=None: "0.0.0.0")
    monkeypatch.setattr(origin, "auth_is_off", lambda auth_cfg=None: False)
    monkeypatch.setattr(origin, "tailscale_cli_present", lambda: False)

    res = await doctor._probe_remote_reachability(DoctorContext(port=10000))

    assert res.ok is True


@pytest.mark.asyncio
async def test_probe_is_registered_as_a_CAPABILITY_probe():
    """A missing tailnet is not a core failure — the probe lives on the CAPABILITY
    tier so it never gates the readiness ladder, and it groups under a 'remote' card."""
    ids = {p.id: p for p in doctor.all_probes()}
    assert "remote.reachability" in ids
    probe = ids["remote.reachability"]
    assert probe.tier is doctor.Tier.CAPABILITY
    assert probe.capability == "remote"
