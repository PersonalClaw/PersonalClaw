"""A chat linked to a channel thread tells that thread of a turn only where it may.

A chat linked to a channel thread used to post every turn that did not come from the thread into
it ("From the dashboard: …"), with its progress and its answer: the owner's own messages from the
dashboard, whose answers read her memory and her Inbox, and internal turns too (a helper's report
handed back, a scheduled run's result). In a channel or a group, everyone in the conversation read
them, though a colleague's own turn there is no longer given any of it.

Now the thread hears a turn its own message started, as its answer. Any other turn is told there
only when it is the owner's own message from the dashboard and the thread is her own DM with
PersonalClaw, which nobody else reads. A channel's conversation, a group's, and one whose readers
can't be told stay unposted. A helper's report is answered in the thread only when the turn that
started the helper came from that thread's own message, or the thread is her own DM; else its
reply goes to her DM, and stays in the dashboard when nothing reaches her. Any other internal turn
is told nowhere.

Driven as the turn drives it: the real gateway over a scratch home, the door a channel's message
crosses, and the real turn engine on a native runtime whose model is scripted, with the channel's
handle connected and hearing everything sent to it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from test_a_standing_grant_answers_only_the_owners_own_asks import (  # noqa: F401 — autouse
    THREAD,
    _a_shared_channel,
    _gateway,
)
from test_a_turn_someone_else_started_changes_no_memory_on_its_own import (
    CHANNEL,
    OWNER,
    PROVIDER,
    _settled,
)

from personalclaw import channel_delivery
from personalclaw.turn_source import DASHBOARD_SOURCE, STARTED_ON, arrived_on

#: The owner's own DM with PersonalClaw on the channel, as the channel opens it for her.
HER_DM = "D0OWNERDM1"
#: What a helper's report to her held.
REPORT = "Helper report: the landlord's number is 555-0142"
#: What she writes in the chat on the dashboard.
NOTE = "Remind me to book the therapist for Thursday."
#: A thread in her own DM, where a chat was before it moved to the shared thread.
DM_THREAD = "1790800000.000555"
COLLEAGUE = "U0JONASCOL"


class _Thread:
    """The channel's handle: it opens *dm* as the owner's DM (or fails to, when *dm* is an
    exception), and hears everything sent to it."""

    def __init__(self, dm: object) -> None:
        self.handle = MagicMock()
        if isinstance(dm, BaseException):
            self.handle.open_dm = AsyncMock(side_effect=dm)
        else:
            self.handle.open_dm = AsyncMock(return_value=dm)
        self.handle.deliver_text = AsyncMock(return_value="")
        self.handle.deliver_chat_mirror = AsyncMock()
        self.handle.start_stream = AsyncMock(return_value="")
        self.handle.append_stream_task = AsyncMock()
        self.handle.stop_stream = AsyncMock()
        self.handle.deliver_subagent_reply = AsyncMock()

    def replied_in(self) -> list[str]:
        """Each conversation a helper's reply was sent to apart from the thread's mirror: her DM."""
        return [c.args[0] for c in self.handle.deliver_subagent_reply.await_args_list]

    def heard(self) -> list[str]:
        """Everything posted to the channel, in the order it was sent: texts, answers and
        progress streams."""
        posts = ("deliver_text", "deliver_chat_mirror", "start_stream")
        return [f"{name}: {a} {kw}" for name, a, kw in self.handle.mock_calls if name in posts]


@pytest.fixture
def thread():
    """The channel connected, its owner's DM a conversation other than the shared thread."""
    t = _Thread(HER_DM)
    channel_delivery.register(t.handle, provider=PROVIDER)
    try:
        yield t
    finally:
        channel_delivery.register(None, provider=PROVIDER)


async def _a_linked_chat(gw):
    """The chat the door opened for her message on the thread, its answer posted there."""
    gw.agent.calls.append([])
    await gw.say(OWNER, "Morning! I'll keep the offsite notes in this thread.")
    return gw.chat()


async def _run(gw, chat, role: str, text: str, meta: dict | None = None) -> None:
    """A turn *chat* runs on a *role* row of *text* (and *meta*), as the dashboard or the gateway
    starts one."""
    from personalclaw.dashboard.chat_runner import run_chat

    chat.append(role, text, meta=meta)
    gw.agent.calls.append([])
    await run_chat(gw.state, chat, text)
    await _settled(gw.state)


