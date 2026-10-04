"""An automation whose work keeps failing pauses itself, whatever started that work.

A fire whose action starts an agent (Invoke Agent, Run prompt) or a workflow run is recorded the
moment the work starts, as `launched`: started is not succeeded. When the work ended, its failure
was written onto the row's status and nowhere else. The row kept the fire's own exit, a clean one,
so the failure count that pauses a failing automation (`autopause.consecutive_failures_from`) read
every such run as a success: an automation whose agent failed on every fire never paused, and
without "Collapse repeat failures" it said so on every fire, for good. A workflow run's ending did
not reach its row at all, so its trigger's last run read "launched" for good.

Now the work's ending is the run's ending: its row takes the exit it ended with, and a fire's
ending walks the lifecycle decision a direct failure walks (`autopause.ending_decision`). A failure
counts; a success resets the count; a run a restart cut off, and one its owner stopped, count for
nothing either way. The pause says why: "paused after N failed runs: <reason>".
"""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from typing import Any, cast

import pytest
from test_an_automation_says_it_finished_when_it_has import _notes, _on_done

import personalclaw.action_providers as AP
from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.schedule_history import ScheduleRunStore
from personalclaw.subagent import SubagentInfo, agent_work_id
from personalclaw.triggers import reaper
from personalclaw.triggers.history import schedule_run_to_record
from personalclaw.triggers.models import Outcome, Trigger, TriggerHealth, TriggerState
from personalclaw.triggers.store import TriggerStore

TRIGGER_ID = "clock:morning-brief"
#: The trigger's own `failure_policy.autopause_after`, so a pause takes three failed runs.
BUDGET = 3
REASON = "the model provider answered 503"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    # The two the suite already points at a home of its own (`conftest`): the Triggers page's
    # reads, and the gateway's.
    monkeypatch.setattr("personalclaw.dashboard.handlers.triggers.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.gateway.config_dir", lambda: tmp_path)
    return tmp_path


def _trigger(home, *, provider: str = "invoke-agent") -> Trigger:
    trigger = Trigger(
        id=TRIGGER_ID,
        name="Morning brief",
        kind="clock",
        spec={"kind": "interval", "interval_secs": 3600},
        capabilities={"providers": [provider]},
        workflow={"inline": {"provider": provider, "config": {"task_template": "Summarise."}}},
        failure_policy={"autopause_after": BUDGET},
        delivery="inbox",
    )
    TriggerStore(base_dir=home).upsert(trigger)
    return trigger


def _live(home) -> Trigger:
    row = TriggerStore(base_dir=home).get(TRIGGER_ID)
    assert row is not None
    return row.trigger


def _rows(home) -> list[dict[str, Any]]:
    rows, _total = ScheduleRunStore(home)._list_for_job_sync(TRIGGER_ID, 0, 50)
    return rows


def _cards(orch: GatewayOrchestrator) -> list[dict[str, Any]]:
    """The attention cards the gateway raised: the note that says the automation stopped."""
    cards = []
    for call in orch.dashboard_state.notify.call_args_list:
        meta = call.kwargs.get("meta") or {}
        if meta.get("event") == "automation.needs_attention":
            cards.append({"title": call.kwargs.get("title"), "body": call.kwargs.get("body")})
    return cards


# ── an agent a fire started ───────────────────────────────────────────────────────────────────


class _StartsAnAgent(ActionProvider):
    """An action that starts an agent and returns at once, as Invoke Agent and Run prompt do: the
    run is `launched`, and names the agent it started."""

    started: list[str] = []

    @property
    def name(self) -> str:
        return "invoke-agent"

    @property
    def display_name(self) -> str:
        return "Invoke Agent"

    async def execute(self, action_config, ctx: ActionContext, timeout: int = 30) -> ActionResult:
        agent_id = f"agent{len(type(self).started) + 1}"
        type(self).started.append(agent_id)
        return ActionResult(
            success=True,
            stdout=f"spawned agent {agent_id}",
            outcome="launched",
            work_id=agent_work_id(agent_id),
        )


@pytest.fixture()
def gateway(home, monkeypatch):
    """The gateway's real fire path and its real subagent completion callback."""
    _trigger(home)
    monkeypatch.setattr(_StartsAnAgent, "started", [])
    monkeypatch.setattr(AP, "get_action_provider", lambda name: _StartsAnAgent())
    orch, on_done = _on_done()
    return SimpleNamespace(orch=orch, on_done=on_done, home=home)


