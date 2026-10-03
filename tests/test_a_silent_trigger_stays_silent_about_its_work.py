"""A silent trigger stays silent about the work it starts, however that work ends.

A trigger whose results route is ``none`` (the Triggers page's Silent) says nothing about a run that
went well. An action that only starts its work (Invoke Agent, Run prompt, Run workflow) says nothing
when it fires: the work reports on the trigger's route when it ends. For an agent, a route that
sends nothing read as a route that had not reported, so the agent's own completion note went out in
its place, on every run. Every app's scheduled job is such a trigger, since its route is always
``none``: an app whose job runs every ten minutes put a note in the bell every ten minutes.

Now a ``none`` route has spoken for a success by saying nothing, however the trigger's work ends:
its agent, alone or in a batch, its workflow run, or an action done at once, on a scheduled fire
and on Run now alike. Each run is still in the trigger's history. A failure takes the failure route
("If it fails"), the Inbox unless its owner chose otherwise, in one note; and a trigger whose
results go to the dashboard still says so.

Driven through the gateway's own paths: the app's install reconciled into the trigger store, the
real Invoke Agent action on a fire and on Run now, and the gateway's subagent completion callback.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from test_an_apps_agent_is_held_to_its_tier import _Recorder
from test_an_automation_says_it_finished_when_it_has import _notes, _on_done

import personalclaw.action_providers as AP
from personalclaw import notification_kinds
from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.schedule_history import ScheduleRunStore
from personalclaw.subagent import SubagentInfo
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore
from personalclaw.workflows.models import OriginKind, RunOrigin, RunStatus, WorkflowRun

APP = "harbour-watch"
NAMED = "Harbour Watch"
#: The app's scheduled job, as its install registers it (`app_crons.job_id`).
JOB = f"app:{APP}:sweep"
JOB_NAME = f"{NAMED}: sweep"
MESSAGE = "Take one sweep of the harbour log."
#: One of the owner's own automations.
OWN = "clock:morning-brief"
OWN_NAME = "Morning brief"
REPLY = "Swept the log: nothing new."
REASON = "the model provider answered 503"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    from personalclaw.apps import manager

    for target in (
        "personalclaw.config.loader.config_dir",
        "personalclaw.workflows.store.config_dir",
        "personalclaw.dashboard.handlers.triggers.config_dir",
        "personalclaw.gateway.config_dir",
    ):
        monkeypatch.setattr(target, lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


def _install_app(home: Path) -> None:
    """The app as its install leaves it on disk: a scheduled job, at the tools tier."""
    appdir = home / "apps" / APP
    appdir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": NAMED,
        "description": "Sweeps the harbour log for new entries.",
        "permissions": {"cron": True, "agent": "tools"},
        "crons": [{"name": "sweep", "cron_expr": "*/10 * * * *", "message": MESSAGE}],
    }
    (appdir / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (appdir / "installed.json").write_text(
        json.dumps({"name": APP, "version": "1.0.0", "enabled": True}), encoding="utf-8"
    )


def _own_trigger(
    home: Path,
    *,
    delivery: str = "none",
    failure_delivery: str = "inbox",
    provider: str = "invoke-agent",
) -> Trigger:
    """The owner's automation: Silent unless *delivery* says otherwise."""
    trigger = Trigger(
        id=OWN,
        name=OWN_NAME,
        kind="clock",
        spec={"kind": "interval", "interval_secs": 3600},
        capabilities={"providers": [provider]},
        workflow={"inline": {"provider": provider, "config": {"task_template": "Brief me."}}},
        delivery=delivery,
        failure_delivery=failure_delivery,
    )
    TriggerStore(base_dir=home).upsert(trigger)
    return trigger


@pytest.fixture()
def sent(monkeypatch) -> list[str]:
    """Everything sent on a chat channel: a trigger's channel route, or a reply to the owner's
    DM."""
    from personalclaw.triggers import delivery

    out: list[str] = []
    monkeypatch.setattr(
        delivery,
        "_start_channel_delivery",
        lambda _state, note, name, _target: out.append(f"{name}: {note.title}"),
    )

    async def _to_owner(*_args: Any, **kwargs: Any) -> None:
        out.append(f"your DM: {kwargs.get('title', '')}")

    monkeypatch.setattr("personalclaw.channel_delivery.deliver_to_owner", _to_owner)
    return out


@pytest.fixture()
def world(home, sent, monkeypatch):
    """The app installed and its job registered, the owner's own Silent automation, and the
    gateway's fire paths and subagent completion callback, over agents that are recorded."""
    from personalclaw.action_providers import invoke_agent_provider
    from personalclaw.apps.app_crons import reconcile_app_crons

    _install_app(home)
    reconcile_app_crons(TriggerStore(base_dir=home))
    _own_trigger(home)
    agents = _Recorder()
    monkeypatch.setattr(
        invoke_agent_provider, "get_action_services", lambda: SimpleNamespace(subagents=agents)
    )
    orch, on_done = _on_done()
    return SimpleNamespace(home=home, orch=orch, on_done=on_done, agents=agents, sent=sent)


