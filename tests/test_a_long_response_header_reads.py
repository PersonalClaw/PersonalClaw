"""A response with a header line GitHub's size reads, and one far past it is still refused.

aiohttp refuses a header line over 8,190 bytes by default, and GitHub's redirect for a release
download carried a 9,243-byte ``content-security-policy`` header on 2026-10-23, so every code-map
grammar fetched on a cold cache failed (#3905). A real server on loopback answers with a 9 KB header
and with a 40 KB one, through the egress fetch and through the guarded client session. That every
``aiohttp.ClientSession`` takes the same limits is held by
``tests/rails/test_a_long_response_header_reads_tree.py``.
"""

from __future__ import annotations

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

from personalclaw.net import fetch
from personalclaw.net.http_clients import http_session
from personalclaw.net.policy import LOOPBACK_INTERNAL

#: GitHub's header, in size: one ``content-security-policy`` line of 9 KiB.
GITHUBS = "default-src 'none'; " + "x" * (9 * 1024)
#: Far past any header a server sends in good faith, and past the limit.
TOO_LONG = "x" * (40 * 1024)


@pytest_asyncio.fixture
async def server():
    """A loopback server whose ``/github`` answers with GitHub's header and ``/too-long`` with one
    past the limit, each with the body ``ok``."""

    async def githubs(_: web.Request) -> web.Response:
        return web.Response(text="ok", headers={"Content-Security-Policy": GITHUBS})

    async def too_long(_: web.Request) -> web.Response:
        return web.Response(text="ok", headers={"Content-Security-Policy": TOO_LONG})

    app = web.Application()
    app.router.add_get("/github", githubs)
    app.router.add_get("/too-long", too_long)
    # The server writes what the client must read: its own reader is not what is tested.
    runner = web.AppRunner(app, handler_args={"max_field_size": 64 * 1024})
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    port = runner.addresses[0][1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_the_egress_fetch_reads_a_header_githubs_size(server):
    got = await fetch(f"{server}/github", policy=LOOPBACK_INTERNAL)
    assert got.status == 200 and got.body == b"ok"


@pytest.mark.asyncio
async def test_the_egress_fetch_still_refuses_a_header_far_past_it(server):
    with pytest.raises(aiohttp.ClientResponseError, match="Got more than"):
        await fetch(f"{server}/too-long", policy=LOOPBACK_INTERNAL)


@pytest.mark.asyncio
async def test_the_guarded_session_reads_a_header_githubs_size_and_refuses_one_far_past_it(server):
    async with http_session(endpoint=server) as session:
        async with session.get(f"{server}/github") as resp:
            assert resp.status == 200 and await resp.text() == "ok"
        with pytest.raises(aiohttp.ClientResponseError, match="Got more than"):
            async with session.get(f"{server}/too-long"):
                pass