@pytest.mark.asyncio
async def test_a_turn_its_own_message_started_is_answered_on_the_thread(
    tmp_path, monkeypatch, thread
):
    """The control: her message on the thread is answered there."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        await _a_linked_chat(gw)
    assert any("Noted." in post for post in thread.heard()), thread.heard()


async def _report(gw, chat, started_on: dict[str, str] | None) -> None:
    """A helper's report handed back to *chat*, its start recorded as *started_on* (no record
    at all for ``None``), as the gateway hands one back (``subagent_endings.report_meta``)."""
    from personalclaw.dashboard.state import SUBAGENT_COMPLETION_PREFIX

    meta = {STARTED_ON: started_on} if started_on is not None else None
    await _run(gw, chat, "subagent", f"{SUBAGENT_COMPLETION_PREFIX}\n{REPORT}", meta)


@pytest.mark.asyncio
async def test_a_helpers_report_is_not_posted_to_the_thread(tmp_path, monkeypatch, thread):
    """🔴 Red before: the report's turn was posted to the thread, the report and its answer. A
    start nothing records is not the thread's: its reply goes to her DM."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()
        await _report(gw, chat, None)
    assert thread.heard() == before
    assert thread.replied_in() == [HER_DM]


@pytest.mark.asyncio
async def test_a_helper_its_thread_started_is_answered_in_the_thread(tmp_path, monkeypatch, thread):
    """🔴 Red before: a helper a colleague asked for in the thread had its reply posted nowhere
    on the channel. The work was asked for there, whoever asked."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()
        await _report(gw, chat, arrived_on(THREAD, COLLEAGUE, PROVIDER))
    posted = thread.heard()[len(before) :]
    assert any(post.startswith("deliver_chat_mirror") for post in posted), posted
    assert thread.replied_in() == []


@pytest.mark.asyncio
async def test_the_turn_engine_records_the_thread_a_helpers_work_was_asked_for_on(
    tmp_path, monkeypatch, thread
):
    """The turn of her message on the thread records where it came from (``run_chat`` →
    ``memory_writes.started_on``): a helper that turn starts carries the thread's source, and the
    reply to its report is answered in the thread. With no start recorded, the reply goes to her DM
    instead, which costs the thread its answer."""
    from personalclaw import memory_writes
    from personalclaw.dashboard.state import SUBAGENT_COMPLETION_PREFIX
    from personalclaw.subagent import SubagentInfo
    from personalclaw.subagent_endings import report_meta

    seen: dict[str, dict[str, str]] = {}
    async with _gateway(tmp_path, monkeypatch) as gw:
        real = gw.agent.complete

        def complete(*args, **kwargs):
            # What a helper spawned now records (``SubagentManager.spawn``): the turn's start.
            chat = gw.state.get_linked_session(THREAD)
            if chat is not None and "origin" not in seen:
                seen["origin"] = memory_writes.origin_for(chat.key)
            return real(*args, **kwargs)

        monkeypatch.setattr(gw.agent, "complete", complete)
        chat = await _a_linked_chat(gw)
        assert seen.get("origin") == arrived_on(THREAD, OWNER, PROVIDER), seen
        before = thread.heard()
        helper = SubagentInfo(
            id="h1e2l3p4", task="Find the offsite venue", started_on=seen["origin"]
        )
        report = f"{SUBAGENT_COMPLETION_PREFIX}\n{REPORT}"
        await _run(gw, chat, "subagent", report, report_meta({}, [helper]))
    posted = thread.heard()[len(before) :]
    assert any(post.startswith("deliver_chat_mirror") for post in posted), posted
    assert thread.replied_in() == []


@pytest.mark.asyncio
async def test_a_helper_started_from_the_dashboard_answers_in_her_dm(tmp_path, monkeypatch, thread):
    """🔴 Red before: the reply to work she started from the dashboard reached her nowhere off the
    dashboard. It goes to her DM, never into the shared thread."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()
        await _report(gw, chat, dict(DASHBOARD_SOURCE))
    assert thread.heard() == before
    assert thread.replied_in() == [HER_DM]


