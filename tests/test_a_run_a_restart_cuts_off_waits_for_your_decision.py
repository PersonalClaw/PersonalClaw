"""A run a stop or a restart cuts off says so, waits for your decision, and runs once when you say.

A Run now of an automation whose action starts an agent (a morning brief, `invoke-agent`) returns
as soon as the agent starts, and its run is recorded `launched`. A Restart then cancelled the agent,
which ends with its own word for that, "cancelled", and that ending was written onto the run's row
as a failure: its history read "failure · cancelled" and the bell "Morning brief failed ·
cancelled". Nothing named the restart, no card offered Run now or Dismiss, and the next start said
nothing about it.

Now the stop closes the run before it cuts the agent off: the row is `interrupted`, with a reason
that names the restart, and it is the trigger's last run; the card waits on the review; the agent's
own ending is not reported; and the next start sends one "Runs interrupted by a restart" notice.
Run now from the card runs the action once. The same holds for an action still running when the
stop came, and for the passes that close a run whose action never returned: the boot's, for a run
a crash left, and the deadline's.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from test_gateway import _make_orchestrator, _mock_dashboard_state, _mock_sessions

import personalclaw.config.loader as loader
from personalclaw import notification_kinds, restart_request
from personalclaw.action_providers.base import ActionResult
from personalclaw.dashboard.handlers import trigger_runs
from personalclaw.dashboard.handlers import triggers as T
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.schedule_history import ScheduleRunStore
from personalclaw.subagent import SubagentInfo
from personalclaw.triggers import claims, history, reaper, review
from personalclaw.triggers.models import TRUE_FAILURE_OUTCOMES, Outcome, Trigger, TriggerHealth
from personalclaw.triggers.schedule_view import _last_run_ts
from personalclaw.triggers.scheduling import PROCESS_IMAGE, Claim
from personalclaw.triggers.store import TriggerStore

TID = "clock:morning-brief"
NAME = "Morning brief"
AGENT = "5ca1ab1e"


# ── harness ──


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Every seam the stop, the boot and the handlers build a store or a history from."""
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.gateway.config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def restarting(monkeypatch):
    """The system menu's Restart: the stop that is under way is one that starts again."""
    asked = restart_request.RestartRequest("python", ("python",), {})
    monkeypatch.setattr(restart_request, "pending", lambda: asked)


class _Action:
    """The trigger's action. It starts the agent and returns, as `invoke-agent` does; with
    `blocks`, its first run is still going when the stop comes, as a long command's is."""

    def __init__(self, *, blocks: bool = False) -> None:
        self.runs = 0
        self.blocks = blocks
        self.started = asyncio.Event()

    async def execute(self, config: Any, ctx: Any, timeout: float = 30) -> ActionResult:
        self.runs += 1
        if self.blocks and self.runs == 1:
            self.started.set()
            await asyncio.sleep(3600)
        return ActionResult(
            success=True,
            stdout="spawned agent for: Summarise my Inbox.",
            outcome="launched",
            work_id=f"subagent:{AGENT}",
        )


@pytest.fixture
def action(monkeypatch):
    made = _Action()
    monkeypatch.setattr("personalclaw.action_providers.get_action_provider", lambda name: made)
    return made


def _brief(home) -> Trigger:
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=TID,
            name=NAME,
            kind="clock",
            enabled=True,
            created_by="user",
            spec={"kind": "cron", "expr": "15 7 * * 1-5"},
            capabilities={"providers": ["invoke-agent"]},
            workflow={
                "inline": {
                    "provider": "invoke-agent",
                    "config": {"task_template": "Summarise my Inbox."},
                }
            },
        )
    )
    return _live(home)


def _live(home) -> Trigger:
    loaded = TriggerStore(base_dir=home).get(TID)
    assert loaded is not None
    return loaded.trigger


