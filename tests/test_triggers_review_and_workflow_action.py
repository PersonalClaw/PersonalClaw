"""Four trigger defects the persona lane found (coverage rows F-27).

Measured on `main` (33d20e10f) before any of this was written:

* **F-27.** A trigger's "Run workflow" action could not name its workflow. The provider was
  re-registered against the v2 engine without the manifest its form reads, so the form rendered
  nothing, the action saved with an empty config, and every fire failed with "run-workflow
  requires a `workflow` name". A fire that did name one skipped the input checks the Run button
  makes, so a missing required input became a binding failure mid-run.
* **F-28.** A cron schedule's missed run was never reported. The boot review walked interval
  triggers only, and a cron has no interval, so "Missed scheduled runs" never appeared for one.
  And the notice's "Review them and choose what to run now" had nothing behind it:
  `missed.resolve_missed`, the review's run-now and dismiss, had no caller.
* **F-34.** A fire a restart interrupted was recorded as `timeout` and simply dropped.
* **F-60.** `workspace/HEARTBEAT.md` ran every 60 seconds with no UI: an automation nobody could
  see, check or turn off.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.service import to_iso
from personalclaw.triggers.store import TriggerStore
from personalclaw.workflows import defs as defs_mod

NOW = 1_700_000_000.0
HOUR = 3600.0
NATIVE = Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "apps" / "native"


# ── harness ──


class _Defs(defs_mod.WorkflowDefProvider):
    """One workflow whose tree reads a required input, so its start can be refused."""

    def __init__(self) -> None:
        self._defs: dict[str, dict] = {
            "brief": {
                "name": "brief",
                "version": 1,
                "inputs": {"topic": {"type": "string", "required": True}},
                "root": {
                    "kind": "infer",
                    "id": "write",
                    "config": {"prompt": "Write three lines about {{inputs.topic}}."},
                },
            }
        }

    @property
    def name(self) -> str:
        return "review-test-defs"

    @property
    def readonly(self) -> bool:
        return True

    async def list_defs(self, *, limit: int = 200, offset: int = 0):
        items = list(self._defs.values())
        return items[offset : offset + limit], len(items)

    async def get_def(self, name: str):
        return self._defs.get(name)


@pytest.fixture
def workflows():
    provider = _Defs()
    defs_mod.register_provider(provider)
    yield provider
    defs_mod.unregister_provider(provider.name)


class _Echo(ActionProvider):
    """An action that records that it ran."""

    ran: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "review-echo"

    @property
    def display_name(self) -> str:
        return "Review echo"

    async def execute(self, action_config, ctx: ActionContext, timeout: int = 30) -> ActionResult:
        type(self).ran.append(dict(ctx.payload or {}))
        return ActionResult(success=True, stdout="echoed")


@pytest.fixture
def echo():
    from personalclaw.action_providers import registry

    _Echo.ran = []
    registry.register_action_provider(_Echo())
    yield _Echo
    registry._providers.pop("review-echo", None)


@pytest.fixture
def home(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader
    import personalclaw.dashboard.handlers.triggers as h

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(h, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    store = TriggerStore(base_dir=tmp_path)
    monkeypatch.setattr(h, "_trigger_store", lambda: store)
    return tmp_path


def _client() -> TestClient:
    import personalclaw.dashboard.handlers.triggers as h

    app = web.Application()
    app["state"] = MagicMock()
    h.register_trigger_routes(app)
    return TestClient(TestServer(app))


def _store(home) -> TriggerStore:
    return TriggerStore(base_dir=home)


def _schedule(provider: str, config: dict[str, Any]) -> dict[str, Any]:
    # `confirm: true` is the owner's yes to the dialog a `run-workflow` action is created behind.
    # An action that could not run is refused before that question is asked, so the refusals below
    # are the same with or without it.
    return {
        "trigger_type": "schedule",
        "name": "Morning brief",
        "every": 3600,
        "action": {"provider": provider, "config": config},
        "confirm": True,
    }


def _clock(tid: str, *, provider: str = "review-echo") -> Trigger:
    return Trigger(
        id=tid,
        name=f"Automation {tid}",
        kind="clock",
        spec={"kind": "cron", "expr": "0 * * * *", "timezone": "UTC"},
        workflow={"inline": {"provider": provider, "config": {}}},
        # Granted, as a created trigger is: Run now refuses an ungranted one (`triggers.grants`).
        capabilities={"providers": [provider]},
    )


def _history(home, tid: str) -> list[dict[str, Any]]:
    path = home / "cron-history" / f"{tid}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── A Run workflow action names its workflow, and is checked where it is saved ──


def test_the_run_workflow_form_asks_for_the_workflow_and_its_inputs():
    """🔴 Red on main: no manifest, so the form's schema was `{}` and it rendered nothing."""
    manifest = json.loads((NATIVE / "run-workflow-action" / "app.json").read_text())
    schema = manifest["provider"]["settingsSchema"]
    assert schema["required"] == ["workflow"]
    assert schema["properties"]["workflow"]["x-meta"]["widget"] == "workflow"
    assert schema["properties"]["inputs"]["x-meta"]["widget"] == "workflow-inputs"


