"""BA-8 — the non-test CALLER of ``browse.target.register_connector``, at the HTTP boundary.

BA-7 built the connector registry and proved its *decisions* with the transport stubbed;
it deliberately shipped ``register_connector`` with no non-test caller. BA-8's core half is
this loopback route, and the clauses that shape the test are BA-8's ``done_when``:

* **loopback only, no new listener** — the route lives on the existing dashboard server
  (a ``web.Application`` here, exactly as ``server.py`` builds it) and refuses a
  non-loopback caller; the announced ``cdp_url`` is refused unless it rides the shipped
  ``LOOPBACK_INTERNAL`` rail.
* **listed as a connected device via the shipped pairing (§C1/C2 — consumed, not forked)**
  — the attaching client is an ordinary paired device, so the ``device_id`` that reaches
  the connector registry is the same one ``GET /api/devices`` lists. An owner session with
  no ``device`` provenance may NOT attach.
* **zero browser-vendor strings in core** — railed on the module source below.

Every leg drives the REAL token-auth middleware over a real ``TestServer`` with a
``DummyCookieJar``, so a request carries a credential only when a leg names it — the same
discipline as ``test_mc2_device_session_consumption``. The connector is a process global,
so the fixture detaches around every test: a leaked attachment would make a "not attached"
vacuity leg pass for the wrong reason.
"""

from __future__ import annotations

import json
import pathlib

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from personalclaw.auth import pairing
from personalclaw.browse import target as bt
from personalclaw.dashboard import session_store as ss
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.handlers import browse_connector as bc
from personalclaw.dashboard.handlers import devices as devices_h

PORT = 10001
COOKIE = f"pc_token_{PORT}"

# A page-target endpoint on loopback — the shape ``resolve_cdp_url`` hands the CDP transport.
CDP_URL = "ws://127.0.0.1:9333/devtools/page/MYOWNBROWSER"
# RFC 5737 documentation address: a non-loopback host that resolves to itself as an IP
# literal (so the guard denies it without any real DNS), for the "public endpoint" leg.
PUBLIC_CDP_URL = "ws://203.0.113.7:9222/devtools/page/SOMEWHEREELSE"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Every store this surface touches points at *tmp_path*, and the connector starts and
    ends DETACHED — the process-global reset ``test_browse_target`` also relies on."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(pairing, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path, raising=False)
    (tmp_path / "config.json").write_text(json.dumps({"auth": {}}), encoding="utf-8")
    assert ss.sessions_path().is_relative_to(tmp_path), "the session store escaped tmp_path"
    assert pairing.codes_path().is_relative_to(tmp_path), "the code store escaped tmp_path"
    for var in ("PERSONALCLAW_DEV_NO_AUTH", "PERSONALCLAW_BYPASS_LOCAL_NETWORKS"):
        monkeypatch.delenv(var, raising=False)
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()
    bt.clear_connector()
    yield tmp_path
    bt.clear_connector()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()


@pytest.fixture
def enabled(monkeypatch):
    """Turn the ``user_browser`` switch ON so ``connector_status`` reveals the registered
    session. The switch (a settings decision) and the attachment (a connector decision) are
    orthogonal in BA-7; this test is about the attachment, so the switch is stubbed rather
    than plumbed through config."""
    monkeypatch.setattr(bt, "user_browser_enabled", lambda: True)


def _app() -> web.Application:
    """The connector route behind the REAL auth middleware, with the device routes it pairs
    through. This is the dashboard server's own wiring, minus the rest of the surface."""
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    app["port"] = PORT
    app["allowed_origins"] = {f"http://localhost:{PORT}"}
    devices_h.register_device_routes(app)
    bc.register_browse_connector_routes(app)
    return app


def _client(server: TestServer) -> TestClient:
    """A client with NO cookie jar — a credential travels only when a leg names it."""
    return TestClient(server, cookie_jar=aiohttp.DummyCookieJar())


