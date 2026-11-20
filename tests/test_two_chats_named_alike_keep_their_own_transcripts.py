"""Two chats whose keys differ only in punctuation keep a transcript each.

A transcript's file is named by its key's slug (``history._safe_key``): every character but a
letter, a digit, ``-``, ``_`` and ``.`` becomes ``_``. So ``dashboard:a:b``, ``dashboard:a?b`` and
``dashboard:a_b`` all named ``dashboard_a_b.jsonl``. The second chat opened the first one's
transcript as its own, took on its record (title, tab id), and whichever saved last replaced the
other's turns.

A transcript records the key it is now (``history.RECORDED_KEY``), and a key whose slug's file
records another key has a file of its own, the slug and a short hash of the whole key
(``ConversationLog._file_stem``). Nothing is refused, since most keys are no person's (a workflow
step's, a channel thread's), and nothing already kept moves: a file an earlier version wrote,
which records no key, is its slug's until a save claims it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_app_with_agent_routes, _make_state

from personalclaw.dashboard.chat_persistence import (
    save_all_sessions_to_history,
    save_session_to_history,
)
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import RECORDED_KEY, ConversationLog


def _turn(chat, words: str) -> None:
    chat.append("user", words, "msg msg-u", broadcast=False)
    chat.append("assistant", f"About {words}: noted.", "msg msg-a", broadcast=False)


def _said(path: Path) -> list[str]:
    """What the transcript at *path* says the owner wrote, read from the file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["content"] for line in lines[1:] if line.strip()][::2]


def _first_line(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])


async def _open(gateway: DashboardState, name: str):
    """A chat opened by *name* through the create route, as an API caller opens one."""
    client = TestClient(TestServer(_make_app_with_agent_routes(gateway)))
    await client.start_server()
    try:
        resp = await client.post("/api/chat/sessions", json={"name": name})
        assert resp.status == 200, await resp.text()
    finally:
        await client.close()
    return gateway._sessions[name]


@pytest.mark.asyncio
async def test_two_chats_whose_names_differ_only_in_punctuation_keep_their_own_transcripts(
    tmp_path,
):
    """🔴 Red before: ``a?b`` opened ``a:b``'s conversation as its own, and the stop's save
    replaced ``a:b``'s turns with its own."""
    gateway = _make_state(tmp_path)
    first = await _open(gateway, "a:b")
    _turn(first, "the garden plan")
    save_all_sessions_to_history(gateway)

    second = await _open(gateway, "a?b")
    assert [m["content"] for m in second.messages] == [], "it opened the other chat's transcript"
    _turn(second, "the kitchen quotes")
    save_all_sessions_to_history(gateway)

    log = gateway.conversation_log
    mine, theirs = log._path("dashboard:a:b"), log._path("dashboard:a?b")
    assert mine != theirs
    assert mine.name == "dashboard_a_b.jsonl", "the first chat's file moved"
    assert theirs.name.startswith("dashboard_a_b-") and theirs.name.endswith(".jsonl")
    assert _said(mine) == ["the garden plan"]
    assert _said(theirs) == ["the kitchen quotes"]
    assert _first_line(mine)[RECORDED_KEY] == "dashboard:a:b"
    assert _first_line(theirs)[RECORDED_KEY] == "dashboard:a?b"

    # Each opens as itself on the next start, and both save again without touching the other.
    later = _make_state(tmp_path)
    again = await _open(later, "a?b")
    assert [m["content"] for m in again.messages][:1] == ["the kitchen quotes"]
    _turn(again, "the second quote")
    save_all_sessions_to_history(later)
    assert _said(mine) == ["the garden plan"]
    assert _said(theirs) == ["the kitchen quotes", "the second quote"]


@pytest.mark.parametrize("first", ["the workflow's step", "the typed chat"])
def test_a_workflow_steps_key_and_a_chat_with_its_slug_each_keep_theirs(tmp_path, first):
    """A workflow step's transcript and a chat whose key has the same slug: whichever writes second
    is given a file of its own, and neither is refused or written into the other's."""
    log = ConversationLog(base_dir=tmp_path / "sessions")
    step, typed = "workflow:ab12cd34:find_the_callers", "workflow:ab12cd34:find:the:callers"
    order = [step, typed] if first == "the workflow's step" else [typed, step]
    for key in order:
        log.append(key, "user", f"for {key}")
    assert log._path(step) != log._path(typed)
    assert [m["content"] for m in log.read_messages(step)] == [f"for {step}"]
    assert [m["content"] for m in log.read_messages(typed)] == [f"for {typed}"]
    # The one written first keeps the slug's own name.
    assert log._path(order[0]).name == "workflow_ab12cd34_find_the_callers.jsonl"


