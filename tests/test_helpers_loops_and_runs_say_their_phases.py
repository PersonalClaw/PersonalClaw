"""A helper, a loop and a workflow run say what they are doing in the one ``work_phase`` frame.

A chat's turn said its phases and nothing else did, so a helper waiting for a slot, a loop stalled
on its owner and a run parked at a gate read as nothing at all until a page polled each kind's own
status. Each now says its phase from what the gateway already measures, through the same frame a
chat's turn uses (``dashboard/work_phases.py``): ``{work, kind, session, phase, line, since, seq}``,
sent when the phase or its line changes, never per chunk, with the last frame saying how it ended.

Everything runs through the real producers: the gateway's subagent event handler and the real
subagent manager's queue, the real approval registry, the real loop store and a loop worker's real
chat run state, and the workflow watchdog's real run publisher.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from table_rows import row
from test_work_phase_classifier import CHAT, _gateway_events, _scripted, _World

from personalclaw.approval_answer import YOU
from personalclaw.dashboard import chat_phase


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)


def _of(w: _World, work: str) -> list[dict]:
    return [d for k, d in w.frames if k == "work_phase" and d["work"] == work]


def _said(w: _World, work: str) -> list[tuple[str, str]]:
    return [(f["phase"], f["line"]) for f in _of(w, work)]


# ── 1. a helper ──────────────────────────────────────────────────────────────────────────────


def _helper(agent_id: str = "h1", parent: str = f"dashboard:{CHAT}", **over: Any):
    from personalclaw.subagent import SubagentInfo

    return SubagentInfo(id=agent_id, task="look it up", parent_session_key=parent, **over)


@pytest.mark.asyncio
async def test_a_helper_says_each_phase_from_waiting_for_a_slot_to_its_end(tmp_path):
    w = _World(tmp_path, _scripted())
    on_event, manager = _gateway_events(w.state)
    helper = _helper()

    await on_event("subagent_queued", helper, {"task": helper.task, "agent": ""})
    manager.running = [helper]
    await on_event("subagent_spawn", helper, {"task": helper.task, "agent": ""})
    await on_event("subagent_tool", helper, {"tool": "knowledge_search", "tool_kind": ""})
    await on_event("subagent_tool", helper, {"tool": "Read notes.md", "tool_kind": "read"})
    for word in ("one ", "two ", "three "):
        await on_event("subagent_chunk", helper, {"text": word})
    helper.done, helper.elapsed = True, 12.0
    manager.running = []
    await on_event("subagent_done", helper, {})

    assert _said(w, "subagent:h1") == [
        ("queued", "Waiting to start"),
        ("thinking", "Thinking"),
        ("searching", "Searching your knowledge"),
        ("tool", "Read notes.md"),
        ("streaming", "Writing"),
        ("done", "Done in 12 seconds"),
    ], "one frame per change: the three chunks say writing once"
    first = _of(w, "subagent:h1")[0]
    assert first["kind"] == "subagent"
    assert first["session"] == CHAT, "a helper names the chat it works for"
    assert {"work", "kind", "session", "phase", "line", "since", "seq"} <= set(first)
    seqs = [f["seq"] for f in _of(w, "subagent:h1")]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


async def _a_helpers_own_frames(tmp_path, say_helper: Any) -> list[tuple[str, Any]]:
    """The frames the gateway sends for one helper's run, with its phase said by *say_helper*
    (or as written, for ``None``), the phase frames left out."""
    tmp_path.mkdir()
    w = _World(tmp_path, _scripted())
    if say_helper is not None:
        w.state.say_helper = say_helper
    on_event, manager = _gateway_events(w.state)
    helper = _helper()
    await on_event("subagent_queued", helper, {"task": helper.task, "agent": ""})
    manager.running = [helper]
    await on_event("subagent_spawn", helper, {"task": helper.task, "agent": ""})
    await on_event("subagent_tool", helper, {"tool": "shell", "tool_kind": ""})
    helper.done, helper.elapsed = True, 3.0
    manager.running = []
    await on_event("subagent_done", helper, {})
    return [(k, _plain(d)) for k, d in w.frames if k != "work_phase"]


@pytest.mark.asyncio
async def test_a_failure_saying_a_helpers_phase_leaves_its_events_frames_alone(tmp_path):
    """A helper's phase is read from its events: one that fails to be said leaves every frame the
    gateway sends for them as it was."""
    tried: list[str] = []

    def failing(event: str, info: Any, extra: Any, session: str) -> None:
        tried.append(event)
        raise RuntimeError("the phase could not be said")

    said = await _a_helpers_own_frames(tmp_path / "said", None)
    assert [k for k, _d in said] == ["subagent_spawn", "subagent_tool", "subagent_done"]
    assert await _a_helpers_own_frames(tmp_path / "failing", failing) == said
    assert tried == ["subagent_queued", "subagent_spawn", "subagent_tool", "subagent_done"]


@pytest.mark.asyncio
async def test_a_helpers_ending_says_how_it_ended(tmp_path):
    w = _World(tmp_path, _scripted())
    on_event, _manager = _gateway_events(w.state)
    failed = _helper("f1", done=True, error="the model refused the request")
    stopped = _helper("s1", done=True, cancelled=True, error="Cancelled by user")
    declined = _helper("d1", done=True, never_ran=True, declined=True, error="You declined it.")
    for helper in (failed, stopped, declined):
        await on_event("subagent_done", helper, {})

    assert _said(w, "subagent:f1") == [("error", "Stopped: the model refused the request")]
    assert _said(w, "subagent:s1") == [("done", "Stopped")]
    assert _said(w, "subagent:d1") == [("done", "You declined it.")]


@pytest.mark.asyncio
async def test_a_task_of_a_chats_batch_names_that_chat(tmp_path):
    w = _World(tmp_path, _scripted())
    on_event, _manager = _gateway_events(w.state)
    task = _helper("t1", parent="workflow:run-1:n1")

    def task_of_chat(key: str) -> tuple[str, str, str]:
        return (CHAT, "run-1", "Compare") if key == task.parent_session_key else ("", "", "")

    with patch("personalclaw.workflows.batch_start.task_of_chat", task_of_chat):
        await on_event("subagent_spawn", task, {"task": task.task, "agent": ""})
    assert [f["session"] for f in _of(w, "subagent:t1")] == [CHAT]


#: Whose work a helper is, by the key its parent runs under, and the chat its frames name: a chat
#: started it, itself or with a batch it is a task of, or nothing names a chat.
_PARENTS: dict[str, tuple[str, str]] = {
    "a chat's own": (f"dashboard:{CHAT}", CHAT),
    "a task of a chat's batch": ("workflow:run-1:n1", CHAT),
    "a workflow step no chat's batch is": ("workflow:run-2:n1", ""),
    "an app's own run": ("app:probe-app", ""),
    "another helper": ("subagent:h0", ""),
    "a loop's worker": ("dashboard:loop-a1b2c3d4", ""),
    "a scheduled automation": ("cron:t1", ""),
    "nothing": ("", ""),
}


def _task_of_chat(key: str) -> tuple[str, str, str]:
    return (CHAT, "run-1", "Compare") if key == "workflow:run-1:n1" else ("", "", "")


@pytest.mark.asyncio
async def test_a_helper_names_a_chat_only_when_a_chat_started_it(subtests, tmp_path):
    """Through the gateway's real handler and the approval registry's hook: a helper's frames name
    the chat that started it, its own or the one whose batch it is a task of, from its first wait
    to its end, and its start's wait on her OK too. Started by anything else, they name no chat,
    so its phase is about that work alone (``ws_state.WORK_FRAMES``), whatever key its parent
    runs under."""
    from personalclaw.dashboard import work_phases
    from personalclaw.dashboard.ws_state import frame_subject

    w = _World(tmp_path, _scripted())
    on_event, manager = _gateway_events(w.state)
    with patch("personalclaw.workflows.batch_start.task_of_chat", _task_of_chat):
        for i, (case, (parent, chat)) in enumerate(_PARENTS.items()):
            with row(subtests, case=case):
                helper = _helper(f"x{i}", parent=parent)
                await on_event("subagent_queued", helper, {"task": helper.task, "agent": ""})
                manager.running = [helper]
                await on_event("subagent_spawn", helper, {"task": helper.task, "agent": ""})
                await on_event("subagent_tool", helper, {"tool": "shell", "tool_kind": ""})
                helper.done, helper.elapsed = True, 1.0
                manager.running = []
                await on_event("subagent_done", helper, {})
                frames = _of(w, f"subagent:x{i}")
                assert len(frames) == 4, "the positive control: every phase was said"
                assert {f["session"] for f in frames} == {chat}
                about = ("conversation", chat) if chat else ("work", f"subagent:x{i}")
                assert {frame_subject("work_phase", f) for f in frames} == {about}
        for i, (case, (parent, chat)) in enumerate(_PARENTS.items()):
            with row(subtests, case=f"a start asked for {case}"):
                # As the registry calls it (`_say_helper_waits`): the ask's entry, listed under its
                # parent's key without `dashboard:` (`_spawn_session_resolver`), then its id.
                entry = {"id": f"spawn:a{i}", "session": parent.removeprefix("dashboard:")}
                work_phases.approval_changed(w.state, {**entry, "tool": "subagent"}, waiting=True)
                work_phases.approval_changed(w.state, {"id": entry["id"]}, waiting=False)
                frames = _of(w, f"subagent:a{i}")
                assert len(frames) == 2, "the positive control: its wait and its end were said"
                assert {f["session"] for f in frames} == {chat}


async def _a_spawn_stopped_while_it_waits(w: _World, *, in_the_tick_it_queued: bool) -> str:
    """The real manager, its events fed to the gateway's real handler: a spawn that waits for the
    one slot is stopped, *in_the_tick_it_queued* or once its wait was said. Its id."""
    from test_subagent_fanout import _completing_sessions, _mock_ctx_builder

    from personalclaw.subagent import SubagentManager

    gateway_on_event, _manager = _gateway_events(w.state)
    mgr = SubagentManager(
        sessions=_completing_sessions(),
        ctx_builder=_mock_ctx_builder(),
        on_done=AsyncMock(),
        on_event=gateway_on_event,
        is_yolo=lambda: True,
        max_concurrent=1,
    )
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        mgr.spawn("first", parent_session_key=f"dashboard:{CHAT}")
        waiting = mgr.spawn("second", parent_session_key=f"dashboard:{CHAT}")
        assert waiting is not None and waiting.queued is True
        for _ in range(0 if in_the_tick_it_queued else 100):
            if _said(w, f"subagent:{waiting.id}"):
                break
            await asyncio.sleep(0.01)
        assert await mgr.cancel(waiting.id) is True
        for _ in range(20):  # every event the manager fired from code that could not await it
            await asyncio.sleep(0.01)
    return waiting.id


@pytest.mark.parametrize("in_the_tick_it_queued", [False, True])
@pytest.mark.asyncio
async def test_a_spawn_stopped_while_it_waits_for_a_slot_ends_stopped(
    tmp_path, in_the_tick_it_queued
):
    """Its last frame says it stopped, and nothing is kept of it, even when it is stopped before
    the manager's word that it waits has been heard."""
    from personalclaw.dashboard import work_phases

    w = _World(tmp_path, _scripted())
    agent = await _a_spawn_stopped_while_it_waits(w, in_the_tick_it_queued=in_the_tick_it_queued)
    said = _said(w, f"subagent:{agent}")
    assert said, "the positive control: the spawn's phase was said"
    assert said[-1] == ("done", "Stopped"), said
    assert work_phases.current(w.state, f"subagent:{agent}") is None