def _rows(home) -> list[dict[str, Any]]:
    rows, _total = asyncio.run(ScheduleRunStore(home).list_for_job(TID, 0, 20))
    return rows


class _Agents:
    """The background agents as a stop finds them, and the cancel it makes: every agent it cuts
    off ends "cancelled", and that ending is delivered (`SubagentManager._run`)."""

    def __init__(self, on_done: Any) -> None:
        self.on_done = on_done
        self.agents: list[SubagentInfo] = []

    @property
    def running(self) -> list[SubagentInfo]:
        return [a for a in self.agents if not a.done]

    def get(self, agent_id: str) -> SubagentInfo | None:
        return next((a for a in self.agents if a.id == agent_id), None)

    def notify_injection_failed(self, info: Any, reason: str = "") -> None:
        pass

    async def cancel_all(self) -> None:
        cut = self.running
        for info in cut:
            info.done = True
            info.error = "cancelled"
        if cut:
            await self.on_done(cut)


def _gateway() -> tuple[GatewayOrchestrator, _Agents]:
    """A gateway with its real agent-completion delivery, over the agents a Run now started."""
    orch = _make_orchestrator()
    orch.sessions = _mock_sessions()
    orch.ctx_builder = MagicMock()
    orch.ctx_builder.hooks = MagicMock()
    orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
    orch.dashboard_state = _mock_dashboard_state()
    with (
        patch("personalclaw.trust_mode.is_yolo_active", return_value=False),
        patch("personalclaw.gateway.SubagentManager") as manager,
    ):
        manager.return_value = MagicMock(running=[], get=MagicMock(return_value=None))
        orch._init_subagents()
    agents = _Agents(manager.call_args[1]["on_done"])
    orch.subagent_mgr = agents
    return orch, agents


def _the_agent_works() -> SubagentInfo:
    return SubagentInfo(
        id=AGENT,
        task="Summarise my Inbox.",
        trigger_id=TID,
        title=NAME,
        started=time.time(),
    )


def _notes(state: Any) -> list[tuple[str, str, str]]:
    """Every note that went out, as (kind, title, body)."""
    out = []
    for call in state.notify.call_args_list:
        args, kwargs = call
        kind = kwargs.get("kind", args[0] if args else "")
        title = kwargs.get("title", args[1] if len(args) > 1 else "")
        body = kwargs.get("body", args[2] if len(args) > 2 else "")
        out.append((kind, title, body))
    return out


def _next_start(home) -> list[tuple[str, str, str]]:
    """What the start after the stop announces about its triggers' runs."""
    orch = object.__new__(GatewayOrchestrator)
    orch.dashboard_state = MagicMock()
    orch._record_boot_review({}, [], base_dir=home)
    return _notes(orch.dashboard_state)


def _req(method: str, *, body: dict | None = None) -> web.Request:
    app = web.Application()
    app["state"] = types.SimpleNamespace(push_refresh=lambda *k: None, _background_tasks=set())
    req = make_mocked_request(method, "/api/triggers/review", app=app)
    req["user"] = "owner"

    async def _json() -> dict:
        return body or {}

    req.json = _json  # type: ignore[assignment]
    return req


def _review(method: str, *, body: dict | None = None) -> tuple[int, dict]:
    resp = asyncio.run(T.api_trigger_review(_req(method, body=body)))
    return resp.status, json.loads(resp.body.decode())


def _run_now_from_the_card() -> tuple[int, dict]:
    return _review("POST", body={"trigger_id": TID, "kind": "interrupted", "action": "run_now"})


def _run_now(trigger: Trigger) -> tuple[bool, str]:
    return asyncio.run(
        trigger_runs._dispatch_store_action(trigger, {"trigger_id": TID, "manual": True})
    )


# ── the run whose agent a Restart cut off ──


