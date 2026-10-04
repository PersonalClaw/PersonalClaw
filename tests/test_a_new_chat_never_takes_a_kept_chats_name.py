"""A new chat never takes a kept chat's name, and a save never replaces another chat's transcript.

A new chat is named ``chat-<number>-<second>``. The number was a counter each gateway kept in
memory, starting again at 1 every time it started, and the name was never checked against the chats
already kept. So a chat opened in the second an earlier run of the gateway opened one with the same
number (a quick restart, or the clock set back across one) was given that chat's name, and its saves
replaced that chat's transcript: the stop's last save did, whatever either chat held.

A gateway here is what one is on a running host: a dashboard state over the home's transcripts, and
its stop saves every chat it holds, as a stopping gateway does. The clock is a stand-in, so "the
same second" is that second, not a race against the real one.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app_with_agent_routes, _make_state

from personalclaw.dashboard.chat_persistence import (
    _rehydrate_session_from_history,
    restore_recent_sessions,
    save_all_sessions_to_history,
    save_session_to_history,
)
from personalclaw.dashboard.chat_utils import persisted_history_key
from personalclaw.dashboard.state import DashboardState

#: A second on the stand-in clock.
SECOND = 1_791_067_183


def _open_chat(gateway: DashboardState, second: int = SECOND):
    """A new chat opened at *second*, as the dashboard opens one: with no name, for the gateway to
    give it one."""
    with patch("time.time", return_value=float(second)):
        return gateway.get_or_create_session()


def _turn(chat, words: str) -> None:
    """One exchange in *chat*: the owner's message and its answer."""
    chat.append("user", words, "msg msg-u", broadcast=False)
    chat.append("assistant", f"About {words}: noted.", "msg msg-a", broadcast=False)


def _stop(gateway: DashboardState) -> None:
    """A gateway stopping: it saves every chat it holds."""
    save_all_sessions_to_history(gateway)


def _kept(gateway: DashboardState, name: str) -> list[str]:
    """What the transcript kept under *name* says, read from the file, never from a chat."""
    log = gateway.conversation_log
    key = persisted_history_key(log, name)
    path = log._path(key)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return [json.loads(line)["content"] for line in lines[1:] if line.strip()]


def _kept_chats(home: Path) -> set[str]:
    """The chat transcripts kept in *home*, by file name."""
    return {p.name for p in home.glob("dashboard_chat-*.jsonl")}


# ── a new chat's name is one no chat has ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("brought_back", ["on disk only", "restored at the start", "archived"])
def test_a_chat_opened_in_the_second_of_a_restart_gets_its_own_name(tmp_path, brought_back):
    """🔴 Red before: the second run's first chat was given the first run's chat's name, and the
    stop replaced that chat's transcript with the new chat's (restored at the start, the "new"
    chat was the old chat itself)."""
    first = _make_state(tmp_path)
    earlier = _open_chat(first)
    _turn(earlier, "the garden plan")
    _stop(first)
    if brought_back == "archived":
        save_session_to_history(first, earlier, closed=True, force=True)

    second = _make_state(tmp_path)
    if brought_back == "restored at the start":
        restore_recent_sessions(second, 0)
    later = _open_chat(second)
    _turn(later, "the boat repairs")
    _stop(second)

    assert later.key != earlier.key, f"the new chat was given the kept chat's name {earlier.key}"
    assert later is not second._sessions.get(earlier.key), "the new chat is the kept chat"
    assert _kept(second, earlier.key) == ["the garden plan", "About the garden plan: noted."]
    assert _kept(second, later.key) == ["the boat repairs", "About the boat repairs: noted."]


def test_two_gateways_on_one_home_open_chats_in_the_same_second_under_different_names(tmp_path):
    """🔴 Red before: a gateway started beside a running one named its first chat after the running
    one's, saved or not, so the two chats shared one transcript file."""
    running = _make_state(tmp_path)
    theirs = _open_chat(running)
    _turn(theirs, "the quarterly figures")
    save_session_to_history(running, theirs)  # the running gateway's flush has saved it

    beside = _make_state(tmp_path)
    ours = _open_chat(beside)

    assert ours.key != theirs.key
    _turn(ours, "a reading list")
    _stop(beside)
    _stop(running)
    assert _kept(running, theirs.key) == [
        "the quarterly figures",
        "About the quarterly figures: noted.",
    ]
    assert _kept(running, ours.key) == ["a reading list", "About a reading list: noted."]