@pytest.mark.asyncio
async def test_a_helper_stopped_while_its_call_waits_on_her_ends_stopped(tmp_path):
    """The reaper says a stopped helper ended before its cancelled call takes back the approval it
    waited on: the helper's last frame says it stopped, not that it still waits on her OK, and the
    approval taken back after says nothing of it."""
    from personalclaw.dashboard import work_phases

    w = _World(tmp_path, _scripted())
    on_event, manager = _gateway_events(w.state)
    helper = _helper()
    manager.running = [helper]
    await on_event("subagent_spawn", helper, {"task": helper.task, "agent": ""})
    await on_event("subagent_tool", helper, {"tool": "shell", "tool_kind": ""})

    async def ask() -> Any:
        try:
            return await w.state.request_approval(
                "subagent:h1:r1", "subagent", "shell", session=CHAT
            )
        except Exception as exc:  # noqa: BLE001 — what the asker was told is not this test's
            return f"raised {type(exc).__name__}"

    asked = asyncio.ensure_future(ask())
    for _ in range(200):
        if ("approval", "Needs your OK: shell") in _said(w, "subagent:h1"):
            break
        await asyncio.sleep(0.01)
    assert _said(w, "subagent:h1")[-1] == ("approval", "Needs your OK: shell")
    helper.done, helper.cancelled, helper.error = True, True, "Cancelled by user"
    manager.running = []
    await on_event("subagent_done", helper, {})
    assert w.state.cancel_approval("subagent:h1:r1", reason="its helper was stopped") is True
    await asyncio.wait_for(asked, timeout=5)

    assert _said(w, "subagent:h1")[-2:] == [
        ("approval", "Needs your OK: shell"),
        ("done", "Stopped"),
    ]
    assert work_phases.current(w.state, "subagent:h1") is None


