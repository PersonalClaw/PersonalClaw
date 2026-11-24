"""The triage digest's channel section reports a channel conversation that waits, by its counts.

🔴 The defect (Backlog: "The triage digest's channel section never reports a channel conversation").
The collector took a session whose key starts with ``channel:``, but no chat has one: a message
through the channel door opens an ordinary chat (``chat-<n>-<s>``) and links it to its thread
(``channel_links.chat_link``). So the section was always empty on a real gateway, while the digest
read as if it had checked; its test passed because it built sessions under made-up ``channel:``
keys. It also put the waiting message's words in the item.

Now the lane finds a chat by its channel link, and an item carries counts and the title, never
message text: the chat's title, its channel, how many messages wait since its last answer and how
long. What the digest collects reaches its model and its run's record, which other chats' agents
can read. A Temporary or Incognito chat is not listed.

Every message below arrives through the real door (``channel_inbound.deliver_inbound``), into the
real dashboard state and session store, in this test's home.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from personalclaw import channel_inbound as ci
from personalclaw import channel_trust as ct
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard import chat_names
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.proactive.collect import collect_all, collect_channels, current_item
from personalclaw.proactive.manifest import SOURCE_CHANNEL
from personalclaw.session import SessionManager

PROVIDER = "discord"
DM = "880000000000000001"
ADA = "770000000000000002"
#: What Ada writes: none of its words may reach the digest.
SECRET = "my card number is 4111 1111 1111 1111, please book it"


@pytest.fixture(autouse=True)
def _a_trusted_sender():
    """Ada is let in."""
    ct.allow_sender(PROVIDER, ADA, name="Ada")
    yield


class _Gateway:
    """The gateway's session store and dashboard state over this test's home, and the door its
    channels deliver to (``GatewayServices.deliver_channel_inbound``)."""

    def __init__(self, home: Path) -> None:
        self.sessions: Any = SessionManager(AppConfig())
        self.dashboard_state = DashboardState(
            sessions=self.sessions,
            start_time=0.0,
            conversation_log=ConversationLog(base_dir=home / "sessions"),
        )
        self._count = 0

    @property
    def state(self) -> DashboardState:
        return self.dashboard_state

    async def message(self, text: str, *, answered: bool) -> str:
        """Ada sends *text* in the DM; the turn it starts answers it, or not (it waits, or its
        reply failed). Returns the chat the door handed it to."""
        reached: list[str] = []

        async def _turn(state: Any, session: Any, message: str) -> None:
            reached.append(session.key)
            if answered:
                session.append("assistant", "Booked.", "msg msg-a")

        self._count += 1
        msg = ChannelMessage(
            channel_id=DM, text=text, sender=ADA, thread_id=DM, message_id=f"m-{self._count}"
        )
        verdict = await ci.deliver_inbound(self, PROVIDER, msg, is_dm=True, turn_runner=_turn)
        assert verdict.allowed, verdict
        for _ in range(50):
            running = {t for t in self.state._background_tasks if not t.done()}
            if not running:
                break
            await asyncio.wait(running, timeout=10)
        assert reached, "the door ran no turn"
        return reached[-1]


def _items(gateway: _Gateway) -> list[Any]:
    return collect_channels(gateway.state)


@pytest.mark.asyncio
async def test_a_channel_chat_whose_message_waits_is_reported(tmp_path):
    """🔴 Before: the chat is named `chat-…`, not `channel:…`, so the lane reported nothing."""
    gateway = _Gateway(tmp_path)
    chat = await gateway.message(SECRET, answered=False)
    assert not chat.startswith("channel:"), chat
    (item,) = _items(gateway)
    assert (item.source, item.source_id) == (SOURCE_CHANNEL, chat)
    assert item.title.startswith("unanswered in ")
    assert item.detail.startswith("1 message waiting on "), item.detail
    assert item.detail.endswith(", for under an hour"), item.detail


#: The second a merge-queue run opened its chat in (`chat-1-1795512322`), whose digits hold "123".
QUEUE_RUN_SECOND = 1795512322.0


@pytest.mark.asyncio
@pytest.mark.parametrize("second", [None, QUEUE_RUN_SECOND], ids=["now", "a second holding 123"])
async def test_no_channel_item_carries_what_the_message_says(tmp_path, monkeypatch, second):
    """🔴 Before: an item's `detail` was the waiting message's text. Now no word of it reaches the
    digest, whose run's record other chats' agents can read. Whatever second the chat opened in:
    🔴 the check read the chat's own name too, and failed in every second whose digits hold 123."""
    if second is not None:
        monkeypatch.setattr(chat_names, "time", SimpleNamespace(time=lambda: second))
    gateway = _Gateway(tmp_path)
    await gateway.message(SECRET, answered=False)
    await gateway.message("and the CVV is 123", answered=False)
    (item,) = _items(gateway)
    # The chat's own name is the door's, not her words: an untitled chat is named by the second it
    # opened (`chat-1-1795512322`), whose digits hold "123" about one second in 125.
    said = f"{item.title.removesuffix(item.source_id)} {item.detail}".lower()
    for word in ("card", "4111", "book", "cvv", "123"):
        assert word not in said, (word, item)
    assert item.detail.startswith("2 messages waiting on "), item.detail


