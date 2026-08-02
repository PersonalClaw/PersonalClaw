"""Tests for loop-node middleware — the breaker's next tier and the escalation ladder.

Two properties are load-bearing and easy to break silently: a recoverable failure must
never burn an escalation rung (or a 429 surfaces a run that would have succeeded), and
every declared rung must be REACHABLE (a rung that can never be selected is dead
configuration that reads as a working feature).
"""

import pytest

from personalclaw.workflows.loop_middleware import (
    CLASS_ENTRY_RUNG,
    DEFAULT_LADDER,
    RECOVERABLE,
    Action,
    FailureClass,
    InterruptQueue,
    LoopState,
    MiddlewareVerdict,
    Rung,
    call_fingerprint,
    check_middleware,
    classify_failure,
    structured_brief,
)


def stalled(state: LoopState, *, n: int = 3, **kw) -> LoopState:
    """Drive `state` into an identical-call stall."""
    for _ in range(n):
        state.record_failure(tool="bash", args={"cmd": "make test"}, **kw)
    return state


# ── classification ──


@pytest.mark.parametrize(
    "text,expected",
    [
        ("HTTP 429 Too Many Requests", FailureClass.RATE_LIMIT),
        ("rate limited, retry after 5s", FailureClass.RATE_LIMIT),
        ("throttled by the provider", FailureClass.RATE_LIMIT),
        ("context_length_exceeded", FailureClass.CONTEXT_OVERFLOW),
        ("prompt is too long", FailureClass.CONTEXT_OVERFLOW),
        ("command not found: pytest", FailureClass.ENVIRONMENT),
        ("permission denied", FailureClass.ENVIRONMENT),
        ("connection reset by peer", FailureClass.TRANSIENT),
        ("504 Gateway Timeout", FailureClass.TRANSIENT),
        ("json decode error", FailureClass.MALFORMED_OUTPUT),
        ("schema validation failed", FailureClass.MALFORMED_OUTPUT),
        ("I was unable to finish", FailureClass.GAVE_UP),
        ("something opaque happened", FailureClass.UNKNOWN),
    ],
)
def test_failures_are_classified_deterministically(text, expected):
    assert classify_failure(text) is expected


def test_rate_limits_are_matched_before_generic_patterns():
    """Misclassifying a 429 as wrong-work is the EXPENSIVE direction: it spends a fresh
    session on something that needed a sleep."""
    assert classify_failure("request failed: 429 rate limited") is FailureClass.RATE_LIMIT


def test_an_explicit_hint_wins_over_the_text():
    assert classify_failure("anything", hint="wrong_work") is FailureClass.WRONG_WORK


def test_a_bogus_hint_falls_back_to_the_text():
    assert classify_failure("429 rate limited", hint="not_a_class") is FailureClass.RATE_LIMIT


def test_the_failure_class_enum_is_closed():
    with pytest.raises(ValueError):
        FailureClass("something_new")


# ── fingerprinting ──


def test_identical_calls_fingerprint_identically():
    assert call_fingerprint("bash", {"cmd": "pytest"}) == call_fingerprint(
        "bash", {"cmd": "pytest"}
    )


def test_argument_order_does_not_change_the_fingerprint():
    assert call_fingerprint("t", {"a": 1, "b": 2}) == call_fingerprint("t", {"b": 2, "a": 1})


def test_different_arguments_fingerprint_differently():
    assert call_fingerprint("bash", {"cmd": "a"}) != call_fingerprint("bash", {"cmd": "b"})


def test_the_tool_name_is_part_of_the_fingerprint():
    assert call_fingerprint("bash", {}) != call_fingerprint("python", {})


def test_unserializable_arguments_still_fingerprint():
    assert call_fingerprint("t", object())


# ── recoverable classes ──


def test_a_rate_limit_does_not_burn_an_escalation_rung():
    """Burning the ladder on a 429 is how a run that would have succeeded gets
    surfaced to a human instead."""
    state = LoopState()
    for _ in range(3):
        state.record_failure(text="429 rate limited")
    verdict = check_middleware(state)
    assert verdict.action is Action.CONTINUE
    assert verdict.consumed_rung is False
    assert state.escalation_index == 0


def test_a_recoverable_failure_asks_for_a_wait():
    state = LoopState()
    state.record_failure(text="429 rate limited")
    assert check_middleware(state).wait_secs > 0