@pytest.mark.asyncio
async def test_a_helpers_frames_are_not_the_chats_own(tmp_path):
    """A helper's phase is its own: the chat's run state, and the frames that say it, are
    unchanged."""
    w = _World(tmp_path, _scripted())
    on_event, manager = _gateway_events(w.state)
    helper = _helper()
    manager.running = [helper]
    await on_event("subagent_spawn", helper, {"task": helper.task, "agent": ""})
    assert [d["kind"] for k, d in w.frames if k == "work_phase"] == ["subagent"]
    assert w.session.run_state == chat_phase.idle_payload(CHAT)


@pytest.mark.asyncio
async def test_a_helpers_approval_is_said_while_it_waits(tmp_path):
    """Its call asks her through the approval registry: the helper waits on her OK until she
    answers, then is back on the step it was on. A start that asks before it runs waits on her OK
    too."""
    w = _World(tmp_path, _scripted())
    on_event, manager = _gateway_events(w.state)
    helper = _helper()
    manager.running = [helper]
    await on_event("subagent_spawn", helper, {"task": helper.task, "agent": ""})
    await on_event("subagent_tool", helper, {"tool": "shell", "tool_kind": ""})

    asked = asyncio.ensure_future(
        w.state.request_approval("subagent:h1:r1", "subagent", "shell", session=CHAT)
    )
    for _ in range(200):
        if ("approval", "Needs your OK: shell") in _said(w, "subagent:h1"):
            break
        await asyncio.sleep(0.01)
    assert w.state.resolve_approval("subagent:h1:r1", True, by=YOU) is True
    await asyncio.wait_for(asked, timeout=5)

    assert _said(w, "subagent:h1")[-3:] == [
        ("tool", "shell"),
        ("approval", "Needs your OK: shell"),
        ("tool", "shell"),
    ]

    start = asyncio.ensure_future(
        w.state.request_approval("spawn:h2", "subagent", "subagent", session=CHAT)
    )
    for _ in range(200):
        if _said(w, "subagent:h2"):
            break
        await asyncio.sleep(0.01)
    assert w.state.resolve_approval("spawn:h2", False, by=YOU) is True
    await asyncio.wait_for(start, timeout=5)
    assert _said(w, "subagent:h2") == [
        ("approval", "Needs your OK: subagent"),
        ("queued", "Waiting to start"),
    ]
    assert _of(w, "subagent:h2")[0]["session"] == CHAT


#: The keys of a record that differ between two runs of the same thing: when, the stream stamp,
#: and the home's own id.
_WHEN = frozenset(
    {
        "ts",
        "since",
        "seq",
        "created_at",
        "updated_at",
        "resolved_at",
        "expired_at",
        "origin_harness",
    }
)

#: An Inbox row's id, stamped with when it was raised, and the clock time a note says it was asked.
_ROW_ID = re.compile(r"^([a-z_]+?)_[0-9a-f]{8}_[0-9.]+$")
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b")


