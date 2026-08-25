"""MC-2 — the CONSUMPTION side of COMPANION-APPS' device session, at the HTTP boundary.

MC-2 builds no mechanism. Pairing, the durable session row, the registry route and the
Settings → Devices panel are all CA-1/CA-2's, merged already; this module asserts the two
clauses a *phone* depends on, and both of them live in the middleware rather than in the store
that CA-2's tests exercise:

* **Roaming IP** — plan 54 §C1's transport constraint says a device session rides the session
  **cookie**, and the cookie branch skips IP binding outright (``not from_cookie`` guards both
  :func:`check_token_ip` and :func:`bind_token_ip` in ``token_auth.token_auth_middleware``). So a
  phone that changes carrier or Wi-Fi keeps its session with zero ``token_auth.py`` change. That
  is free *today* and silently broken the day someone binds cookie sessions too — which is
  exactly what COMPANION-APPS' ``CA-3`` log flagged as "worth an atom of its own". This is it.

* **Revocation** — CA-2 asserts revoke through :func:`token_auth.validate_token`. A phone never
  calls ``validate_token``; it makes an HTTP request. Between the two sit the bypass lists, the
  cookie branch and the adopt-from-store path, any of which could keep a revoked device alive
  while the validator already says no. MC-2's clause is worded "on the next **request**", so it
  is asserted as a request.

Every leg drives the real middleware over a real ``TestServer`` with a ``DummyCookieJar``, so a
request carries a credential only when that credential is named in the call — no leg can pass by
inheriting a cookie from an earlier one.

The clauses that are already met on ``main`` are deliberately NOT re-asserted here: the Settings →
Devices columns (name/kind/minted/last-seen/issuer + Revoke) are covered by
``web/src/pages/settings/devicesPanel.test.tsx`` and ``test_device_pairing.py``'s registry tests.
"""

from __future__ import annotations

import inspect
import json
import os

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.auth import pairing
from personalclaw.dashboard import session_store as ss
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.handlers import devices as devices_h

PORT = 10000
COOKIE = f"pc_token_{PORT}"

# Two addresses a roaming phone plausibly holds in one afternoon: RFC 5737 documentation
# ranges, so neither can collide with a real interface on the machine running this.
IP_HOME = "203.0.113.7"
IP_ROAMED = "198.51.100.22"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Every store this surface touches points at *tmp_path*, never the real home."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(pairing, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path, raising=False)
    (tmp_path / "config.json").write_text(json.dumps({"auth": {}}), encoding="utf-8")
    # The redirect is ASSERTED, not assumed: patching `loader.config_dir` alone misses a store
    # that bound the symbol at import time, and a leaked write would land in the real home.
    assert ss.sessions_path().is_relative_to(tmp_path), "the session store escaped tmp_path"
    assert pairing.codes_path().is_relative_to(tmp_path), "the code store escaped tmp_path"
    # 🪤 Either of these turns every authorization leg below into a no-op that reads as a pass.
    for var in ("PERSONALCLAW_DEV_NO_AUTH", "PERSONALCLAW_BYPASS_LOCAL_NETWORKS"):
        monkeypatch.delenv(var, raising=False)
    assert os.environ.get("PERSONALCLAW_DEV_NO_AUTH") != "1"
    assert os.environ.get("PERSONALCLAW_BYPASS_LOCAL_NETWORKS") != "1"
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()
    yield tmp_path
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()


async def _probe(request: web.Request) -> web.Response:
    """A minimal protected route: it exists only to be reached, or not reached."""
    return web.json_response(
        {"user": request.get("user", ""), "nonce": request.get("session_nonce", "")}
    )


def _app() -> web.Application:
    """The device routes behind the REAL auth middleware, plus one protected probe route."""
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    app["port"] = PORT
    app["allowed_origins"] = {f"http://localhost:{PORT}"}
    devices_h.register_device_routes(app)
    app.router.add_get("/api/mc2/probe", _probe)
    return app


