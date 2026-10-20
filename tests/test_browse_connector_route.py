"""The connector routes the operator's own browser attaches and answers runs on.

``browse.target`` keeps the connector registry and the run tabs; these routes are their writer,
and the clauses that shape the test are what the routes must do:

* **loopback only, no new listener** — the routes live on the existing dashboard server
  (a ``web.Application`` here, exactly as ``server.py`` builds it) and refuse a
  non-loopback caller; an announced ``cdp_url`` is refused unless it rides the shipped
  ``LOOPBACK_INTERNAL`` rail.
* **listed as a connected device via the shipped pairing (consumed, not forked)** — the
  attaching client is an ordinary paired device, so the ``device_id`` that reaches the
  connector registry is the same one ``GET /api/devices`` lists. An owner session with no
  ``device`` provenance may NOT attach.
* **a run works only in a tab it opened for itself** — attaching names no page; a page target is
  announced only for a tab a granted run asked for, once, by the device it asked, and a later
  announce can never re-point the run at another page.
* **zero browser-vendor strings in core** — railed on the module source below.

Every leg drives the REAL token-auth middleware over a real ``TestServer`` with a
``DummyCookieJar``, so a request carries a credential only when a leg names it — the same
discipline as ``test_device_session_consumption``. The connector is a process global,
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

# The page target of the tab the browser opened for a run, on loopback — the shape the grant is
# bound to and ``resolve_cdp_url`` hands the CDP transport.
CDP_URL = "ws://127.0.0.1:9333/devtools/page/RUNSOWNTAB"
# Another page the same browser's debugger lists: one the operator has open.
OWNER_PAGE_URL = "ws://127.0.0.1:9333/devtools/page/OWNERSPAGE"
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
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
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
    orthogonal; this test is about the attachment, so the switch is stubbed rather
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

        attached = await device.post("/api/browse/connector", json={}, cookies={COOKIE: token})
        assert attached.status == 200, await attached.text()
        assert (await attached.json())["device_id"] == device_id

        status = bt.connector_status()
        assert status.connected is True, "the POST must have driven register_connector"
        assert status.device_id == device_id, "the connector's identity is the paired device id"


@pytest.mark.asyncio
async def test_the_attached_connector_is_listed_as_a_connected_device(_isolated, enabled) -> None:
    """The attached browser IS a paired-device row (consumed, not forked): it shows in the same
    registry ``GET /api/devices`` renders, and the connector status names it."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, device_id = await _pair_a_device(owner, device)
        attached = await device.post("/api/browse/connector", json={}, cookies={COOKIE: token})
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


# ── Clause: "a run works only in a tab it opened for itself" ──


async def _attached(owner: TestClient, device: TestClient) -> tuple[str, str]:
    """A paired device, attached as the connector. Returns (device cookie, device id)."""
    token, device_id = await _pair_a_device(owner, device)
    attached = await device.post("/api/browse/connector", json={}, cookies={COOKIE: token})
    assert attached.status == 200, await attached.text()
    return token, device_id


@pytest.mark.asyncio
async def test_attaching_names_no_page(_isolated, enabled) -> None:
    """Attaching records the DEVICE and no page, whatever the body carries: a page target exists
    only for a tab a run asked for. Before any run asks, the browser is answering for no tab."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, device_id = await _pair_a_device(owner, device)
        attached = await device.post(
            "/api/browse/connector", json={"cdp_url": OWNER_PAGE_URL}, cookies={COOKIE: token}
        )
        assert attached.status == 200, await attached.text()
        polled = await device.get("/api/browse/connector/tabs", cookies={COOKIE: token})
        assert polled.status == 200, await polled.text()
        assert await polled.json() == {"runs": []}
        assert bt.run_tabs_for(device_id) == []


@pytest.mark.asyncio
async def test_a_run_asks_the_attached_browser_for_a_tab_named_after_its_task(
    _isolated, enabled
) -> None:
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, device_id = await _attached(owner, device)
        tab = bt.request_run_tab(group="Check the lamp prices", device_id=device_id)
        assert tab is not None
        polled = await device.get("/api/browse/connector/tabs", cookies={COOKIE: token})
        assert await polled.json() == {
            "runs": [
                {
                    "request_id": tab.request_id,
                    "group": "Check the lamp prices",
                    "state": "requested",
                }
            ]
        }


@pytest.mark.asyncio
async def test_a_browser_that_is_not_the_connector_is_told_which_way(_isolated, enabled) -> None:
    """``not_attached`` when no browser is attached (the gateway restarted: attach again), and
    ``replaced`` when another one is (go quiet, rather than attaching over it)."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as first, _client(server) as second:
        first_token, _ = await _pair_a_device(owner, first)
        second_token, _ = await _pair_a_device(owner, second)
        polled = await first.get("/api/browse/connector/tabs", cookies={COOKIE: first_token})
        assert polled.status == 409, await polled.text()
        assert (await polled.json())["error"]["code"] == "browse_connector_not_attached"

        await second.post("/api/browse/connector", json={}, cookies={COOKIE: second_token})
        polled = await first.get("/api/browse/connector/tabs", cookies={COOKIE: first_token})
        assert polled.status == 409, await polled.text()
        assert (await polled.json())["error"]["code"] == "browse_connector_replaced"


