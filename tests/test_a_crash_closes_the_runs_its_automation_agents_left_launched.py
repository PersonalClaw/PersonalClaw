"""After a crash, the next start closes each automation run its agent left `launched`.

🔴 The defect (Backlog: "An automation's agent run stays 'launched' forever after the gateway
crashes, whether its agent ran or waited"). An automation whose action starts an agent (Invoke
agent, Run prompt) records its run as `launched`, naming the agent in `work_id`, and the agent's
ending settles it. A graceful stop or Restart closes such a run first, as interrupted, with a card
on the restart review (`reaper.record_stopped_work`). A gateway that died instead (a crash, a
kill, a power loss) never did, and nothing at the next start knew the run: the pass over the agents
it left knows no trigger, and the run holds no claim for the claim pass. The row read "started work
that has not reported back yet" for good, the trigger's stamps did not move, and no card went on
the review. An agent still waiting to start (a free slot, or her Allow) is not kept on disk, since
its run was to carry it, so the same crash lost its work without a word.

Now the start closes each such run as interrupted (`reaper.record_left_behind_work`, from the
gateway's boot pass in `_init_cron`), with a reason saying the last gateway did not stop cleanly,
moves the trigger's stamps, and puts the run on the restart's one notice. Each case runs the
gateway's real boot (`_init_cron`) over a home a crash left, with no dashboard yet, as a real start
has: the notice is held until the dashboard is up (`_held_boot_review`).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore
from personalclaw.subagent import agent_work_id
from personalclaw.triggers import reaper, review, run_source
from personalclaw.triggers.models import Trigger, TriggerHealth
from personalclaw.triggers.store import TriggerStore
from personalclaw.workref import work_ref

TID = "clock:morning-brief"
NAME = "Morning brief"
AGENT = "5ca1ab1e"
#: How long before the crash the run began.
RAN = 240.0
NOT_CLEAN = "Interrupted: the last gateway ended without stopping cleanly"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.gateway.config_dir", lambda: tmp_path)
    return tmp_path


def _brief(home: Path, trigger_id: str = TID) -> None:
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=trigger_id,
            name=NAME,
            kind="clock",
            enabled=True,
            created_by="user",
            spec={"kind": "cron", "expr": "15 7 * * 1-5"},
            capabilities={"providers": ["invoke-agent"]},
            workflow={"inline": {"provider": "invoke-agent", "config": {"task_template": "Sum."}}},
        )
    )


def _launched(
    home: Path,
    *,
    trigger_id: str = TID,
    work_id: str = "",
    source: str = run_source.SCHEDULE,
    status: str = "launched",
) -> str:
    """The row a fire writes once its action started its agent, as the run store keeps it."""
    run = ScheduleRun(
        job_id=trigger_id,
        job_name=NAME,
        source=source,
        started_at=time.time() - RAN,
        status=status,
        work_id=work_id or agent_work_id(AGENT),
    )
    ScheduleRunStore(home).append_sync(run)
    return run.run_id


def _rows(home: Path, trigger_id: str = TID) -> list[dict[str, Any]]:
    rows, _total = ScheduleRunStore(home)._list_for_job_sync(trigger_id, 0, 20)
    return rows


def _live(home: Path, trigger_id: str = TID) -> Trigger:
    row = TriggerStore(base_dir=home).get(trigger_id)
    assert row is not None
    return row.trigger


def _start(home: Path) -> Any:
    """The next start's boot, as `run` begins it: `_init_cron`, before any agent or dashboard.
    The gateway it started, whose background tasks are stopped again."""
    from personalclaw.config.loader import AppConfig
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg, no_dashboard=True, no_crons=False, no_open=True)

    async def _boot() -> None:
        before = asyncio.all_tasks()
        with patch.object(ScheduleRunStore, "rotate_all", AsyncMock()):
            await orch._init_cron()
        started = asyncio.all_tasks() - before
        for task in started:
            task.cancel()
        if started:
            await asyncio.wait(started, timeout=5.0)

    asyncio.run(_boot())
    return orch


def _held(orch: Any) -> list[tuple[str, str, str]]:
    """The cards the start's one notice will name, once the dashboard is up."""
    held = getattr(orch, "_held_boot_review", None)
    assert held is not None, "the start holds no notice for the dashboard"
    _report, cards = held
    return [(c.trigger_id, c.kind, c.reason) for c in cards]


