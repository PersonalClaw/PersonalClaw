"""A payload the injection screen stopped spends none of its automation's hourly cap.

🔴 The defect (Backlog: "A screened inbound payload spends its automation's hourly cap", from CORE's
review of the held-fire fix). An automation's hourly cap (`rate_cap`, `max_runs_per_hour`,
`max_actions_per_hour`) reads `ScheduleRunStore.count_since`, which passes over the inert outcomes
and a `refused` row. The row the gateway writes when the injection screen stops an inbound payload
(`gateway._record_blocked_fire`: status and trigger both `blocked_injection`) was not passed over,
so it counted as a fire: a payload that did no work spent the cap, and enough of them held the
automation's real fires as "running away".

Now `count_since` passes over it as it does a suppression and a refusal. The rows are written by
the gateway's own writer, and the cap is read by the real tick over a scratch home.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore
from personalclaw.triggers import service as svc
from personalclaw.triggers.models import Outcome, Trigger
from personalclaw.triggers.store import TriggerStore

TID = "clock:inbox-watch"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.gateway.config_dir", lambda: tmp_path)
    return tmp_path


def _screened(times: int) -> None:
    """*times* payloads the injection screen stopped, recorded by the gateway's own writer."""
    from personalclaw.gateway import GatewayOrchestrator

    gateway = object.__new__(GatewayOrchestrator)
    trigger = SimpleNamespace(id=TID, name="Inbox watch")
    for _ in range(times):
        assert asyncio.run(gateway._record_blocked_fire(trigger, "role-forgery", source="webhook"))


def _count(home: Path) -> int:
    return asyncio.run(ScheduleRunStore(home).count_since(TID, time.time() - 3600.0))


def test_a_screened_payload_leaves_the_hourly_count_at_zero(home: Path) -> None:
    """🔴 Before: three screened payloads counted as three fires."""
    _screened(3)
    rows, total = asyncio.run(ScheduleRunStore(home).list_for_job(TID, 0, 20))
    assert total == 3, "each is still a row in the automation's history"
    assert {row["status"] for row in rows} == {Outcome.BLOCKED_INJECTION.value}, rows
    assert _count(home) == 0


def test_a_fire_that_ran_still_counts_beside_them(home: Path) -> None:
    """The control: the cap still measures the work the machine did."""
    _screened(2)
    now = time.time()
    asyncio.run(
        ScheduleRunStore(home).append(
            ScheduleRun(
                run_id="ran-1",
                job_id=TID,
                job_name="Inbox watch",
                trigger="schedule",
                source="schedule",
                started_at=now,
                finished_at=now,
                status=Outcome.RAN.value,
            )
        )
    )
    assert _count(home) == 1


def test_screened_payloads_do_not_hold_the_automations_next_fire(home: Path) -> None:
    """🔴 Before: two screened payloads filled a cap of one an hour, and the tick held its real
    fire as running away."""
    now = time.time()
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=TID,
            name="Inbox watch",
            kind="clock",
            enabled=True,
            overlap="parallel",
            spec={"kind": "interval", "interval_secs": 3600},
            next_fire_at=svc.to_iso(now - 1),
            gates={"max_runs_per_hour": 1},
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {"title_template": "New mail"}}},
        )
    )
    _screened(2)
    result = asyncio.run(
        svc.tick(TriggerStore(base_dir=home), now=now, base_dir=home, persist=True)
    )
    assert TID in [fire.trigger.id for fire in result.fires], result