async def _fire(gw: SimpleNamespace) -> str:
    """One fire of the trigger; returns the id of the agent it started."""
    trigger = _live(gw.home)
    await gw.orch._fire_store_trigger(trigger, {"trigger_id": TRIGGER_ID})
    # A fire's row is named by the millisecond it was recorded.
    await asyncio.sleep(0.003)
    return _StartsAnAgent.started[-1]


def _ending(agent_id: str, *, error: str = "", cancelled: bool = False) -> SubagentInfo:
    return SubagentInfo(
        id=agent_id,
        task="Summarise.",
        done=True,
        result="" if error else "Two invoices are due.",
        error=error,
        trigger_id=TRIGGER_ID,
        title="Morning brief",
        cancelled=cancelled,
    )


async def _runs(gw: SimpleNamespace, endings: list[str]) -> None:
    """Fire once per ending and end each fire's agent that way: ``fail``, ``ok``, ``stop`` (its
    owner stopped it) or ``restart`` (a restart cut it off)."""
    for how in endings:
        agent_id = await _fire(gw)
        if how == "restart":
            reaper.record_stopped_work(
                TRIGGER_ID,
                work_id=agent_work_id(agent_id),
                restarting=True,
                store=TriggerStore(base_dir=gw.home),
                base_dir=gw.home,
            )
            continue
        error = {"fail": REASON, "stop": "Cancelled by user", "ok": ""}[how]
        await gw.on_done([_ending(agent_id, error=error, cancelled=how == "stop")])


@pytest.mark.asyncio
async def test_an_automation_whose_agent_fails_every_time_pauses_itself(gateway):
    """🔴 Before: three failed agents and the trigger was `active`, `ok`, enabled, firing on."""
    await _runs(gateway, ["fail"] * BUDGET)
    live = _live(gateway.home)
    assert (live.state, live.enabled) == (TriggerState.AUTOPAUSED.value, False)
    assert live.health_status == TriggerHealth.FAILING.value
    assert live.last_error_summary == REASON


@pytest.mark.asyncio
async def test_the_pause_says_why(gateway):
    await _runs(gateway, ["fail"] * BUDGET)
    assert _cards(gateway.orch) == [
        {
            "title": "Morning brief paused itself",
            "body": f"paused after {BUDGET} failed runs: {REASON}",
        }
    ]


@pytest.mark.asyncio
async def test_one_failure_short_of_the_budget_keeps_it_running(gateway):
    await _runs(gateway, ["fail"] * (BUDGET - 1))
    live = _live(gateway.home)
    assert (live.state, live.enabled) == (TriggerState.ACTIVE.value, True)
    assert live.health_status == TriggerHealth.DEGRADED.value
    assert _cards(gateway.orch) == []


@pytest.mark.asyncio
async def test_a_success_starts_the_count_again(gateway):
    await _runs(gateway, ["fail"] * (BUDGET - 1) + ["ok"] + ["fail"] * (BUDGET - 1))
    live = _live(gateway.home)
    assert (live.state, live.enabled) == (TriggerState.ACTIVE.value, True)
    # And the count still adds up after it: one more failure is the third in a row.
    await _runs(gateway, ["fail"])
    assert _live(gateway.home).state == TriggerState.AUTOPAUSED.value


@pytest.mark.asyncio
async def test_a_success_says_the_automation_is_well_again(gateway):
    await _runs(gateway, ["fail"])
    assert _live(gateway.home).health_status == TriggerHealth.DEGRADED.value
    await _runs(gateway, ["ok"])
    assert _live(gateway.home).health_status == TriggerHealth.OK.value


@pytest.mark.asyncio
@pytest.mark.parametrize("cut", ["restart", "stop"])
async def test_a_run_a_restart_or_its_owner_cut_off_counts_for_nothing(gateway, cut):
    """Neither a failure nor a success: two failures, the cut-off run, and the next failure is
    the third in a row."""
    await _runs(gateway, ["fail"] * (BUDGET - 1) + [cut])
    live = _live(gateway.home)
    assert (live.state, live.enabled) == (TriggerState.ACTIVE.value, True)
    await _runs(gateway, ["fail"])
    assert _live(gateway.home).state == TriggerState.AUTOPAUSED.value


@pytest.mark.asyncio
async def test_a_run_its_owner_stopped_reads_as_stopped_and_sends_no_note(gateway):
    """🔴 Before: its row read "failure · Cancelled by user" and the bell "Morning brief failed"."""
    await _runs(gateway, ["stop"])
    (row,) = _rows(gateway.home)
    assert (row["status"], row["error"]) == ("stopped", "")
    assert row["summary"] == "Cancelled by user"
    record = schedule_run_to_record(row, trigger_id=TRIGGER_ID)
    assert record.outcome == Outcome.STOPPED.value
    assert _notes(gateway.orch) == []
    assert not _live(gateway.home).last_failure_at


