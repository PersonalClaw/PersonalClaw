"""A chat linked to a channel thread tells that thread of a turn only where it may.

A chat linked to a channel thread used to post every turn that did not come from the thread into
it ("From the dashboard: …"), with its progress and its answer: the owner's own messages from the
dashboard, whose answers read her memory and her Inbox, and internal turns too (a helper's report
handed back, a scheduled run's result). In a channel or a group, everyone in the conversation read
them, though a colleague's own turn there is no longer given any of it.

Now the thread hears a turn its own message started, as its answer. Any other turn is told there
only when it is the owner's own message from the dashboard and the thread is her own DM with
PersonalClaw, which nobody else reads. A channel's conversation, a group's, and one whose readers
can't be told stay unposted, and an internal turn is told nowhere.

Driven as the turn drives it: the real gateway over a scratch home, the door a channel's message
crosses, and the real turn engine on a native runtime whose model is scripted, with the channel's
handle connected and hearing everything sent to it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from test_a_standing_grant_answers_only_the_owners_own_asks import (  # noqa: F401 — autouse
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

#: The owner's own DM with PersonalClaw on the channel, as the channel opens it for her.
HER_DM = "D0OWNERDM1"
#: What a helper's report to her held.
REPORT = "Helper report: the landlord's number is 555-0142"
#: What she writes in the chat on the dashboard.
NOTE = "Remind me to book the therapist for Thursday."


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


async def _run(gw, chat, role: str, text: str) -> None:
    """A turn *chat* runs on a *role* row of *text*, as the dashboard or the gateway starts one."""
    from personalclaw.dashboard.chat_runner import run_chat

    chat.append(role, text)
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


@pytest.mark.asyncio
async def test_a_helpers_report_is_not_posted_to_the_thread(tmp_path, monkeypatch, thread):
    """🔴 Red before: the report's turn was posted to the thread, the report and its answer."""
    from personalclaw.dashboard.state import SUBAGENT_COMPLETION_PREFIX

    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()
        await _run(gw, chat, "subagent", f"{SUBAGENT_COMPLETION_PREFIX}\n{REPORT}")
    assert thread.heard() == before


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
    """The plant: with the rule that an internal turn is no owner's dashboard turn taken out, the
    report's turn is posted again, and the rows above see it."""
    from personalclaw.dashboard import chat_runner
    from personalclaw.dashboard.state import SUBAGENT_COMPLETION_PREFIX

    monkeypatch.setattr(chat_runner, "_the_owners_dashboard_turn", lambda _row: True)
    thread.handle.open_dm = AsyncMock(return_value=CHANNEL)
    async with _gateway(tmp_path, monkeypatch) as gw:
        chat = await _a_linked_chat(gw)
        before = thread.heard()
        await _run(gw, chat, "subagent", f"{SUBAGENT_COMPLETION_PREFIX}\n{REPORT}")
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
    from personalclaw.dashboard.channel_links import is_the_owners_own_dm

    t = _Thread(opened)
    assert await is_the_owners_own_dm(PROVIDER, conversation, t.handle) is hers


@pytest.mark.asyncio
async def test_a_channel_not_connected_or_with_no_owner_has_no_dm_of_hers(monkeypatch):
    from personalclaw.config import credentials
    from personalclaw.dashboard.channel_links import is_the_owners_own_dm

    assert await is_the_owners_own_dm(PROVIDER, HER_DM) is False, "not connected"
    monkeypatch.setattr(credentials, "owner_id_for", lambda _provider: "")
    t = _Thread(HER_DM)
    assert await is_the_owners_own_dm(PROVIDER, HER_DM, t.handle) is False, "no owner kept"
    t.handle.open_dm.assert_not_awaited()