def _plain(value: Any) -> Any:
    """*value* as it compares between two worlds: the state as a name, and no times."""
    from personalclaw.dashboard.state import DashboardState

    if isinstance(value, DashboardState):
        return "<state>"
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items() if k not in _WHEN}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, str):
        return _CLOCK.sub("<clock>", _ROW_ID.sub(r"\1", value))
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return type(value).__name__


def _hook_absent(state: Any, entry: Any, *, waiting: bool) -> None:
    """The registry as it was before a helper said its waits: no hook at all."""


async def _an_approval_ends(tmp_path, approval_id: str, ending: str, hook: Any) -> list:
    """Ask *approval_id* through the real registry, in a home of its own (*tmp_path*), and end it
    as *ending*, with the helper's phase hook replaced by *hook*: every effect the registry has, in
    order, and what each party was answered, with the helper's own frames left out, and the
    running list's and the waiting list's rings, which go out on their own clock once their window
    closes."""
    from personalclaw import auto_denials, inbox
    from personalclaw.dashboard import work_phases
    from personalclaw.triggers import lifecycle_fire

    tmp_path.mkdir()
    w = _World(tmp_path, _scripted())
    effects: list[Any] = []
    w.state.broadcast_ws = lambda kind, data=None: (
        effects.append(("frame", kind, _plain(data)))
        if kind not in ("work_phase", "running_changed", "waiting_changed")
        else None
    )
    audit = MagicMock()

    def spy(name: str, real: Any) -> Any:
        def seen(*a: Any, **kw: Any) -> Any:
            effects.append((name, _plain(a), _plain(kw)))
            return real(*a, **kw)

        return seen

    async def fired(*a: Any, **kw: Any) -> None:
        effects.append(("lifecycle", _plain(a), _plain(kw)))

    with (
        patch.dict(os.environ, {"PERSONALCLAW_HOME": str(tmp_path)}),
        patch("personalclaw.dashboard.approval_state.sel", return_value=audit),
        patch("personalclaw.sel.sel", return_value=audit),
        patch.object(lifecycle_fire, "fire", fired),
        patch.object(w.state, "_push_approval", spy("push", w.state._push_approval)),
        patch.object(w.state, "_raise_approval_row", spy("row", w.state._raise_approval_row)),
        patch.object(
            inbox, "resolve_attention_items", spy("handled", inbox.resolve_attention_items)
        ),
        patch.object(inbox, "expire_attention_items", spy("expired", inbox.expire_attention_items)),
        patch.object(auto_denials, "note_expired", spy("note", auto_denials.note_expired)),
        patch.object(auto_denials, "settle_retried", spy("settle", auto_denials.settle_retried)),
        patch.object(work_phases, "approval_changed", hook),
        patch.object(
            w.state, "approval_window_secs", return_value=0.2 if ending == "expired" else 30
        ),
    ):

        async def ask() -> Any:
            try:
                return await w.state.request_approval(
                    approval_id, "subagent", "shell", session=CHAT
                )
            except Exception as exc:  # noqa: BLE001 — what the asker was told is the result
                return f"raised {type(exc).__name__}"

        asked = asyncio.ensure_future(ask())
        for _ in range(200):
            if asked.done() or approval_id in w.state._pending_approvals:
                break
            await asyncio.sleep(0.01)
        try:
            if ending in ("approved", "rejected"):
                answered = w.state.resolve_approval(approval_id, ending == "approved", by=YOU)
            elif ending == "cancelled":
                answered = w.state.cancel_approval(approval_id, reason="its helper was stopped")
            else:
                answered = None
        except Exception as exc:  # noqa: BLE001 — what the one answering was told is the result
            answered = f"raised {type(exc).__name__}"
        effects.append(("asked", await asyncio.wait_for(asked, timeout=5), "answered", answered))
    effects.append(("audit", _plain([(c[0], c[1], c[2]) for c in audit.mock_calls])))
    effects.append(("pending", sorted(w.state._pending_approvals), w.state.ended_as(approval_id)))
    return effects


@pytest.mark.parametrize("ending", ["approved", "rejected", "expired", "cancelled"])
@pytest.mark.parametrize("approval_id", ["subagent:h1:r1", "spawn:h2", "workflow:r1:n1"])
@pytest.mark.asyncio
async def test_the_helpers_phase_only_reads_the_approval_it_waits_on(tmp_path, approval_id, ending):
    """The hook the registry calls for a helper's wait (``work_phases.approval_changed``) only
    reads: an approval asked, answered, expired or cancelled has every effect it had with no hook
    at all, in the same order, and answers both parties the same, and so it does when the hook
    fails."""
    from personalclaw.dashboard import work_phases

    failures: list[bool] = []

    def failing(state: Any, entry: Any, *, waiting: bool) -> None:
        failures.append(waiting)
        raise RuntimeError("the phase could not be said")

    before = await _an_approval_ends(tmp_path / "before", approval_id, ending, _hook_absent)
    assert any(e[0] == "push" for e in before), "the positive control: it was asked"
    said: list[tuple[str, bool]] = []

    def written(state: Any, entry: Any, *, waiting: bool) -> None:
        said.append((str(entry.get("id")), waiting))
        real(state, entry, waiting=waiting)

    real = work_phases.approval_changed
    assert await _an_approval_ends(tmp_path / "now", approval_id, ending, written) == before
    assert said == [(approval_id, True), (approval_id, False)], "the hook heard it raised and end"
    assert await _an_approval_ends(tmp_path / "failing", approval_id, ending, failing) == before
    assert failures == [True, False], "the planted failure was reached both ways"