def test_the_wait_grows_but_is_capped():
    state = LoopState()
    waits = []
    for _ in range(10):
        state.record_failure(text="connection reset")
        waits.append(check_middleware(state).wait_secs)
    assert waits[0] < waits[3]
    assert max(waits) <= 60.0


def test_persistent_recoverable_failures_eventually_halt():
    """A rate limit that never clears is no longer "wait" — it is a wall."""
    state = LoopState()
    for _ in range(12):
        state.record_failure(text="429 rate limited")
    verdict = check_middleware(state)
    assert verdict.action is Action.HALT
    assert verdict.reason == "recoverable_exhausted"


def test_recoverable_classes_are_exactly_the_worlds_fault():
    assert RECOVERABLE == {FailureClass.RATE_LIMIT, FailureClass.TRANSIENT}


# ── the environment shortcut ──


def test_an_environment_failure_halts_immediately():
    """No retry fixes a missing binary. Walking the ladder would waste every rung."""
    state = LoopState()
    state.record_failure(text="command not found: pytest")
    verdict = check_middleware(state)
    assert verdict.action is Action.HALT
    assert verdict.reason == "environment_broken"


def test_the_environment_class_enters_at_surface():
    assert CLASS_ENTRY_RUNG[FailureClass.ENVIRONMENT] is Rung.SURFACE


# ── the Continue → Nudge → Halt ladder ──


def test_a_stall_gets_a_nudge_before_anything_expensive():
    """Halting a run that one corrective sentence would fix is expensive in exactly the
    way autonomous execution cannot afford."""
    verdict = check_middleware(stalled(LoopState()))
    assert verdict.action is Action.NUDGE
    assert verdict.nudge_text


def test_the_nudge_names_the_actual_stall_when_the_class_is_unknown():
    """ "You ran the same command three times" is precise advice; "change your approach"
    is not."""
    verdict = check_middleware(stalled(LoopState()))
    assert "identical command" in verdict.nudge_text


def test_later_nudges_keep_the_specific_stall_text():
    """Measured on a real sequence: the FIRST nudge got "you ran the identical command"
    but cycles 4-5 fell back to the generic "change your approach". The later nudges are
    the ones a worker most needs specifics from."""
    state = stalled(LoopState())
    check_middleware(state)  # first nudge
    state.record_failure(tool="bash", args={"cmd": "make test"})
    later = check_middleware(state)
    assert "identical command" in later.nudge_text


def test_a_template_mutation_beats_the_generic_nudge():
    state = LoopState()
    for _ in range(3):
        state.record_failure(text="json decode error", tool="t", args={})
    verdict = check_middleware(state, failure_mutations={"malformed_output": "MY INSTRUCTION"})
    assert verdict.nudge_text == "MY INSTRUCTION"


def test_only_a_stall_that_survives_its_nudge_escalates():
    state = stalled(LoopState())
    assert check_middleware(state).action is Action.NUDGE
    state.record_failure(tool="bash", args={"cmd": "make test"})
    assert check_middleware(state).rung is not None


def test_every_declared_rung_is_reachable():
    """Measured regression: `attempt_cap` was applied to the ladder POSITION, so
    `restart_from_scratch` could never be selected under the plan's own declared values
    (cap 3 against a 5-rung ladder). A rung that can never be selected is dead
    configuration that reads as a working feature.
    """
    state = LoopState()
    seen: set[Rung] = set()
    for _ in range(40):
        state.record_failure(tool="bash", args={"cmd": "make test"})
        verdict = check_middleware(state)
        if verdict.rung:
            seen.add(verdict.rung)
        if verdict.action is Action.HALT:
            break
    for rung in DEFAULT_LADDER:
        assert rung in seen, f"{rung.value} was never selected"


def test_an_engine_rung_escalates_rather_than_halting():
    """Only SURFACE stops the run; fresh_session and model_switch are engine actions."""
    state = LoopState()
    for _ in range(3):
        state.record_failure(tool="t", args={}, hint="wrong_work")
    check_middleware(state)  # consume the nudge
    verdict = check_middleware(state)
    assert verdict.action is Action.ESCALATE
    assert verdict.rung is Rung.FRESH_SESSION


def test_wrong_work_skips_the_cheap_rung():
    """A classified retry does not fix work aimed at the wrong target."""
    assert CLASS_ENTRY_RUNG[FailureClass.WRONG_WORK] is Rung.FRESH_SESSION


