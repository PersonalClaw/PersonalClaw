"""A dashboard socket selects its own subprotocol and never holds the sign-in's pair.

The dashboard offers its sign-in's pair as the subprotocol ``pc-pair.<pair>`` beside
``personalclaw`` on every upgrade (``web/src/lib/pair.ts``), because a browser can't set a header
there. So every socket route:

* selects ``personalclaw``, or a browser that offered subprotocols fails the handshake;
* strips the ``pc-pair.*`` entry before the handshake, which otherwise writes the client's whole
  offered list to the log at WARNING when nothing overlaps, and could echo it in the 101.

These drive the real ``/api/ws`` handler over a real ``TestServer``. A rail holds
every ``WebSocketResponse`` in the gateway to the same two rules
(``tests/rails/test_every_socket_strips_the_sign_in_pair_tree.py``).
"""

from __future__ import annotations

import logging
from unittest import mock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard import ws as ws_mod
from personalclaw.dashboard.origin import build_allowed_origins
from personalclaw.dashboard.socket_protocols import without_pair_protocol
from personalclaw.dashboard.state import DashboardState

PAIR = "Q" * 43
OFFERED = f"personalclaw, pc-pair.{PAIR}"


async def _gateway() -> TestClient:
    """The shipped `/api/ws` handler, with no sign-in middleware: what is under test is the
    handshake, which runs after the sign-in check whatever it decides."""
    state = DashboardState(sessions=mock.MagicMock(count=0), start_time=0.0)
    state._sessions = {}

    @web.middleware
    async def _signed_in(request: web.Request, handler):  # type: ignore[no-untyped-def]
        request["user"] = "owner"
        request["session_nonce"] = ""
        return await handler(request)

    app = web.Application(middlewares=[_signed_in])
    app["state"] = state
    app["allowed_origins"] = build_allowed_origins(10000, True)
    app.router.add_get("/api/ws", ws_mod.api_ws)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_the_socket_selects_personalclaw_and_never_echoes_the_pair() -> None:
    client = await _gateway()
    try:
        sock = await client.ws_connect(
            "/api/ws",
            protocols=("personalclaw", f"pc-pair.{PAIR}"),
            headers={"Origin": "http://127.0.0.1:10000"},
        )
        assert sock.protocol == "personalclaw"
        assert PAIR not in str(sock._response.headers)  # noqa: SLF001 — the 101's own headers
        await sock.close()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_no_log_line_ever_holds_the_pair(caplog: pytest.LogCaptureFixture) -> None:
    """An upgrade offering only the pair overlaps nothing, which is when aiohttp logs the offered
    list. With the pair stripped first, there is nothing of it to log."""
    client = await _gateway()
    try:
        with caplog.at_level(logging.DEBUG):
            sock = await client.ws_connect(
                "/api/ws",
                protocols=(f"pc-pair.{PAIR}",),
                headers={"Origin": "http://127.0.0.1:10000"},
            )
            await sock.close()
        assert all(PAIR not in record.getMessage() for record in caplog.records)
    finally:
        await client.close()


def test_without_pair_protocol_keeps_every_other_entry_and_leaves_a_plain_upgrade_alone() -> None:
    from aiohttp.test_utils import make_mocked_request

    plain = make_mocked_request(
        "GET", "/api/ws", headers={"Sec-WebSocket-Protocol": "personalclaw"}
    )
    assert without_pair_protocol(plain) is plain
    none = make_mocked_request("GET", "/api/ws")
    assert without_pair_protocol(none) is none
    offered = make_mocked_request("GET", "/api/ws", headers={"Sec-WebSocket-Protocol": OFFERED})
    assert without_pair_protocol(offered).headers["Sec-WebSocket-Protocol"] == "personalclaw"
    only = make_mocked_request(
        "GET", "/api/ws", headers={"Sec-WebSocket-Protocol": f"pc-pair.{PAIR}"}
    )
    assert "Sec-WebSocket-Protocol" not in without_pair_protocol(only).headers
