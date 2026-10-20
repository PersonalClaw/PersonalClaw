"""A paired device must stay RECOGNISABLE on the two auth paths that skip token validation.

``token_auth_middleware`` records **which session** authorized a request in
``request["session_nonce"]``, and it is the only writer. Two admitted paths never reach that
line, because both grant before it:

* the IP-gated local-network bypass (``PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1``) — the
  tokenless-ness ``make serve`` actually ships; and
* ``AuthMode.NONE``'s ``_dev_user_middleware`` in ``server.py`` — what
  ``PERSONALCLAW_AUTH_MODE=none`` installs INSTEAD of csrf + token auth.

Every handler that asks "which paired device is calling?" reads that one key and nothing else
(``handlers/browse_connector._paired_device``, ``ws._paired_device_session``), so on both paths
the answer was permanently "none". Measured on the standing validation gateway (2026-09-18,
``AUTH_MODE=none``): ``pair/start`` + ``pair/complete`` returned 200, ``GET /api/devices``
listed the new device, and ``POST /api/browse/connector`` still answered 403
``browse_connector_unpaired`` with that device's own freshly-minted cookie. The code was
correct; the mode made a whole CLASS of features unobservable — the connector attach,
companion-app device sessions, the origin-less ``/api/ws`` upgrade, mobile pairing.

This is the same hole the ``app`` claim had before none-mode learned to adopt it
(``test_app_permissions.test_none_mode_adopts_app_claim_and_enforces``), and it is closed the
same way: ``token_auth.presented_session_nonce``.

Both HTTP legs drive the REAL ``token_auth_middleware`` and the REAL connector handler over a
real ``TestServer`` with a ``DummyCookieJar``, so a request carries a credential only when the
leg names it. The strict-path leg is the vacuity floor: without it, "the bypassed device gets
in" would be indistinguishable from "this build has no paired-device gate at all".
"""

from __future__ import annotations

import inspect
import json
import os
import re

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.auth import pairing
from personalclaw.dashboard import session_store as ss
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.handlers import browse_connector as bc_h
from personalclaw.dashboard.handlers import devices as devices_h

PORT = 10000
COOKIE = f"pc_token_{PORT}"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Every store this surface touches points at *tmp_path*, never the real home."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(pairing, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path, raising=False)
    (tmp_path / "config.json").write_text(json.dumps({"auth": {}}), encoding="utf-8")
    assert ss.sessions_path().is_relative_to(tmp_path), "the session store escaped tmp_path"
    assert pairing.codes_path().is_relative_to(tmp_path), "the code store escaped tmp_path"
    # 🪤 The local-network bypass would admit every leg below unconditionally and read as a pass.
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    assert os.environ.get("PERSONALCLAW_BYPASS_LOCAL_NETWORKS") != "1"
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()
    bc_h.clear_connector()
    yield tmp_path
    bc_h.clear_connector()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()


async def _probe(request: web.Request) -> web.Response:
    """A minimal admitted route: it reports the identity the middleware chain resolved."""
    return web.json_response(
        {"user": request.get("user", ""), "nonce": request.get("session_nonce", "")}
    )


def _app() -> web.Application:
    """The device + connector routes behind the REAL auth middleware, plus one probe route."""
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    app["port"] = PORT
    app["allowed_origins"] = {f"http://localhost:{PORT}"}
    devices_h.register_device_routes(app)
    bc_h.register_browse_connector_routes(app)
    app.router.add_get("/api/probe", _probe)
    return app


def _client(server: TestServer) -> TestClient:
    """A client with NO cookie jar — a credential travels only when a leg names it."""
    return TestClient(server, cookie_jar=aiohttp.DummyCookieJar())


async def _pair_a_device(owner: TestClient, device: TestClient) -> str:
    """Mint a code as the owner, redeem it as the device. Returns the device's session cookie."""
    owner_token = token_auth.generate_token("owner", ttl_seconds=3600)
    started = await owner.post("/api/devices/pair/start", json={}, cookies={COOKIE: owner_token})
    assert started.status == 200, await started.text()
    code = (await started.json())["code"]

    done = await device.post(
        "/api/devices/pair/complete",
        json={"code": code, "device_name": "Pixel", "kind": "mobile"},
    )
    assert done.status == 200, await done.text()
    assert done.cookies[COOKIE], "pairing must hand back the ordinary session cookie"
    return done.cookies[COOKIE].value


async def _attach(client: TestClient, cookies: dict[str, str] | None) -> aiohttp.ClientResponse:
    return await client.post("/api/browse/connector", json={}, cookies=cookies or {})


# ── vacuity floor: the paired-device gate is LIVE on the strict path ──────


@pytest.mark.asyncio
async def test_the_paired_device_gate_is_live_on_the_strict_path(_isolated) -> None:
    """VACUITY FLOOR. On the ordinary token path a paired device attaches (200) and the
    owner's own browser session — which has no ``device`` row — is refused (403).

    Without both halves, the bypass legs below would pass on a build that had no
    paired-device gate at all, or on one that admitted every session as a device.
    """
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        cookie = await _pair_a_device(owner, device)

        ok = await _attach(device, {COOKIE: cookie})
        assert ok.status == 200, await ok.text()
        assert (await ok.json())["ok"] is True

        # The owner's own session authenticates fine and is still NOT a device.
        owner_token = token_auth.generate_token("owner", ttl_seconds=3600)
        refused = await _attach(owner, {COOKIE: owner_token})
        assert refused.status == 403
        assert (await refused.json())["error"]["code"] == "browse_connector_unpaired"


