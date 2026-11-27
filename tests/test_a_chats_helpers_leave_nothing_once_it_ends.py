"""A chat's helpers end with it, and nothing of them stays behind: not their folders, not a backup's
copy of them, not a restart's notice.

Measured from the code: a Temporary chat that ended when its inactive session was evicted stopped
its own turn and left its helpers (subagents) alone. One still running went on with the chat's task,
and one waiting for your Allow still asked for it. A Delete stopped them, but every chat end left
each stopped or failed helper's folder (its task, its state and what it wrote) until the cleanup a
week later. A backup taken while a Temporary chat was open copied its helpers' folders and its
workflow runs, its rows of the run ledger with them. After a crash, the restart's notice named each
stopped helper's task and the path of what it had saved, a Temporary chat's too.

Driven through the real ends: the eviction route, a Delete, a gateway's stop and the start after a
crash, over a real ``SubagentManager``; the real snapshot and shard export; and the gateway's own
start-up pass over what a previous gateway left.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tarfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app
from test_ended_owner_ends_its_approvals import world  # noqa: F401 - a fixture
from test_ended_owner_ends_its_approvals import (
    _real_manager,
    _state_over_a_real_session_manager,
    _until,
)

from personalclaw import session_keys, subagent_persistence
from personalclaw.config import loader as config_loader
from personalclaw.dashboard.chat_forget import delete_chats
from personalclaw.dashboard.chat_persistence import (
    chat_has_ended,
    restore_recent_sessions,
    save_session_to_history,
)
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.workflows import ownership, store
from personalclaw.workflows.models import OriginKind, RunOrigin, WorkflowRun

CHAT = "chat-a"
KEY = f"dashboard:{CHAT}"
OTHER = "dashboard:chat-b"
#: How what a Temporary chat started reads once its end ends it (``started_work.TEMPORARY_ENDED``).
TEMPORARY_ENDED = "its Temporary chat ended"
#: Words only the chat's helpers hold.
ASKED = "the plumber's quote for the kitchen sink"
#: Words only another chat's helpers hold.
KEPT = "the dentist's opening hours"
LONG_AGO = "2026-01-02T03:04:05+00:00"


def _blocking(manager) -> asyncio.Event:
    """Make every helper that starts wait for its runtime forever: it is running, and never ends
    on its own. Returns the event set once one has started."""
    started = asyncio.Event()

    async def _waits(*_a, **_kw):
        started.set()
        await asyncio.Event().wait()

    manager._sessions.get_or_create = AsyncMock(side_effect=_waits)
    return started


def _folder(agent_id: str) -> Path:
    return subagent_persistence._subagents_dir() / agent_id


def _left(parent: str, task: str, *, tombstone: bool = True) -> str:
    """The folder a helper of *parent* leaves once it failed (or, without *tombstone*, the one a
    gateway that died left it running with): its state, what it wrote, its tombstone, and the file
    its agent session kept. Returns its id."""
    agent_id = uuid.uuid4().hex[:8]
    subagent_persistence.create_agent_folder(agent_id, task=task, parent_session=parent)
    subagent_persistence.write_result_chunk(agent_id, f"what it found so far: {task}")
    session_id = f"acp-{agent_id}"
    subagent_persistence.update_state(agent_id, session_id=session_id)
    sessions = config_loader.config_dir() / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{session_id}.jsonl").write_text(f'{{"task": "{task}"}}\n', encoding="utf-8")
    if tombstone:
        subagent_persistence.write_tombstone(agent_id, cause="error", recovery_action="pending")
    return agent_id


def _kept(agent_id: str) -> dict[str, bool]:
    session = config_loader.config_dir() / "sessions" / f"acp-{agent_id}.jsonl"
    return {"folder": _folder(agent_id).exists(), "session file": session.exists()}


GONE = {"folder": False, "session file": False}
THERE = {"folder": True, "session file": True}


async def _evict(state) -> None:
    """The inactive-chat cleanup, as the dashboard asks for it, once every message is old."""
    for session in state._sessions.values():
        for message in session.messages:
            message["ts"] = LONG_AGO
    client = TestClient(TestServer(_make_app(state)))
    await client.start_server()
    try:
        resp = await client.post("/api/chat/sessions/cleanup", json={"max_inactive_days": 1})
        assert resp.status == 200, await resp.text()
    finally:
        await client.close()


# ── a Temporary chat's end stops its helpers ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_evicted_temporary_chats_helpers_end_with_it(world):  # noqa: F811
    """🔴 Red before: the eviction stopped the chat's turn and none of its helpers. The running one
    and its own helper went on with the chat's task, and the one that asked still waited for an
    Allow, which would have started it."""
    state = world.state
    manager = _real_manager(state)
    started = _blocking(manager)
    session = state.get_or_create_session(CHAT, memory_mode="temporary")
    session.append("user", f"compare {ASKED}", broadcast=False)
    running = manager.spawn(f"compare {ASKED}", parent_session_key=KEY, approval_mode="auto")
    assert running is not None
    await asyncio.wait_for(started.wait(), 5)
    nested = manager.spawn(
        "check the first quote",
        parent_session_key=session_keys.SUBAGENT.key(running.id),
        approval_mode="auto",
    )
    asking = manager.spawn("draft the reply", parent_session_key=KEY)
    elsewhere = manager.spawn("tidy the notes", parent_session_key=OTHER, approval_mode="auto")
    assert nested is not None and asking is not None and elsewhere is not None
    await _until(lambda: f"spawn:{asking.id}" in state._pending_approvals, "it never asked")

    await _evict(state)

    assert CHAT not in state._sessions, "precondition: the cleanup evicted it"
    for info in (running, nested, asking):
        await _until(lambda info=info: info.done, f"{info.task!r} never ended")
        assert info.cancelled, info.task
        assert info.starter_ended == TEMPORARY_ENDED, "its report starts no turn and tells nobody"
    assert f"spawn:{asking.id}" not in state._pending_approvals, "its Allow still waited"
    waiting = {w.get("id") for w in subagent_persistence.list_waiting()}
    assert asking.id not in waiting, "a restart would bring the asking helper back"
    assert [i.task for i in (running, nested, asking) if _folder(i.id).exists()] == []
    # Another chat's helper is not this chat's to end.
    assert not elsewhere.cancelled and not elsewhere.starter_ended
    assert _folder(elsewhere.id).exists()

    await manager.stop_agents([elsewhere.id], because="the test ended")


# ── every chat end removes its helpers' folders ────────────────────────────────────────────────


async def _deleted(state) -> None:
    state.get_or_create_session(CHAT).append("user", f"compare {ASKED}", broadcast=False)
    assert (await delete_chats(state, [CHAT], by="you")).deleted


async def _evicted(state) -> None:
    session = state.get_or_create_session(CHAT, memory_mode="temporary")
    session.append("user", f"compare {ASKED}", broadcast=False)
    save_session_to_history(state, session, force=True)
    await _evict(state)
    assert CHAT not in state._sessions


async def _its_gateway_stopped(state) -> None:
    """The gateway's own stop (``GatewayOrchestrator._shutdown``), which ends the Temporary chats
    as a step of its own before the last save."""
    from test_gateway import _make_orchestrator

    session = state.get_or_create_session(CHAT, memory_mode="temporary")
    session.append("user", f"compare {ASKED}", broadcast=False)
    save_session_to_history(state, session, force=True)
    orch = _make_orchestrator()
    orch.dashboard_state, orch.subagent_mgr = state, state.subagents
    steps: list[str] = []
    step = orch._stopping_step

    async def _named(what, aw):
        steps.append(what)
        await step(what, aw)

    orch._stopping_step = _named
    await orch._shutdown()
    assert "the Temporary chats" in steps, f"the stop ended no Temporary chat: {steps}"


async def _started_after_a_crash(state) -> None:
    """The gateway died without its last save; the next one forgets what it left."""
    session = state.get_or_create_session(CHAT, memory_mode="temporary")
    session.append("user", f"compare {ASKED}", broadcast=False)
    save_session_to_history(state, session, force=True)
    started, _ = _state_over_a_real_session_manager(state.conversation_log._dir)
    restore_recent_sessions(started, window_minutes=0)
    assert not started.conversation_log.has_log(_history_key_for(CHAT))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "end",
    [_deleted, _evicted, _its_gateway_stopped, _started_after_a_crash],
    ids=lambda e: e.__name__,
)
async def test_each_end_of_a_chat_removes_its_helpers_folders(world, end):  # noqa: F811
    """🔴 Red before: each end left the folder of the helper that had failed, and of the helper
    that one had started, with its task, what it wrote and its agent session's file, until the
    cleanup a week later."""
    _real_manager(world.state)
    failed = _left(KEY, f"price {ASKED}")
    below = _left(session_keys.SUBAGENT.key(failed), "price the second quote")
    elsewhere = _left(OTHER, f"look up {KEPT}")

    await end(world.state)

    assert {"failed": _kept(failed), "its helper": _kept(below)} == {
        "failed": GONE,
        "its helper": GONE,
    }
    assert _kept(elsewhere) == THERE, "another chat's helper is not this chat's"


@pytest.mark.asyncio
async def test_a_helper_that_may_still_run_keeps_its_folder_and_those_above_it(world):  # noqa: F811
    """A folder goes only once its helper has ended: one this gateway could not stop, and one a
    gateway that died left with no tombstone, its process maybe still alive, stay, and so does
    each folder above one of them, which says whose work it is. The rest go."""
    state = world.state
    manager = _real_manager(state)
    started = _blocking(manager)
    state.get_or_create_session(CHAT).append("user", f"compare {ASKED}", broadcast=False)
    stuck = manager.spawn(f"compare {ASKED}", parent_session_key=KEY, approval_mode="auto")
    assert stuck is not None
    await asyncio.wait_for(started.wait(), 5)
    stop = manager.stop_agents
    manager.stop_agents = AsyncMock(return_value=0)  # it will not stop
    below_stuck = _left(session_keys.SUBAGENT.key(stuck.id), "price the second quote")
    above = _left(KEY, "price the first quote")
    left_running = _left(session_keys.SUBAGENT.key(above), "price the third quote", tombstone=False)
    failed = _left(KEY, "price the fourth quote")

    await delete_chats(state, [CHAT], by="you")

    assert not stuck.done, "precondition: it is still running"
    assert _folder(stuck.id).exists(), "a running helper's folder went"
    assert _kept(left_running) == THERE, "a previous gateway's running helper's folder went"
    assert _kept(above) == THERE, "the folder that says whose that helper is went"
    assert _kept(below_stuck) == GONE and _kept(failed) == GONE

    await stop([stuck.id], because="the test ended")


# ── a backup or an export taken while it runs holds none of them ───────────────────────────────


def _run(session_key: str, mode: ownership.MemoryMode, words: str) -> str:
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="compare-quotes",
            inputs={"topic": words},
            origin=RunOrigin(kind=OriginKind.CHAT, session_key=session_key),
            created_at="2026-01-01T00:00:00Z",
            extra=ownership.stamp_run_mode({}, mode),
        )
    )
    (store.run_dir(run.id) / "outputs.json").write_text(f'{{"found": "{words}"}}', encoding="utf-8")
    return run.id


def test_no_backup_or_export_taken_while_it_runs_holds_its_helpers_or_runs(tmp_path):
    """🔴 Red before: the snapshot left out the chat's transcript and copied its helpers' folders,
    its workflow run's folder and its rows of the run ledger, the export its ledger rows."""
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.durability import shards
    from personalclaw.history import ConversationLog
    from personalclaw.snapshot import snapshot_main

    home = config_loader.config_dir()
    state = DashboardState(
        sessions=MagicMock(count=0),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=home / "sessions"),
    )
    temporary = state.get_or_create_session(CHAT, memory_mode="temporary")
    temporary.append("user", f"compare {ASKED}", broadcast=False)
    save_session_to_history(state, temporary, force=True)
    kept = state.get_or_create_session("chat-b")
    kept.append("user", f"look up {KEPT}", broadcast=False)
    save_session_to_history(state, kept, force=True)
    helper = _left(KEY, f"price {ASKED}")
    below = _left(session_keys.SUBAGENT.key(helper), f"price the second {ASKED}")
    run = _run(KEY, ownership.MemoryMode.TEMPORARY, ASKED)
    step = _left(ownership.owned_key(run, "price"), f"step: {ASKED}")
    kept_helper = _left(OTHER, f"look up {KEPT}")
    kept_run = _run(OTHER, ownership.MemoryMode.NORMAL, KEPT)

    out = tmp_path / "snaps"
    assert snapshot_main([str(out)]) == 0
    [archive] = list(out.glob("personalclaw-snapshot-*.tar.gz"))
    members: dict[str, bytes] = {}
    with tarfile.open(archive, "r:gz") as tar:
        for info in tar.getmembers():
            if info.isfile():
                members[info.name] = tar.extractfile(info).read()
    held = [n for n in members for i in (helper, below, step, run) if f"/{i}/" in n]
    assert held == [], "its helpers' or its run's folders were copied"
    assert ASKED.encode() not in b"\n".join(members.values()), "its words are in the snapshot"
    [ledger] = [n for n in members if n.endswith("workflows/runs.db")]
    copy = tmp_path / "runs.db"
    copy.write_bytes(members[ledger])
    with sqlite3.connect(copy) as db:
        assert {r for (r,) in db.execute("SELECT id FROM runs")} == {kept_run}
    # The positive control: the same snapshot carries the other chat's helper and run.
    assert [n for n in members if f"/{kept_helper}/" in n]
    assert [n for n in members if f"/{kept_run}/" in n]

    exported = tmp_path / "shards"
    shards.export_shards(home, exported)
    written = b"\n".join(p.read_bytes() for p in exported.rglob("*.jsonl"))
    assert ASKED.encode() not in written
    assert KEPT.encode() in written

    # And the chat and its work are still there: nothing was deleted to leave them out.
    assert _kept(helper) == THERE and store.get(run) is not None


