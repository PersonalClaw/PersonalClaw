"""An Incognito or Temporary chat is handed to no background model, and still works.

The defect this pins: an Incognito chat's first turn was answered by the model the chat ran on, and
then the chat was titled by the Background model, which was sent the chat's first messages. That
model can be a cloud one while the chat runs on a local model, so the chat's words left the machine
through a call the user never asked for. Its siblings did the same: a reopened chat whose history no
longer fits was condensed by the Background model, and the suggestions built from your recent chats
quoted an Incognito chat's messages to it.

The behaviour now:

* an Incognito or Temporary chat is titled without a model, by its mode ("Incognito chat",
  "Temporary chat"), on its first turn and when a title is asked for again; a normal chat is still
  titled by the model;
* the answer is the one the stores give (``memory_writes``): a chat any record marks as keeping
  nothing gets no title, tags or follow-ups from a model;
* a restricted chat's history is cut to fit rather than condensed by a model, and the suggestions
  built from recent chats leave restricted ones out.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app, _make_state

from personalclaw import memory_writes, session_restrictions, suggestions
from personalclaw.dashboard.chat_followups import _maybe_followups
from personalclaw.dashboard.chat_title import _maybe_auto_title, api_chat_session_generate_title
from personalclaw.dashboard.state import _ChatSession
from personalclaw.history import ConversationLog, session_path

_FIRST = "Here is this week's training log: Monday easy, Wednesday skipped, Friday hard."
_REPLY = "Noted. Wednesday is the gap to make up."


def _no_model(monkeypatch, what: str) -> AsyncMock:
    """The one way a chore reaches a model (``chores.run_chore``), failing the test by name if
    anything asks it."""
    asked = AsyncMock(side_effect=AssertionError(f"a background model was asked to {what}"))
    monkeypatch.setattr("personalclaw.chores.run_chore", asked)
    return asked


def _model_says(monkeypatch, text: str) -> AsyncMock:
    """Wire the chores to a model that answers *text*; returns it, holding each prompt it was
    asked."""
    answered = AsyncMock(return_value=text)
    monkeypatch.setattr("personalclaw.chores.run_chore", answered)
    return answered


def _chat(state, key: str, memory_mode: str = "persistent") -> _ChatSession:
    session = _ChatSession(key, memory_mode=memory_mode)
    session.messages = [
        {"role": "user", "content": _FIRST},
        {"role": "assistant", "content": _REPLY},
    ]
    state._sessions[key] = session
    return session


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.chat_title._auto_tag_enabled", lambda: True)
    return _make_state(tmp_path)


# ── Titles ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "memory_mode,title", [("incognito", "Incognito chat"), ("temporary", "Temporary chat")]
)
async def test_a_restricted_chats_first_turn_is_titled_without_a_model(
    state, monkeypatch, memory_mode, title
):
    session = _chat(state, "chat-1-1700000000", memory_mode)
    asked = _no_model(monkeypatch, "title a chat that keeps nothing")

    await _maybe_auto_title(state, session)

    asked.assert_not_called()
    assert session.title == title
    assert session._titled is True
    # Titled once: the next turn does not try again.
    await _maybe_auto_title(state, session)
    asked.assert_not_called()


@pytest.mark.asyncio
async def test_a_normal_chat_is_still_titled_by_the_model(state, monkeypatch):
    session = _chat(state, "chat-2-1700000100")
    answered = _model_says(monkeypatch, "Training Week Catch-up\nTAGS: none")

    await _maybe_auto_title(state, session)

    assert session.title == "Training Week Catch-up"
    assert session._titled is True
    assert answered.await_count == 1 and "Monday easy" in answered.call_args.args[0]


@pytest.mark.asyncio
async def test_asking_again_for_a_restricted_chats_title_asks_no_model(state, monkeypatch):
    session = _chat(state, "chat-3-1700000200", "incognito")
    asked = _no_model(monkeypatch, "title a chat that keeps nothing")
    app = _api_app(state)
    app.router.add_post(
        "/api/chat/sessions/{session}/generate-title", api_chat_session_generate_title
    )

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(f"/api/chat/sessions/{session.key}/generate-title")
        body = await resp.json()

    assert resp.status == 200
    assert body == {"ok": True, "title": "Incognito chat"}
    asked.assert_not_called()
    assert session.title == "Incognito chat"


@pytest.mark.asyncio
async def test_asking_again_for_a_normal_chats_title_asks_the_model(state, monkeypatch):
    session = _chat(state, "chat-4-1700000300")
    answered = _model_says(monkeypatch, "Training Week Catch-up")
    app = _api_app(state)
    app.router.add_post(
        "/api/chat/sessions/{session}/generate-title", api_chat_session_generate_title
    )

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(f"/api/chat/sessions/{session.key}/generate-title")
        body = await resp.json()

    assert body == {"ok": True, "title": "Training Week Catch-up"}
    answered.assert_awaited()


# ── The one answer: any record of the mode ──────────────────────────────────────────────────


def _record_mode(key: str, mode: str) -> None:
    """Write *key*'s transcript in the active home, its metadata recording *mode*."""
    path = session_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"_type": "metadata", "created_at": "2026-01-01T00:00:00+00:00", "memory_mode": mode}
    path.write_text(json.dumps(meta) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_a_chat_its_transcript_records_as_incognito_gets_no_model_title_or_follow_ups(
    state, monkeypatch
):
    """The live object says persistent, the transcript says Incognito: the one answer the stores
    give is the one these chores give, so nothing of the chat goes to a model."""
    session = _chat(state, "chat-5-1700000400")
    _record_mode("dashboard:chat-5-1700000400", "incognito")
    monkeypatch.setattr("personalclaw.dashboard.chat_followups._followups_enabled", lambda: True)
    asked = _no_model(monkeypatch, "read a chat that keeps nothing")
    sent: list = []
    state.broadcast_ws = lambda event, data: sent.append(event)

    await _maybe_followups(state, session)
    await _maybe_auto_title(state, session)

    asked.assert_not_called()
    assert "chat_followups" not in sent
    assert session.title == "Incognito chat"


@pytest.mark.asyncio
async def test_a_chat_a_channel_marked_incognito_gets_no_model_title(state, monkeypatch):
    session = _chat(state, "chat-6-1700000500")
    session_restrictions.mark_incognito(session.key)
    try:
        asked = _no_model(monkeypatch, "title a chat that keeps nothing")
        await _maybe_auto_title(state, session)
    finally:
        session_restrictions.clear(session.key)
    asked.assert_not_called()
    assert session.title == "Incognito chat"


def test_the_answer_is_the_one_the_stores_give():
    """Each spelling of the key is asked, the caller's mode first; no key and no mode is refused."""
    assert memory_writes.blocks_background_models("dashboard:chat-9-1", memory_mode="incognito")
    assert memory_writes.blocks_background_models("dashboard:chat-9-1", memory_mode="temporary")
    assert memory_writes.blocks_background_models("", memory_mode=None)
    assert not memory_writes.blocks_background_models("dashboard:chat-9-1", "chat-9-1")
    session_restrictions.mark_temporary("chat-9-1")
    try:
        assert memory_writes.blocks_background_models("dashboard:chat-9-1", "chat-9-1")
    finally:
        session_restrictions.clear("chat-9-1")
    with memory_writes.derived_from("dashboard:chat-9-2", memory_mode="incognito"):
        # Work that derives from a chat that keeps nothing hands nothing on, whatever it names.
        assert memory_writes.blocks_background_models("dashboard:chat-9-1")


# ── A reopened chat's history ───────────────────────────────────────────────────────────────


def _long_history() -> list[dict]:
    out: list[dict] = []
    for i in range(50):
        out.append({"role": "user", "content": f"entry {i} " + "x" * 1400})
        out.append({"role": "assistant", "content": f"reply {i} " + "y" * 1400})
    return out


@pytest.mark.asyncio
async def test_a_reopened_incognito_chats_history_is_cut_to_fit_not_condensed_by_a_model(
    monkeypatch,
):
    """The turn's own work (the scope ``run_chat`` sets) and a channel's mark both say so; the
    caller then cuts the history to fit, as it does when no condensed history comes back."""
    from personalclaw.context import compress_thread_history

    asked = _no_model(monkeypatch, "condense a chat that keeps nothing")
    with memory_writes.derived_from("dashboard:chat-7-1700000600", memory_mode="incognito"):
        result = await compress_thread_history(
            _long_history(), "dashboard:chat-7-1700000600", "and now?"
        )
    assert result is None
    asked.assert_not_called()

    session_restrictions.mark_incognito("thread:C1-1700000700")
    try:
        result = await compress_thread_history(_long_history(), "thread:C1-1700000700", "and now?")
    finally:
        session_restrictions.clear("thread:C1-1700000700")
    assert result is None
    asked.assert_not_called()


# ── The suggestions built from recent chats ─────────────────────────────────────────────────


def test_the_suggestions_built_from_recent_chats_leave_restricted_chats_out(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    log = ConversationLog(base_dir=tmp_path / "sessions")
    log.append("dashboard:chat-10-1", "user", "Plan the launch week for the garden club")
    log.update_metadata("dashboard:chat-10-1", {"title": "Launch week"})
    for mode, words in (("incognito", "my private training log"), ("temporary", "a scratch note")):
        key = f"dashboard:chat-{mode}-2"
        log.append(key, "user", words)
        log.update_metadata(key, {"memory_mode": mode, "title": f"About {words}"})
    memory = SimpleNamespace(
        read_preferences=lambda: "# User Preferences\n\n<!-- Learned from conversations -->",
        read_projects=lambda: "# Active Projects\n\n<!-- Current work context -->",
        read_recent_history=lambda days=2: "",
    )

    with patch("personalclaw.context.ContextBuilder.get_memory_for", return_value=memory):
        context = suggestions._build_context(SimpleNamespace(conversation_log=log))

    assert "Plan the launch week for the garden club" in context
    assert "training log" not in context
    assert "scratch note" not in context
