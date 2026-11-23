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


def _shown_on(provider: str, thread: str, channel: str = "C0EXAMPLE01") -> dict:
    """The mark an answer row carries once its mirror posted it to *thread* on *provider*."""
    return {"provider": provider, "channel": channel, "thread": thread}


def test_the_channel_view_shows_what_the_channel_showed_and_stubs_the_rest():
    """The view a turn someone else asked for is built with: the messages a channel took in, the
    answers the thread was shown, and a stub for each turn the channel never showed and each answer
    it was not shown. 🔴 Red before: the answer to the helper's report, which the thread was never
    shown, was in it."""
    from personalclaw.dashboard.chat_persistence import channel_view
    from personalclaw.turn_source import FROM_OUTSIDE, SHOWN_ON_CHANNEL, arrived_on

    hers = arrived_on("1790800000.000100", "U0OWNER", "slack")
    his = arrived_on("1790800000.000100", "U0JONAS", "slack")
    shown = {"meta": {SHOWN_ON_CHANNEL: _shown_on("slack", "1790800000.000100")}}
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


#: The owner's own DM with PersonalClaw, where the chat ran before it was moved to the thread.
HER_DM = "D0OWNERDM1"


def _moved_from_her_dm(where_he_writes: str) -> SimpleNamespace:
    """A chat that ran in her DM, where she wrote and was answered, and then moved to a shared
    thread, where he writes on *where_he_writes*."""
    from personalclaw.turn_source import SHOWN_ON_CHANNEL, arrived_on

    hers = arrived_on(HER_DM, "U0OWNER", "slack")
    his = arrived_on(where_he_writes, "U0JONAS", "slack")
    in_her_dm = {"meta": {SHOWN_ON_CHANNEL: _shown_on("slack", HER_DM, channel=HER_DM)}}
    return SimpleNamespace(
        messages=[
            _row("user", "Remind me: therapy is Thursday at four.", **hers),
            _row("assistant", "Noted: therapy, Thursday at four.", **in_her_dm),
            _row("user", "What's the plan for the offsite?", **his),
        ]
    )


def test_what_was_said_in_her_dm_is_not_in_a_thread_the_chat_moved_to():
    """A chat moved from her DM to a shared thread is the same chat: his turn in the thread is
    shown stubs for her DM's message and its answer, which the thread never showed. 🔴 Red before:
    a row a channel took in, and an answer the mark said was shown, were shown whatever thread
    they were on."""
    from personalclaw.dashboard.chat_persistence import channel_view

    view = channel_view(_moved_from_her_dm("1790800000.000100"), "What's the plan for the offsite?")
    said = json.dumps(view)
    assert "therapy" not in said and "Thursday" not in said, view
    assert "A message from another thread this chat ran in was here" in said
    assert "A reply the channel was not shown was here" in said


def test_the_view_is_for_the_thread_whoever_asked_is_on():
    """A turn that carries on work he asked for (a helper's report handed back) is built for the
    thread he asked on, which the turn's own row does not name: what was said and shown there is in
    it, and nothing from her DM. 🔴 Red before: there was no thread to say it for."""
    from personalclaw.dashboard.chat_persistence import channel_view
    from personalclaw.turn_source import SHOWN_ON_CHANNEL, arrived_on

    thread = "1790800000.000100"
    session = _moved_from_her_dm(thread)
    session.messages += [
        _row(
            "assistant",
            "The venue opens at nine.",
            meta={SHOWN_ON_CHANNEL: _shown_on("slack", thread)},
        ),
        _row("subagent", REPORT),
    ]
    view = channel_view(session, REPORT, where=arrived_on(thread, "U0JONAS", "slack"))
    said = json.dumps(view)
    assert "What's the plan for the offsite?" in said and "The venue opens at nine." in said
    assert "therapy" not in said, view


