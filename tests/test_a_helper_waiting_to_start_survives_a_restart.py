"""A helper (a subagent) waiting to start survives a restart: it is kept, asked about again, or
ended with its chat told, and the restart's notice says which.

A spawn waits before it starts: for a free slot when the concurrency limit is full, or for its
owner's Allow of its start. It lived only in memory while it waited, so a restart (a crash, an
update, the owner's own Restart) dropped it: the chat that asked for it was told it had started,
nothing of it was on disk, the notice after the restart named only the agents it stopped, and a
status read of its id found nothing.

Now it is recorded the moment it starts to wait, beside the folder every agent keeps, with no grant
in the record; and the start after a restart takes each one back through the door a new spawn
takes, in the order they were made, asking again for a start nobody allowed, or ends it saying why
when what it reports to is gone. A spawn the owner cancelled stays cancelled.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import approval_grants, lasting_work, memory_writes, session_restrictions
from personalclaw.approval_grants import ToolDecision
from personalclaw.security import redact_for_model
from personalclaw.subagent import SubagentManager, agent_work_id
from personalclaw.subagent_orphans import Orphan, announce_orphans
from personalclaw.subagent_persistence import ASKING, QUEUED
from personalclaw.subagent_waiting import ENDED, STARTED, TakenBack, cut_off, take_back
from personalclaw.turn_source import arrived_on

#: The chat the helpers work for, and another one.
CHAT = "dashboard:chat-7-1791160000"
OTHER_CHAT = "dashboard:chat-8-1791160100"


def _forget_every_mark() -> None:
    """What a process's work marks dies with it: who asked for each session's work."""
    for key in list(session_restrictions._asked_by):
        session_restrictions.clear(key)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """The agents' folders, in a home of the test's own."""
    root = tmp_path / "subagents"
    monkeypatch.setattr("personalclaw.subagent_persistence._subagents_dir", lambda: root)
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        yield root
    _forget_every_mark()


def _state(home: Path, agent_id: str) -> dict[str, Any] | None:
    path = home / agent_id / "state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _sessions(*, working: bool = False) -> MagicMock:
    """A session manager whose agents answer nothing and end at once, or, *working*, work on until
    they are stopped."""
    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_approval_policy = MagicMock(return_value="")
    provider = AsyncMock()
    provider.context_usage_pct = lambda: 0.0

    async def _stream(*_a: object, **_kw: object):  # type: ignore[no-untyped-def]
        if working:
            await asyncio.Event().wait()
        return
        yield  # noqa: unreachable — an async generator that yields nothing

    provider.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    sessions.get_or_create = AsyncMock(return_value=(provider, True, False))
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    sessions.record_success = MagicMock()
    sessions.get_agent = MagicMock(return_value="")
    return sessions


def _builder() -> MagicMock:
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("built_message", None))
    ctx.hooks = SimpleNamespace(
        auto_approve_subagent_spawn=False, auto_approve_subagent_tools=False, on_tool_call=None
    )
    return ctx


class _Owner:
    """Where a start asks its owner: each ask is kept, and waits until the test answers it."""

    def __init__(self) -> None:
        self.asked: list[Any] = []
        self._answers: dict[str, asyncio.Future[ToolDecision]] = {}

    async def __call__(self, event: Any, parent_session_key: str = "") -> ToolDecision:
        self.asked.append(event)
        answer = self._answers.setdefault(
            str(event.request_id), asyncio.get_running_loop().create_future()
        )
        return await answer

    def answer(self, agent_id: str, allowed: bool) -> None:
        decision = ToolDecision(allowed, "approved" if allowed else "rejected", approval_grants.YOU)
        self._answers[f"spawn:{agent_id}"].set_result(decision)


def _manager(
    owner: _Owner, *, slots: int = 1, working: bool = False, **kwargs: Any
) -> SubagentManager:
    return SubagentManager(
        sessions=_sessions(working=working),
        ctx_builder=_builder(),
        max_concurrent=slots,
        on_spawn_approval=owner,
        delivery_coalesce_secs=0,
        **kwargs,
    )


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


async def _restart(manager: SubagentManager) -> None:
    """The gateway stops: its agents are cancelled, and its memory goes with the process."""
    await manager.cancel_all()
    _forget_every_mark()


@pytest.mark.asyncio
async def test_a_spawn_is_recorded_the_moment_it_starts_to_wait_and_carries_no_grant(home):
    """🔴 Red before: nothing of a queued or asking spawn was on disk."""
    owner = _Owner()
    manager = _manager(owner)
    asking = manager.spawn("Survey the venues for the offsite", parent_session_key=CHAT)
    queued = manager.spawn("Draft the agenda", parent_session_key=CHAT, agent="")
    await _settle()

    assert [e.request_id for e in owner.asked] == [f"spawn:{asking.id}"]
    assert (_state(home, asking.id) or {})["status"] == ASKING
    record = _state(home, queued.id) or {}
    assert queued.queued and record["status"] == QUEUED
    assert record["parent_session"] == CHAT and record["task"] == "Draft the agenda"
    request = record["request"]
    assert request["prompt"] == redact_for_model("Draft the agenda")
    assert request["requested_at"] == queued.started
    assert lasting_work.recorded(request[lasting_work.ASKED_BY]) == {}
    assert not {"approval_mode", "approved_at", "request_key"} & set(request)

    # What its caller holds on to is its caller's to start again, so nothing records it: a run's
    # step, an automation's agent, and a start its caller gave its own consent for.
    for held in (
        {"approval_mode": "auto"},
        {"trigger_id": "trg-digest"},
        {"parent_run": "workflow:run-1"},
        {"extra_env": {"PERSONALCLAW_LEAF": "1"}},
    ):
        info = manager.spawn("Summarize the thread", parent_session_key=CHAT, **held)
        assert info is not None and info.queued, held
        assert _state(home, info.id) is None, held
    await manager.cancel_all()


@pytest.mark.asyncio
async def test_after_a_restart_each_comes_back_in_its_order_under_the_same_limit(home):
    """🔴 Red before: the next start knew none of them."""
    owner = _Owner()
    before = _manager(owner)
    first = before.spawn("Survey the venues", parent_session_key=CHAT)
    second = before.spawn("Draft the agenda", parent_session_key=CHAT)
    third = before.spawn("Book the room", parent_session_key=OTHER_CHAT)
    await _settle()
    (card,) = owner.asked
    await _restart(before)

    owner = _Owner()
    after = _manager(owner)
    taken = take_back(after, lambda _key: "")
    await _settle()

    assert [(t.agent_id, t.was, t.now) for t in taken] == [
        (first.id, ASKING, ASKING),
        (second.id, QUEUED, QUEUED),
        (third.id, QUEUED, QUEUED),
    ]
    assert [info.id for info in after._queue] == [second.id, third.id]
    # The start nobody allowed asks again, with the same card.
    (again,) = owner.asked
    fields = ("request_id", "title", "tool_input", "tool_purpose", "risk_level")
    assert [getattr(again, f) for f in fields] == [getattr(card, f) for f in fields]
    assert after.get(first.id).task == "Survey the venues"

    # Allowed now, it runs; its slot goes to the next one in the order they were made.
    owner.answer(first.id, True)
    for _ in range(50):
        await asyncio.sleep(0)
        if len(owner.asked) == 2:
            break
    assert [e.request_id for e in owner.asked] == [f"spawn:{first.id}", f"spawn:{second.id}"]
    assert after.get(first.id).done and not after.get(first.id).error
    assert [info.id for info in after._queue] == [third.id]
    await after.cancel_all()


@pytest.mark.asyncio
async def test_a_spawn_someone_else_asked_for_comes_back_theirs(home):
    """🔴 Red before: nothing was recorded, and a restart that took it back with no record of who
    asked would have started his work on her YOLO. The control: her own starts on it."""
    owner = _Owner()
    before = _manager(owner)
    hers = before.spawn("Hold the slot", parent_session_key=CHAT)
    with memory_writes.derived_from(CHAT):
        memory_writes.asked_for(arrived_on("1791160000.000100", "U0COLLEAGUE", "teamchat"))
        his = before.spawn("Tidy the shared notes", parent_session_key=CHAT)
    await _settle()
    record = _state(home, his.id) or {}
    asked = lasting_work.recorded(record["request"][lasting_work.ASKED_BY])
    assert asked.get("source_user") == "U0COLLEAGUE", record
    await _restart(before)
    assert memory_writes.asked_for_work(agent_work_id(his.id)) == {}, "the mark died with it"

    owner = _Owner()
    after = _manager(owner, slots=2, is_yolo=lambda: True)
    taken = {t.agent_id: t for t in take_back(after, lambda _key: "")}
    await _settle()

    assert taken[hers.id].now == STARTED, "her own start runs on her YOLO"
    assert taken[his.id].now == ASKING, "her YOLO does not answer for his work"
    assert [e.request_id for e in owner.asked] == [f"spawn:{his.id}"]
    assert memory_writes.asked_for_work(agent_work_id(his.id)) == asked
    await after.cancel_all()


@pytest.mark.asyncio
async def test_one_that_can_no_longer_run_is_ended_and_its_chat_told_why(home):
    """🔴 Red before: each was dropped with no word. Its agent gone, it is refused at the door
    and its chat is told; its chat gone, or the turn that asked for it ended with the restart,
    nothing is there to tell but the restart's notice."""
    owner = _Owner()
    before = _manager(owner)
    holder = before.spawn("Hold the slot", parent_session_key=CHAT)
    with patch("personalclaw.subagent._validate_agent", lambda name: (name, "")):
        on_helper = before.spawn("Run on the helper", parent_session_key=CHAT, agent="helper-x")
    for_a_gone_chat = before.spawn("Report to a chat she deleted", parent_session_key=OTHER_CHAT)
    for_a_helper = before.spawn("Asked for by a helper", parent_session_key="subagent:5e1f00aa")
    await _settle()
    await _restart(before)

    delivered: list[list[Any]] = []

    async def _deliver(batch: list[Any]) -> None:
        delivered.append(batch)

    after = _manager(_Owner(), on_done=_deliver)
    chats = {OTHER_CHAT: "its chat no longer exists"}
    with patch("personalclaw.subagent_waiting.sel") as audit:
        taken = {t.agent_id: t for t in take_back(after, lambda key: chats.get(key, ""))}
    await after.flush_deliveries()

    assert taken[holder.id].now == ASKING
    gone = taken[on_helper.id]
    assert (gone.now, gone.told) == (ENDED, True)
    assert gone.why.startswith("unknown agent 'helper-x'"), gone.why
    assert taken[for_a_gone_chat.id] == TakenBack(
        for_a_gone_chat.id,
        "Report to a chat she deleted",
        QUEUED,
        ENDED,
        why="its chat no longer exists",
    )
    assert taken[for_a_helper.id].why == (
        "the turn that asked for it (a subagent) ended with the restart"
    )
    assert not taken[for_a_helper.id].told
    # Only the chat that is still there is told, by the delivery every ending takes.
    ((told,),) = delivered
    assert told.id == on_helper.id and told.done
    assert told.error == (
        "The gateway restarted before it started, and it cannot start now: " + gone.why
    )
    # Each ending reads by the id its chat was given, is audited, and leaves no record behind.
    for ended in (on_helper, for_a_gone_chat, for_a_helper):
        assert after.get(ended.id).done and _state(home, ended.id) is None
    outcomes = [c.kwargs["outcome"] for c in audit.return_value.log_tool_invocation.call_args_list]
    assert outcomes == ["cancelled"] * 3
    await after.cancel_all()


