"""A one-shot runs at its time, its run is in its history, and only then does it leave the list.

The Triggers page saves a One-shot to retire after its run (`delete_after_run`), and so does a
decision's review. The clock's tick retired such a row BEFORE it handed the fire out: it deleted the
row, and the gateway's runner, which fires the stored row, then found nothing and answered an error.
So at its time the reminder vanished and nothing ran: no history row, no notification, no error.

Now the tick only takes a one-shot's slot, in the same write that grants its fire: the row is
switched off with no next fire. It leaves the list once a run of it has done its work and that run
is in its history. A run that did not (it failed, a gate held it, it waits on you) leaves the row in
the list, switched off, with its record, so it can still be run. And a restart between its time and
its run loses nothing: the row is still there, and the review offers the interrupted run.

Every test drives the gateway's own clock loop (tick, dispatch, runner, recorder) over a real store.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.action_providers.base import ActionResult
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.triggers import service as SVC
from personalclaw.triggers.store import TriggerStore

TITLE = "Pack the soccer bag."
ZONE = "America/Toronto"


class _State:
    """The dashboard, as far as a fire reaches it: the bell."""

    def __init__(self) -> None:
        self.notes: list[dict[str, Any]] = []

    def notify(self, kind, title, body="", *, meta=None, raised_by_app=""):
        self.notes.append({"kind": kind, "title": title, "body": body})

    def push_refresh(self, *keys: str) -> None:
        return None

    def broadcast_ws(self, *_a: Any, **_k: Any) -> None:
        return None


class _Request:
    def __init__(self, body: dict) -> None:
        self._body = body

    async def json(self) -> dict:
        return self._body

    def get(self, key: str, default: object = None) -> object:
        return default


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """One home for the page's store, the gateway's store and the run history alike."""
    for seam in (
        "personalclaw.config.loader.config_dir",
        "personalclaw.dashboard.handlers.triggers.config_dir",
        "personalclaw.gateway.config_dir",
    ):
        monkeypatch.setattr(seam, lambda: tmp_path, raising=False)
    return tmp_path


@pytest.fixture()
def bell(monkeypatch):
    """The Dashboard Notification action rings this, as it rings the dashboard's bell."""
    from personalclaw.action_providers import notify_provider

    state = _State()
    monkeypatch.setattr(
        notify_provider, "get_action_services", lambda: SimpleNamespace(state=state)
    )
    return state


RING = {"provider": "notify", "config": {"title_template": TITLE, "kind": "info"}}


def _soon(secs: int) -> float:
    """A time ``secs`` from now, in whole seconds, as the page sends one (`epochSeconds`)."""
    return float(int(time.time()) + secs)


def _own_action(monkeypatch, name: str, result: ActionResult) -> dict:
    """Register an action that answers ``result``, into a copy of the registry so it is gone after
    the test, and return the action a trigger names to run it."""
    from personalclaw.action_providers import registry
    from personalclaw.action_providers.base import ActionProvider

    class _Answers(ActionProvider):
        @property
        def name(self) -> str:
            return name

        @property
        def display_name(self) -> str:
            return name

        async def execute(self, action_config, ctx, timeout=30):
            return result

    monkeypatch.setattr(registry, "_providers", {**registry._providers})
    registry.register_action_provider(_Answers())
    return {"provider": name, "config": {}}


async def _page_trigger(*, action: dict | None = None, **when: Any) -> str:
    """A trigger made as the Triggers page makes one, ringing the bell unless told otherwise.
    Returns its store id."""
    from personalclaw.dashboard.handlers import triggers as handlers

    body = {
        "name": "Pack the soccer bag",
        "timezone": ZONE,
        **when,
        "action": action or RING,
        "confirm": True,
    }
    resp = await handlers._create_schedule(_State(), body, _Request(body))
    payload = json.loads(resp.body.decode())
    assert resp.status == 200, payload
    return str(payload["trigger"]["raw_id"])


async def _one_shot(at: float) -> str:
    """The page's One-shot: "Run once at date and time"."""
    return await _page_trigger(at=at)