def test_a_file_an_earlier_version_wrote_is_read_and_claimed_where_it_is(tmp_path):
    """A transcript that records no key is its slug's: read as the chat's, claimed by its next
    write without moving, and a key with the same slug written after it has a file of its own."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    kept = sessions / "dashboard_a_b.jsonl"
    kept.write_text(
        json.dumps(
            {"_type": "metadata", "created_at": "2026-10-01T09:00:00+00:00", "last_consolidated": 0}
        )
        + "\n"
        + json.dumps({"role": "user", "content": "the old plan", "ts": "2026-10-01T09:00:01+00:00"})
        + "\n",
        encoding="utf-8",
    )
    log = ConversationLog(base_dir=sessions)

    assert log._path("dashboard:a:b") == kept
    assert [m["content"] for m in log.read_messages("dashboard:a:b")] == ["the old plan"]
    log.append("dashboard:a:b", "user", "a new line")
    assert log.get_metadata("dashboard:a:b")[RECORDED_KEY] == "dashboard:a:b"
    assert sorted(p.name for p in sessions.glob("*.jsonl")) == ["dashboard_a_b.jsonl"], "it moved"

    log.append("dashboard:a?b", "user", "another chat")
    assert log._path("dashboard:a?b") != kept
    assert [m["content"] for m in log.read_messages("dashboard:a:b")] == [
        "the old plan",
        "a new line",
    ]
    assert [m["content"] for m in log.read_messages("dashboard:a?b")] == ["another chat"]


def test_a_key_that_is_its_own_slug_names_its_own_file(tmp_path):
    """The chat list's keys are file names, and each still reads its own file."""
    log = ConversationLog(base_dir=tmp_path / "sessions")
    log.append("dashboard:a:b", "user", "first")
    log.append("dashboard:a?b", "user", "second")
    names = sorted(entry["key"] for entry in log.list_sessions())
    assert len(names) == 2
    assert sorted(m["content"] for name in names for m in log.read_messages(name)) == [
        "first",
        "second",
    ]
    # A key its slug leaves as it is records nothing: its file's name already says it.
    log.append("plain-chat-name", "user", "third")
    assert RECORDED_KEY not in _first_line(log._path("plain-chat-name"))


@pytest.mark.asyncio
async def test_a_run_chat_is_listed_once(tmp_path):
    """🔴 Red before: ``personalclaw run``'s chat (``inbound:cli:<name>``) was listed twice, the
    second row read off its file's name (``inbound_cli_<name>``), which named no chat."""
    gateway = _make_state(tmp_path)
    run = await _open(gateway, "inbound:cli:nightly")
    _turn(run, "the first night's report")
    save_all_sessions_to_history(gateway)

    client = TestClient(TestServer(_make_app_with_agent_routes(gateway)))
    await client.start_server()
    try:
        resp = await client.get("/api/chat/sessions")
        assert resp.status == 200
        rows = await resp.json()
    finally:
        await client.close()
    rows = rows.get("sessions", rows) if isinstance(rows, dict) else rows
    keys = [row["key"] for row in rows]
    assert keys.count("inbound:cli:nightly") == 1, keys
    assert "inbound_cli_nightly" not in keys, keys


# ── every reader finds a chat's own transcript ───────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first", "first_mode", "second", "second_mode"),
    [
        ("a:b", "persistent", "a?b", "incognito"),
        ("c:d", "temporary", "c?d", "persistent"),
    ],
)
async def test_each_of_two_alike_chats_is_read_in_its_own_memory_mode(
    tmp_path, monkeypatch, first, first_mode, second, second_mode
):
    """🔴 Red before: the memory-mode readers found a transcript by its key's slug alone
    (``history.session_path``), so the second chat, whose file is the slug and a hash, read as the
    first. After a restart the transcripts are the only record of a chat's mode: the cleanup left an
    Incognito chat's leftovers, and removed a persistent chat's records when its alike was
    Temporary."""
    from personalclaw import memory_writes
    from personalclaw.history import session_path

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    gateway = _make_state(tmp_path / "sessions")
    for name, mode in ((first, first_mode), (second, second_mode)):
        chat = gateway.get_or_create_session(name, memory_mode=mode)
        _turn(chat, f"what {name} is about")
        # Saved as a live chat is, before a crash or a kill: a stop forgets a Temporary one.
        save_session_to_history(gateway, chat)

    log = gateway.conversation_log
    for name, mode in ((first, first_mode), (second, second_mode)):
        key = f"dashboard:{name}"
        assert memory_writes._transcribed(key) == mode
        assert memory_writes.transcript_keeps_nothing(key) is (mode != "persistent")
        assert session_path(key) == log._path(key)


@pytest.mark.asyncio
async def test_a_chat_keeps_its_own_transcript_once_its_alike_is_deleted(tmp_path, monkeypatch):
    """A chat whose alike held the slug keeps its own file when that chat is deleted: its key
    names the file with its hash while there is one, so it opens with its own turns, not on the
    empty slug."""
    import personalclaw.config.loader as cfg
    import personalclaw.session_workspace as ws

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path / "home")
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path / "home")
    gateway = _make_state(tmp_path)
    first = await _open(gateway, "a:b")
    _turn(first, "the garden plan")
    save_all_sessions_to_history(gateway)
    second = await _open(gateway, "a?b")
    _turn(second, "the kitchen quotes")
    save_all_sessions_to_history(gateway)
    theirs = gateway.conversation_log._path("dashboard:a?b")

    async with TestClient(TestServer(_make_app(gateway))) as client:
        deleted = await client.delete("/api/chat/sessions/a:b")
        assert deleted.status == 200, await deleted.text()
    assert not (tmp_path / "dashboard_a_b.jsonl").exists()

    restarted = _make_state(tmp_path)
    assert restarted.conversation_log._path("dashboard:a?b") == theirs
    reopened = restarted.get_or_create_session("a?b")
    assert [m["content"] for m in reopened.messages][::2] == ["the kitchen quotes"]
    _turn(reopened, "the tiles")
    save_all_sessions_to_history(restarted)
    assert _said(theirs) == ["the kitchen quotes", "the tiles"]
    assert not (tmp_path / "dashboard_a_b.jsonl").exists()
