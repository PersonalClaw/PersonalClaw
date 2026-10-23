"""Everything running now is one list, each piece of work with what it is doing (#3889).

Each kind of live work kept its own list, and only helpers said what they were doing: a reader that
wanted to know what was running read six routes and guessed the rest, a queued helper read as
running, a loop's workers read as chats of their own, and a webhook's turn was nowhere at all.
``GET /api/running`` lists every piece of live work once, each row carrying its kind, its name, the
agent doing it, its exact phase and line, when that last changed, when it started, the route that
opens it, the chat it is for and the ids of what waits on the owner for it; a short tail of what
just finished; and ``GET /api/running/count`` answers how many, how many are blocked and how many
wait on her, reading no loop's findings.

Who reads what is the frame's rule: an app reads only the rows its socket would hear the phase of
(``ws_state.WORK_FRAMES``), an agent only its own chat's, and the owner everything.

Everything runs through the real producers: a chat's real turn, the gateway's subagent event
handler, the real loop and workflow stores, a run's real publisher, the trigger claim store, and
the webhook door's real turn.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app
from test_apps_cannot_post_into_your_chats import _gateway
from test_apps_cannot_run_code_or_bypass_approvals import _home, _install
from test_helpers_loops_and_runs_say_their_phases import _helper, _loop
from test_work_phase_classifier import CHAT, _answer, _call, _gateway_events, _World

from personalclaw.dashboard import chat_phase

#: The app that reads the list, the chat it started, and an app that does not declare the route.
APP = "probe-running"
UNDECLARED_APP = "probe-silent"
APPS_CHAT = "chat-app-1"

#: Every field a row carries, whatever its kind.
ROW = {
    "work",
    "kind",
    "name",
    "agent",
    "phase",
    "line",
    "since",
    "started_at",
    "route",
    "session",
    "blocked",
    "waiting",
}


def _app(state: Any) -> web.Application:
    from personalclaw.dashboard.handlers.running import register_running_routes

    app = _api_app(state)
    register_running_routes(app)
    return app


def _routes() -> list[tuple[str, str, Any]]:
    from personalclaw.dashboard.handlers import running

    return [
        ("GET", "/api/running", running.api_running),
        ("GET", "/api/running/count", running.api_running_count),
    ]


class _Helpers:
    """The subagent manager as the list reads it: every helper it holds."""

    def __init__(self, *helpers: Any) -> None:
        self.all_agents = list(helpers)


def _held(gate: asyncio.Event, *before: Any):
    async def stream() -> AsyncIterator[Any]:
        for event in before:
            yield event
        await gate.wait()
        for event in _answer():
            yield event

    return stream


@asynccontextmanager
async def _everything(tmp_path):
    """One of each kind of live work, each said by its real producer: a chat on a tool, a helper
    on a search and one queued for a slot, a running loop with a stage worker and a task worker, a
    workflow run on a step, a loop backed by a run, a trigger's fire whose work has no row, a
    trigger's fire whose helper speaks for it, and the app's own chat with a helper of its own."""
    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import LoopStatus
    from personalclaw.triggers import claims
    from personalclaw.triggers.scheduling import Claim
    from personalclaw.workflows import store as runs
    from personalclaw.workflows.models import RunStatus, WorkflowRun
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    gate = asyncio.Event()
    w = _World(tmp_path, _held(gate, _call("shell", "t1", tool_input={"command": "ls"})))
    w.session.agent = "builder"
    task = w.start()
    await w.until("tool")

    on_event, manager = _gateway_events(w.state)
    looking = _helper("h1", agent="researcher")
    waiting = _helper("h2", queued=True)
    fired = _helper("h3", parent="", trigger_id="t-fired")
    apps_chat = w.state.get_or_create_session(APPS_CHAT, created_by_app=APP)
    apps_helper = _helper("h4", parent=f"dashboard:{APPS_CHAT}")
    await on_event("subagent_queued", waiting, {"task": waiting.task, "agent": ""})
    manager.running = [looking, fired, apps_helper]
    for helper in (looking, fired, apps_helper):
        await on_event("subagent_spawn", helper, {"task": helper.task, "agent": helper.agent})
    await on_event("subagent_tool", looking, {"tool": "knowledge_search", "tool_kind": ""})
    w.state.subagents = _Helpers(looking, waiting, fired, apps_helper)
    chat_phase.begin(w.state, apps_chat)

    with patch(
        "personalclaw.inbox_providers.native_source.get_dashboard_state", return_value=w.state
    ):
        loop = _loop(agent="planner")
        loops.update_status(loop.id, LoopStatus.RUNNING)
    workers = [
        w.state.get_or_create_session(f"loop-{loop.id}"),
        w.state.get_or_create_session(f"loop-{loop.id}-t-2b9c41fe"),
    ]
    for worker in workers:
        chat_phase.begin(w.state, worker)

    w.state.workflow_sse = MagicMock()
    run = runs.create(
        WorkflowRun(
            id="",
            workflow_name="nightly-report",
            title="Nightly report",
            status=RunStatus.RUNNING,
            started_at="2026-10-21T09:00:00Z",
        )
    )
    publish = WorkflowWatchdog(state=w.state)._publisher(run.id)
    publish("workflow_run_update", {"status": "running"})
    publish("workflow_node_started", {"node_id": "fetch", "label": "Fetch the report"})
    backed = runs.create(
        WorkflowRun(
            id="",
            workflow_name="general-loop",
            title="Tidy the docs",
            status=RunStatus.RUNNING,
            loop_kind="general",
            started_at="2026-10-21T09:05:00Z",
        )
    )
    for trigger_id in ("t-alone", "t-fired"):
        claims.write_claim(Claim(trigger_id=trigger_id, holder="cron:1", claimed_at=time.time()))

    try:
        yield w, {
            "chat": f"chat:{CHAT}",
            "helper": "subagent:h1",
            "queued": "subagent:h2",
            "fired": "subagent:h3",
            "apps_chat": f"chat:{APPS_CHAT}",
            "apps_helper": "subagent:h4",
            "loop": f"loop:{loop.id}",
            "loop_id": loop.id,
            "workers": [f"chat:{s.key}" for s in workers],
            "run": f"workflow:{run.id}",
            "run_id": run.id,
            "backed": f"workflow:{backed.id}",
            "backed_id": backed.id,
            "trigger": "trigger:t-alone",
        }
    finally:
        for session in (apps_chat, *workers):
            chat_phase.end(w.state, session)
        gate.set()
        await asyncio.wait_for(task, timeout=10)


async def _read(state: Any, path: str = "/api/running") -> Any:
    async with TestClient(TestServer(_app(state))) as http:
        resp = await http.get(path)
        assert resp.status == 200, await resp.text()
        return await resp.json()


def _by_work(listed: dict) -> dict[str, dict]:
    rows = listed["running"]
    works = [r["work"] for r in rows]
    assert len(works) == len(set(works)), f"a piece of work is listed twice: {works}"
    return {r["work"]: r for r in rows}


# ── 1. every kind, once, with what it is doing ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_each_kind_of_live_work_is_one_row_with_its_phase_its_agent_and_its_route(tmp_path):
    async with _everything(tmp_path) as (w, ids):
        rows = _by_work(await _read(w.state))
        served = w.session.run_state

    for found in rows.values():
        assert set(found) >= ROW, sorted(ROW - set(found))

    chat = rows[ids["chat"]]
    assert (chat["kind"], chat["phase"], chat["line"]) == (
        "chat",
        "delegating",
        served["line"],
    ), "its turn hands off to the helpers working for it"
    assert (chat["agent"], chat["route"], chat["session"]) == ("builder", f"#/chat/{CHAT}", CHAT)

    helper = rows[ids["helper"]]
    assert (helper["kind"], helper["phase"], helper["line"]) == (
        "subagent",
        "searching",
        "Searching your knowledge",
    )
    assert (helper["agent"], helper["session"], helper["route"]) == (
        "researcher",
        CHAT,
        f"#/chat/{CHAT}",
    ), "a helper names the chat it works for, and opens there"
    assert helper["blocked"] is False

    queued = rows[ids["queued"]]
    assert (queued["phase"], queued["line"], queued["blocked"]) == (
        "queued",
        "Waiting to start",
        True,
    ), "a helper waiting for a slot is blocked, never working"

    loop = rows[ids["loop"]]
    assert (loop["kind"], loop["phase"], loop["agent"]) == ("loop", "queued", "planner")
    assert loop["route"] == f"#/loops/{ids['loop_id']}"
    assert loop["session"] == "", "a loop has no chat of its own"
    for worker in ids["workers"]:
        assert worker not in rows, "a loop's workers are inside their loop, never rows of their own"

    run = rows[ids["run"]]
    assert (run["kind"], run["name"], run["phase"], run["line"]) == (
        "workflow",
        "Nightly report",
        "tool",
        "Fetch the report",
    )
    assert run["route"] == f"#/workflows/runs/{ids['run_id']}"
    assert run["started_at"].startswith("2026-10-21T09:00:00")

    backed = rows[ids["backed"]]
    assert (backed["kind"], backed["name"]) == ("loop", "Tidy the docs")
    assert backed["route"] == f"#/workflows/runs/{ids['backed_id']}"
    assert sum(r["route"] == backed["route"] for r in rows.values()) == 1, "listed once, as a loop"

    trigger = rows[ids["trigger"]]
    assert (trigger["kind"], trigger["session"], trigger["route"]) == (
        "trigger",
        "",
        "#/triggers?open=t-alone",
    )
    assert "trigger:t-fired" not in rows, "a fire whose helper runs is that helper's row"
    assert rows[ids["fired"]]["route"] == "#/triggers?open=t-fired"

    assert set(rows) == {
        ids["chat"],
        ids["helper"],
        ids["queued"],
        ids["fired"],
        ids["apps_chat"],
        ids["apps_helper"],
        ids["loop"],
        ids["run"],
        ids["backed"],
        ids["trigger"],
    }, "nothing else is listed"


@pytest.mark.asyncio
async def test_the_rows_keep_their_order_as_their_phases_move(tmp_path):
    """Rows are ordered by when their work started, so a phase changing never moves a row."""
    async with _everything(tmp_path) as (w, ids):
        before = [r["work"] for r in (await _read(w.state))["running"]]
        on_event, _manager = _gateway_events(w.state)
        helper = w.state.subagents.all_agents[0]
        await on_event("subagent_tool", helper, {"tool": "Read notes.md", "tool_kind": "read"})
        after = await _read(w.state)
    assert [r["work"] for r in after["running"]] == before
    assert _by_work(after)[ids["helper"]]["line"] == "Read notes.md"


@pytest.mark.asyncio
async def test_a_webhooks_turn_is_listed_while_it_runs_and_then_just_finished(tmp_path):
    from personalclaw.dashboard.handlers import hooks

    w = _World(tmp_path, _held(asyncio.Event()))
    w.state.sessions.release = MagicMock()
    w.state.sessions.reset = AsyncMock()
    going, gate = asyncio.Event(), asyncio.Event()

    async def inner(*_a: Any, **_kw: Any) -> str:
        going.set()
        await gate.wait()
        return "deployed"

    # The door takes a slot before it starts the turn, which gives it back.
    await hooks._hook_semaphore.acquire()
    with patch.object(hooks, "_run_hook_inner", inner), patch.object(hooks, "_sel", MagicMock()):
        turn = asyncio.ensure_future(
            hooks._run_hook_agent(
                w.state, "hook:deploy", "ship it", "Deploy hook", "ops", False, 30
            )
        )
        await asyncio.wait_for(going.wait(), timeout=5)
        listed = await _read(w.state)
        gate.set()
        await asyncio.wait_for(turn, timeout=5)
    (row,) = listed["running"]
    assert (row["work"], row["kind"], row["name"], row["agent"]) == (
        "webhook:deploy",
        "webhook",
        "Deploy hook",
        "ops",
    )
    assert row["phase"] == "thinking" and row["session"] == ""

    after = await _read(w.state)
    assert after["running"] == []
    (done,) = after["finished"]
    assert (done["work"], done["phase"]) == ("webhook:deploy", "done")


# ── 2. what waits on the owner, and what is blocked ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_row_names_the_approvals_that_wait_on_the_owner_for_it(tmp_path):
    async with _everything(tmp_path) as (w, ids):
        w.state._pending_approvals["subagent:h1:c1"] = {
            "id": "subagent:h1:c1",
            "session": CHAT,
            "tool": "shell",
            "ts": time.time(),
        }
        w.state._pending_approvals["a-loop"] = {
            "id": "a-loop",
            "session": f"loop-{ids['loop_id']}-t-2b9c41fe",
            "tool": "shell",
            "ts": time.time(),
        }
        w.state._pending_approvals["a-run"] = {
            "id": "a-run",
            "session": f"workflow:{ids['run_id']}:fetch",
            "tool": "shell",
            "ts": time.time(),
        }
        rows = _by_work(await _read(w.state))
        count = await _read(w.state, "/api/running/count")
    assert rows[ids["chat"]]["waiting"] == ["subagent:h1:c1"], "its helper's ask waits for it too"
    assert rows[ids["helper"]]["waiting"] == ["subagent:h1:c1"]
    assert rows[ids["loop"]]["waiting"] == ["a-loop"], "a task worker's ask is its loop's"
    assert rows[ids["run"]]["waiting"] == ["a-run"]
    assert rows[ids["queued"]]["waiting"] == []
    # Blocked: the queued helper, and the app's chat and the loop, whose turns are accepted and not
    # started yet. Waiting: the four rows an approval names.
    assert count == {"live": 10, "blocked": 3, "waiting": 4, "finished": 0}, count


@pytest.mark.asyncio
async def test_a_loop_incident_mode_holds_is_blocked_and_says_held(tmp_path):
    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import LoopStatus

    w = _World(tmp_path, _held(asyncio.Event()))
    with (
        patch(
            "personalclaw.inbox_providers.native_source.get_dashboard_state", return_value=w.state
        ),
        patch("personalclaw.loop.loop.held_reason", return_value="Held: incident mode is on"),
    ):
        loop = _loop()
        loops.update_status(loop.id, LoopStatus.RUNNING)
        (row,) = (await _read(w.state))["running"]
    assert (row["phase"], row["line"], row["blocked"]) == (
        "queued",
        "Held until incident mode is off",
        True,
    )


# ── 3. what just finished ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_work_that_ends_leaves_the_list_for_a_bounded_just_finished_tail(tmp_path):
    from personalclaw import running_now

    w = _World(tmp_path, _held(asyncio.Event()))
    on_event, manager = _gateway_events(w.state)
    clock = [1_000_000.0]
    helpers = [_helper(f"g{i}") for i in range(running_now.TAIL_ROWS + 3)]
    w.state.subagents = _Helpers(*helpers)
    with patch.object(running_now, "_now", lambda: clock[0]):
        for helper in helpers:
            manager.running = [helper]
            await on_event("subagent_spawn", helper, {"task": helper.task, "agent": ""})
            helper.done, helper.elapsed = True, 2.0
            manager.running = []
            await on_event("subagent_done", helper, {})
            clock[0] += 1
        listed = await _read(w.state)
        assert listed["running"] == []
        finished = [r["work"] for r in listed["finished"]]
        assert finished == [
            f"subagent:g{i}" for i in reversed(range(3, len(helpers)))
        ], "the newest first, and no more than the tail holds"
        assert listed["finished"][0]["phase"] == "done"
        assert listed["finished"][0]["line"] == "Done in 2 seconds"
        clock[0] += running_now.TAIL_SECS + 1
        assert (await _read(w.state))["finished"] == [], "a finish is kept for its while, no longer"


# ── 4. the count is cheap ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_count_and_the_list_read_no_loops_findings_at_fifty_loops(tmp_path):
    from personalclaw.loop import files
    from personalclaw.loop import store as loops
    from personalclaw.loop.loop import LoopStatus

    w = _World(tmp_path, _held(asyncio.Event()))
    with patch(
        "personalclaw.inbox_providers.native_source.get_dashboard_state", return_value=w.state
    ):
        for _ in range(50):
            loops.update_status(_loop().id, LoopStatus.RUNNING)
    with patch.object(files, "get_findings", wraps=files.get_findings) as spy:
        count = await _read(w.state, "/api/running/count")
        listed = await _read(w.state)
    assert count["live"] == 50 and len(listed["running"]) == 50
    assert spy.call_count == 0, "neither read opens a loop's findings"


# ── 5. who reads what ────────────────────────────────────────────────────────────────────────


async def _as(state: Any, caller: str, path: str, **headers: str) -> tuple[int, Any]:
    async with TestClient(TestServer(_gateway(state, caller, _routes()))) as http:
        resp = await http.get(path, headers=headers)
        body = await resp.json() if resp.content_type == "application/json" else await resp.text()
        return resp.status, body


def test_a_frame_about_nothing_is_heard_by_no_app():
    """The running list asks the state whether an app hears of a row's frame
    (``_app_hears_of``), and a frame whose subject is nothing answers no: an app that would own
    any conversation still hears no notification frame and no phase frame without a mapping. A
    row that names no work and no session reads as work no chat started, which no app hears of,
    so the list shows it to no app either."""
    from personalclaw.dashboard.ws_state import DashboardWebSocketState, frame_subject
    from personalclaw.running_now import Reach

    ws = DashboardWebSocketState.__new__(DashboardWebSocketState)
    ws.session_creating_app = lambda key: APP  # it would own any conversation
    assert frame_subject("notification", {"session": "c1"}) is None
    assert ws._app_hears_of(APP, "notification", {"session": "c1"}) is False
    assert frame_subject("work_phase", None) is None
    assert ws._app_hears_of(APP, "work_phase", None) is False
    assert Reach(ws, app=APP).sees({}) is False
    assert Reach(ws, app=APP).sees({"work": None, "session": ""}) is False
    # The control: the same app hears of, and reads, the work of a chat it started.
    assert ws._app_hears_of(APP, "work_phase", {"work": "chat:c1", "session": "c1"}) is True
    assert Reach(ws, app=APP).sees({"work": "chat:c1", "session": "c1"}) is True


@pytest.mark.asyncio
async def test_an_app_reads_only_the_work_its_socket_would_hear_of(tmp_path):
    """The differential: every row the owner reads, an app reads exactly when its socket would
    carry that work's phase frame (``ws_state.frame_subject``, ``_app_may_hear_about``). So its
    own chat and the helper working for it, and none of your chats, loops, runs, fires or the
    helpers that work for them; and the count is cut the same way."""
    from personalclaw.dashboard.ws_state import frame_subject

    with _home(tmp_path / "home"):
        _install(tmp_path / "home", APP, {"api": ["/api/running"]})
        async with _everything(tmp_path) as (w, ids):
            owner = _by_work(await _read(w.state))
            status, listed = await _as(w.state, APP, "/api/running")
            assert status == 200, listed
            status, count = await _as(w.state, APP, "/api/running/count")
            assert status == 200, count

    heard = {
        work
        for work, found in owner.items()
        if w.state._app_may_hear_about(
            APP,
            "work_phase",
            frame_subject("work_phase", {"work": work, "session": found["session"]}),
        )
    }
    assert heard == {ids["apps_chat"], ids["apps_helper"]}, heard
    assert set(_by_work(listed)) == heard
    assert count == {"live": 2, "blocked": 1, "waiting": 0, "finished": 0}, count


@pytest.mark.asyncio
async def test_an_app_that_does_not_declare_the_route_is_refused(tmp_path):
    with _home(tmp_path / "home"):
        _install(tmp_path / "home", UNDECLARED_APP, {"api": ["/api/chat"]})
        w = _World(tmp_path, _held(asyncio.Event()))
        for path in ("/api/running", "/api/running/count"):
            status, body = await _as(w.state, UNDECLARED_APP, path)
            assert status == 403, (path, body)
            assert "api path not in declared permissions" in str(body)


@pytest.mark.asyncio
async def test_an_agent_reads_only_its_own_chats_work(tmp_path):
    async with _everything(tmp_path) as (w, ids):
        status, listed = await _as(
            w.state,
            "",
            "/api/running",
            **{"X-Internal-Secret": "the gateway's", "X-Session-Key": f"dashboard:{CHAT}"},
        )
    assert status == 200, listed
    assert set(_by_work(listed)) == {ids["chat"], ids["helper"], ids["queued"]}


@pytest.mark.asyncio
async def test_an_app_reads_only_its_own_of_what_just_finished(tmp_path):
    """The tail is cut by the same rule: when the app's chat and your helper both end, the app reads
    its chat's ending and not your helper's."""
    with _home(tmp_path / "home"):
        _install(tmp_path / "home", APP, {"api": ["/api/running"]})
        async with _everything(tmp_path) as (w, ids):
            on_event, manager = _gateway_events(w.state)
            helper = w.state.subagents.all_agents[0]
            helper.done, helper.elapsed = True, 4.0
            await on_event("subagent_done", helper, {})
            chat_phase.end(w.state, w.state._sessions[APPS_CHAT])
            owner = await _read(w.state)
            status, listed = await _as(w.state, APP, "/api/running")
    assert status == 200, listed
    assert {r["work"] for r in owner["finished"]} == {ids["helper"], ids["apps_chat"]}
    assert [r["work"] for r in listed["finished"]] == [ids["apps_chat"]]


@pytest.mark.asyncio
async def test_an_app_holding_the_approval_relay_reads_no_more_of_the_list(tmp_path):
    """An app that may read every approval (``/api/approvals``) hears approval frames for every
    conversation; a phase frame is not one, so it reads the same rows as any other app."""
    with _home(tmp_path / "home"):
        _install(tmp_path / "home", APP, {"api": ["/api/running", "/api/approvals"]})
        async with _everything(tmp_path) as (w, ids):
            w.state._pending_approvals["a-loop"] = {
                "id": "a-loop",
                "session": f"loop-{ids['loop_id']}",
                "tool": "shell",
                "ts": time.time(),
            }
            status, listed = await _as(w.state, APP, "/api/running")
    assert status == 200, listed
    assert set(_by_work(listed)) == {ids["apps_chat"], ids["apps_helper"]}


@pytest.mark.asyncio
async def test_a_chats_ending_joins_the_tail_and_a_loop_workers_does_not(tmp_path):
    w = _World(tmp_path, lambda: _streamed(_answer()))
    worker = w.state.get_or_create_session("loop-0a1b2c3d")
    chat_phase.begin(w.state, worker)
    await w.turn()
    chat_phase.end(w.state, worker)
    listed = await _read(w.state)
    assert listed["running"] == []
    (done,) = listed["finished"]
    assert (done["work"], done["kind"], done["phase"], done["route"]) == (
        f"chat:{CHAT}",
        "chat",
        "done",
        f"#/chat/{CHAT}",
    ), "a loop's worker's turn is its loop's, which says its own ending"


async def _streamed(events: list) -> AsyncIterator[Any]:
    for event in events:
        yield event


@pytest.mark.asyncio
async def test_two_webhook_turns_on_one_key_are_one_row_and_a_failed_one_ends_stopped(tmp_path):
    from personalclaw.dashboard.handlers import hooks

    w = _World(tmp_path, _held(asyncio.Event()))
    w.state.sessions.release = MagicMock()
    w.state.sessions.reset = AsyncMock()
    w.state.sessions.record_failure = AsyncMock()
    started, gates = [], [asyncio.Event(), asyncio.Event()]

    async def inner(*_a: Any, **_kw: Any) -> str:
        gate = gates[len(started)]
        started.append(gate)
        await gate.wait()
        if gate is gates[1]:
            raise RuntimeError("the deploy script exited 1")
        return "deployed"

    for _ in gates:
        await hooks._hook_semaphore.acquire()
    with patch.object(hooks, "_run_hook_inner", inner), patch.object(hooks, "_sel", MagicMock()):
        turns = [
            asyncio.ensure_future(
                hooks._run_hook_agent(w.state, "hook:deploy", "go", "Deploy hook", "", False, 30)
            )
            for _ in gates
        ]
        while len(started) < 2:
            await asyncio.sleep(0.01)
        (one,) = (await _read(w.state))["running"]
        assert one["work"] == "webhook:deploy", "two turns on one key are one piece of work"
        gates[0].set()
        await asyncio.wait_for(turns[0], timeout=5)
        assert [r["work"] for r in (await _read(w.state))["running"]] == ["webhook:deploy"]
        gates[1].set()
        await asyncio.wait_for(turns[1], timeout=5)
    listed = await _read(w.state)
    assert listed["running"] == []
    (done,) = listed["finished"]
    assert done["phase"] == "error" and done["line"].startswith("Hook agent error"), done