@pytest.mark.asyncio
async def test_the_last_run_reads_how_the_agent_ended(gateway):
    """The trigger's last run is its newest row, and that row says how its agent ended."""
    from personalclaw.dashboard.handlers.triggers import _last_run_status_for

    await _runs(gateway, ["fail"])
    assert _last_run_status_for(TRIGGER_ID) == "failure"
    await _runs(gateway, ["ok"])
    assert _last_run_status_for(TRIGGER_ID) == "success"


@pytest.mark.asyncio
async def test_a_launch_alone_changes_nothing_about_how_it_is_going(gateway):
    """Started is not succeeded: a fire that only launched its agent leaves the health and the
    count to that agent's ending, where it used to read as a success and reset both."""
    await _runs(gateway, ["fail"] * (BUDGET - 1))
    await _fire(gateway)
    assert _live(gateway.home).health_status == TriggerHealth.DEGRADED.value
    agent_id = _StartsAnAgent.started[-1]
    await gateway.on_done([_ending(agent_id, error=REASON)])
    assert _live(gateway.home).state == TriggerState.AUTOPAUSED.value


@pytest.mark.asyncio
async def test_an_agent_that_ended_before_its_row_was_written_still_counts(gateway, monkeypatch):
    """An agent refused at once can end before its fire records the run; the row is then written
    as it ended, and that ending is the fire's."""
    await _runs(gateway, ["fail"] * (BUDGET - 1))

    class _EndsAtOnce(_StartsAnAgent):
        async def execute(self, action_config, ctx, timeout=30):
            result = await super().execute(action_config, ctx, timeout)
            await gateway.on_done([_ending(type(self).started[-1], error=REASON)])
            return result

    monkeypatch.setattr(AP, "get_action_provider", lambda name: _EndsAtOnce())
    await _fire(gateway)
    assert _rows(gateway.home)[0]["status"] == "failure"
    assert _live(gateway.home).state == TriggerState.AUTOPAUSED.value


@pytest.mark.asyncio
async def test_a_run_by_hand_whose_agent_fails_never_pauses_it(home, monkeypatch):
    """Testing a broken automation by hand must neither pause it nor clear a real streak."""
    from personalclaw.triggers import run_source
    from personalclaw.triggers.run_record import record_run

    _trigger(home)
    monkeypatch.setattr(_StartsAnAgent, "started", [])
    orch, on_done = _on_done()
    for _ in range(BUDGET + 1):
        trigger = _live(home)
        result = await _StartsAnAgent().execute({}, cast(Any, None))
        await record_run(trigger, started_at=0.0, result=result, source=run_source.YOU)
        await asyncio.sleep(0.003)
        await on_done([_ending(_StartsAnAgent.started[-1], error=REASON)])
    live = _live(home)
    assert (live.state, live.enabled) == (TriggerState.ACTIVE.value, True)
    assert {row["status"] for row in _rows(home)} == {"failure"}
    assert {row["source"] for row in _rows(home)} == {"you"}


@pytest.mark.asyncio
async def test_an_automation_an_app_serves_pauses_itself_where_it_lives(home, monkeypatch):
    """A trigger an app serves keeps its state in that app's store: its agent's endings are taken
    there (`routing.routed`), where they found no row and moved nothing, and the pause lands
    there too, with nothing written into this home's own `triggers.json`."""
    from test_triggers_write_back import FileProviderStore

    from personalclaw.triggers import registry as TREG
    from personalclaw.triggers import routing as ROUTE

    team = FileProviderStore(home / "shared" / "automations.json")
    TREG.register_trigger_store(team.name, team)
    try:
        team.seed(
            Trigger(
                id=TRIGGER_ID,
                name="Morning brief",
                kind="clock",
                spec={"kind": "interval", "interval_secs": 3600},
                capabilities={"providers": ["invoke-agent"]},
                workflow={"inline": {"provider": "invoke-agent", "config": {}}},
                failure_policy={"autopause_after": BUDGET},
            )
        )
        monkeypatch.setattr(_StartsAnAgent, "started", [])
        monkeypatch.setattr(AP, "get_action_provider", lambda name: _StartsAnAgent())
        orch, on_done = _on_done()
        for _ in range(BUDGET):
            served = team.get(TRIGGER_ID)
            assert served is not None
            await orch._fire_store_trigger(served.trigger, {"trigger_id": TRIGGER_ID})
            await asyncio.sleep(0.003)
            await on_done([_ending(_StartsAnAgent.started[-1], error=REASON)])
        served = team.get(TRIGGER_ID)
        assert served is not None
        assert (served.trigger.state, served.trigger.enabled) == (
            TriggerState.AUTOPAUSED.value,
            False,
        )
        assert served.trigger.last_error_summary == REASON
        assert TriggerStore(base_dir=home).get(TRIGGER_ID) is None
    finally:
        TREG.unregister_trigger_store(team.name)
        ROUTE.clear_quarantine()