async def _tick_at(monkeypatch, bell: _State, now: float) -> None:
    """One pass of the gateway's clock loop at ``now``: its tick, dispatch, runner and recorder."""
    import personalclaw.triggers.loop as clock_loop

    tick_once = clock_loop.tick_once

    async def one_tick(store, *, runner, sessions=None, base_dir=None, **_kw):
        await tick_once(store, runner=runner, sessions=sessions, base_dir=base_dir, now=now)

    monkeypatch.setattr(clock_loop, "run_forever", one_tick)
    orch = object.__new__(GatewayOrchestrator)
    # No trigger has a session inbox, which is the normal case: the fire runs directly.
    orch.sessions = SimpleNamespace(_sessions={}, enqueue=lambda *_a, **_k: False)
    orch.dashboard_state = bell
    await orch._clock_loop()


def _history(home: Path, tid: str) -> list[dict[str, Any]]:
    """The trigger's run history, oldest first, as its file keeps it."""
    path = home / "cron-history" / f"{tid}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _row(home: Path, tid: str):
    row = TriggerStore(base_dir=home).get(tid)
    return row.trigger if row is not None else None


def _slot(home: Path, tid: str) -> float:
    return SVC.to_epoch(_row(home, tid).next_fire_at)


# ── the one-shot the page makes ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_one_shot_made_on_the_triggers_page_runs_at_its_time(home, bell, monkeypatch):
    """🔴 Before: at its time the row was gone, the bell stayed empty and its history had no row."""
    at = _soon(3600)
    tid = await _one_shot(at)
    await _tick_at(monkeypatch, bell, at + 1)
    assert [n["title"] for n in bell.notes] == [TITLE]
    (run,) = _history(home, tid)
    assert run["status"] == "success", run
    # It did its work and that is in its history, so it leaves the list, and not before.
    assert _row(home, tid) is None


@pytest.mark.asyncio
async def test_a_one_shot_that_runs_late_runs_once_and_says_it_ran_late(home, bell, monkeypatch):
    """The laptop slept through its time. It runs when the clock ticks again, once, and its history
    says it ran late and by how much. 🔴 Before: nothing ran, and nothing was recorded."""
    at = _soon(3600)
    tid = await _one_shot(at)
    await _tick_at(monkeypatch, bell, at + 480)
    assert [n["title"] for n in bell.notes] == [TITLE]
    (run,) = _history(home, tid)
    assert run["status"] == "ran_late", run
    assert run["summary"].startswith("Ran 8 min after its scheduled slot."), run
    await _tick_at(monkeypatch, bell, at + 540)
    assert len(bell.notes) == 1, "a one-shot runs once"


@pytest.mark.asyncio
async def test_a_one_shot_whose_run_failed_stays_in_the_list_with_its_failure(
    home, bell, monkeypatch
):
    """It did not do its work, so it stays, switched off, to be run again; its history says why.
    🔴 Before: the row was deleted first, so nothing ran and nothing recorded the failure."""
    fails = _own_action(
        monkeypatch,
        "reminds-by-phone",
        ActionResult(success=False, error="the notification service is not reachable"),
    )
    at = _soon(3600)
    tid = await _page_trigger(at=at, action=fails)
    await _tick_at(monkeypatch, bell, at + 1)
    (run,) = _history(home, tid)
    assert run["status"] == "failure", run
    assert "the notification service is not reachable" in run["error"]
    kept = _row(home, tid)
    assert kept is not None, "a one-shot that did not do its work stays, to be run again"
    assert kept.enabled is False and kept.next_fire_at == ""


@pytest.mark.asyncio
async def test_a_one_shot_a_gate_holds_at_its_time_stays_in_the_list_with_the_reason(
    home, bell, monkeypatch
):
    """Its gate refused its only fire. The refusal is its record, and the row stays, switched off.
    🔴 Before: the row was deleted, so the one place its record could be read from was gone."""
    at = _soon(3600)
    tid = await _one_shot(at)
    store = TriggerStore(base_dir=home)
    trigger = store.get(tid).trigger
    trigger.gates = {**dict(trigger.gates or {}), "max_fires": 1}
    trigger.run_count = 1  # its one fire already spent
    store.upsert(trigger)
    await _tick_at(monkeypatch, bell, at + 1)
    assert bell.notes == []
    (run,) = _history(home, tid)
    assert run["status"] == "skipped_budget", run
    kept = _row(home, tid)
    assert kept is not None
    assert kept.enabled is False and kept.next_fire_at == ""