@pytest.mark.asyncio
async def test_an_apps_work_is_held_to_what_its_app_may_run_now(home):
    """🔴 Red before: dropped. Taken back, an app's work starts on its app's install consent, so
    it starts only while that app still runs agent work, and no wider than its tier is now."""
    owner = _Owner()
    before = _manager(owner)
    before.spawn("Hold the slot", parent_session_key=CHAT)
    switched_off = before.spawn(
        "Summarize the meeting",
        parent_session_key="app:minutes",
        app="minutes",
        capability_class="mutating",
    )
    narrowed = before.spawn(
        "Tag the notes", parent_session_key="app:notes", app="notes", capability_class="mutating"
    )
    await _settle()
    await _restart(before)

    tiers = {"minutes": "", "notes": "read"}
    delivered: list[list[Any]] = []

    async def _deliver(batch: list[Any]) -> None:
        delivered.append(batch)

    after = _manager(_Owner(), slots=3, on_done=_deliver)
    with (
        patch("personalclaw.apps.permissions.agent_tier_now", lambda app: tiers[app]),
        patch(
            "personalclaw.apps.permissions.no_agent_work",
            lambda app: f"app {app!r} is switched off",
        ),
        patch("personalclaw.subagent_waiting.sel"),
    ):
        taken = {t.agent_id: t for t in take_back(after, lambda _key: "")}

    await after.flush_deliveries()
    # An app's request is no chat's: its app reads how it ended by its id.
    assert taken[switched_off.id].now == ENDED and not taken[switched_off.id].told
    assert taken[switched_off.id].why == "app 'minutes' is switched off"
    assert after.get(switched_off.id).error.endswith("app 'minutes' is switched off")
    # Told where it reports, as any helper's ending is (the narrowed one's own ending follows).
    assert [switched_off.id] in [[info.id for info in batch] for batch in delivered]
    assert taken[narrowed.id].now == STARTED, "an app's agent starts on its install consent"
    assert after.get(narrowed.id).capability_class == "research"
    await after.cancel_all()


