"""A room member's call that needs your approval asks you the way a chat's call does.

A room runs its members' turns in the background (``rooms.arbiter.drain_round``), and that round
bound nobody to ask: every call a member's tier allowed and its runtime asked about was refused
with "no approval channel is bound to this turn". No card in the room, no Inbox row, no phone. The
round now asks through the approval registry a chat's call asks through, so the call is listed
where every approval is (the room's own view, Home, the Inbox, the phone, the channel approvals go
to), named by the room and the member; your Allow runs it and your Deny refuses it; an ask nobody
answers in time is refused for that; and with nowhere to ask, the call is refused for that, saying
so.

Driven through the shipped round, a real native runtime with the platform's own file tools, a real
``DashboardState`` registry and a live Inbox store, in a scratch home. Only the member's model is
scripted.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from test_a_read_only_room_member_reads_and_only_reads import _call, _NativeSessions, _ScriptedModel

from personalclaw.approval_answer import YOU
from personalclaw.approval_answer import agent as agent_principal
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.rooms import arbiter, posture, store
from personalclaw.rooms.turn import session_key

TITLE = "Should the incident notes say the key is claimed first?"
MEMBER = "executor"
NOTE = "incident-notes.md"
FIXED = "The key is claimed before the event is sent.\n"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """The home, the user's own folder, the operator ceiling and the spend meter are this test's."""
    import personalclaw.config.loader as cfg
    from personalclaw.guardrails.budgets import reset_meter
    from personalclaw.guardrails.ceiling import reset_ceiling

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path / "home")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    (tmp_path / "home").mkdir()
    (tmp_path / "user").mkdir()
    reset_ceiling()
    reset_meter()
    yield tmp_path
    reset_ceiling()
    reset_meter()


@pytest.fixture
def enabled(monkeypatch):
    """Rooms on, and the member's binding."""
    from personalclaw.config.loader import AgentProfile, AppConfig

    cfg = AppConfig()
    cfg.rooms.enabled = True
    cfg.agents = {MEMBER: AgentProfile()}
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls, *a, **k: cfg))
    return cfg


