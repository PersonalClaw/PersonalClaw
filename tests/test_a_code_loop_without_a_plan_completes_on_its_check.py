"""A Code loop with no stage plan completes on the cycle its check first passes.

Such a loop is what the chat tools make when the agent leaves out the optional ``stage_plan``
(``code_project_create``). It is meant to complete once a finding lands and its verify and test
commands pass (``CodeKind._no_stage_done``). That hook answered "done" and completed nothing, and
the watchdog, reading "done" as "the hook already completed the loop", skipped the rest of the
cycle. So the worker was nudged on finished code for every remaining cycle of the budget, and the
loop then ended "Ended early", on its budget, as a loop whose check failed every cycle does. A
check that could not run read as one that passed, and the loop never showed what its check did.

Now the hook completes the loop as the stage path does; a check that could not run, or ran out
of time, holds completion and says why; each check's outcome is published as a stage's is; and a
hook that answers "done" for a loop still running has the watchdog complete it.

Every poll here is the watchdog's own (``LoopWatchdog._poll_once``), on the fakes
``tests/test_loop_watchdog.py`` drives it with.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from test_loop_watchdog import _FakeSession, _run, _running, _wd, _write_finding

from personalclaw.loop import gates, kinds, manager, store
from personalclaw.loop.loop import LoopStatus, LoopStopReason

#: A program no machine running these tests has: the shell exits 127.
NOT_INSTALLED = "pc-fixture-check-not-installed-81c4"


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    return tmp_path


def _code_loop(**kind_config: Any):
    """A Code loop with no stage plan, as the chat tools make one, its worker's session there."""
    loop = _running(kind="code", kind_config=dict(kind_config), max_cycles=3)
    wd = _wd()
    events: list[tuple[str, dict]] = []
    wd._publish = lambda lid, event, data=None: events.append(  # type: ignore[method-assign]
        (event, dict(data or {}))
    )
    key = manager.session_key(loop.id)
    wd._state._sessions[key] = _FakeSession(key)
    _run(wd._poll_once())  # seed liveness
    return loop, wd, events


def _checks(events: list[tuple[str, dict]]) -> list[dict]:
    return [data for event, data in events if event == "gate_check"]


@pytest.mark.parametrize("check", [{"verify_command": "true"}, {"test_command": "true"}])
def test_it_completes_as_done_on_the_cycle_its_check_first_passes(check):
    """🔴 Red before: it stayed running, and ended on its budget as "Ended early"."""
    loop, wd, events = _code_loop(**check)
    _write_finding(loop.id, 1)

    _run(wd._poll_once())

    now = store.get(loop.id)
    assert now.status == LoopStatus.COMPLETE.value, now.status
    assert now.stop_reason == LoopStopReason.DONE.value, now.stop_reason
    assert [c["ok"] for c in _checks(events)] == [True], events


def test_with_no_check_it_completes_on_its_first_finding():
    """🔴 Red before: it stayed running."""
    loop, wd, _events = _code_loop()
    _write_finding(loop.id, 1)

    _run(wd._poll_once())

    now = store.get(loop.id)
    assert (now.status, now.stop_reason) == (LoopStatus.COMPLETE.value, LoopStopReason.DONE.value)


def test_a_check_whose_program_is_not_installed_holds_completion_and_says_so():
    """🔴 Red before: the check that could not run read as a pass, and nothing said it ran."""
    loop, wd, events = _code_loop(verify_command=NOT_INSTALLED)
    _write_finding(loop.id, 1)

    _run(wd._poll_once())

    assert store.get(loop.id).status == LoopStatus.RUNNING.value
    [check] = _checks(events)
    assert check["ok"] is None and check["label"] == "verify", check
    assert check["skipped"], check


def test_a_check_that_runs_out_of_time_holds_completion_and_says_so(monkeypatch):
    """🔴 Red before: a check stopped for its time read as a pass, and nothing said so."""
    monkeypatch.setattr(gates, "VERIFY_TIMEOUT_SECS", 1)
    loop, wd, events = _code_loop(verify_command="sleep 5")
    _write_finding(loop.id, 1)

    _run(wd._poll_once())

    assert store.get(loop.id).status == LoopStatus.RUNNING.value
    [check] = _checks(events)
    assert check["ok"] is None and "1 second" in check["skipped"], check


def test_a_check_that_fails_keeps_it_cycling_until_its_budget():
    """Unchanged: a real failure holds completion every cycle, and the budget ends the loop."""
    loop, wd, events = _code_loop(verify_command="false")
    for cycle in (1, 2, 3):
        _write_finding(loop.id, cycle)
        _run(wd._poll_once())

    now = store.get(loop.id)
    assert now.status == LoopStatus.COMPLETE.value
    assert now.stop_reason == LoopStopReason.CYCLE_BUDGET.value, now.stop_reason
    assert [c["ok"] for c in _checks(events)] == [False, False, False], events


def test_a_hook_that_says_done_and_leaves_the_loop_running_is_completed(monkeypatch, caplog):
    """🔴 Red before: the watchdog took the hook's word, skipped the cycle and left it running."""
    loop, wd, _events = _code_loop(verify_command="true")

    async def _forgets(*_args: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(kinds, "run_cycle_hook", _forgets)
    _write_finding(loop.id, 1)

    with caplog.at_level(logging.WARNING, logger="personalclaw.loop.watchdog"):
        _run(wd._poll_once())

    assert store.get(loop.id).status == LoopStatus.COMPLETE.value
    assert any("still running" in r.getMessage() for r in caplog.records), caplog.records