# ── a restart's notice names no ended chat's helper ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_restart_names_no_ended_chats_helper_and_removes_what_it_left(world):  # noqa: F811
    """🔴 Red before: after a crash, the start forgot the Temporary chat and told you, in one
    notice, each of its stopped helpers' task and the path of what it had saved there, which then
    stayed for a week."""
    crashed = world.state
    temporary = crashed.get_or_create_session(CHAT, memory_mode="temporary")
    temporary.append("user", f"compare {ASKED}", broadcast=False)
    save_session_to_history(crashed, temporary, force=True)
    kept = crashed.get_or_create_session("chat-b")
    kept.append("user", f"look up {KEPT}", broadcast=False)
    save_session_to_history(crashed, kept, force=True)
    # What the gateway was running when it died: no tombstones.
    helper = _left(KEY, f"compare {ASKED}", tombstone=False)
    below = _left(session_keys.SUBAGENT.key(helper), f"price {ASKED}", tombstone=False)
    kept_helper = _left(OTHER, f"look up {KEPT}", tombstone=False)
    yours = _left(session_keys.DASHBOARD_UI, "tidy my downloads", tombstone=False)

    started, _ = _state_over_a_real_session_manager(crashed.conversation_log._dir)
    restore_recent_sessions(started, window_minutes=0)
    started.notify = MagicMock()
    manager = _real_manager(started)
    await GatewayOrchestrator._settle_left_behind_agents(
        SimpleNamespace(subagent_mgr=manager, dashboard_state=started)
    )

    [(_kind, title, body)] = [c.args for c in started.notify.call_args_list]
    assert "stopped" in title or "found" in title, title
    for what in (ASKED, helper, below, str(_folder(helper))):
        assert what not in body, f"the notice names {what!r}"
    assert body.count("A helper of a chat that has ended") == 2, body
    assert KEPT in body and kept_helper in body, "another chat's helper is named as before"
    # The owner's own key names no chat that ends: its helper is named and kept as before.
    assert not chat_has_ended(started, session_keys.DASHBOARD_UI)
    assert "tidy my downloads" in body and yours in body
    assert {"helper": _kept(helper), "below": _kept(below)} == {"helper": GONE, "below": GONE}
    assert _folder(kept_helper).exists(), "another chat's helper's folder is kept, as before"
    assert _folder(yours).exists(), "the owner's own helper's folder is kept, as before"


