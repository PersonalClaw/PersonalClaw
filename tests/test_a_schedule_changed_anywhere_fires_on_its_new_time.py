"""A schedule changed anywhere fires on its new time, and a trigger switched on anywhere is armed.

The clock fires a trigger at its stored `next_fire_at` (`service.due_ids`), so an edit that moves
the schedule must move that instant too, or the trigger fires once more on the schedule it just
left. The Triggers page's save did that and the chat did not: `automation_update` saved the new
spec and kept the old next fire, so a reminder moved from 09:00 to 07:30 in chat still went off at
09:00. The same split held for switching a trigger on: the page's switch armed it, while the chat's
`automation_resume` (and an `automation_update` that sets `enabled`) left a trigger that had never
been armed switched on with no next fire, inert until the gateway restarted.

Every writer now goes through the one rule in `tools.update` / `tools.set_paused`
(`arm.next_fire_after_edit`), so these tests drive the chat's own tool entry point and the page's
own handler, and compare the two.
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
from personalclaw.triggers.arm import arm
from personalclaw.triggers.store import TriggerStore

_NOTIFY = {"inline": {"provider": "notify", "config": {"title_template": "Stand up"}}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(H, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(H, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    monkeypatch.setattr(H, "_used_by_index", lambda: {})
    return tmp_path


def _store(home) -> TriggerStore:
    return TriggerStore(base_dir=home)


def _make(home, spec: dict, *, name: str = "Stand up", enabled: bool = True) -> str:
    result = Tools.create(
        _store(home),
        name=name,
        kind="clock",
        spec=spec,
        workflow=_NOTIFY,
        created_by="user",
        enabled=enabled,
        owner_consented=True,
    )
    assert result.ok, result.text
    return str(result.data["trigger"]["id"])


def _chat(tool: str, args: dict) -> str:
    """The chat's own tool entry point, as the agent calls it."""
    return mcp_automation._call_tool_inner(tool, args)


def _trigger(home, trigger_id: str):
    row = _store(home).get(trigger_id)
    assert row is not None
    return row.trigger


def _state():
    from unittest.mock import MagicMock

    st = MagicMock()
    st.crons.list_jobs.return_value = []
    st._hook_store = None
    return st


def _put(trigger_id: str, body: dict) -> web.Response:
    """The Triggers page's save of a schedule."""
    app = web.Application()
    app["state"] = _state()
    req = make_mocked_request(
        "PUT", "/api/triggers/x", match_info={"id": f"schedule:{trigger_id}"}, app=app
    )
    req["user"] = "tester"

    async def _json():
        return body

    req.json = _json  # type: ignore[assignment]
    return asyncio.run(H.api_trigger_detail(req))


def _due_at(home, trigger_id: str, instant: float) -> bool:
    return trigger_id in SVC.due_ids([r.trigger for r in _store(home).load()], now=instant)


def _armed_as_arm_says(trigger) -> bool:
    """Whether the stored next fire is the one `arm` computes for the trigger as it now is. Within a
    second: an interval with no creation grid is anchored on the moment it is armed."""
    return abs(SVC.to_epoch(trigger.next_fire_at) - SVC.to_epoch(arm(trigger))) < 1.0


# ── the chat moves the next fire with the schedule ──


def test_a_cron_moved_in_chat_fires_at_its_new_time_and_not_its_old_one(home):
    trigger_id = _make(home, {"kind": "cron", "expr": "0 9 * * *", "timezone": "UTC"})
    old = SVC.to_epoch(_trigger(home, trigger_id).next_fire_at)
    assert old > 0

    out = _chat(
        "automation_update",
        {
            "id": trigger_id,
            "patch": {"spec": {"kind": "cron", "expr": "30 7 * * *", "timezone": "UTC"}},
        },
    )
    assert out.startswith("Updated"), out

    moved = _trigger(home, trigger_id)
    assert moved.spec["expr"] == "30 7 * * *"
    assert moved.next_fire_at == arm(moved), "the next fire is still the one armed for 09:00"
    new = SVC.to_epoch(moved.next_fire_at)
    assert time.gmtime(new).tm_hour == 7 and time.gmtime(new).tm_min == 30
    # At the old slot the trigger is not due; at the new one it is.
    if old < new:
        assert not _due_at(home, trigger_id, old + 1)
    assert _due_at(home, trigger_id, new + 1)


def test_an_interval_changed_in_chat_fires_on_the_new_interval(home):
    trigger_id = _make(home, {"kind": "interval", "interval_secs": 86400})
    old = SVC.to_epoch(_trigger(home, trigger_id).next_fire_at)

    _chat(
        "automation_update",
        {"id": trigger_id, "patch": {"spec": {"kind": "interval", "interval_secs": 3600}}},
    )

    moved = _trigger(home, trigger_id)
    new = SVC.to_epoch(moved.next_fire_at)
    assert _armed_as_arm_says(moved)
    assert 0 < new - time.time() <= 3600 + 5
    assert new < old, "the next fire is still a day out, on the interval the edit replaced"


