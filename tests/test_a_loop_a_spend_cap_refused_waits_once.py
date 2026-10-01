"""A loop a spend cap refused waits for its owner: told once, shown as paused, never retried.

Measured on a Code loop whose planner ran on a paid model past the daily dollar cap: the cap refused
the planner at 10:16:36, again at 10:21:40 and again at 10:23:10 — its nudges sent the same refused
call to the same cap twice more, and the walkthrough's one retry would have sent a fourth — while
the cockpit stayed on "Planning… · Drafting this step…" with no bell, no Inbox item and nothing on
the phone. The refusal is a pause, not a fault:

* the session a cap refused is not nudged again (``AutoNudgeService.notify_turn_complete``);
* a planner pass the cap refused is not retried, and the walkthrough is paused with the cap's own
  sentence until its owner resumes it: a poll or a restart runs nothing (``advance_plan``);
* a worker of a running loop the cap refused puts the loop on hold (``needs_input``) with that
  sentence, and does not count toward failing it;
* each pause raises ONE Inbox item and its ONE notification, however many calls the cap refuses.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from personalclaw.guardrails.failure import NO_ROOM, BudgetExceededError
from personalclaw.inbox import InboxStore, ItemStatus
from personalclaw.loop import files as loop_files
from personalclaw.loop import kinds
from personalclaw.loop import plan_walkthrough as pw
from personalclaw.loop import spend_cap
from personalclaw.loop import store as loop_store
from personalclaw.loop import watchdog as W
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.planning import runner as R
from personalclaw.planning.session import PlanSession, PlanStep, StepStatus


def _refusal() -> BudgetExceededError:
    return BudgetExceededError(
        "day", "dollars", 4.0, 3.944, unpriced=11, why=NO_ROOM, needed=0.299, ref="cloud:example"
    )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _InboxSvc:
    def __init__(self, store: InboxStore) -> None:
        self.inbox = store


class _State:
    """A live gateway's state as far as a loop's pause reaches it: its Inbox service (a real
    ``InboxStore``, which ``inbox.live_store`` checks by type), the bell, the loop stream."""

    def __init__(self, store: InboxStore) -> None:
        from personalclaw.dashboard.sse import SseRegistry

        self._inbox_svc = _InboxSvc(store)
        self._sessions: dict[str, Any] = {}
        self._sse = SseRegistry()
        self.notified: list[tuple] = []

    def notify(self, *args: Any, **kwargs: Any) -> None:
        self.notified.append(args)

    def broadcast_ws(self, *_a: Any, **_kw: Any) -> None:
        pass

    def loop_sse(self):
        return self._sse

    def push_refresh(self, *_kinds: Any) -> None:
        pass


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _State:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path, raising=False)
    kinds.ensure_loaded()
    store = InboxStore()
    store.load()
    state = _State(store)
    from personalclaw.inbox_providers import native_source

    monkeypatch.setattr(native_source, "_dashboard_state", state, raising=False)
    return state


def _rows(state: _State, loop_id: str) -> list:
    return [i for i in state._inbox_svc.inbox.items.values() if i.refs.get("loop") == loop_id]


def _open(state: _State, loop_id: str) -> list:
    return [i for i in _rows(state, loop_id) if i.status == ItemStatus.PENDING.value]


# ── a refused session is not nudged again ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_refused_turn_switches_its_nudge_loop_off_at_once(tmp_path):
    from personalclaw.triggers.nudge import AutoNudgeService

    svc = AutoNudgeService(base_dir=tmp_path)
    await svc.add(session_name="loop-a1b2c3d4", message="go", idle_secs=30, max_cycles=5)

    svc.notify_turn_complete("loop-a1b2c3d4", errored=True, refused=True)

    loop = svc.get_by_session("loop-a1b2c3d4")
    assert loop is not None, "the loop is kept, for its owner's Resume"
    assert loop.active is False, "the next cycle would send the same refused call again"


@pytest.mark.asyncio
async def test_an_ordinary_failed_turn_is_still_retried(tmp_path):
    # The control: a failure that is not a refusal keeps the loop's bounded retries.
    from personalclaw.triggers.nudge import AutoNudgeService

    svc = AutoNudgeService(base_dir=tmp_path)
    await svc.add(session_name="loop-a1b2c3d4", message="go", idle_secs=30, max_cycles=5)

    svc.notify_turn_complete("loop-a1b2c3d4", errored=True)

    assert svc.get_by_session("loop-a1b2c3d4").active is True


# ── a planner the cap refused ──────────────────────────────────────────────────────────────


def _planning_loop() -> Loop:
    loop = loop_store.create(Loop(id="", name="Fix the feed titles", kind="code", task="fix it"))
    loop_store.update_status(loop.id, LoopStatus.PLANNING)
    step = PlanStep(id="step-0", kind="problem_framing", title="Frame it", objective="o")
    loop_files.write_plan_session(PlanSession(project_id=loop.id, steps=[step]))
    return loop


def _scripted(monkeypatch, *passes: R.PlannerPass) -> list[str]:
    briefs: list[str] = []
    queue = list(passes)

    async def _fake_run_pass(state, svc, lp, wt, *, brief, sentinel, timeout_secs=None):
        briefs.append(brief)
        return queue.pop(0)

    monkeypatch.setattr(pw, "_run_pass", _fake_run_pass)
    return briefs


def test_a_refused_planner_pass_pauses_the_walkthrough_and_is_not_retried(monkeypatch, live):
    loop = _planning_loop()
    refused = R.PlannerPass(ended=R.REFUSED, limit_secs=600, refusal=_refusal())
    briefs = _scripted(monkeypatch, refused)

    assert _run(pw.run_step_pass(live, object(), loop.id, "step-0")) is None

    assert len(briefs) == 1, "the pass was retried against the same cap"
    session = loop_files.read_plan_session(loop.id)
    step = session.steps[0]
    assert step.status == StepStatus.PENDING.value
    assert step.error == _refusal().sentence()
    assert "gateway log" not in step.error and "doesn't recognize" not in step.error
    assert session.paused == {"by": "spend_cap", "settings": "guardrails"}
    (row,) = _rows(live, loop.id)
    assert row.status == ItemStatus.PENDING.value
    assert spend_cap.TITLE in row.message and _refusal().sentence() in row.message
    assert len(live.notified) == 1, live.notified


def test_a_paused_walkthrough_runs_nothing_until_its_owner_resumes_it(monkeypatch, live):
    loop = _planning_loop()
    good = R.PlannerPass(text='{"markdown": "Framed."}', ended=R.WROTE, limit_secs=600)
    briefs = _scripted(
        monkeypatch, R.PlannerPass(ended=R.REFUSED, limit_secs=600, refusal=_refusal()), good
    )
    _run(pw.run_step_pass(live, object(), loop.id, "step-0"))

    # A poll, a remount or a restart's re-kick: nothing runs, and nobody is told twice.
    assert _run(pw.advance_plan(live, object(), loop.id)) == "paused"
    assert _run(pw.advance_plan(live, object(), loop.id)) == "paused"
    assert len(briefs) == 1
    assert len(live.notified) == 1

    # Resume (the walkthrough's button, the plan's retry route): the pause and its row are over.
    pw.clear_design_error(loop.id)
    assert _open(live, loop.id) == []
    assert _run(pw.advance_plan(live, object(), loop.id)) == "produced"
    assert len(briefs) == 2
    assert "previous attempt" not in briefs[1], "the planner was told the cap was its failure"
    assert loop_files.read_plan_session(loop.id).paused == {}


def test_a_design_pass_the_cap_refused_is_paused_not_failed(monkeypatch, live):
    loop = loop_store.create(Loop(id="", name="Fix the feed titles", kind="code", task="fix it"))
    loop_store.update_status(loop.id, LoopStatus.PLANNING)
    _scripted(monkeypatch, R.PlannerPass(ended=R.REFUSED, limit_secs=600, refusal=_refusal()))

    assert _run(pw.run_design_pass(live, object(), loop.id)) is None

    session = loop_files.read_plan_session(loop.id)
    assert session.design_error == _refusal().sentence()
    assert session.paused["by"] == "spend_cap"
    assert len(_open(live, loop.id)) == 1


def test_a_loop_that_stops_planning_closes_its_pause(monkeypatch, live):
    loop = _planning_loop()
    _scripted(monkeypatch, R.PlannerPass(ended=R.REFUSED, limit_secs=600, refusal=_refusal()))
    _run(pw.run_step_pass(live, object(), loop.id, "step-0"))
    assert len(_open(live, loop.id)) == 1

    loop_store.update_status(loop.id, LoopStatus.STOPPED)

    assert _open(live, loop.id) == []


@pytest.mark.asyncio
async def test_the_planner_pass_ends_on_the_refusal(monkeypatch):
    """The pass reads its own session's refusal the moment its nudge loop is switched off,
    rather than waiting out the dead-loop grace and calling it a planner that stopped."""
    from types import SimpleNamespace

    monkeypatch.setattr(R, "PLANNER_POLL_SECS", 0.01)
    refusal = _refusal()
    session = SimpleNamespace(_trust=False, _extra_tool_roots=[], _last_turn_refusal=None)
    nudge = SimpleNamespace(id="N1", active=True)

    class _Svc:
        async def add(self, **_kw):
            pass

        def get_by_session(self, _key):
            # The planner's first turn has run: the cap refused it, and its nudge loop is off.
            session._last_turn_refusal = refusal
            nudge.active = False
            return nudge

        async def remove(self, _id):
            pass

    state = SimpleNamespace(
        get_or_create_session=lambda **_kw: session, push_sessions_update=lambda: None
    )
    result = await R.run_planner_pass(
        state,
        _Svc(),
        session_key="loop-plan-a1b2c3d4",
        agent_name="planner",
        workspace_dir="",
        files_dir="",
        sentinel="step_artifact.json",
        brief="plan",
        app="loops",
        timeout_secs=5,
    )
    assert result.ended == R.REFUSED and result.refusal is refusal


# ── a running loop's worker the cap refused ──────────────────────────────────────────────


class _Svc:
    def list_all(self):
        return []

    def get_by_session(self, _name):
        return None


def _running_loop() -> Loop:
    loop = loop_store.create(Loop(id="", name="Fix the feed titles", kind="code", task="fix it"))
    loop_store.update_status(loop.id, LoopStatus.RUNNING)
    return loop


def test_a_refused_worker_puts_the_loop_on_hold_with_one_notification_and_one_item(live):
    loop = _running_loop()
    watchdog = W.LoopWatchdog(live, _Svc())

    assert watchdog.hold_for_spend_cap(f"loop-{loop.id}", _refusal()) is True
    # A task worker of the same loop the same cap refuses a moment later: the loop already waits.
    assert watchdog.hold_for_spend_cap(f"loop-{loop.id}-t-1a2b3c4d", _refusal()) is False

    assert loop_store.get(loop.id).status == LoopStatus.NEEDS_INPUT.value
    question = loop_files.pending_question(loop.id)
    assert question["question"] == _refusal().sentence()
    assert question["spend_cap"] is True and question["settings"] == "guardrails"
    (row,) = _rows(live, loop.id)
    assert row.message.startswith(spend_cap.TITLE)
    assert _refusal().sentence() in row.message
    assert "doesn't recognize" not in row.message and "Worker failed" not in row.message
    assert len(live.notified) == 1, live.notified
    assert watchdog._consec_errors.get(loop.id) is None, "the refusal counted toward failing it"


def test_resuming_the_loop_closes_its_item(live):
    loop = _running_loop()
    W.LoopWatchdog(live, _Svc()).hold_for_spend_cap(f"loop-{loop.id}", _refusal())

    loop_store.update_status(loop.id, LoopStatus.RUNNING)

    assert _open(live, loop.id) == []


def test_a_loop_that_is_not_running_is_left_as_it_is(live):
    loop = loop_store.create(Loop(id="", name="Fix the feed titles", kind="code", task="fix it"))
    loop_store.update_status(loop.id, LoopStatus.PAUSED)

    assert W.LoopWatchdog(live, _Svc()).hold_for_spend_cap(f"loop-{loop.id}", _refusal()) is False
    assert loop_store.get(loop.id).status == LoopStatus.PAUSED.value
    assert live.notified == []


# ── a loop that is a workflow run (a General loop) ───────────────────────────────────────


def test_a_loop_run_a_spend_cap_stopped_raises_one_item_not_only_a_failure_note(monkeypatch):
    from personalclaw.workflows import attention
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    raised: list[dict] = []
    monkeypatch.setattr(
        "personalclaw.inbox.emit_attention_item", lambda state, **kw: raised.append(kw) or "i-1"
    )

    class _Bell:
        notes: list = []

        def notify(self, *args, **kwargs):
            self.notes.append(args)

    reason = f"“work” failed: {_refusal().headline()}."
    run = WorkflowRun(
        id="run-7f3c", workflow_name="general", loop_kind="general", error_message=reason
    )

    assert attention.announce_run_end(_Bell(), run, RunStatus.FAILED, refused=True) == "i-1"
    (item,) = raised
    assert item["title"] == spend_cap.TITLE and reason in item["body"]
    assert item["refs"]["loop"] == "run-7f3c"
    assert _Bell.notes == [], "a second note beside the item's own"


def test_the_run_knows_a_step_the_cap_refused():
    from types import SimpleNamespace

    from personalclaw.workflows.controller import RunController
    from personalclaw.workflows.failure_taxonomy import classify_exception
    from personalclaw.workflows.models import InstanceState

    refused = SimpleNamespace(state=InstanceState.FAILED, failure=classify_exception(_refusal()))
    other = SimpleNamespace(
        state=InstanceState.FAILED, failure=classify_exception(RuntimeError("boom"))
    )
    assert RunController._spend_refused(SimpleNamespace(instances={"a": refused})) is True
    assert RunController._spend_refused(SimpleNamespace(instances={"a": other})) is False
