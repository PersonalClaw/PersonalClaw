"""S68 — generalized autopause, parking, quarantine, and Runs-inbox surfacing (§3.7).

The plan says autopause-after-5 ALREADY EXISTS for the cron action path and that the substrate
generalizes it. So this suite starts from what the shipped one does, measured by driving it:
`GatewayOrchestrator._maybe_autopause` increments one counter at all four of its call sites with no
notion of WHY the fire failed, so **five consecutive denylist blocks disable the trigger** — a
policy the operator configured on purpose, read as five failures.

`test_a_policy_refusal_never_spends_the_budget` is the regression for that. The rest pin the
distinctions that make the generalization worth having: an outage parks (reversible), a config error
pauses at once (retrying cannot help), and only a true failure spends the budget.
"""

from __future__ import annotations

import pytest

from personalclaw.triggers.autopause import (
    EXIT_TYPES,
    FAILURE_BUDGET,
    IMMEDIATE_PAUSE_EXITS,
    PARK_COOLDOWN_SECS,
    PARK_REASONS,
    PARKING_EXITS,
    ExitType,
    attention_card,
    classify_exception,
    counts_toward_autopause,
    evaluate,
    inbox_fingerprint,
    is_duplicate_card,
    needs_attention,
    outcome_for_exit,
    resume_state,
    unpark_due,
)
from personalclaw.triggers.models import (
    FIRE_OUTCOMES,
    TRUE_FAILURE_OUTCOMES,
    Outcome,
    TriggerHealth,
    TriggerState,
)

# ── the shipped behaviour this session generalizes ──


def test_the_shipped_budget_is_preserved_exactly():
    """5, matching `GatewayOrchestrator._maybe_autopause`.

    Pinned because changing it during a port would be a behaviour change disguised as a migration:
    every existing cron author tuned their expectations against this number.
    """
    assert FAILURE_BUDGET == 5


def test_five_true_failures_still_pause_like_the_shipped_path():
    count = 0
    states = []
    for _ in range(FAILURE_BUDGET):
        decision = evaluate(exit_type=ExitType.FAILED.value, consecutive_failures=count)
        count = decision.consecutive_failures
        states.append(decision.state)
    assert count == FAILURE_BUDGET
    assert states[-1] == TriggerState.AUTOPAUSED.value
    assert states[:-1] == [TriggerState.ACTIVE.value] * (FAILURE_BUDGET - 1)


def test_a_policy_refusal_never_spends_the_budget():
    """THE regression for the measured bug.

    Driven against the shipped `_maybe_autopause`, five consecutive denylist blocks set
    `enabled = False`. A refusal is a policy decision the operator configured; counting it as a
    failure disables the user's trigger for working as designed.
    """
    assert counts_toward_autopause(Outcome.REFUSED.value) is False
    assert counts_toward_autopause(Outcome.BLOCKED_INJECTION.value) is False


def test_only_failed_counts_toward_autopause():
    """Every outcome in the closed vocabulary, not a spot-check of three.

    A parameterized walk is what catches an outcome ADDED later that silently starts or stops
    counting — the failure mode is invisible either way.
    """
    counting = {o for o in FIRE_OUTCOMES if counts_toward_autopause(o)}
    assert counting == {Outcome.FAILED.value}
    assert counting == set(TRUE_FAILURE_OUTCOMES)


def test_skipped_outcomes_do_not_count():
    """A trigger that skipped 5× on quiet hours is working as configured."""
    for outcome in (
        Outcome.SKIPPED_GATE.value,
        Outcome.SKIPPED_OVERLAP.value,
        Outcome.SKIPPED_BUDGET.value,
        Outcome.SKIPPED_NOOP.value,
        Outcome.SKIPPED_TRIAGE.value,
        Outcome.SKIPPED_MISSED.value,
        Outcome.DEFERRED.value,
    ):
        assert counts_toward_autopause(outcome) is False, outcome


# ── typed exits ──


@pytest.mark.parametrize("exit_type", EXIT_TYPES)
def test_every_exit_type_maps_to_a_real_outcome(exit_type):
    assert outcome_for_exit(exit_type) in FIRE_OUTCOMES


