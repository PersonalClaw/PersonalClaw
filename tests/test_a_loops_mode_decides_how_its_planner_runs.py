"""A loop's Mode decides how its planner runs, the way it decides how its workers run.

Measured on an Attended Code loop: its planner (the design pass and each step pass of the planning
walkthrough) was told "[AUTONOMOUS RUN — no user is present to reply] You are running unattended",
and all 23 of its tool calls ran with no approval — the repository's test suite and an ``rm -rf``
of a scratch folder among them — while the Mode's own words promised that the loop's tool calls ask
the way a chat's do. Its model spend counted against the daily cap for unattended work.

The planner runner armed every planner with a standing grant and the autonomous framing whatever
the loop's Mode was. Now one place reads a loop's Mode and arms every session the loop runs from it
(``loop.posture``): an Attended loop's planner asks a person and is told of no autonomous run, an
Unattended loop's runs on its grant, and a Mode that cannot be read is the cautious reading.

Driven with the shipped offline model (``ScriptedProvider``): its turn asks to run a command, and
the test watches where that ask goes.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.loop import manager, plan_walkthrough
from personalclaw.loop import posture as posture_mod
from personalclaw.loop import store
from personalclaw.loop.loop import Loop
from personalclaw.planning import runner

#: The command the planner's scripted turn asks to run: the test suite of the codebase it plans.
_COMMAND = {"command": "uv run pytest -q"}


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch home: the loop store, the tasks store and the offline model's gate all read it."""
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    script = tmp_path / "planner-script.json"
    script.write_text(
        json.dumps(
            {
                "version": 1,
                "on_exhausted": "repeat_last",
                "turns": [
                    {
                        "text": "Reading the codebase first.",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "name": "bash",
                                "input": _COMMAND,
                                "risk_level": "destructive",
                                "requires_approval": True,
                            }
                        ],
                        "stop_reason": "end_turn",
                    }
                ],
            }
        )
    )
    from personalclaw.llm.registry import SCRIPTED_PROVIDER_ENV

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv(SCRIPTED_PROVIDER_ENV, str(script))
    monkeypatch.setattr(runner, "PLANNER_POLL_SECS", 0.02)
    yield tmp_path
    manager._LOOP_GRANTS.clear()


def _planning_loop(*, attended) -> Loop:
    loop = store.create(
        Loop(
            id="",
            name="Digest titles",
            kind="code",
            task="fix the double-escaped titles in the morning digest",
            attended=attended,
        )
    )
    return loop