def test_a_run_now_whose_agent_a_restart_cut_off_is_interrupted_not_failed(
    home, restarting, action
):
    """🔴 Red before: the row read `failure` with error "cancelled", and the bell "Morning brief
    failed" over "cancelled"."""
    trigger = _brief(home)
    assert _run_now(trigger) == (True, "ran")
    (launched,) = _rows(home)
    assert launched["status"] == "launched"
    orch, agents = _gateway()
    agents.agents.append(_the_agent_works())

    asyncio.run(orch._shutdown())

    (row,) = _rows(home)
    assert row["status"] == reaper.RESTART_INTERRUPTED_STATUS
    assert row["trigger"] == "manual", "a Run now is recorded as the hand run it was"
    assert row["error"].startswith(
        "Interrupted by a gateway restart: the gateway restarted while this was running."
    )
    assert "run it again from the review on the Triggers page" in row["error"]
    assert _notes(orch.dashboard_state) == [], "the stop calls nothing a failure"
    cards = review.pending(base_dir=home)
    assert [(c.trigger_id, c.kind) for c in cards] == [(TID, review.INTERRUPTED)]
    assert cards[0].reason == row["error"]


def test_its_trigger_reads_the_interrupted_run_as_its_last(home, restarting, action):
    """The trigger's last run is the interrupted one, at its own time, with its own reason; a run
    by hand leaves the trigger's health alone. 🔴 Red before: its last error was "cancelled"."""
    trigger = _brief(home)
    _run_now(trigger)
    orch, agents = _gateway()
    agents.agents.append(_the_agent_works())

    asyncio.run(orch._shutdown())

    (row,) = _rows(home)
    live = _live(home)
    assert live.last_run_id == row["run_id"]
    assert live.last_error_summary == row["error"]
    assert live.last_error_summary.startswith("Interrupted by a gateway restart: ")
    assert _last_run_ts(live) == pytest.approx(row["finished_at"], abs=1.0)
    assert live.health_status == TriggerHealth.OK.value


def test_a_scheduled_fire_whose_agent_a_restart_cut_off_reads_degraded(home, restarting, action):
    """A fire, unlike a run by hand, marks its trigger: its last run did not finish."""
    from personalclaw.triggers.run_record import record_run

    trigger = _brief(home)
    launch = ActionResult(success=True, outcome="launched", work_id=f"subagent:{AGENT}")
    asyncio.run(record_run(trigger, started_at=time.time(), result=launch))
    orch, agents = _gateway()
    agents.agents.append(_the_agent_works())

    asyncio.run(orch._shutdown())

    (row,) = _rows(home)
    assert row["status"] == reaper.RESTART_INTERRUPTED_STATUS
    assert row["trigger"] != "manual"
    assert _live(home).health_status == TriggerHealth.DEGRADED.value


def test_a_plain_stop_says_it_stopped(home, monkeypatch, action):
    """A stop that is not a restart must not be called one: the gateway may never come back."""
    monkeypatch.setattr(restart_request, "pending", lambda: None)
    trigger = _brief(home)
    _run_now(trigger)
    orch, agents = _gateway()
    agents.agents.append(_the_agent_works())

    asyncio.run(orch._shutdown())

    (row,) = _rows(home)
    assert row["error"].startswith("Interrupted when the gateway stopped: ")
    assert "restart" not in row["error"]


def test_an_agent_that_ended_before_the_stop_keeps_its_own_ending(home, restarting, action):
    """CONTROL: only the agents the stop cuts off are closed by it. One that already finished says
    how it went, as it always did."""
    trigger = _brief(home)
    _run_now(trigger)
    orch, agents = _gateway()
    done = _the_agent_works()
    done.done = True
    done.result = "Two invoices; one is overdue."
    agents.agents.append(done)

    asyncio.run(orch._shutdown())

    (row,) = _rows(home)
    assert row["status"] == "launched", "the stop must not close a run whose agent had ended"
    assert review.pending(base_dir=home) == []


# ── the notice the next start sends, and the card's Run now ──