def test_an_outage_defers_rather_than_failing():
    """The mapping that keeps a transport outage out of the failure budget."""
    for exit_type in PARKING_EXITS:
        assert outcome_for_exit(exit_type) == Outcome.DEFERRED.value
        assert counts_toward_autopause(outcome_for_exit(exit_type)) is False


def test_an_unknown_exit_reads_as_failure_not_as_benign():
    """Fail-safe direction: an unclassified exit is more likely real breakage than a benign skip.

    Defaulting to benign would let a whole class of failure fire forever without ever autopausing.
    """
    assert outcome_for_exit("something-nobody-classified") == Outcome.FAILED.value


def test_partial_is_a_success_not_a_failure():
    """`partial` is resumable with a persisted cursor — re-firing continues the work."""
    decision = evaluate(exit_type=ExitType.PARTIAL.value, consecutive_failures=3)
    assert decision.state == TriggerState.ACTIVE.value
    assert decision.consecutive_failures == 0


# ── parking ──


def test_an_outage_parks_and_leaves_the_counter_untouched():
    """Untouched, not reset: an outage is neither progress nor failure.

    Resetting would let a flapping credential clear a real failure streak on every other fire.
    """
    decision = evaluate(
        exit_type=ExitType.AUTH_UNAVAILABLE.value, consecutive_failures=3, now=1000.0
    )
    assert decision.state == TriggerState.PARKED.value
    assert decision.consecutive_failures == 3
    assert decision.health == TriggerHealth.PARKED.value
    assert decision.retry_after == 1000.0 + PARK_COOLDOWN_SECS
    assert decision.fires_automatically is False


def test_parking_never_autopauses_no_matter_how_long_the_outage():
    """An expired token must not leave the automation disabled after the user fixes it."""
    count = 0
    for _ in range(FAILURE_BUDGET * 3):
        decision = evaluate(
            exit_type=ExitType.TRANSPORT_UNAVAILABLE.value, consecutive_failures=count, now=0.0
        )
        count = decision.consecutive_failures
        assert decision.state == TriggerState.PARKED.value


def test_every_parking_exit_explains_what_to_fix():
    for exit_type in PARKING_EXITS:
        decision = evaluate(exit_type=exit_type, consecutive_failures=0, now=0.0)
        assert decision.reason, f"{exit_type} parked with no reason"
        assert decision.reason == PARK_REASONS[exit_type]


def test_a_success_clears_a_park():
    """A successful fire is proof the outage ended.

    Leaving the parked state set would keep skipping a trigger that demonstrably works.
    """
    decision = evaluate(exit_type=ExitType.OK.value, consecutive_failures=4)
    assert decision.state == TriggerState.ACTIVE.value
    assert decision.consecutive_failures == 0
    assert decision.health == TriggerHealth.OK.value
    assert decision.retry_after == 0.0


def test_unpark_is_clock_driven_and_a_missing_deadline_reads_as_due():
    # Clock-driven because a parked trigger produces no fires to evaluate — nothing would ever
    # bring it back if the transition lived in the outcome path.
    assert unpark_due(retry_after=500.0, now=499.0) is False
    assert unpark_due(retry_after=500.0, now=500.0) is True
    # A park written before `retry_after` existed must not strand the trigger forever.
    assert unpark_due(retry_after=0.0, now=0.0) is True


# ── config errors + quarantine ──


def test_a_config_error_pauses_on_the_first_fire():
    """A fire that cannot succeed has nothing to wait for.

    Five attempts is four pointless fires and four rows of inbox noise.
    """
    decision = evaluate(exit_type=ExitType.CONFIG_ERROR.value, consecutive_failures=0)
    assert decision.state == TriggerState.AUTOPAUSED.value
    assert decision.consecutive_failures == 1
    assert "cannot succeed on retry" in decision.reason
    assert ExitType.CONFIG_ERROR.value in IMMEDIATE_PAUSE_EXITS


