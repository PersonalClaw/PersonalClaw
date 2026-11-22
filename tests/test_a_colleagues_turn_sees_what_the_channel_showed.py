"""A colleague's turn in a shared thread is built from what the channel showed, not from the owner's
turns as their model read them.

The thread's chat runs on one native runtime, whose history keeps each earlier turn as its model
got it: the owner's turn message as it was built (her context, which can carry her memory), the
calls her turn made with their arguments and results, and the turns a helper's report started. A
colleague's turn in the same thread was answered from all of it. His turn reads none of her memory
(``memory_reads``), and the channel never showed him any of that.

Now a turn someone other than the owner asked for is answered apart from the runtime's history
(``NativeRuntime._history_for_turn``), and built from the conversation as the channel showed it
(``chat_persistence.channel_view``): what was said in the thread and the answers the thread was
shown (``turn_source.SHOWN_ON_CHANNEL``, written once an answer's mirror is posted), with a stub
that names each turn the channel never showed, and each answer it was not shown, and holds none of
it. Her own next turn reads everything as before.

Every turn here crosses the channel's real door into the real turn engine, on a real native
runtime whose model is scripted.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_a_standing_grant_answers_only_the_owners_own_asks import (  # noqa: F401 — autouse
    _a_shared_channel,
    _gateway,
)
from test_a_turn_someone_else_asked_for_reads_none_of_your_memory import FACT, _her_memory
from test_a_turn_someone_else_started_changes_no_memory_on_its_own import (
    COLLEAGUE,
    OWNER,
    PROVIDER,
)

from personalclaw import channel_delivery

#: What a file of hers holds, which her own turn's call read.
NOTED = "the spare key is under the blue pot"
#: What a helper's report to her held.
REPORT = "Helper report: the landlord's number is 555-0142"


def _handed(gw) -> str:
    """Everything the model was handed for the last step it took."""
    return json.dumps(gw.agent.handed[-1])


#: An answer the thread's mirror fails to post (:func:`_the_thread_is_shown_its_answers`).
LOST = "the mirror of this answer failed"


@pytest.fixture(autouse=True)
def _the_thread_is_shown_its_answers():
    """The channel is connected: the chat's answers are mirrored to the thread its messages came
    on (``chat_runner._mirror_the_answer``), except one that says :data:`LOST`, whose post fails.
    Returns the handle, which hears everything sent there."""

    async def mirror(_channel: str, text: str, _thread: str = "") -> None:
        if LOST in text:
            raise ConnectionError("the channel refused the post")

    handle = MagicMock()
    handle.deliver_text = AsyncMock(return_value="")
    handle.deliver_chat_mirror = AsyncMock(side_effect=mirror)
    handle.start_stream = AsyncMock(return_value="")
    handle.append_stream_task = AsyncMock()
    handle.stop_stream = AsyncMock()
    channel_delivery.register(handle, provider=PROVIDER)
    try:
        yield handle
    finally:
        channel_delivery.register(None, provider=PROVIDER)


@pytest.mark.asyncio
async def test_a_colleagues_turn_holds_none_of_her_turns_context(tmp_path, monkeypatch):
    """🔴 Red before: her turn's message, built with her saved fact, was in the history his turn
    was answered from."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        _her_memory(gw)
        gw.agent.calls.append([])
        await gw.say(OWNER, "Morning! I'll keep the offsite notes in this thread.")
        assert FACT in _handed(gw), "the control: her own turn is built with her saved fact"

        gw.agent.calls.append([])
        await gw.say(COLLEAGUE, "What's the plan for the offsite?")
        his = _handed(gw)
        assert FACT not in his
        # The conversation as the channel showed it: her words, and the reply to them.
        assert "keep the offsite notes in this thread" in his and "Noted." in his

        gw.agent.calls.append([])
        await gw.say(OWNER, "Thanks, both.")
        assert FACT in _handed(gw), "her own next turn reads everything as before"