@pytest.mark.asyncio
async def test_a_step_agent_taken_back_keeps_the_run_it_works_for(home, tmp_path):
    """🔴 Red before: dropped. A restored agent of an allowed run's step must still be held to
    what that run may start (`SubagentInfo.workflow_run`), or it would start workflows unpinned;
    and it is the run's project's work, with the files its step may change, as its queue held it.
    Those files are held to what an automation may be saved to change, as at its step's save."""
    note = str((tmp_path / "notes" / "kitchen.md").resolve())
    made_with = {
        "workflow_run": "run-41",
        "project_id": "proj-9",
        "held_back": "Its working folder is in Preview, so it only reads there.",
    }
    owner = _Owner()
    before = _manager(owner)
    before.spawn("Hold the slot", parent_session_key=CHAT)
    step_agent = before.spawn("Write the brief", may_change=(note,), **made_with)
    too_wide = before.spawn("Tidy everything", may_change=("/",), workflow_run="run-41")
    await _settle()
    request = (_state(home, step_agent.id) or {})["request"]
    assert request["workflow_run"] == "run-41" and request["may_change"] == [note]
    await _restart(before)

    after = _manager(_Owner(), slots=2)
    taken = {t.agent_id: t for t in take_back(after, lambda _key: "")}
    assert taken[step_agent.id].now == ASKING
    restored = after.get(step_agent.id)
    assert {key: getattr(restored, key) for key in made_with} == made_with
    assert restored.may_change == (note,)
    assert taken[too_wide.id].now == ENDED
    assert "/ is a whole disk or home folder" in taken[too_wide.id].why
    assert _state(home, too_wide.id) is None
    await after.cancel_all()