# ── a deleted run's helpers go with it ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [ownership.MemoryMode.TEMPORARY, ownership.MemoryMode.NORMAL])
async def test_a_deleted_runs_step_helpers_leave_nothing(world, mode):  # noqa: F811
    """🔴 Red before: a run's deletion ended what its steps started and removed the run's own
    folder, and left each step helper's folder, with its task, what it wrote and its agent
    session's file, a Temporary chat's run's included, until the cleanup a week later."""
    from personalclaw.workflows import service
    from personalclaw.workflows.models import RunStatus

    run = _run(KEY, mode, ASKED)
    other = _run(OTHER, ownership.MemoryMode.NORMAL, KEPT)
    for run_id in (run, other):
        ended = store.get(run_id)
        ended.status = RunStatus.CANCELLED
        store.save(ended)
    step = _left(ownership.owned_key(run, "price"), f"step: {ASKED}")
    below = _left(session_keys.SUBAGENT.key(step), f"its helper: {ASKED}")
    elsewhere = _left(ownership.owned_key(other, "price"), f"step: {KEPT}")

    done = await service.delete_run(run, supervisor=None)

    assert done.get("ok") and store.get(run) is None, done
    assert {"step": _kept(step), "below": _kept(below)} == {"step": GONE, "below": GONE}
    assert _kept(elsewhere) == THERE, "another run's step helper is not this run's"


