"""A chat moved from the owner's DM into a shared thread shows a colleague's turn there nothing
said in her DM.

A chat she ran in her DM with PersonalClaw can be continued in a shared thread, by linking it to
a channel or by handing it off there. It is the same chat: her DM's messages carry the channel
that took them in, and the answers mirrored to her DM carry the shown mark. So a colleague's turn
in the thread was built from everything she said and was answered in her DM, of which the thread
was shown at most the few rows a link posts.

Now a colleague's turn is built from what its own thread showed (``chat_persistence.channel_view``
for the thread whoever asked is on): a message only when the channel took it in on that thread,
and an answer only when its mark names that thread. The rest is a stub.

Driven as the turn drives it: the real gateway over a scratch home, the door her DM's message and
his crossed, the dashboard's own link and handoff handlers, and the real turn engine on a native
runtime whose model is scripted, with the channel's handle connected.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from test_a_standing_grant_answers_only_the_owners_own_asks import (  # noqa: F401 — autouse
    _a_shared_channel,
    _gateway,
)
from test_a_turn_someone_else_started_changes_no_memory_on_its_own import (
    CHANNEL,
    COLLEAGUE,
    OWNER,
    PROVIDER,
    _settled,
)

from personalclaw import channel_delivery
from personalclaw import channel_inbound as ci
from personalclaw.channel_transports.base import ChannelMessage

#: Her own DM with PersonalClaw on the channel, where the chat ran first.
HER_DM = "D0OWNERDM1"
#: The shared thread the chat moves to, as the channel opens it.
THREAD = "1790800000.000100"
#: What she said in her DM, and what the answer there repeated.
PRIVATE = "therapy is at the Larkspur clinic at four"


@pytest.fixture(autouse=True)
def _the_channel_is_connected():
    """The channel's handle: her DM is the one it opens for her, a thread it opens is
    :data:`THREAD`, and it hears everything sent to it."""
    handle = MagicMock()
    handle.open_dm = AsyncMock(return_value=HER_DM)
    handle.deliver_text = AsyncMock(return_value=THREAD)
    handle.deliver_chat_mirror = AsyncMock()
    handle.start_stream = AsyncMock(return_value="")
    handle.append_stream_task = AsyncMock()
    handle.stop_stream = AsyncMock()
    channel_delivery.register(handle, provider=PROVIDER)
    try:
        yield handle
    finally:
        channel_delivery.register(None, provider=PROVIDER)


async def _in_her_dm(gw):
    """She writes in her DM, where the chat starts and its answer, which repeats her words, is
    mirrored; the chat the door opened."""
    from personalclaw.dashboard.chat_runner import run_chat

    gw.agent.answer = f"Noted: {PRIVATE}."
    gw.agent.calls.append([])
    msg = ChannelMessage(
        channel_id=HER_DM,
        text=f"Remind me: {PRIVATE}.",
        sender=OWNER,
        thread_id=HER_DM,
        message_id="m-dm-1",
    )
    verdict = await ci.deliver_inbound(gw, PROVIDER, msg, is_dm=True, turn_runner=run_chat)
    assert verdict.allowed, verdict
    await _settled(gw.state)
    gw.agent.answer = "Noted."
    chat = gw.state.get_linked_session(HER_DM)
    assert chat is not None, "the door linked no chat to her DM"
    return chat


async def _post(gw, path: str, body: dict) -> None:
    from personalclaw.dashboard.chat_channel import (
        api_chat_session_channel_link,
        api_chat_session_handoff,
    )
    from personalclaw.dashboard.chat_regenerate import (
        api_chat_session_edit_resend,
        api_chat_session_regenerate,
    )
    from personalclaw.dashboard.request_boundary import request_boundary_middleware

    app = web.Application(middlewares=[request_boundary_middleware()])
    app.router.add_post("/api/chat/sessions/{session}/channel-link", api_chat_session_channel_link)
    app.router.add_post("/api/chat/sessions/{session}/handoff", api_chat_session_handoff)
    app.router.add_post("/api/chat/sessions/{session}/regenerate", api_chat_session_regenerate)
    app.router.add_post("/api/chat/sessions/{session}/edit-resend", api_chat_session_edit_resend)
    app["state"] = gw.state
    with (
        patch("personalclaw.dashboard.chat_channel.sel", MagicMock()),
        patch("personalclaw.dashboard.chat_regenerate.sel", MagicMock()),
    ):
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(path, json=body)
            assert resp.status == 200, await resp.text()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "body"),
    [
        ("channel-link", {"provider": PROVIDER, "channel": CHANNEL}),
        ("handoff", {"provider": PROVIDER, "channel": CHANNEL}),
    ],
    ids=["linked to a channel", "handed off to a channel"],
)
async def test_a_colleague_in_the_thread_is_shown_nothing_from_her_dm(
    tmp_path, monkeypatch, route, body
):
    """🔴 Red before: his turn was built with her DM's message and the answer to it."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _in_her_dm(gw)
        assert PRIVATE in json.dumps(gw.agent.handed[-1]), "the control: her DM turn read it"
        await _post(gw, f"/api/chat/sessions/{chat.key}/{route}", body)
        assert gw.state.get_linked_session(THREAD) is chat, "the chat did not move to the thread"

        gw.agent.calls.append([])
        await gw.say(COLLEAGUE, "What's the plan for the offsite?")
        his = json.dumps(gw.agent.handed[-1])
        assert PRIVATE not in his and "Larkspur" not in his, his
        assert "A message from another thread this chat ran in was here" in his


def _posted(handle) -> list[str]:
    """Everything posted to the channel, in the order it was sent."""
    posts = ("deliver_text", "deliver_chat_mirror", "start_stream")
    return [f"{name}: {a} {kw}" for name, a, kw in handle.mock_calls if name in posts]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "body"),
    [("regenerate", {}), ("edit-resend", {"index": 0, "content": f"Remind me: {PRIVATE}!"})],
    ids=["regenerated", "edited and sent again"],
)
async def test_her_dm_message_run_again_after_the_move_is_not_posted_to_the_thread(
    tmp_path, monkeypatch, _the_channel_is_connected, route, body
):
    """Her DM message, run again from the dashboard once the chat is on the shared thread, is no
    message of that thread: nothing of its turn is posted there. A regenerate runs it on its own
    row, her DM's; an edit sends a new message from the dashboard. 🔴 Red before (the regenerate):
    a row of the thread's channel counted as the thread's own, whatever thread took it in."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _in_her_dm(gw)
        await _post(
            gw,
            f"/api/chat/sessions/{chat.key}/channel-link",
            {"provider": PROVIDER, "channel": CHANNEL},
        )
        before = _posted(_the_channel_is_connected)
        gw.agent.calls.append([])
        await _post(gw, f"/api/chat/sessions/{chat.key}/{route}", body)
        await _settled(gw.state)
        assert len(gw.agent.handed) >= 2, "the control: her message was run again"
        posted = _posted(_the_channel_is_connected)[len(before) :]
    assert not [p for p in posted if CHANNEL in p], posted
