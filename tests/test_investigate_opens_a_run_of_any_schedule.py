"""Investigate on a schedule's run opens that run, whatever the schedule's id holds.

A run is addressed ``<schedule id>:<run id>`` — the Triggers page's run list builds it from the
row's raw id — and the resolver split that at the FIRST colon. Every schedule the store makes has
one in its id already (``clock:pack-the-soccer-bag``, ``system:triage:digest``), so the job came
out as ``clock`` and Investigate found nothing for any run of any of them. A run id never holds a
colon, so the run is what follows the LAST one; a bare schedule id still opens its latest run.
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


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    return tmp_path


def _schedule(home, name: str) -> str:
    result = Tools.create(
        TriggerStore(base_dir=home),
        name=name,
        kind="clock",
        spec={"kind": "cron", "expr": "0 7 * * 6"},
        workflow={"inline": {"provider": "notify", "config": {"title_template": name}}},
        created_by="user",
        owner_consented=True,
    )
    assert result.ok, result.text
    return str(result.data["trigger"]["id"])


def _ran(home, job_id: str, run_id: str, status: str = "success") -> None:
    ScheduleRunStore(home).append_sync(
        ScheduleRun(run_id=run_id, job_id=job_id, status=status, summary=f"run {run_id}")
    )


def _investigate(entity_id: str):
    return asyncio.run(resolve("schedule_run", entity_id, MagicMock()))


def test_a_run_of_a_clock_schedule_opens(home):
    job = _schedule(home, "Pack the soccer bag")
    assert job == "clock:pack-the-soccer-bag"
    _ran(home, job, "a1b2c3d4e5f6", status="failure")

    ctx = _investigate(f"{job}:a1b2c3d4e5f6")

    assert ctx is not None, "Investigate found nothing for a run of a clock schedule"
    assert ctx.title == "Run · Pack the soccer bag"
    assert "Schedule run a1b2c3d4e5f6 of job clock:pack-the-soccer-bag" in ctx.snapshot
    assert "Status: failure" in ctx.snapshot
    assert ctx.back_link == "#/triggers?open=schedule:clock:pack-the-soccer-bag"
    assert ctx.opening_prompt.startswith("Why did this run fail")


def test_the_named_run_opens_not_the_latest(home):
    job = _schedule(home, "Pack the soccer bag")
    _ran(home, job, "fire-1000", status="failure")
    _ran(home, job, "fire-2000", status="success")

    ctx = _investigate(f"{job}:fire-1000")

    assert ctx is not None
    assert "Schedule run fire-1000" in ctx.snapshot and "Status: failure" in ctx.snapshot


def test_a_schedule_id_with_two_colons_opens_its_run(home):
    _ran(home, "system:triage:digest", "b2c3d4e5f6a7")

    ctx = _investigate("system:triage:digest:b2c3d4e5f6a7")

    assert ctx is not None
    assert "Schedule run b2c3d4e5f6a7 of job system:triage:digest" in ctx.snapshot


def test_a_bare_clock_schedule_id_opens_its_latest_run(home):
    job = _schedule(home, "Pack the soccer bag")
    _ran(home, job, "fire-1000")
    _ran(home, job, "fire-2000")

    ctx = _investigate(job)

    assert ctx is not None
    assert "of job clock:pack-the-soccer-bag" in ctx.snapshot


def test_an_id_with_no_colon_still_opens(home):
    """The shape a schedule migrated from an older version carries (`digest:r1`)."""
    _ran(home, "digest", "r1")

    assert _investigate("digest:r1") is not None
    assert _investigate("digest") is not None


def test_a_run_that_is_not_there_is_nothing(home):
    job = _schedule(home, "Pack the soccer bag")
    _ran(home, job, "fire-1000")

    assert _investigate(f"{job}:fire-9999") is None
    assert _investigate("clock:no-such-schedule:fire-1000") is None


def test_a_run_of_a_one_shot_that_left_the_list_opens_by_the_name_it_kept(home):
    ScheduleRunStore(home).append_sync(
        ScheduleRun(
            run_id="c3d4e5f6a7b8",
            job_id="clock:pack-the-soccer-bag",
            job_name="Pack the soccer bag",
            status="success",
        )
    )

    ctx = _investigate("clock:pack-the-soccer-bag:c3d4e5f6a7b8")

    assert ctx is not None
    assert ctx.title == "Run · Pack the soccer bag"
    assert "Job: Pack the soccer bag (no longer in the list)" in ctx.snapshot