async def _start(world: SimpleNamespace, trigger_id: str, how: str) -> None:
    """Start the trigger's work as its schedule does (``fire``) or as its Run now does."""
    from personalclaw.dashboard.handlers import trigger_runs

    row = TriggerStore(base_dir=world.home).get(trigger_id)
    assert row is not None
    if how == "fire":
        await world.orch._fire_store_trigger(row.trigger, {"trigger_id": trigger_id})
    else:
        ran, said = await trigger_runs._dispatch_store_action(
            row.trigger,
            {"trigger_id": trigger_id, "manual": True},
            state=world.orch.dashboard_state,
        )
        assert ran, said
    # A run's row is named by the millisecond it was recorded.
    await asyncio.sleep(0.003)


def _ended(world: SimpleNamespace, *, error: str = "") -> SubagentInfo:
    """How the agent the last start began ended, as the subagent manager hands it over."""
    started = world.agents.started[-1]
    return SubagentInfo(
        id=f"run-{len(world.agents.started)}",
        task=started["task"],
        done=True,
        result="" if error else REPLY,
        error=error,
        parent_session_key=started.get("parent_session_key", ""),
        trigger_id=started.get("trigger_id", ""),
        title=started.get("title", ""),
        app=started.get("app", ""),
    )


def _runs(home: Path, trigger_id: str) -> list[tuple[str, str]]:
    """The trigger's run history, newest first, as ``(status, what it said)``."""
    rows, _total = ScheduleRunStore(home)._list_for_job_sync(trigger_id, 0, 50)
    return [(row["status"], row["error"] or row["summary"]) for row in rows]


def _filed(home: Path) -> list[str]:
    """The trigger failures waiting in the Inbox, by their first line."""
    from personalclaw.inbox import InboxStore

    store = InboxStore()
    store.load()
    return [item.message.splitlines()[0] for item in store.open_items() if item.refs.get("trigger")]


# ── an app's scheduled job, and the owner's own Silent automation ─────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["fire", "run now"])
@pytest.mark.parametrize(("trigger_id", "named"), [(JOB, JOB_NAME), (OWN, OWN_NAME)])
async def test_a_silent_triggers_agent_that_went_well_posts_no_note(world, trigger_id, named, how):
    """🔴 Before: one note per run, titled by the trigger and holding the agent's reply — the
    agent's own completion note, sent because the trigger's route sent nothing."""
    for _ in range(2):
        await _start(world, trigger_id, how)
        await world.on_done([_ended(world)])
    assert _notes(world.orch) == []
    assert world.sent == []
    assert _filed(world.home) == []
    assert _runs(world.home, trigger_id) == [("success", REPLY)] * 2
    assert {started["title"] for started in world.agents.started} == {named}


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["fire", "run now"])
@pytest.mark.parametrize(("trigger_id", "named"), [(JOB, JOB_NAME), (OWN, OWN_NAME)])
async def test_its_failure_reaches_the_inbox_in_one_note(world, trigger_id, named, how):
    await _start(world, trigger_id, how)
    await world.on_done([_ended(world, error=REASON)])
    assert _notes(world.orch) == [(notification_kinds.CRON_FAILED, f"{named} failed", REASON)]
    assert _filed(world.home) == [f"{named} failed"]
    assert world.sent == []
    assert _runs(world.home, trigger_id) == [("failure", REASON)]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_route", ["none", ""], ids=["don't tell me", "same as results"])
async def test_a_failure_she_asked_not_to_hear_about_posts_nothing(world, failure_route):
    """Her choice, "If it fails: Don't tell me", or "Same as results" on a Silent automation, holds
    for a failure as for a success. 🔴 Before: the agent's own note told her anyway."""
    _own_trigger(world.home, failure_delivery=failure_route)
    await _start(world, OWN, "fire")
    await world.on_done([_ended(world, error=REASON)])
    assert _notes(world.orch) == []
    assert _filed(world.home) == []
    assert _runs(world.home, OWN) == [("failure", REASON)]


@pytest.mark.asyncio
async def test_a_trigger_whose_results_reach_the_dashboard_still_says_so(world):
    _own_trigger(world.home, delivery="inbox")
    await _start(world, OWN, "run now")
    await world.on_done([_ended(world)])
    assert _notes(world.orch) == [(notification_kinds.CRON, f"{OWN_NAME} finished", REPLY)]


@pytest.mark.asyncio
async def test_a_batch_note_names_only_the_runs_no_trigger_spoke_for(world):
    """Her Silent automation's agent and an agent nobody named end together: the note is about
    the second alone. 🔴 Before: "2 agents finished", the automation's run among them."""
    await _start(world, OWN, "fire")
    unnamed = SubagentInfo(
        id="5f3a9c21",
        task="Check the release notes for typos",
        done=True,
        result="Two typos, both fixed.",
    )
    await world.on_done([_ended(world), unnamed])
    assert _notes(world.orch) == [
        (notification_kinds.SUBAGENT, "Check the release notes for typos", "Two typos, both fixed.")
    ]
    assert _runs(world.home, OWN) == [("success", REPLY)]