@pytest.mark.asyncio
async def test_a_channel_chat_whose_last_message_was_answered_is_not_reported(tmp_path):
    gateway = _Gateway(tmp_path)
    await gateway.message(SECRET, answered=True)
    assert _items(gateway) == []


@pytest.mark.asyncio
async def test_a_temporary_or_incognito_channel_chat_is_not_reported(tmp_path):
    gateway = _Gateway(tmp_path)
    chat = await gateway.message(SECRET, answered=False)
    for mode in ("temporary", "incognito"):
        gateway.state._sessions[chat].memory_mode = mode
        assert _items(gateway) == [], mode


@pytest.mark.parametrize("marked", ["the thread", "the chat"])
@pytest.mark.parametrize("mode", ["temporary", "incognito"])
@pytest.mark.asyncio
async def test_a_channel_chat_the_channel_made_private_is_not_reported(tmp_path, mode, marked):
    """A channel app makes its thread Temporary or Incognito in core's registry
    (`session_restrictions`), under the thread's key, while the chat object still says persistent:
    the lane asks the one reader of a session's mode (`memory_writes.session_mode`), under the
    thread's key and the chat's, so neither its title nor its count reaches the digest."""
    from personalclaw import session_restrictions
    from personalclaw.constants import dashboard_history_key
    from personalclaw.dashboard.channel_links import chat_link

    gateway = _Gateway(tmp_path)
    chat = await gateway.message(SECRET, answered=False)
    assert gateway.state._sessions[chat].memory_mode == "persistent"
    thread, _channel = chat_link(gateway.state, chat)
    assert thread
    mark = getattr(session_restrictions, f"mark_{mode}")
    mark(thread if marked == "the thread" else dashboard_history_key(chat))
    assert _items(gateway) == []
    assert current_item(SOURCE_CHANNEL, chat, state=gateway.state) is None


@pytest.mark.asyncio
async def test_a_chat_on_no_channel_is_not_in_the_channel_section(tmp_path):
    """A dashboard chat whose last message waits is no channel conversation."""
    gateway = _Gateway(tmp_path)
    session = gateway.state.get_or_create_session("chat-typed-here")
    session.append("user", SECRET, "msg msg-u")
    assert _items(gateway) == []


@pytest.mark.asyncio
async def test_a_carried_over_channel_item_is_read_again_the_same_way(tmp_path):
    """`current_item` re-reads a carried-over item by the same link test: the same item while it
    waits, and gone once the chat is answered."""
    gateway = _Gateway(tmp_path)
    chat = await gateway.message(SECRET, answered=False)
    (item,) = _items(gateway)
    again = current_item(SOURCE_CHANNEL, chat, state=gateway.state)
    assert again is not None and (again.title, again.detail) == (item.title, item.detail)
    await gateway.message("thanks", answered=True)
    assert current_item(SOURCE_CHANNEL, chat, state=gateway.state) is None


@pytest.mark.asyncio
async def test_the_digest_collects_the_channel_lane(tmp_path):
    """What the digest's collect step gathers (`collect_all`) holds the waiting chat."""
    gateway = _Gateway(tmp_path)
    chat = await gateway.message(SECRET, answered=False)
    got = collect_all(state=gateway.state, include_runs=False)
    assert [(i.source, i.source_id) for i in got] == [(SOURCE_CHANNEL, chat)]