def test_the_next_start_says_once_that_a_restart_interrupted_it(home, restarting, action):
    """🔴 Red before: the review held nothing and the start said nothing."""
    trigger = _brief(home)
    _run_now(trigger)
    orch, agents = _gateway()
    agents.agents.append(_the_agent_works())
    asyncio.run(orch._shutdown())

    ((kind, title, body),) = _next_start(home)

    assert kind == notification_kinds.RUN_REVIEW
    assert title == "Runs interrupted by a restart"
    assert body == (
        "1 run was interrupted by the restart and is not run again on its own. "
        "Review it on the Triggers page and choose what to run now."
    )
    assert _next_start(home) == [], "announced once, not at every start"
    assert [c.trigger_id for c in review.pending(base_dir=home)] == [TID], "the card still waits"


def test_a_run_cut_off_again_before_you_decided_is_still_one_decision(home):
    """A second stop that cuts the run off again joins the card that already waits, and the
    notice after it asks one decision of you. 🔴 Red before: "1 run was interrupted … Review
    them", counting the card's two interruptions as two things to review."""
    _brief(home)
    reaper.record_stopped_run(TID, started_at=time.time() - 5, restarting=True, base_dir=home)
    _next_start(home)
    reaper.record_stopped_run(TID, started_at=time.time() - 5, restarting=False, base_dir=home)

    ((_kind, _title, body),) = _next_start(home)

    assert body == (
        "1 run was interrupted by the restart and is not run again on its own. "
        "Review it on the Triggers page and choose what to run now."
    )
    ((card),) = review.pending(base_dir=home)
    assert card.count == 2


def test_run_now_from_the_card_runs_it_once(home, restarting, action):
    trigger = _brief(home)
    _run_now(trigger)
    orch, agents = _gateway()
    agents.agents.append(_the_agent_works())
    asyncio.run(orch._shutdown())
    _next_start(home)

    status, listed = _review("GET")
    assert status == 200
    ((card),) = listed["cards"]
    assert (card["trigger_id"], card["kind"], card["name"]) == (TID, "interrupted", NAME)
    before = action.runs

    status, ran = _run_now_from_the_card()
    assert status == 200 and ran["ok"] is True
    assert action.runs == before + 1
    assert _review("GET")[1]["cards"] == []
    assert _run_now_from_the_card()[0] == 404, "a second click runs nothing"
    assert action.runs == before + 1
    newest = _rows(home)[0]
    assert "a restart interrupted it" in newest["summary"]


# ── an action still running when the stop came ──


def test_a_long_action_a_stop_cuts_off_is_interrupted_announced_and_runs_once_from_its_card(
    home, restarting, monkeypatch
):
    """The action itself was running (a long command), not an agent it started: the dispatch's
    cancellation records it, and the rest is the same."""
    long = _Action(blocks=True)
    monkeypatch.setattr("personalclaw.action_providers.get_action_provider", lambda name: long)
    trigger = _brief(home)

    async def _cut_off() -> None:
        run = asyncio.ensure_future(
            trigger_runs._dispatch_store_action(trigger, {"trigger_id": TID, "manual": True})
        )
        await asyncio.wait_for(long.started.wait(), timeout=10)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    asyncio.run(_cut_off())

    (row,) = _rows(home)
    assert row["status"] == reaper.RESTART_INTERRUPTED_STATUS and row["trigger"] == "manual"
    assert _live(home).last_run_id == row["run_id"]
    ((_kind, title, _body),) = _next_start(home)
    assert title == "Runs interrupted by a restart"
    status, ran = _run_now_from_the_card()
    assert status == 200 and ran["ok"] is True
    assert long.runs == 2, "the cut-off run, then the card's one"
    assert _run_now_from_the_card()[0] == 404
    assert long.runs == 2


# ── the passes that close a run whose action never returned ──