def test_quarantine_wins_over_every_other_branch():
    """Ordered first so nothing below can put an injection-screened trigger back into firing."""
    for exit_type in EXIT_TYPES:
        decision = evaluate(exit_type=exit_type, consecutive_failures=0, quarantined=True)
        assert decision.state == TriggerState.QUARANTINED.value, exit_type
        assert decision.fires_automatically is False


def test_a_quarantined_trigger_is_not_resumable_from_a_button():
    """One click is too cheap a gesture for "run the thing that looked like an attack"."""
    state, refusal = resume_state(TriggerState.QUARANTINED.value)
    assert state == TriggerState.QUARANTINED.value
    assert "re-author" in refusal


def test_resume_reactivates_a_paused_or_parked_trigger():
    for state in (TriggerState.AUTOPAUSED.value, TriggerState.PARKED.value):
        new_state, refusal = resume_state(state)
        assert new_state == TriggerState.ACTIVE.value
        assert refusal == ""


def test_a_retired_trigger_is_not_resumable():
    state, refusal = resume_state(TriggerState.RETIRED.value)
    assert state == TriggerState.RETIRED.value
    assert "duplicate" in refusal


def test_only_active_fires_automatically():
    """Read through `fires_automatically`, never by checking `enabled` — that is how an autopaused
    trigger keeps firing."""
    assert evaluate(exit_type=ExitType.OK.value, consecutive_failures=0).fires_automatically is True
    for exit_type in (ExitType.FAILED.value, ExitType.CONFIG_ERROR.value):
        decision = evaluate(exit_type=exit_type, consecutive_failures=FAILURE_BUDGET)
        assert decision.fires_automatically is False


# ── Runs-inbox surfacing ──


def test_a_park_gets_no_inbox_card():
    """A park self-heals. A card the user cannot act on trains them to dismiss the surface."""
    decision = evaluate(exit_type=ExitType.AUTH_UNAVAILABLE.value, consecutive_failures=0, now=0.0)
    assert needs_attention(decision.state) is False
    assert attention_card(trigger_id="t", trigger_name="T", decision=decision) is None


def test_a_healthy_fire_gets_no_inbox_card():
    decision = evaluate(exit_type=ExitType.OK.value, consecutive_failures=0)
    assert attention_card(trigger_id="t", trigger_name="T", decision=decision) is None


def test_an_autopause_surfaces_with_the_error_and_a_resume_action():
    """A pause reason with no error text is an alert the user must go digging to act on."""
    decision = evaluate(exit_type=ExitType.FAILED.value, consecutive_failures=FAILURE_BUDGET - 1)
    card = attention_card(
        trigger_id="t1", trigger_name="Nightly backup", decision=decision, last_error="conn reset"
    )
    assert card is not None
    assert "Nightly backup" in card.title
    assert "conn reset" in card.body
    assert "resume" in card.actions


def test_a_quarantine_card_offers_no_resume():
    """`resume_state` refuses it, and a button that returns a refusal is worse than no button."""
    decision = evaluate(exit_type=ExitType.FAILED.value, consecutive_failures=0, quarantined=True)
    card = attention_card(trigger_id="t1", trigger_name="Scraper", decision=decision)
    assert card is not None
    assert "quarantined" in card.title
    assert "resume" not in card.actions
    assert "review" in card.actions


def test_a_card_falls_back_to_the_id_when_the_trigger_is_unnamed():
    decision = evaluate(exit_type=ExitType.CONFIG_ERROR.value, consecutive_failures=0)
    card = attention_card(trigger_id="t-42", trigger_name="", decision=decision)
    assert card is not None and "t-42" in card.title


def test_one_card_per_episode_but_a_new_one_on_re_entry():
    """Keyed on (trigger, state), NOT on the fire.

    Per-fire keying yields exactly one card ever, because an autopaused trigger stops firing — so a
    trigger that pauses, gets resumed, and pauses again would never surface the second time.
    """
    decision = evaluate(exit_type=ExitType.FAILED.value, consecutive_failures=FAILURE_BUDGET - 1)
    first = attention_card(trigger_id="t1", trigger_name="N", decision=decision)
    assert first is not None
    seen = {first.fingerprint}
    repeat = attention_card(trigger_id="t1", trigger_name="N", decision=decision)
    assert repeat is not None
    assert is_duplicate_card(repeat.fingerprint, seen) is True

    quarantined = evaluate(
        exit_type=ExitType.FAILED.value, consecutive_failures=0, quarantined=True
    )
    other = attention_card(trigger_id="t1", trigger_name="N", decision=quarantined)
    assert other is not None
    assert is_duplicate_card(other.fingerprint, seen) is False


