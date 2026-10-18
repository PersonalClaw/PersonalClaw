"""The session-resurrection audit: a deleted chat stays deleted.

``api_chat_session_delete`` is a HARD delete — it purges the JSONL history, the
tool-result store and the turn-checkpoint tree, precisely so a destroyed conversation
cannot come back (see ``test_chat_hard_delete.py``). Measured on ``origin/main``
against a live gateway, two writers undid that:

* ``POST /api/chat`` naming a hard-deleted key answered **200**, put the key back in
  ``GET /api/chat/sessions`` and served ``GET /api/chat/sessions/{key}`` 200 again —
  carrying only the resurrecting turn, so the conversation returned as a half-session.
* ``POST /api/chat/sessions/{session}/resume`` did the same, with an empty transcript.

There are **50** ``{session}``-addressed routes; ``/resume`` is one of them. Driving the
other 49 against an unknown key on a live gateway, **47 already answered 404**. The two
that did not are both under ``/api/skills/ephemeral/`` and neither writes a chat session:
``GET .../{session}`` answers an empty ``200`` (it lists a collection) and
``POST .../{session}/promote`` answers ``400`` because body validation runs first. So
this is not a missing convention — it is two writers outside an established one.

**What this module audits.** The durable question is *who owns "does this session key
exist, and may it be written?"*. That owner is
:func:`~personalclaw.dashboard.chat_persistence.session_key_exists`, and the hazard is a
writer that reaches ``state.get_or_create_session`` with a CLIENT-SUPPLIED name without
asking it first — because ``get_or_create_session`` mints a blank session on a miss.

The census is an AST walk (no import side effects) over every
``get_or_create_session`` call site in ``src/personalclaw``, keyed by
``file::qualname``. The keys are partitioned across the two hardcoded allowlists below.
**A new, unmapped call site reds this test naming its file:line** — the author must
consciously classify it as client-named (and then it must call the owner) or
creates-by-design. That conscious step is the control.

Adding a site → add its key to exactly one allowlist with a one-line reason. Removing a
site → drop its key. The test fails if the census and the union of the allowlists
disagree in either direction, so a stale entry is caught too. Modelled on
``test_spawn_ceiling_audit.py``, whose shape this is.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import chat_session_resurrection_audit_shared
import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_session_resurrection_audit_shared import (
    _CLIENT_NAMED_MUST_GUARD,
    _CREATES_BY_DESIGN,
    _OWNER,
    SRC,
    scan_source,
)
from chat_test_helpers import _make_app, _make_state

_isolate_home = chat_session_resurrection_audit_shared._isolate_home


# ── CLIENT-NAMED: the session name arrives from a REQUEST, so existence must be

# ── CREATES-BY-DESIGN: minting on a miss is the point. Either no name is passed at


# ── the scanner ───────────────────────────────────────────────────────────────


# ── vacuity: a census that measures nothing must not read as clean ────────────


# ── the classification ratchet ────────────────────────────────────────────────


def test_the_two_allowlists_are_disjoint():
    """A site cannot be both client-named and creates-by-design."""
    both = set(_CLIENT_NAMED_MUST_GUARD) & set(_CREATES_BY_DESIGN)
    assert not both, f"sites in both allowlists: {sorted(both)}"


# ── the vacuity floors for the ratchet, proved against source ─────────────────
#
# "No unmapped call site" and "every client-named writer guards" are both satisfied by a
# working scanner AND by a scanner that has stopped matching. These tell those apart.


_SYNTHETIC = '''
class State:
    def get_or_create_session(self, name=None):
        ...


async def a_new_unguarded_writer(request):
    """The hazard: a client-supplied name straight into the creator."""
    state = request.app["state"]
    name = request.match_info["session"]
    return state.get_or_create_session(name)


async def a_properly_guarded_writer(request):
    state = request.app["state"]
    name = request.match_info["session"]
    if not session_key_exists(state, name):
        return json_error("session_not_found", status=404)
    return state.get_or_create_session(name)
'''


def test_the_scanner_sees_a_planted_unguarded_writer():
    """The positive: a new creator call site is COUNTED, with its qualname."""
    creators: dict[str, list[int]] = {}
    guards: dict[str, list[int]] = {}
    scan_source(_SYNTHETIC, "synthetic.py", creators, guards)
    assert "synthetic.py::a_new_unguarded_writer" in creators, creators
    assert "synthetic.py::a_properly_guarded_writer" in creators, creators
    # The method DEFINITION is not a call site and must not be counted as one.
    assert "synthetic.py::State.get_or_create_session" not in creators, creators


def test_the_scanner_tells_a_guarded_writer_from_an_unguarded_one():
    """The negative half — otherwise "sees planted writers" could be satisfied by a
    scanner that simply flags everything."""
    creators: dict[str, list[int]] = {}
    guards: dict[str, list[int]] = {}
    scan_source(_SYNTHETIC, "synthetic.py", creators, guards)
    assert "synthetic.py::a_properly_guarded_writer" in guards, guards
    assert "synthetic.py::a_new_unguarded_writer" not in guards, (
        "the unguarded writer was scored as guarded — the owner matcher is matching the "
        "module, not the enclosing function"
    )


_HANDLERS = SRC / "dashboard" / "chat_handlers.py"
#: The live guard line in ``api_chat``. The plant below removes it.
_GUARD_LINE = "    if session_name and not session_key_exists(state, session_name):"


def test_stripping_the_real_guard_reds_the_ratchet():
    """The vacuity floor for :func:`test_every_client_named_writer_actually_calls_the_owner`.

    That test passes when the guard is present AND when the scanner has stopped
    detecting it. This proves the detector works against the source that actually
    ships: strip ``api_chat``'s guard out of the real module (a string swap on source
    read into memory — nothing is written to disk) and assert the ratchet's input
    changes. Do not delete this to make the ratchet cheaper; it is the only thing that
    makes "every client-named writer guards" mean anything.
    """
    real = _HANDLERS.read_text(encoding="utf-8")

    # 1) As it ships, api_chat guards — so a red below is the plant, not the tree.
    creators: dict[str, list[int]] = {}
    guards: dict[str, list[int]] = {}
    scan_source(real, "dashboard/chat_handlers.py", creators, guards)
    assert "dashboard/chat_handlers.py::api_chat" in guards, (
        "api_chat does not call the owner in the shipped source, so this floor cannot "
        f"attribute the plant. guards={sorted(guards)}"
    )

    # 2) The plant APPLIED. A swap that silently matched nothing would make step 3 a
    #    tautology about unmodified source — the exact shape of a vacuous guard.
    stripped = real.replace(
        _GUARD_LINE + '\n        return json_error("session_not_found", status=404)\n',
        "",
        1,
    )
    assert stripped != real, (
        f"the guard-strip swap matched nothing in {_HANDLERS.name}; _GUARD_LINE has "
        f"drifted from the shipped source, so this floor is measuring nothing. "
        f"Re-anchor it on api_chat's live `{_OWNER}` refusal."
    )
    assert _GUARD_LINE not in stripped, "the swap applied but the guard is still present"

    # 3) The detector notices — api_chat still reaches the creator, now unguarded.
    creators2: dict[str, list[int]] = {}
    guards2: dict[str, list[int]] = {}
    scan_source(stripped, "dashboard/chat_handlers.py", creators2, guards2)
    assert "dashboard/chat_handlers.py::api_chat" in creators2, (
        "api_chat stopped reaching get_or_create_session under the plant — the plant "
        "removed more than the guard, so this floor proves nothing"
    )
    assert "dashboard/chat_handlers.py::api_chat" not in guards2, (
        "stripping api_chat's `session_key_exists` call left the ratchet green. "
        "test_every_client_named_writer_actually_calls_the_owner is therefore measuring "
        f"the detector, not the code. guards={sorted(guards2)}"
    )


def test_a_new_unguarded_writer_planted_into_the_real_module_is_unmapped():
    """The vacuity floor for :func:`test_every_session_creator_call_site_is_classified`.

    "No unmapped call site" is also what a broken walk reports. So plant a brand-new
    unguarded writer into the REAL handlers module (in memory) and assert it lands
    OUTSIDE both allowlists — i.e. the classification ratchet would red and name it.
    """
    real = _HANDLERS.read_text(encoding="utf-8")
    planted = real + (
        "\n\nasync def api_chat_planted_writer(request):\n"
        '    state = request.app["state"]\n'
        '    return state.get_or_create_session(request.match_info["session"])\n'
    )
    creators: dict[str, list[int]] = {}
    guards: dict[str, list[int]] = {}
    scan_source(planted, "dashboard/chat_handlers.py", creators, guards)

    key = "dashboard/chat_handlers.py::api_chat_planted_writer"
    assert key in creators, f"the planted writer was not censused at all: {sorted(creators)}"
    allow = set(_CLIENT_NAMED_MUST_GUARD) | set(_CREATES_BY_DESIGN)
    assert key not in allow, (
        "the planted writer is somehow already allowlisted — the allowlist keys are not "
        "specific enough to catch a new site"
    )
    assert key not in guards, "the planted writer must not read as guarded"


# ── the behavioural rail: the defect itself, against an independent oracle ────
#
# The oracle is never the route under test. Existence is asserted from
# ``state._sessions`` and from the on-disk JSONL, plus a SECOND endpoint
# (``GET /api/chat/sessions/{key}``) — not from the send route's own reply.


def _seed_persisted_chat(state):
    """A session with turns PERSISTED to the JSONL (append alone is memory-only)."""
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    session = state.get_or_create_session(name=None)
    session.append("user", "the original question", "msg u0", broadcast=False)
    session.append("assistant", "the original answer", "msg a0", broadcast=False)
    session.drain()
    save_session_to_history(state, session, force=True)
    return session


async def _client(state) -> TestClient:
    client = TestClient(TestServer(_make_app(state)))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_a_send_to_a_deleted_session_is_a_coded_404(tmp_path):
    """The defect, end to end: DELETE then POST /api/chat must not resurrect.

    Measured before the fix: 200, and the key was back in the sidebar with only the
    resurrecting turn — "delete" degraded to "close".
    """
    from personalclaw.dashboard.chat_utils import _history_key_for

    state = _make_state(tmp_path)
    session = _seed_persisted_chat(state)
    key = session.key
    hk = _history_key_for(key)
    assert state.conversation_log.has_log(hk), "precondition: the history file exists"

    client = await _client(state)
    try:
        assert (await client.delete(f"/api/chat/sessions/{key}")).status == 200
        # Independent oracles: memory, disk, and a SECOND endpoint.
        assert key not in state._sessions
        assert not state.conversation_log.has_log(hk)
        assert (await client.get(f"/api/chat/sessions/{key}")).status == 404

        resp = await client.post("/api/chat", json={"session": key, "message": "resurrect"})
        assert resp.status == 404, f"a send to a deleted session answered {resp.status}"
        assert (await resp.json())["error"]["code"] == "session_not_found"

        # And it STAYED deleted — measured against the oracles, not the reply above.
        assert key not in state._sessions, "the refused send created the session anyway"
        assert not state.conversation_log.has_log(hk), "the refused send wrote history"
        assert (await client.get(f"/api/chat/sessions/{key}")).status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_resume_of_a_deleted_session_is_a_coded_404(tmp_path):
    """The second writer, found by deriving the family rather than taking the ticket."""
    from personalclaw.dashboard.chat_utils import _history_key_for

    state = _make_state(tmp_path)
    session = _seed_persisted_chat(state)
    key = session.key
    hk = _history_key_for(key)

    client = await _client(state)
    try:
        assert (await client.delete(f"/api/chat/sessions/{key}")).status == 200
        assert key not in state._sessions

        resp = await client.post(f"/api/chat/sessions/{key}/resume", json={})
        assert resp.status == 404, f"a resume of a deleted session answered {resp.status}"
        assert (await resp.json())["error"]["code"] == "session_not_found"
        assert key not in state._sessions, "the refused resume created the session anyway"
        assert not state.conversation_log.has_log(hk)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_never_created_key_is_refused_too(tmp_path):
    """A key that never existed is the same failure as a deleted one: a 200 claiming a
    continuity it does not have. Both writers refuse it."""
    state = _make_state(tmp_path)
    client = await _client(state)
    try:
        for path, body in (
            ("/api/chat", {"session": "chat-9999-1", "message": "hi"}),
            ("/api/chat/sessions/chat-9999-1/resume", {}),
        ):
            resp = await client.post(path, json=body)
            assert resp.status == 404, path
            assert (await resp.json())["error"]["code"] == "session_not_found", path
        assert "chat-9999-1" not in state._sessions
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_omitting_the_session_still_starts_a_new_conversation(tmp_path):
    """The contract the refusal does NOT break, and the reason a 404 is the right answer
    rather than "silently start a new session under a new key": starting a new
    conversation is already spelled by OMITTING ``session``. That is what the dashboard's
    ``ensureSession`` does, so a refusal on a named-but-dead key costs no reachable flow.
    """
    state = _make_state(tmp_path)
    client = await _client(state)
    try:
        # ``?ws=1`` so the reply is JSON rather than a held-open SSE stream.
        resp = await client.post("/api/chat?ws=1", json={"message": "a brand new chat"})
        assert resp.status == 200, await resp.text()
        minted = (await resp.json())["session"]
        assert minted and minted in state._sessions
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_an_archived_session_is_still_reopenable(tmp_path):
    """The refusal the guard must NOT introduce — and the whole reason the predicate is
    persisted-metadata instead of :func:`resolve_session`.

    ``/cleanup`` ARCHIVES a stale chat: ``save_session_to_history(closed=True)`` plus an
    eviction from ``state._sessions``. ``resolve_session`` answers ``None`` for exactly
    that state (``_rehydrate_session_from_history`` returns None on ``meta['closed']``),
    so a guard built on it would 404 a key that DOES exist — and specifically it would
    404 ``POST /resume``, the one route whose entire job is to reopen an archived chat
    (it clears the ``closed`` flag itself, a few dozen lines below the guard).

    The precondition asserts the two predicates genuinely DIVERGE on this state. Without
    it, a ``resolve_session`` swap could leave this test green for a trivial reason and
    the design call would be unfalsified.
    """
    from personalclaw.dashboard.chat_persistence import (
        resolve_session,
        save_session_to_history,
    )
    from personalclaw.dashboard.chat_utils import _history_key_for

    state = _make_state(tmp_path)
    session = _seed_persisted_chat(state)
    key = session.key
    hk = _history_key_for(key)

    # Archive it exactly as ``api_chat_sessions_cleanup`` does: soft-close, then evict.
    save_session_to_history(state, session, closed=True)
    state._sessions.pop(key, None)
    assert (
        state.conversation_log.get_metadata(hk).get("closed") is True
    ), "precondition: the archive did not set `closed` on the meta line"
    assert key not in state._sessions
    assert resolve_session(state, key) is None, (
        "precondition: `resolve_session` must answer None for an ARCHIVED session, or "
        "this test cannot tell the two predicates apart and proves nothing about the "
        "design call"
    )

    client = await _client(state)
    try:
        resp = await client.post(f"/api/chat/sessions/{key}/resume", json={"key": hk})
        assert resp.status == 200, (
            f"reopening an ARCHIVED session was refused ({resp.status}) — the guard's "
            f"predicate is answering 'is this session LIVE' instead of 'does this key "
            f"exist', and archival is not deletion: {await resp.text()}"
        )
        # Independent oracles, not the reopen's own reply: the `closed` flag is cleared
        # ON DISK, and a SECOND endpoint serves the original conversation again.
        assert not state.conversation_log.get_metadata(hk).get(
            "closed"
        ), "the reopen did not clear `closed` on disk"
        detail = await client.get(f"/api/chat/sessions/{key}")
        assert detail.status == 200
        contents = [m["content"] for m in (await detail.json())["messages"]]
        assert "the original question" in contents, contents
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_send_to_an_archived_session_is_not_refused(tmp_path):
    """The send half of the same control: archival is not deletion, so the guard must
    not turn an archived key into a 404 either.

    Scope note, measured against a live gateway on this branch and on ``origin/main``
    alike: ``POST /api/chat`` to an archived key is accepted and then CLOBBERS the
    archive — ``_rehydrate_session_from_history`` refuses a ``closed`` meta line, so
    ``get_or_create_session`` mints a blank session under the same key and the next
    flush overwrites the JSONL (title and turns gone). That is a SEPARATE pre-existing
    defect of the send path's rehydrate, unchanged by this commit, and deliberately not
    blessed here: this test asserts only that the key is not REFUSED, which is the
    claim the guard owns. Fixing the clobber is a product decision (should a send
    auto-reopen an archived chat?) and belongs in its own change.
    """
    from personalclaw.dashboard.chat_persistence import save_session_to_history
    from personalclaw.dashboard.chat_utils import _history_key_for

    state = _make_state(tmp_path)
    session = _seed_persisted_chat(state)
    key = session.key
    hk = _history_key_for(key)
    save_session_to_history(state, session, closed=True)
    state._sessions.pop(key, None)
    assert state.conversation_log.get_metadata(hk).get("closed") is True

    with patch("personalclaw.dashboard.chat_handlers.run_chat", new=AsyncMock()):
        client = await _client(state)
        try:
            resp = await client.post("/api/chat?ws=1", json={"session": key, "message": "hi"})
            assert resp.status == 200, (
                f"a send to an ARCHIVED session was refused ({resp.status}) — that is a "
                f"NEW refusal on a key that exists: {await resp.text()}"
            )
        finally:
            await client.close()


def test_a_disk_fault_is_never_reported_as_session_not_found(tmp_path, monkeypatch, caplog):
    """The fail-open branch, two-sided and against fixed literals.

    A broken conversation log must not make the guard say "your chat does not exist" —
    that reports the machine's problem as the user's data being gone. So the except
    branch reads as "exists".

    Both halves are asserted, because "returns True under a fault" alone is also what a
    helper that ignores its input returns: the SAME unknown key answers ``False`` with a
    working log and ``True`` with a raising one. If the fault injection silently missed
    its target, both halves would answer ``False`` and this reds.

    What fail-open does NOT buy, measured on this tree: a working send. ``api_chat``'s
    very next statement (``_rehydrate_session_from_history``) re-reads the same log and
    is not defensive, so the route answers **500**. The route-level assertion here is
    therefore the honest one — *not a 404, not ``session_not_found``* — and it is what
    flipping the branch to fail-closed breaks.
    """
    import logging

    from personalclaw.dashboard import chat_persistence as cp

    state = _make_state(tmp_path)
    assert cp.session_key_exists(state, "ghost-key") is False, (
        "with a WORKING log an unknown key must read as absent, or the contrast below "
        "is measuring nothing"
    )

    def _boom(*_a, **_k):
        raise OSError("simulated unreadable conversation log")

    monkeypatch.setattr(cp, "candidate_history_keys", _boom)
    with caplog.at_level(logging.WARNING):
        assert cp.session_key_exists(state, "ghost-key") is True, (
            "an unreadable log made the guard refuse — a disk fault now presents as the "
            "user's chat having been deleted"
        )
    assert any("session existence check failed" in r.getMessage() for r in caplog.records), (
        f"the fail-open branch swallowed a log read failure silently: "
        f"{[r.getMessage() for r in caplog.records]}"
    )


@pytest.mark.asyncio
async def test_the_route_under_a_disk_fault_does_not_answer_session_not_found(
    tmp_path, monkeypatch
):
    """The route-level half of the fail-open claim (see the unit test above for why the
    outcome is a 500 rather than a 200)."""
    from personalclaw.dashboard import chat_persistence as cp

    state = _make_state(tmp_path)

    def _boom(*_a, **_k):
        raise OSError("simulated unreadable conversation log")

    monkeypatch.setattr(cp, "candidate_history_keys", _boom)
    client = await _client(state)
    try:
        resp = await client.post("/api/chat?ws=1", json={"session": "ghost-key", "message": "hi"})
        assert resp.status != 404, (
            "a disk fault was reported to the user as `session_not_found` — the guard "
            "failed CLOSED and blamed their data for the machine's problem"
        )
        assert "session_not_found" not in (await resp.text())
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_disk_only_session_is_still_writable_after_eviction(tmp_path):
    """The regression the guard must NOT cause.

    After a gateway restart only recent/pinned/foldered sessions are resident, so the
    first send to an older chat arrives for a key that is on DISK and not in memory.
    That must still work — the guard's predicate is "persisted metadata", not "resident".
    Simulated by evicting the session from ``state._sessions`` while leaving its JSONL.
    """
    from personalclaw.dashboard.chat_utils import _history_key_for

    state = _make_state(tmp_path)
    session = _seed_persisted_chat(state)
    key = session.key
    hk = _history_key_for(key)

    # Evict from memory ONLY — the disk history stays, exactly as after a restart.
    state._sessions.pop(key, None)
    assert key not in state._sessions
    assert state.conversation_log.has_log(hk), "precondition: history still on disk"

    client = await _client(state)
    try:
        resp = await client.post("/api/chat?ws=1", json={"session": key, "message": "still here?"})
        assert resp.status == 200, (
            f"a disk-only session was refused ({resp.status}) — the guard is asking "
            f"'resident?' instead of 'persisted?': {await resp.text()}"
        )
        # And it came back WITH its history, not as a blank session.
        assert key in state._sessions
        contents = [m["content"] for m in state._sessions[key].messages]
        assert "the original question" in contents, contents
    finally:
        await client.close()


def _archive(state, key: str) -> str:
    """Put *key* into the ARCHIVED state and evict it, returning its history key.

    Archival is ``closed: true`` on the meta line — the flag
    ``save_session_to_history(..., closed=True)`` writes, which is what
    ``POST /api/chat/sessions/cleanup`` does to a stale session. Written directly (with
    the session evicted) because the cleanup route only archives sessions whose last
    turn is older than a day, and the point here is the resulting STATE, not the route
    that produces it.
    """
    from personalclaw.dashboard.chat_utils import _history_key_for

    hk = _history_key_for(key)
    path = state.conversation_log._path(hk)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    meta = json.loads(lines[0])
    meta["closed"] = True
    lines[0] = json.dumps(meta) + "\n"
    path.write_text("".join(lines), encoding="utf-8")
    state.conversation_log._invalidate_cache(hk)
    state._sessions.pop(key, None)
    return hk


@pytest.mark.asyncio
async def test_an_archived_session_is_still_writable_by_both_writers(tmp_path):
    """The OTHER regression the guard must not cause, and the reason the predicate is
    persisted-metadata rather than :func:`resolve_session`.

    ``resolve_session`` answers ``None`` for an ARCHIVED (``closed``) session — it is
    "give me the live session", and a soft-closed chat is not one. But archival is not
    deletion: the conversation is right there on disk, ``/cleanup`` put it away, and
    reopening it is the documented way back (``api_chat_session_resume`` explicitly
    CLEARS the closed flag). A predicate that asked ``resolve_session`` would 404 a key
    that exists — a brand-new refusal on live user data, worse than the bug being fixed.

    So the archived state is established first and asserted to be REAL — ``resolve_session``
    really does answer ``None`` there — and then both writers are required NOT to refuse
    it. Swap the predicate for ``resolve_session`` and this reds.

    Scope note: what each writer then DOES with an archived transcript is a property of
    the code behind the guard, not of the guard, and this PR changes none of it. Measured
    on this state: ``POST /resume`` answers 200 but with an EMPTY transcript, because
    everything past the guard reads ``body["key"]`` — the BARE key — while the file lives
    under the ``dashboard:`` form. That mismatch pre-dates this change (the guard itself
    asks ``resolve_history_key``, which tries both forms), so it is recorded here rather
    than asserted as correct. Do not "fix" this test by asserting the empty transcript is
    right.
    """
    from personalclaw.dashboard.chat_persistence import resolve_session

    state = _make_state(tmp_path)
    key = _seed_persisted_chat(state).key
    hk = _archive(state, key)

    # The archived state is real, and it IS the state resolve_session refuses. Asserted
    # against the disk (the oracle), not against the predicate under test.
    assert state.conversation_log.has_log(hk), "precondition: the archived history is on disk"
    assert state.conversation_log.get_metadata(hk).get("closed") is True
    assert resolve_session(state, key) is None, (
        "resolve_session no longer refuses an archived session, so this test can no "
        "longer tell the two predicates apart — re-anchor it on whatever `closed` became"
    )
    state._sessions.pop(key, None)  # resolve_session may have rehydrated on the way past

    client = await _client(state)
    try:
        # WRITER 2: resume is the documented way back into an archived chat.
        resp = await client.post(f"/api/chat/sessions/{key}/resume", json={})
        assert resp.status == 200, (
            f"resume of an ARCHIVED session was refused ({resp.status}) — the predicate "
            f"is asking 'is it live?' instead of 'does it exist?': {await resp.text()}"
        )

        # WRITER 1: and a send lands on it too.
        _archive(state, key)  # re-archive: resume is entitled to have cleared the flag
        resp = await client.post("/api/chat?ws=1", json={"session": key, "message": "still here?"})
        assert (
            resp.status == 200
        ), f"a send to an ARCHIVED session was refused ({resp.status}): {await resp.text()}"
        # The archived conversation is still THERE — the refusal-free path did not
        # quietly destroy what /cleanup put away. Oracle is the file, not a reply.
        assert state.conversation_log.has_log(hk)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_an_unreadable_log_does_not_lock_the_user_out(tmp_path):
    """The fail-open direction, measured through the scenario it is written for.

    ``resolve_history_key`` swallows a failed read into the same ``None`` it returns for
    "never persisted", so asking it alone cannot tell a hard-deleted key from a session
    whose file is present but unreadable — and answering "gone" for the second locks a
    user out of a chat that exists, which is a worse failure than the resurrection.

    The break is injected at the LOWEST layer that can fail on a real broken disk
    (``ConversationLog._read_metadata`` raising ``OSError``, exactly what
    ``path.read_text`` does on EIO), and the oracle is the route's status code plus the
    file's own presence on disk — never the predicate under test.
    """
    from personalclaw.dashboard.chat_utils import _history_key_for

    state = _make_state(tmp_path)
    key = _seed_persisted_chat(state).key
    hk = _history_key_for(key)
    state._sessions.pop(key, None)  # disk-only, as after a restart

    log = state.conversation_log
    log._invalidate_cache(hk)
    real_read = log._read_metadata

    def unreadable(k: str):
        if k in (hk, key):
            raise OSError("Input/output error")
        return real_read(k)

    log._read_metadata = unreadable
    assert log.has_log(hk), "precondition: the file is still right there"

    client = await _client(state)
    try:
        resp = await client.post("/api/chat?ws=1", json={"session": key, "message": "still here?"})
        assert resp.status == 200, (
            f"an unreadable log refused a send ({resp.status}) on a session whose file "
            f"IS on disk — a broken disk now locks the user out of their own chat, which "
            f"is what the fail-open branch exists to prevent: {await resp.text()}"
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_failing_open_still_refuses_a_key_with_nothing_on_disk(tmp_path):
    """The other half of fail-open, or it would be indistinguishable from no guard.

    "An unreadable log reads as exists" must not widen into "any unreadable state reads
    as exists": with the SAME read failure injected, a key that has no file at all is
    still refused. Otherwise the previous test could be satisfied by a predicate that
    simply answered ``True`` — which is the bug.
    """
    state = _make_state(tmp_path)
    log = state.conversation_log

    def unreadable(k: str):
        raise OSError("Input/output error")

    log._read_metadata = unreadable
    assert not log.has_log("chat-4242-gone"), "precondition: no file for this key"

    client = await _client(state)
    try:
        for path, body in (
            ("/api/chat?ws=1", {"session": "chat-4242-gone", "message": "hi"}),
            ("/api/chat/sessions/chat-4242-gone/resume", {}),
        ):
            resp = await client.post(path, json=body)
            assert resp.status == 404, (
                f"{path} answered {resp.status} for a key with NO file on disk — the "
                f"fail-open branch has swallowed the refusal whole: {await resp.text()}"
            )
            assert (await resp.json())["error"]["code"] == "session_not_found", path
        assert "chat-4242-gone" not in state._sessions
    finally:
        await client.close()


def test_a_predicate_that_blows_up_answers_exists(tmp_path):
    """The defensive backstop under the fail-open branch.

    Deliberately a UNIT test, and the only one in this module — the two tests above
    drive the fail-open path a real broken disk takes (a failed metadata read) through
    the routes, but this branch cannot be reached through a route with an observable
    outcome: anything violent enough to make the predicate itself RAISE also raises in
    ``_rehydrate_session_from_history`` three lines later, which has no ``except``, so
    the request 500s no matter which way the predicate answered. Asserting a 200 there
    would be asserting something the system cannot deliver.

    So the contract is pinned directly, against a FIXED LITERAL (``True``) and a fault
    built here rather than borrowed from the code under test: a ``conversation_log``
    whose every method raises. Without this, flipping that ``return True`` to
    ``return False`` is a silent, untested behaviour change.
    """
    from personalclaw.dashboard.chat_persistence import session_key_exists

    class Unusable:
        def has_log(self, key):
            raise OSError("Input/output error")

        def get_metadata(self, key):
            raise OSError("Input/output error")

    state = _make_state(tmp_path)
    state.conversation_log = Unusable()
    assert session_key_exists(state, "chat-1-1") is True, (
        "a conversation log that raises on every call read as 'this key does not "
        "exist' — a bug in the existence check now costs the user their chat"
    )