async def _pair_a_device(owner: TestClient, device: TestClient) -> tuple[str, str]:
    """Mint a code as the owner, redeem it as the device. Returns (device cookie, device id)."""
    owner_token = token_auth.generate_token("owner", ttl_seconds=3600)
    started = await owner.post("/api/devices/pair/start", json={}, cookies={COOKIE: owner_token})
    assert started.status == 200, await started.text()
    code = (await started.json())["code"]
    done = await device.post(
        "/api/devices/pair/complete",
        json={"code": code, "device_name": "Workstation", "kind": "browser"},
    )
    assert done.status == 200, await done.text()
    return done.cookies[COOKIE].value, (await done.json())["device_id"]


# ── Clause: "a paired device attaches, and register_connector is actually driven" ──


@pytest.mark.asyncio
async def test_a_paired_device_attaches_as_the_user_browser_connector(_isolated, enabled) -> None:
    """THE CLAUSE: the route is the non-test writer of the connector registry.

    The vacuity partner is the pre-attach read on the SAME (switched-on) status: it must say
    "not connected", or a green post-attach read would prove nothing about the POST.
    """
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, device_id = await _pair_a_device(owner, device)

        assert bt.connector_status().connected is False, "vacuity floor: nothing attached yet"

        attached = await device.post(
            "/api/browse/connector", json={"cdp_url": CDP_URL}, cookies={COOKIE: token}
        )
        assert attached.status == 200, await attached.text()
        assert (await attached.json())["device_id"] == device_id

        status = bt.connector_status()
        assert status.connected is True, "the POST must have driven register_connector"
        assert status.cdp_url == CDP_URL, "the announced endpoint is what resolve_cdp_url returns"
        assert status.device_id == device_id, "the connector's identity is the paired device id"


@pytest.mark.asyncio
async def test_the_attached_connector_is_listed_as_a_connected_device(_isolated, enabled) -> None:
    """The attached browser IS a paired-device row (§C1/C2 consumed, not forked): it shows in
    the same registry ``GET /api/devices`` renders, and the connector status names it."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, device_id = await _pair_a_device(owner, device)
        attached = await device.post(
            "/api/browse/connector", json={"cdp_url": CDP_URL}, cookies={COOKIE: token}
        )
        assert attached.status == 200, await attached.text()

        owner_token = token_auth.generate_token("owner", ttl_seconds=3600)
        listed = await owner.get("/api/devices", cookies={COOKIE: owner_token})
        assert listed.status == 200
        rows = (await listed.json())["devices"]
        row = next((r for r in rows if r["id"] == device_id), None)
        assert row is not None, "the connector must be a listed device"
        assert row["issuer"] == ss.ISSUER_PAIR, "listed via the shipped pairing, not a fork"

        seen = await device.get("/api/browse/connector", cookies={COOKIE: token})
        assert seen.status == 200
        payload = await seen.json()
        assert payload["connected"] is True and payload["device_id"] == device_id


# ── Clause: "loopback only" ──


@pytest.mark.asyncio
async def test_a_public_cdp_url_is_refused_on_the_loopback_rail(_isolated, enabled) -> None:
    """A non-loopback endpoint is refused via ``LOOPBACK_INTERNAL`` — the connector stays
    detached. The loopback endpoint accepted in the test above is the vacuity partner."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, _device_id = await _pair_a_device(owner, device)
        resp = await device.post(
            "/api/browse/connector", json={"cdp_url": PUBLIC_CDP_URL}, cookies={COOKIE: token}
        )
        assert resp.status == 400, await resp.text()
        assert (await resp.json())["error"]["code"] == "browse_connector_endpoint_invalid"
        assert bt.connector_status().connected is False, "a refused endpoint must not attach"