def _client(server: TestServer) -> TestClient:
    """A client with NO cookie jar — a credential travels only when a leg names it."""
    return TestClient(server, cookie_jar=aiohttp.DummyCookieJar())


async def _pair_a_device(owner: TestClient, device: TestClient, ip: str) -> tuple[str, str]:
    """Mint a code as the owner, redeem it as the device. Returns (device cookie, device id)."""
    owner_token = token_auth.generate_token("owner", ttl_seconds=3600)
    started = await owner.post(
        "/api/devices/pair/start", json={}, cookies={COOKIE: owner_token}, headers={"X-Real-IP": ip}
    )
    assert started.status == 200, await started.text()
    code = (await started.json())["code"]

    # No cookie: a device redeeming a code has no session yet — that is the point, and why
    # `pair/complete` is in the middleware's bypass list.
    done = await device.post(
        "/api/devices/pair/complete",
        json={"code": code, "device_name": "Pixel", "kind": "mobile"},
        headers={"X-Real-IP": ip},
    )
    assert done.status == 200, await done.text()
    assert done.cookies[COOKIE], "pairing must hand back the ordinary session cookie"
    return done.cookies[COOKIE].value, (await done.json())["device_id"]


# ── Clause: "a roaming-IP phone keeps its device session valid" ──────────


@pytest.mark.asyncio
async def test_the_ip_check_is_live_on_the_query_param_exchange(_isolated) -> None:
    """VACUITY FLOOR for the roaming test below: IP binding really does deny a mismatch.

    Without this leg, "the phone still gets in from a new IP" is indistinguishable from "this
    build has no IP binding at all", and the roaming assertion would pass on a gateway whose
    transport constraint had been deleted.
    """
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, _device_id = await _pair_a_device(owner, device, IP_HOME)

        # First query-param use binds the token to the address it arrived from.
        bound = await device.get(
            "/api/mc2/probe", params={"token": token}, headers={"X-Real-IP": IP_HOME}
        )
        assert bound.status == 200, await bound.text()

        # The SAME credential, the same route, one different address.
        moved = await device.get(
            "/api/mc2/probe", params={"token": token}, headers={"X-Real-IP": IP_ROAMED}
        )
        assert moved.status == 403, "the query-param path must reject a moved token"
        assert (await moved.json())["error"] == "IP mismatch"


@pytest.mark.asyncio
async def test_a_device_session_roams_between_client_ips(_isolated) -> None:
    """THE CLAUSE: the cookie-borne device session survives a change of client IP.

    Same credential, same route, both addresses the query-param leg above proves are treated as
    a mismatch — and the phone stays in, because §C1's transport constraint says the cookie
    branch never consults the binding.
    """
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, _device_id = await _pair_a_device(owner, device, IP_HOME)

        first = await device.get(
            "/api/mc2/probe", cookies={COOKIE: token}, headers={"X-Real-IP": IP_HOME}
        )
        assert first.status == 200, await first.text()
        assert (await first.json())["user"] == devices_h.PAIRED_DEVICE_USER

        # Bind the token to IP_HOME through the query path, so the roam below is denied for the
        # ?token= exchange at the very moment the cookie is accepted. The contrast is the point:
        # a build that bound cookie sessions too would fail HERE, not in some future refactor.
        primed = await device.get(
            "/api/mc2/probe", params={"token": token}, headers={"X-Real-IP": IP_HOME}
        )
        assert primed.status == 200, await primed.text()

        roamed = await device.get(
            "/api/mc2/probe", cookies={COOKIE: token}, headers={"X-Real-IP": IP_ROAMED}
        )
        assert roamed.status == 200, "a device session must ride the cookie, not the address"
        assert (await roamed.json())["user"] == devices_h.PAIRED_DEVICE_USER

        # And it is still the same session, not a silently re-minted one: same nonce, and the
        # registry still lists exactly one device.
        assert (await roamed.json())["nonce"] == (await first.json())["nonce"]
        assert len(ss.device_sessions()) == 1