@pytest.mark.asyncio
async def test_with_no_dm_a_helpers_reply_stays_in_the_dashboard(tmp_path, monkeypatch, thread):
    """With no DM that reaches her, the reply is in the dashboard chat and posted nowhere."""
    thread.handle.open_dm = AsyncMock(side_effect=ConnectionError("the channel did not answer"))
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()
        await _report(gw, chat, dict(DASHBOARD_SOURCE))
    assert thread.heard() == before
    assert thread.replied_in() == []


@pytest.mark.asyncio
async def test_a_helper_started_in_her_dm_before_a_move_answers_there(
    tmp_path, monkeypatch, thread
):
    """A chat moved after its helper started is judged by where the starting message came from:
    her DM, so the shared thread it is in now is shown none of it. 🔴 Red before: the reply
    reached her nowhere off the dashboard."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()
        await _report(gw, chat, arrived_on(DM_THREAD, OWNER, PROVIDER))
    assert thread.heard() == before
    assert thread.replied_in() == [HER_DM]


@pytest.mark.asyncio
async def test_a_scheduled_result_is_not_posted_to_the_thread(tmp_path, monkeypatch, thread):
    """🔴 Red before: the inject's turn was posted to the thread."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()
        await _run(gw, chat, "inject", "Scheduled digest: 3 new invoices from the landlord")
    assert thread.heard() == before


@pytest.mark.asyncio
async def test_her_dashboard_turn_stays_out_of_a_shared_thread(tmp_path, monkeypatch, thread):
    """🔴 Red before: her note from the dashboard, its progress and its answer were posted to the
    thread everyone in the channel reads."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()
        await _run(gw, chat, "user", NOTE)
    assert thread.heard() == before
    thread.handle.open_dm.assert_awaited()


@pytest.mark.asyncio
async def test_her_dashboard_turn_is_mirrored_into_her_own_dm(tmp_path, monkeypatch, thread):
    """The control: in her own DM, which nobody else reads, her note and its answer are posted as
    they always were."""
    thread.handle.open_dm = AsyncMock(return_value=CHANNEL)
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()
        await _run(gw, chat, "user", NOTE)
    posted = thread.heard()[len(before) :]
    assert any(f"From the dashboard: _{NOTE}_" in post for post in posted), posted
    assert any(post.startswith("deliver_chat_mirror") for post in posted), posted


@pytest.mark.asyncio
async def test_a_thread_whose_readers_cant_be_told_is_shared(tmp_path, monkeypatch, thread):
    """🔴 Red before: her note was posted whatever the conversation; now one whose readers the
    channel can't say is read as shared."""
    thread.handle.open_dm = AsyncMock(side_effect=ConnectionError("the channel did not answer"))
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()
        await _run(gw, chat, "user", NOTE)
    assert thread.heard() == before


@pytest.mark.asyncio
async def test_her_dashboard_turn_that_fails_says_nothing_in_a_shared_thread(
    tmp_path, monkeypatch, thread
):
    """A turn the thread was not told of ends without a word there either. 🔴 Red before: the
    shared thread was told her dashboard turn failed, and why."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()

        async def _fails(*_a, **_k):
            raise RuntimeError("the model refused the request")
            yield  # pragma: no cover - makes this an async generator

        monkeypatch.setattr(gw.agent, "complete", _fails)
        chat.append("user", NOTE)
        from personalclaw.dashboard.chat_runner import run_chat

        await run_chat(gw.state, chat, NOTE)
        await _settled(gw.state)
    assert thread.heard() == before


@pytest.mark.asyncio
async def test_the_rows_see_a_mirror_that_lets_an_internal_turn_through(
    tmp_path, monkeypatch, thread
):
    """The plant: with the rule that an internal turn is no owner's dashboard turn taken out, a
    scheduled result's turn is posted again, and the rows above see it."""
    from personalclaw.dashboard import chat_runner

    monkeypatch.setattr(chat_runner, "_the_owners_dashboard_turn", lambda _row: True)
    thread.handle.open_dm = AsyncMock(return_value=CHANNEL)
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()
        await _run(gw, chat, "inject", "Scheduled digest: 3 new invoices from the landlord")
    assert thread.heard() != before


