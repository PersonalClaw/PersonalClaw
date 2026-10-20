"""A report's schedule is ONE schedule: the report owns it, and its automation mirrors it.

A scheduled report's time lives on the report, and the clock fires it through an automation row on
the Triggers page. That row could be edited there, and the edit moved the row alone: its fire
reached a runner that checked the REPORT's own schedule a second time, found no slot of it had
passed, and skipped — and the history called the skip a success. Switching the report off and on
then wrote the report's old time back over the edit, and Run now on that row ran nothing.

Driven here against the real stores and the Triggers page's own handlers:

* an edit made on either side moves both, and what the other side set survives it;
* the automation's switch is the report's, and deleting the automation unschedules the report;
* the Triggers page is told which automation is a report's schedule;
* an edit the report cannot hold is refused, in words, and changes nothing;
* a fire of the automation runs the report, whenever the automation says it is time;
* a run that does nothing is recorded as such, never as a success.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.action_providers import knowledge_report_provider as krp
from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.knowledge_persist_provider import KnowledgePersistActionProvider
from personalclaw.dashboard.handlers import trigger_runs
from personalclaw.dashboard.handlers import triggers as trigger_handlers
from personalclaw.knowledge import report_schedules as rs
from personalclaw.knowledge import research_reports as rr
from personalclaw.schedule_history import ScheduleRunStore
from personalclaw.triggers import claims
from personalclaw.triggers import tools as T
from personalclaw.triggers.scheduling import Claim
from personalclaw.triggers.store import TriggerStore

MONDAY_8 = "0 8 * * 1"
FRIDAY_0328 = "28 3 * * 5"
TORONTO = "America/Toronto"


class ScriptedModel:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.prompts)

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "One release shipped [1]."


class _State:
    """The dashboard state members an edit and a run touch."""

    def push_refresh(self, *keys: str) -> None:
        return None

    def notify(self, kind: str, title: str, body: str, *, meta: dict | None = None) -> None:
        return None


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(trigger_handlers, "config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def model(monkeypatch):
    scripted = ScriptedModel()
    monkeypatch.setattr(krp, "_one_shot", scripted)
    return scripted


def _store(home) -> TriggerStore:
    return TriggerStore(base_dir=home)


def _report(**over: Any) -> rr.ReportDefinition:
    raw: dict[str, Any] = {
        "name": "Weekly releases",
        "prompt": "What shipped this week?",
        "schedule": {"kind": "cron", "cron_expr": MONDAY_8},
        "tz": TORONTO,
        "source": {"tags": ["releases"], "window_secs": 0},
        "citation_policy": rr.CITE_SOURCE_ONLY,
    }
    raw.update(over)
    return rr.save_report(rr.from_dict(raw))


def _row(home, defn: rr.ReportDefinition):
    row = _store(home).get(rs.trigger_id_for(defn.id))
    assert row is not None, "the report has no automation"
    return row.trigger


def _seed(title: str, *, tags: list[str]) -> None:
    result = asyncio.run(
        KnowledgePersistActionProvider().execute(
            {
                "title": title,
                "content": f"{title}: what the notes say.",
                "kind": "fact",
                "tags": tags,
                "unsourced": True,
            },
            ActionContext(event="seed", payload={}),
        )
    )
    assert result.success, result.error


def _request(method: str, path: str, match_id: str, body: dict | None = None) -> web.Request:
    app = web.Application()
    app["state"] = _State()
    req = make_mocked_request(method, path, match_info={"id": match_id}, app=app)
    # You, signed in: a Run now from the automation's page.
    req["user"] = "owner"

    async def _json() -> dict:
        return dict(body or {})

    req.json = _json  # type: ignore[assignment]
    return req


async def _history(home, trigger_id: str) -> list[dict[str, Any]]:
    rows, _total = await ScheduleRunStore(home).list_for_job(trigger_id, 0, 10)
    return rows


# ── an edit on either side moves both ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_editing_the_time_on_the_triggers_page_moves_the_report(home):
    defn = _report()
    tid = rs.trigger_id_for(defn.id)

    resp = await trigger_handlers.api_trigger_detail(
        _request(
            "PUT",
            f"/api/triggers/schedule:{tid}",
            f"schedule:{tid}",
            {"cron": FRIDAY_0328, "timezone": TORONTO},
        )
    )
    assert resp.status == 200, resp.text

    moved = rr.get_report(defn.id)
    assert moved is not None and moved.schedule.cron_expr == FRIDAY_0328
    assert "Friday" in rs.shown(moved)["words"], "the Reports page still states the old time"

    # The report's own switch, pressed twice (the Reports page's toggle), keeps the new time: it
    # wrote the report's old one back over the edit.
    for on in (False, True):
        moved.enabled = on
        rr.save_report(moved)
    assert _row(home, defn).spec["expr"] == FRIDAY_0328


def test_editing_the_time_on_the_reports_page_moves_the_automation_and_keeps_its_settings(home):
    defn = _report()
    tid = rs.trigger_id_for(defn.id)
    # What the Triggers page set on the automation: run a missed time late, and keep failures quiet.
    done = T.update(
        _store(home), trigger_id=tid, patch={"catch_up": True, "failure_delivery": "none"}
    )
    assert done.ok, done.text

    defn.schedule = rr.from_dict({"schedule": {"kind": "cron", "cron_expr": FRIDAY_0328}}).schedule
    rr.save_report(defn)

    row = _row(home, defn)
    assert row.spec["expr"] == FRIDAY_0328
    assert row.next_fire_at, "the moved automation was left unarmed"
    assert (
        row.catch_up is True and row.failure_delivery == "none"
    ), "saving the report rebuilt its automation and dropped what the Triggers page set"


def test_the_automations_switch_is_the_reports_switch(home):
    defn = _report()
    tid = rs.trigger_id_for(defn.id)

    paused = T.set_paused(_store(home), trigger_id=tid, paused=True)
    assert paused.ok, paused.text
    assert rr.get_report(defn.id).enabled is False, "the report still says it runs"

    resumed = T.set_paused(_store(home), trigger_id=tid, paused=False)
    assert resumed.ok, resumed.text
    assert rr.get_report(defn.id).enabled is True
    assert _row(home, defn).next_fire_at


@pytest.mark.asyncio
async def test_the_control_bridge_switches_the_report_with_its_automation(home):
    """The local control bridge's toggle goes through the one switch, as the page's does."""
    from personalclaw.inbound import bridge

    defn = _report()
    tid = rs.trigger_id_for(defn.id)
    answer = await bridge._toggle_automation(_State(), {"id": tid, "enabled": False})
    assert answer == {"id": tid, "enabled": False}
    assert rr.get_report(defn.id).enabled is False, "the report still says it runs"


def test_deleting_the_automation_leaves_the_report_unscheduled(home):
    defn = _report()
    tid = rs.trigger_id_for(defn.id)

    gone = T.delete(_store(home), trigger_id=tid, confirm=True)
    assert gone.ok, gone.text

    left = rr.get_report(defn.id)
    assert left is not None, "deleting the automation deleted the report"
    assert rs.shown(left)["words"] == "", "the Reports page still states a time nothing fires at"
    rr.save_report(left)
    assert _store(home).get(tid) is None, "saving the report brought the deleted automation back"


@pytest.mark.asyncio
async def test_deleting_it_from_the_triggers_page_does_the_same(home):
    defn = _report()
    tid = rs.trigger_id_for(defn.id)
    resp = await trigger_handlers.api_trigger_detail(
        _request("DELETE", f"/api/triggers/schedule:{tid}", f"schedule:{tid}")
    )
    assert resp.status == 200
    assert rs.shown(rr.get_report(defn.id))["words"] == ""


@pytest.mark.asyncio
async def test_the_triggers_page_says_which_automation_is_a_reports_schedule(home):
    """The row the Triggers page reads names the report it is the schedule of, by the same rule
    the edits follow, so its panel can say that the time is the report's and that deleting it keeps
    the report. An automation that only runs the same action is not a report's schedule."""
    from personalclaw.triggers.models import Trigger

    defn = _report()
    tid = rs.trigger_id_for(defn.id)
    _store(home).upsert(
        Trigger(
            id="clock:runs-the-same-report",
            name="Also runs it",
            kind="clock",
            spec={"kind": "cron", "expr": MONDAY_8, "timezone": TORONTO},
            workflow={"provider": "knowledge-report", "config": {"report_id": defn.id}},
        )
    )

    resp = await trigger_handlers.api_triggers(_request("GET", "/api/triggers?type=schedule", ""))
    rows = {row["raw_id"]: row for row in json.loads(resp.body)["triggers"]}

    assert rows[tid]["report_id"] == defn.id
    assert rows["clock:runs-the-same-report"]["report_id"] is None