def test_the_attempt_cap_bounds_attempts_within_a_rung():
    state = LoopState()
    rungs = []
    for _ in range(12):
        state.record_failure(tool="t", args={})
        verdict = check_middleware(state, escalation_cfg={"attempt_cap": 1})
        if verdict.rung:
            rungs.append(verdict.rung)
        if verdict.action is Action.HALT:
            break
    # With one attempt per rung it walks straight up.
    assert rungs[:2] == [Rung.CLASSIFIED_RETRY, Rung.FRESH_SESSION]


def test_a_template_ladder_is_honored():
    state = LoopState()
    for _ in range(3):
        state.record_failure(tool="t", args={})
    check_middleware(state, escalation_cfg={"ladder": ["classified_retry"]})
    verdict = check_middleware(state, escalation_cfg={"ladder": ["classified_retry"]})
    assert verdict.rung in (Rung.CLASSIFIED_RETRY, Rung.SURFACE)


def test_an_unknown_rung_is_dropped_not_fatal():
    """A template with a typo should escalate along the rungs it named correctly."""
    state = LoopState()
    for _ in range(3):
        state.record_failure(tool="t", args={})
    check_middleware(state, escalation_cfg={"ladder": ["classified_retry", "nonsense"]})
    verdict = check_middleware(state, escalation_cfg={"ladder": ["classified_retry", "nonsense"]})
    assert verdict.rung is not None


def test_surface_is_always_the_last_resort():
    """A ladder with no terminal rung would loop at its top forever."""
    from personalclaw.workflows.loop_middleware import _resolve_ladder

    assert _resolve_ladder({"ladder": ["classified_retry"]})[-1] is Rung.SURFACE
    assert _resolve_ladder({})[-1] is Rung.SURFACE
    assert _resolve_ladder({"ladder": "not a list"})[-1] is Rung.SURFACE


# ── the other stall shapes ──


def test_the_same_fix_repeated_abandons_the_hypothesis():
    """The diagnosis is wrong, not the execution."""
    state = LoopState()
    for _ in range(3):
        state.record_failure(text="still failing", fix="add a null check at line 52")
    state.nudges_issued = 1
    verdict = check_middleware(state)
    assert verdict.reason == "hypothesis_exhausted"


def test_flat_progress_across_the_window_is_a_stall():
    state = LoopState()
    for mark in (0.5, 0.5, 0.4, 0.5, 0.3):
        state.record_progress(mark)
    state.nudges_issued = 1
    assert check_middleware(state).reason == "no_progress"


def test_improving_progress_is_not_a_stall():
    state = LoopState()
    for mark in (0.1, 0.3, 0.5, 0.7, 0.9):
        state.record_progress(mark)
    assert check_middleware(state).action is Action.CONTINUE


def test_a_clean_state_continues():
    assert check_middleware(LoopState()).action is Action.CONTINUE


def test_the_windows_are_template_tunable():
    state = LoopState()
    for _ in range(2):
        state.record_failure(tool="t", args={})
    assert check_middleware(state).action is Action.CONTINUE
    assert check_middleware(state, breaker_cfg={"fingerprint_window": 2}).action is Action.NUDGE


@pytest.mark.parametrize(
    "bogus",
    [{"fingerprint_window": 0}, {"fingerprint_window": True}, {"fingerprint_window": "x"}],
)
def test_a_bogus_window_falls_back_to_the_default(bogus):
    """`True` is an int in Python, so a bool has to be rejected explicitly — a
    `fingerprint_window: true` would otherwise become a window of 1 and trip on the
    first failure."""
    assert check_middleware(stalled(LoopState()), breaker_cfg=bogus).action is Action.NUDGE


# ── success resets ──


def test_success_resets_the_counters():
    """A run that recovers is not on thin ice."""
    state = stalled(LoopState())
    state.escalation_index = 2
    state.reset_after_success()
    assert state.call_fingerprints == []
    assert state.escalation_index == 0
    assert state.attempts_at_rung == 0
    assert check_middleware(state).action is Action.CONTINUE


# ── the verdict object ──


def test_the_verdict_has_no_truthiness():
    """Deliberate: a convenience `__bool__` on a verdict is how `if verdict` came to
    mean "is this healthy" where the code meant "did I get one"."""
    with pytest.raises(TypeError):
        bool(MiddlewareVerdict())


# ── the structured brief ──