@pytest.mark.asyncio
async def test_a_colleagues_turn_holds_none_of_her_turns_calls(tmp_path, monkeypatch):
    """🔴 Red before: the call her turn made, and what it read, were in the history his turn was
    answered from."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        gw.notes.write_text(f"# Notes\n\n- {NOTED}\n", encoding="utf-8")
        gw.agent.calls.append(("read_file", {"path": "notes.md"}))
        await gw.say(OWNER, "Read my notes, please.")
        assert NOTED in gw.agent.told(), "the control: her call read the file"

        gw.agent.calls.append([])
        await gw.say(COLLEAGUE, "Anything I should know?")
        his = _handed(gw)
        assert NOTED not in his
        assert "read_file" not in his and "notes.md" not in his


@pytest.mark.asyncio
async def test_a_thread_only_he_wrote_in_goes_on_as_his(tmp_path, monkeypatch):
    """A conversation every turn of which he asked for is his: his next turn is answered from it
    as it is, the call his own turn made with it. 🔴 Red before: every turn he asked for was
    answered apart from it, so a conversation of his own lost what his turns had read."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        gw.notes.write_text(f"# Notes\n\n- {NOTED}\n", encoding="utf-8")
        gw.agent.calls.append(("read_file", {"path": "notes.md"}))
        await gw.say(COLLEAGUE, "Read the team notes, please.")
        assert NOTED in gw.agent.told(), "the control: his call read the file"

        gw.agent.calls.append([])
        await gw.say(COLLEAGUE, "And what did they say about the key?")
        assert NOTED in _handed(gw)


@pytest.mark.asyncio
async def test_a_colleagues_turn_holds_none_of_a_helpers_report(tmp_path, monkeypatch):
    """A helper's report to her chat starts a turn of its own, which the channel never showed: his
    turn is shown a stub that names it. 🔴 Red before: the report's turn was in the history his
    turn was answered from."""
    from test_a_turn_someone_else_started_changes_no_memory_on_its_own import _settled

    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.state import SUBAGENT_COMPLETION_PREFIX

    async with _gateway(tmp_path, monkeypatch) as gw:
        gw.agent.calls.append([])
        await gw.say(OWNER, "Morning! I'll keep the offsite notes in this thread.")
        chat = gw.chat()
        delivered = f"{SUBAGENT_COMPLETION_PREFIX}\n{REPORT}"
        chat.append("subagent", delivered)
        gw.agent.calls.append([])
        await run_chat(gw.state, chat, delivered)
        await _settled(gw.state)
        assert "555-0142" in _handed(gw), "the control: the report's own turn read it"

        gw.agent.calls.append([])
        await gw.say(COLLEAGUE, "What did the helper find?")
        his = _handed(gw)
        assert "555-0142" not in his
        assert "A helper's report arrived here" in his


@pytest.mark.asyncio
async def test_a_reply_the_thread_was_not_shown_is_not_in_his_turn(tmp_path, monkeypatch):
    """The answer to a helper's report repeats it, and its mirror to the thread failed: his turn
    is shown a stub where it was. 🔴 Red before: every answer was in the view his turn was built
    with, the ones the thread was never shown too."""
    from test_a_turn_someone_else_started_changes_no_memory_on_its_own import _settled

    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.state import SUBAGENT_COMPLETION_PREFIX

    async with _gateway(tmp_path, monkeypatch) as gw:
        gw.agent.calls.append([])
        await gw.say(OWNER, "Morning! I'll keep the offsite notes in this thread.")
        chat = gw.chat()
        delivered = f"{SUBAGENT_COMPLETION_PREFIX}\n{REPORT}"
        chat.append("subagent", delivered)
        gw.agent.calls.append([])
        gw.agent.answer = f"The helper says the landlord's number is 555-0142 ({LOST})."
        await run_chat(gw.state, chat, delivered)
        await _settled(gw.state)

        gw.agent.answer = "Noted."
        gw.agent.calls.append([])
        await gw.say(COLLEAGUE, "What did the helper find?")
        his = _handed(gw)
        assert "555-0142" not in his
        assert "A reply the channel was not shown was here" in his
        # The answer the thread was shown is in it.
        assert "Noted." in his


def _row(role: str, content: str, **extra) -> dict:
    return {"role": role, "content": content, **extra}