@pytest.mark.asyncio
async def test_a_spawn_the_owner_cancelled_or_declined_stays_ended(home):
    """🔴 Red before on the queue's side: a restart took nothing back, and it must take back none
    of these. A queued spawn she cancelled, a start she cancelled while it asked, and one she
    declined each leave no record."""
    owner = _Owner()
    before = _manager(owner, slots=2)
    declined = before.spawn("Rename the files", parent_session_key=CHAT)
    stopped_asking = before.spawn("Clean the folder", parent_session_key=CHAT)
    cancelled = before.spawn("Draft the reply", parent_session_key=CHAT)
    kept = before.spawn("Plan the week", parent_session_key=CHAT)
    await _settle()
    assert cancelled.queued and (_state(home, cancelled.id) or {})["status"] == QUEUED
    assert await before.cancel(cancelled.id)
    owner.answer(declined.id, False)
    await _settle()
    assert before.get(declined.id).declined
    assert await before.cancel(stopped_asking.id)
    await _settle()
    for gone in (declined, stopped_asking, cancelled):
        assert _state(home, gone.id) is None, gone.task
    assert _state(home, kept.id) is not None, "the control: one still waiting is kept"
    await _restart(before)

    after = _manager(_Owner(), slots=4)
    assert [t.agent_id for t in take_back(after, lambda _key: "")] == [kept.id]
    await after.cancel_all()