def test_the_brief_is_structured_never_a_transcript():
    """A transcript makes the user redo the diagnosis the engine already did."""
    brief = structured_brief(
        goal="make the gate pass",
        attempts=[{"class": "wrong_work", "error_signature": "AssertionError: expected 3"}],
        where_stuck="the judge rejects on evidence",
        recommendation="narrow the rubric",
    )
    assert set(brief) == {"goal", "attempts", "where_stuck", "recommendation", "options"}
    assert brief["attempts"][0]["error_signature"] == "AssertionError: expected 3"


def test_the_brief_offers_typed_choices():
    """A free-text "it failed" leaves the user to invent the next move."""
    assert (
        "reassign"
        in structured_brief(goal="g", attempts=[], where_stuck="w", recommendation="r")["options"]
    )


def test_the_brief_bounds_the_attempt_list():
    brief = structured_brief(
        goal="g",
        attempts=[{"error_signature": f"e{i}"} for i in range(30)],
        where_stuck="w",
        recommendation="r",
    )
    assert len(brief["attempts"]) <= 8


def test_error_signatures_are_kept_verbatim_but_bounded():
    """Paraphrasing an error is how the one detail that identifies it gets lost."""
    long_sig = "E" * 900
    brief = structured_brief(
        goal="g", attempts=[{"error_signature": long_sig}], where_stuck="w", recommendation="r"
    )
    kept = brief["attempts"][0]["error_signature"]
    assert kept.startswith("EEE") and len(kept) <= 400


# ── the interrupt queue ──


def test_interrupts_are_consumed_atomically_at_the_boundary():
    """Single-use consumption means a double-resume cannot replay them."""
    queue = InterruptQueue()
    queue.push("focus on the parser", now=1.0)
    queue.push("skip the docs", now=2.0)
    taken = queue.consume(now=3.0)
    assert len(taken) == 2
    assert queue.pending() == []
    assert queue.consume(now=4.0) == []


def test_blank_interrupts_are_rejected():
    queue = InterruptQueue()
    assert queue.push("") is None
    assert queue.push("   ") is None
    assert queue.pending() == []


def test_interrupts_keep_their_queue_order():
    """The user's second thought usually refines the first, so reversing them would
    apply the refinement before the thing it refines."""
    queue = InterruptQueue()
    queue.push("first", now=1.0)
    queue.push("second", now=2.0)
    assert [i.text for i in queue.consume(now=3.0)] == ["first", "second"]


def test_the_steering_prompt_demands_a_replan():
    """An instruction dropped into a running loop without one gets treated as extra
    work appended to the plan, when the user usually meant it to CHANGE the plan."""
    queue = InterruptQueue()
    queue.push("focus on the parser", now=1.0)
    prompt = queue.as_steering_prompt(queue.consume(now=2.0))
    assert "re-rank" in prompt
    assert "supersede" in prompt
    assert "focus on the parser" in prompt


def test_an_empty_steering_prompt_is_empty():
    assert InterruptQueue().as_steering_prompt([]) == ""


def test_a_consumed_interrupt_reports_itself_consumed():
    queue = InterruptQueue()
    item = queue.push("x", now=1.0)
    assert not item.consumed
    queue.consume(now=2.0)
    assert item.consumed


# ── the backend↔frontend event coupling ──

MIDDLEWARE_EVENTS = ("breaker_trip", "steering", "judge_verdict", "judge_divergence")


def test_the_middleware_events_are_ledger_kinds():
    """A refiner needs to know a run was nudged or steered: a verdict that followed a
    human's mid-run instruction is not evidence about the TEMPLATE, and without the
    event there is no way to tell the two apart."""
    from personalclaw.workflows.journal import LEDGER_KINDS

    for event in MIDDLEWARE_EVENTS:
        assert event in LEDGER_KINDS, event


def test_every_middleware_event_is_registered_in_the_frontend_union():
    """EventSource silently DROPS event types it has no listener for.

    So an unregistered event is not a rendering bug you can see — it is an event that
    never arrives. This test is the only thing standing between "the backend emits it"
    and "the user sees it".
    """
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "web/src/pages/loops/useRunStream.ts"
    text = source.read_text(encoding="utf-8")
    union = text.split("export const RUN_LIFECYCLE = [", 1)[1].split("] as const", 1)[0]
    for event in MIDDLEWARE_EVENTS:
        assert f"'{event}'" in union, f"{event} is emitted but not registered in RUN_LIFECYCLE"