@pytest.mark.asyncio
async def test_a_roam_does_not_need_a_token_auth_change(_isolated) -> None:
    """The clause's second half: "no new claim added to token_auth.py".

    Asserted as a shape rather than as a diff, so it keeps holding: the device identity lives in
    the session STORE, and the token that authorizes the phone is byte-for-byte the shape an
    owner's browser carries.
    """
    assert list(inspect.signature(token_auth.generate_token).parameters) == [
        "user_id",
        "ttl_seconds",
        "app",
    ], "a `device` parameter on the minting path would be a second credential type"

    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, device_id = await _pair_a_device(owner, device, IP_HOME)

    payload = json.loads(token_auth._b64url_decode(token.split(".")[0]))
    # Exactly the claim set an owner-token carries — `nonce` is the store handle every session
    # has, not a device claim. An extra key here IS the second credential type §C1 forbids.
    assert set(payload) == {"sub", "exp", "session_exp", "iat", "nonce"}, "a claim was added"
    assert payload["sub"] == devices_h.PAIRED_DEVICE_USER
    assert device_id not in json.dumps(payload), "the device id must live in the store, not here"

    # …and the store is where it does live, so nothing was lost by keeping it out of the token.
    record = next(iter(ss.device_sessions().values()))
    assert record.device is not None and record.device.id == device_id
    assert record.issuer == ss.ISSUER_PAIR


# ── Clause: "revoking kills the device session on the next request" ─────


@pytest.mark.asyncio
async def test_revoke_refuses_the_devices_next_http_request(_isolated) -> None:
    """THE CLAUSE, at the boundary a phone actually crosses.

    CA-2 asserts this through ``validate_token``. Between that verdict and a request stand the
    bypass lists, the cookie branch and the adopt-from-store path — so the request is what gets
    asserted here.
    """
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, device_id = await _pair_a_device(owner, device, IP_HOME)

        before = await device.get("/api/mc2/probe", cookies={COOKIE: token})
        assert before.status == 200, "the vacuity floor: the device must be in before it is out"

        owner_token = token_auth.generate_token("owner", ttl_seconds=3600)
        revoked = await owner.post(
            f"/api/devices/{device_id}/revoke", json={}, cookies={COOKIE: owner_token}
        )
        assert revoked.status == 200, await revoked.text()
        assert (await revoked.json())["revoked"] == 1

        after = await device.get("/api/mc2/probe", cookies={COOKIE: token})
        assert after.status == 403, "the very next request must be refused"

        # And the panel the owner is looking at agrees, from the same read the UI performs.
        listed = await owner.get("/api/devices", cookies={COOKIE: owner_token})
        assert listed.status == 200
        assert (await listed.json())["devices"] == []


@pytest.mark.asyncio
async def test_a_revoked_device_stays_refused_across_a_restart(_isolated) -> None:
    """A revoke that un-revokes on reboot is worse than no revoke: the owner was told it worked.

    CA-2 covers this at the validator; repeated here at the HTTP boundary because that is where
    the store-adoption path runs, and adoption is the exact mechanism that could resurrect a
    forgotten nonce.
    """
    server = TestServer(_app())
    async with _client(server) as owner, _client(server) as device:
        token, device_id = await _pair_a_device(owner, device, IP_HOME)
        owner_token = token_auth.generate_token("owner", ttl_seconds=3600)
        assert (
            await owner.post(
                f"/api/devices/{device_id}/revoke", json={}, cookies={COOKIE: owner_token}
            )
        ).status == 200

        # The in-memory half of a restart, with the durable store left exactly as it is.
        token_auth._state.clear_all()
        token_auth.reset_secret_cache()

        after = await device.get("/api/mc2/probe", cookies={COOKIE: token})
        assert after.status == 403, "the durable half of the revoke must bite too"
