"""A time that passed while an automation was switched off is not a missed run.

Switching an automation off kept its stored next fire, and switching it back on kept that instant
too, so a slot that went by while it was off was overdue at the next tick: run late, unasked, when
the resume came within a few minutes of it, or else put on the missed-run review, whose notice
blames "PersonalClaw was paused or the computer was asleep" for a time the owner skipped on
purpose. Switched back on, an automation whose stored instant passed while it was off is armed
from now, to its next slot, as a restore's hold is (`arm.next_fire_after_edit`). One whose stored
instant is still ahead keeps it, and a one-time automation whose time passed while it was off is
not switched on with nothing to fire: the resume is refused, asking for a new time.

Driven through the chat's own tool entry point and the Triggers page's own switch.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.config.loader as loader
from personalclaw import mcp_automation
from personalclaw.dashboard.handlers import triggers as H
from personalclaw.triggers import service as SVC
from personalclaw.triggers import tools as Tools
from personalclaw.triggers.arm import arm, next_fire_after_edit
from personalclaw.triggers.store import TriggerStore

_NOTIFY = {"inline": {"provider": "notify", "config": {"title_template": "Stand up"}}}
_DAILY = {"kind": "cron", "expr": "0 9 * * *", "timezone": "UTC"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(H, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(H, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    monkeypatch.setattr(H, "_used_by_index", lambda: {})
    return tmp_path


def _store(home) -> TriggerStore:
    return TriggerStore(base_dir=home)


def _make(home, spec: dict) -> str:
    result = Tools.create(
        _store(home),
        name="Stand up",
        kind="clock",
        spec=spec,
        workflow=_NOTIFY,
        created_by="user",
        enabled=True,
        owner_consented=True,
    )
    assert result.ok, result.text
    return str(result.data["trigger"]["id"])


def _chat(tool: str, args: dict) -> str:
    return mcp_automation._call_tool_inner(tool, args)


def _trigger(home, trigger_id: str):
    row = _store(home).get(trigger_id)
    assert row is not None
    return row.trigger


def _slot_passes_while_off(home, trigger_id: str, *, secs_ago: float = 3600.0) -> float:
    """Its stored next fire, now gone by: the clock reached it while the switch was off."""
    store = _store(home)
    trigger = store.get(trigger_id).trigger
    assert trigger.enabled is False
    passed = time.time() - secs_ago
    trigger.next_fire_at = SVC.to_iso(passed)
    if (trigger.spec or {}).get("kind") == "at":
        trigger.spec = {**trigger.spec, "at": passed}
    store.upsert(trigger)
    return passed


def _toggle_on(trigger_id: str) -> web.Response:
    """The Triggers page's switch, sent on."""
    from unittest.mock import MagicMock

    app = web.Application()
    state = MagicMock()
    state.crons.list_jobs.return_value = []
    state._hook_store = None
    app["state"] = state
    req = make_mocked_request(
        "POST", "/api/triggers/x/toggle", match_info={"id": f"schedule:{trigger_id}"}, app=app
    )
    req["user"] = "tester"

    async def _json():
        return {"enabled": True}

    req.json = _json  # type: ignore[assignment]
    return asyncio.run(H.api_trigger_toggle(req))


def test_resumed_in_chat_after_its_slot_passed_it_waits_for_the_next_slot(home):
    trigger_id = _make(home, _DAILY)
    assert _chat("automation_pause", {"id": trigger_id}).startswith("Paused")
    passed = _slot_passes_while_off(home, trigger_id)

    out = _chat("automation_resume", {"id": trigger_id})

    assert out.startswith("Resumed"), out
    on = _trigger(home, trigger_id)
    assert on.enabled is True
    assert SVC.to_epoch(on.next_fire_at) > time.time(), "armed to the slot it skipped"
    assert SVC.to_epoch(on.next_fire_at) != passed
    assert on.next_fire_at == arm(on)
    assert trigger_id not in SVC.due_ids([on], now=time.time()), "overdue the moment it resumed"


def test_switched_on_from_the_page_after_its_slot_passed_it_waits_for_the_next_slot(home):
    trigger_id = _make(home, _DAILY)
    assert _chat("automation_pause", {"id": trigger_id}).startswith("Paused")
    _slot_passes_while_off(home, trigger_id)

    resp = _toggle_on(trigger_id)

    assert resp.status == 200, resp.text
    on = _trigger(home, trigger_id)
    assert on.enabled is True
    assert SVC.to_epoch(on.next_fire_at) > time.time()
    assert trigger_id not in SVC.due_ids([on], now=time.time())


