"""A file the agent sends lands in the chat whose turn sent it, and in no other.

Measured from the code: ``POST /api/outbox/notify``, which the ``notify_attachment`` tool calls
with the calling chat's key, ignored that key. It wrote the file's row into whichever open chat had
the newest message, which can be another conversation (a channel thread with another person), and
broadcast a frame nothing read, so the file showed in neither chat though the agent said it was
sent. Its audit rows named no chat at all.

Driven through the real route, with the gateway's chats and the internal secret's headers as the
tool server sends them.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from personalclaw.dashboard.handlers.files import api_outbox_notify
from personalclaw.subagent import SubagentInfo, SubagentManager

pytestmark = pytest.mark.asyncio

#: The headers the tool server sends with each internal call (``mcp_core._internal_headers``).
AS_A = {"X-Internal-Secret": "the-gateways-own", "X-Session-Key": "dashboard:chat-a"}


@pytest.fixture
def sel():
    with patch("personalclaw.sel.sel") as m:
        m.return_value = MagicMock()
        yield m.return_value


@pytest.fixture
def outbox(tmp_path):
    d = tmp_path / "outbox"
    d.mkdir()
    with patch("personalclaw.config.loader.outbox_dir", return_value=d):
        yield d


def _two_chats(tmp_path):
    """Chat A, the one working, and chat B, a channel thread with the newer message."""
    state = _make_state(tmp_path)
    state.broadcast_ws = MagicMock()
    a = state.get_or_create_session("chat-a")
    a.append("user", "export this as a CSV and send it to me", broadcast=False)
    b = state.get_or_create_session("chat-b")
    b.append("user", "thanks, see you tomorrow", broadcast=False)
    b.messages[-1]["ts"] = "9999-12-31T00:00:00"  # the newest message of any chat
    return state, a, b


async def _notify(state, outbox, headers: dict, *, app_token: str = "") -> None:
    """Post the notice as the tool server does; with *app_token*, as the auth middleware admits
    an app's token (``request["app"]``, which ``approval_answer.of_request`` reads first)."""
    sent = outbox / "export.csv"
    sent.write_text("name,total\nLisbon,3\n")

    @web.middleware
    async def as_the_app(request, handler):
        if app_token:
            request["app"] = app_token
        return await handler(request)

    app = web.Application(middlewares=[as_the_app])
    app["state"] = state
    app.router.add_post("/api/outbox/notify", api_outbox_notify)
    body = {"filename": "export.csv", "path": str(sent), "description": "the export", "size": 21}
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/outbox/notify", json=body, headers=headers)
        assert resp.status == 200, await resp.text()


def _files(session) -> list[dict]:
    return [json.loads(m["content"]) for m in session.messages if m["role"] == "file"]


async def test_the_file_lands_in_the_chat_that_sent_it(tmp_path, outbox, sel):
    """🔴 Red before: it landed in chat B, whose message was newest, and chat A had none."""
    state, a, b = _two_chats(tmp_path)

    await _notify(state, outbox, AS_A)

    assert [f["filename"] for f in _files(a)] == ["export.csv"]
    assert _files(b) == []


async def test_a_file_no_chat_sent_lands_in_none(tmp_path, outbox, sel):
    """🔴 Red before: with no chat named it still went to the chat with the newest message."""
    state, a, b = _two_chats(tmp_path)

    await _notify(state, outbox, {"X-Internal-Secret": "the-gateways-own"})
    await _notify(state, outbox, {})

    assert _files(a) == [] and _files(b) == []


async def test_an_apps_notice_lands_in_no_chat_whatever_chat_it_names(tmp_path, outbox, sel):
    """An app's token is the app's own work (``app:<name>``), whatever chat the request names in
    ``X-Session-Key``: its file is shown in none of your chats, and its audit row names the app."""
    state, a, b = _two_chats(tmp_path)

    await _notify(state, outbox, {"X-Session-Key": "dashboard:chat-a"}, app_token="demo-app")

    assert _files(a) == [] and _files(b) == []
    assert not [c for c in state.broadcast_ws.call_args_list if c.args[0] == "file_ready"]
    assert sel.log_tool_invocation.call_args.kwargs["session_key"] == "app:demo-app"


async def test_a_helpers_file_lands_in_the_chat_it_works_for(tmp_path, outbox, sel):
    """🔴 Red before: a helper's file went to the newest chat too."""
    state, a, b = _two_chats(tmp_path)
    state.subagents = SubagentManager(sessions=MagicMock(), ctx_builder=None)
    state.subagents._agents["a1b2c3"] = SubagentInfo(
        id="a1b2c3", task="Export the totals", parent_session_key="dashboard:chat-a"
    )

    await _notify(
        state,
        outbox,
        {"X-Internal-Secret": "the-gateways-own", "X-Session-Key": "subagent:a1b2c3"},
    )

    assert [f["filename"] for f in _files(a)] == ["export.csv"]
    assert _files(b) == []


async def test_the_live_frame_and_the_audit_row_name_the_chat(tmp_path, outbox, sel):
    """🔴 Red before: the frame named no chat, so no page could tell whose file it was, and the
    audit row read ``api``."""
    state, a, _b = _two_chats(tmp_path)

    await _notify(state, outbox, AS_A)

    frames = [c.args[1] for c in state.broadcast_ws.call_args_list if c.args[0] == "file_ready"]
    assert [(f["session"], f["filename"]) for f in frames] == [(a.key, "export.csv")]
    audit = sel.log_tool_invocation.call_args.kwargs
    assert (audit["session_key"], audit["outcome"]) == ("dashboard:chat-a", "completed")


async def test_the_tools_answer_says_where_the_file_shows():
    """🔴 Red before: the agent said "File sent" whether or not any chat showed the file."""
    from personalclaw import mcp_core

    assert mcp_core._where_it_shows({"ok": True, "shown": True}) == "; it shows in this chat"
    assert "in the outbox" in mcp_core._where_it_shows({"ok": True, "shown": False})
    assert mcp_core._where_it_shows({"ok": True}) == ""