def test_fingerprints_are_per_trigger():
    a = inbox_fingerprint("t1", TriggerState.AUTOPAUSED.value)
    b = inbox_fingerprint("t2", TriggerState.AUTOPAUSED.value)
    assert a != b


def test_needs_attention_excludes_working_and_self_healing_states():
    assert needs_attention(TriggerState.AUTOPAUSED.value) is True
    assert needs_attention(TriggerState.QUARANTINED.value) is True
    for state in (
        TriggerState.ACTIVE.value,
        TriggerState.PARKED.value,
        TriggerState.PAUSED.value,
        TriggerState.RETIRED.value,
    ):
        assert needs_attention(state) is False, state


def test_a_user_paused_trigger_never_shows_as_needing_attention():
    """`paused` is a user decision; `autopaused` is the system reporting failures.

    Showing both as attention-worthy would make the user look for a problem they created on purpose.
    """
    assert needs_attention(TriggerState.PAUSED.value) is False


def test_decision_round_trips_for_persistence():
    decision = evaluate(exit_type=ExitType.FAILED.value, consecutive_failures=1)
    d = decision.to_dict()
    assert d["state"] == TriggerState.ACTIVE.value
    assert d["consecutive_failures"] == 2
    assert d["health_status"] == TriggerHealth.DEGRADED.value
    assert d["reason"]


# ── the exception classifier ──


@pytest.mark.parametrize(
    "exc,expected",
    [
        (ConnectionResetError("reset by peer"), ExitType.TRANSPORT_UNAVAILABLE.value),
        (TimeoutError("timed out"), ExitType.TRANSPORT_UNAVAILABLE.value),
        (ConnectionError("no route"), ExitType.TRANSPORT_UNAVAILABLE.value),
        (RuntimeError("401 Unauthorized"), ExitType.AUTH_UNAVAILABLE.value),
        (RuntimeError("invalid api key"), ExitType.AUTH_UNAVAILABLE.value),
        (RuntimeError("expired token for provider"), ExitType.AUTH_UNAVAILABLE.value),
        (ValueError("unknown action provider 'nope'"), ExitType.CONFIG_ERROR.value),
        (ValueError("missing required field"), ExitType.CONFIG_ERROR.value),
        (RuntimeError("something odd happened"), ExitType.FAILED.value),
        (None, ExitType.FAILED.value),
    ],
)
def test_classify_exception(exc, expected):
    assert classify_exception(exc) == expected


def test_auth_is_classified_before_transport():
    """An expired credential often arrives as an HTTP error whose TYPE is a transport class.

    Reading it as transport would tell the user to check their network when the fix is to
    re-authenticate. Both park, so only the explanation differs — which is the whole value.
    """

    class ClientConnectorError(Exception):
        pass

    exc = ClientConnectorError("403 Forbidden")
    assert classify_exception(exc) == ExitType.AUTH_UNAVAILABLE.value


def test_an_unclassified_exception_is_a_failure_not_an_outage():
    """Fail-safe: defaulting to a parking exit would let broken work retry forever."""
    assert classify_exception(Exception("")) == ExitType.FAILED.value


def test_an_unknown_exit_type_still_fails_closed():
    """A caller's typo must not become a benign skip."""
    decision = evaluate(exit_type="cnofig_error", consecutive_failures=FAILURE_BUDGET - 1)
    assert decision.state == TriggerState.AUTOPAUSED.value