# ── a backup leaves out only what is the chat's ────────────────────────────────────────────────


def test_a_helpers_session_id_names_no_file_outside_the_sessions_folder():
    """A helper's recorded session id is its agent client's: one that walks out of ``sessions/``
    names no file a backup or an export of an open Temporary chat leaves out."""
    from personalclaw.chat_traces import kept_by_temporary_chats
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog

    home = config_loader.config_dir()
    state = DashboardState(
        sessions=MagicMock(count=0),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=home / "sessions"),
    )
    temporary = state.get_or_create_session(CHAT, memory_mode="temporary")
    temporary.append("user", f"compare {ASKED}", broadcast=False)
    save_session_to_history(state, temporary, force=True)
    (home / "feedback.jsonl").write_text('{"id": "f1"}\n', encoding="utf-8")
    walker = _left(KEY, f"price {ASKED}")
    subagent_persistence.update_state(walker, session_id="../feedback")

    kept = kept_by_temporary_chats(home)

    assert _folder(walker).resolve() in kept, "precondition: the helper's folder is left out"
    assert (home / "feedback.jsonl").resolve() not in kept, "a file outside sessions/ was left out"


# ── an eviction whose end fails still forgets the chat ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_eviction_whose_end_fails_still_forgets_the_chat(world, monkeypatch):  # noqa: F811
    """A Temporary chat whose helpers' end raises is forgotten all the same, and the cleanup goes
    on: the request answers, and the chat is neither kept nor left half-ended."""
    from personalclaw import started_work

    _real_manager(world.state)
    monkeypatch.setattr(started_work, "end_chat", AsyncMock(side_effect=RuntimeError("no")))
    failed = _left(KEY, f"price {ASKED}")

    await _evicted(world.state)

    assert not world.state.conversation_log.has_log(_history_key_for(CHAT)), "it was kept"
    assert _kept(failed) == GONE