def test_a_one_shot_moved_in_chat_fires_at_its_new_time(home):
    first = time.time() + 2 * 3600
    trigger_id = _make(home, {"kind": "at", "at": first})
    assert SVC.to_epoch(_trigger(home, trigger_id).next_fire_at) == pytest.approx(first, abs=1.0)

    later = first + 5 * 3600
    _chat("automation_update", {"id": trigger_id, "patch": {"spec": {"kind": "at", "at": later}}})

    moved = _trigger(home, trigger_id)
    assert SVC.to_epoch(moved.next_fire_at) == pytest.approx(later, abs=1.0)
    assert not _due_at(home, trigger_id, first + 1), "it still fires at the time it was moved from"


def test_a_rename_in_chat_keeps_the_armed_instant(home):
    """Only a change to WHEN moves the next fire: a rename re-phasing an interval would lose the
    part of the hour already waited."""
    trigger_id = _make(home, {"kind": "interval", "interval_secs": 3600})
    armed = _trigger(home, trigger_id).next_fire_at

    _chat("automation_update", {"id": trigger_id, "patch": {"name": "Stretch"}})
    # The same cadence sent back with an empty zone and no skip dates is the same schedule.
    _chat(
        "automation_update",
        {
            "id": trigger_id,
            "patch": {
                "spec": {
                    "kind": "interval",
                    "interval_secs": 3600,
                    "timezone": "",
                    "skip_dates": [],
                }
            },
        },
    )

    assert _trigger(home, trigger_id).next_fire_at == armed


def test_a_schedule_changed_while_switched_off_is_not_armed(home):
    trigger_id = _make(home, {"kind": "cron", "expr": "0 9 * * *"}, enabled=False)

    _chat(
        "automation_update",
        {"id": trigger_id, "patch": {"spec": {"kind": "cron", "expr": "0 10 * * *"}}},
    )

    assert _trigger(home, trigger_id).next_fire_at == ""


# ── switching on arms, wherever it is done ──


def test_a_trigger_switched_on_by_chat_resume_is_armed(home):
    trigger_id = _make(home, {"kind": "cron", "expr": "0 9 * * *"}, enabled=False)
    assert _trigger(home, trigger_id).next_fire_at == ""

    out = _chat("automation_resume", {"id": trigger_id})
    assert out.startswith("Resumed"), out

    on = _trigger(home, trigger_id)
    assert on.enabled is True
    assert on.next_fire_at and on.next_fire_at == arm(on), "switched on and never going to fire"


def test_a_trigger_switched_on_by_a_chat_update_is_armed(home):
    trigger_id = _make(home, {"kind": "interval", "interval_secs": 3600}, enabled=False)

    _chat("automation_update", {"id": trigger_id, "patch": {"enabled": True}})

    on = _trigger(home, trigger_id)
    assert on.enabled is True
    assert on.next_fire_at and _armed_as_arm_says(on)


def test_resume_keeps_a_next_fire_the_trigger_already_has(home):
    """Arming on the switch is for a trigger with no next fire. One that has its instant keeps it:
    re-arming a live schedule is how a fire gets skipped or doubled."""
    trigger_id = _make(home, {"kind": "interval", "interval_secs": 3600})
    armed = _trigger(home, trigger_id).next_fire_at
    _chat("automation_pause", {"id": trigger_id})

    _chat("automation_resume", {"id": trigger_id})

    assert _trigger(home, trigger_id).next_fire_at == armed


# ── one rule: the page and the chat agree ──


@pytest.mark.parametrize(
    ("created", "page_body", "chat_spec"),
    [
        (
            {"kind": "cron", "expr": "0 9 * * *", "timezone": "UTC"},
            {"cron": "15 6 * * *"},
            {"kind": "cron", "expr": "15 6 * * *", "timezone": "UTC"},
        ),
        (
            {"kind": "interval", "interval_secs": 86400},
            {"every": 1800},
            {"kind": "interval", "interval_secs": 1800},
        ),
    ],
)
def test_the_page_and_the_chat_move_the_next_fire_alike(home, created, page_body, chat_spec):
    by_page = _make(home, created, name="By page")
    by_chat = _make(home, created, name="By chat")

    assert _put(by_page, page_body).status == 200
    _chat("automation_update", {"id": by_chat, "patch": {"spec": chat_spec}})

    page, chat = _trigger(home, by_page), _trigger(home, by_chat)
    assert page.spec == chat.spec
    assert _armed_as_arm_says(page)
    assert _armed_as_arm_says(chat)
    # An interval is anchored on its own creation grid, so the two rows land within the second
    # that separates their creations.
    assert abs(SVC.to_epoch(page.next_fire_at) - SVC.to_epoch(chat.next_fire_at)) < 2


def test_the_page_save_still_moves_the_next_fire(home):
    trigger_id = _make(home, {"kind": "cron", "expr": "0 9 * * *", "timezone": "UTC"})

    resp = _put(trigger_id, {"cron": "45 8 * * *"})

    assert resp.status == 200, json.loads(resp.body.decode())
    moved = _trigger(home, trigger_id)
    assert moved.next_fire_at == arm(moved)
    assert time.gmtime(SVC.to_epoch(moved.next_fire_at)).tm_min == 45