@pytest.mark.parametrize(
    "patch,said",
    [
        ({"name": "Something else"}, "Rename the report"),
        (
            {"workflow": {"provider": "notify", "config": {"message": "hi"}}},
            "runs the report",
        ),
        (
            {"spec": {"kind": "sequence", "at": 2_000_000_000.0, "interval_secs": 86400}},
            "a report runs",
        ),
    ],
)
def test_an_edit_the_report_cannot_hold_is_refused_and_changes_nothing(home, patch, said):
    defn = _report()
    tid = rs.trigger_id_for(defn.id)
    before = _row(home, defn).to_dict()

    result = T.update(_store(home), trigger_id=tid, patch=patch)

    assert result.ok is False
    assert said.lower() in result.text.lower(), result.text
    assert _row(home, defn).to_dict() == before
    assert rr.get_report(defn.id).schedule.cron_expr == MONDAY_8


# ── the automation's fire is the report's run ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_fire_at_the_moved_time_runs_the_report(home, model):
    """The clock fires the automation when IT says it is time; nothing re-checks a second
    schedule. The edit is the one made on the Triggers page, as above."""
    await asyncio.to_thread(_seed, "0.28.1 is out", tags=["releases"])
    defn = _report()
    tid = rs.trigger_id_for(defn.id)
    done = T.update(
        _store(home),
        trigger_id=tid,
        patch={"spec": {"kind": "cron", "expr": FRIDAY_0328, "timezone": TORONTO}},
    )
    assert done.ok, done.text

    row = _row(home, defn)
    result = await krp.KnowledgeReportActionProvider().execute(
        row.workflow["config"], ActionContext(event="clock", payload={"trigger_id": tid})
    )

    assert result.success, result.error
    assert model.calls == 1, f"the fire ran nothing: {result.stdout}"