@pytest.mark.asyncio
async def test_a_non_websocket_endpoint_is_refused(_isolated, enabled) -> None:
    """The endpoint must be a page-target ws(s) URL — an http(s) URL is refused by scheme,
    so a bare debugger-JSON URL cannot be mistaken for a page target."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, _device_id = await _pair_a_device(owner, device)
        resp = await device.post(
            "/api/browse/connector",
            json={"cdp_url": "http://127.0.0.1:9222/json/version"},
            cookies={COOKIE: token},
        )
        assert resp.status == 400, await resp.text()
        assert (await resp.json())["error"]["code"] == "browse_connector_endpoint_invalid"


@pytest.mark.asyncio
async def test_a_missing_endpoint_is_refused(_isolated) -> None:
    """An empty body carries no endpoint, so there is nothing to attach."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, _device_id = await _pair_a_device(owner, device)
        resp = await device.post("/api/browse/connector", json={}, cookies={COOKIE: token})
        assert resp.status == 400, await resp.text()
        assert (await resp.json())["error"]["code"] == "browse_connector_endpoint_invalid"


@pytest.mark.asyncio
async def test_a_non_loopback_caller_is_refused() -> None:
    """A same-machine surface reached from off-box is refused on the raw peer, before any
    session or body is read (a TestServer is always loopback, so the peer is set directly).
    """
    from unittest import mock

    transport = mock.Mock()
    transport.get_extra_info.side_effect = lambda key, default=None: (
        ("198.51.100.9", 40000) if key == "peername" else default
    )
    req = make_mocked_request("POST", "/api/browse/connector", transport=transport)
    assert req.remote == "198.51.100.9", "the peer must be the non-loopback address under test"
    resp = await bc.api_browse_connector_attach(req)
    assert resp.status == 403
    assert json.loads(resp.body.decode())["error"]["code"] == "browse_connector_loopback_only"


# ── Clause: "paired via the shipped pairing — an owner session may not attach" ──


@pytest.mark.asyncio
async def test_an_owner_session_cannot_attach_a_connector(_isolated, enabled) -> None:
    """Only a paired device may attach: an owner token has no ``device`` row, so it is refused
    and nothing is registered — which is what keeps the connector from forking pairing."""
    server = TestServer(_app())
    async with _client(server) as owner:
        owner_token = token_auth.generate_token("owner", ttl_seconds=3600)
        resp = await owner.post(
            "/api/browse/connector", json={"cdp_url": CDP_URL}, cookies={COOKIE: owner_token}
        )
        assert resp.status == 403, await resp.text()
        assert (await resp.json())["error"]["code"] == "browse_connector_unpaired"
        assert bt.connector_status().connected is False


# ── detach ──


@pytest.mark.asyncio
async def test_detach_clears_the_connector(_isolated, enabled) -> None:
    """DELETE detaches; a ``user_browser`` task then skips again rather than driving a dead
    endpoint."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, _device_id = await _pair_a_device(owner, device)
        await device.post(
            "/api/browse/connector", json={"cdp_url": CDP_URL}, cookies={COOKIE: token}
        )
        assert bt.connector_status().connected is True, "vacuity floor: attached before detach"

        gone = await device.delete("/api/browse/connector", cookies={COOKIE: token})
        assert gone.status == 200, await gone.text()
        assert bt.connector_status().connected is False, "detach must clear the registry"


# ── Clause: "zero browser-vendor strings in core" ──


def test_the_connector_module_names_no_browser_vendor() -> None:
    """BA-8 keeps all vendor knowledge in the app bundle. Railed on the module SOURCE with
    word boundaries so ``operator`` / ``knowledge`` are safe while a real vendor name is not.
    Scoped to this new module: BA-8's clause is that the connector adds no vendor string.
    """
    import re

    source = pathlib.Path(bc.__file__).read_text(encoding="utf-8").lower()
    vendors = (
        "chrome",
        "chromium",
        "firefox",
        "safari",
        "edge",
        "msedge",
        "webkit",
        "blink",
        "gecko",
        "brave",
        "opera",
        "vivaldi",
    )
    hits = [v for v in vendors if re.search(rf"\b{v}\b", source)]
    assert not hits, f"the connector module leaked browser-vendor string(s): {hits}"