@pytest.mark.parametrize(
    "folder",
    [
        # Waiting for a free slot or her Allow: never started, so no folder and no waiting record.
        pytest.param(False, id="its-agent-was-waiting"),
        # Working: its folder is left with no ending written, as a kill leaves it.
        pytest.param(True, id="its-agent-was-working"),
    ],
)
def test_the_next_start_closes_the_run_a_crash_left_launched(home: Path, folder: bool) -> None:
    """🔴 Before: the row read `launched` for good, the trigger's last run with it, and the start
    put nothing on the review."""
    _brief(home)
    run_id = _launched(home)
    if folder:
        from personalclaw.subagent_persistence import create_agent_folder

        create_agent_folder(AGENT, task="Sum.", title=NAME)

    orch = _start(home)

    (row,) = _rows(home)
    assert row["status"] == reaper.RESTART_INTERRUPTED_STATUS, "the run still reads launched"
    assert row["error"].startswith(NOT_CLEAN), row["error"]
    assert "run it again from the review on the Triggers page" in row["error"]
    live = _live(home)
    assert live.last_run_id == run_id, "the trigger's last run is not this one"
    assert live.last_error_summary == row["error"]
    assert live.health_status == TriggerHealth.DEGRADED.value, "a fire's trigger reads degraded"
    assert _held(orch) == [(TID, review.INTERRUPTED, row["error"])], "one card, on the one notice"
    assert [c.trigger_id for c in review.pending(base_dir=home)] == [TID]


def test_a_run_now_a_crash_cut_off_leaves_its_triggers_health_alone(home: Path) -> None:
    """A run of yours is closed as the run of yours it was: its row keeps its source, and the
    trigger's health is not marked degraded by it."""
    _brief(home)
    before = _live(home).health_status
    _launched(home, source=run_source.YOU)

    _start(home)

    (row,) = _rows(home)
    assert row["status"] == reaper.RESTART_INTERRUPTED_STATUS
    assert row["source"] == run_source.YOU
    assert _live(home).health_status == before


def test_a_run_waiting_on_a_workflow_run_is_left_to_that_run(home: Path) -> None:
    """A workflow run resumes across a restart and settles its trigger's row itself."""
    _brief(home)
    _launched(home, work_id=work_ref("workflow", "9c2c10ab"))

    orch = _start(home)

    (row,) = _rows(home)
    assert row["status"] == "launched"
    assert _held(orch) == []


def test_a_run_whose_agent_ended_is_not_touched(home: Path) -> None:
    """Only the last row about an agent counts, and only while it has not settled: an agent that
    ended before the crash keeps its own ending."""
    _brief(home)
    _launched(home, status="success")

    orch = _start(home)

    (row,) = _rows(home)
    assert row["status"] == "success"
    assert _held(orch) == []


def test_a_graceful_stop_then_a_start_closes_the_run_once(home: Path) -> None:
    """The stop closed the run and kept its card (`record_stopped_work`): the start finds nothing
    more to close, and the one card is the stop's, announced by this start."""
    _brief(home)
    _launched(home)
    assert reaper.record_stopped_work(
        TID,
        work_id=agent_work_id(AGENT),
        restarting=True,
        store=TriggerStore(base_dir=home),
        base_dir=home,
    )
    (stopped,) = _rows(home)

    orch = _start(home)

    (row,) = _rows(home)
    assert row == stopped, "the start wrote over the stop's ending"
    assert not row["error"].startswith(NOT_CLEAN)
    assert [(tid, kind) for tid, kind, _ in _held(orch)] == [(TID, review.INTERRUPTED)]


def test_a_run_whose_automation_is_gone_is_closed_with_no_card(home: Path) -> None:
    """Its history outlives it (a one-shot retires, a delete keeps the history): the row is closed,
    and nothing on the review offers to run what is no longer there."""
    _launched(home)

    orch = _start(home)

    (row,) = _rows(home)
    assert row["status"] == reaper.RESTART_INTERRUPTED_STATUS
    assert _held(orch) == []


def test_a_lifecycle_hooks_agent_touches_no_run(home: Path) -> None:
    """A lifecycle hook's agent keeps no run rows, and a row under its id is not one to close."""
    hook = "lifecycle:session-start"
    _launched(home, trigger_id=hook)

    orch = _start(home)

    (row,) = _rows(home, hook)
    assert row["status"] == "launched"
    assert _held(orch) == []


def test_each_agent_a_trigger_left_waiting_is_closed(home: Path) -> None:
    """Two fires of one automation (`overlap: allow`) each left their agent working: both runs
    are closed, each with its own card."""
    _brief(home)
    _launched(home, work_id=agent_work_id("a1"))
    _launched(home, work_id=agent_work_id("a2"))

    orch = _start(home)

    assert {row["status"] for row in _rows(home)} == {reaper.RESTART_INTERRUPTED_STATUS}
    assert [tid for tid, _, _ in _held(orch)] == [TID, TID]


def test_the_pass_never_raises_on_a_history_it_cannot_read(home: Path) -> None:
    """A run file that isn't JSON leaves the start to go on, and closes nothing."""
    history = home / "cron-history"
    history.mkdir()
    (history / f"{TID}.jsonl").write_text("{not json\n", encoding="utf-8")
    assert reaper.record_left_behind_work(store=TriggerStore(base_dir=home), base_dir=home) == []
