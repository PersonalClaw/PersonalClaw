"""'remind me at 5 pm' makes a one-time task at 5 pm in the owner's zone, and it runs once.

Every tool that makes a timed task — ``set_onetime_task``, ``automation_create`` and
``set_recurring_task`` — sent its ``when`` to one converter, and that converter could only answer
a cron expression: its prompt told the model to answer NONE for a one-off, and NONE became
"Not a recurring schedule". Measured on ``main`` with a model that follows its prompt, "remind me
at 5 pm" was refused outright; with a model that ignores it, it became a cron that fires every
day. A monitor could not arm its own next check either ("in 1 hour").

Now an explicit time is read without a model, in the owner's zone, into a one-time ``at``
trigger; only a phrase that leaves the time to judgement is asked of the model, which answers
the instant; and each tool keeps its promise — a one-time task runs once, a recurring one repeats.

The stand-in model follows whatever prompt it is handed, as a real one does, so on ``main`` it
answers NONE to the shipped prompt's one-off rule and on this branch it answers the instant.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from personalclaw.triggers.store import TriggerStore

#: Far from any machine this runs on, so a reading in the machine's zone cannot pass by accident.
ZONE = "Asia/Kolkata"
TZ = ZoneInfo(ZONE)


class _Model:
    """A model that follows its prompt: the instant for one time, a cron for a cadence."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def __call__(self, prompt: str, **_: object) -> str:
        self.prompts.append(prompt)
        request = prompt.rsplit("Request:", 1)[-1].lower()
        if "every" in request or "weekday" in request:
            return "0 9 * * 1-5"
        if "ONCE" not in prompt:
            return "NONE"  # what the shipped prompt on main told a model to say for a one-off
        tomorrow = datetime.now(TZ).date() + timedelta(days=1)
        return f"ONCE {tomorrow.isoformat()}T09:00"


@pytest.fixture
def model(monkeypatch):
    model = _Model()
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)
    monkeypatch.setattr("personalclaw.timezones._config_zone_name", lambda: ZONE)
    return model


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path, raising=False)
    return TriggerStore(base_dir=tmp_path)


def _call(name: str, args: dict, store: TriggerStore) -> str:
    """The real chat tool, over the test store."""
    import personalclaw.mcp_automation as M

    orig = M._store
    M._store = lambda: store  # type: ignore[assignment]
    try:
        return str(M._call_tool_inner(name, args))
    finally:
        M._store = orig  # type: ignore[assignment]


def _only(store: TriggerStore):
    rows = store.load()
    assert len(rows) == 1, [r.trigger.to_dict() for r in rows]
    return rows[0].trigger


def _next_five_pm() -> datetime:
    now = datetime.now(TZ)
    today = now.replace(hour=17, minute=0, second=0, microsecond=0)
    return today if today > now else today + timedelta(days=1)


def test_remind_me_at_5pm_is_a_one_time_task_at_5pm_in_the_owners_zone(store, model):
    """🔴 Red on main: refused "Not a recurring schedule" — the model said NONE, as told."""
    text = _call(
        "set_onetime_task",
        {"name": "stretch", "when": "at 5pm", "message": "Remind the owner to stretch."},
        store,
    )
    trigger = _only(store)
    assert trigger.spec["kind"] == "at", (trigger.spec, text)
    assert trigger.spec["at"] == _next_five_pm().timestamp()
    assert trigger.spec["timezone"] == ZONE
    assert "expr" not in trigger.spec, "a one-time task is never a cron"
    assert model.prompts == [], "an explicit time needs no model"
    assert "read as one time" in text and "5:00 PM IST" in text, text


def test_automation_create_reads_a_delay_as_one_time(store, model):
    """The same path for the general tool: 'in 20 minutes' is one run, twenty minutes out."""
    before = time.time()
    _call(
        "automation_create",
        {"name": "tea", "when": "in 20 minutes", "message": "Remind the owner the tea is ready."},
        store,
    )
    trigger = _only(store)
    assert trigger.spec["kind"] == "at"
    assert before + 1200 <= trigger.spec["at"] <= time.time() + 1200
    assert model.prompts == []