@pytest.mark.asyncio
async def test_a_deleted_runs_helper_that_may_still_run_keeps_its_folder(world):  # noqa: F811
    """The run's twin of the chat's row above: a helper under one of the run's steps that the
    run's end could not stop keeps its folder, and so does the step helper's above it, which says
    whose work it is. A failed step helper's folder goes all the same."""
    from personalclaw.workflows import service
    from personalclaw.workflows.models import RunStatus

    state = world.state
    manager = _real_manager(state)
    started = _blocking(manager)
    run = _run(KEY, ownership.MemoryMode.NORMAL, ASKED)
    cancelled = store.get(run)
    cancelled.status = RunStatus.CANCELLED
    store.save(cancelled)
    above = _left(ownership.owned_key(run, "price"), "step: price the first quote")
    stuck = manager.spawn(
        "price the second quote",
        parent_session_key=session_keys.SUBAGENT.key(above),
        approval_mode="auto",
    )
    assert stuck is not None
    await asyncio.wait_for(started.wait(), 5)
    stop = manager.stop_agents
    manager.stop_agents = AsyncMock(return_value=0)  # it will not stop
    failed = _left(ownership.owned_key(run, "draft"), "step: draft the reply")

    done = await service.delete_run(run, supervisor=SimpleNamespace(_state=state))

    assert done.get("ok"), done
    assert not stuck.done, "precondition: it is still running"
    assert _folder(stuck.id).exists(), "a helper still running lost its folder"
    assert _kept(above) == THERE, "the folder that says whose that helper is went"
    assert _kept(failed) == GONE

    await stop([stuck.id], because="the test ended")
