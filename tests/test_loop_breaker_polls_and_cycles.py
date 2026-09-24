"""The structural loop detector stops nagging a waiting agent, and can see a 3-step loop.

Two defects in `record_structural`, both about what it CAN and CANNOT see:

* **A legitimate poll was called a loop.** The no-progress rule fires on three identical
  `(tool, params, result)` triples with no notion of waiting. An agent correctly polling
  `systemctl is-active nginx` while a service starts was told at the third identical answer that
  it was "looping without making progress" — advice to stop doing the right thing.
* **Only period-2 cycles existed.** `span = STRUCT_PINGPONG_CYCLES * 2` hardcoded A↔B, so
  read → edit → test, repeat — the most common real agent loop — was invisible.

The stance stays warn-only, which is a deliberate documented ruling: the failure breaker
hard-blocks error storms; this path only tells a working agent what it looks like from outside.
"""

from __future__ import annotations

import pytest

from personalclaw.guardrails import loop_breaker as lb


def sig(tool: str, args: object, result: str) -> str:
    """A signature in exactly the shape the runtimes build (runtime.py:971)."""
    return f"{lb.params_key(tool, args)}\x1f{lb.result_digest(result)}"


def run(breaker: lb.LoopBreaker, seq: list[str], times: int) -> list[str]:
    """Feed `seq` round-robin `times` entries and return the non-empty reasons."""
    out = []
    for i in range(times):
        reason = breaker.record_structural(seq[i % len(seq)])
        if reason:
            out.append(reason)
    return out


# ── (a) polling is waiting, not looping ─────────────────────────────────────────


def test_a_shell_status_poll_is_not_flagged_at_the_normal_threshold():
    """The reported case: `bash("systemctl is-active x")` while a service starts."""
    poll = sig("bash", {"command": "systemctl is-active nginx"}, "activating")
    reasons = run(lb.LoopBreaker(), [poll], lb.STRUCT_REPEAT + 2)
    assert reasons == [], f"a status poll was called a loop after {lb.STRUCT_REPEAT + 2} calls"


def test_a_poll_that_never_terminates_IS_eventually_flagged():
    """Vacuity: the longer rope is a rope, not an exemption.

    Without this, `POLL_TOOLS` could be a blanket "never warn" and every test above would pass.
    """
    poll = sig("workflow_status", {"run": "r1"}, "running")
    reasons = run(lb.LoopBreaker(), [poll], lb.STRUCT_POLL_REPEAT)
    assert reasons, f"a poll repeated {lb.STRUCT_POLL_REPEAT}× was never flagged"
    assert "status poll" in reasons[0], f"the note does not name the waiting case: {reasons[0]!r}"


def test_a_shell_ACTION_is_still_flagged_at_the_normal_threshold():
    """`bash` is not pollable; `bash` running a status probe is. The command decides."""
    action = sig("bash", {"command": "rm -rf build && make"}, "done")
    reasons = run(lb.LoopBreaker(), [action], lb.STRUCT_REPEAT)
    assert reasons, "an identical shell ACTION repeated 3× was not flagged"


def test_a_read_only_tool_is_exempt_from_the_no_progress_rule():
    """Re-reading a file is how an agent confirms an edit landed."""
    read = sig("read", {"path": "a.py"}, "contents")
    reasons = run(lb.LoopBreaker(), [read], lb.STRUCT_POLL_REPEAT + 3)
    assert reasons == [], f"a repeated read was called a loop: {reasons}"


def test_the_exemption_is_keyed_on_the_TOOL_not_on_the_result():
    """A write repeating identically is still a loop even if its result never changes."""
    write = sig("write", {"path": "a.py", "content": "x"}, "ok")
    assert run(lb.LoopBreaker(), [write], lb.STRUCT_REPEAT), "an identical write was exempted"


# ── (b) cycles longer than two ──────────────────────────────────────────────────


def test_a_period_3_cycle_is_detected():
    """read → edit → test, repeat. Invisible while the span was hardcoded to `cycles * 2`."""
    seq = [
        sig("read", {"path": "a.py"}, "src"),
        sig("write", {"path": "a.py"}, "ok"),
        sig("bash", {"command": "pytest"}, "1 failed"),
    ]
    reasons = run(lb.LoopBreaker(), seq, 3 * lb.STRUCT_PINGPONG_CYCLES)
    assert reasons, "a 3-step rotation repeated 3× was not detected"
    assert "3 tool calls are cycling" in reasons[0], reasons[0]
    assert "A→B→C" in reasons[0], f"the note does not name the shape: {reasons[0]!r}"


def test_a_period_2_cycle_is_still_detected_and_still_named_as_two():
    """The old case must not regress into being reported as a longer cycle."""
    seq = [sig("write", {"p": "a"}, "ok"), sig("write", {"p": "b"}, "ok")]
    reasons = run(lb.LoopBreaker(), seq, 2 * lb.STRUCT_PINGPONG_CYCLES)
    assert reasons and "2 tool calls are cycling" in reasons[0], reasons


def test_a_cycle_shorter_than_the_repeat_count_is_not_flagged():
    """Two passes of A→B→C is work, not a loop. The threshold has to mean something."""
    seq = [
        sig("read", {"p": "a"}, "1"),
        sig("write", {"p": "a"}, "2"),
        sig("bash", {"c": "t"}, "3"),
    ]
    assert run(lb.LoopBreaker(), seq, 3 * (lb.STRUCT_PINGPONG_CYCLES - 1)) == []


def test_a_degenerate_cycle_is_left_to_the_no_progress_rule():
    """`A,A,B` × 3 is not reported twice — a cycle's entries must be distinct."""
    a = sig("write", {"p": "a"}, "ok")
    b = sig("write", {"p": "b"}, "ok")
    reasons = run(lb.LoopBreaker(), [a, a, b], 9)
    assert all("cycling" not in r for r in reasons), f"one loop reported twice: {reasons}"


def test_the_window_can_hold_the_longest_cycle_it_claims_to_detect():
    """A rail on the arithmetic: a period the window cannot span would be dead code."""
    assert lb.STRUCT_WINDOW >= max(lb.STRUCT_CYCLE_PERIODS) * lb.STRUCT_PINGPONG_CYCLES


@pytest.mark.parametrize("period", sorted(lb.STRUCT_CYCLE_PERIODS))
def test_every_declared_period_is_actually_reachable(period):
    """Each declared period detects something — a declared-but-undetected period is a lie."""
    seq = [sig("write", {"p": chr(97 + i)}, "ok") for i in range(period)]
    assert run(
        lb.LoopBreaker(), seq, period * lb.STRUCT_PINGPONG_CYCLES
    ), f"period {period} is declared but never fires"


def test_a_loop_is_reported_once_not_on_every_call():
    """The dedup that keeps the note from becoming the noise it warns about."""
    seq = [sig("write", {"p": "a"}, "ok"), sig("write", {"p": "b"}, "ok")]
    reasons = run(lb.LoopBreaker(), seq, 2 * lb.STRUCT_PINGPONG_CYCLES + 6)
    assert len(reasons) == 1, f"the same loop was reported {len(reasons)} times"
