"""A Temporary chat is forgotten when its session ends, however the session ends.

The mode's notice promises it: "this chat is forgotten when its session ends". The session lives
in the gateway running it, so it ends when that gateway stops or restarts (cleanly or not), when
the chat is deleted, and when an inactive chat is evicted. What the chat keeps on disk while it
runs goes then: its transcript, its working folder, its turn checkpoints and the files attached to
it. Its page never opens again, a send to it starts nothing, and no backup or export taken while
it ran carries it. A persistent or an incognito chat is kept as before.

Every assertion reads the disk or a second route, never the reply of the route under test.
"""

from __future__ import annotations

import tarfile
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app

from personalclaw import turn_checkpoints
from personalclaw.config import loader as config_loader
from personalclaw.dashboard.chat_persistence import (
    restore_recent_sessions,
    save_all_sessions_to_history,
    save_session_to_history,
)
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.session_workspace import workspace_path
from personalclaw.tool_providers import result_store

ASKED = "Am I on track with ai_tools this quarter?"
KEPT = "What did the plumber quote for the kitchen?"


def _state() -> DashboardState:
    """A gateway's chat state over the test's own home, transcripts where a real one keeps them."""
    sessions = MagicMock(count=0)
    sessions.remove = AsyncMock()
    sessions.get_pid = MagicMock(return_value=None)
    return DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=config_loader.config_dir() / "sessions"),
    )


def _chat(state: DashboardState, mode: str, asked: str, *, ts: str = ""):
    """A chat in *mode* with one attached upload and one turn, saved as a running chat saves it,
    plus the raw tool result and the turn checkpoint a turn leaves behind. Returns
    ``(session, upload)``."""
    home = config_loader.config_dir()
    upload = home / "uploads" / f"{uuid.uuid4().hex}_2026-household-budget.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_text("category,budget,spent\nai_tools,315.00,308.56\n", encoding="utf-8")
    session = state.get_or_create_session(
        name=None, memory_mode=None if mode == "persistent" else mode
    )
    session.append("user", asked, ts=ts, broadcast=False, meta={"files": [str(upload)]})
    session.append("assistant", "Spent to date: 308.56 of 315.00.", ts=ts, broadcast=False)
    session.drain()
    save_session_to_history(state, session, force=True)
    history_key = _history_key_for(session.key)
    result_store.store_result(history_key, "raw tool output", content_type="log", tool="bash")
    checkpoint = turn_checkpoints.session_dir(history_key)
    checkpoint.mkdir(parents=True, exist_ok=True)
    (checkpoint / "manifest.json").write_text("{}", encoding="utf-8")
    return session, upload


def _traces(state: DashboardState, key: str, upload) -> dict[str, bool]:
    history_key = _history_key_for(key)
    return {
        "transcript": state.conversation_log.has_log(history_key),
        "working folder": workspace_path(history_key).exists(),
        "turn checkpoints": turn_checkpoints.session_dir(history_key).exists(),
        "attached upload": upload.exists(),
    }


NOTHING = {
    "transcript": False,
    "working folder": False,
    "turn checkpoints": False,
    "attached upload": False,
}


async def _client(state: DashboardState) -> TestClient:
    client = TestClient(TestServer(_make_app(state)))
    await client.start_server()
    return client


def test_the_last_save_before_the_gateway_stops_forgets_it_and_keeps_the_others():
    state = _state()
    temporary, its_upload = _chat(state, "temporary", ASKED)
    persistent, kept_upload = _chat(state, "persistent", KEPT)
    incognito, incognito_upload = _chat(state, "incognito", "a private question")
    assert all(_traces(state, temporary.key, its_upload).values()), "precondition: all written"

    save_all_sessions_to_history(state)

    assert _traces(state, temporary.key, its_upload) == NOTHING
    assert temporary.key not in state._sessions
    assert state.conversation_log.has_log(_history_key_for(persistent.key))
    assert kept_upload.exists(), "a kept chat's upload is a file in its own right"
    assert state.conversation_log.has_log(_history_key_for(incognito.key))
    assert incognito_upload.exists()


def test_a_start_after_a_crash_forgets_what_the_crash_left():
    crashed = _state()
    temporary, its_upload = _chat(crashed, "temporary", ASKED)
    persistent, _ = _chat(crashed, "persistent", KEPT)
    # The gateway died without its last save: everything the running chat wrote is still there.
    assert all(_traces(crashed, temporary.key, its_upload).values())

    started = _state()
    restore_recent_sessions(started, window_minutes=0)

    assert _traces(started, temporary.key, its_upload) == NOTHING
    assert temporary.key not in started._sessions
    assert persistent.key in started._sessions, "the start still restores the kept chats"


