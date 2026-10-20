"""A task's due date tells you it is coming.

`Task.due` was read by exactly one thing — the ready projection's `overdue` rank — and there was no
notification kind for it at all, so a due date set on a task was a promise nothing kept. These pin
the notice's contract:

* WHEN: a date-only due date (what the task form writes) is announced at 09:00 the day before; a
  due date with a time, 24 hours before it;
* ONCE per due date, recorded on disk, so a restart neither repeats it nor loses it — a notice
  missed while the gateway was down goes out on the first sweep after;
* QUIET HOURS hold it until they end (the gate would DROP it — a reminder dropped at 02:00 never
  arrives), while mute and a raised minimum severity mean "not at all" and are not saved up;
* a per-task opt-out, finished tasks, other people's tasks and long-overdue tasks are never
  announced (a first start after an upgrade must not announce every stale task).
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from personalclaw import notification_kinds as nk
from personalclaw.providers import entity_routes
from personalclaw.tasks import due_notices
from personalclaw.tasks.models import Task, TaskStatus, coerce_task_field, task_reset_payload


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(due_notices, "_owner", lambda: "")
    return tmp_path


@pytest.fixture
def settings(monkeypatch):
    """The notification settings the gate reads, as a dict the test edits."""
    current = {
        "mute_all": False,
        "min_severity": "info",
        "quiet_hours_enabled": False,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "08:00",
    }
    monkeypatch.setattr(entity_routes, "load_notifications_settings", lambda: dict(current))
    return current


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, dict]] = []

    def __call__(self, kind: str, title: str, body: str, *, meta: dict | None = None) -> None:
        self.sent.append((kind, title, body, dict(meta or {})))


def _at(*parts: int) -> float:
    return datetime(*parts).timestamp()


def _task(**over) -> Task:
    fields = {"id": "t-1", "title": "Ship the release notes", "due": "2026-10-02"}
    fields.update(over)
    return Task(**fields)


def _sweep(tasks, now: float) -> _Recorder:
    rec = _Recorder()
    due_notices.sweep(tasks, notify=rec, now=now)
    return rec


# ── when ──────────────────────────────────────────────────────────────────────────


def test_a_date_only_due_date_is_announced_once_at_nine_the_day_before(settings):
    task = _task()
    assert _sweep([task], _at(2026, 10, 1, 8, 59)).sent == []

    rec = _sweep([task], _at(2026, 10, 1, 9, 0))
    assert len(rec.sent) == 1
    kind, title, body, meta = rec.sent[0]
    assert kind == nk.TASK_DUE
    assert title == "Due tomorrow: Ship the release notes"
    assert "Friday 2 October" in body
    # The key the notifications page reads for a note's "Open" button (`notificationLink`); a
    # `link` key rendered nowhere. It must be an in-app route, which is all that function follows.
    assert meta["statusUrl"] == "#/tasks?open=t-1"
    assert "link" not in meta

    assert _sweep([task], _at(2026, 10, 1, 9, 5)).sent == [], "once per due date"


def test_a_due_date_with_a_time_is_announced_a_day_before_it(settings):
    task = _task(due="2026-10-02T15:30")
    assert _sweep([task], _at(2026, 10, 1, 15, 29)).sent == []
    rec = _sweep([task], _at(2026, 10, 1, 15, 30))
    assert [t for _k, t, _b, _m in rec.sent] == ["Due tomorrow at 15:30: Ship the release notes"]


def test_a_task_first_seen_on_its_due_day_is_announced_as_due_today(settings):
    rec = _sweep([_task()], _at(2026, 10, 2, 11, 0))
    assert [t for _k, t, _b, _m in rec.sent] == ["Due today: Ship the release notes"]


def test_the_link_encodes_an_id_the_way_the_dashboard_does(settings):
    """Another provider's id can carry `#` or `&`, which would end the route or the query early."""
    rec = _sweep([_task(id="acme/site#12")], _at(2026, 10, 1, 9, 0))
    assert rec.sent[0][3]["statusUrl"] == "#/tasks?open=acme%2Fsite%2312"


# ── once, across a restart ────────────────────────────────────────────────────────


def test_the_record_is_on_disk_so_a_restart_does_not_repeat_it(settings, home):
    _sweep([_task()], _at(2026, 10, 1, 9, 0))
    ledger = json.loads((home / "task_due_notices.json").read_text(encoding="utf-8"))
    assert ledger["notified"] == {"t-1": "2026-10-02"}
    # A new process reads the same file: nothing is held in memory between sweeps.
    assert _sweep([_task()], _at(2026, 10, 1, 12, 0)).sent == []