@pytest.mark.asyncio
async def test_a_one_shot_kept_after_its_run_is_switched_off_with_its_run(home, bell, monkeypatch):
    """Control: a one-shot that keeps its row (`delete_after_run: False`, what the chat's one-time
    task saves) runs, and its row stays, switched off, with the run in its history."""
    at = _soon(3600)
    tid = await _one_shot(at)
    store = TriggerStore(base_dir=home)
    trigger = store.get(tid).trigger
    trigger.spec = {**trigger.spec, "delete_after_run": False}
    store.upsert(trigger)
    await _tick_at(monkeypatch, bell, at + 1)
    assert [n["title"] for n in bell.notes] == [TITLE]
    assert [r["status"] for r in _history(home, tid)] == ["success"]
    kept = _row(home, tid)
    assert kept is not None and kept.enabled is False and kept.next_fire_at == ""


@pytest.mark.parametrize(
    ("ending", "stays"),
    [("", False), ("the review workflow stopped: its model is not reachable", True)],
    ids=["its-work-finished", "its-work-failed"],
)
@pytest.mark.asyncio
async def test_a_one_shot_that_starts_work_leaves_the_list_when_that_work_has_done_it(
    ending, stays, home, bell, monkeypatch
):
    """A decision's review starts a workflow; an Invoke Agent one-shot starts an agent. The fire
    only launches the work, so the row stays until the work ends and its note has gone out: the
    note is read off the row. Work that failed leaves the row, switched off, with the failure."""
    # An action that starts work and returns, as Run workflow and Invoke Agent do.
    starts = _own_action(
        monkeypatch,
        "starts-review",
        ActionResult(success=True, outcome="launched", work_id="agent:review-1"),
    )
    at = _soon(3600)
    tid = await _page_trigger(at=at, action=starts)
    await _tick_at(monkeypatch, bell, at + 1)
    assert [r["status"] for r in _history(home, tid)] == ["launched"]
    assert _row(home, tid) is not None, "the work it started has not ended yet"

    orch = object.__new__(GatewayOrchestrator)
    orch.dashboard_state = bell
    orch._report_to_its_trigger(tid, error=ending, summary="Reviewed: still going.")
    assert len(bell.notes) == 1, "its note went out before its row went"
    kept = _row(home, tid)
    if stays:
        assert kept is not None and kept.enabled is False
    else:
        assert kept is None


@pytest.mark.parametrize("gone", ["retired-after-its-run", "deleted-by-the-chat"])
@pytest.mark.asyncio
async def test_a_new_trigger_never_takes_the_id_of_one_whose_runs_are_kept(
    gone, home, bell, monkeypatch
):
    """A run's record outlives its trigger: a one-shot retires after its run, and the chat's delete
    keeps the history. A new trigger with the same name gets its own id, and its history is its own.
    🔴 Before: it took the same id, and showed the gone one's run as its own."""
    from personalclaw.triggers import tools

    at = _soon(3600)
    tid = await _one_shot(at)
    store = TriggerStore(base_dir=home)
    if gone == "deleted-by-the-chat":
        trigger = store.get(tid).trigger
        trigger.spec = {**trigger.spec, "delete_after_run": False}
        store.upsert(trigger)
    await _tick_at(monkeypatch, bell, at + 1)
    if gone == "deleted-by-the-chat":
        assert tools.delete(store, trigger_id=tid, confirm=True).ok
    assert _row(home, tid) is None
    assert [r["status"] for r in _history(home, tid)] == ["success"], "its run is kept"

    again = await _one_shot(_soon(7200))
    assert again != tid
    assert _history(home, again) == []


async def _runs_feed() -> list[dict[str, Any]]:
    """The run feed across every trigger, as Home's recent runs read it."""
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import triggers as handlers

    app = web.Application()
    app["state"] = _State()
    resp = await handlers.api_trigger_history_all(
        make_mocked_request("GET", "/api/triggers/history?limit=20", app=app)
    )
    return json.loads(resp.body.decode())["runs"]


