"""A loop waiting on your answer spends nothing, and a loop's caps hold in every state.

Seen on an Attended Goal loop capped at three cycles: its worker wrote its question, the loop went
to ``needs_input`` and Home said it needed her, and its worker kept taking a turn at every idle
interval anyway, each one a paid model call that wrote "No step taken. The loop is paused on the
question". The cycle cap never stopped it, because the watchdog reads the cap only off a running
loop's new finding, so Home read "cycle 4/3".

A worker's turn starts in two places: the cycle its nudge fires at each idle interval, and the
re-prompt that asks a worker for a finding its turn did not write. Both now ask one rule,
``manager.turn_held``, before a turn starts. Driven here through the gateway's real cycle driver
on the real chat runner, with a scripted worker:

* a worker that asked its question is not re-prompted, and no idle interval starts a turn while
  the question waits, before the watchdog has seen it and after (``needs_input``);
* her answer, or her Resume, lets the next cycle start, as before;
* an Unattended loop's stray question holds nothing, since nobody is there to answer it;
* a loop at its cycle cap starts no turn whatever its status, nor one past its cost budget or its
  deadline, and a running loop that is already at its cap is completed by the watchdog.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from test_a_cycle_she_declined_is_not_asked_again import (  # noqa: F401 — the fixture is autouse
    _Cycle,
    _Nudges,
    _Row,
    _says,
    _tmp_home,
)

from personalclaw.loop import files as loop_files
from personalclaw.loop import manager, store
from personalclaw.loop import watchdog as W
from personalclaw.loop.loop import Loop, LoopStatus, LoopStopReason
from personalclaw.loop.manager import session_key
from personalclaw.triggers import idle_poll

FIRE = "fire"


def _loop(*, attended: bool = True, **fields: Any) -> Loop:
    loop = store.create(
        Loop(
            id="",
            name="Count the migrations",
            kind="goal",
            task="List the files in migrations/ and count them. Ask me before changing a file.",
            attended=attended,
            max_cycles=3,
            **fields,
        )
    )
    return store.update_status(loop.id, LoopStatus.RUNNING)


def _asks(loop_id: str):
    """The worker writes its question to the owner, as its cycle prompt tells it to."""
    return lambda: loop_files.write_question(loop_id, "May I rename 0003_users.py?")


def _found(loop_id: str, cycle: int) -> None:
    """Cycle *cycle*'s finding, written and on the loop's ledger."""
    d = loop_files.loop_dir(loop_id)
    assert d is not None
    (d / "findings").mkdir(parents=True, exist_ok=True)
    path = d / "findings" / f"cycle_{cycle:03d}.json"
    path.write_text(json.dumps({"cycle": cycle, "summary": f"cycle {cycle}"}))
    loop_files.record_cycle_findings(loop_id)


class _Loop(_Cycle):
    """The worker of *loop*, its nudge fired through the gateway's real cycle driver at each
    ``FIRE`` of *steps*; a callable step is what happens in between (the watchdog's move, her
    answer). :meth:`drive` returns what each fire answered."""

    def drive(self, steps: list[Any]) -> list[Any]:
        from personalclaw.config.loader import AppConfig
        from personalclaw.gateway import GatewayOrchestrator

        cfg = AppConfig()
        with patch.object(cfg, "load_credentials", return_value={}):
            orch = GatewayOrchestrator(cfg, no_dashboard=False, no_crons=True, no_open=True)
        orch.dashboard_state = self.state
        nudge = MagicMock(id="N1", session_name=self.key, message="Run the next cycle.")
        nudge.stop_sentinel_path, nudge.cycle_count = "", 0
        answered: list[Any] = []

        async def _go() -> None:
            with (
                patch("personalclaw.gateway.autonudge_enabled", return_value=True),
                patch("personalclaw.gateway.AutoNudgeService", _Nudges),
                patch("personalclaw.inbox.emit_attention_item", lambda *_a, **_kw: "i-1"),
            ):
                await orch._init_autonudge()
                if orch.loop_watchdog is not None:
                    await orch.loop_watchdog.stop()
                self.svc = _Nudges.last
                assert self.svc is not None
                self.svc.rows["N1"] = _Row("N1", self.key)
                with patch("personalclaw.triggers.nudge._INSTANCE", self.svc):
                    for step in steps:
                        if step != FIRE:
                            step()
                            continue
                        ok = await self.svc.on_fire(nudge)
                        answered.append(ok)
                        if ok is True:
                            await asyncio.wait_for(self.session.task, timeout=60)
                            await asyncio.sleep(0)

        asyncio.run(_go())
        return answered


def _worker(tmp_path, loop: Loop, scripts: list[list[Any]]) -> _Loop:
    return _Loop(tmp_path, loop, scripts, {})


# ── a question she has not answered ──────────────────────────────────────────────────────────


def test_a_loop_waiting_on_your_answer_starts_no_turn(tmp_path):
    loop = _loop()
    worker = _worker(tmp_path, loop, [[_asks(loop.id), *_says("I asked before renaming.")]])
    waits = lambda: store.update_status(loop.id, LoopStatus.NEEDS_INPUT)  # noqa: E731

    answered = worker.drive([FIRE, FIRE, FIRE, FIRE, waits, FIRE, FIRE, FIRE])

    assert answered[0] is True, "the cycle that asks the question runs"
    assert answered[1:] == [idle_poll.SKIP_LOOP_HELD] * 6, answered
    assert len(worker.prompts) == 1, f"a turn ran while she had not answered: {worker.prompts[1:]}"
    assert worker.reprompts() == [], "the cycle that asked was asked again for a finding"


def test_her_answer_lets_the_next_cycle_start(tmp_path):
    loop = _loop()
    worker = _worker(tmp_path, loop, [[_asks(loop.id), *_says("I asked.")], _says("Renamed.")])

    def answered_and_resumed() -> None:
        # Her answer: `manager.nudge` clears the question and starts the loop again.
        store.update_status(loop.id, LoopStatus.NEEDS_INPUT)
        loop_files.clear_question(loop.id)
        store.update_status(loop.id, LoopStatus.RUNNING)

    answered = worker.drive([FIRE, FIRE, answered_and_resumed, FIRE])

    assert answered == [True, idle_poll.SKIP_LOOP_HELD, True]
    assert len(worker.prompts) >= 2


def test_a_question_her_resume_answered_holds_nothing(tmp_path):
    loop = _loop()
    loop_files.write_question(loop.id, "May I rename 0003_users.py?", ts=0.0)
    assert manager.turn_held(session_key(loop.id)) == ""


def test_an_unattended_loops_stray_question_holds_nothing(tmp_path):
    loop = _loop(attended=False)
    worker = _worker(tmp_path, loop, [[_asks(loop.id), *_says("I asked.")]])

    answered = worker.drive([FIRE, FIRE])

    assert answered == [True, True], "nobody is there to answer an Unattended loop's question"


@pytest.mark.parametrize(
    "status", [LoopStatus.NEEDS_INPUT, LoopStatus.PAUSED, LoopStatus.STAGNANT, LoopStatus.BLOCKED]
)
def test_a_loop_that_is_not_running_starts_no_turn(tmp_path, status):
    """No question and no cap reached: a loop waiting on her for another reason (an Unattended
    run's trust ran out, a stall, a failing gate) or paused starts no turn either."""
    loop = _loop()
    store.update_status(loop.id, status)
    worker = _worker(tmp_path, loop, [_says("A turn nobody asked for.")])

    assert worker.drive([FIRE]) == [idle_poll.SKIP_LOOP_HELD]
    assert worker.prompts == []
    assert manager.turn_held(session_key(loop.id)) == f"its loop is {status.value}"


# ── the caps, in every state ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status", [LoopStatus.RUNNING, LoopStatus.NEEDS_INPUT, LoopStatus.PAUSED, LoopStatus.BLOCKED]
)
def test_a_loop_at_its_cycle_cap_starts_no_turn_in_any_state(tmp_path, status):
    loop = _loop()
    for cycle in (1, 2, 3):
        _found(loop.id, cycle)
    if status is not LoopStatus.RUNNING:
        store.update_status(loop.id, status)

    assert manager.turn_held(session_key(loop.id)), f"a {status.value} loop at 3/3 may run a turn"