@pytest.mark.asyncio
async def test_a_stopping_gateway_starts_nothing_from_its_queue(home):
    """🔴 Red before: the stop cancelled the running agent, whose ending drained the queue, so the
    next spawn started into the shutdown and was killed with it, and read as stopped, not kept."""
    owner = _Owner()
    manager = _manager(owner, working=True)
    asking = manager.spawn("Survey the venues", parent_session_key=CHAT)
    queued = manager.spawn("Draft the agenda", parent_session_key=CHAT)
    await _settle()
    owner.answer(asking.id, True)
    await _settle()
    assert not asking.done and queued.queued, "the first one is working, the next one waits"
    await manager.cancel_all()
    await _settle()

    assert queued.queued and queued.id not in manager._tasks
    assert (_state(home, queued.id) or {})["status"] == QUEUED
    # Nor does a spawn made while it stops: it waits for the next start.
    late = manager.spawn("Book the room", parent_session_key=CHAT)
    assert late is not None and late.queued and (_state(home, late.id) or {})["status"] == QUEUED


def test_the_restart_notice_names_each_one_it_kept_asked_again_or_ended(home):
    """🔴 Red before: the notice named only the agents the restart stopped."""
    notes = SimpleNamespace(sent=[])
    notes.notify = lambda kind, title, body, **_kw: notes.sent.append((kind, title, body))
    announce_orphans(
        notes,
        [Orphan("a1b2c3d4", "Research the venues")],
        [
            TakenBack("e5f6a7b8", "Draft the agenda", QUEUED, QUEUED),
            TakenBack("c9d0e1f2", "Book the room", ASKING, ASKING),
            TakenBack("a3b4c5d6", "Plan the week", QUEUED, STARTED),
            TakenBack(
                "f7e8d9c0",
                "Run on the helper",
                QUEUED,
                ENDED,
                why="unknown agent 'helper-x'; valid agents: researcher",
                told=True,
            ),
            TakenBack("b1a2c3d4", "Report", QUEUED, ENDED, why="its chat no longer exists"),
        ],
    )
    ((_kind, title, body),) = notes.sent
    assert title == (
        "A restart stopped a background agent, kept 3 that had not started and ended 2 that "
        "could not start"
    )
    assert body.split("\n\n") == [
        "a1b2c3d4 — Research the venues: it saved no result.",
        "e5f6a7b8 — Draft the agenda: it had not started, and waits for a free slot again.",
        "c9d0e1f2 — Book the room: it was waiting for your Allow to start, and asks again, since "
        "the restart closed the ask you had not answered.",
        "a3b4c5d6 — Plan the week: it had not started, and has started now.",
        "f7e8d9c0 — Run on the helper: it had not started, and was ended: unknown agent "
        "'helper-x'; valid agents: researcher. Its chat was told.",
        "b1a2c3d4 — Report: it had not started, and was ended: its chat no longer exists.",
    ]