def test_a_phrase_left_to_judgement_is_asked_of_the_model_with_the_clock(store, model):
    """'tomorrow morning' names no hour, so the model is asked — told the time and the zone — and
    its answer is the instant, not a cadence."""
    _call(
        "set_onetime_task",
        {"name": "follow up", "when": "tomorrow morning", "message": "Follow up on the PR."},
        store,
    )
    (prompt,) = model.prompts
    assert ZONE in prompt and "ONCE" in prompt, prompt
    trigger = _only(store)
    tomorrow_nine = datetime.combine(
        datetime.now(TZ).date() + timedelta(days=1), datetime.min.time(), TZ
    ) + timedelta(hours=9)
    assert trigger.spec == {
        "kind": "at",
        "at": tomorrow_nine.timestamp(),
        "delete_after_run": False,
        "timezone": ZONE,
    }


def test_a_one_time_task_runs_once_at_its_time(store, model):
    """🔴 Red on main (no task was made). Allowed by the owner, it fires at its time — not
    before, and never again — and then stops, freeing its slot."""
    from personalclaw.triggers import grants
    from personalclaw.triggers import service as SVC

    _call("set_onetime_task", {"name": "tea", "when": "in 20 minutes", "message": "Tea."}, store)
    trigger = _only(store)
    grants.give(trigger)  # the owner's Allow on the Triggers page
    store.upsert(trigger)
    at = float(trigger.spec["at"])

    fires = []
    for moment in (at - 60, at + 1, at + 60, at + 3600, at + 86400):
        result = asyncio.run(SVC.tick(store, now=moment))
        fires.append([f.trigger.id for f in result.fires])
    assert fires == [[], [trigger.id], [], [], []], fires
    after = store.get(trigger.id).trigger
    assert after.enabled is False and after.next_fire_at == "", "it stops after its one run"


def test_a_one_time_task_further_out_than_a_week_still_runs(store, model):
    """🔴 Red on main: every agent one-time task expired a week after it was made, so a reminder
    set three weeks out expired two weeks before its time and never ran."""
    _call("set_onetime_task", {"name": "renew", "when": "in 3 weeks", "message": "Renew."}, store)
    trigger = _only(store)
    expires = datetime.fromisoformat(trigger.expires_at).timestamp()
    assert expires > float(trigger.spec["at"]), (trigger.expires_at, trigger.spec)


def test_a_ttl_that_ends_before_the_task_runs_is_refused(store, model):
    text = _call(
        "set_onetime_task",
        {"name": "late", "when": "in 3 hours", "message": "x", "ttl_secs": 3600},
        store,
    )
    assert "would expire without running" in text, text
    assert store.load() == []


def test_a_time_that_has_passed_is_refused_rather_than_moved(store, model):
    text = _call(
        "set_onetime_task", {"name": "old", "when": "2020-01-01 09:00", "message": "x"}, store
    )
    assert "already passed" in text, text
    assert store.load() == []


def test_a_one_time_task_refuses_a_cadence_and_names_the_tool_that_repeats(store, model):
    """🔴 Red on main: 'every day at 9' became a daily cron from a tool that promises one run."""
    text = _call("set_onetime_task", {"name": "x", "when": "every day at 9", "message": "x"}, store)
    assert "set_recurring_task" in text, text
    assert store.load() == []


def test_a_recurring_task_refuses_one_time_and_names_the_tool_that_runs_once(store, model):
    text = _call("set_recurring_task", {"name": "x", "cadence": "at 5pm", "message": "x"}, store)
    assert "set_onetime_task" in text, text
    assert store.load() == []


def test_a_cadence_is_still_a_repeating_schedule(store, model):
    """The floor: a cadence is asked of the model and becomes a cron, as before."""
    _call(
        "set_recurring_task",
        {"name": "standup", "cadence": "every weekday at 9", "message": "Post standup."},
        store,
    )
    assert _only(store).spec == {"kind": "cron", "expr": "0 9 * * 1-5"}


def test_every_tool_that_takes_a_when_is_covered_here():
    """The census behind "every tool": each chat tool whose schema takes a `when` or a `cadence`
    is one this file drives."""
    import personalclaw.mcp_automation as M

    takes_a_time = sorted(
        t["name"]
        for t in M._list_tools()
        if {"when", "cadence"} & set(t["inputSchema"].get("properties", {}))
    )
    assert takes_a_time == ["automation_create", "set_onetime_task", "set_recurring_task"]
    source = open(__file__, encoding="utf-8").read()
    assert all(json.dumps(name) in source for name in takes_a_time)
