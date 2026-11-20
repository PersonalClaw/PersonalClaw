"""A failed or parked automation run's history keeps what its action printed, not its line twice.

🔴 The defect (Backlog: "A failed or parked automation run's history keeps only its one-line
reason"). The recorder set a failed run's trace to its line (`summary = trace = failure`) and a
parked one's to its waiting line, so what the action printed was dropped:
- a failed command's output, past the 400 characters of its error output the line quotes;
- a browse run's account of the steps it took, which it prints on a failure and a park too;
- any other action's output on a failure, and a failed agent's words before it failed.
Expanding the row showed the same line again, and Investigate ("why did this run fail?") was handed
that one line as the summary, the error and the trace, with nothing to diagnose from.

Now the line stays the summary (and the error), and the trace is the line, then what the action
printed and wrote to its error output, each under a heading (`schedule_history.failure_trace`),
capped and redacted as every trace is. Each test records a run through the real recorder and reads
its row back from the real store.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from personalclaw.action_providers.base import ActionResult
from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

TID = "clock:nightly-check"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    TriggerStore(base_dir=tmp_path).upsert(
        Trigger(
            id=TID, name="Nightly check", kind="clock", spec={"kind": "cron", "expr": "0 9 * * *"}
        )
    )
    return tmp_path


def _recorded(home: Path, result: ActionResult) -> dict[str, Any]:
    """One fire of the automation whose action returned *result*, recorded as the dispatch
    records it, read back whole."""
    from personalclaw.triggers.run_record import record_run

    trigger = TriggerStore(base_dir=home).get(TID)
    assert trigger is not None
    asyncio.run(
        record_run(trigger.trigger, started_at=time.time() - 1, result=result, source="schedule")
    )
    store = ScheduleRunStore(home)
    (row,), _total = asyncio.run(store.list_for_job(TID, 0, 5))
    full = asyncio.run(store.get_run(TID, row["run_id"]))
    assert full is not None
    return full


def test_a_failed_runs_trace_holds_what_its_action_printed(home: Path) -> None:
    """🔴 Before: the trace was "boom", the line again."""
    row = _recorded(home, ActionResult(success=False, error="boom", stdout="step 1\nstep 2"))
    assert (row["status"], row["summary"], row["error"]) == ("failure", "boom", "boom")
    assert row["trace"] == "boom\n\nOutput:\nstep 1\nstep 2"


def test_a_failed_command_keeps_its_whole_error_output(home: Path) -> None:
    """🔴 Before: only the 400-character tail its line quotes was kept; its first lines were lost."""
    written = "\n".join(f"line {n}: no such file or directory" for n in range(60))
    row = _recorded(home, ActionResult(success=False, exit_code=3, stderr=written))
    assert row["summary"].startswith("exited with code 3: ")
    assert "line 0: no such file" not in row["summary"], "the line quotes the tail alone"
    assert row["trace"].endswith(f"Error output:\n{written}")


def test_a_parked_runs_trace_holds_its_account(home: Path) -> None:
    """🔴 Before: a browse run that stopped for a sign-in kept its waiting line twice, and the
    account of the steps it took was dropped."""
    account = json.dumps({"steps": ["opened example.com", "reached the sign-in page"]})
    row = _recorded(
        home,
        ActionResult(
            success=True, outcome="needs_input", stdout=account, stderr="Sign in to example.com"
        ),
    )
    assert row["status"] == "waiting"
    assert row["summary"] == "Waiting for you. Sign in to example.com"
    assert row["trace"] == f"{row['summary']}\n\nOutput:\n{account}"


def test_a_credential_a_failed_action_printed_is_redacted_in_its_trace(home: Path) -> None:
    """What the trace now keeps is stored as a success's trace is: redacted."""
    token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    row = _recorded(
        home, ActionResult(success=False, error="push refused", stdout=f"token={token}")
    )
    assert "Output:" in row["trace"] and token not in row["trace"]


def test_a_failure_that_printed_nothing_keeps_its_line_as_its_trace(home: Path) -> None:
    """Nothing printed: the trace is the line, as it was."""
    row = _recorded(home, ActionResult(success=False, error="boom"))
    assert row["trace"] == "boom"


def _a_failed_agent_settles(home: Path, wrote: str) -> dict[str, Any]:
    """A launched agent of the automation fails having written *wrote*, and its ending settles
    the row its fire recorded; the row, read back whole."""
    from personalclaw.subagent import SubagentInfo, agent_work_id
    from personalclaw.triggers import settle

    ScheduleRunStore(home).append_sync(
        ScheduleRun(
            job_id=TID,
            job_name="Nightly check",
            source="schedule",
            started_at=time.time() - 60,
            status="launched",
            work_id=agent_work_id("a1b2c3d4"),
        )
    )
    info = SubagentInfo(
        id="a1b2c3d4", task="check", trigger_id=TID, error="the model stopped", result=wrote
    )
    assert settle.settle_agent_run(info, base_dir=home)
    store = ScheduleRunStore(home)
    (row,), _total = asyncio.run(store.list_for_job(TID, 0, 5))
    full = asyncio.run(store.get_run(TID, row["run_id"]))
    assert full is not None
    return full


def test_a_failed_agents_trace_holds_what_it_wrote_before_it_failed(home: Path) -> None:
    """🔴 Before: a launched agent that failed settled its row with its error as the trace, and
    what it had written was dropped."""
    full = _a_failed_agent_settles(home, "found 3 of 5")
    assert (full["status"], full["summary"]) == ("failure", "the model stopped")
    assert full["trace"] == "the model stopped\n\nOutput:\nfound 3 of 5"


def test_a_credential_a_failed_agent_wrote_is_redacted_in_its_trace(home: Path) -> None:
    """What a failed agent wrote is new on the settle path's trace, and stored redacted, as the
    recorder's is (`ScheduleRunStore.settle_sync`)."""
    token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    full = _a_failed_agent_settles(home, f"pushed with token={token}")
    assert "Output:\npushed with token=" in full["trace"] and token not in full["trace"]