@pytest.mark.asyncio
async def test_an_apps_job_and_its_silent_agent_run_ending_together_post_nothing(world):
    """An app's own agent run is silent, and its job's agent is spoken for by the job's route:
    ending in one batch, under the app, they post nothing. 🔴 Before: "2 agents finished"."""
    await _start(world, JOB, "fire")
    agent_run = SubagentInfo(
        id="7e8f9a0b",
        task="Summarise the harbour notes.",
        done=True,
        result="Summarised.",
        parent_session_key=f"app:{APP}",
        app=APP,
        silent=True,
    )
    await world.on_done([_ended(world), agent_run])
    assert _notes(world.orch) == []
    assert _runs(world.home, JOB) == [("success", REPLY)]


# ── one rule, however the work ends ───────────────────────────────────────────────────────────


class _StartsARun(ActionProvider):
    """An action that starts a workflow run and returns at once, as Run workflow does."""

    @property
    def name(self) -> str:
        return "starts-a-run"

    @property
    def display_name(self) -> str:
        return "Starts a run"

    async def execute(self, action_config, ctx: ActionContext, timeout: int = 30) -> ActionResult:
        from personalclaw.workflows.models import run_work_id

        return ActionResult(success=True, outcome="launched", work_id=run_work_id("run-7"))


class _DoneAtOnce(ActionProvider):
    """An action whose work is done when it returns, as a command's is."""

    ok = True

    @property
    def name(self) -> str:
        return "done-at-once"

    @property
    def display_name(self) -> str:
        return "Done at once"

    async def execute(self, action_config, ctx: ActionContext, timeout: int = 30) -> ActionResult:
        if type(self).ok:
            return ActionResult(success=True, stdout=REPLY)
        return ActionResult(success=False, error=REASON)


async def _ends(world: SimpleNamespace, monkeypatch, work: str, *, ok: bool) -> None:
    """Start the owner's Silent automation's work on its schedule and let it end, *ok* or not."""
    from personalclaw.workflows import attention, run_finish

    stubs = {"a workflow run": _StartsARun(), "an action done at once": _DoneAtOnce()}
    real = AP.get_action_provider
    if work in stubs:
        stub = stubs[work]
        monkeypatch.setattr(_DoneAtOnce, "ok", ok)
        monkeypatch.setattr(
            AP, "get_action_provider", lambda name: stub if name == stub.name else real(name)
        )
        _own_trigger(world.home, provider=stub.name)
    await _start(world, OWN, "fire")
    if work == "an agent":
        await world.on_done([_ended(world, error="" if ok else REASON)])
    elif work == "a workflow run":
        # The run's terminal write, as the controller makes it (`RunController._finish`).
        run = WorkflowRun(
            id="run-7",
            workflow_name="tidy-the-log",
            error_message="" if ok else REASON,
            origin=RunOrigin(kind=OriginKind.HOOK, trigger_id=OWN),
            extra={"summary": REPLY},
        )
        status = RunStatus.COMPLETE if ok else RunStatus.FAILED
        attention.announce_run_end(world.orch.dashboard_state, run, status)
        services = SimpleNamespace(report_to_trigger=world.orch._report_to_its_trigger)
        run_finish.report_to_its_trigger(services, run, status)


@pytest.mark.asyncio
@pytest.mark.parametrize("work", ["an agent", "a workflow run", "an action done at once"])
async def test_however_its_work_ends_well_a_silent_trigger_says_nothing(world, monkeypatch, work):
    """🔴 Before: the agent alone posted a note; a workflow run and an action done at once already
    said nothing."""
    await _ends(world, monkeypatch, work, ok=True)
    assert _notes(world.orch) == []
    assert _runs(world.home, OWN) == [("success", REPLY)]


@pytest.mark.asyncio
@pytest.mark.parametrize("work", ["an agent", "a workflow run", "an action done at once"])
async def test_however_its_work_fails_a_silent_trigger_says_so_once(world, monkeypatch, work):
    await _ends(world, monkeypatch, work, ok=False)
    assert _notes(world.orch) == [(notification_kinds.CRON_FAILED, f"{OWN_NAME} failed", REASON)]
    assert _filed(world.home) == [f"{OWN_NAME} failed"]
    assert [status for status, _said in _runs(world.home, OWN)] == ["failure"]


# ── what the trigger's report answers ─────────────────────────────────────────────────────────


def test_a_silent_route_has_spoken_for_a_success(home):
    """`report_run` answers whether anyone else may say it: a route of `none` said all it will.
    🔴 Before: False, which is what let the agent's own note go out in its place."""
    from personalclaw.triggers.delivery import report_run

    state = MagicMock()
    assert report_run(state, _own_trigger(home), ok=True, summary=REPLY) is True
    state.notify.assert_not_called()


def test_a_note_that_could_not_go_out_is_left_to_the_caller(home):
    """The other side: a route that tried and failed has not spoken, so the agent's own note is
    still the one that tells her."""
    from personalclaw.triggers.delivery import report_run

    state = MagicMock()
    state.notify.side_effect = RuntimeError("the notification store is unreadable")
    loud = _own_trigger(home, delivery="inbox")
    assert report_run(state, loud, ok=True, summary=REPLY) is False