def _dashboard(tmp_path: Path, model) -> "DashboardState":  # noqa: F821 - imported below
    """A real dashboard state whose session manager hands every session the offline model."""
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import ToolHookResult

    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_or_create = AsyncMock(return_value=(model, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    builder = MagicMock()
    builder.hooks.on_tool_call.return_value = ToolHookResult.allow()
    builder.build_message.side_effect = lambda text, *a, **kw: (text, None)
    state.context_builder = builder
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hooks
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state


class _Nudges:
    """The autonudge service, as far as a planner pass uses it. ``add`` arms the planner's run; the
    first cycle fires at once here (the real service fires it after its idle wait), and once the
    turn has ended the planner's file is written, as a planner that finished its step would."""

    def __init__(self, state, files_dir: str, *, answer: str | None):
        self.state = state
        self.files_dir = files_dir
        self.answer = answer
        self.messages: list[str] = []
        self.asked: list[dict] = []
        self.rows: dict[str, SimpleNamespace] = {}
        self.turns: list[asyncio.Task] = []

    async def add(self, *, session_name, message, **_kw):
        self.messages.append(message)
        row = SimpleNamespace(id=f"N{len(self.rows)}", session_name=session_name, active=True)
        self.rows[row.id] = row
        self.turns.append(
            asyncio.get_running_loop().create_task(self._cycle(session_name, message))
        )
        return row

    async def _cycle(self, key: str, message: str):
        from personalclaw.dashboard.chat_runner import run_chat

        session = self.state._sessions[key]
        answering = asyncio.get_running_loop().create_task(self._answer(session))
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            await run_chat(self.state, session, message)
        answering.cancel()
        Path(self.files_dir, plan_walkthrough.STEPS_SENTINEL).write_text(
            json.dumps({"summary": "s", "steps": [{"kind": "decomposition", "title": "Tasks"}]})
        )

    async def _answer(self, session):
        """Answer the planner's ask the way the loop's page would, once it is put to a person."""
        while True:
            pending = [
                e for e in self.state._pending_approvals.values() if e["session"] == session.key
            ]
            if pending:
                self.asked.extend(pending)
                fut = session._approval_futures.get(pending[0]["request_id"])
                if fut is not None and not fut.done() and self.answer:
                    fut.set_result(self.answer)
                return
            await asyncio.sleep(0.01)

    def get_by_session(self, name):
        return next((r for r in self.rows.values() if r.session_name == name), None)

    def list_all(self):
        return list(self.rows.values())

    async def update(self, row_id, **kw):
        for k, v in kw.items():
            setattr(self.rows[row_id], k, v)

    async def remove(self, row_id):
        self.rows.pop(row_id, None)


async def _design_pass(tmp_path: Path, loop: Loop, *, answer: str | None = "approved"):
    from personalclaw.llm.scripted import ScriptedProvider

    model = ScriptedProvider()
    state = _dashboard(tmp_path, model)
    nudges = _Nudges(state, plan_walkthrough._loop_folder(loop), answer=answer)
    seeded = await asyncio.wait_for(
        plan_walkthrough.run_design_pass(state, nudges, loop.id), timeout=30
    )
    for turn in nudges.turns:
        await turn
    planner = state._sessions[plan_walkthrough.planner_session_key(loop.id)]
    return SimpleNamespace(seeded=seeded, nudges=nudges, planner=planner, model=model, state=state)


# ── the planner of an Attended loop asks, as its workers do ──────────────────────────────


@pytest.mark.asyncio
async def test_an_attended_loops_planner_asks_before_its_first_command(tmp_path):
    loop = _planning_loop(attended=True)

    run = await _design_pass(tmp_path, loop)

    assert run.nudges.messages, "vacuity: no planner pass was armed"
    for message in run.nudges.messages:
        assert "AUTONOMOUS RUN" not in message and "running unattended" not in message, message
    assert [e["tool"] for e in run.nudges.asked] == ["bash"], "the command was put to nobody"
    assert run.model.decisions == [("call-1", "approved")], "it ran without the person's answer"
    assert run.planner._trust is False and run.planner._unattended is False
    assert run.seeded is not None, "the pass did not finish once its ask was answered"


@pytest.mark.asyncio
async def test_an_attended_loops_planner_asks_under_the_owners_approval_mode_auto(
    tmp_path, monkeypatch
):
    """Settings → Agent defaults → Approval mode "auto", once the owner chooses it, is the grant an
    agent that no chat started runs on. An Attended loop's planner is not answered by that grant,
    nor by any standing one: its posture is its loop's, so its call is put to a person under
    "auto" too."""
    from personalclaw.config.loader import AppConfig

    chosen = AppConfig()
    chosen.agent.approval_mode = "auto"
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: chosen))
    loop = _planning_loop(attended=True)

    run = await _design_pass(tmp_path, loop)

    assert [e["tool"] for e in run.nudges.asked] == ["bash"], "the command was put to nobody"
    assert run.model.decisions == [("call-1", "approved")]
    assert run.planner._trust is False and run.planner._unattended is False


@pytest.mark.asyncio
async def test_a_command_the_person_declines_does_not_run(tmp_path):
    loop = _planning_loop(attended=True)

    run = await _design_pass(tmp_path, loop, answer="rejected")

    assert run.model.decisions == [("call-1", "rejected")]


# ── the planner of an Unattended loop runs on its grant, framed as an autonomous run ─────