def test_the_channel_view_shows_what_the_channel_showed_and_stubs_the_rest():
    """The view a turn someone else asked for is built with: the messages a channel took in, the
    answers the thread was shown, and a stub for each turn the channel never showed and each answer
    it was not shown. 🔴 Red before: the answer to the helper's report, which the thread was never
    shown, was in it."""
    from personalclaw.dashboard.chat_persistence import channel_view
    from personalclaw.turn_source import FROM_OUTSIDE, SHOWN_ON_CHANNEL, arrived_on

    hers = arrived_on("1790800000.000100", "U0OWNER", "slack")
    his = arrived_on("1790800000.000100", "U0JONAS", "slack")
    shown = {"meta": {SHOWN_ON_CHANNEL: True}}
    session = SimpleNamespace(
        messages=[
            _row("user", "Morning! Notes go here.", **hers),
            _row("assistant", "Noted.", **shown),
            _row("tool", "read_file: the spare key is under the blue pot"),
            _row("subagent", REPORT),
            _row("assistant", "The helper found the landlord's number: 555-0142."),
            _row("inject", "Scheduled digest: 3 new invoices"),
            _row("user", "a typed note", source_thread="dashboard", source_user="dashboard"),
            _row("user", "Weather today: rain", meta={FROM_OUTSIDE: {"what": "a check-in"}}),
            _row("nudge", "Keep going on the offsite plan"),
            _row("user", "What's the plan?", **his),
        ]
    )

    view = channel_view(session, "What's the plan?")

    said = json.dumps(view)
    assert [m["role"] for m in view] == [
        "user",
        "assistant",
        "summary",
        "summary",
        "summary",
        "summary",
        "summary",
        "summary",
    ], view
    assert "Morning! Notes go here." in said and "Noted." in said
    for hidden in ("spare key", "555-0142", "invoices", "a typed note", "rain", "offsite plan"):
        assert hidden not in said, hidden
    for stub in (
        "A helper's report arrived here",
        "A scheduled run's result arrived here",
        "The owner wrote here outside this thread",
        "Words taken in from outside arrived here",
        "A loop's nudge arrived here",
        "A reply the channel was not shown was here",
    ):
        assert stub in said, stub
    assert "What's the plan?" not in said, "the in-flight message is cut off"


def test_a_conversation_holds_a_turn_someone_else_asked_for():
    """Whose turns a conversation holds, read off the rows that start them: his alone, or one of
    hers, a helper's report's or another person's beside his."""
    from personalclaw.turn_source import arrived_on, holds_a_turn_not_asked_by

    his = arrived_on("1790800000.000100", "U0JONAS", "teamchat")
    ana = arrived_on("1790800000.000100", "U0ANAOTHR", "teamchat")
    mine = [
        _row("user", "Read the team notes.", **his),
        _row("assistant", "Noted."),
        _row("tool", "x"),
    ]
    assert not holds_a_turn_not_asked_by(mine, his)
    assert holds_a_turn_not_asked_by([*mine, _row("user", "Thanks!")], his), "hers"
    assert holds_a_turn_not_asked_by([*mine, _row("subagent", REPORT)], his), "a report's"
    assert holds_a_turn_not_asked_by([*mine, _row("user", "Hi", **ana)], his), "another's"


def test_a_turn_whose_channel_owner_cant_be_read_is_someone_elses(monkeypatch):
    """Closed: with the channel's owner unreadable, even his own earlier turn counts as someone
    else's, so his next turn is answered apart. 🔴 Red before: there was no such reading."""
    from personalclaw.config import credentials
    from personalclaw.turn_source import arrived_on, holds_a_turn_not_asked_by

    his = arrived_on("1790800000.000100", "U0JONAS", "teamchat")
    mine = [_row("user", "Read the team notes.", **his), _row("assistant", "Noted.")]

    def _unreadable(_channel: str) -> str:
        raise OSError("the credential store could not be read")

    monkeypatch.setattr(credentials, "owner_id_for", _unreadable)
    assert holds_a_turn_not_asked_by(mine, his), "an unreadable owner"
    monkeypatch.setattr(credentials, "owner_id_for", lambda _channel: "")
    assert holds_a_turn_not_asked_by(mine, his), "no owner kept"