@pytest.mark.asyncio
async def test_the_switch_turned_off_does_not_detach_the_browser(_isolated, monkeypatch) -> None:
    """With the ``user_browser`` switch off no run asks for a tab, but the attached browser is
    still the connector: it is answered, not told to attach again on every poll."""
    monkeypatch.setattr(bt, "user_browser_enabled", lambda: False)
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, _device_id = await _attached(owner, device)
        assert bt.connector_status().connected is False, "vacuity: the switch reads off"
        polled = await device.get("/api/browse/connector/tabs", cookies={COOKIE: token})
        assert polled.status == 200, await polled.text()
        assert await polled.json() == {"runs": []}


@pytest.mark.asyncio
async def test_the_runs_tab_is_announced_once_and_never_re_pointed(_isolated, enabled) -> None:
    """The page target the browser names for a run's tab is set once: a later announce naming
    another page (the operator's, say) is refused, and the run's tab still names its own."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, device_id = await _attached(owner, device)
        tab = bt.request_run_tab(group="Check the lamp prices", device_id=device_id)
        route = f"/api/browse/connector/tabs/{tab.request_id}"
        announced = await device.post(route, json={"cdp_url": CDP_URL}, cookies={COOKIE: token})
        assert announced.status == 200, await announced.text()
        assert (await announced.json())["state"] == "open"
        assert bt.run_tab(tab.request_id).cdp_url == CDP_URL

        again = await device.post(route, json={"cdp_url": OWNER_PAGE_URL}, cookies={COOKIE: token})
        assert again.status == 404, await again.text()
        assert (await again.json())["error"]["code"] == "browse_run_tab_unknown"
        assert bt.run_tab(tab.request_id).cdp_url == CDP_URL, "a second announce re-pointed it"


@pytest.mark.asyncio
async def test_only_the_browser_a_run_asked_may_answer_for_its_tab(_isolated, enabled) -> None:
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device, _client(server) as other:
        _token, device_id = await _attached(owner, device)
        other_token, _ = await _pair_a_device(owner, other)
        tab = bt.request_run_tab(group="Check the lamp prices", device_id=device_id)
        route = f"/api/browse/connector/tabs/{tab.request_id}"
        for body in ({"cdp_url": OWNER_PAGE_URL}, {"state": "closed"}):
            resp = await other.post(route, json=body, cookies={COOKIE: other_token})
            assert resp.status == 404, await resp.text()
        assert bt.run_tab(tab.request_id).state == "requested"


@pytest.mark.asyncio
async def test_a_public_or_non_websocket_page_is_refused_on_the_loopback_rail(
    _isolated, enabled
) -> None:
    """A run's tab must be a loopback page-target ws(s) URL: a public endpoint, a bare
    debugger-JSON URL and an empty one are all refused via ``LOOPBACK_INTERNAL``, and the run's
    tab stays unannounced."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, device_id = await _attached(owner, device)
        tab = bt.request_run_tab(group="Check the lamp prices", device_id=device_id)
        route = f"/api/browse/connector/tabs/{tab.request_id}"
        for bad in (PUBLIC_CDP_URL, "http://127.0.0.1:9222/json/version", ""):
            resp = await device.post(route, json={"cdp_url": bad}, cookies={COOKIE: token})
            assert resp.status == 400, await resp.text()
            assert (await resp.json())["error"]["code"] == "browse_connector_endpoint_invalid"
        assert bt.run_tab(tab.request_id).state == "requested", "a refused page was recorded"


@pytest.mark.asyncio
async def test_the_tabs_take_over_and_end_are_reported(_isolated, enabled) -> None:
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, device_id = await _attached(owner, device)
        tab = bt.request_run_tab(group="Check the lamp prices", device_id=device_id)
        route = f"/api/browse/connector/tabs/{tab.request_id}"
        await device.post(route, json={"cdp_url": CDP_URL}, cookies={COOKIE: token})

        taken = await device.post(route, json={"state": "taken_over"}, cookies={COOKIE: token})
        assert taken.status == 200 and (await taken.json())["state"] == "taken_over"
        closed = await device.post(route, json={"state": "closed"}, cookies={COOKIE: token})
        assert closed.status == 200 and (await closed.json())["state"] == "closed"
        late = await device.post(route, json={"state": "taken_over"}, cookies={COOKIE: token})
        assert late.status == 404, "a closed tab cannot be taken over"
        assert bt.run_tab(tab.request_id).state == "closed"


@pytest.mark.asyncio
async def test_a_browser_that_cannot_open_the_runs_tab_says_so(_isolated, enabled) -> None:
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, device_id = await _attached(owner, device)
        tab = bt.request_run_tab(group="Check the lamp prices", device_id=device_id)
        route = f"/api/browse/connector/tabs/{tab.request_id}"
        said = await device.post(route, json={"state": "unavailable"}, cookies={COOKIE: token})
        assert said.status == 200 and (await said.json())["state"] == "unavailable"
        late = await device.post(route, json={"cdp_url": CDP_URL}, cookies={COOKIE: token})
        assert late.status == 404, "a tab the browser said it could not open was announced"