@pytest.mark.parametrize("ending", ["approved", "rejected", "expired", "cancelled"])
@pytest.mark.parametrize("approval_id", ["subagent:h1:r1", "spawn:h2", "workflow:r1:n1"])
@pytest.mark.asyncio
async def test_the_running_lists_ring_only_reads_the_approval_it_rings_for(
    tmp_path, monkeypatch, approval_id, ending
):
    """The running list's ring for an approval (``running_now.asked``) only reads too: when it
    fails, an approval asked and then answered, expired or cancelled has every effect it has when
    the ring works, in the same order, and answers both parties the same."""
    from personalclaw import running_now

    before = await _an_approval_ends(tmp_path / "before", approval_id, ending, _hook_absent)
    assert any(e[0] == "push" for e in before), "the positive control: it was asked"
    rang: list[str] = []

    def failing(state: Any, entry: Any) -> None:
        rang.append(str(entry.get("id")))
        raise RuntimeError("the running list could not be rung")

    monkeypatch.setattr(running_now, "asked", failing)
    assert (
        await _an_approval_ends(tmp_path / "failing", approval_id, ending, _hook_absent) == before
    )
    assert rang and set(rang) == {approval_id}, "the planted failure was reached"


@pytest.mark.asyncio
async def test_the_manager_says_when_a_spawn_waits_for_a_slot_and_when_it_ends_waiting():
    """The real queue: a spawn that waits for a slot is said to, and one stopped while it waits
    ends with its own last event, as one that ran does."""
    from test_subagent_fanout import _completing_sessions, _mock_ctx_builder

    from personalclaw.subagent import SubagentManager

    events: list[tuple[str, str]] = []

    async def on_event(etype: str, info: Any, extra: dict) -> None:
        events.append((etype, info.id))

    mgr = SubagentManager(
        sessions=_completing_sessions(),
        ctx_builder=_mock_ctx_builder(),
        on_done=AsyncMock(),
        on_event=on_event,
        is_yolo=lambda: True,
        max_concurrent=1,
    )
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        mgr.spawn("first", parent_session_key=f"dashboard:{CHAT}")
        waiting = mgr.spawn("second", parent_session_key=f"dashboard:{CHAT}")
        assert waiting is not None and waiting.queued is True
        for _ in range(100):
            if ("subagent_queued", waiting.id) in events:
                break
            await asyncio.sleep(0.01)
        assert await mgr.cancel(waiting.id) is True
    assert ("subagent_queued", waiting.id) in events
    assert ("subagent_done", waiting.id) in events
    assert events.index(("subagent_queued", waiting.id)) < events.index(
        ("subagent_done", waiting.id)
    )


@pytest.mark.asyncio
async def test_the_manager_says_a_spawn_refused_while_it_waited_ended_waiting():
    """The real queue's drain: a spawn whose fan-out was stopped while it waited for a slot is
    refused when the slot frees, and ends with its own last event, as one stopped while it waits
    does."""
    from personalclaw.subagent import SubagentManager, _fanout_key

    events: list[tuple[str, str]] = []

    async def on_event(etype: str, info: Any, extra: dict) -> None:
        events.append((etype, info.id))

    mgr = SubagentManager(
        sessions=MagicMock(),
        ctx_builder=MagicMock(),
        on_done=AsyncMock(),
        on_event=on_event,
        is_yolo=lambda: True,
        max_concurrent=1,
    )
    with (
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.subagent.sel"),
        patch.object(mgr, "_dispatch_run", lambda info: None),
    ):
        mgr._running_count = 1  # the one slot is taken
        waiting = mgr.spawn("second", parent_session_key=f"dashboard:{CHAT}")
        assert waiting is not None and waiting.queued is True
        mgr._fanout_stops[_fanout_key(waiting)] = "the fan-out's breaker tripped"
        mgr._running_count = 0  # the slot frees, and the drain refuses what waited for it
        mgr._drain_queue()
        for _ in range(20):
            await asyncio.sleep(0.01)
    assert waiting.done and waiting.never_ran, "the positive control: it was refused"
    assert [e for e in events if e[1] == waiting.id] == [
        ("subagent_queued", waiting.id),
        ("subagent_done", waiting.id),
    ]


# ── 2. a loop ────────────────────────────────────────────────────────────────────────────────


def _loop(**over: Any):
    from personalclaw.loop import store
    from personalclaw.loop.loop import Loop

    base = dict(
        id="",
        name="G",
        kind="goal",
        task="investigate the latency regression",
        project_id="p-1",
        kind_config={"goal_type": "open_ended", "granularity": "balanced"},
    )
    base.update(over)
    return store.create(Loop(**base))


@pytest.mark.asyncio
async def test_a_loop_says_its_phase_as_its_status_moves(tmp_path):
    from personalclaw.loop import store
    from personalclaw.loop.loop import LoopStatus, LoopStopReason

    w = _World(tmp_path, _scripted())
    with patch(
        "personalclaw.inbox_providers.native_source.get_dashboard_state", return_value=w.state
    ):
        loop = _loop()
        ref = f"loop:{loop.id}"
        store.update_status(loop.id, LoopStatus.RUNNING)
        store.update_status(loop.id, LoopStatus.NEEDS_INPUT)
        store.update_status(loop.id, LoopStatus.NEEDS_INPUT)
        store.update_status(loop.id, LoopStatus.RUNNING)
        store.update_status(loop.id, LoopStatus.COMPLETE, stop_reason=LoopStopReason.DONE)

    said = _said(w, ref)
    assert said[1:] == [
        ("thinking", "Running"),
        ("input", "Needs your answer"),
        ("thinking", "Running"),
        ("done", "Done"),
    ], "a status written again with nothing new says nothing"
    assert said[0] == ("queued", "Waiting to start"), "a new loop is ready, not yet started"
    assert {f["session"] for f in _of(w, ref)} == {""}, "a loop has no conversation of its own"
    assert {f["kind"] for f in _of(w, ref)} == {"loop"}