def test_a_restart_carries_the_numbers_on_past_the_kept_chats(tmp_path):
    """🔴 Red before: every start numbered its chats from 1 again, so in the second it started in
    each of its chats took the kept chat of its number's name."""
    first = _make_state(tmp_path)
    kept = []
    for subject in ("rent", "the vet", "a birthday"):
        chat = _open_chat(first)
        _turn(chat, subject)
        kept.append(chat.key)
    _stop(first)
    assert kept == [f"chat-{n}-{SECOND}" for n in (1, 2, 3)]

    second = _make_state(tmp_path)
    opened = [_open_chat(second).key for _ in range(3)]

    assert opened == [f"chat-{n}-{SECOND}" for n in (4, 5, 6)]
    assert not set(opened) & set(kept)


def test_a_clock_set_back_across_a_restart_reuses_no_name(tmp_path):
    """🔴 Red before: after a restart with the clock set back a second, the new run passed the old
    run's seconds again with its counter back at 1, and gave each chat the name of the one the old
    run opened in that second with that number — and the stop replaced both transcripts."""
    first = _make_state(tmp_path)
    one = _open_chat(first, SECOND)
    _turn(one, "the first draft")
    two = _open_chat(first, SECOND + 1)
    _turn(two, "the second draft")
    _stop(first)

    second = _make_state(tmp_path)  # the clock was set back to SECOND before it started
    later = [_open_chat(second, SECOND), _open_chat(second, SECOND + 1)]
    for chat, subject in zip(later, ("a recipe", "a packing list"), strict=True):
        _turn(chat, subject)
    _stop(second)

    assert not {c.key for c in later} & {one.key, two.key}
    assert _kept(second, one.key) == ["the first draft", "About the first draft: noted."]
    assert _kept(second, two.key) == ["the second draft", "About the second draft: noted."]
    assert len(_kept_chats(tmp_path)) == 4


def test_a_name_the_disk_cannot_answer_for_is_stepped_past(tmp_path, monkeypatch):
    """A disk that cannot say whether a name is a kept chat's counts it as taken: stepping past a
    free name costs nothing, and taking a kept one costs its transcript."""
    gateway = _make_state(tmp_path)
    log = gateway.conversation_log
    unreadable = f"dashboard:chat-1-{SECOND}"
    real_has_log = log.has_log

    def _has_log(key: str) -> bool:
        if key == unreadable:
            raise PermissionError("the disk would not say")
        return real_has_log(key)

    monkeypatch.setattr(log, "has_log", _has_log)
    assert _open_chat(gateway).key == f"chat-2-{SECOND}"


# ── the save never replaces another chat's transcript ────────────────────────────────────────────


def test_a_save_never_replaces_another_chats_transcript(tmp_path, caplog):
    """🔴 Red before: two gateways on one home that opened a chat in the same second, before either
    saved, gave both the same name — neither can see the other's unsaved chat — and the second save
    replaced the first chat's transcript, even its archive flag. Now the save that would is refused
    and says so, and the chat it refused is still held, with its turns."""
    one = _make_state(tmp_path)
    other = _make_state(tmp_path)
    theirs = _open_chat(one)
    ours = _open_chat(other)
    assert ours.key == theirs.key, "precondition: neither gateway could see the other's chat"

    _turn(theirs, "the lease renewal")
    save_session_to_history(one, theirs)
    _turn(ours, "a holiday")
    _turn(ours, "a second holiday")
    save_session_to_history(other, ours, force=True)
    save_session_to_history(other, ours, closed=True)

    log = one.conversation_log
    meta = log.get_metadata(persisted_history_key(log, theirs.key))
    assert _kept(one, theirs.key) == ["the lease renewal", "About the lease renewal: noted."]
    assert not meta.get("closed"), "another chat's save archived this one"
    assert any("was not saved" in r.getMessage() for r in caplog.records), "the refusal is silent"
    assert [m["content"] for m in other._sessions[ours.key].messages][0] == "a holiday"