@pytest.mark.asyncio
async def test_an_unattended_loops_planner_runs_on_its_grant(tmp_path):
    from personalclaw.loop import posture

    loop = _planning_loop(attended=False)

    run = await _design_pass(tmp_path, loop, answer=None)

    assert run.nudges.messages and all("AUTONOMOUS RUN" in m for m in run.nudges.messages)
    assert run.nudges.asked == [], "an unattended planner put its call to a person"
    assert run.model.decisions == [("call-1", "approved")]
    assert posture.of(store.get(loop.id)) is posture.UNATTENDED
    assert run.planner._trust is True and run.planner._unattended is True


# ── a Mode that cannot be read is the cautious reading ───────────────────────────────────


@pytest.mark.parametrize("mode", [None, "", "no", 0, 1, "true"])
def test_a_mode_that_is_not_true_or_false_asks_and_its_spend_still_counts(mode):
    from personalclaw.loop import posture

    got = posture.of(SimpleNamespace(attended=mode))

    assert got.asks is True and got.metered is True
    assert posture.of(None) is posture.UNREADABLE
    assert posture.of(SimpleNamespace()) is posture.UNREADABLE


def test_only_an_explicit_mode_is_read_as_one():
    from personalclaw.loop import posture

    assert posture.of(SimpleNamespace(attended=True)) is posture.ATTENDED
    assert posture.of(SimpleNamespace(attended=False)) is posture.UNATTENDED
    assert posture.ATTENDED.asks and not posture.ATTENDED.metered
    assert not posture.UNATTENDED.asks and posture.UNATTENDED.metered


@pytest.mark.asyncio
async def test_a_planner_pass_given_no_mode_asks(tmp_path, monkeypatch):
    """A caller that names no Mode gets the cautious one: the planner asks, framed as a person's
    work, and its spend counts."""
    monkeypatch.setattr(runner, "PLANNER_FIRST_IDLE", 0)
    files = tmp_path / "loop-folder"
    files.mkdir()
    planner = SimpleNamespace(acp_provider="", acp_mode="", _extra_tool_roots=[])
    state = SimpleNamespace(
        get_or_create_session=lambda **_kw: planner,
        push_sessions_update=lambda: None,
        waiting_on_owner=lambda _key: False,
    )
    armed: list[str] = []

    async def _add(*, message, **_kw):
        armed.append(message)
        (files / "plan_steps.json").write_text("{}")

    svc = SimpleNamespace(
        add=_add, get_by_session=lambda _k: None, remove=AsyncMock(), list_all=lambda: []
    )

    out = await runner.run_planner_pass(
        state,
        svc,
        session_key="loop-plan-0a1b2c3d",
        agent_name="planner",
        workspace_dir="",
        files_dir=str(files),
        sentinel="plan_steps.json",
        brief="plan it",
        app="loops",
    )

    assert out.ended == runner.WROTE
    assert armed and "AUTONOMOUS RUN" not in armed[0]
    assert planner._trust is False and planner._unattended is False
    assert planner._spend_metered is True


def test_a_stored_mode_that_cannot_be_read_comes_back_attended():
    loop = _planning_loop(attended=False)
    conn = store._connect()
    try:
        # A row whose Mode is not one the store writes (a damaged or hand-edited home).
        conn.execute("UPDATE loops SET attended = '' WHERE id = ?", (loop.id,))
        conn.commit()
    finally:
        conn.close()

    assert store.get(loop.id).attended is True


def test_a_loop_made_with_no_mode_is_attended():
    from personalclaw.dashboard.handlers.loop_routes import _build_loop_from_body

    assert Loop(id="", name="n", kind="code", task="t").attended is True
    assert _build_loop_from_body({"kind": "code", "task": "fix the digest"}).attended is True
    assert _build_loop_from_body({"kind": "code", "task": "t", "attended": False}).attended is False