@pytest.mark.parametrize("gone", ["retired-after-its-run", "deleted-by-the-chat"])
@pytest.mark.asyncio
async def test_a_run_whose_trigger_has_left_the_list_is_still_named_in_the_runs_feed(
    gone, home, bell, monkeypatch
):
    """The feed named each run from the triggers in the list, so a one-shot that ran and retired,
    or a trigger the chat deleted, read as its id. Its run keeps the name it ran under."""
    from personalclaw.triggers import tools

    at = _soon(3600)
    tid = await _one_shot(at)
    store = TriggerStore(base_dir=home)
    if gone == "deleted-by-the-chat":
        trigger = store.get(tid).trigger
        trigger.spec = {**trigger.spec, "delete_after_run": False}
        store.upsert(trigger)
    await _tick_at(monkeypatch, bell, at + 1)
    if gone == "deleted-by-the-chat":
        assert tools.delete(store, trigger_id=tid, confirm=True).ok
    assert _row(home, tid) is None

    (run,) = [r for r in await _runs_feed() if str(r.get("trigger_id", "")).endswith(tid)]
    assert run["trigger_name"] == "Pack the soccer bag"


@pytest.mark.asyncio
async def test_asking_about_a_run_whose_trigger_has_left_the_list_names_it(home):
    """Investigate on a run puts the run in the agent's context under its trigger's name: a run of
    a trigger no longer in the list read "Job: (deleted)" and its title named the bare id."""
    from personalclaw import investigate
    from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore

    ScheduleRunStore(home).append_sync(
        ScheduleRun(run_id="fire-1", job_id="soccer-bag", job_name="Pack the soccer bag")
    )
    context = await investigate.resolve("schedule_run", "soccer-bag:fire-1", _State())
    assert context is not None and context.title == "Run · Pack the soccer bag"
    assert "Job: Pack the soccer bag (no longer in the list)" in context.snapshot


@pytest.mark.asyncio
async def test_a_renamed_trigger_is_named_in_the_runs_feed_as_it_is_called_now(
    home, bell, monkeypatch
):
    """A trigger still in the list is named as it is now called, not as it was when it ran."""
    at = _soon(3600)
    tid = await _page_trigger(every=3600, start_at=at)
    await _tick_at(monkeypatch, bell, at + 1)
    store = TriggerStore(base_dir=home)
    trigger = store.get(tid).trigger
    trigger.name = "Pack the swim bag"
    store.upsert(trigger)

    runs = [r for r in await _runs_feed() if str(r.get("trigger_id", "")).endswith(tid)]
    assert runs and {r["trigger_name"] for r in runs} == {"Pack the swim bag"}


# ── every one-shot, however it was made ──────────────────────────────────────────────────────


def _made_on_the_page(home: Path) -> str:
    import asyncio

    return asyncio.run(_one_shot(_soon(3600)))


def _made_by_the_chat(home: Path, monkeypatch) -> str:
    """The agent's `set_onetime_task`, allowed by the owner as the Triggers page allows it."""
    import personalclaw.mcp_automation as M
    from personalclaw.triggers import grants

    monkeypatch.setattr("personalclaw.timezones._config_zone_name", lambda: ZONE)
    store = TriggerStore(base_dir=home)
    monkeypatch.setattr(M, "_store", lambda: store)
    M._call_tool_inner(
        "set_onetime_task",
        {"name": "tea", "when": "in 20 minutes", "message": "Remind the owner the tea is ready."},
    )
    (row,) = store.load()
    grants.give(row.trigger)
    store.upsert(row.trigger)
    return row.trigger.id


def _made_by_a_decision(home: Path) -> str:
    import os

    from personalclaw.decisions import horizon_from_days, log_decision, review_trigger_id
    from personalclaw.knowledge.store import KnowledgeStore

    made = log_decision(
        summary="Take the evening class",
        expectation="I will still be going in a month",
        confidence=0.6,
        domain="other",
        review_horizon=horizon_from_days(30),
        store=KnowledgeStore(os.path.join(home, "knowledge.db")),
        trigger_store=TriggerStore(base_dir=home),
    )
    return review_trigger_id(made["id"])