@pytest.fixture
def world(tmp_path, monkeypatch, enabled):
    """The registry the gateway builds, with a live Inbox store, a frame log and the audit rows."""
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.inbox import InboxStore
    from personalclaw.sel import SecurityEventLog

    state = DashboardState(
        sessions=MagicMock(count=0),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    frames: list[tuple[str, dict]] = []
    state.broadcast_ws = lambda kind, data=None: frames.append((kind, data or {}))  # type: ignore
    inbox = InboxStore(tmp_path / "inbox_items.json")
    state._inbox_svc = SimpleNamespace(inbox=inbox)
    audit: list = []
    monkeypatch.setattr(SecurityEventLog, "log", lambda self, event: audit.append(event))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return SimpleNamespace(
        state=state, frames=frames, inbox=inbox, audit=audit, workspace=workspace
    )


def _room(member_tier: str = "read_write") -> str:
    room = store.create_room(TITLE)
    store.add_member(
        room.id,
        MEMBER,
        role_blurb="fixes what the room agrees on",
        profile_narrowing={"tool_grants": member_tier},
    )
    store.append_message(
        room.id, role="user", content=f"@{MEMBER} fix the line in {NOTE}.", speaker=""
    )
    arbiter.queue_human_turns(room.id, f"@{MEMBER} fix the line in {NOTE}.")
    return room.id


def _model(target: Path) -> _ScriptedModel:
    """Writes the note, then says it did, or that it could not when its call was refused."""
    return _ScriptedModel(
        [
            [
                _call("write_file", {"path": str(target), "content": FIXED}),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
            [
                AgentEvent(kind=EVENT_TEXT_CHUNK, text="I tried to fix the line."),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
        ]
    )


async def _until(check, what: str, *, tries: int = 600) -> None:
    for _ in range(tries):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never happened: {what}")


async def _asking(w, room_id: str) -> tuple[asyncio.Task, str, dict]:
    """Start the room's round and wait until the member's call is waiting on you."""
    target = w.workspace / NOTE
    sessions = _NativeSessions(w.workspace, _model(target))
    task = asyncio.create_task(arbiter.drain_round(w.state, sessions, room_id))
    await _until(lambda: bool(w.state._pending_approvals), "the member's call never asked you")
    ((approval_id, entry),) = w.state._pending_approvals.items()
    return task, approval_id, entry


def _notes(room_id: str) -> list[str]:
    return [m["content"] for m in store.read_messages(room_id) if m["role"] == store.ROOM_NOTE_ROLE]


def _rows(w, tool: str) -> list:
    return [e for e in w.audit if e.event_type == "tool_invocation" and e.operation == tool]


def _inbox_row(w, approval_id: str):
    (row,) = [i for i in w.inbox.items.values() if i.refs.get("approval") == approval_id]
    return row


# ── it asks you where every approval is ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_members_call_that_asks_is_listed_where_every_approval_is(world):
    """🔴 Before: the round bound no approver, so the call was refused before anything was listed."""
    room_id = _room()
    task, approval_id, entry = await _asking(world, room_id)
    key = session_key(room_id, MEMBER)

    assert entry["session"] == key
    assert entry["tool"] == "write_file"
    assert entry["source_label"] == f"room “{TITLE}” · member “{MEMBER}”"
    # The member that asked is recorded as asking, and so may never answer it.
    assert entry["asked_by"] == agent_principal(key).label
    assert world.state.answer_refusal(approval_id, agent_principal(key))
    # The card's frame, and the Inbox row, both name the room and the member.
    assert [d["id"] for kind, d in world.frames if kind == "approval"] == [approval_id]
    row = _inbox_row(world, approval_id)
    lines = [line for line in row.message.splitlines() if line.strip()]
    assert lines[0] == "Approval needed: write_file", lines
    assert lines[1].startswith(
        f"{MEMBER} in the room “{TITLE}” is waiting for your decision on write_file"
    ), lines
    assert row.refs.get("session") == key
    assert not (world.workspace / NOTE).exists(), "the call ran before you answered"

    world.state.resolve_approval(approval_id, False, by=YOU)
    await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_your_allow_runs_the_call(world):
    room_id = _room()
    task, approval_id, _entry = await _asking(world, room_id)

    assert world.state.resolve_approval(approval_id, True, by=YOU) is True
    assert await asyncio.wait_for(task, timeout=10) == [MEMBER]

    assert (world.workspace / NOTE).read_text(encoding="utf-8") == FIXED
    assert _notes(room_id) == [], "an allowed call leaves no refusal on the transcript"
    (row,) = _rows(world, "write_file")
    assert row.outcome == "approved"
    assert row.metadata.get("decided_by") == "you"
    # The row names the member that made the call, in its room.
    assert row.caller_identity == session_key(room_id, MEMBER)
    assert row.agent == MEMBER
    assert row.source == "room"
    assert _inbox_row(world, approval_id).message.startswith("Approved: write_file")


@pytest.mark.asyncio
async def test_your_deny_refuses_it_and_the_room_says_you_denied_it(world):
    room_id = _room()
    task, approval_id, _entry = await _asking(world, room_id)

    assert world.state.resolve_approval(approval_id, False, by=YOU) is True
    await asyncio.wait_for(task, timeout=10)

    assert not (world.workspace / NOTE).exists()
    assert _notes(room_id) == [f"{MEMBER} was refused write_file — you denied it"]
    (row,) = _rows(world, "write_file")
    assert row.outcome == "rejected" and row.metadata.get("decided_by") == "you"
    assert row.caller_identity == session_key(room_id, MEMBER)


@pytest.mark.asyncio
async def test_an_ask_nobody_answers_in_time_is_refused_for_that(world, monkeypatch):
    monkeypatch.setattr(type(world.state), "approval_window_secs", lambda _self: 0.05)
    room_id = _room()
    target = world.workspace / NOTE
    sessions = _NativeSessions(world.workspace, _model(target))

    await asyncio.wait_for(arbiter.drain_round(world.state, sessions, room_id), timeout=10)

    assert not target.exists()
    assert _notes(room_id) == [
        f"{MEMBER} was refused write_file — nobody answered within 0 seconds"
    ], "an unanswered ask is not your Deny"
    (row,) = _rows(world, "write_file")
    assert row.outcome == "rejected" and row.metadata.get("decided_by") == "nobody"
    assert world.state._pending_approvals == {}


@pytest.mark.asyncio
async def test_with_nowhere_to_ask_the_call_is_refused_and_says_why(world):
    """A round that holds no approval registry has nobody to ask, so nothing runs unasked."""
    room_id = _room()
    target = world.workspace / NOTE
    sessions = _NativeSessions(world.workspace, _model(target))

    await asyncio.wait_for(arbiter.drain_round(None, sessions, room_id), timeout=10)

    assert not target.exists()
    assert _notes(room_id) == [f"{MEMBER} was refused write_file — {posture.NO_APPROVER_REASON}"]
    assert posture.NO_APPROVER_REASON == (
        "only you can approve a room member's call, and nothing here can ask you, so nobody "
        "can answer it"
    )


# ── it ends with the room, and with the member's seat ───────────────────────────────────────────


async def _route(handler, method: str, path: str, state, **match) -> web.Response:
    app = web.Application()
    app["state"] = state
    req = make_mocked_request(method, path, app=app, match_info=match)
    return await handler(req)


def _room_errors(caplog) -> list[str]:
    """ERROR lines the room code wrote: an archive or a removal is the human's decision, so the
    turn it ends must not read as a fault in the log."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("personalclaw.rooms") and r.levelno >= logging.ERROR
    ]


@pytest.mark.asyncio
async def test_archiving_the_room_ends_its_members_ask_everywhere(world, caplog):
    from personalclaw.dashboard.handlers.rooms import api_room_archive

    room_id = _room()
    task, approval_id, _entry = await _asking(world, room_id)

    resp = await _route(
        api_room_archive, "POST", f"/api/rooms/{room_id}/archive", world.state, room_id=room_id
    )
    assert resp.status == 200
    with caplog.at_level(logging.INFO):
        await asyncio.wait_for(task, timeout=10)
    assert _room_errors(caplog) == []

    assert approval_id not in world.state._pending_approvals
    ((outcome, ended),) = [
        (d["outcome"], d.get("ended"))
        for kind, d in world.frames
        if kind == "approval_resolved" and d["id"] == approval_id
    ]
    assert (outcome, ended) == ("cancelled", "the room that asked for it was archived")
    assert _inbox_row(world, approval_id).status == "expired"
    assert not (world.workspace / NOTE).exists()


@pytest.mark.asyncio
async def test_removing_the_member_ends_its_ask(world, caplog):
    from personalclaw.dashboard.handlers.rooms import api_room_member_remove

    room_id = _room()
    task, approval_id, _entry = await _asking(world, room_id)

    resp = await _route(
        api_room_member_remove,
        "DELETE",
        f"/api/rooms/{room_id}/members/{MEMBER}",
        world.state,
        room_id=room_id,
        name=MEMBER,
    )
    assert resp.status == 200
    with caplog.at_level(logging.INFO):
        await asyncio.wait_for(task, timeout=10)
    assert _room_errors(caplog) == []

    assert approval_id not in world.state._pending_approvals
    ended = [
        d.get("ended")
        for kind, d in world.frames
        if kind == "approval_resolved" and d["id"] == approval_id
    ]
    assert ended == [f"{MEMBER} left the room that asked for it"]
    assert not (world.workspace / NOTE).exists()


@pytest.mark.asyncio
async def test_an_answer_for_a_room_that_was_archived_runs_nothing(world):
    """The decision path's second line: an archived room's ask is refused at the answer too."""
    room_id = _room()
    task, approval_id, _entry = await _asking(world, room_id)

    store.archive_room(room_id)
    assert world.state.resolve_approval(approval_id, True, by=YOU) is False
    await asyncio.wait_for(task, timeout=10)

    assert not (world.workspace / NOTE).exists()
    assert approval_id not in world.state._pending_approvals


# ── the read-only member still asks nobody about a write ───────────────────────────────────────


@pytest.mark.asyncio
async def test_a_read_only_members_write_is_refused_by_its_tier_and_asks_nobody(world):
    """The tier is asked before you are, as a solo session's grant precedes its approval."""
    room_id = _room(member_tier="read")
    target = world.workspace / NOTE
    sessions = _NativeSessions(world.workspace, _model(target))

    await asyncio.wait_for(arbiter.drain_round(world.state, sessions, room_id), timeout=10)

    assert not target.exists()
    assert world.state._pending_approvals == {}
    assert [kind for kind, _d in world.frames if kind == "approval"] == []
    (note,) = _notes(room_id)
    assert note.startswith(f"{MEMBER} was refused write_file — its tools are read-only"), note
    assert json.dumps(store.read_messages(room_id))  # the transcript is still readable
