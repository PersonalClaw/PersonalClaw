"""A chat deleted while it is busy stays deleted.

Measured from the code: deleting an ordinary chat removed its transcript and stopped its turn, and
two paths wrote the transcript back afterwards, so the chat came back in Chat history. A message
waiting in the chat's queue ran when the stopped turn ended, with its model calls and tools, and its
end-of-turn save wrote the whole transcript again. And the title-and-tags request a chat's first
turns start, answering after the delete, saved the tags it applied with ``force=True``. The save
refused a chat no longer loaded only when it was Temporary.

Driven through the real delete (``chat_forget.delete_chats``, the one path the Delete button and
both history routes take), the real chat runner and its queue, with a scripted runtime.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from test_dashboard_approval import _context_builder, _make_session, _make_state

from personalclaw.dashboard.chat import run_chat
from personalclaw.dashboard.chat_forget import delete_chats
from personalclaw.dashboard.chat_persistence import save_session_to_history
from personalclaw.dashboard.chat_title import _maybe_auto_title
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent
from personalclaw.llm.events import EVENT_TEXT_CHUNK


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """What a delete removes besides the transcript (the chat's workspace, its scratch folder)
    lands in the sandbox."""
    import personalclaw.config.loader as cfg
    import personalclaw.session_workspace as ws

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)


async def _works_until_stopped():
    yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Reading the overnight logs first")
    await asyncio.sleep(60)
    yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


async def _answers_at_once():
    yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Because the disk filled up.")
    yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


def _kept(state, session) -> bool:
    """Whether a transcript is kept for *session*, under either spelling of its key."""
    log = state.conversation_log
    return bool(log.has_log(_history_key_for(session.key)) or log.has_log(session.key))


async def _until(check, what: str, *, seconds: float = 10.0) -> None:
    for _ in range(int(seconds * 100)):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never: {what}")


@pytest.mark.asyncio
async def test_a_message_queued_behind_a_deleted_chats_turn_never_runs(tmp_path):
    """🔴 Red before the fix: the stopped turn's end ran the queued message on the deleted chat,
    a second model call, and that turn's end saved the transcript back."""
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    turns = [_works_until_stopped, _answers_at_once]
    client.stream = MagicMock(side_effect=lambda *a, **k: turns.pop(0)())
    client.context_usage_pct = MagicMock(return_value=None)
    session = _make_session("chat-3-busy")
    session._titled = True  # no title call: the runtime plays the turns only
    state._sessions[session.key] = session

    assert session.enqueue_or_run_prompt("what failed overnight?", run_chat, state)
    await _until(lambda: client.stream.call_count == 1, "the first turn reached the model")
    session.queue_append("and why?", attended=True)
    stopped = session.task
    assert stopped is not None

    done = await delete_chats(state, [session.key], by="you")

    assert done.deleted == (session.key,)
    await _until(stopped.done, "the deleted chat's turn ended")
    # Its end has run: a turn the queue started is the chat's task now, and has its turn to end.
    await _until(lambda: session.task is None or session.task.done(), "the chat went idle")
    assert client.stream.call_count == 1, "a queued message ran on the deleted chat"
    assert not session._queue
    assert not _kept(state, session), "the deleted chat's transcript was written back"
    assert session.key not in state._sessions


@pytest.mark.asyncio
async def test_tags_the_title_request_brings_after_the_delete_write_nothing(tmp_path, monkeypatch):
    """🔴 Red before the fix: the tags were saved with ``force=True``, which wrote the deleted
    chat's transcript again, whole."""
    state, _client = _make_state(tmp_path)
    state._tags = [{"id": "t-trip", "name": "Travel", "color": "#111111", "order": 0}]
    session = _make_session("chat-4-new")
    state._sessions[session.key] = session
    session.append("user", "plan a weekend in Lisbon", "msg u0", broadcast=False)
    session.append("assistant", "Here is a plan for two days.", "msg a0", broadcast=False)
    session.drain()
    save_session_to_history(state, session, force=True)
    assert _kept(state, session)

    asked = asyncio.Event()
    answer = asyncio.Event()

    async def _run_chore(prompt, **_kw):
        asked.set()
        await answer.wait()
        return "Lisbon Weekend\nTAGS: Travel"

    monkeypatch.setattr("personalclaw.chores.run_chore", _run_chore)
    titling = asyncio.create_task(_maybe_auto_title(state, session))
    await asyncio.wait_for(asked.wait(), 5)

    await delete_chats(state, [session.key], by="you")
    assert not _kept(state, session)
    answer.set()
    await asyncio.wait_for(titling, 5)

    assert session.tags == ["t-trip"], "precondition: the tags were applied"
    assert not _kept(state, session), "the deleted chat's transcript was written back"


@pytest.mark.asyncio
async def test_no_save_writes_a_deleted_chat_whatever_force_says(tmp_path):
    """🔴 Red before the fix: an ordinary chat's save checked nothing of whether it was deleted."""
    state, _client = _make_state(tmp_path)
    session = _make_session("chat-5-gone")
    state._sessions[session.key] = session
    session.append("user", "hello", "msg u0", broadcast=False)
    session.append("assistant", "hi there", "msg a0", broadcast=False)
    session.drain()
    save_session_to_history(state, session, force=True)

    await delete_chats(state, [session.key], by="you")
    save_session_to_history(state, session, force=True)
    save_session_to_history(state, session, closed=True)

    assert not _kept(state, session)


@pytest.mark.asyncio
async def test_a_chat_cleanup_lets_go_of_is_still_saved_as_closed(tmp_path):
    """Kept green: ``/cleanup`` takes a chat out of the gateway's chats and then saves it closed,
    on purpose, so the save does not refuse every chat that is no longer loaded."""
    state, _client = _make_state(tmp_path)
    session = _make_session("chat-6-idle")
    state._sessions[session.key] = session
    session.append("user", "hello", "msg u0", broadcast=False)
    session.append("assistant", "hi there", "msg a0", broadcast=False)
    session.drain()
    state._sessions.pop(session.key)

    save_session_to_history(state, session, closed=True)

    assert _kept(state, session)


@pytest.mark.asyncio
async def test_no_model_is_asked_anything_for_a_deleted_chat_after_its_turn(tmp_path, monkeypatch):
    """The stopped turn's end asks no model for the deleted chat: no title, no follow-ups, no offer,
    no plan draft and no planning turn, each of which would send its words to a model after the
    delete (``chat_runner._goes_on_after_its_turn``, ``_start_plan_after_turn``)."""
    asked: list[str] = []

    async def _title(_state, _session):
        asked.append("title")

    async def _followups(_state, _session):
        asked.append("follow-ups")

    monkeypatch.setattr("personalclaw.dashboard.chat_runner._maybe_auto_title", _title)
    monkeypatch.setattr("personalclaw.dashboard.chat_runner._maybe_followups", _followups)
    monkeypatch.setattr(
        "personalclaw.dashboard.chat_runner.maybe_offer_check_work",
        lambda *_a, **_k: asked.append("offer"),
    )
    monkeypatch.setattr(
        "personalclaw.dashboard.chat_runner._submit_plan_draft",
        lambda *_a, **_k: asked.append("plan draft"),
    )
    monkeypatch.setattr(
        "personalclaw.dashboard.chat_plan.start_after_turn",
        lambda *_a, **_k: asked.append("planning turn"),
    )
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    client.stream = MagicMock(side_effect=lambda *a, **k: _works_until_stopped())
    client.context_usage_pct = MagicMock(return_value=None)
    session = _make_session("chat-7-new")  # untitled: its turn's end would ask for a title
    state._sessions[session.key] = session

    assert session.enqueue_or_run_prompt("what failed overnight?", run_chat, state)
    await _until(lambda: client.stream.call_count == 1, "the turn reached the model")
    stopped = session.task
    assert stopped is not None

    await delete_chats(state, [session.key], by="you")
    await _until(stopped.done, "the deleted chat's turn ended")
    for _ in range(10):  # a chore its end started as a task has had its turn to begin
        await asyncio.sleep(0)

    assert asked == []