@pytest.mark.parametrize("made", ["page", "chat", "decision"])
def test_every_one_shot_reaches_its_action_at_its_time(made, home, monkeypatch):
    """The page's One-shot, the chat's one-time task and a decision's review are one-shots made
    three ways. At its time each one's stored row reaches the dispatch. 🔴 Before: the page's and
    the decision's, which retire after their run, were deleted first and reached nothing."""
    import asyncio

    tid = {
        "page": lambda: _made_on_the_page(home),
        "chat": lambda: _made_by_the_chat(home, monkeypatch),
        "decision": lambda: _made_by_a_decision(home),
    }[made]()
    at = _slot(home, tid)
    reached: list[str] = []

    async def dispatch(self, trigger, payload, **_kw):
        reached.append(str(trigger.id))

    monkeypatch.setattr(GatewayOrchestrator, "_fire_store_trigger", dispatch)
    asyncio.run(_tick_at(monkeypatch, _State(), at + 1))
    assert reached == [tid]


# ── a restart between its time and its run ───────────────────────────────────────────────────


def _review_client() -> TestClient:
    import personalclaw.dashboard.handlers.triggers as handlers

    app = web.Application()
    app["state"] = MagicMock()
    handlers.register_trigger_routes(app)
    return TestClient(TestServer(app))


@pytest.mark.asyncio
async def test_a_restart_between_its_time_and_its_run_leaves_it_on_the_review(
    home, bell, monkeypatch
):
    """The gateway went away after its tick took the one-shot's slot and before its run. The boot
    pass records the run as interrupted and the review offers it, because its row is still there;
    Run now runs it, once. 🔴 Before: the row was already deleted, so the review dropped the card
    and the reminder was lost."""
    from personalclaw.triggers import reaper, scheduling

    at = _soon(3600)
    tid = await _one_shot(at)
    store = TriggerStore(base_dir=home)
    decided = await SVC.tick(store, now=at + 1, base_dir=home)
    assert [f.trigger.id for f in decided.fires] == [tid]  # granted, and never run
    # The restart: another program image, so the claim the gone one held is an orphan.
    monkeypatch.setattr(scheduling, "PROCESS_IMAGE", "the-gateway-after-the-restart")
    report = SVC.boot(store, now=at + 60)
    interrupted = reaper.terminalize_orphans_sync(store=store, now=at + 60, base_dir=home)
    assert [r["trigger_id"] for r in interrupted] == [tid]
    orch = object.__new__(GatewayOrchestrator)
    orch.dashboard_state = None
    orch._record_boot_review(report, interrupted, base_dir=home)
    assert bell.notes == [], "not run again on its own: it may have done part of its work"

    async with _review_client() as client:
        (card,) = (await (await client.get("/api/triggers/review")).json())["cards"]
        assert card["trigger_id"] == tid and card["kind"] == "interrupted"
        resp = await client.post(
            "/api/triggers/review",
            json={"trigger_id": tid, "kind": "interrupted", "action": "run_now"},
        )
        body = await resp.json()
    assert body["ok"] is True, body
    assert [n["title"] for n in bell.notes] == [TITLE]
    assert [r["status"] for r in _history(home, tid)] == ["interrupted", "ran_late"]
    # Its run has now done its work, so it leaves the list.
    assert _row(home, tid) is None


@pytest.mark.asyncio
async def test_a_one_shot_whose_time_passed_while_the_gateway_was_down_runs_when_it_is_back(
    home, bell, monkeypatch
):
    """Nothing was running at its time. The boot re-arms it just after the restart; it runs then,
    once, and its history says it ran late. 🔴 Before: that fire deleted the row first."""
    at = _soon(3600)
    tid = await _one_shot(at)
    store = TriggerStore(base_dir=home)
    back = at + 7200
    SVC.boot(store, now=back)
    rearmed = _slot(home, tid)
    assert back < rearmed <= back + 300, "re-armed to run just after the restart"
    await _tick_at(monkeypatch, bell, rearmed + 1)
    assert [n["title"] for n in bell.notes] == [TITLE]
    assert [r["status"] for r in _history(home, tid)] == ["ran_late"]
    assert _row(home, tid) is None