# ── switched back on ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_switching_a_paused_automation_back_on_makes_it_fire_again(gateway):
    """🔴 Before: the switch went on and the trigger stayed `autopaused`, so it never fired again
    while the chat said "Resumed" and the page "Stopped by the system after repeated failures"."""
    from personalclaw.triggers import tools as T

    await _runs(gateway, ["fail"] * BUDGET)
    assert _live(gateway.home).state == TriggerState.AUTOPAUSED.value
    result = T.set_paused(TriggerStore(base_dir=gateway.home), trigger_id=TRIGGER_ID, paused=False)
    assert result.ok, result.text
    live = _live(gateway.home)
    assert (live.state, live.enabled, live.fires_automatically) == (
        TriggerState.ACTIVE.value,
        True,
        True,
    )


def test_a_trigger_its_own_command_paused_runs_again_when_switched_back_on(home):
    """Whatever paused it: the switch is every surface's Resume (the page, the chat, the CLI)."""
    from personalclaw.triggers import tools as T

    trigger = _trigger(home)
    trigger.state, trigger.enabled = TriggerState.AUTOPAUSED.value, False
    trigger.health_status = TriggerHealth.FAILING.value
    TriggerStore(base_dir=home).upsert(trigger)
    assert T.set_paused(TriggerStore(base_dir=home), trigger_id=TRIGGER_ID, paused=False).ok
    assert _live(home).fires_automatically is True


@pytest.mark.asyncio
async def test_a_second_pause_after_it_was_switched_back_on_is_told_too(gateway):
    """🔴 Before: the gateway kept the first pause's card for good, so a second one said nothing."""
    from personalclaw.triggers import tools as T

    await _runs(gateway, ["fail"] * BUDGET)
    T.set_paused(TriggerStore(base_dir=gateway.home), trigger_id=TRIGGER_ID, paused=False)
    await _runs(gateway, ["fail"])
    assert _live(gateway.home).state == TriggerState.AUTOPAUSED.value
    assert [card["title"] for card in _cards(gateway.orch)] == ["Morning brief paused itself"] * 2


def test_a_quarantined_automation_is_not_switched_back_on(home):
    """Re-running what matched an injection pattern takes re-authoring it, not a switch."""
    from personalclaw.triggers import tools as T

    trigger = _trigger(home)
    trigger.state, trigger.enabled = TriggerState.QUARANTINED.value, False
    TriggerStore(base_dir=home).upsert(trigger)
    result = T.set_paused(TriggerStore(base_dir=home), trigger_id=TRIGGER_ID, paused=False)
    assert not result.ok and "quarantined" in result.text
    assert _live(home).enabled is False


# ── a workflow run a fire started ─────────────────────────────────────────────────────────────

WORKFLOW = "triage-inbox"


def _spec(how: str) -> dict[str, Any]:
    """A one-step workflow: a transform that ends well (``ok``) or whose output breaks its contract
    (``fail``), or a wait that lasts until someone stops it (``stop``)."""
    if how == "stop":
        return {
            "name": WORKFLOW,
            "root": {"kind": "wait", "id": "only", "config": {"duration_secs": 3600}},
        }
    config: dict[str, Any] = {"expr": "done"}
    if how == "fail":
        config["output_contract"] = {"must_be_json": True}
    return {"name": WORKFLOW, "root": {"kind": "transform", "id": "only", "config": config}}


@pytest.fixture()
def workflows(home, monkeypatch):
    """The real run-workflow action, a registered workflow and a real supervisor, wired into the
    gateway's own trigger callbacks."""
    from personalclaw.workflows import defs as defs_mod
    from personalclaw.workflows.controller import EngineServices
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    _trigger(home, provider="run-workflow")
    orch, _on = _on_done()
    holder: dict[str, Any] = {"spec": _spec("fail"), "version": 0}

    class _Defs(defs_mod.WorkflowDefProvider):
        @property
        def name(self) -> str:
            return "autopause-stub"

        async def list_defs(self, *, limit: int = 200, offset: int = 0):
            return [holder["spec"]], 1

        async def get_def(self, name: str):
            return holder["spec"] if name == WORKFLOW else None

    defs_mod.register_provider(_Defs())
    supervisor = WorkflowWatchdog(
        state=None,
        services=EngineServices(
            report_to_trigger=orch._report_to_its_trigger,
            on_attention=orch._surface_attention_card,
        ),
    )
    monkeypatch.setattr(
        "personalclaw.action_providers.services.get_action_services",
        lambda: SimpleNamespace(workflows=supervisor),
    )
    trigger = _live(home)
    trigger.workflow = {"inline": {"provider": "run-workflow", "config": {"workflow": WORKFLOW}}}
    TriggerStore(base_dir=home).upsert(trigger)
    try:
        yield SimpleNamespace(orch=orch, home=home, supervisor=supervisor, holder=holder)
    finally:
        defs_mod.unregister_provider("autopause-stub")