@pytest.mark.asyncio
async def test_a_report_names_a_page_or_one_known_state(_isolated, enabled) -> None:
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, device_id = await _attached(owner, device)
        tab = bt.request_run_tab(group="Check the lamp prices", device_id=device_id)
        route = f"/api/browse/connector/tabs/{tab.request_id}"
        for body in ({}, {"state": "focused"}, {"cdp_url": CDP_URL, "state": "closed"}):
            resp = await device.post(route, json=body, cookies={COOKIE: token})
            assert resp.status == 400, (body, await resp.text())
            assert (await resp.json())["error"]["code"] == "browse_run_tab_report_invalid"
        assert bt.run_tab(tab.request_id).state == "requested"


@pytest.mark.asyncio
async def test_a_granted_run_is_bound_to_the_tab_the_browser_opened_for_it(
    _isolated, enabled
) -> None:
    """The whole handshake over the real routes: a granted run asks for its tab, the browser sees
    the request on its poll and announces the tab it opened, and the grant comes back bound to
    that tab — and to no other page the browser has open."""
    import asyncio

    from personalclaw.browse import grant as bg

    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, device_id = await _attached(owner, device)
        granted = bg.BrowserGrant(
            task="Check the lamp prices",
            scope=("shop.example.com",),
            group_name="Check the lamp prices",
            request_id="g1",
            granted=True,
            granted_at=1.0,
            bound_device_id=device_id,
        )
        waiting = asyncio.create_task(bg.open_run_tab(granted, timeout=5))
        runs: list[dict] = []
        for _ in range(200):
            polled = await device.get("/api/browse/connector/tabs", cookies={COOKIE: token})
            runs = (await polled.json())["runs"]
            if runs:
                break
            await asyncio.sleep(0.01)
        assert [r["group"] for r in runs] == ["Check the lamp prices"]
        route = f"/api/browse/connector/tabs/{runs[0]['request_id']}"
        await device.post(route, json={"cdp_url": CDP_URL}, cookies={COOKIE: token})

        bound = await waiting
        assert bound.bound_tab == runs[0]["request_id"]
        assert bound.bound_cdp_url == CDP_URL
        drives = bt.resolve_cdp_url(
            bt.TARGET_USER_BROWSER, {"cdp_url": OWNER_PAGE_URL}, grant=bound
        )
        assert drives == CDP_URL


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
    for method, path, handler in (
        ("POST", "/api/browse/connector", bc.api_browse_connector_attach),
        ("GET", "/api/browse/connector/tabs", bc.api_browse_connector_tabs),
        ("POST", "/api/browse/connector/tabs/abc", bc.api_browse_connector_tab),
    ):
        req = make_mocked_request(method, path, transport=transport)
        assert req.remote == "198.51.100.9", "the peer must be the non-loopback address under test"
        resp = await handler(req)
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
        resp = await owner.post("/api/browse/connector", json={}, cookies={COOKIE: owner_token})
        assert resp.status == 403, await resp.text()
        assert (await resp.json())["error"]["code"] == "browse_connector_unpaired"
        assert bt.connector_status().connected is False
        for leg in (
            owner.get("/api/browse/connector/tabs", cookies={COOKIE: owner_token}),
            owner.post(
                "/api/browse/connector/tabs/abc",
                json={"cdp_url": CDP_URL},
                cookies={COOKIE: owner_token},
            ),
        ):
            refused = await leg
            assert refused.status == 403, await refused.text()
            assert (await refused.json())["error"]["code"] == "browse_connector_unpaired"


# ── detach ──


@pytest.mark.asyncio
async def test_detach_clears_the_connector(_isolated, enabled) -> None:
    """DELETE detaches; a ``user_browser`` task then skips again rather than driving a dead
    endpoint."""
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, device_id = await _pair_a_device(owner, device)
        await device.post("/api/browse/connector", json={}, cookies={COOKIE: token})
        assert bt.connector_status().connected is True, "vacuity floor: attached before detach"
        tab = bt.request_run_tab(group="Check the lamp prices", device_id=device_id)
        assert tab is not None

        gone = await device.delete("/api/browse/connector", cookies={COOKIE: token})
        assert gone.status == 200, await gone.text()
        assert bt.connector_status().connected is False, "detach must clear the registry"
        assert bt.run_tab(tab.request_id) is None, "a detached browser holds no run's tab"


# ── Clause: "zero browser-vendor strings in core" ──


def test_the_connector_module_names_no_browser_vendor() -> None:
    """All vendor knowledge stays in the app bundle. Railed on the module SOURCE with word
    boundaries so ``operator`` / ``knowledge`` are safe while a real vendor name is not.
    Scoped to this module: the clause is that the connector adds no vendor string.
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