def test_resumed_before_its_slot_it_keeps_the_instant_it_has(home):
    """The control: a stored next fire still ahead is kept, since re-arming a live schedule is how
    a fire gets skipped or doubled."""
    trigger_id = _make(home, _DAILY)
    armed = _trigger(home, trigger_id).next_fire_at
    assert SVC.to_epoch(armed) > time.time()
    _chat("automation_pause", {"id": trigger_id})

    _chat("automation_resume", {"id": trigger_id})

    assert _trigger(home, trigger_id).next_fire_at == armed


def test_the_one_rule_arms_from_now_only_an_off_to_on_switch_whose_instant_passed():
    """`next_fire_after_edit`, the one rule every writer re-arms by."""
    from types import SimpleNamespace

    gone = SVC.to_iso(time.time() - 3600)
    ahead = SVC.to_iso(time.time() + 3600)

    def row(enabled: bool, next_fire_at: str):
        return SimpleNamespace(
            id="t",
            kind="clock",
            spec=dict(_DAILY),
            enabled=enabled,
            next_fire_at=next_fire_at,
            restore_hold="",
            created_at="",
        )

    switched_on = next_fire_after_edit(row(False, gone), row(True, gone))
    assert switched_on and SVC.to_epoch(switched_on) > time.time()
    assert next_fire_after_edit(row(False, ahead), row(True, ahead)) is None, "kept its instant"
    assert next_fire_after_edit(row(True, gone), row(True, gone)) is None, "not a switch-on"


def test_a_one_time_automation_whose_time_passed_while_off_is_not_switched_on(home):
    trigger_id = _make(home, {"kind": "at", "at": time.time() + 7200})
    assert _chat("automation_pause", {"id": trigger_id}).startswith("Paused")
    _slot_passes_while_off(home, trigger_id)

    out = _chat("automation_resume", {"id": trigger_id})

    assert out.startswith("Error"), out
    assert "has passed" in out and "new time" in out, out
    assert _trigger(home, trigger_id).enabled is False, "on, with nothing it will ever fire"


def test_a_passed_one_time_automation_switched_on_by_an_update_is_left_off(home):
    # The chat's automation_update patches `enabled`, a second way to switch a trigger on: it asks
    # the same rule (`arm.switch_on_refusal`), and saves nothing.
    trigger_id = _make(home, {"kind": "at", "at": time.time() + 7200})
    assert _chat("automation_pause", {"id": trigger_id}).startswith("Paused")
    _slot_passes_while_off(home, trigger_id)

    out = _chat(
        "automation_update", {"id": trigger_id, "patch": {"enabled": True, "name": "Renamed"}}
    )

    assert out.startswith("Error"), out
    assert "has passed" in out and "new time" in out, out
    stored = _trigger(home, trigger_id)
    assert stored.enabled is False and stored.name == "Stand up", "nothing of the update is saved"


def test_an_update_that_gives_a_passed_one_time_automation_a_new_time_switches_it_on(home):
    trigger_id = _make(home, {"kind": "at", "at": time.time() + 7200})
    assert _chat("automation_pause", {"id": trigger_id}).startswith("Paused")
    _slot_passes_while_off(home, trigger_id)
    later = time.time() + 3600

    out = _chat(
        "automation_update",
        {"id": trigger_id, "patch": {"enabled": True, "spec": {"kind": "at", "at": later}}},
    )

    assert not out.startswith("Error"), out
    stored = _trigger(home, trigger_id)
    assert stored.enabled is True and abs(SVC.to_epoch(stored.next_fire_at) - later) < 1


# ── a report's automation, switched by the report ──────────────────────────────────────────────


def _once_report(at: float):
    """A report run once, at *at*, saved as the Reports page saves one."""
    from personalclaw.knowledge import research_reports as RR

    return RR.save_report(
        RR.from_dict(
            {
                "name": "Launch notes",
                "prompt": "What did the launch notes say?",
                "schedule": {"kind": "at", "at_ts": at},
                "tz": "UTC",
                "source": {"tags": ["launch"], "window_secs": 0},
                "citation_policy": RR.CITE_SOURCE_ONLY,
            }
        )
    )