def test_a_notice_missed_while_the_gateway_was_down_goes_out_on_the_first_sweep_after(settings):
    rec = _sweep([_task()], _at(2026, 10, 1, 18, 40))
    assert len(rec.sent) == 1


def test_a_new_due_date_gets_its_own_notice(settings):
    _sweep([_task()], _at(2026, 10, 1, 9, 0))
    moved = _task(due="2026-10-05")
    assert _sweep([moved], _at(2026, 10, 3, 9, 0)).sent == []
    assert len(_sweep([moved], _at(2026, 10, 4, 9, 0)).sent) == 1


def test_a_sweep_that_does_not_see_the_task_does_not_forget_it_was_announced(settings, home):
    """The ledger used to be trimmed to the tasks the sweep was handed. A task provider that fails
    to list is logged and skipped by `registry.collect_tasks`, so one bad sweep read as "every task
    is gone", erased the record, and the next sweep announced the same due date again."""
    _sweep([_task()], _at(2026, 10, 1, 9, 0))
    assert _sweep([], _at(2026, 10, 1, 9, 5)).sent == []
    ledger = json.loads((home / "task_due_notices.json").read_text(encoding="utf-8"))
    assert ledger["notified"] == {"t-1": "2026-10-02"}
    assert _sweep([_task()], _at(2026, 10, 1, 9, 10)).sent == [], "not announced twice"


def test_a_record_is_dropped_once_its_due_date_can_no_longer_be_announced(settings, home):
    _sweep([_task()], _at(2026, 10, 1, 9, 0))
    # Past the due moment by more than STALE_AFTER: nothing could announce it again, so the
    # record has no job left — whether or not the task still exists.
    _sweep([_task()], _at(2026, 10, 3, 0, 1))
    ledger = json.loads((home / "task_due_notices.json").read_text(encoding="utf-8"))
    assert ledger["notified"] == {}


# ── quiet hours, mute ─────────────────────────────────────────────────────────────


def test_quiet_hours_hold_the_notice_until_they_end(settings, home):
    settings["quiet_hours_enabled"] = True  # 22:00-08:00
    task = _task(due="2026-10-02T07:30")
    assert _sweep([task], _at(2026, 10, 1, 7, 30)).sent == [], "not now"
    assert not (home / "task_due_notices.json").exists(), "and not recorded as sent"
    rec = _sweep([task], _at(2026, 10, 1, 8, 5))
    assert len(rec.sent) == 1


def test_mute_means_not_at_all_so_the_notice_is_not_saved_up(settings, home):
    settings["mute_all"] = True
    rec = _sweep([_task()], _at(2026, 10, 1, 9, 0))
    # Handed to notify(), whose own gate drops it — and recorded, so unmuting next week does not
    # deliver a week-old reminder.
    assert len(rec.sent) == 1
    assert json.loads((home / "task_due_notices.json").read_text())["notified"] == {
        "t-1": "2026-10-02"
    }


# ── who is never notified ─────────────────────────────────────────────────────────


def test_a_task_that_opted_out_is_never_announced(settings):
    assert _sweep([_task(due_reminder=False)], _at(2026, 10, 1, 9, 0)).sent == []


@pytest.mark.parametrize("status", [TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.SKIPPED])
def test_a_finished_task_is_not_announced(settings, status):
    assert _sweep([_task(status=status)], _at(2026, 10, 1, 9, 0)).sent == []


def test_someone_elses_task_is_not_announced(settings, monkeypatch):
    monkeypatch.setattr(due_notices, "_owner", lambda: "ada")
    assert _sweep([_task(assignee="grace")], _at(2026, 10, 1, 9, 0)).sent == []
    assert len(_sweep([_task(assignee="ada")], _at(2026, 10, 1, 9, 0)).sent) == 1


def test_a_long_overdue_task_is_not_announced(settings):
    """A first start after an upgrade sees every task that went stale months ago."""
    assert _sweep([_task(due="2026-09-01")], _at(2026, 10, 1, 9, 0)).sent == []


def test_a_task_with_no_due_date_or_a_malformed_one_is_skipped(settings):
    assert (
        _sweep([_task(due=""), _task(id="t-2", due="next week")], _at(2026, 10, 1, 9, 0)).sent == []
    )