@pytest.mark.asyncio
async def test_a_run_workflow_trigger_with_no_workflow_is_refused_when_saved(home, workflows):
    """🔴 Red on main: it saved, and every fire failed "run-workflow requires a `workflow` name"."""
    async with _client() as client:
        resp = await client.post("/api/triggers", json=_schedule("run-workflow", {}))
        body = await resp.json()
    assert resp.status == 400, body
    assert body["error"]["message"] == "choose the workflow this trigger runs"
    assert _store(home).load() == []


@pytest.mark.asyncio
async def test_a_run_workflow_trigger_naming_no_workflow_that_exists_is_refused(home, workflows):
    async with _client() as client:
        resp = await client.post(
            "/api/triggers", json=_schedule("run-workflow", {"workflow": "nightly-typo"})
        )
        body = await resp.json()
    assert resp.status == 400, body
    assert body["error"]["message"] == "there is no workflow named 'nightly-typo'"


@pytest.mark.asyncio
async def test_a_run_workflow_trigger_missing_a_required_input_is_refused(home, workflows):
    """The question the Run button's start asks, asked when the trigger is saved."""
    async with _client() as client:
        resp = await client.post(
            "/api/triggers", json=_schedule("run-workflow", {"workflow": "brief", "inputs": {}})
        )
        body = await resp.json()
    assert resp.status == 400, body
    assert body["error"]["message"] == "workflow 'brief': missing required input(s): topic"


@pytest.mark.asyncio
async def test_a_run_workflow_trigger_with_its_inputs_saves(home, workflows):
    """The control for the three refusals above: a complete action saves as it always did."""
    async with _client() as client:
        resp = await client.post(
            "/api/triggers",
            json=_schedule("run-workflow", {"workflow": "brief", "inputs": {"topic": "tides"}}),
        )
        body = await resp.json()
    assert resp.status == 200, body
    (row,) = _store(home).load()
    assert row.trigger.workflow["inline"]["config"]["workflow"] == "brief"


@pytest.mark.asyncio
async def test_an_edit_that_leaves_the_workflow_without_its_input_is_refused(home, workflows):
    """🔴 Red on main. The edit is checked against the provider the trigger already runs."""
    async with _client() as client:
        made = await client.post(
            "/api/triggers",
            json=_schedule("run-workflow", {"workflow": "brief", "inputs": {"topic": "tides"}}),
        )
        tid = (await made.json())["trigger"]["id"]
        resp = await client.put(
            f"/api/triggers/{tid}", json={"action": {"config": {"workflow": "brief"}}}
        )
        body = await resp.json()
    assert resp.status == 400, body
    assert "missing required input(s): topic" in body["error"]["message"]


@pytest.mark.asyncio
async def test_a_fire_whose_workflow_now_needs_another_input_says_so(workflows):
    """🔴 Red on main: the fire created the run anyway, with the input missing. A workflow can
    change after its trigger was saved, so the fire asks the same question and names the answer."""
    from personalclaw.action_providers.run_workflow_provider import RunWorkflowActionProvider

    result = await RunWorkflowActionProvider().execute(
        {"workflow": "brief", "inputs": {}}, ActionContext(event="clock", context="t")
    )
    assert result.success is False
    assert result.error == "workflow 'brief': missing required input(s): topic"
    assert result.failure_class == "user", "a retry sends the same inputs"


# ── A cron's missed runs are reviewed, and the review can be decided ──