@pytest.mark.asyncio
async def test_a_loop_deleted_before_it_ended_says_it_stopped(tmp_path):
    from personalclaw.dashboard import work_phases
    from personalclaw.loop import store
    from personalclaw.loop.loop import LoopStatus

    w = _World(tmp_path, _scripted())
    with patch(
        "personalclaw.inbox_providers.native_source.get_dashboard_state", return_value=w.state
    ):
        paused, ended = _loop(), _loop()
        store.update_status(paused.id, LoopStatus.PAUSED)
        store.update_status(ended.id, LoopStatus.STOPPED)
        store.delete(paused.id)
        store.delete(ended.id)

    assert _said(w, f"loop:{paused.id}")[-2:] == [("idle", "Paused"), ("done", "Stopped")]
    assert work_phases.current(w.state, f"loop:{paused.id}") is None
    assert _said(w, f"loop:{ended.id}")[-1:] == [("done", "Stopped")], "an ended loop is said once"
    assert len(_said(w, f"loop:{ended.id}")) == 2


@pytest.mark.asyncio
async def test_a_running_loop_says_what_its_worker_does(tmp_path):
    """Its worker's turn is a chat's (``loop-<id>``), which says its own phases; while the loop
    runs, the loop says them too, and is back to running once the worker's turn ends."""
    from personalclaw.loop import manager, store
    from personalclaw.loop.loop import LoopStatus

    w = _World(tmp_path, _scripted())
    with patch(
        "personalclaw.inbox_providers.native_source.get_dashboard_state", return_value=w.state
    ):
        loop = _loop()
        store.update_status(loop.id, LoopStatus.RUNNING)
        worker = w.state.get_or_create_session(manager.session_key(loop.id))
        chat_phase.begin(w.state, worker)
        chat_phase.step(w.state, worker, "grep", "")
        chat_phase.end(w.state, worker)

    assert _said(w, f"loop:{loop.id}")[1:] == [
        ("thinking", "Running"),
        ("queued", "Waiting to start"),
        ("searching", "Searching the files"),
        ("thinking", "Running"),
    ]


async def _a_loop_workers_own_phases(tmp_path, worker_said: Any) -> list[tuple[str, str]]:
    """The phases a running loop's worker's turn says of its own chat, with the loop's said from
    them by *worker_said*."""
    from personalclaw.dashboard import work_phases
    from personalclaw.loop import manager, store
    from personalclaw.loop.loop import LoopStatus

    tmp_path.mkdir()
    w = _World(tmp_path, _scripted())
    with (
        patch(
            "personalclaw.inbox_providers.native_source.get_dashboard_state", return_value=w.state
        ),
        patch.object(work_phases, "worker_said", worker_said),
    ):
        loop = _loop()
        store.update_status(loop.id, LoopStatus.RUNNING)
        worker = w.state.get_or_create_session(manager.session_key(loop.id))
        chat_phase.begin(w.state, worker)
        chat_phase.step(w.state, worker, "grep", "")
        chat_phase.end(w.state, worker)
    return [(d["phase"], d["line"]) for d in w.phases()]


@pytest.mark.asyncio
async def test_a_failure_saying_a_loops_phase_leaves_its_workers_turn_alone(tmp_path):
    """The loop's phase is read from its worker's chat frame: one that fails to be said leaves the
    chat's own phases, and so every approval it waits on, as they were."""
    from personalclaw.dashboard import work_phases

    tried: list[str] = []

    def failing(state: Any, key: str, payload: Any) -> None:
        tried.append(key)
        raise RuntimeError("the phase could not be said")

    said = await _a_loop_workers_own_phases(tmp_path / "said", work_phases.worker_said)
    assert len(said) >= 3, "the positive control: the worker's turn said its phases"
    assert await _a_loop_workers_own_phases(tmp_path / "failing", failing) == said
    assert len(tried) == len(said), "the planted failure was reached on every frame"


@pytest.mark.parametrize(
    ("status", "says"),
    [("paused", ("idle", "Paused")), ("needs_input", ("input", "Needs your answer"))],
)
@pytest.mark.asyncio
async def test_a_loops_worker_speaks_for_it_only_while_it_runs(tmp_path, status, says):
    """A running turn's frame is said again while it runs, and its end after: once the loop has
    stopped running, what its worker's turn still says is not the loop's."""
    from personalclaw.loop import manager, store
    from personalclaw.loop.loop import LoopStatus

    w = _World(tmp_path, _scripted())
    with patch(
        "personalclaw.inbox_providers.native_source.get_dashboard_state", return_value=w.state
    ):
        loop = _loop()
        store.update_status(loop.id, LoopStatus.RUNNING)
        worker = w.state.get_or_create_session(manager.session_key(loop.id))
        chat_phase.begin(w.state, worker)
        chat_phase.step(w.state, worker, "grep", "")
        store.update_status(loop.id, LoopStatus(status))
        chat_phase.step(w.state, worker, "edit", "")
        chat_phase.end(w.state, worker)

    assert _said(w, f"loop:{loop.id}")[1:] == [
        ("thinking", "Running"),
        ("queued", "Waiting to start"),
        ("searching", "Searching the files"),
        says,
    ]