@pytest.mark.asyncio
async def test_run_now_on_the_reports_automation_runs_the_report(home, model):
    await asyncio.to_thread(_seed, "0.28.1 is out", tags=["releases"])
    defn = _report()
    tid = rs.trigger_id_for(defn.id)

    resp = await trigger_runs.api_trigger_run(
        _request("POST", f"/api/triggers/schedule:{tid}/run", f"schedule:{tid}")
    )
    body = json.loads(resp.body)

    assert body["ok"] is True
    assert model.calls == 1, "Run now on the report's automation ran nothing"
    [newest, *_] = await _history(home, tid)
    assert newest["status"] == "success"
    assert newest["summary"] == "Wrote a finding from 1 item in your knowledge tagged releases."


@pytest.mark.asyncio
async def test_a_run_that_found_nothing_new_is_recorded_as_such(home, model):
    defn = _report()
    tid = rs.trigger_id_for(defn.id)

    await trigger_runs.api_trigger_run(
        _request("POST", f"/api/triggers/schedule:{tid}/run", f"schedule:{tid}")
    )

    [newest, *_] = await _history(home, tid)
    assert newest["status"] == "skipped_noop", newest
    assert newest["summary"] == "Found nothing in your knowledge tagged releases to report on yet."
    assert model.calls == 0


@pytest.mark.asyncio
async def test_a_fire_that_did_not_run_is_recorded_as_skipped_never_as_success(home, model):
    """A run of this report already in flight (a Run now from the Reports page): the fire runs
    nothing, and its history row says so and why."""
    await asyncio.to_thread(_seed, "0.28.1 is out", tags=["releases"])
    defn = _report()
    tid = rs.trigger_id_for(defn.id)
    claims.write_claim(
        Claim(
            trigger_id=rr.report_claim_id(defn.id),
            holder="knowledge-report",
            claimed_at=time.time(),
        )
    )
    try:
        await trigger_runs.api_trigger_run(
            _request("POST", f"/api/triggers/schedule:{tid}/run", f"schedule:{tid}")
        )
    finally:
        claims.release_claim(rr.report_claim_id(defn.id))

    [newest, *_] = await _history(home, tid)
    assert newest["status"] == "skipped_noop", newest
    assert newest["summary"] == "Not run: this report was already running."
    assert model.calls == 0