def _armed_hours_ago(hours: int) -> float:
    return (NOW // HOUR) * HOUR - (hours - 1) * HOUR


def test_a_cron_schedule_that_missed_three_slots_is_reviewed():
    """🔴 Red on main: 0 rows. A cron has no `interval_secs`, so there was no grid to walk."""
    from personalclaw.triggers.missed import review_at_boot

    armed = _armed_hours_ago(3)
    trigger = _clock("clock:hourly")
    trigger.next_fire_at = to_iso(armed)
    review = review_at_boot([trigger.to_dict()], now=NOW)
    assert [r.scheduled_for for r in review.rows] == [armed, armed + HOUR, armed + 2 * HOUR]
    assert review.summaries == [] and review.truncated is False


def test_a_paused_schedule_missed_nothing():
    """It would not have run, so there is nothing to decide about. Red on main for an interval."""
    from personalclaw.triggers.missed import review_at_boot

    trigger = Trigger(
        id="clock:paused",
        name="Paused",
        kind="clock",
        enabled=False,
        spec={"kind": "interval", "interval_secs": HOUR},
    )
    trigger.next_fire_at = to_iso(NOW - 2 * HOUR)
    assert review_at_boot([trigger.to_dict()], now=NOW).rows == []


def _boot_with_misses(home, tid: str) -> None:
    """What the gateway does after its boot passes: keep the review, then notify once."""
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.triggers import service as SVC

    store = _store(home)
    trigger = _clock(tid)
    # Armed two hours before the top of the current hour: that slot and the next two are due.
    trigger.next_fire_at = to_iso((time.time() // HOUR) * HOUR - 2 * HOUR)
    store.save_all([trigger])
    report = SVC.boot(store)
    orch = GatewayOrchestrator.__new__(GatewayOrchestrator)
    orch.dashboard_state = MagicMock()
    orch._record_boot_review(report, [], base_dir=home)
    (sent,) = [c.kwargs for c in orch.dashboard_state.notify.call_args_list]
    assert sent["meta"]["statusUrl"] == "#/triggers"
    assert "Review them on the Triggers page" in sent["body"]


@pytest.mark.asyncio
async def test_the_missed_runs_wait_on_the_triggers_page_after_the_boot(home, echo):
    """🔴 Red on main: the boot re-armed the schedule, which destroyed the evidence, and nothing
    kept the list — the notice pointed at a review that did not exist."""
    _boot_with_misses(home, "clock:hourly")
    async with _client() as client:
        resp = await client.get("/api/triggers/review")
        body = await resp.json()
    assert resp.status == 200, body
    (card,) = body["cards"]
    assert card["trigger_id"] == "clock:hourly" and card["kind"] == "missed"
    assert card["count"] == 3, card
    assert card["name"] == "Automation clock:hourly"
    assert card["open_id"] == "schedule:clock:hourly"


def test_the_notice_reaches_the_dashboard_that_starts_after_the_boot_passes(home, echo):
    """🔴 Found driving a real restart: no "Missed scheduled runs" notice, on main or here. The boot
    passes run in `_init_cron`, before `_init_dashboard` creates the state the notice goes
    through, so `_surface_missed_review` returned without sending it. Held, then sent once."""
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.triggers import service as SVC

    store = _store(home)
    trigger = _clock("clock:hourly")
    trigger.next_fire_at = to_iso((time.time() // HOUR) * HOUR - 2 * HOUR)
    store.save_all([trigger])
    orch = GatewayOrchestrator.__new__(GatewayOrchestrator)
    orch.dashboard_state = None  # what `start` has when `_init_cron` runs the boot passes
    orch._record_boot_review(SVC.boot(store), [], base_dir=home)
    orch.dashboard_state = MagicMock()  # `_init_dashboard`
    orch._surface_held_boot_review()
    orch._surface_held_boot_review()  # and only once
    (sent,) = [c.kwargs for c in orch.dashboard_state.notify.call_args_list]
    assert sent["title"] == "Missed scheduled runs"
    assert sent["body"].startswith("3 scheduled runs were missed across 1 automation")


@pytest.mark.asyncio
async def test_a_schedule_that_catches_up_on_its_own_gets_no_card(home, echo):
    """`catch_up: true` already decided: it fires once, staggered, by itself. A card offering Run
    now beside that fire would run it twice, and the notice must not send you to review it."""
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.triggers import service as SVC

    armed = to_iso((time.time() // HOUR) * HOUR - 2 * HOUR)
    store = _store(home)
    on_its_own, waits = _clock("clock:catches-up"), _clock("clock:waits")
    on_its_own.catch_up = True
    on_its_own.next_fire_at = waits.next_fire_at = armed
    store.save_all([on_its_own, waits])
    report = SVC.boot(store)
    orch = GatewayOrchestrator.__new__(GatewayOrchestrator)
    orch.dashboard_state = MagicMock()
    orch._record_boot_review(report, [], base_dir=home)
    (sent,) = [c.kwargs for c in orch.dashboard_state.notify.call_args_list]
    assert "1 with catch-up enabled will fire once, staggered, on its own." in sent["body"]
    assert "Review the others on the Triggers page" in sent["body"]
    async with _client() as client:
        cards = (await (await client.get("/api/triggers/review")).json())["cards"]
    assert [c["trigger_id"] for c in cards] == ["clock:waits"]


@pytest.mark.asyncio
async def test_run_now_runs_the_action_once_and_records_it_as_late(home, echo):
    """`resolve_missed` finally has its caller: the run is a row that says it was late, and why."""
    _boot_with_misses(home, "clock:hourly")
    async with _client() as client:
        resp = await client.post(
            "/api/triggers/review",
            json={"trigger_id": "clock:hourly", "kind": "missed", "action": "run_now"},
        )
        body = await resp.json()
        left = await (await client.get("/api/triggers/review")).json()
    assert resp.status == 200 and body["ok"] is True, body
    assert body["outcome"] == "ran_late"
    assert len(echo.ran) == 1, "one decision runs the automation once, however many slots it missed"
    (row,) = _history(home, "clock:hourly")
    assert row["status"] == "ran_late" and row["trigger"] == "manual"
    assert row["summary"].startswith("Ran from a missed-fire review card, after its scheduled slot")
    assert left["cards"] == []


@pytest.mark.asyncio
async def test_dismiss_records_the_decision_and_runs_nothing(home, echo):
    _boot_with_misses(home, "clock:hourly")
    async with _client() as client:
        resp = await client.post(
            "/api/triggers/review",
            json={"trigger_id": "clock:hourly", "kind": "missed", "action": "dismiss"},
        )
        body = await resp.json()
    assert resp.status == 200 and body["outcome"] == "skipped_missed", body
    assert echo.ran == []
    (row,) = _history(home, "clock:hourly")
    assert row["status"] == "skipped_missed"
    assert row["error"] == "the user dismissed the missed-fire card"


@pytest.mark.asyncio
async def test_a_decision_nothing_is_waiting_for_is_a_404(home, echo):
    _store(home).save_all([_clock("clock:hourly")])
    async with _client() as client:
        resp = await client.post(
            "/api/triggers/review",
            json={"trigger_id": "clock:hourly", "kind": "missed", "action": "run_now"},
        )
    assert resp.status == 404
    assert echo.ran == []


# ── A run a restart cut off is recorded as interrupted and waits on the review ──


def _orphan_record(tid: str) -> dict[str, Any]:
    return {
        "trigger_id": tid,
        "owner_pid": 4242,
        "elapsed": 12,
        "released": True,
        "reason": "Interrupted by a gateway restart: the process running this (pid 4242) is gone.",
        "recorded": True,
    }


@pytest.mark.asyncio
async def test_a_run_a_restart_interrupted_waits_on_the_review_and_is_not_rerun(home, echo):
    """🔴 Red on main: the run was closed and forgotten; nothing offered to run it again."""
    from personalclaw.gateway import GatewayOrchestrator

    _store(home).save_all([_clock("clock:backup")])
    orch = GatewayOrchestrator.__new__(GatewayOrchestrator)
    orch.dashboard_state = MagicMock()
    orch._record_boot_review({}, [_orphan_record("clock:backup")], base_dir=home)
    (sent,) = [c.kwargs for c in orch.dashboard_state.notify.call_args_list]
    assert "1 run was interrupted by the restart and is not run again on its own" in sent["body"]
    assert echo.ran == [], "not re-run on its own: it may already have done part of its work"
    async with _client() as client:
        (card,) = (await (await client.get("/api/triggers/review")).json())["cards"]
        assert card["kind"] == "interrupted" and card["count"] == 1
        assert card["reason"].startswith("Interrupted by a gateway restart")
        resp = await client.post(
            "/api/triggers/review",
            json={"trigger_id": "clock:backup", "kind": "interrupted", "action": "run_now"},
        )
        body = await resp.json()
    assert body["ok"] is True and len(echo.ran) == 1
    (row,) = _history(home, "clock:backup")
    assert row["summary"].startswith(
        "Ran again from the review card after a restart interrupted it"
    )


# ── The HEARTBEAT.md queue is a trigger you can see and switch off ──


@pytest.fixture
def queue(tmp_path, monkeypatch):
    import personalclaw.heartbeat as hb

    path = tmp_path / "workspace" / "HEARTBEAT.md"
    monkeypatch.setattr(hb, "heartbeat_path", lambda: path)
    yield path
    hb.set_task_runner(None)


@pytest.mark.asyncio
async def test_the_heartbeat_queue_is_listed_with_the_other_automations(home, queue):
    """🔴 Red on main: it ran every minute and appeared nowhere."""
    from personalclaw.action_providers.heartbeat_tasks_provider import (
        reconcile_heartbeat_tasks_trigger,
    )

    reconcile_heartbeat_tasks_trigger(_store(home))
    assert queue.exists(), "the queue file exists for the agent to write"
    async with _client() as client:
        listed = (await (await client.get("/api/triggers?type=schedule")).json())["triggers"]
    (row,) = [t for t in listed if t["id"] == "schedule:system:heartbeat-tasks"]
    assert row["name"] == "Heartbeat tasks"
    assert row["enabled"] is True
    trigger = _store(home).get("system:heartbeat-tasks").trigger
    assert trigger.spec["interval_secs"] == 60
    assert trigger.workflow["inline"]["provider"] == "heartbeat-tasks"


@pytest.mark.asyncio
async def test_switching_the_queue_off_or_slowing_it_survives_a_restart(home, queue):
    """The boot converges what the code owns (the action), never the user's switch or cadence."""
    from personalclaw.action_providers.heartbeat_tasks_provider import (
        reconcile_heartbeat_tasks_trigger,
    )

    reconcile_heartbeat_tasks_trigger(_store(home))
    async with _client() as client:
        resp = await client.post(
            "/api/triggers/schedule:system:heartbeat-tasks/toggle", json={"enabled": False}
        )
        assert resp.status == 200, await resp.text()
    store = _store(home)
    row = store.get("system:heartbeat-tasks").trigger
    row.spec = {"kind": "interval", "interval_secs": 900}
    store.upsert(row)
    reconcile_heartbeat_tasks_trigger(_store(home))  # the next boot
    after = _store(home).get("system:heartbeat-tasks").trigger
    assert after.enabled is False
    assert after.spec["interval_secs"] == 900


def test_a_pass_the_queue_missed_is_not_reviewed_because_the_next_pass_does_its_work(home, queue):
    """Found driving the UI: the first restart put "Heartbeat tasks — Missed 1 scheduled run" on the
    Triggers page. Every pass runs all that is queued, so a missed one loses nothing, and a card
    would follow every restart. A slot of any other action is still reviewed (the control)."""
    from personalclaw.action_providers.heartbeat_tasks_provider import (
        HEARTBEAT_TASKS_TRIGGER_ID,
        reconcile_heartbeat_tasks_trigger,
    )
    from personalclaw.triggers.missed import review_at_boot

    reconcile_heartbeat_tasks_trigger(_store(home))
    queue_trigger = _store(home).get(HEARTBEAT_TASKS_TRIGGER_ID).trigger
    queue_trigger.next_fire_at = to_iso(NOW - 180)
    other = Trigger(
        id="clock:every-minute",
        name="Every minute",
        kind="clock",
        spec={"kind": "interval", "interval_secs": 60},
        workflow={"inline": {"provider": "notify", "config": {}}},
    )
    other.next_fire_at = to_iso(NOW - 180)
    review = review_at_boot([queue_trigger.to_dict(), other.to_dict()], now=NOW)
    assert {r.trigger_id for r in review.rows} == {"clock:every-minute"}


class _Reports(ActionProvider):
    """An action that reports what it did in `ActionResult.outcome`, like the queue and the
    run-workflow action do."""

    outcome = ""
    failure = ""
    delivered: list[tuple[bool, str]] = []

    @property
    def name(self) -> str:
        return "reports"

    @property
    def display_name(self) -> str:
        return "Reports"

    async def execute(self, action_config, ctx: ActionContext, timeout: int = 30) -> ActionResult:
        if type(self).failure:
            return ActionResult(success=False, error=type(self).failure)
        return ActionResult(success=True, stdout="done", outcome=type(self).outcome)


def _fire_reports(tmp_path, monkeypatch) -> list[dict[str, Any]]:
    """Fire one store trigger through the REAL dispatch and return its history rows."""
    import asyncio

    import personalclaw.action_providers as AP
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.schedule_history import ScheduleRunStore

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id="clock:reports",
            name="Reports",
            kind="clock",
            spec={"kind": "interval", "interval_secs": 60},
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    monkeypatch.setattr(AP, "get_action_provider", lambda name: _Reports())
    orch = object.__new__(GatewayOrchestrator)

    def deliver(trigger: Any, *, ok: bool, error: str = "") -> None:
        _Reports.delivered.append((ok, error))

    monkeypatch.setattr(orch, "_deliver_fire_outcome", deliver)
    trigger = store.get("clock:reports").trigger
    asyncio.run(orch._fire_store_trigger(trigger, {"trigger_id": "clock:reports"}))
    runs, _total = asyncio.run(ScheduleRunStore(tmp_path).list_for_job("clock:reports", 0, 5))
    return runs


@pytest.mark.parametrize(
    ("reported", "recorded"),
    [("skip", "skipped_noop"), ("launched", "launched"), ("", "success")],
)
def test_a_fire_records_what_its_action_reported(tmp_path, monkeypatch, reported, recorded):
    """🔴 Red on main for the first two: every successful fire was recorded `success`. So a
    minutely queue pass with nothing to do buried the passes that ran a task, and a fire that only
    launched a workflow read as one whose work had succeeded, where the Run button already said
    `launched`. `skipped_noop` is inert, so it folds out of the default history. The output is the
    row's summary, as on a Run-button row."""
    monkeypatch.setattr(_Reports, "outcome", reported)
    monkeypatch.setattr(_Reports, "failure", "")
    runs = _fire_reports(tmp_path, monkeypatch)
    assert [(r["status"], r["summary"]) for r in runs] == [(recorded, "done")]


def test_a_fire_whose_action_failed_says_why_on_its_row_and_in_its_notice(tmp_path, monkeypatch):
    """🔴 Red on main: found driving the review's catch-up fire, whose history row read "error" and
    nothing else, and whose inbox notice read "Catch-up job failed" with an empty body. A provider
    that RETURNED a failure (rather than raising) left both without a reason; only the trigger's
    `last_error_summary` kept it."""
    monkeypatch.setattr(_Reports, "outcome", "")
    monkeypatch.setattr(_Reports, "failure", "the API answered 503")
    monkeypatch.setattr(_Reports, "delivered", [])
    (row,) = _fire_reports(tmp_path, monkeypatch)
    assert row["status"] == "failure"
    assert row["error"] == "the API answered 503"
    assert row["summary"] == "the API answered 503"
    # And the failure notice the inbox gets says why, as it does for an action that raised.
    assert _Reports.delivered == [(False, "the API answered 503")]


@pytest.mark.asyncio
async def test_a_pass_over_an_empty_queue_reports_it_had_nothing_to_do(queue):
    import personalclaw.heartbeat as hb
    from personalclaw.action_providers.heartbeat_tasks_provider import (
        HeartbeatTasksActionProvider,
    )

    async def run(task: str, deliver: str) -> str:
        raise AssertionError("an empty queue runs no task")

    hb.set_task_runner(run)
    hb.ensure_heartbeat_file()
    result = await HeartbeatTasksActionProvider().execute({}, ActionContext(event="clock"))
    assert (result.success, result.outcome) == (True, "skip"), result
    assert result.stdout == "no tasks in HEARTBEAT.md"


@pytest.mark.asyncio
async def test_the_queue_trigger_runs_the_tasks_and_keeps_the_unfinished_ones(queue):
    """The heartbeat's retention rule, now run by the trigger rather than the loop."""
    import personalclaw.heartbeat as hb
    from personalclaw.action_providers.heartbeat_tasks_provider import (
        HeartbeatTasksActionProvider,
    )

    seen: list[str] = []

    async def run(task: str, deliver: str) -> str:
        seen.append(task)
        return "still waiting HEARTBEAT_KEEP" if "deploy" in task else "done"

    hb.set_task_runner(run)
    hb.ensure_heartbeat_file()
    queue.write_text(queue.read_text() + "- Watch the deploy\n- Say hello\n")
    result = await HeartbeatTasksActionProvider().execute({}, ActionContext(event="clock"))
    assert result.success is True, result
    assert result.stdout == "2 tasks ran: 1 done, 1 kept for the next pass"
    assert sorted(seen) == ["Say hello", "Watch the deploy"]
    assert "Watch the deploy" in queue.read_text() and "Say hello" not in queue.read_text()


@pytest.mark.asyncio
async def test_the_heartbeat_loop_no_longer_reads_the_queue(queue):
    """🔴 Red on main: the loop ran the queue itself, which is what made it invisible."""
    import personalclaw.heartbeat as hb

    ran: list[str] = []

    async def run(task: str, deliver: str) -> str:
        ran.append(task)
        return "done"

    hb.set_task_runner(run)
    hb.ensure_heartbeat_file()
    queue.write_text(queue.read_text() + "- Say hello\n")
    await hb.HeartbeatService()._beat()
    assert ran == []
    assert "Say hello" in queue.read_text()


@pytest.mark.asyncio
async def test_running_it_again_gives_a_command_the_time_its_scheduled_fire_gets(home):
    """🔴 Red on main, found driving the review: Run now on an interrupted 90-second `bash` job
    failed "Timed out after 30s". A hand-run passed no timeout, so the providers' 30s default
    applied where a scheduled fire of the same command gets 300s."""
    import personalclaw.action_providers as AP
    import personalclaw.dashboard.handlers.triggers as h

    seen: list[int] = []

    class _Command(ActionProvider):
        @property
        def name(self) -> str:
            return "bash"

        @property
        def display_name(self) -> str:
            return "Bash"

        async def execute(self, action_config, ctx: ActionContext, timeout: int = 30):
            seen.append(timeout)
            return ActionResult(success=True, stdout="done")

    trigger = Trigger(
        id="clock:slow",
        name="Slow job",
        kind="clock",
        spec={"kind": "interval", "interval_secs": 900},
        workflow={"inline": {"provider": "bash", "config": {"command": "sleep 90"}}},
        # Granted, as a created trigger is: the dispatch refuses an ungranted one.
        capabilities={"providers": ["bash"]},
    )
    _store(home).save_all([trigger])
    real = AP.get_action_provider
    try:
        AP.get_action_provider = lambda name: _Command()  # type: ignore[assignment]
        ran, _note = await h._dispatch_store_action(trigger, {"trigger_id": "clock:slow"})
    finally:
        AP.get_action_provider = real  # type: ignore[assignment]
    assert ran is True
    assert seen == [300]


@pytest.mark.asyncio
async def test_run_now_waits_while_the_automation_is_running_and_keeps_the_card(home, echo):
    """The Run button answers 409 while a run is in flight; the review's Run now refuses the same
    way, and the decision stays waiting instead of starting a second run beside the first."""
    from personalclaw.triggers import claims
    from personalclaw.triggers.scheduling import Claim

    _boot_with_misses(home, "clock:hourly")
    claims.write_claim(
        Claim(trigger_id="clock:hourly", holder="tick:1", claimed_at=time.time()), base_dir=home
    )
    async with _client() as client:
        resp = await client.post(
            "/api/triggers/review",
            json={"trigger_id": "clock:hourly", "kind": "missed", "action": "run_now"},
        )
        body = await resp.json()
        left = (await (await client.get("/api/triggers/review")).json())["cards"]
    assert body["ok"] is False and "running now" in body["refused"], body
    assert echo.ran == []
    assert [c["trigger_id"] for c in left] == ["clock:hourly"]


def test_status_for_result_answers_only_inside_its_declared_vocabulary():
    """The status rail reads `RESULT_STATUSES` as everything the rule can return; this holds the
    rule to it, for every `ActionResult.outcome` refinement the base class documents."""
    from personalclaw.schedule_history import RESULT_STATUSES, status_for_result

    answers = {
        status_for_result(ActionResult(success=ok, outcome=outcome))
        for ok in (True, False)
        for outcome in ("", "skip", "done", "launched", "queued", "needs_input", "report")
    } | {status_for_result(None)}
    assert answers <= RESULT_STATUSES
    assert {"success", "failure", "launched", "queued", "skipped_noop"} <= answers
