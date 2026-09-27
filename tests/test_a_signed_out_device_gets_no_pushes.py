"""A device that is signed out gets no more pushes.

A phone or browser that turns push on registers a destination for the gateway's pings: a web-push
subscription, or a vendor push token for the push relay. Both are stored by a push id the browser
mints, and neither remembered which sign-in registered it. So signing a device out from Settings →
Devices, or signing out all the others, ended its session and left its push destination behind:
the phone the owner had just signed out kept being woken for every approval.

Each destination now records the sign-in that registered it (its row in Settings → Devices), and
every way a sign-in ends early ends its pushes with it (``token_auth.sign_out``). A browser that
signs in again with a newer link is the one exception: it is the same browser, so its pushes move
to its new sign-in. A sign-in that ends by running out gets no more pushes either, and a push
destination from before this record existed gets none: nothing says it belongs to a device that is
still signed in.

Driven through the real middleware and routes, with one cookie jar per device, as
``test_signed_in_devices`` drives them. Only the push services are fakes, at the HTTP boundary.
"""

from __future__ import annotations

import json
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from personalclaw import push
from personalclaw.auth import credentials as creds
from personalclaw.auth import pairing
from personalclaw.dashboard import session_store as ss
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.handlers import core as core_h
from personalclaw.dashboard.handlers import devices as devices_h
from personalclaw.dashboard.handlers.push import register_push_routes

