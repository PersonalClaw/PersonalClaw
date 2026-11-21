"""The approvals route answers an approval by the id the waiting list gives it, and an id it does
not hold says no approval waits under it, never that it expired.

``GET /api/waiting`` lists an approval as ``approval:<registry id>`` (``waiting``'s item id, the
kind before its own id), and ``POST /api/approvals/{id}/approve`` took only the registry id: sent
the id the list gave, it answered 404 "not found or expired" while the ask still waited. And it
said "expired" for every id it did not hold, a malformed one included.

Now the route takes either form, and an id no approval waits under answers 404
``approval_not_found``, saying it was answered, it expired, or there never was one.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

APPROVAL = "loop-7:tooluse_1"


def _state(tmp_path):
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog

    state = DashboardState(
        sessions=MagicMock(count=0),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    state.broadcast_ws = MagicMock()
    return state


async def _client(state) -> TestClient:
    from personalclaw.dashboard.handlers.sessions import api_approval_resolve

    @web.middleware
    async def _you(request, handler):  # the auth middleware admitted you, signed in
        request["user"] = "owner"
        return await handler(request)

    app = web.Application(middlewares=[_you])
    app["state"] = state
    app.router.add_post("/api/approvals/{id}/{action}", api_approval_resolve)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _asked(state) -> asyncio.Future:
    waiter = asyncio.ensure_future(
        state.request_approval(APPROVAL, "loop:7", "bash", tool_input="ls", confirms=())
    )
    for _ in range(300):
        if APPROVAL in state._pending_approvals:
            return waiter
        await asyncio.sleep(0.01)
    raise AssertionError("the approval was never asked")


def _waiting_id(state) -> str:
    """The id the waiting list gives the approval, built by the list itself."""
    from personalclaw import waiting

    (item,) = waiting._approvals(
        [state._pending_approvals[APPROVAL]], window=600.0, now=time.time()
    )
    return item["id"]


@pytest.mark.asyncio
async def test_the_id_the_waiting_list_gives_answers_the_approval(tmp_path):
    """🔴 Red before: the list's id answered 404 "not found or expired" while the ask waited."""
    from urllib.parse import quote

    state = _state(tmp_path)
    waiter = await _asked(state)
    listed = _waiting_id(state)
    assert listed == f"approval:{APPROVAL}"
    client = await _client(state)
    try:
        resp = await client.post(f"/api/approvals/{quote(listed, safe='')}/approve")
        said = await resp.json()
    finally:
        await client.close()

    assert resp.status == 200, said
    assert await asyncio.wait_for(waiter, timeout=5) is True


@pytest.mark.asyncio
async def test_the_registry_id_still_answers_it(tmp_path):
    state = _state(tmp_path)
    waiter = await _asked(state)
    client = await _client(state)
    try:
        resp = await client.post(f"/api/approvals/{APPROVAL}/reject")
    finally:
        await client.close()

    assert resp.status == 200
    assert await asyncio.wait_for(waiter, timeout=5) is False


@pytest.mark.parametrize("given", ["loop-7:tooluse_9", "approval:nothing-here", "%%%", "approval:"])
@pytest.mark.asyncio
async def test_an_id_no_approval_waits_under_says_so_not_that_it_expired(tmp_path, given):
    """🔴 Red before: every id the registry did not hold read as expired."""
    from urllib.parse import quote

    state = _state(tmp_path)
    client = await _client(state)
    try:
        resp = await client.post(f"/api/approvals/{quote(given, safe='')}/approve")
        said = await resp.json()
    finally:
        await client.close()

    assert resp.status == 404
    assert said["error"]["code"] == "approval_not_found"
    assert "no approval is waiting under this id" in said["error"]["message"].lower()
    assert "expired" not in said["error"]["message"].split(":")[0]