def test_a_late_save_of_a_deleted_chat_leaves_the_chat_named_after_it(tmp_path):
    """🔴 Red before: a save still on its way for a deleted chat replaced the transcript of the new
    chat given its name since (a named one: ``personalclaw run --session`` names its chat)."""
    gateway = _make_state(tmp_path)
    log = gateway.conversation_log
    deleted = gateway.get_or_create_session("inbound:cli:notes")
    _turn(deleted, "the deleted chat's words")
    save_session_to_history(gateway, deleted)
    log.delete_session(persisted_history_key(log, deleted.key))
    gateway._sessions.pop(deleted.key)

    named_again = gateway.get_or_create_session("inbound:cli:notes")
    _turn(named_again, "the new chat's words")
    save_session_to_history(gateway, named_again)
    _turn(deleted, "a late turn")
    save_session_to_history(gateway, deleted, force=True)

    assert _kept(gateway, "inbound:cli:notes") == [
        "the new chat's words",
        "About the new chat's words: noted.",
    ]


def test_a_chat_keeps_saving_to_its_own_transcript(tmp_path):
    """Positive control: the guard refuses another chat's transcript, never the chat's own — turn
    by turn, forced, archived, and after the chat comes back from a restart by every way back."""
    first = _make_state(tmp_path)
    chat = _open_chat(first)
    _turn(chat, "one")
    save_session_to_history(first, chat)
    _turn(chat, "two")
    save_session_to_history(first, chat, force=True)
    _stop(first)
    assert _kept(first, chat.key)[::2] == ["one", "two"]

    for way_back in ("restored at the start", "opened from the list"):
        gateway = _make_state(tmp_path)
        if way_back == "restored at the start":
            restore_recent_sessions(gateway, 0)
            again = gateway._sessions.get(chat.key)
        else:
            again = _rehydrate_session_from_history(gateway, chat.key)
        assert again is not None
        _turn(again, way_back)
        _stop(gateway)
    assert _kept(first, chat.key)[::2] == [
        "one",
        "two",
        "restored at the start",
        "opened from the list",
    ], "a chat that came back from a restart could not save to its own transcript"

    save_session_to_history(gateway, again, closed=True)
    log = gateway.conversation_log
    assert log.get_metadata(persisted_history_key(log, chat.key)).get("closed"), "not archived"
    assert len(_kept(gateway, chat.key)) == 8


def test_a_transcript_that_names_no_chat_still_saves_when_continued(tmp_path):
    """Positive control: a transcript an import or an older version wrote records no tab id, so it
    names no chat, and continuing it saves to it as before."""
    gateway = _make_state(tmp_path)
    key = "dashboard:imported-notes"
    lines = [
        {"_type": "metadata", "created_at": "2026-09-01T10:00:00+00:00", "title": "Notes"},
        {"role": "user", "content": "an imported question", "ts": "2026-09-01T10:00:00+00:00"},
        {"role": "assistant", "content": "an imported answer", "ts": "2026-09-01T10:00:01+00:00"},
    ]
    gateway.conversation_log._path(key).write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )
    chat = _rehydrate_session_from_history(gateway, "imported-notes")
    assert chat is not None
    _turn(chat, "a follow-up")
    _stop(gateway)
    assert _kept(gateway, "imported-notes") == [
        "an imported question",
        "an imported answer",
        "a follow-up",
        "About a follow-up: noted.",
    ]


# ── the create route given a kept chat's name opens that chat ────────────────────────────────────


@pytest.mark.asyncio
async def test_the_create_route_given_a_kept_chats_name_opens_that_chat(tmp_path):
    """🔴 Red before: a kept chat's name minted a blank session over it, and the stop's save replaced
    the kept transcript with the new turn: ``personalclaw run --session`` on a gateway it started
    itself lost every earlier run's conversation, and never continued it."""
    first = _make_state(tmp_path)
    run = first.get_or_create_session("inbound:cli:nightly")
    _turn(run, "the first night's report")
    _stop(first)

    second = _make_state(tmp_path)
    client = TestClient(TestServer(_make_app_with_agent_routes(second)))
    await client.start_server()
    try:
        resp = await client.post("/api/chat/sessions", json={"name": "inbound:cli:nightly"})
        assert resp.status == 200
    finally:
        await client.close()
    chat = second._sessions["inbound:cli:nightly"]
    assert [m["content"] for m in chat.messages][:1] == ["the first night's report"]
    _turn(chat, "the second night's report")
    _stop(second)
    assert _kept(second, "inbound:cli:nightly")[::2] == [
        "the first night's report",
        "the second night's report",
    ]