def _reports_page_save(report_id: str, body: dict) -> web.Response:
    """The Reports page's save, its switch or any edit: PUT /api/knowledge/reports/{id}."""
    from personalclaw.dashboard.handlers import research_reports as RH

    req = make_mocked_request(
        "PUT",
        f"/api/knowledge/reports/{report_id}",
        match_info={"id": report_id},
        app=web.Application(),
    )
    req["user"] = "tester"

    async def _json():
        return dict(body)

    req.json = _json  # type: ignore[assignment]
    return asyncio.run(RH.api_report_update(req))


def _off_until_its_time_passed(monkeypatch, report_id: str, start: float) -> None:
    """Switched off on the Reports page, then three hours go by: its one time, two hours after
    *start*, passes while it is off."""
    assert _reports_page_save(report_id, {"enabled": False}).status == 200
    monkeypatch.setattr(time, "time", lambda: start + 10800)


def test_a_passed_one_time_report_switched_on_from_the_reports_page_is_left_off(home, monkeypatch):
    # The report's switch is a third way to switch an automation on: saving the report switches
    # the automation that mirrors it. The page asks the same rule, and saves nothing.
    from personalclaw.knowledge import report_schedules as RS
    from personalclaw.knowledge import research_reports as RR

    start = time.time()
    defn = _once_report(start + 7200)
    _off_until_its_time_passed(monkeypatch, defn.id, start)

    resp = _reports_page_save(defn.id, {"enabled": True, "name": "Renamed"})

    assert resp.status == 400, resp.text
    said = json.loads(resp.text)["error"]["message"]
    assert "has passed" in said and "new time" in said, said
    kept = RR.get_report(defn.id)
    assert kept.enabled is False and kept.name == "Launch notes", "nothing of the save is kept"
    trigger = _trigger(home, RS.trigger_id_for(defn.id))
    assert trigger.enabled is False, "its automation is on, with nothing it will ever fire"


def test_a_report_saved_on_after_its_one_time_passed_leaves_its_automation_off(home, monkeypatch):
    # A writer that saves the definition itself, past the page's door: the mirror asks the rule
    # too, and leaves the automation off rather than on with no next fire, for the next tick to
    # arm to its own past time.
    from personalclaw.knowledge import report_schedules as RS
    from personalclaw.knowledge import research_reports as RR

    start = time.time()
    defn = _once_report(start + 7200)
    _off_until_its_time_passed(monkeypatch, defn.id, start)

    saved = RR.get_report(defn.id)
    saved.enabled = True
    RR.save_report(saved)

    trigger = _trigger(home, RS.trigger_id_for(defn.id))
    assert trigger.enabled is False, "its automation is on, with nothing it will ever fire"


def test_a_passed_one_time_report_given_a_new_time_on_the_reports_page_switches_on(
    home, monkeypatch
):
    from personalclaw.knowledge import report_schedules as RS

    start = time.time()
    defn = _once_report(start + 7200)
    _off_until_its_time_passed(monkeypatch, defn.id, start)
    later = start + 14400

    resp = _reports_page_save(
        defn.id, {"enabled": True, "schedule": {"kind": "at", "at_ts": later}}
    )

    assert resp.status == 200, resp.text
    trigger = _trigger(home, RS.trigger_id_for(defn.id))
    assert trigger.enabled is True and abs(SVC.to_epoch(trigger.next_fire_at) - later) < 1


def test_an_edit_of_a_one_time_report_whose_automation_took_its_time_is_saved(home, monkeypatch):
    # Its automation fired (or was refused, or missed), and the clock switched it off; the report
    # still reads on. An edit that switches nothing on is the report's own, and is saved: only a
    # save that switches the report on is refused. The automation stays off, with its time gone.
    from personalclaw.knowledge import report_schedules as RS
    from personalclaw.knowledge import research_reports as RR

    start = time.time()
    defn = _once_report(start + 7200)
    tid = RS.trigger_id_for(defn.id)
    taken = _trigger(home, tid)
    taken.enabled = False  # the clock's switch-off after it took its slot, which the report misses
    _store(home).upsert(taken)
    monkeypatch.setattr(time, "time", lambda: start + 10800)

    resp = _reports_page_save(defn.id, {"prompt": "What did the launch notes say, in short?"})

    assert resp.status == 200, resp.text
    assert RR.get_report(defn.id).prompt == "What did the launch notes say, in short?"
    assert (
        _trigger(home, tid).enabled is False
    ), "its automation is on, with nothing it will ever fire"