def test_a_capped_loops_nudge_starts_no_fourth_cycle(tmp_path):
    loop = _loop()
    for cycle in (1, 2, 3):
        _found(loop.id, cycle)
    worker = _worker(tmp_path, loop, [_says("Cycle four.")])

    assert worker.drive([FIRE]) == [idle_poll.SKIP_LOOP_HELD]
    assert worker.prompts == []


def test_a_loop_past_its_cost_budget_or_deadline_starts_no_turn(tmp_path):
    spent = _loop(max_cost_usd=0.10)
    with patch.object(manager, "loop_spend", return_value={"dollars_est": 0.12}):
        assert manager.turn_held(session_key(spent.id)) == "its loop reached its cost budget"
    with patch.object(manager, "loop_spend", return_value={"dollars_est": 0.05}):
        assert manager.turn_held(session_key(spent.id)) == ""

    late = _loop(deadline_secs=60.0, elapsed_seconds=120.0)  # two active minutes banked
    assert manager.turn_held(session_key(late.id)) == "its loop reached its deadline"


def test_a_running_loop_already_at_its_cap_is_completed(tmp_path):
    """A loop resumed at 3/3 credits nothing new, and its nudge is held: the watchdog ends it at
    its cap rather than leaving it running with nothing to do."""
    loop = _loop()
    for cycle in (1, 2, 3):
        _found(loop.id, cycle)
    loop_files.write_credited_cycles(loop.id, 3)
    state = MagicMock()
    state._sessions = {}
    dog = W.LoopWatchdog(state, MagicMock())
    dog._swept = True

    async def _polls() -> None:
        with patch.object(manager, "end_run", new=MagicMock(side_effect=lambda *_a: _done())):
            await dog._poll_once()  # the first sight seeds the baseline
            await dog._poll_once()

    async def _done() -> None:
        return None

    asyncio.run(_polls())
    ended = store.get(loop.id)
    assert ended is not None and ended.status == LoopStatus.COMPLETE.value
    assert ended.stop_reason == LoopStopReason.CYCLE_BUDGET.value


def test_a_worker_that_asked_is_not_reprompted(tmp_path):
    """The re-prompt asks the same rule: a worker whose loop now waits on her answer is not asked
    again for a finding."""
    loop = _loop()
    loop_files.write_question(loop.id, "May I rename 0003_users.py?")
    session = MagicMock()
    session.key = session_key(loop.id)
    session.messages = []

    again, _title = asyncio.run(manager.reprompt_due(session, 0))
    assert again is False
