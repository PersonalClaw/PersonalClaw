"""An ARCHIVED chat keeps its transcript, and stays archived until asked otherwise.

Two symptoms of one defect: ``chat_persistence`` and ``chat_handlers`` decided "which
stored conversation does this key name?" and "does the persisted side hold more than my
buffer?" from IN-MEMORY facts.

**Symptom A — data loss.** A send to an archived, non-resident session destroyed its
transcript. ``session_key_exists`` correctly passes an archived key (archival is not
deletion), but the seed refused it, so ``get_or_create_session`` minted a BLANK session
whose ``_resumed_count`` was ``0``. The save guard asked ``_resumed_count > 0 and
len(msgs) <= _resumed_count`` — an in-memory question with an in-memory answer — so it did
not fire, and the blank buffer was written over the real transcript while the rebuilt
metadata line silently dropped ``closed``. Measured on a live gateway: **785 B / 2 turns →
693 B / 1 turn**, un-archived.

**Symptom B — /resume read the bare key against disk.** ``api_chat_session_resume`` took
``body.get("key", name)`` — a session NAME — and handed it to ``get_metadata``, ``_path``
and ``read_messages_chained``, while the canonical form sat computed four lines above and
was used only for an in-memory dedupe. So for a non-resident dashboard session it answered
``200`` with ``messages: []`` and ``meta_closed`` still true, and the clear-``closed``
block no-opped. Masked in practice because the startup restore usually makes the session
resident first, taking the early return.

The two are interlocked, which is why they are one change: making ``closed`` survive a
save is only safe BECAUSE ``/resume`` can now actually clear it. Fix either alone and
archival is either lossy or irreversible.

Every assertion below reads an INDEPENDENT oracle — bytes on disk, the parsed metadata
line, the message count from ``ConversationLog``, or a second endpoint — never the reply
of the route under test.
"""

from __future__ import annotations