# ── the clause, leg 1: the IP-gated local-network bypass ──────────────────


@pytest.mark.asyncio
async def test_a_paired_device_is_still_a_device_under_the_local_network_bypass(
    _isolated, monkeypatch
) -> None:
    """THE CLAUSE (bypass). ``PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1`` grants on the client IP
    and returns before the middleware records the session — so the same paired cookie that
    attaches on the strict path used to be refused as unpaired here.
    """
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        # Pair with the bypass OFF, so the credential is minted by the ordinary path.
        cookie = await _pair_a_device(owner, device)

        monkeypatch.setenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", "1")
        # The bypass really is the path being taken: a request with NO credential at all is
        # admitted, which the strict path would have refused with "Token required".
        anon = await device.get("/api/probe")
        assert anon.status == 200, await anon.text()
        assert (await anon.json())["user"].startswith("local-net:")

        named = await device.get("/api/probe", cookies={COOKIE: cookie})
        assert (await named.json())["nonce"], "a validated session cookie must name its session"

        attached = await _attach(device, {COOKIE: cookie})
        assert attached.status == 200, await attached.text()
        assert (await attached.json())["ok"] is True


@pytest.mark.asyncio
async def test_the_bypass_names_only_a_session_the_token_proves(_isolated, monkeypatch) -> None:
    """Naming the session must not WIDEN the bypass: no cookie, a forged cookie and a revoked
    one are all admitted (that is what the bypass does) and none of them is a paired device.
    """
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        cookie = await _pair_a_device(owner, device)
        monkeypatch.setenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", "1")

        for label, cookies in (
            ("no credential", None),
            ("forged", {COOKIE: "junk.payload"}),
        ):
            refused = await _attach(device, cookies)
            assert refused.status == 403, f"{label}: {await refused.text()}"
            assert (await refused.json())["error"]["code"] == "browse_connector_unpaired", label

        # A revoked device stops being a device on its very next request, bypass or not.
        token_auth.revoke_all_sessions()
        gone = await _attach(device, {COOKIE: cookie})
        assert gone.status == 403, await gone.text()
        assert (await gone.json())["error"]["code"] == "browse_connector_unpaired"


# ── the clause, leg 2: AuthMode.NONE ─────────────────────────────────────


def test_presented_session_nonce_resolves_a_real_paired_session(_isolated) -> None:
    """The helper both skip-paths call, over a REAL paired session row.

    ``AuthMode.NONE``'s middleware is defined inside ``start_dashboard`` and cannot be
    imported, so its behaviour is asserted where it lives — in the helper — and its wiring is
    asserted separately below.
    """

    class _Req:
        def __init__(self, cookies: dict[str, str]) -> None:
            self.query: dict[str, str] = {}
            self.cookies = cookies
            self.headers: dict[str, str] = {}

    device = ss.DeviceInfo(id="abc123", name="Pixel", kind="mobile")
    minted = token_auth.mint_session(
        devices_h.PAIRED_DEVICE_USER, 3600, issuer=ss.ISSUER_PAIR, device=device
    )
    assert minted.persisted, "the fixture must persist a real device row"
    token, nonce = minted.token, minted.nonce

    assert token_auth.presented_session_nonce(_Req({COOKIE: token}), PORT) == nonce
    record = ss.paired_sessions().get(nonce)
    assert record is not None
    assert record.device.id == "abc123"

    # Fails CLOSED on everything the token does not prove.
    assert token_auth.presented_session_nonce(_Req({}), PORT) == ""
    assert token_auth.presented_session_nonce(_Req({COOKIE: "junk.payload"}), PORT) == ""
    assert token_auth.presented_session_nonce(_Req({COOKIE: token + "x"}), PORT) == ""
    token_auth.revoke_all_sessions()
    assert token_auth.presented_session_nonce(_Req({COOKIE: token}), PORT) == ""


def test_none_mode_middleware_populates_the_session_nonce() -> None:
    """WIRING: ``AuthMode.NONE``'s dev middleware must call the helper.

    The helper passing is not the point — the point is that none-mode USES it. Asserted on
    the source of ``start_dashboard`` because that middleware is a closure with no import
    path; the same reason ``test_device_session_consumption`` reads middleware source.
    """
    from personalclaw.dashboard.server import start_dashboard

    src = inspect.getsource(start_dashboard)
    head, _, tail = src.partition("async def _dev_user_middleware")
    assert tail, "the none-mode dev middleware was renamed; re-point this rail"
    # The closure's own body: up to the first line back at ``start_dashboard``'s indent.
    body = re.split(r"\n    (?=\S)", tail, maxsplit=1)[0]
    assert "presented_session_nonce" in body, (
        "AuthMode.NONE's middleware does not record session_nonce — every paired-device "
        "feature is unobservable under PERSONALCLAW_AUTH_MODE=none"
    )
    assert 'request["session_nonce"]' in body