@pytest.mark.asyncio
async def test_a_loop_an_agent_makes_without_saying_is_attended(tmp_path):
    from personalclaw.agents.native import sdlc_tools
    from personalclaw.dashboard.handlers import loop_routes

    with patch.object(
        loop_routes, "_installed_capability_catalogs", AsyncMock(return_value=([], []))
    ):
        made = await sdlc_tools.code_project_create(
            {"task": "fix the double-escaped digest titles", "project_kind": "greenfield"}
        )
        goal = await sdlc_tools.goal_loop_create(
            {"goal": "summarise the week's feeds into one digest", "kind": "general"}
        )
    assert made.success and goal.success, (made.error, goal.error)
    assert {lp.attended for lp in store.list_all()} == {True}


# ── one place arms every session a loop runs ─────────────────────────────────────────────


class _Sessions:
    """The slice of the dashboard the loop manager arms its workers through."""

    def __init__(self):
        from personalclaw.dashboard.state import _ChatSession

        self._make = _ChatSession
        self._sessions: dict = {}
        self.sessions = MagicMock()

    def get_or_create_session(self, *, name, agent="", model="", workspace_dir="", app="", **_kw):
        s = self._sessions.get(name)
        if s is None:
            s = self._make(name, agent=agent, workspace_dir=workspace_dir, model=model)
            s._app = app
            self._sessions[name] = s
        return s

    def push_sessions_update(self):
        pass

    def waiting_on_owner(self, _key):
        return False


@pytest.mark.parametrize("attended", [True, False])
def test_the_planner_and_the_workers_of_one_loop_share_one_posture(attended):
    loop = _planning_loop(attended=attended)
    state = _Sessions()
    nudges = _Nudges(state, "", answer=None)
    nudges._cycle = AsyncMock()  # type: ignore[method-assign]
    planner_key = plan_walkthrough.planner_session_key(loop.id)

    async def _drive():
        files = plan_walkthrough._loop_folder(loop)
        task = asyncio.get_running_loop().create_task(
            plan_walkthrough._run_pass(
                state,
                nudges,
                store.get(loop.id),
                plan_walkthrough._walkthrough_for(loop.id)[1],
                brief="b",
                sentinel="plan_steps.json",
                timeout_secs=1,
            )
        )
        await asyncio.sleep(0.1)
        Path(files, "plan_steps.json").write_text("{}")
        await task
        await manager.start(state, nudges, loop.id)

    asyncio.get_event_loop().run_until_complete(_drive())

    planner = state._sessions[planner_key]
    worker = state._sessions[manager.session_key(loop.id)]
    for session in (planner, worker):
        assert session._trust is (not attended), session.key
        assert session._unattended is (not attended), session.key
        assert session._spend_metered is (not attended), session.key
    framed = ["AUTONOMOUS RUN" in m for m in nudges.messages]
    assert framed == [not attended] * len(framed) and len(framed) == 2


# ── the planner's asks are the loop's: named for it, and "This loop" reaches its passes ──


def test_the_inbox_row_names_the_loop_whose_planner_is_asking():
    from personalclaw.dashboard.approval_state import _approval_row_body

    loop = _planning_loop(attended=True)
    body = _approval_row_body(
        {
            "agent": "personalclaw-code-planner",
            "tool": "bash",
            "risk": "destructive",
            "session": plan_walkthrough.planner_session_key(loop.id),
        }
    )

    assert body.startswith(
        "personalclaw-code-planner in the loop “Digest titles” is waiting for your decision on bash"
    ), body