# 🔴 The legacy-parity block retired with `ScheduleService` (S112). It drove
# `GatewayOrchestrator._maybe_autopause` directly, as a reference the substrate had to match —
# and both that method and the dispatcher that called it are gone. Every contract it pinned is
# covered above against `autopause.evaluate`: the 5-failure budget
# (`test_the_shipped_budget_is_preserved_exactly`), config-error-on-first-fire
# (`test_a_config_error_pauses_on_the_first_fire`), outage parking that never touches `enabled`
# (`test_parking_never_autopauses_no_matter_how_long_the_outage`), and a policy refusal never
# spending the budget (`test_a_policy_refusal_never_spends_the_budget`) — which is the DEFECT the
# legacy pair carried: one counter incremented at four call sites with no way to tell a policy
# block from a real failure.


# ── 🔴 the per-trigger failure budget was declared and never read (S160) ──


def _with_policy(policy):
    from personalclaw.triggers.models import Trigger

    t = Trigger(id="t", name="t", kind="clock")
    t.failure_policy = policy
    return t


def test_a_DECLARED_autopause_after_is_HONOURED():
    """🔴 THE DEFECT. §1.1 declares `failure_policy: {autopause_after: 5, dedupe_hash: true}` and
    `evaluate` has always accepted `budget=` — the fire path never passed one, so `autopause_after`
    had **zero readers anywhere in the tree**.

    Measured: a trigger declaring `{"autopause_after": 2}` stayed ACTIVE at streaks 1, 2 and 3 and
    paused at 4. An author who asked to stop after two failures got five. The direction is
    what makes it invisible: it silently WIDENS a tolerance its author narrowed, and a
    trigger that keeps running looks exactly like a healthy one.
    """
    from personalclaw.triggers.autopause import budget_for, evaluate

    trigger = _with_policy({"autopause_after": 2})
    assert budget_for(trigger) == 2
    budget = budget_for(trigger)
    assert evaluate(exit_type="failed", consecutive_failures=0, budget=budget).state == "active"
    assert evaluate(exit_type="failed", consecutive_failures=1, budget=budget).state == "autopaused"


def test_NO_policy_keeps_the_shipped_default():
    """The control case, and the compatibility guarantee: every trigger authored before this session
    behaves exactly as it did."""
    from personalclaw.triggers.autopause import FAILURE_BUDGET, budget_for

    assert budget_for(_with_policy({})) == FAILURE_BUDGET
    assert budget_for(_with_policy(None)) == FAILURE_BUDGET
    assert budget_for(_with_policy("nope")) == FAILURE_BUDGET


def test_a_MALFORMED_budget_falls_back_to_the_DEFAULT_not_to_ONE():
    """🔴 The direction that matters. `evaluate` floors at `max(1, budget)`, so coercing a bad value
    to 0 would mean "pause on the FIRST failure" — turning a typo into an automation that stops the
    first time anything goes wrong. Falling back to the shipped tolerance is the only reading that
    cannot surprise."""
    from personalclaw.triggers.autopause import FAILURE_BUDGET, budget_for

    for junk in ("two", None, [], {}, 0, -3, 0.0):
        assert budget_for(_with_policy({"autopause_after": junk})) == FAILURE_BUDGET, junk
    # …and a well-formed narrow value still binds, so failing back is not a blanket excuse.
    assert budget_for(_with_policy({"autopause_after": 1})) == 1


def test_the_fire_path_PASSES_the_per_trigger_budget():
    """The wiring — the defect was a missing argument, which source inspection sees exactly."""
    import inspect

    from personalclaw import gateway

    assert "budget=autopause.budget_for(trigger)" in inspect.getsource(gateway)


def test_the_reason_string_reports_the_REAL_budget():
    """`evaluate`'s reasons already interpolate the budget (`failure 1 of 5`), so before the wiring
    they confidently quoted a number the trigger had not asked for. The user is being told why their
    automation stopped; that sentence has to be true."""
    from personalclaw.triggers.autopause import evaluate

    degraded = evaluate(exit_type="failed", consecutive_failures=0, budget=3)
    assert "of 3" in degraded.reason
    paused = evaluate(exit_type="failed", consecutive_failures=2, budget=3)
    assert "3 consecutive failures" in paused.reason