async def _workflow_runs(wf: SimpleNamespace, endings: list[str]) -> None:
    """Fire once per ending, and let the run it started end: ``fail``, ``ok``, or ``stop`` (its
    owner cancels it, as the run page's Cancel does). The owner saves each change to the workflow
    in its editor, so the automation runs it (`workflows.automation_version`)."""
    from personalclaw.workflows import service, versions

    for how in endings:
        wf.holder["version"] += 1
        wf.holder["spec"] = {**_spec(how), "version": wf.holder["version"]}
        versions.record_version(WORKFLOW, wf.holder["spec"], saved_by=versions.OWNER)
        await wf.orch._fire_store_trigger(_live(wf.home), {"trigger_id": TRIGGER_ID})
        run_id = _rows(wf.home)[0]["work_id"].removeprefix("workflow:")
        controller = wf.supervisor._controllers.get(run_id)
        if how == "stop":
            assert service.cancel_run(run_id, supervisor=wf.supervisor)["ok"]
        if controller is not None:
            await asyncio.wait_for(controller._terminal.wait(), timeout=10)
        await asyncio.sleep(0.003)


@pytest.mark.asyncio
async def test_an_automation_whose_workflow_run_fails_every_time_pauses_itself(workflows):
    await _workflow_runs(workflows, ["fail"] * BUDGET)
    live = _live(workflows.home)
    assert (live.state, live.enabled) == (TriggerState.AUTOPAUSED.value, False)
    (card,) = _cards(workflows.orch)
    assert card["body"].startswith(f"paused after {BUDGET} failed runs: ")
    assert {row["status"] for row in _rows(workflows.home)} == {"failure"}


@pytest.mark.asyncio
async def test_a_workflow_run_says_how_it_ended_on_its_triggers_row(workflows):
    """🔴 Before: the row read "launched" for good, so the trigger's last run did too."""
    from personalclaw.dashboard.handlers.triggers import _last_run_status_for

    await _workflow_runs(workflows, ["ok"])
    assert _last_run_status_for(TRIGGER_ID) == "success"
    assert _live(workflows.home).last_success_at


@pytest.mark.asyncio
async def test_a_workflow_run_its_owner_stopped_counts_for_nothing(workflows):
    await _workflow_runs(workflows, ["fail"] * (BUDGET - 1) + ["stop"])
    assert _rows(workflows.home)[0]["status"] == "stopped"
    assert _live(workflows.home).state == TriggerState.ACTIVE.value
    await _workflow_runs(workflows, ["fail"])
    assert _live(workflows.home).state == TriggerState.AUTOPAUSED.value


@pytest.mark.asyncio
async def test_a_queued_workflow_run_says_how_it_ended_too(workflows, monkeypatch):
    """A start queued behind a run in flight is recorded `queued`, and settles when it ends."""
    from personalclaw.workflows import store
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    workflows.holder["spec"] = {**_spec("fail"), "on_overlap": "queue"}
    prior = store.create(WorkflowRun(id="", workflow_name=WORKFLOW, status=RunStatus.RUNNING))
    store.write_spec(prior.id, copy.deepcopy(workflows.holder["spec"]))
    await workflows.orch._fire_store_trigger(_live(workflows.home), {"trigger_id": TRIGGER_ID})
    (row,) = _rows(workflows.home)
    assert row["status"] == "queued" and row["work_id"].startswith("workflow:")
    # The prior ends, the drain starts the queued run, and the queued run fails.
    prior.status = RunStatus.COMPLETE
    store.save(prior)
    from personalclaw.workflows import overlap

    await overlap.drain(WORKFLOW, workflows.supervisor)
    queued = workflows.supervisor._controllers.get(row["work_id"].removeprefix("workflow:"))
    assert queued is not None
    await asyncio.wait_for(queued._terminal.wait(), timeout=10)
    assert _rows(workflows.home)[0]["status"] == "failure"