@pytest.mark.asyncio
async def test_a_tick_that_dies_before_its_grant_is_written_leaves_the_one_shot_armed(
    home, bell, monkeypatch
):
    """The tick went away while deciding the fire, before the grant reached the disk. Nothing of the
    one-shot has changed on disk, so the next tick fires it. 🔴 Before: its row was deleted before
    the grant, so a tick that died there lost it."""
    from personalclaw.triggers import claims

    at = _soon(3600)
    tid = await _one_shot(at)

    def dies(*_a, **_k):
        raise RuntimeError("the gateway went away here")

    with monkeypatch.context() as scoped:
        scoped.setattr(claims, "write_claim", dies)
        with pytest.raises(RuntimeError):
            await SVC.tick(TriggerStore(base_dir=home), now=at + 1, base_dir=home)
    armed = _row(home, tid)
    assert armed is not None and armed.enabled is True and _slot(home, tid) == at
    await _tick_at(monkeypatch, bell, at + 30)
    assert [n["title"] for n in bell.notes] == [TITLE]
    assert _row(home, tid) is None


# ── a late fire of a schedule that repeats ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_repeating_fire_that_starts_late_is_recorded_late(home, bell, monkeypatch):
    """The tick decided the fire was late and the history kept only that it ran. A fire well after
    its slot is `ran_late`, with how late; the next day's on-time fire is a plain run.
    🔴 Before: both read `success`."""
    tid = await _page_trigger(cron="45 6 * * *")
    slot = _slot(home, tid)
    await _tick_at(monkeypatch, bell, slot + 600)
    (late,) = _history(home, tid)
    assert late["status"] == "ran_late", late
    assert late["summary"].startswith("Ran 10 min after its scheduled slot."), late
    await _tick_at(monkeypatch, bell, _slot(home, tid) + 1)
    assert [r["status"] for r in _history(home, tid)] == ["ran_late", "success"]
    assert _row(home, tid).enabled is True, "a repeating schedule never retires"


# ── a schedule an app serves ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def app_store(tmp_path, monkeypatch):
    """A trigger store an app serves (Shared Automations is one), holding the owner's rows."""
    from test_triggers_write_back import FileProviderStore

    from personalclaw.triggers import ownership
    from personalclaw.triggers import registry as TREG
    from personalclaw.triggers import routing

    monkeypatch.setattr(ownership, "owner_username", lambda: "robin")
    store = FileProviderStore(tmp_path / "shared" / "automations.json")
    TREG.register_trigger_store(store.name, store)
    routing.clear_quarantine()
    yield store
    TREG.unregister_trigger_store(store.name)
    routing.clear_quarantine()


@pytest.mark.asyncio
async def test_a_schedule_an_app_serves_runs_its_action_and_retires_in_the_apps_store(
    home, bell, monkeypatch, app_store
):
    """The owner's one-shot in an app's store. At its time it runs; its run is recorded; and it
    leaves the app's store, which is where it lives. 🔴 Before: the tick moved its schedule in the
    app's store, and the gateway's runner, reading only the local file, found no row and ran
    nothing."""
    from personalclaw.triggers.models import Trigger

    at = _soon(3600)
    app_store.seed(
        Trigger(
            id="shared:pack-the-bag",
            name="Pack the soccer bag",
            kind="clock",
            enabled=True,
            author="robin",
            spec={"kind": "at", "at": at, "delete_after_run": True},
            workflow={"inline": {"provider": "notify", "config": {"title_template": TITLE}}},
            capabilities={"providers": ["notify"]},
            next_fire_at=SVC.to_iso(at),
        )
    )
    await _tick_at(monkeypatch, bell, at + 1)
    assert [n["title"] for n in bell.notes] == [TITLE]
    assert [r["status"] for r in _history(home, "shared:pack-the-bag")] == ["success"]
    assert app_store.load() == []
    assert TriggerStore(base_dir=home).load() == [], "never copied into the local store"


def test_the_name_a_run_keeps_is_masked_as_every_read_of_the_trigger_masks_it(home):
    """The name is written to the run ledger with the credential masking its summary and error
    get on the way in, so a key pasted into a trigger's name is not kept in its history."""
    from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore

    ScheduleRunStore(home).append_sync(
        ScheduleRun(run_id="fire-1", job_id="deploy", job_name="Deploy with AKIAIOSFODNN7EXAMPLE")
    )
    stored = (home / "cron-history" / "deploy.jsonl").read_text(encoding="utf-8")
    assert "Deploy with" in stored and "AKIAIOSFODNN7EXAMPLE" not in stored
