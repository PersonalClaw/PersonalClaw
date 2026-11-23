"""An open-ended goal loop's cycle judge can say it cannot tell, and the loop asks you.

🔴 The defect (Backlog: "An open-ended goal loop's cycle judge has no way to say it cannot tell,
so the loop keeps cycling"). Each cycle's judge answered only done or not done, and its parser read
no can't-tell. A judge that could not see whether the goal was met (the report too thin, the
deliverable not shown) could only answer not done, which reads as "keep going": the loop ran
another cycle, and another, until its budget or the stagnation check stopped it. Seen live: a
skeptic shown the first 4,000 characters of a finished report wrote "cannot be confirmed" twice,
and the loop spent two more cycles on it.

Now both cycle-judge prompts offer `cannot_judge`, the parser reads it (`judge._parse_verdict`,
never done while it is said), the skeptic's merge keeps it (`judge_contract.adjudicate`), and the
supervisor defers that cycle, as one with no verdict: after two in a row it pauses the loop for you
with the judge's own reason (`supervisor.pause_when_its_judge_cannot_tell`). An ordinary not-done
answer still keeps the loop going.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from personalclaw.loop import files as loop_files
from personalclaw.loop import judge as judge_mod
from personalclaw.loop import store, supervisor
from personalclaw.loop import watchdog as W
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.workflows.judge_contract import JudgeVerdict, Verdict, adjudicate
from personalclaw.workflows.supervisor_policy import policy_for_kind

PROMPTS = Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "config" / "prompts"
WHY = "Only the first 4,000 characters of the 29,353-character report were shown"


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _homes(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "workspace"))
    assert loop_files.config_dir() == tmp_path, "the store redirect did not take — refusing to run"
    return tmp_path


def _loop() -> Loop:
    return store.create(
        Loop(
            id="",
            name="o",
            kind="goal",
            task="write a report on the field",
            kind_config={"goal_type": "open_ended", "judge_calibrated": True},
        )
    )


def _judged(monkeypatch, *answers: str) -> None:
    """The cycle judge answers each of *answers* in turn, as its parser reads a model's text."""
    said = list(answers)

    async def assess(*_a: Any, **_k: Any) -> JudgeVerdict | None:
        return judge_mod._parse_verdict(said.pop(0))

    monkeypatch.setattr(judge_mod, "assess_cycle", assess)


def _cycle(loop: Loop, n: int) -> bool | None:
    fresh = store.get(loop.id)
    findings = [{"cycle": i, "summary": f"cycle {i}"} for i in range(1, n + 1)]
    return _run(
        supervisor.done_signal(fresh, findings, policy_for_kind(loop.kind, loop.kind_config))
    )


@pytest.mark.parametrize("name", ["task-cycle_judge.md", "task-cycle_judge_skeptic.md"])
def test_each_cycle_judge_is_offered_a_cannot_tell_answer(name):
    text = (PROMPTS / name).read_text(encoding="utf-8")
    assert '"cannot_judge":' in text and "cannot tell" in text, name


def test_the_parser_reads_it_and_a_cannot_tell_is_never_done():
    read = judge_mod._parse_verdict(f'{{"done": true, "cannot_judge": "{WHY}"}}')
    assert read is not None and read.cannot_judge == WHY and not read.done, read
    for nothing in ("", "none", "N/A", "None."):
        said = judge_mod._parse_verdict(f'{{"done": false, "cannot_judge": "{nothing}"}}')
        assert said is not None and said.cannot_judge == "", (nothing, said)


def test_a_skeptic_that_cannot_tell_overturns_a_completion_saying_why():
    primary = JudgeVerdict(verdict=Verdict.PASS, done_reason="it reads as finished")
    skeptic = JudgeVerdict(verdict=Verdict.REJECT, cannot_judge=WHY)
    merged = adjudicate(primary, skeptic)
    assert not merged.done and merged.cannot_judge == WHY, merged
    both = adjudicate(primary, JudgeVerdict(verdict=Verdict.PASS))
    assert both.done and both.cannot_judge == "", "the control: two yeses"


def test_a_cycle_it_cannot_tell_defers_and_two_in_a_row_pause_the_loop(monkeypatch):
    """🔴 Before: each read as not done (False), and the loop kept cycling."""
    _judged(
        monkeypatch,
        f'{{"done": false, "marginal_value": 3, "cannot_judge": "{WHY}"}}',
        f'{{"done": false, "marginal_value": 3, "cannot_judge": "{WHY}"}}',
    )
    loop = _loop()
    assert _cycle(loop, 1) is None, "a cycle it cannot tell defers"
    (first,) = loop_files.get_verdicts(loop.id)
    assert first["done"] is False and WHY in first["cannot_judge"], first
    assert first["cannot_judge"].startswith("The judge could not tell whether the goal is met")
    assert "marginal_value" not in first, "a cycle nobody could rule on carries no score"
    events: list[tuple[str, Any]] = []
    watchdog = SimpleNamespace(_publish=lambda cid, event, data: events.append((event, data)))
    policy = policy_for_kind(loop.kind, loop.kind_config)
    W.LoopWatchdog._surface_a_deferred_check(watchdog, loop.id, loop, policy, 1)
    assert store.get(loop.id).status != LoopStatus.NEEDS_INPUT.value, "one is not yet a pause"

    assert _cycle(loop, 2) is None
    W.LoopWatchdog._surface_a_deferred_check(watchdog, loop.id, store.get(loop.id), policy, 2)
    assert store.get(loop.id).status == LoopStatus.NEEDS_INPUT.value
    asked = (loop_files.pending_question(loop.id) or {}).get("question", "")
    assert "could not tell, 2 cycles in a row" in asked and WHY in asked, asked
    assert [e for e, _d in events if e != "judge_error"] == ["needs_input"], events
    assert ("judge_error", {"loop_id": loop.id, "cycle": 1}) in events, "one defers as before"


def test_an_ordinary_not_done_still_keeps_the_loop_going_and_ends_the_streak(monkeypatch):
    _judged(
        monkeypatch,
        f'{{"done": false, "cannot_judge": "{WHY}"}}',
        '{"done": false, "marginal_value": 3, "quality_score": 3}',
        f'{{"done": false, "cannot_judge": "{WHY}"}}',
    )
    loop = _loop()
    assert _cycle(loop, 1) is None
    assert _cycle(loop, 2) is False, "a judge that could tell, and said not done, keeps it going"
    assert _cycle(loop, 3) is None
    assert not supervisor.pause_when_its_judge_cannot_tell(
        loop.id, lambda *_a: None
    ), "the not-done between them ended the streak"


def test_one_cannot_tell_between_cycles_it_could_rule_on_never_pauses_the_loop(monkeypatch):
    """Two in a row pause it; a single one between two cycles the judge ruled on is a thin report
    the next cycle answered, and the loop goes on without asking you."""
    ruled = '{"done": false, "marginal_value": 3, "quality_score": 3}'
    _judged(monkeypatch, ruled, f'{{"done": false, "cannot_judge": "{WHY}"}}', ruled, ruled)
    loop = _loop()
    watchdog = SimpleNamespace(_publish=lambda *_a: None)
    policy = policy_for_kind(loop.kind, loop.kind_config)
    for n, said in enumerate((False, None, False, False), start=1):
        assert _cycle(loop, n) is said, n
        W.LoopWatchdog._surface_a_deferred_check(watchdog, loop.id, store.get(loop.id), policy, n)
        assert store.get(loop.id).status != LoopStatus.NEEDS_INPUT.value, n
    assert loop_files.pending_question(loop.id) is None