async def _a_loop_written_through_the_store(tmp_path, state: Any) -> list[Any]:
    """A loop created, moved through its statuses and deleted through the real store, with its
    phase said to *state* (``None``: headless): what each call answered, and what it left."""
    from personalclaw.loop import store
    from personalclaw.loop.loop import LoopStatus

    tmp_path.mkdir()
    with (
        patch("personalclaw.loop.files.config_dir", lambda: tmp_path),
        patch("personalclaw.inbox_providers.native_source.get_dashboard_state", return_value=state),
    ):
        loop = _loop(id="a1b2c3d4")
        seen: list[Any] = [(loop.id, loop.status)]
        for status in (LoopStatus.RUNNING, LoopStatus.PAUSED):
            out = store.update_status(loop.id, status)
            seen.append((out.id, out.status, store.get(loop.id).status))
        seen.append((store.delete(loop.id), store.get(loop.id)))
    return seen


@pytest.mark.asyncio
async def test_a_failure_saying_a_loops_phase_leaves_the_store_alone(tmp_path):
    """The loop store says each loop's phase as it writes its status: one that fails to be said,
    or to be said deleted, leaves every write, and what each call answers, as it is headless."""
    tried: list[str] = []

    def say_loop(loop: Any) -> None:
        tried.append(str(getattr(loop.status, "value", loop.status)))
        raise RuntimeError("the phase could not be said")

    def say_loop_deleted(loop_id: str) -> None:
        tried.append("deleted")
        raise RuntimeError("the phase could not be said")

    state = MagicMock(say_loop=say_loop, say_loop_deleted=say_loop_deleted)
    headless = await _a_loop_written_through_the_store(tmp_path / "headless", None)
    assert await _a_loop_written_through_the_store(tmp_path / "failing", state) == headless
    assert tried == ["ready", "running", "paused", "deleted"], "the planted failure was reached"


@pytest.mark.asyncio
async def test_a_running_loop_says_it_is_held_while_incident_mode_holds_it(tmp_path, monkeypatch):
    """Incident mode holds a running loop without writing its status, and its views say it is
    held. Its phase says so when the switch moves, through the gateway's own watch of it, while
    its worker's turn ends too, and that it runs again once the switch is off."""
    from test_gateway import _make_orchestrator

    from personalclaw.guardrails import incident
    from personalclaw.loop import manager, store
    from personalclaw.loop.loop import LoopStatus

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    incident.reset_incident_mirror()
    w = _World(tmp_path, _scripted())
    orch = _make_orchestrator()
    orch.dashboard_state = w.state

    async def until(said: tuple[str, str]) -> None:
        for _ in range(300):
            if _said(w, ref)[-1:] == [said]:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"{said} was never said: {_said(w, ref)}")

    try:
        with (
            patch(
                "personalclaw.inbox_providers.native_source.get_dashboard_state",
                return_value=w.state,
            ),
            patch.object(incident, "WATCH_INTERVAL_SECS", 0.01),
        ):
            loop = _loop()
            ref = f"loop:{loop.id}"
            store.update_status(loop.id, LoopStatus.RUNNING)
            worker = w.state.get_or_create_session(manager.session_key(loop.id))
            chat_phase.begin(w.state, worker)
            chat_phase.step(w.state, worker, "grep", "")
            orch._start_incident_watch()
            try:
                await asyncio.sleep(0.05)
                incident.activate("a drill")
                await until(("queued", "Held until incident mode is off"))
                chat_phase.end(w.state, worker)
                incident.resume()
                await until(("thinking", "Running"))
            finally:
                orch._incident_watch_task.cancel()
    finally:
        incident.reset_incident_mirror()

    assert _said(w, ref)[1:] == [
        ("thinking", "Running"),
        ("queued", "Waiting to start"),
        ("searching", "Searching the files"),
        ("queued", "Held until incident mode is off"),
        ("thinking", "Running"),
    ], "held while the switch is on, the worker's turn ending under it too"


# ── 3. a workflow run ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_workflow_run_says_its_status_its_step_and_its_gate(tmp_path):
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    w = _World(tmp_path, _scripted())
    w.state.workflow_sse = MagicMock()
    publish = WorkflowWatchdog(state=w.state)._publisher("r1")
    publish("workflow_run_update", {"status": "running"})
    publish("workflow_node_started", {"node_id": "fetch", "label": "Fetch the report"})
    publish("workflow_node_started", {"node_id": "fetch", "label": "Fetch the report"})
    publish(
        "workflow_attention",
        {"node_id": "ok", "kind": "approval", "ask": {"kind": "approval", "prompt": "Send it?"}},
    )
    publish("workflow_run_update", {"status": "needs_input"})
    publish("workflow_gate_resolved", {"node_id": "ok", "approved": True})
    publish("workflow_run_update", {"status": "running"})
    publish("workflow_run_update", {"status": "failed", "error": "the mail server said no"})

    assert _said(w, "workflow:r1") == [
        ("thinking", "Running"),
        ("tool", "Fetch the report"),
        ("approval", "Needs your OK: Send it?"),
        ("thinking", "Running"),
        ("error", "Stopped: the mail server said no"),
    ], "the gate's own ask outlasts the run's generic needs-input status"
    assert {f["session"] for f in _of(w, "workflow:r1")} == {""}
    assert {f["kind"] for f in _of(w, "workflow:r1")} == {"workflow"}


