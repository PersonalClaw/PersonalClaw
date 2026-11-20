"""A run a spend ceiling refused as it ran is held, as a fire the ceiling holds before it runs is.

🔴 The defect (Backlog: "An automation run refused at the spend ceiling mid-run counts toward
auto-pause"). The gateway's pre-run hold covers the token ceiling only, by design, so an
automation whose action makes paid model calls starts past a spent dollar ceiling, and the guard
refuses its first paid call (`BudgetExceededError`). That ending was recorded as `failure`, with the
typed exit `failed`, which counts toward the automation pausing itself. After five in a row it
paused, and stayed paused after the ceiling reset at midnight. A fire the pre-run hold refuses is
recorded `needs_input` instead: deferred, with the ceiling's sentence, counted toward nothing.

Now a run the day's ceiling refused as it ran is recorded that way
(`schedule_history.HELD_BY_A_SPEND_CEILING`): an agent the refusal ended (`SubagentInfo.ended_by`,
set where `SubagentManager._run` recognises it) and an action that raised it. The day's ceiling
resets at midnight; a run's own cap (`max_tokens_per_run`, a trigger's `max_cost_usd_per_run`)
resets with nothing, so its refusal stays the automation's failure, as does a result that only
says "budget" (`ActionResult.failure_class`) without saying which ceiling: held, an automation
whose job needs more than its cap would spend the cap on every fire and never pause.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.guardrails.failure import NO_ROOM, BudgetExceededError
from personalclaw.schedule_history import (
    HELD_BY_A_SPEND_CEILING,
    ScheduleRun,
    ScheduleRunStore,
    status_for_result,
)
from personalclaw.subagent import DAY_SPEND_CEILING, SubagentInfo, agent_work_id
from personalclaw.triggers import autopause, settle
from personalclaw.triggers.models import Trigger, TriggerHealth, TriggerState
from personalclaw.triggers.store import TriggerStore

TID = "clock:morning-brief"
#: Its own pause budget: the default the automation would pause itself at.
BUDGET = autopause.FAILURE_BUDGET


def _refusal(scope: str = "day") -> BudgetExceededError:
    """The day's ceiling's refusal, or a run's own cap's (*scope* "run")."""
    return BudgetExceededError(
        scope, "dollars", 4.0, 3.944, why=NO_ROOM, needed=0.299, ref="cloud:example-large"
    )


SAID = _refusal().sentence()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    TriggerStore(base_dir=tmp_path).upsert(
        Trigger(
            id=TID,
            name="Morning brief",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 3600},
            capabilities={"providers": ["invoke-agent"]},
            workflow={"inline": {"provider": "invoke-agent", "config": {"task_template": "Sum."}}},
        )
    )
    return tmp_path


def _launched(home: Path, work_id: str, n: int) -> None:
    """The row a fire writes once its action started its work: `launched`, naming the work."""
    ScheduleRunStore(home).append_sync(
        ScheduleRun(
            job_id=TID,
            job_name="Morning brief",
            source="schedule",
            started_at=time.time() - 60 + n,
            status="launched",
            work_id=work_id,
        )
    )


def _rows(home: Path) -> list[dict[str, Any]]:
    rows, _total = ScheduleRunStore(home)._list_for_job_sync(TID, 0, 50)
    return rows


def _live(home: Path) -> Trigger:
    row = TriggerStore(base_dir=home).get(TID)
    assert row is not None
    return row.trigger


async def _ended_by(refusal: BudgetExceededError, agent: str) -> SubagentInfo:
    """An agent of the automation whose run *refusal* ended, as `SubagentManager._run` leaves it."""
    from personalclaw.subagent import SubagentManager

    sessions = MagicMock()
    sessions.reset = AsyncMock()
    manager = SubagentManager(sessions=sessions, ctx_builder=MagicMock())

    async def _refused(info: Any, session_key: str) -> None:
        raise refusal

    manager._run_inner = _refused  # type: ignore[method-assign]
    manager._write_tombstone = lambda *_a, **_kw: None  # type: ignore[method-assign]
    manager._fire_event = AsyncMock()  # type: ignore[method-assign]
    info = SubagentInfo(id=agent, task="Sum.", trigger_id=TID)
    await asyncio.wait_for(manager._run(info), timeout=5)
    return info


def _agents_end(home: Path, n: int, scope: str) -> None:
    """*n* fires of the automation, each agent ended by a refusal of *scope*, each settled."""
    for i in range(n):
        agent = f"agent{i}"
        _launched(home, agent_work_id(agent), i)
        info = asyncio.run(_ended_by(_refusal(scope), agent))
        assert settle.settle_agent_run(info, base_dir=home)


def test_an_agent_the_days_ceiling_refused_says_the_ceiling_ended_it() -> None:
    """🔴 Before: its ending said the sentence, and nothing typed said what ended it."""
    info = asyncio.run(_ended_by(_refusal("day"), "a1b2c3d4"))
    assert (info.error, info.ended_by) == (SAID, DAY_SPEND_CEILING)


def test_an_agent_its_own_run_cap_refused_is_not_said_to_be_held() -> None:
    """A run's own cap resets with nothing: what ended it is the agent's failure."""
    info = asyncio.run(_ended_by(_refusal("run"), "a1b2c3d4"))
    assert info.error and info.ended_by == ""


def test_five_agent_runs_the_days_ceiling_refused_leave_the_automation_on(home: Path) -> None:
    """🔴 Before: five such endings in a row and the automation paused itself, for good."""
    _agents_end(home, BUDGET, "day")

    live = _live(home)
    assert (live.enabled, live.state) == (True, TriggerState.ACTIVE.value)
    assert live.health_status != TriggerHealth.FAILING.value
    rows = _rows(home)
    assert autopause.consecutive_failures_from(rows) == 0
    assert {(r["status"], r["summary"][:80], r.get("error") or "") for r in rows} == {
        (HELD_BY_A_SPEND_CEILING, SAID[:80], "")
    }
    assert live.last_waiting_at and not live.last_failure_at and not live.last_success_at


def test_five_agent_runs_their_own_cap_refused_still_pause_it(home: Path) -> None:
    """Held, an automation whose job needs more than its cap would spend it on every fire and
    never pause: its own cap's refusal counts, as any failure of its own does."""
    _agents_end(home, BUDGET, "run")

    live = _live(home)
    assert (live.enabled, live.state) == (False, TriggerState.AUTOPAUSED.value)