def test_this_loop_on_the_planners_card_reaches_its_later_passes_and_ends_at_launch(tmp_path):
    from personalclaw.approval_answer import YOU

    loop = _planning_loop(attended=True)
    state = _dashboard(tmp_path, MagicMock())
    key = plan_walkthrough.planner_session_key(loop.id)
    planner = state.get_or_create_session(name=key, app="loops")
    planner._approval_futures["call-1"] = asyncio.get_event_loop().create_future()

    state.decide_session_approval(planner, "call-1", "trust", by=YOU)
    assert manager._LOOP_GRANTS == {loop.id}

    async def _next_pass():
        nudges = _Nudges(state, "", answer=None)
        nudges._cycle = AsyncMock()  # type: ignore[method-assign]
        files = plan_walkthrough._loop_folder(loop)
        task = asyncio.get_running_loop().create_task(
            plan_walkthrough._run_pass(
                state,
                nudges,
                store.get(loop.id),
                plan_walkthrough._walkthrough_for(loop.id)[1],
                brief="b",
                sentinel="plan_steps.json",
                timeout_secs=1,
            )
        )
        await asyncio.sleep(0.1)
        Path(files, "plan_steps.json").write_text("{}")
        await task
        return nudges

    nudges = asyncio.get_event_loop().run_until_complete(_next_pass())
    assert planner._trust is True, "the planner's next pass asked again inside the grant"

    asyncio.get_event_loop().run_until_complete(manager.start(state, nudges, loop.id))
    worker = state._sessions[manager.session_key(loop.id)]
    assert manager._LOOP_GRANTS == set() and worker._trust is False, "the launch kept the grant"


def test_a_planner_pass_does_not_run_out_of_time_while_its_owner_decides(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PLANNER_FIRST_IDLE", 0)
    files = tmp_path / "loop-folder"
    files.mkdir()
    planner = SimpleNamespace(acp_provider="", acp_mode="", _extra_tool_roots=[])
    deciding = {"now": True}
    state = SimpleNamespace(
        get_or_create_session=lambda **_kw: planner,
        push_sessions_update=lambda: None,
        waiting_on_owner=lambda _key: deciding["now"],
    )

    async def _add(**_kw):
        async def _answered_late():
            await asyncio.sleep(0.6)  # longer than the pass's whole time limit
            deciding["now"] = False
            (files / "plan_steps.json").write_text("{}")

        asyncio.get_running_loop().create_task(_answered_late())

    svc = SimpleNamespace(
        add=_add,
        get_by_session=lambda _k: SimpleNamespace(active=True),
        remove=AsyncMock(),
        list_all=lambda: [],
    )

    out = asyncio.get_event_loop().run_until_complete(
        runner.run_planner_pass(
            state,
            svc,
            session_key="loop-plan-0a1b2c3d",
            agent_name="planner",
            workspace_dir="",
            files_dir=str(files),
            sentinel="plan_steps.json",
            brief="plan it",
            app="loops",
            timeout_secs=0.3,
            posture=posture_mod.ATTENDED,
        )
    )

    assert out.ended == runner.WROTE, out


def test_a_planners_scratch_goes_in_the_loops_folder_and_goes_with_the_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PLANNER_FIRST_IDLE", 0)
    files = tmp_path / "loop-folder"
    files.mkdir()
    planner = SimpleNamespace(acp_provider="", acp_mode="", _extra_tool_roots=[])
    state = SimpleNamespace(
        get_or_create_session=lambda **_kw: planner,
        push_sessions_update=lambda: None,
        waiting_on_owner=lambda _key: False,
    )
    told: list[str] = []

    async def _add(*, message, **_kw):
        told.append(message)
        (files / runner.SCRATCH_DIR / "copy").mkdir(parents=True)
        (files / runner.SCRATCH_DIR / "copy" / "test_digest.py").write_text("x")
        (files / "plan_steps.json").write_text("{}")

    svc = SimpleNamespace(
        add=_add, get_by_session=lambda _k: None, remove=AsyncMock(), list_all=lambda: []
    )
    asyncio.get_event_loop().run_until_complete(
        runner.run_planner_pass(
            state,
            svc,
            session_key="loop-plan-0a1b2c3d",
            agent_name="planner",
            workspace_dir="",
            files_dir=str(files),
            sentinel="plan_steps.json",
            brief="plan it",
            app="loops",
            posture=posture_mod.ATTENDED,
        )
    )

    assert f"`{files / runner.SCRATCH_DIR}`" in told[0] and "never in a shared temporary" in told[0]
    assert not (files / runner.SCRATCH_DIR).exists()