PORT = 10000
SECRET = "synthetic-local-secret"
CHROME = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)
FIREFOX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.0; rv:140.0) Gecko/20100101 Firefox/140.0"
IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(pairing, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(creds, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr(ss, "LAST_SEEN_THROTTLE_SECS", 0.0)
    (tmp_path / "config.json").write_text(json.dumps({"auth": {}}), encoding="utf-8")
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()
    push.push_init()
    yield tmp_path
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()


@pytest.fixture()
def woken(monkeypatch) -> list[str]:
    """Where each ping went: the endpoint or relay token it was addressed to."""
    calls: list[str] = []

    def fake_post(url: str, body: bytes, headers: dict[str, str]) -> int:
        calls.append(json.loads(body)["token"] if url == RELAY else url)
        return 201

    monkeypatch.setattr(push, "_post", fake_post)
    return calls


RELAY = "https://relay.example/push"


def _backend(tmp_path, backend: str) -> None:
    cfg = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    cfg["mobile"] = {"push_backend": backend, "relay_url": RELAY}
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")


async def _status(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _page(request: web.Request) -> web.Response:
    return web.Response(text="<html>the dashboard</html>", content_type="text/html")


def _gateway_app() -> web.Application:
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    app["port"] = PORT
    app["local_secret"] = SECRET
    app["allowed_origins"] = {f"http://localhost:{PORT}"}
    app.router.add_get("/", _page)
    app.router.add_get("/api/status", _status)
    app.router.add_get("/api/token/local", core_h.api_token_local)
    app.router.add_post("/api/logout", core_h.api_logout)
    app.router.add_post("/api/auth/logout", auth_h.api_auth_logout)
    devices_h.register_device_routes(app)
    register_push_routes(app)
    return app


def _browser_keys() -> dict[str, str]:
    """A browser-shaped subscription keypair: the encryptor needs a real P-256 point."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    public = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(encoding=Encoding.X962, format=PublicFormat.UncompressedPoint)
    )
    return {"p256dh": push._b64url(public), "auth": push._b64url(b"0123456789abcdef")}


class Device:
    def __init__(self, server: TestServer, user_agent: str, name: str) -> None:
        self.server = server
        self.name = name
        self.http = aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True), headers={"User-Agent": user_agent}
        )

    @property
    def endpoint(self) -> str:
        return f"https://push.example/send/{self.name}"

    async def get(self, path: str) -> aiohttp.ClientResponse:
        resp = await self.http.get(self.server.make_url(path), allow_redirects=False)
        await resp.read()
        return resp

    async def post(self, path: str, body: Any = None) -> aiohttp.ClientResponse:
        resp = await self.http.post(self.server.make_url(path), json=body or {})
        await resp.read()
        return resp

    async def turn_on_push(self) -> None:
        """The companion page's Turn on push: this browser's subscription, under its push id."""
        resp = await self.post(
            "/api/push/subscribe",
            {
                "device_id": f"web-{self.name}",
                "subscription": {"endpoint": self.endpoint, "keys": _browser_keys()},
            },
        )
        assert resp.status == 200, await resp.text()

    async def register_with_the_relay(self) -> None:
        """The store app's native registration, under this device's push id."""
        resp = await self.post(
            "/api/push/relay-register",
            {"device_id": f"app-{self.name}", "platform": "ios", "token": f"apns-{self.name}"},
        )
        assert resp.status == 200, await resp.text()


class Gateway:
    def __init__(self) -> None:
        self.server = TestServer(_gateway_app())
        self.devices: list[Device] = []

    async def __aenter__(self) -> "Gateway":
        await self.server.start_server()
        return self

    async def __aexit__(self, *exc: object) -> None:
        for device in self.devices:
            await device.http.close()
        await self.server.close()

    def device(self, user_agent: str, name: str) -> Device:
        device = Device(self.server, user_agent, name)
        self.devices.append(device)
        return device

    async def mint_token(self) -> str:
        async with aiohttp.ClientSession() as http:
            resp = await http.get(
                self.server.make_url("/api/token/local"), headers={"X-Local-Secret": SECRET}
            )
            assert resp.status == 200, await resp.text()
            return (await resp.json())["token"]

    async def sign_in_with_link(self, device: Device) -> None:
        resp = await device.get(f"/?token={await self.mint_token()}")
        assert resp.status == 200, await resp.text()

    async def pair(self, phone: Device, *, using: Device) -> str:
        start = await using.post("/api/devices/pair/start")
        assert start.status == 200, await start.text()
        done = await phone.post(
            "/api/devices/pair/complete", {"code": (await start.json())["code"]}
        )
        assert done.status == 200, await done.text()
        return (await done.json())["device_id"]


def _pinged() -> None:
    push.deliver("approval", "ap-1")


# ── signing a device out ends its pushes ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_phone_signed_out_from_settings_is_not_woken_again(woken) -> None:
    async with Gateway() as gw:
        laptop = gw.device(CHROME, "laptop")
        await gw.sign_in_with_link(laptop)
        phone = gw.device(IPHONE, "phone")
        phone_id = await gw.pair(phone, using=laptop)
        await phone.turn_on_push()
        await laptop.turn_on_push()

        resp = await laptop.post(f"/api/devices/{phone_id}/revoke")
        assert resp.status == 200, await resp.text()

        assert "web-phone" not in push.load_subscriptions(), "the signed-out phone's push stayed"
        _pinged()
        assert woken == [laptop.endpoint], "the signed-out phone was woken"


@pytest.mark.asyncio
async def test_the_audit_log_lists_an_ended_push_under_succeeded_beside_its_sign_out() -> None:
    """Settings → Audit log's Succeeded filter returns the push a sign-out ended, as it returns the
    sign-out's own ``device_revoked`` row. The row said ``revoked``, a word no filter family holds,
    so no filter returned it and it read neutral beside the sign-out it belongs to. It names the
    push id and the sign-in, never the endpoint: that is a capability to wake the phone."""
    from personalclaw.sel import AUDIT_OUTCOME_FAMILIES, sel

    async with Gateway() as gw:
        laptop = gw.device(CHROME, "laptop")
        await gw.sign_in_with_link(laptop)
        phone = gw.device(IPHONE, "phone")
        phone_id = await gw.pair(phone, using=laptop)
        await phone.turn_on_push()

        resp = await laptop.post(f"/api/devices/{phone_id}/revoke")
        assert resp.status == 200, await resp.text()

    succeeded = next(f for f in AUDIT_OUTCOME_FAMILIES if f["key"] == "ok")
    page = sel().audit_page(filters={"outcome": ",".join(succeeded["values"])}, limit=200)
    rows = {row["operation"]: row for row in page["events"]}
    assert "device_revoked" in rows, "the floor: Succeeded returns the sign-out itself"
    ended = rows.get("push_revoked:webpush")
    assert ended is not None, "Succeeded left out the push the sign-out ended"
    assert ended["outcome_tone"] == rows["device_revoked"]["outcome_tone"] == "success"
    assert ended["resources"] == (
        f"device=web-phone sign_in={phone_id} reason=signed_out_elsewhere"
    )
    assert "push.example" not in json.dumps(ended)


@pytest.mark.asyncio
async def test_signing_out_all_other_devices_ends_their_pushes_and_keeps_yours(woken) -> None:
    async with Gateway() as gw:
        laptop = gw.device(CHROME, "laptop")
        await gw.sign_in_with_link(laptop)
        phone = gw.device(IPHONE, "phone")
        await gw.pair(phone, using=laptop)
        other = gw.device(FIREFOX, "other")
        await gw.sign_in_with_link(other)
        for device in (laptop, phone, other):
            await device.turn_on_push()

        resp = await laptop.post("/api/devices/revoke-others", {"confirm": True})
        assert resp.status == 200, await resp.text()

        assert sorted(push.load_subscriptions()) == ["web-laptop"]
        _pinged()
        assert woken == [laptop.endpoint], "a device signed out with the others was woken"


@pytest.mark.asyncio
async def test_a_signed_out_phone_s_relay_token_is_revoked_too(tmp_path, woken) -> None:
    _backend(tmp_path, "relay")
    async with Gateway() as gw:
        laptop = gw.device(CHROME, "laptop")
        await gw.sign_in_with_link(laptop)
        phone = gw.device(IPHONE, "phone")
        phone_id = await gw.pair(phone, using=laptop)
        await phone.register_with_the_relay()
        # A relay token is a capability to wake the phone, kept like the subscriptions are.
        assert (tmp_path / "push_relay_tokens.json").stat().st_mode & 0o777 == 0o600

        resp = await laptop.post(f"/api/devices/{phone_id}/revoke")
        assert resp.status == 200, await resp.text()

        assert push.load_relay_tokens() == {}, "the signed-out phone's relay token stayed"
        _pinged()
        assert woken == []


@pytest.mark.asyncio
async def test_a_device_that_signs_itself_out_is_not_woken_again(woken) -> None:
    async with Gateway() as gw:
        laptop = gw.device(CHROME, "laptop")
        await gw.sign_in_with_link(laptop)
        phone = gw.device(IPHONE, "phone")
        await gw.pair(phone, using=laptop)
        await phone.turn_on_push()

        assert (await phone.post("/api/auth/logout")).status == 200

        assert "web-phone" not in push.load_subscriptions()
        _pinged()
        assert woken == []


@pytest.mark.asyncio
async def test_signing_out_everywhere_ends_every_device_s_pushes(woken) -> None:
    """`personalclaw logout`: every sign-in ends, and so does every push they registered."""
    async with Gateway() as gw:
        laptop = gw.device(CHROME, "laptop")
        await gw.sign_in_with_link(laptop)
        phone = gw.device(IPHONE, "phone")
        await gw.pair(phone, using=laptop)
        await laptop.turn_on_push()
        await phone.turn_on_push()

        async with aiohttp.ClientSession() as cli:
            resp = await cli.post(
                gw.server.make_url("/api/logout"), headers={"X-Local-Secret": SECRET}
            )
            assert resp.status == 200, await resp.text()

        assert push.load_subscriptions() == {}
        _pinged()
        assert woken == []


# ── what keeps its pushes, and what never gets one ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_browser_that_opens_a_newer_link_keeps_its_pushes(woken) -> None:
    """The gateway opens a fresh link in the default browser at every start, and the browser
    swaps its sign-in for it. It is the same browser: its pushes follow it. The floor for every
    case above: a device still signed in is woken."""
    async with Gateway() as gw:
        laptop = gw.device(CHROME, "laptop")
        await gw.sign_in_with_link(laptop)
        await laptop.turn_on_push()

        await gw.sign_in_with_link(laptop)  # a restart's startup link, in the same browser

        _pinged()
        assert woken == [laptop.endpoint], "the browser lost its pushes to its own new sign-in"


@pytest.mark.asyncio
async def test_a_sign_in_that_ran_out_is_not_woken(woken, monkeypatch) -> None:
    """A sign-in's end is fixed when it is made; past it, its device is not woken."""
    async with Gateway() as gw:
        laptop = gw.device(CHROME, "laptop")
        await gw.sign_in_with_link(laptop)
        await laptop.turn_on_push()
        (ends,) = [r.expiry for r in ss.signed_in_sessions().values()]

    _pinged()
    assert woken == [laptop.endpoint], "the floor: while the sign-in lasts, it is woken"
    monkeypatch.setattr(push.time, "time", lambda: ends + 1)  # the sign-in ran its lifetime
    _pinged()
    assert woken == [laptop.endpoint], "a device whose sign-in ran out was woken"
    assert push.push_status()["devices"] == [], "the companion says push is on for it"


@pytest.mark.asyncio
async def test_a_push_destination_nothing_ties_to_a_sign_in_gets_no_ping(tmp_path, woken) -> None:
    """A row from before the sign-in was recorded: nothing says its device is signed in."""
    (tmp_path / "push_subscriptions.json").write_text(
        json.dumps(
            {"web-old": {"endpoint": "https://push.example/send/old", "keys": _browser_keys()}}
        ),
        encoding="utf-8",
    )
    _pinged()
    assert woken == []
