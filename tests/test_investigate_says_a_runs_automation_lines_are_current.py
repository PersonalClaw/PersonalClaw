"""Investigating a past run says the cadence and action it shows are the automation's now.

A run record keeps its status, duration, summary and error, but not the cadence it fired on or
the action it ran: Investigate reads those from the automation as it is today. It printed them as
plain ``Cadence:`` / ``Action:`` lines among the run's own facts, so after an edit the snapshot
described a run with a schedule it never had. They now sit under a line saying they are the
automation's current settings.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

import personalclaw.config.loader as loader
from personalclaw.investigate import resolve
from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore
from personalclaw.triggers import tools as Tools
from personalclaw.triggers.store import TriggerStore

_NOW = "The automation as it is now (it may have changed since this run):"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    return tmp_path


def _schedule(home) -> str:
    result = Tools.create(
        TriggerStore(base_dir=home),
        name="Water the plants",
        kind="clock",
        spec={"kind": "cron", "expr": "0 7 * * 6"},
        workflow={"inline": {"provider": "notify", "config": {"title_template": "Water"}}},
        created_by="user",
        owner_consented=True,
    )
    assert result.ok, result.text
    return str(result.data["trigger"]["id"])


def _snapshot(job: str, run_id: str) -> list[str]:
    ctx = asyncio.run(resolve("schedule_run", f"{job}:{run_id}", MagicMock()))
    assert ctx is not None
    return ctx.snapshot.split("\n")


def test_the_cadence_and_action_are_labelled_as_the_automations_current_ones(home):
    job = _schedule(home)
    ScheduleRunStore(home).append_sync(ScheduleRun(run_id="fire-1", job_id=job, status="success"))
    edited = Tools.update(
        TriggerStore(base_dir=home),
        trigger_id=job,
        patch={"spec": {"kind": "cron", "expr": "30 18 * * 1"}},
        owner_consented=True,
    )
    assert edited.ok, edited.text

    lines = _snapshot(job, "fire-1")

    assert _NOW in lines
    after = lines[lines.index(_NOW) + 1 :]
    cadence = [ln for ln in lines if ln.strip().startswith("Cadence:")]
    action = [ln for ln in lines if ln.strip().startswith("Action:")]
    assert cadence and action, lines
    # Both are read from the automation today, so both sit under the line that says so.
    assert all(ln in after and ln.startswith("  ") for ln in cadence + action)
    # The run's own facts come before it, unindented.
    assert lines.index("Status: success") < lines.index(_NOW)


def test_a_run_whose_automation_is_gone_shows_no_current_settings(home):
    job = _schedule(home)
    ScheduleRunStore(home).append_sync(ScheduleRun(run_id="fire-2", job_id=job, status="failure"))
    assert TriggerStore(base_dir=home).delete(job)

    lines = _snapshot(job, "fire-2")

    assert _NOW not in lines
    assert not [ln for ln in lines if ln.strip().startswith(("Cadence:", "Action:"))]
