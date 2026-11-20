"""Pair a device names an address a phone can reach, and the phone's own calls to it are taken.

The dashboard is usually open on loopback, and the pairing link echoed the host the owner's own
request came in on: ``http://127.0.0.1:<port>/pair?code=…``, which on a phone is the phone, so the
scan went nowhere. Now a request that came in on loopback is pointed at the network address the
gateway listens on: the one it is bound to, or this machine's own address when it listens on every
interface (``origin.lan_address``, the rule LAN discovery advertises by). What the gateway listens
on is never changed here. A gateway that listens only on this machine gets no link, and the panel
says what to turn on instead of showing a QR code no phone can use.

The gateway's own network address at its port is an allowed origin once it listens beyond
loopback, so the phone's redeem, its changes and its live socket are taken from there. A name that
only resolves to that address (DNS rebinding: ``Origin`` and ``Host`` both the attacker's) still
isn't, because only the address literal is added.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request
from signed_in_sessions import without_sign_in

from personalclaw.dashboard import exposure, origin
from personalclaw.dashboard.handlers import devices as devices_h

PORT = 10000
LAN = "192.0.2.5"


@pytest.fixture(autouse=True)
def _network(monkeypatch):
    """This machine's own network address, and no public URL: nothing read from the real host."""
    monkeypatch.setattr(origin, "address_beyond_loopback", lambda addresses=None: LAN)
    monkeypatch.setattr(exposure, "public_url", lambda cfg=None: "")


def _app(bind_host: str) -> web.Application:
    app = web.Application()
    app["port"] = PORT
    app["bind_host"] = bind_host
    return app


def _request(host: str, bind_host: str):
    return make_mocked_request(
        "POST", "/api/devices/pair/start", headers={"Host": host}, app=_app(bind_host)
    )


@pytest.mark.parametrize(
    ("bind_host", "expected"),
    [
        ("0.0.0.0", f"http://{LAN}:{PORT}"),
        ("192.0.2.7", f"http://192.0.2.7:{PORT}"),
        ("2001:db8::7", f"http://[2001:db8::7]:{PORT}"),
    ],
)
@pytest.mark.parametrize("host", [f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}"])
def test_a_pairing_asked_on_loopback_names_the_network_address_the_gateway_listens_on(
    host, bind_host, expected
):
    """🔴 Red before: the link was ``http://127.0.0.1:10000``, the host the request came in on."""
    assert devices_h._pair_base_url(_request(host, bind_host)) == (expected, "")


@pytest.mark.parametrize("bind_host", ["127.0.0.1", "::1", ""])
def test_a_gateway_that_listens_only_on_this_machine_gets_no_link_and_says_what_to_turn_on(
    bind_host,
):
    """🔴 Red before: a loopback-only gateway handed the phone ``127.0.0.1`` too."""
    base, why = devices_h._pair_base_url(_request(f"127.0.0.1:{PORT}", bind_host))
    assert base == ""
    assert "listens only on this computer" in why
    assert "PERSONALCLAW_BIND_HOST=0.0.0.0" in why


def test_a_gateway_on_every_interface_with_no_network_address_says_so(monkeypatch):
    monkeypatch.setattr(origin, "address_beyond_loopback", lambda addresses=None: "")
    base, why = devices_h._pair_base_url(_request(f"127.0.0.1:{PORT}", "0.0.0.0"))
    assert base == ""
    assert "no network address" in why


def test_a_gateway_on_every_ipv6_interface_names_no_address():
    """CORE N1: a ``::`` listener sets IPV6_V6ONLY (asyncio's ``create_server``), so it refuses
    IPv4 clients, and an IPv4 link would point at nothing: it names none."""
    base, why = devices_h._pair_base_url(_request(f"127.0.0.1:{PORT}", "::"))
    assert base == ""
    assert "no network address" in why


def test_a_pairing_asked_by_another_address_still_names_that_address():
    """The owner reached the dashboard by the machine's name or address: that is the address a
    phone on the same network uses too, so it is kept."""
    request = _request(f"laptop.local:{PORT}", "0.0.0.0")
    assert devices_h._pair_base_url(request) == (f"http://laptop.local:{PORT}", "")


def test_a_public_url_comes_first(monkeypatch):
    monkeypatch.setattr(exposure, "public_url", lambda cfg=None: "https://claw.example/")
    request = _request(f"127.0.0.1:{PORT}", "127.0.0.1")
    assert devices_h._pair_base_url(request) == ("https://claw.example", "")


@pytest.mark.asyncio
async def test_pair_start_answers_the_network_link_or_why_there_is_none():
    """The route as the panel reads it: the link, or no link and the sentence instead."""
    for bind_host, link, why in (
        ("0.0.0.0", f"http://{LAN}:{PORT}/pair?code=", ""),
        ("127.0.0.1", "", "listens only on this computer"),
    ):
        app = without_sign_in(_app(bind_host))
        app["allowed_origins"] = {f"http://127.0.0.1:{PORT}"}
        devices_h.register_device_routes(app)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/devices/pair/start", json={})
            assert resp.status == 200
            data = await resp.json()
        assert data["pairing_url"].startswith(link) if link else data["pairing_url"] == ""
        assert (why in data["pairing_unreachable"]) if why else "pairing_unreachable" not in data


# ── The origin the phone sends ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("bind_host", "lan_origin"),
    [("0.0.0.0", f"http://{LAN}:{PORT}"), ("192.0.2.7", f"http://192.0.2.7:{PORT}")],
)
def test_the_network_address_the_gateway_listens_on_is_an_allowed_origin(bind_host, lan_origin):
    """🔴 Red before: only the machine's hostname was added, so the phone's redeem from the
    address the link names was refused by the origin check."""
    assert lan_origin in origin.build_allowed_origins(PORT, False, bind_host=bind_host)


@pytest.mark.parametrize(
    ("local_only", "bind_host"), [(True, "0.0.0.0"), (False, "127.0.0.1"), (False, "::")]
)
def test_no_network_address_is_added_for_a_gateway_on_loopback(local_only, bind_host):
    allowed = origin.build_allowed_origins(PORT, local_only, bind_host=bind_host)
    assert f"http://{LAN}:{PORT}" not in allowed


def _from(peer: str, headers: dict[str, str], allowed: set[str]):
    transport = MagicMock()
    transport.get_extra_info.side_effect = lambda name, default=None: (
        (peer, 52100) if name == "peername" else default
    )
    app = web.Application()
    app["allowed_origins"] = allowed
    return make_mocked_request("POST", "/pair", headers=headers, transport=transport, app=app)


def test_the_phone_is_taken_from_that_address_and_a_rebinding_name_is_not():
    allowed = origin.build_allowed_origins(PORT, False, bind_host="0.0.0.0")
    phone = {"Origin": f"http://{LAN}:{PORT}", "Host": f"{LAN}:{PORT}"}
    assert origin.check_origin(_from("192.0.2.9", phone, allowed))
    rebound = {"Origin": f"http://evil.example:{PORT}", "Host": f"evil.example:{PORT}"}
    assert not origin.check_origin(_from("192.0.2.9", rebound, allowed))
    other_port = {"Origin": f"http://{LAN}:8080", "Host": f"{LAN}:{PORT}"}
    assert not origin.check_origin(_from("192.0.2.9", other_port, allowed))