def test_a_restart_that_only_took_back_says_so_in_its_title(home):
    notes = SimpleNamespace(sent=[])
    notes.notify = lambda kind, title, body, **_kw: notes.sent.append(title)
    announce_orphans(notes, [], [TakenBack("e5f6a7b8", "Draft the agenda", QUEUED, QUEUED)])
    announce_orphans(notes, [], [TakenBack("b1a2c3d4", "Report", QUEUED, ENDED, why="gone")])
    assert notes.sent == [
        "A restart kept a background agent that had not started",
        "A restart ended a background agent that could not start",
    ]


@pytest.mark.asyncio
async def test_the_gateway_takes_them_back_once_its_dashboard_is_up(home):
    """🔴 Red before: the start settled only the agents that had been running."""
    from personalclaw.gateway import GatewayOrchestrator

    owner = _Owner()
    before = _manager(owner)
    before.spawn("Hold the slot")
    waiting = before.spawn("Draft the weekly note")
    await _settle()
    await _restart(before)

    gateway = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gateway._held_boot_review = None
    gateway._background_tasks = set()
    gateway.subagent_mgr = _manager(_Owner(), slots=2)
    sent: list[tuple[str, str]] = []
    gateway.dashboard_state = SimpleNamespace(
        notify=lambda kind, title, body, **_kw: sent.append((title, body))
    )
    with patch("personalclaw.triggers.legacy_import.announce"):
        gateway._surface_held_boot_review()
        await asyncio.gather(*list(gateway._background_tasks))

    ((title, body),) = sent
    assert title == "A restart kept 2 background agents that had not started"
    assert f"{waiting.id} — Draft the weekly note: it had not started, and now asks" in body
    assert gateway.subagent_mgr.get(waiting.id) is not None
    await gateway.subagent_mgr.cancel_all()


@pytest.mark.asyncio
async def test_the_restart_confirm_counts_only_what_a_restart_cuts_off(home):
    """🔴 Red before: it counted a spawn queued for a slot, which a restart now keeps, as work the
    restart interrupts. One asking for its start still counts: its ask is closed."""
    owner = _Owner()
    manager = _manager(owner)
    manager.spawn("Survey the venues", parent_session_key=CHAT)
    manager.spawn("Draft the agenda", parent_session_key=CHAT)
    manager.spawn("Summarize the thread", parent_session_key=CHAT, trigger_id="trg-digest")
    await _settle()
    assert cut_off(manager.all_agents) == 2

    from chat_test_helpers import _make_state

    state = _make_state(Path(home.parent / "logs"), subagents=manager)
    assert state.active_work_snapshot()["running_agents"] == 2
    await manager.cancel_all()


def test_a_chat_is_told_only_while_it_is_kept_and_is_opened_for_the_report(tmp_path):
    """A dashboard chat kept on disk but not open after the restart is opened, so the report of the
    helper taken back reaches it; one no longer kept is said to be gone."""
    from chat_test_helpers import _make_state

    from personalclaw.dashboard.chat_persistence import (
        save_session_to_history,
        why_no_report_reaches,
    )

    before = _make_state(tmp_path)
    chat = before.get_or_create_session("chat-7-1791160000")
    chat.append("user", "Plan the offsite", "msg msg-u", broadcast=False)
    chat.append("assistant", "Started a helper on the venues.", "msg msg-a", broadcast=False)
    save_session_to_history(before, chat)

    after = _make_state(tmp_path)
    assert after.get_session("chat-7-1791160000") is None
    assert why_no_report_reaches(after, CHAT) == ""
    assert after.get_session("chat-7-1791160000") is not None, "opened for its report"
    assert why_no_report_reaches(after, OTHER_CHAT) == "its chat no longer exists"
    assert why_no_report_reaches(after, "teamchat:C0TEAM:1791160000.0001") == ""