@pytest.mark.asyncio
async def test_the_rows_see_a_reply_rule_that_drops_the_origin_check(tmp_path, monkeypatch, thread):
    """The plant: with where the helper's start came from no longer asked, the reply to work she
    started from the dashboard is posted into the shared thread, and the rows above see it."""
    from personalclaw.dashboard import chat_runner

    monkeypatch.setattr(chat_runner, "came_from_thread", lambda *_a: True)
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()
        await _report(gw, chat, dict(DASHBOARD_SOURCE))
    assert thread.heard() != before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dm", "shared"), [(HER_DM, True), (CHANNEL, False)], ids=["a channel", "her own DM"]
)
async def test_the_chat_says_whether_its_thread_is_shared(
    tmp_path, monkeypatch, thread, dm, shared
):
    """What the chat's opening tells the composer (``shared_thread``), which says above it that
    what she writes here stays here. 🔴 Red before: the chat said nothing of it."""
    from personalclaw.dashboard.chat_handlers import _linked_to_a_shared_thread

    thread.handle.open_dm = AsyncMock(return_value=dm)
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        assert await _linked_to_a_shared_thread(gw.state, chat.key) is shared
        own = gw.state.get_or_create_session("chat-on-no-thread")
        assert await _linked_to_a_shared_thread(gw.state, own.key) is False


@pytest.mark.asyncio
async def test_a_helpers_report_is_answered_in_her_own_dm(tmp_path, monkeypatch, thread):
    """In her own DM, which nobody else reads, the reply to work she started from the dashboard
    is posted in the chat's thread there. 🔴 Red before: it was posted nowhere."""
    thread.handle.open_dm = AsyncMock(return_value=CHANNEL)
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()
        await _report(gw, chat, dict(DASHBOARD_SOURCE))
    posted = thread.heard()[len(before) :]
    assert any(post.startswith("deliver_chat_mirror") for post in posted), posted
    assert not any(REPORT in post for post in posted), "the report itself is not posted"


@pytest.mark.asyncio
async def test_a_channel_that_never_says_whose_dm_it_is_holds_no_turn(
    tmp_path, monkeypatch, thread
):
    """The check of whose DM the thread is waits a bounded time: a channel that never answers holds
    her turn no longer than that, and the turn stays in the dashboard. 🔴 Red before: the turn
    waited on the channel for as long as it took."""
    import asyncio

    async def _never(_owner):
        await asyncio.Event().wait()

    import time

    thread.handle.open_dm = AsyncMock(side_effect=_never)
    monkeypatch.setattr(channel_delivery, "AUDIENCE_WAIT_SECS", 0.05)
    # The check itself gives up within a small multiple of its wait, saying the thread is shared.
    began = time.monotonic()
    assert not await asyncio.wait_for(
        channel_delivery.is_the_owners_own_dm(
            PROVIDER, CHANNEL, thread.handle, wait=channel_delivery.AUDIENCE_WAIT_SECS
        ),
        timeout=1,
    )
    assert time.monotonic() - began < 20 * channel_delivery.AUDIENCE_WAIT_SECS
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()
        began = time.monotonic()
        await asyncio.wait_for(_run(gw, chat, "user", NOTE), timeout=20)
        took = time.monotonic() - began
    assert thread.heard() == before
    thread.handle.open_dm.assert_awaited()
    # Bounded by the check's wait, not by an outer limit that ends a turn some 30 s on.
    assert took < 5, f"her turn waited {took:.1f} s on a channel that never answered"