import json

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Every write lands in tmp_path — never the real ``~/.personalclaw``."""
    import personalclaw.config.loader as cfg
    import personalclaw.dashboard.state as st
    import personalclaw.session_workspace as ws

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(st, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)
    return tmp_path


def _hk(name: str) -> str:
    from personalclaw.dashboard.chat_utils import _history_key_for

    return _history_key_for(name)


def _seed_two_turns(state, name: str = "chat-arch-1") -> str:
    """A session with TWO turns really on disk, then evicted from memory.

    Eviction is what makes it non-resident — the state a gateway restart leaves an
    un-foldered session in, and the state both symptoms need.
    """
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    session = state.get_or_create_session(name)
    session.append("user", "the original question", "msg u0", broadcast=False)
    session.append("assistant", "the original answer", "msg a0", broadcast=False)
    session.append("user", "a follow-up question", "msg u1", broadcast=False)
    session.append("assistant", "a follow-up answer", "msg a1", broadcast=False)
    session.drain()
    save_session_to_history(state, session, force=True)
    state._sessions.pop(name, None)
    return name


def _archive_on_disk(state, name: str) -> None:
    """Set ``closed`` directly on the metadata line, with no session resident.

    Deliberately NOT via ``/cleanup``: that pops the session from memory and writes
    through ``save_session_to_history``, so a later shutdown flush of the same live state
    could rebuild the line. Writing the flag while nothing is resident is the only way to
    construct a genuinely archived, genuinely non-resident session.
    """
    hk = _hk(name)
    path = state.conversation_log._path(hk)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    meta = json.loads(lines[0])
    meta["closed"] = True
    lines[0] = json.dumps(meta) + "\n"
    path.write_text("".join(lines), encoding="utf-8")
    state.conversation_log._meta_cache.pop(hk, None)
    state.conversation_log._msg_cache.pop(hk, None)


def _disk(state, name: str) -> tuple[int, int, bool]:
    """The independent oracle: (bytes on disk, transcript messages, closed flag)."""
    hk = _hk(name)
    path = state.conversation_log._path(hk)
    state.conversation_log._meta_cache.pop(hk, None)
    state.conversation_log._msg_cache.pop(hk, None)
    return (
        path.stat().st_size,
        len(state.conversation_log.read_messages(hk)),
        bool(state.conversation_log.get_metadata(hk).get("closed")),
    )


async def _client(state) -> TestClient:
    client = TestClient(TestServer(_make_app(state)))
    await client.start_server()
    return client


# ── Symptom A: the transcript survives, and stays archived ───────────────────────


@pytest.mark.asyncio
async def test_a_send_to_an_archived_session_does_not_destroy_its_transcript(tmp_path):
    """The measured defect: 785 B / 2 turns → 693 B / 1 turn, ``closed`` cleared."""
    state = _make_state(tmp_path)
    name = _seed_two_turns(state)
    _archive_on_disk(state, name)

    size_before, msgs_before, closed_before = _disk(state, name)
    assert msgs_before == 4, "precondition: two turns really persisted"
    assert closed_before, "precondition: genuinely archived on disk"
    assert name not in state._sessions, "precondition: genuinely non-resident"

    client = await _client(state)
    try:
        resp = await client.post("/api/chat", json={"session": name, "message": "resurrect"})
        # The send is accepted — an archived session is writable ("archival is not
        # deletion"); it is the OVERWRITE that had to stop.
        assert resp.status == 200, f"a send to an archived session answered {resp.status}"
    finally:
        await client.close()

    size_after, msgs_after, closed_after = _disk(state, name)
    assert msgs_after >= msgs_before, (
        f"transcript SHRANK: {msgs_before} → {msgs_after} messages "
        f"({size_before} B → {size_after} B)"
    )
    assert size_after >= size_before, f"file shrank: {size_before} B → {size_after} B"
    assert closed_after, "a normal send silently un-archived the session"
    # And the original content is still there, verbatim — a count can be satisfied by
    # the wrong four messages.
    hk = _hk(name)
    contents = [m.get("content") for m in state.conversation_log.read_messages(hk)]
    assert "the original question" in contents
    assert "a follow-up answer" in contents


@pytest.mark.asyncio
async def test_the_archived_send_seeds_from_disk_rather_than_minting_a_blank(tmp_path):
    """The mechanism, asserted directly: the session is seeded, not blank.

    A count guard alone cannot save a transcript from a blank buffer — a single turn that
    emits more messages than a short transcript holds would still be "bigger". The seed
    is what makes the buffer the SAME conversation, grown.
    """
    state = _make_state(tmp_path)
    name = _seed_two_turns(state)
    _archive_on_disk(state, name)

    client = await _client(state)
    try:
        assert (
            await client.post("/api/chat", json={"session": name, "message": "hi"})
        ).status == 200
    finally:
        await client.close()

    session = state._sessions[name]
    assert session._resumed_count >= 4, (
        f"session was minted blank (_resumed_count={session._resumed_count}); the seed "
        "refused an archived key that session_key_exists had just declared writable"
    )
    contents = [m.get("content") for m in session.messages]
    assert "the original question" in contents, "the seeded buffer lost the transcript"


@pytest.mark.asyncio
async def test_a_shutdown_flush_does_not_un_archive(tmp_path):
    """``save_all_sessions_to_history`` passes ``force=True`` and rebuilds the meta line.

    With no user in the loop at all, that used to drop ``closed`` — which is also what
    invalidated an earlier attempt to even REPRODUCE symptom A.
    """
    from personalclaw.dashboard.chat_persistence import (
        _rehydrate_session_from_history,
        save_all_sessions_to_history,
    )

    state = _make_state(tmp_path)
    name = _seed_two_turns(state)
    _archive_on_disk(state, name)
    # Make it resident the way the send path does, then flush as shutdown does.
    assert _rehydrate_session_from_history(state, name, include_archived=True) is not None
    save_all_sessions_to_history(state)

    _, msgs, closed = _disk(state, name)
    assert closed, "the shutdown flush un-archived the session"
    assert msgs == 4, f"the shutdown flush changed the transcript: 4 → {msgs}"


@pytest.mark.asyncio
async def test_archiving_lands_its_flag_without_rewriting_the_transcript(tmp_path):
    """``closed=True`` with a buffer poorer than disk is a METADATA-only write.

    The archive still has to take effect; it just must not pay for the flag with the
    transcript. (The ``side`` buffer twenty lines away already prefers the persisted copy
    for exactly this reason; ``messages`` never did.)
    """
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    state = _make_state(tmp_path)
    name = _seed_two_turns(state)
    size_before, msgs_before, _ = _disk(state, name)

    blank = state.get_or_create_session(name)
    blank.append("user", "a single stray turn", "msg u", broadcast=False)
    blank.drain()
    save_session_to_history(state, blank, closed=True)

    size_after, msgs_after, closed_after = _disk(state, name)
    assert closed_after, "the archive flag never landed"
    assert msgs_after == msgs_before, f"archiving cost the transcript: {msgs_before} → {msgs_after}"
    assert size_after >= size_before - 2, "archiving rewrote the file from the poorer buffer"


# ── Symptom B: /resume reads the resolved key ────────────────────────────────────


@pytest.mark.asyncio
async def test_resume_of_a_non_resident_session_returns_its_messages(tmp_path):
    """Measured before the fix: ``200`` with ``messages: []`` for any non-resident chat."""
    state = _make_state(tmp_path)
    name = _seed_two_turns(state)
    assert name not in state._sessions, "precondition: genuinely non-resident"

    client = await _client(state)
    try:
        resp = await client.post(f"/api/chat/sessions/{name}/resume", json={})
        assert resp.status == 200
        body = await resp.json()
        assert body["total"] == 4, f"resume served total={body['total']} for a 4-message file"
        assert len(body["messages"]) == 4, f"resume served {len(body['messages'])} messages"
        assert any(
            m.get("content") == "the original question" for m in body["messages"]
        ), "resume served messages, but not this session's"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_resume_of_a_non_resident_archived_session_clears_closed_on_disk(tmp_path):
    """The un-archive path. It is now the ONLY one, so it has to work off the real key."""
    state = _make_state(tmp_path)
    name = _seed_two_turns(state)
    _archive_on_disk(state, name)
    assert _disk(state, name)[2], "precondition: archived on disk"

    client = await _client(state)
    try:
        resp = await client.post(f"/api/chat/sessions/{name}/resume", json={})
        assert resp.status == 200
        assert len((await resp.json())["messages"]) == 4
    finally:
        await client.close()

    _, msgs, closed = _disk(state, name)
    assert not closed, "resume did not clear `closed` — archival would be irreversible"
    assert msgs == 4, f"resume changed the transcript: 4 → {msgs}"


@pytest.mark.asyncio
async def test_resume_honours_an_explicit_body_key(tmp_path):
    """``body["key"]`` may name a different session than the path; both must resolve."""
    state = _make_state(tmp_path)
    other = _seed_two_turns(state, "chat-arch-other")

    client = await _client(state)
    try:
        resp = await client.post("/api/chat/sessions/chat-arch-fresh/resume", json={"key": other})
        assert resp.status == 200
        body = await resp.json()
        assert body["total"] == 4, f"body-key resume served total={body['total']}"
        assert any(m.get("content") == "the original question" for m in body["messages"])
    finally:
        await client.close()


# ── The properties e3d0dcef0 established must survive ────────────────────────────


@pytest.mark.asyncio
async def test_a_hard_deleted_key_is_still_refused(tmp_path):
    """Routing delete through the owner must not weaken the refusal it enforces."""
    state = _make_state(tmp_path)
    name = _seed_two_turns(state)

    client = await _client(state)
    try:
        assert (await client.delete(f"/api/chat/sessions/{name}")).status == 200
        assert not state.conversation_log.has_log(_hk(name)), "the JSONL survived a hard delete"
        for path, body in (
            ("/api/chat", {"session": name, "message": "resurrect"}),
            (f"/api/chat/sessions/{name}/resume", {}),
        ):
            resp = await client.post(path, json=body)
            assert resp.status == 404, f"{path} answered {resp.status} on a deleted key"
            assert (await resp.json())["error"]["code"] == "session_not_found"
    finally:
        await client.close()


# ── The owner must actually RESOLVE, not just be called ──────────────────────────
#
# Every test above uses a dashboard session, whose file is under the `dashboard:` form —
# the one case where the prefix helper and the resolver agree. So they all passed with
# `persisted_history_key` reduced to a bare `_history_key_for` (measured: mutant M5
# survived the whole suite). The two answers only diverge for a session persisted under
# its OWN bare key, which is what a channel-provider thread is, so that is the case that
# holds the owner to its contract.


def _seed_bare_key_thread(state, key: str = "chan-thread-77") -> str:
    """A conversation persisted under its own BARE key, as a channel app writes it."""
    log = state.conversation_log
    log.append(key, "user", "thread question", source_thread=key, source_user="u1")
    log.append(key, "assistant", "thread answer", source_thread=key, source_user="bot")
    log.append(key, "user", "thread follow-up", source_thread=key, source_user="u1")
    log.append(key, "assistant", "thread follow-up answer", source_thread=key, source_user="bot")
    assert log.has_log(key), "precondition: the file is under the BARE key"
    assert not log.has_log(_hk(key)), "precondition: nothing under the dashboard: form"
    return key


def test_the_owner_resolves_a_bare_key_the_prefix_helper_would_miss(tmp_path):
    from personalclaw.dashboard.chat_utils import _history_key_for, persisted_history_key

    state = _make_state(tmp_path)
    key = _seed_bare_key_thread(state)
    assert persisted_history_key(state.conversation_log, key) == key
    assert _history_key_for(key) != key, "the prefix helper would have keyed a second file"


def test_a_save_does_not_orphan_a_bare_key_transcript(tmp_path):
    """The write must land in the file the conversation already lives in.

    Keyed off the prefix instead, the save wrote a SECOND, near-empty file beside the
    real transcript — and the guard, reading that empty file, saw nothing to protect.
    """
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    state = _make_state(tmp_path)
    key = _seed_bare_key_thread(state)
    before = len(state.conversation_log.read_messages(key))
    assert before == 4

    session = state.get_or_create_session(key)
    session.append("user", "a stray one-message buffer", "msg u", broadcast=False)
    session.drain()
    save_session_to_history(state, session)

    assert not state.conversation_log.has_log(
        _hk(key)
    ), "the save created a second file under the dashboard: form, orphaning the transcript"
    assert (
        len(state.conversation_log.read_messages(key)) == before
    ), "the one-message buffer replaced a 4-message thread transcript"


@pytest.mark.asyncio
async def test_resume_of_a_bare_key_thread_returns_its_messages(tmp_path):
    state = _make_state(tmp_path)
    key = _seed_bare_key_thread(state)

    client = await _client(state)
    try:
        resp = await client.post(f"/api/chat/sessions/{key}/resume", json={})
        assert resp.status == 200
        body = await resp.json()
        assert body["total"] == 4, f"resume served total={body['total']} for a 4-message thread"
        assert any(m.get("content") == "thread question" for m in body["messages"])
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_delete_of_a_bare_key_thread_unlinks_the_real_file(tmp_path):
    """Keyed off the prefix, the hard delete unlinked nothing and left an orphan that
    ``session_key_exists`` still reports as present — "delete" degraded back to the
    resurrection this route exists to prevent."""
    state = _make_state(tmp_path)
    key = _seed_bare_key_thread(state)

    client = await _client(state)
    try:
        assert (await client.delete(f"/api/chat/sessions/{key}")).status == 200
        assert not state.conversation_log.has_log(key), "the real transcript survived the delete"
        resp = await client.post("/api/chat", json={"session": key, "message": "resurrect"})
        assert resp.status == 404, f"a send to the deleted thread answered {resp.status}"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_an_unreadable_log_fails_open_on_the_save_guard(tmp_path):
    """A disk that misbehaves must never cost a live write. Same posture as the
    existence check: absence is unprovable, so do not refuse."""
    from personalclaw.dashboard import chat_persistence

    state = _make_state(tmp_path)
    name = _seed_two_turns(state)

    def _boom(_key):
        raise OSError("disk on fire")

    state.conversation_log.read_messages = _boom  # type: ignore[method-assign]
    session = state.get_or_create_session(name)
    session.append("user", "written despite the fault", "msg u", broadcast=False)
    session.drain()
    chat_persistence.save_session_to_history(state, session)

    del state.conversation_log.read_messages
    _, msgs, _ = _disk(state, name)
    assert msgs == 1, f"the guard refused a live write on an unreadable log (msgs={msgs})"