def _claim(home, *, holder: str, age: float, image: str = "an-image-a-restart-replaced") -> None:
    claims.write_claim(
        Claim(
            trigger_id=TID,
            holder=holder,
            claimed_at=time.time() - age,
            owner_pid=os.getpid(),
            owner_image=image,
        ),
        base_dir=home,
    )


def test_the_deadline_reap_writes_the_row_it_promises(home):
    """🔴 Red before: the reap marked the trigger degraded and wrote no row, so the run was in no
    history and the trigger's last run stayed the one before it."""
    _brief(home)
    over = reaper.RUN_DEADLINE_SECS + 120
    _claim(home, holder="tick:1", age=over, image=PROCESS_IMAGE)

    with patch("personalclaw.sel.sel"):
        reaper.sweep_once(store=TriggerStore(base_dir=home), base_dir=home)

    (row,) = _rows(home)
    assert row["status"] == reaper.DEADLINE_STATUS and row["trigger"] == "scheduled"
    assert row["error"].startswith("Reaped after ")
    assert row["job_name"] == NAME
    live = _live(home)
    assert live.last_run_id == row["run_id"]
    assert live.last_error_summary == row["error"]
    assert live.health_status == TriggerHealth.DEGRADED.value
    assert history.schedule_run_to_record(row).outcome == Outcome.FAILED.value


def test_a_reaped_run_by_hand_is_recorded_as_one(home):
    _brief(home)
    _claim(
        home,
        holder=claims.hand_run_holder("manual.run", at=time.time()),
        age=reaper.RUN_DEADLINE_SECS + 120,
        image=PROCESS_IMAGE,
    )

    with patch("personalclaw.sel.sel"):
        reaper.sweep_once(store=TriggerStore(base_dir=home), base_dir=home)

    (row,) = _rows(home)
    assert row["trigger"] == "manual"
    assert _live(home).health_status == TriggerHealth.OK.value


def test_the_boot_pass_closes_a_run_now_a_crash_left_as_the_hand_run_it_was(home):
    """🔴 Red before: a Run now's claim left by a crash was closed as a `scheduled` run, and marked
    its trigger degraded."""
    _brief(home)
    _claim(home, holder=claims.hand_run_holder("manual.run", at=time.time()), age=5.0)

    (record,) = reaper.terminalize_orphans_sync(store=TriggerStore(base_dir=home), base_dir=home)

    (row,) = _rows(home)
    assert row["status"] == reaper.RESTART_INTERRUPTED_STATUS
    assert row["trigger"] == "manual"
    assert record["by_hand"] is True
    assert _live(home).health_status == TriggerHealth.OK.value


def test_the_boot_pass_moves_the_triggers_stamps(home):
    """🔴 Red before: the interrupted row moved neither `last_run_id` nor the stamp the trigger's
    last run is dated by, so its panel read the interrupted run at the time of the one before."""
    _brief(home)
    _claim(home, holder="tick:1", age=5.0)

    reaper.terminalize_orphans_sync(store=TriggerStore(base_dir=home), base_dir=home)

    (row,) = _rows(home)
    live = _live(home)
    assert live.last_run_id == row["run_id"]
    assert _last_run_ts(live) == pytest.approx(row["finished_at"], abs=1.0)
    assert live.health_status == TriggerHealth.DEGRADED.value


# ── the outcome it is ──


def test_an_interrupted_run_is_its_own_outcome_not_a_failure(home):
    """The runs feed called it `failed`, beside a reason saying the restart cut it off."""
    row = {"run_id": "r1", "job_id": TID, "status": "interrupted", "error": "Interrupted by a…"}

    assert history.SCHEDULE_STATUS_TO_OUTCOME["interrupted"] == Outcome.INTERRUPTED.value
    assert history.schedule_run_to_record(row).outcome == Outcome.INTERRUPTED.value
    assert Outcome.INTERRUPTED.value not in TRUE_FAILURE_OUTCOMES, "it never counts to autopause"
