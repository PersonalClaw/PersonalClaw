"""PL-9 — the API version is a negotiated contract, not an emitted number.

Before this atom ``API_VERSION`` was write-only: defined in ``manifest.py`` and
emitted into ``/api/manifest`` plus two generated reference docs, with no
comparison anywhere. These tests are the comparison, driven through the real
middleware, so the negotiation cannot ship inert the way the constant did.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import api_version as av
from personalclaw.dashboard.api_version_gate import (
    EXEMPT_EXACT,
    api_version_middleware,
    is_gated,
)

# ── The resolution rule, at the function ──────────────────────────────────────
#
# `negotiate` takes the window as call-time arguments precisely so these cases are
# observable: the SHIPPED window is one version wide (min == current == 1), which
# makes "absent ⇒ oldest" and "absent ⇒ current" indistinguishable end to end.
# Driving a wider window is what makes the inversion visible.


class TestResolutionRule:
    def test_absent_declaration_resolves_to_the_floor_not_the_current(self):
        # The whole point: a client that declares NOTHING is the oldest supported
        # version. With a window of 2-5 that resolution is 2 — never 5 — and 2 is
        # inside the window, so the request proceeds *as version 2*. If absence
        # resolved to `server`, an unversioned old client would be credited with
        # the current shape, which is the specific hole this atom closes.
        out = av.negotiate(None, server=5, minimum=2)
        assert out.refusal is None
        assert out.negotiated == 2, "absence must resolve to the FLOOR, not the current version"

        # And with an incoherent window (floor above the ceiling — what a bad bump
        # produces), the same absent declaration is refused rather than waved
        # through, and the refusal says so in words.
        refused = av.negotiate(None, server=5, minimum=6)
        assert refused.refusal is not None
        assert refused.negotiated == 6
        assert refused.refusal.client_version == 6
        assert refused.refusal.upgrade == "client"
        assert "declared no API version" in refused.refusal.message
        assert "oldest supported version (6)" in refused.refusal.message

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_blank_is_treated_as_absent(self, blank):
        assert av.negotiate(blank, server=5, minimum=2).negotiated == 2
        assert av.negotiate(blank, server=5, minimum=6).refusal is not None

    def test_inside_window_passes_as_the_declared_version(self):
        for v in (2, 3, 5):
            out = av.negotiate(str(v), server=5, minimum=2)
            assert out.refusal is None
            assert out.negotiated == v

    def test_below_floor_refuses_and_points_at_the_client(self):
        refusal = av.negotiate("1", server=5, minimum=2).refusal
        assert refusal is not None
        assert (refusal.client_version, refusal.server_version) == (1, 5)
        assert refusal.min_supported_version == 2
        assert refusal.upgrade == "client"
        assert "built for API version 1" in refusal.message
        assert "speaks versions 2-5" in refusal.message
        assert "Upgrade the client" in refusal.message

    def test_above_current_refuses_and_points_at_the_server(self):
        refusal = av.negotiate("9", server=5, minimum=2).refusal
        assert refusal is not None
        assert (refusal.client_version, refusal.server_version) == (9, 5)
        assert refusal.upgrade == "server"
        assert "Upgrade the gateway" in refusal.message

    def test_unparseable_declaration_refuses_and_is_truncated(self):
        out = av.negotiate("banana" * 20)
        assert out.negotiated is None  # nothing to credit it with
        assert out.refusal is not None
        assert isinstance(out.refusal.client_version, str)
        assert len(out.refusal.client_version) <= 32
        assert out.refusal.upgrade == "client"

    def test_defaults_read_the_module_constants_at_call_time(self):
        # No literal window in the signature: a bump to the constants moves the
        # gate with it, which is what keeps emitted/declared/negotiated in step.
        assert av.negotiate(str(av.API_VERSION)).refusal is None
        assert av.negotiate(str(av.MIN_SUPPORTED_API_VERSION)).refusal is None
        assert av.negotiate(str(av.API_VERSION + 1)).refusal is not None
        assert av.negotiate(str(av.MIN_SUPPORTED_API_VERSION - 1)).refusal is not None

    def test_shipped_window_is_coherent(self):
        floor, current = av.supported_window()
        assert floor <= current
        assert (floor, current) == (av.MIN_SUPPORTED_API_VERSION, av.API_VERSION)


# ── The chokepoint, driven end to end ─────────────────────────────────────────


def _make_app() -> web.Application:
    """The real middleware in front of a stand-in handler."""
    app = web.Application()
    app.middlewares.append(api_version_middleware())

    async def ok(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    for path in ("/api/thing", "/api/healthz", "/api/manifest", "/api/ws", "/mcp"):
        app.router.add_get(path, ok)
    app.router.add_get("/", ok)
    app.router.add_get("/assets/index.js", ok)
    return app


HDR = av.VERSION_HEADER


class TestChokepointRefusal:
    @pytest.mark.asyncio
    async def test_mismatched_client_is_refused_through_the_pl8_envelope(self):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/api/thing", headers={HDR: str(av.API_VERSION + 1)})
            assert resp.status == 400
            body = await resp.json()
            # PL-8's envelope, not a second error shape and not a 500.
            assert set(body) == {"error"}
            err = body["error"]
            assert err["code"] == "api_version_unsupported"
            # Both versions AND the direction — the three facts that make the
            # refusal actionable instead of merely correct.
            assert err["client_version"] == av.API_VERSION + 1
            assert err["server_version"] == av.API_VERSION
            assert err["min_supported_version"] == av.MIN_SUPPORTED_API_VERSION
            assert err["upgrade"] == "server"
            assert str(av.API_VERSION + 1) in err["message"]
            assert "Upgrade the gateway" in err["message"]

    @pytest.mark.asyncio
    async def test_stale_bundle_below_the_floor_is_told_to_reload(self):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(
                "/api/thing", headers={HDR: str(av.MIN_SUPPORTED_API_VERSION - 1)}
            )
            assert resp.status == 400
            err = (await resp.json())["error"]
            assert err["upgrade"] == "client"
            assert "reload the page" in err["message"]

    @pytest.mark.asyncio
    async def test_matching_client_passes(self):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/api/thing", headers={HDR: str(av.API_VERSION)})
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_undeclared_client_passes_while_the_floor_is_reachable(self):
        # Today min == current == 1, so an unversioned caller (a curl, an old
        # script) still works. The rule that makes this safe is tested at the
        # function above: absence pins to the FLOOR, so this stops passing the
        # moment the floor rises.
        async with TestClient(TestServer(_make_app())) as client:
            assert (await client.get("/api/thing")).status == 200

    @pytest.mark.asyncio
    async def test_undeclared_client_is_credited_with_the_floor_not_the_current(self, monkeypatch):
        # The shipped window is one version wide, which makes "absent ⇒ oldest" and
        # "absent ⇒ current" indistinguishable. Widen it (negotiate reads the module
        # constants at call time, so the gate sees this) and the difference is a
        # fact on the wire: the echoed header must be the FLOOR.
        monkeypatch.setattr(av, "MIN_SUPPORTED_API_VERSION", 2)
        monkeypatch.setattr(av, "API_VERSION", 5)
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/api/thing")
            assert resp.status == 200
            assert resp.headers[HDR] == "2", (
                "an undeclared client was credited with the current version instead "
                "of the oldest supported one"
            )
            # A declaring client is credited with exactly what it declared.
            resp = await client.get("/api/thing", headers={HDR: "4"})
            assert resp.headers[HDR] == "4"

    @pytest.mark.asyncio
    async def test_accepted_response_echoes_the_negotiated_version(self):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/api/thing", headers={HDR: str(av.API_VERSION)})
            assert resp.headers[HDR] == str(av.API_VERSION)

    @pytest.mark.asyncio
    async def test_exempt_routes_are_never_refused(self):
        # A version refusal on any of these is an outage, not a legible message:
        # healthz reads as "gateway down"; the manifest is how a client learns the
        # number; a WS cannot carry a header; the SPA + /assets are the recovery
        # path a refused client must reach to stop being refused.
        bad = {HDR: str(av.API_VERSION + 1)}
        async with TestClient(TestServer(_make_app())) as client:
            for path in (
                "/api/healthz",
                "/api/manifest",
                "/api/ws",
                "/mcp",
                "/",
                "/assets/index.js",
            ):
                resp = await client.get(path, headers=bad)
                assert resp.status == 200, f"{path} was version-gated"


class TestExemptionPolicy:
    def test_only_api_and_mcp_are_in_scope(self):
        assert is_gated("/api/config")
        assert not is_gated("/")
        assert not is_gated("/assets/index-abc.js")
        assert not is_gated("/login")
        assert not is_gated("/claw.svg")

    def test_every_exemption_is_inside_the_gate_scope(self):
        # A dead exemption row reads as a deliberate decision while protecting
        # nothing. Each exact exemption must name a path the gate would otherwise
        # have caught.
        from personalclaw.dashboard.api_version_gate import GATED_PREFIXES

        for path in EXEMPT_EXACT:
            assert path.startswith(GATED_PREFIXES), f"{path} was never gated anyway"
            assert not is_gated(path)

    def test_ws_prefix_covers_the_terminal_socket_too(self):
        assert not is_gated("/api/ws")
        assert not is_gated("/api/ws/terminal/abc")

    def test_pre_session_front_door_is_exempt(self):
        # A version wall in front of login turns "reload the page" into "you
        # cannot authenticate far enough to see why you were refused".
        for path in (
            "/api/auth/login",
            "/api/auth/status",
            "/api/token/local",
            "/api/logout",
            "/api/auth/enroll/complete",
            "/api/devices/pair/complete",
        ):
            assert not is_gated(path), path


class TestWindowPhrasing:
    """The refusal is a sentence, not a range dump.

    The SHIPPED window is one version wide, so "speaks 1-1" would be the only
    phrasing anyone ever read — machine-shaped copy on the surface whose whole
    purpose is legibility.
    """

    def test_a_one_version_window_reads_as_a_version_not_a_range(self):
        msg = av.negotiate("2", server=1, minimum=1).refusal.message
        assert "speaks version 1" in msg
        assert "1-1" not in msg

    def test_a_wider_window_reads_as_a_range(self):
        msg = av.negotiate("9", server=5, minimum=2).refusal.message
        assert "speaks versions 2-5" in msg

    def test_the_shipped_window_reads_correctly_whatever_its_width(self):
        floor, current = av.supported_window()
        refusal = av.negotiate(str(current + 1)).refusal
        assert refusal is not None
        expected = (
            f"speaks version {current}"
            if floor == current
            else f"speaks versions {floor}-{current}"
        )
        assert expected in refusal.message