def _actions_end(home: Path, n: int, *, raised: BudgetExceededError | None) -> None:
    """*n* fires of the automation whose action raised *raised*, or else returned a failure it
    classed "budget", each recorded as the dispatch records it."""
    from personalclaw.action_providers.base import ActionResult
    from personalclaw.triggers.run_record import record_run

    trigger = _live(home)
    for _ in range(n):
        asyncio.run(
            record_run(
                trigger,
                started_at=time.time() - 1,
                exc=raised,
                result=(
                    None
                    if raised is not None
                    else ActionResult(success=False, failure_class="budget", error=SAID)
                ),
                error="the provider raised" if raised is not None else "",
                source="schedule",
            )
        )
        time.sleep(0.002)  # a row is named by the millisecond it was recorded


def test_an_action_that_raised_the_days_ceilings_refusal_is_held_and_counts_nothing(
    home: Path,
) -> None:
    """🔴 Before: recorded `failure` with the exit `failed`, five of them and it paused."""
    _actions_end(home, BUDGET, raised=_refusal("day"))

    rows = _rows(home)
    assert {(r["status"], r["trigger"]) for r in rows} == {
        (HELD_BY_A_SPEND_CEILING, HELD_BY_A_SPEND_CEILING)
    }, "held, and with no exit"
    assert all(r["summary"][:80] == SAID[:80] for r in rows), [r["summary"] for r in rows]
    assert _live(home).enabled is True


@pytest.mark.parametrize(
    "raised",
    [pytest.param(_refusal("run"), id="its-own-run-cap"), pytest.param(None, id="returned-budget")],
)
def test_an_action_refused_by_a_cap_that_does_not_reset_still_pauses_it(
    home: Path, raised: BudgetExceededError | None
) -> None:
    """Its own run cap's refusal, and a result that only says "budget" without saying which ceiling,
    are the automation's failures."""
    _actions_end(home, BUDGET, raised=raised)

    assert {r["status"] for r in _rows(home)} == {"failure"}
    assert _live(home).enabled is False


def test_a_result_that_only_says_budget_is_a_failure() -> None:
    """`ActionResult.failure_class` "budget" does not say which ceiling; held, a run cap's refusal
    would never pause its automation."""
    from personalclaw.action_providers.base import ActionResult

    assert status_for_result(ActionResult(success=False, failure_class="budget")) == "failure"