# ── the kind and the field ────────────────────────────────────────────────────────


def test_the_kind_is_registered_with_its_own_wire_string():
    pair = nk.kind_for_legacy(nk.TASK_DUE)
    assert (pair.source, pair.kind) == ("tasks", "due")
    assert pair.label == "Task due"
    assert pair.owner == "personalclaw.tasks.due_notices"
    assert nk.TASK_DUE in nk.WIRE_CONSTANTS


def test_the_opt_out_is_a_task_field_that_round_trips_and_survives_a_reset():
    task = Task.from_dict({"id": "t-1", "title": "x", "due_reminder": False})
    assert task.due_reminder is False
    assert Task.from_dict(task.to_dict()).due_reminder is False
    assert Task.from_dict({"id": "t-2", "title": "y"}).due_reminder is True, "on by default"
    assert coerce_task_field("due_reminder", False) is False
    with pytest.raises(ValueError):
        coerce_task_field("due_reminder", "maybe")
    assert "due_reminder" not in task_reset_payload(task), "a reset keeps the owner's choice"


@pytest.mark.asyncio
async def test_the_native_provider_keeps_the_opt_out_on_create_and_on_update(tmp_path):
    """The form posts it on both paths. `create_task` enumerates what a caller may set, so a new
    field it does not name is dropped on create while `update_task` (which walks the coercer
    table) keeps it — the create/update asymmetry `test_task_field_coercion.py` exists to stop."""
    from unittest.mock import patch

    from personalclaw.tasks.native import NativeTaskProvider

    def stored(task_id: str) -> dict:
        return json.loads((tmp_path / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))

    with patch("personalclaw.tasks.native.config_dir", return_value=tmp_path):
        provider = NativeTaskProvider()
        created = await provider.create_task(title="t", due="2026-10-02", due_reminder=False)
        assert stored(created.id)["due_reminder"] is False
        await provider.update_task(created.id, due_reminder=True)
        assert stored(created.id)["due_reminder"] is True
        with pytest.raises(ValueError):
            await provider.update_task(created.id, due_reminder="sometimes")


# ── the gateway loop ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_gateway_loop_sweeps_through_notify_and_outlives_a_failed_sweep(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from personalclaw.gateway import GatewayOrchestrator

    calls: list = []

    async def fake_run_once(notify, *, now):
        calls.append(notify)
        if len(calls) == 1:
            raise RuntimeError("a task provider blew up")
        raise asyncio.CancelledError  # stop the loop on the second sweep

    monkeypatch.setattr(due_notices, "run_once", fake_run_once)
    monkeypatch.setattr(due_notices, "SWEEP_SECS", 0)
    orch = GatewayOrchestrator.__new__(GatewayOrchestrator)
    orch.dashboard_state = SimpleNamespace(notify=lambda *a, **k: None)

    with pytest.raises(asyncio.CancelledError):
        await orch._task_due_loop()
    assert calls == [orch.dashboard_state.notify] * 2, "the first failure did not end the loop"


@pytest.mark.asyncio
async def test_the_first_sweep_waits_for_the_dashboard_instead_of_skipping_an_interval(monkeypatch):
    """The timers start before the dashboard (`_init_cron` runs before `_init_dashboard`), so the
    loop's first pass found no `notify` and slept a whole `SWEEP_SECS`: a notice that fell due
    while the gateway was down went out five minutes after the restart, not at it. Measured on a
    dev gateway: restarted at 18:56:41, first notice at 19:01:43."""
    import asyncio
    from types import SimpleNamespace

    from personalclaw.gateway import GatewayOrchestrator

    orch = GatewayOrchestrator.__new__(GatewayOrchestrator)
    orch.dashboard_state = None
    state = SimpleNamespace(notify=lambda *a, **k: None)
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(secs):
        slept.append(secs)
        orch.dashboard_state = state  # the dashboard comes up while the loop waits
        await real_sleep(0)

    swept: list = []

    async def fake_run_once(notify, *, now):
        swept.append(notify)
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(due_notices, "run_once", fake_run_once)
    with pytest.raises(asyncio.CancelledError):
        await orch._task_due_loop()
    assert swept == [state.notify]
    assert slept and slept[0] < due_notices.SWEEP_SECS, f"waited {slept[0]}s for the dashboard"