@pytest.mark.asyncio
async def test_its_page_never_opens_again_and_a_send_to_it_starts_nothing():
    stopped = _state()
    opened, opened_upload = _chat(stopped, "temporary", ASKED)
    sent_to, sent_upload = _chat(stopped, "temporary", "a second temporary question")
    # A stop that could not finish its last save, read by the next gateway before its start pass.
    running = _state()

    client = await _client(running)
    try:
        page = await client.get(f"/api/chat/sessions/{opened.key}")
        assert page.status == 404
        assert _traces(running, opened.key, opened_upload) == NOTHING
        assert (await client.post(f"/api/chat/sessions/{opened.key}/resume", json={})).status == 404

        sent = await client.post(
            "/api/chat", json={"session": sent_to.key, "message": "still here?"}
        )
        assert sent.status == 404
        assert (await sent.json())["error"]["code"] == "session_not_found"
        assert sent_to.key not in running._sessions, "the refused send started the chat again"
        assert _traces(running, sent_to.key, sent_upload) == NOTHING
        assert (await client.get(f"/api/chat/sessions/{sent_to.key}")).status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_while_its_session_runs_a_reload_still_opens_it():
    state = _state()
    temporary, its_upload = _chat(state, "temporary", ASKED)

    client = await _client(state)
    try:
        page = await client.get(f"/api/chat/sessions/{temporary.key}")
        assert page.status == 200
        body = await page.json()
        assert body["memory_mode"] == "temporary"
        assert ASKED in [m.get("content") for m in body["messages"]]
    finally:
        await client.close()
    assert all(_traces(state, temporary.key, its_upload).values())


@pytest.mark.asyncio
async def test_deleting_it_takes_its_attached_files_and_a_kept_chat_keeps_its_own():
    state = _state()
    temporary, its_upload = _chat(state, "temporary", ASKED)
    persistent, kept_upload = _chat(state, "persistent", KEPT)

    client = await _client(state)
    try:
        assert (await client.delete(f"/api/chat/sessions/{temporary.key}")).status == 200
        assert (await client.delete(f"/api/chat/sessions/{persistent.key}")).status == 200
    finally:
        await client.close()

    assert _traces(state, temporary.key, its_upload) == NOTHING
    assert not state.conversation_log.has_log(_history_key_for(persistent.key))
    assert kept_upload.exists(), "Files lists a kept chat's upload after the chat is deleted"


@pytest.mark.asyncio
async def test_evicting_it_as_inactive_forgets_it_where_a_kept_chat_is_archived():
    state = _state()
    long_ago = "2026-01-02T03:04:05+00:00"
    temporary, its_upload = _chat(state, "temporary", ASKED, ts=long_ago)
    persistent, kept_upload = _chat(state, "persistent", KEPT, ts=long_ago)

    client = await _client(state)
    try:
        resp = await client.post("/api/chat/sessions/cleanup", json={"max_inactive_days": 1})
        assert resp.status == 200
    finally:
        await client.close()

    assert _traces(state, temporary.key, its_upload) == NOTHING
    kept = state.conversation_log.get_metadata(_history_key_for(persistent.key))
    assert kept.get("closed") is True, "a kept chat is archived to history, as before"
    assert kept_upload.exists()


def test_a_save_still_in_flight_for_it_writes_nothing_back():
    state = _state()
    temporary, its_upload = _chat(state, "temporary", ASKED)
    save_all_sessions_to_history(state)
    assert _traces(state, temporary.key, its_upload) == NOTHING

    # The flush loop took its list of sessions before the chat was forgotten.
    temporary.append("user", "one more", broadcast=False)
    save_session_to_history(state, temporary, force=True)

    assert not state.conversation_log.has_log(_history_key_for(temporary.key))


def test_no_backup_or_export_taken_while_it_runs_carries_it(tmp_path):
    from personalclaw.durability import shards
    from personalclaw.snapshot import snapshot_main

    home = config_loader.config_dir()
    state = _state()
    temporary, its_upload = _chat(state, "temporary", ASKED)
    persistent, kept_upload = _chat(state, "persistent", KEPT)
    temporary_history = _history_key_for(temporary.key)

    out = tmp_path / "snaps"
    assert snapshot_main([str(out)]) == 0
    [archive] = list(out.glob("personalclaw-snapshot-*.tar.gz"))
    members: dict[str, bytes] = {}
    with tarfile.open(archive, "r:gz") as tar:
        for info in tar.getmembers():
            if info.isfile():
                members[info.name] = tar.extractfile(info).read()
    snapshot = b"\n".join(members.values())
    assert ASKED.encode() not in snapshot
    assert not [n for n in members if its_upload.name in n or temporary.key in n]
    # The positive control: the same snapshot carries the kept chat and its upload.
    assert KEPT.encode() in snapshot
    assert [n for n in members if n.endswith(kept_upload.name)]

    exported = tmp_path / "shards"
    shards.export_shards(home, exported)
    written = b"\n".join(p.read_bytes() for p in exported.rglob("*.jsonl"))
    assert ASKED.encode() not in written
    assert KEPT.encode() in written

    # And the chat itself is still running: nothing was deleted to leave it out.
    assert state.conversation_log.has_log(temporary_history)
    assert its_upload.exists()