#: What a compaction says of a conversation in the chat: the model's account of it.
SUMMARY = "Her therapist moved Thursday's session to four, and the offsite is in Lisbon."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dm", "summarised"),
    [(HER_DM, False), (CHANNEL, True), (ConnectionError("no answer"), False)],
    ids=["a shared thread", "her DM", "an audience that can't be read"],
)
async def test_a_compactions_summary_goes_only_to_her_own_dm(
    tmp_path, monkeypatch, thread, dm, summarised
):
    """A compaction's summary is the model's account of the whole conversation: a thread others
    read is told only that the conversation was compacted. 🔴 Red before: every linked thread was
    posted the summary."""
    from personalclaw.dashboard.chat_utils import _broadcast_compaction_result
    from personalclaw.llm.events import EVENT_COMPACTION_STATUS, AgentEvent

    if isinstance(dm, BaseException):
        thread.handle.open_dm = AsyncMock(side_effect=dm)
    else:
        thread.handle.open_dm = AsyncMock(return_value=dm)
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()
        await _broadcast_compaction_result(
            gw.state,
            chat,
            AgentEvent(kind=EVENT_COMPACTION_STATUS, text="completed", title=SUMMARY),
        )
    posted = " ".join(thread.heard()[len(before) :])
    assert ("Lisbon" in posted) is summarised, posted
    assert "Conversation compacted" in posted


async def _moved_by(gw, route: str, body: dict) -> None:
    """The dashboard's own link or handoff handler, as the chat's menu calls it."""
    from unittest.mock import patch

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.chat_channel import (
        api_chat_session_channel_link,
        api_chat_session_handoff,
    )
    from personalclaw.dashboard.request_boundary import request_boundary_middleware

    chat = gw.state.get_or_create_session("chat-on-the-dashboard")
    chat.append("user", NOTE, "msg msg-u")
    chat.append("assistant", "Booked: Thursday at four.", "msg msg-a")
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    save_session_to_history(gw.state, chat, force=True)
    app = web.Application(middlewares=[request_boundary_middleware()])
    app.router.add_post("/api/chat/sessions/{session}/channel-link", api_chat_session_channel_link)
    app.router.add_post("/api/chat/sessions/{session}/handoff", api_chat_session_handoff)
    app["state"] = gw.state
    with patch("personalclaw.dashboard.chat_channel.sel", MagicMock()):
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(f"/api/chat/sessions/{chat.key}/{route}", json=body)
            assert resp.status == 200, await resp.text()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["channel-link", "handoff"])
@pytest.mark.parametrize(
    ("dm", "shared"), [(HER_DM, True), (CHANNEL, False)], ids=["a channel", "her DM"]
)
async def test_a_chat_moved_to_a_thread_others_read_posts_none_of_it_there(
    tmp_path, monkeypatch, thread, route, dm, shared
):
    """A link or handoff of a dashboard chat into a thread others read opens it saying only that a
    conversation continues there: not its title, not her last message, not the messages a link
    posts as context. Into her own DM, as before. 🔴 Red before: the shared thread was posted them.
    """
    thread.handle.open_dm = AsyncMock(return_value=dm)
    thread.handle.deliver_text = AsyncMock(return_value="1790800000.000999")
    async with _gateway(tmp_path, monkeypatch) as gw:
        await _moved_by(gw, route, {"provider": PROVIDER, "channel": CHANNEL})
    posted = " ".join(thread.heard())
    assert ("therapist" in posted or "Thursday" in posted) is not shared, posted
    assert ("Session linked" in posted) or ("continues here" in posted) or not shared, posted


# ── whose DM it is ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("opened", "conversation", "hers"),
    [
        (HER_DM, HER_DM, True),
        (HER_DM, CHANNEL, False),
        ("", HER_DM, False),
        (ConnectionError("no answer"), HER_DM, False),
    ],
    ids=["her own DM", "a channel", "no DM opened", "the channel did not answer"],
)
async def test_her_own_dm_is_the_one_the_channel_opens_for_her(opened, conversation, hers):
    from personalclaw.channel_delivery import is_the_owners_own_dm

    t = _Thread(opened)
    assert await is_the_owners_own_dm(PROVIDER, conversation, t.handle) is hers


@pytest.mark.asyncio
async def test_a_channel_not_connected_or_with_no_owner_has_no_dm_of_hers(monkeypatch):
    from personalclaw.channel_delivery import is_the_owners_own_dm
    from personalclaw.config import credentials

    assert await is_the_owners_own_dm(PROVIDER, HER_DM) is False, "not connected"
    monkeypatch.setattr(credentials, "owner_id_for", lambda _provider: "")
    t = _Thread(HER_DM)
    assert await is_the_owners_own_dm(PROVIDER, HER_DM, t.handle) is False, "no owner kept"
    t.handle.open_dm.assert_not_awaited()