def test_the_turn_engine_builds_his_carried_on_turn_for_the_thread_he_asked_on():
    """The turn engine (``chat_runner._turn_history``) builds a turn that carries on work he asked
    for, a helper's report handed back whose row names no thread, for the thread whoever asked is
    on: the asker's, not the in-flight row's. A view built for the report's row would stub all of
    it, his own thread's messages and answers too."""
    from personalclaw import memory_writes
    from personalclaw.dashboard.chat_runner import _turn_history
    from personalclaw.turn_source import SHOWN_ON_CHANNEL, arrived_on

    thread = "1790800000.000100"
    session = _moved_from_her_dm(thread)
    session.messages += [
        _row(
            "assistant",
            "The venue opens at nine.",
            meta={SHOWN_ON_CHANNEL: _shown_on("slack", thread)},
        ),
        _row("subagent", REPORT),
    ]
    with memory_writes.derived_from("dashboard:offsite"):
        memory_writes.asked_for(arrived_on(thread, "U0JONAS", "slack"))
        history, apart = _turn_history(session, REPORT, 0)
    said = json.dumps(history)
    assert apart, "the control: his turn is answered apart from her DM's"
    assert "What's the plan for the offsite?" in said and "The venue opens at nine." in said
    assert "therapy" not in said, history


def test_an_answer_marked_before_marks_named_a_thread_is_a_stub():
    """Closed: an answer whose mark names no thread (one written while marks were a plain yes) is
    a stub, since nothing says which thread it was shown on."""
    from personalclaw.dashboard.chat_persistence import channel_view
    from personalclaw.turn_source import SHOWN_ON_CHANNEL, arrived_on

    thread = "1790800000.000100"
    session = SimpleNamespace(
        messages=[
            _row("assistant", "Noted: therapy, Thursday at four.", meta={SHOWN_ON_CHANNEL: True}),
            _row("user", "What's the plan?", **arrived_on(thread, "U0JONAS", "slack")),
        ]
    )
    said = json.dumps(channel_view(session, "What's the plan?"))
    assert "therapy" not in said and "A reply the channel was not shown was here" in said


def test_a_shown_answer_is_masked_as_the_channel_masks_it():
    """An answer the thread was shown is handed on masked as the channel's handle masks what it
    sends, whatever its row holds. 🔴 Red with the mask taken out."""
    from personalclaw.dashboard.chat_persistence import channel_view
    from personalclaw.turn_source import SHOWN_ON_CHANNEL, arrived_on

    thread = "1790800000.000100"
    key = "AKIAIOSFODNN7EXAMPLE"
    session = SimpleNamespace(
        messages=[
            _row(
                "assistant",
                f"The key is {key}.",
                meta={SHOWN_ON_CHANNEL: _shown_on("slack", thread)},
            ),
            _row("user", "What's the plan?", **arrived_on(thread, "U0JONAS", "slack")),
        ]
    )
    said = json.dumps(channel_view(session, "What's the plan?"))
    assert key not in said and "The key is" in said, said


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


def test_a_runner_that_never_says_answers_his_turn_apart():
    """Closed: a runner that never says whether its turn is answered apart (``run_chat`` always
    does: ``memory_writes.answers_apart``) answers a turn someone else asked for apart from the
    history all the same, from its own message on. Said, the turn engine's word decides. 🔴 Red
    with that default open."""
    from personalclaw import memory_writes
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.turn_source import arrived_on

    hers = {"role": "user", "content": "Morning! Notes go here."}
    reply = {"role": "assistant", "content": "Noted."}
    his = {"role": "user", "content": "What's the plan?"}
    runtime = SimpleNamespace(_messages=[hers, reply, his], _turn_message=his)
    with memory_writes.derived_from("dashboard:chat-41-1790900041", "chat-41-1790900041"):
        memory_writes.asked_for(arrived_on("1790800000.000100", "U0JONAS", "teamchat"))
        assert NativeAgentRuntime._history_for_turn(runtime) == [his]
        memory_writes.answers_apart(False)
        assert NativeAgentRuntime._history_for_turn(runtime) == [hers, reply, his]