#: A gate's ask and the run's other events, each sequence published in order, and what the run
#: says: a step started while the gate waits leaves its ask said, an escalation asks nothing of
#: her, and a gate the run left (paused, then running) no longer stands over the next step.
_GATE_ROWS: dict[str, tuple[list[tuple[str, dict]], list[tuple[str, str]]]] = {
    "a step starts while a gate waits": (
        [
            (
                "workflow_attention",
                {"kind": "approval", "ask": {"kind": "approval", "prompt": "Go?"}},
            ),
            ("workflow_run_update", {"status": "needs_input"}),
            ("workflow_node_started", {"node_id": "b", "label": "Other branch"}),
        ],
        [("thinking", "Running"), ("approval", "Needs your OK: Go?")],
    ),
    "an escalation is not a gate": (
        [
            (
                "workflow_attention",
                {"kind": "escalation", "ask": {"kind": "approval", "prompt": "!"}},
            )
        ],
        [("thinking", "Running")],
    ),
    "a gate the run left stands over no step": (
        [
            (
                "workflow_attention",
                {"kind": "approval", "ask": {"kind": "approval", "prompt": "Go?"}},
            ),
            ("workflow_run_update", {"status": "paused"}),
            ("workflow_run_update", {"status": "running"}),
            ("workflow_node_started", {"node_id": "c", "label": "Next step"}),
        ],
        [
            ("thinking", "Running"),
            ("approval", "Needs your OK: Go?"),
            ("idle", "Paused"),
            ("thinking", "Running"),
            ("tool", "Next step"),
        ],
    ),
}


@pytest.mark.asyncio
async def test_a_runs_gate_stands_over_its_steps_until_it_is_answered_or_left(subtests, tmp_path):
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    w = _World(tmp_path, _scripted())
    w.state.workflow_sse = MagicMock()
    watchdog = WorkflowWatchdog(state=w.state)
    for i, (case, (events, says)) in enumerate(_GATE_ROWS.items()):
        with row(subtests, case=case):
            publish = watchdog._publisher(f"g{i}")
            publish("workflow_run_update", {"status": "running"})
            for event, payload in events:
                publish(event, payload)
            assert _said(w, f"workflow:g{i}") == says


@pytest.mark.asyncio
async def test_a_held_run_says_it_is_held_and_carries_on(tmp_path):
    """Incident mode holds a running run without changing its status: the run's own publisher says
    it is held, and that it carries on (``workflows.incident_hold``), and so its phase does."""
    from types import SimpleNamespace

    from personalclaw.workflows import incident_hold
    from personalclaw.workflows.models import RunStatus
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    w = _World(tmp_path, _scripted())
    w.state.workflow_sse = MagicMock()
    publish = WorkflowWatchdog(state=w.state)._publisher("r4")
    run = SimpleNamespace(
        run=SimpleNamespace(id="r4", status=RunStatus.RUNNING),
        _incident_held=False,
        _incident_wake=0.0,
        _withdraw_inflight=AsyncMock(),
        _publish=publish,
    )
    publish("workflow_run_update", {"status": "running"})
    await incident_hold.hold(run, wake_secs=1.0)  # type: ignore[arg-type]
    publish("workflow_run_update", {"status": "running"})  # an update that names no hold
    assert _said(w, "workflow:r4")[-1] == ("queued", "Held until incident mode is off")
    incident_hold.carry_on(run)  # type: ignore[arg-type]
    assert _said(w, "workflow:r4") == [
        ("thinking", "Running"),
        ("queued", "Held until incident mode is off"),
        ("thinking", "Running"),
    ]


@pytest.mark.asyncio
async def test_a_failure_saying_a_runs_phase_leaves_its_events_alone(tmp_path):
    """A run's phase is read from what its publisher publishes: one that fails to be said leaves
    every event the run publishes as it was."""
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    def published(say_run: Any) -> list[tuple[str, str, Any]]:
        w = _World(tmp_path, _scripted())
        if say_run is not None:
            w.state.say_run = say_run
        watchdog = WorkflowWatchdog(state=w.state)
        sent: list[tuple[str, str, Any]] = []
        with (
            patch.object(watchdog._coalescer, "publish", lambda *a: sent.append(a)),
            patch.object(watchdog._coalescer, "flush"),
        ):
            publish = watchdog._publisher("r5")
            publish("workflow_run_update", {"status": "running"})
            publish("workflow_node_started", {"node_id": "a", "label": "Fetch"})
            publish("workflow_run_update", {"status": "complete"})
        return sent

    tried: list[str] = []

    def failing(run_id: str, event: str, payload: Any) -> None:
        tried.append(event)
        raise RuntimeError("the phase could not be said")

    said = published(None)
    assert len(said) == 3, "the positive control: every event was published"
    assert published(failing) == said
    assert tried == [e for _k, e, _p in said], "the planted failure was reached on every event"


@pytest.mark.asyncio
async def test_ended_work_is_forgotten_once_its_last_frame_is_sent(tmp_path):
    from personalclaw.dashboard import work_phases
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    w = _World(tmp_path, _scripted())
    w.state.workflow_sse = MagicMock()
    publish = WorkflowWatchdog(state=w.state)._publisher("r2")
    publish("workflow_run_update", {"status": "running"})
    assert work_phases.current(w.state, "workflow:r2") is not None
    publish("workflow_run_update", {"status": "complete"})
    assert work_phases.current(w.state, "workflow:r2") is None
    assert _said(w, "workflow:r2")[-1] == ("done", "Done")
