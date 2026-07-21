"""Chat hard-delete — the explicit "Delete chat" button DESTROYS the conversation.

Product decision (2026-07-03): delete must purge every on-disk artifact (the JSONL
history + the per-session workspace incl. the tool_results raw store) and must NOT let
the chat resurrect on reopen. The soft-close/archive path stays ONLY in /cleanup.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Point config_dir at tmp_path so the tool_results store + workspace dirs land
    in the sandbox (the ConversationLog already uses tmp_path via _make_state)."""
    import personalclaw.config.loader as cfg
    import personalclaw.session_workspace as ws

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)
    return tmp_path


async def _client(state) -> TestClient:
    client = TestClient(TestServer(_make_app(state)))
    await client.start_server()
    return client


def _seed_persistent_chat(state):
    """A persistent session with a couple of turns, PERSISTED to the JSONL history
    the way a real turn does (via _save_session_to_history, not just append+drain —
    append only updates the in-memory list)."""
    from personalclaw.dashboard.chat_persistence import _save_session_to_history

    session = state.get_or_create_session(name=None)
    session.append("user", "hello", "msg u0", broadcast=False)
    session.append("assistant", "hi there", "msg a0", broadcast=False)
    session.drain()
    _save_session_to_history(state, session, force=True)
    return session


@pytest.mark.asyncio
async def test_delete_purges_history_file(tmp_path):
    state = _make_state(tmp_path)
    session = _seed_persistent_chat(state)
    from personalclaw.dashboard.chat_utils import _history_key_for

    hk = _history_key_for(session.key)
    assert state.conversation_log.has_log(hk), "precondition: history file exists"

    client = await _client(state)
    try:
        resp = await client.delete(f"/api/chat/sessions/{session.key}")
        assert resp.status == 200
        # The JSONL history file is GONE (not just flagged closed).
        assert not state.conversation_log.has_log(hk)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_delete_purges_tool_result_store(tmp_path):
    state = _make_state(tmp_path)
    session = _seed_persistent_chat(state)
    from personalclaw.dashboard.chat_utils import _history_key_for
    from personalclaw.tool_providers import result_store

    hk = _history_key_for(session.key)
    # A retained raw tool result under the canonical (dashboard:-prefixed) key.
    rid = result_store.store_result(hk, "SECRET FILE CONTENTS", content_type="log", tool="bash")
    assert result_store.fetch_slice(hk, rid).get("ok"), "precondition: raw store readable"

    client = await _client(state)
    try:
        resp = await client.delete(f"/api/chat/sessions/{session.key}")
        assert resp.status == 200
        # The raw tool-result store for this session is GONE — a deleted chat's
        # retained outputs (file contents / command output) must not survive on disk.
        assert not result_store.fetch_slice(hk, rid).get("ok")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_deleted_chat_does_not_resurrect_on_detail(tmp_path):
    state = _make_state(tmp_path)
    session = _seed_persistent_chat(state)
    key = session.key

    client = await _client(state)
    try:
        assert (await client.delete(f"/api/chat/sessions/{key}")).status == 200
        # Reopening the deleted chat's URL must 404 — NOT rehydrate it from disk
        # (the pre-fix soft-close cleared the `closed` flag and revived the session).
        detail = await client.get(f"/api/chat/sessions/{key}")
        assert detail.status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_delete_purges_disk_only_session(tmp_path):
    """A chat that's persisted on disk but NOT resident in state._sessions (the common
    case after a gateway restart — only recent/pinned/foldered sessions are restored)
    must STILL hard-delete. Regression guard for the in-memory-gated no-op BLOCKER:
    delete used to 404 (leaving JSONL + tool_results on disk + resurrectable)."""
    state = _make_state(tmp_path)
    session = _seed_persistent_chat(state)
    from personalclaw.dashboard.chat_utils import _history_key_for
    from personalclaw.tool_providers import result_store

    hk = _history_key_for(session.key)
    key = session.key
    rid = result_store.store_result(hk, "SECRET", content_type="log", tool="bash")
    # Evict it from memory to simulate a post-restart on-disk-only session.
    state._sessions.pop(key, None)
    assert state.conversation_log.has_log(hk), "precondition: on disk"
    assert key not in state._sessions, "precondition: not resident in memory"

    client = await _client(state)
    try:
        resp = await client.delete(f"/api/chat/sessions/{key}")
        assert resp.status == 200, "disk-only session must be deletable, not 404 no-op"
        # Fully purged: JSONL gone, raw store gone, detail 404 (no resurrect).
        assert not state.conversation_log.has_log(hk)
        assert not result_store.fetch_slice(hk, rid).get("ok")
        assert (await client.get(f"/api/chat/sessions/{key}")).status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_delete_truly_absent_session_404s(tmp_path):
    """A key that exists in neither memory nor on disk still 404s (no phantom purge)."""
    state = _make_state(tmp_path)
    client = await _client(state)
    try:
        resp = await client.delete("/api/chat/sessions/chat-nonexistent-999")
        assert resp.status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cleanup_still_soft_archives(tmp_path):
    """The /cleanup path is UNCHANGED — it soft-closes (archives, resumable), it must
    not start hard-deleting. Guards that the hard-delete change was scoped to the
    explicit Delete button only. Seeds an OLD last-activity so the staleness gate fires."""
    state = _make_state(tmp_path)
    from personalclaw.dashboard.chat_persistence import _save_session_to_history
    from personalclaw.dashboard.chat_utils import _history_key_for

    session = state.get_or_create_session(name=None)
    # Old timestamps so cleanup considers it stale (>1 day inactive).
    session.append("user", "hello", "msg u0", broadcast=False, ts="2020-01-01T00:00:00+00:00")
    session.append("assistant", "hi", "msg a0", broadcast=False, ts="2020-01-01T00:00:01+00:00")
    session.drain()
    _save_session_to_history(state, session, force=True)
    hk = _history_key_for(session.key)

    client = await _client(state)
    try:
        resp = await client.post(
            "/api/chat/sessions/cleanup",
            json={"max_inactive_days": 1, "active_session": "other"},
        )
        assert resp.status == 200
        body = await resp.json()
        assert session.key in body.get("keys", []), "the stale session should have been archived"
        # cleanup archives (closed=True) — the history file is KEPT, not purged.
        assert state.conversation_log.has_log(hk), "cleanup must archive, not hard-delete"
        meta = state.conversation_log.get_metadata(hk)
        assert meta.get("closed") is True
    finally:
        await client.close()
