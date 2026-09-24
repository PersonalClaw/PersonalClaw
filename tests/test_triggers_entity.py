"""The Trigger entity, its specs, and the fire records (AUTOMATION-SUBSTRATE §1 — S62).

Session 62 is the ENTITY layer only: the record, the per-kind specs, the typed fire outcomes. No
scheduler, no dispatch, no migration — those are 63/64/66. The shape has to be settled first because
the migration is the step that cannot be redone cheaply.

**The measurement that shaped this session.** `ScheduleJob` has 33 fields and `EventTrigger` 11.
Checked against the new dataclass: **31 of the 44 have no same-named home.** A migration written
against `Trigger` alone would silently drop `skip_dates` (the trigger keeps firing on a holiday),
`strict_schedule` (a missed slot catches up when the author said not to) and `content_re` (an event
trigger fires on everything). So `LEGACY_FIELD_MAP` lands in THIS session, and the tests
below assert
coverage against the real dataclasses — a field added to `ScheduleJob` next month fails here rather
than vanishing in session 66.

**Two contracts the plan states, made checkable.** Never-throw structural validation (R15): an
agent-authored near-miss becomes a warning with a suggestion, on a row that still loads. And "silent
drops are banned" (R2): every non-clean outcome must carry a one-line reason, asserted by
`fire_issues` rather than trusted.
"""

import dataclasses as dc

import pytest

from personalclaw.triggers.models import (
    FIRE_OUTCOMES,
    GATE_KEYS,
    INERT_OUTCOMES,
    KINDS,
    LEGACY_FIELD_MAP,
    MIN_CLOCK_INTERVAL_SECS,
    SPEC_KEYS,
    TRUE_FAILURE_OUTCOMES,
    FireRecord,
    Outcome,
    RunWeight,
    Trigger,
    TriggerHealth,
    TriggerState,
    classify_weight,
    fire_issues,
    gate_failure_mode,
    parse_trigger,
    require_reason,
    unmapped_legacy_fields,
    validate_gates,
    validate_spec,
)


def _raw(**over) -> dict:
    base = {
        "id": "t-1",
        "name": "nightly backup",
        "kind": "clock",
        "spec": {"kind": "cron", "expr": "0 3 * * *"},
    }
    base.update(over)
    return base


# ── never-throw structural validation (R15) ──


def test_a_VALID_trigger_parses_with_no_issues():
    trigger, issues = parse_trigger(_raw())
    assert issues == []
    assert trigger.id == "t-1"
    assert trigger.enabled is True


def test_an_UNKNOWN_field_is_a_warning_with_a_SUGGESTION():
    """An agent that wrote `enable` for `enabled` should be told which key it meant. A validation
    error with no suggestion is how a near-miss becomes a dead row nobody diagnoses."""
    _t, issues = parse_trigger(_raw(enable=True))
    hit = next(i for i in issues if i.path == "enable")
    assert hit.severity == "warning"
    assert hit.closest == "enabled"


def test_a_NEAR_MISS_gate_name_suggests_the_real_one():
    _t, issues = parse_trigger(_raw(gates={"debounce_seconds": 30}))
    hit = next(i for i in issues if i.path == "gates.debounce_seconds")
    assert hit.closest == "debounce_secs"


def test_a_FAR_field_name_suggests_NOTHING():
    """Suggesting `timezone` for `xyzzy` is worse than suggesting nothing: the reader trusts it and
    goes looking for a relationship that is not there."""
    _t, issues = parse_trigger(_raw(xyzzy=1))
    assert next(i for i in issues if i.path == "xyzzy").closest == ""


def test_parsing_NEVER_raises_on_garbage():
    """The whole contract. A store with one broken row must still load every other row."""
    for garbage in (None, [], "nope", 42, {}):
        trigger, issues = parse_trigger(garbage)  # type: ignore[arg-type]
        assert isinstance(trigger, Trigger)
        assert issues


def test_a_STRUCTURALLY_BROKEN_trigger_loads_DISABLED():
    """It stays visible and editable — that is what makes the warning actionable — but the service
    must not try to dispatch something it cannot interpret."""
    trigger, issues = parse_trigger(_raw(kind="quantum"))
    assert trigger.enabled is False
    assert any(i.severity == "error" for i in issues)


def test_a_WARNING_alone_does_not_disable():
    """A typo in one optional key must not stop a working automation."""
    trigger, _issues = parse_trigger(_raw(gates={"debounce_seconds": 30}))
    assert trigger.enabled is True


def test_an_unknown_field_is_DROPPED_not_echoed():
    """Keeping it would make `to_dict` round-trip a field nothing reads, which is how a
    typo survives
    a save and looks supported."""
    trigger, _ = parse_trigger(_raw(enable=True))
    assert "enable" not in trigger.to_dict()


# ── the closed vocabularies ──


def test_the_PHASE_2_kinds_are_not_accepted_yet():
    """A kind the service cannot dispatch would let a user author a trigger that never fires — the
    exact failure the never-throw validation exists to prevent."""
    assert "pulse" not in KINDS
    assert "observe" not in KINDS
    _t, issues = parse_trigger(_raw(kind="pulse", spec={}))
    assert any(i.severity == "error" for i in issues)


@pytest.mark.parametrize("kind", KINDS)
def test_every_declared_kind_has_a_spec_key_set(kind):
    """A kind with no spec contract would accept anything, so nothing could be validated."""
    assert kind in SPEC_KEYS


def test_an_unknown_OVERLAP_policy_falls_back_to_skip():
    """Skip is the safe default: `parallel` on a typo would run a trigger concurrently
    with itself."""
    trigger, issues = parse_trigger(_raw(overlap="parallell"))
    assert trigger.overlap == "skip"
    assert next(i for i in issues if i.path == "overlap").closest == "parallel"


def test_an_unknown_STATE_falls_back_to_active():
    trigger, issues = parse_trigger(_raw(state="sleeping"))
    assert trigger.state == TriggerState.ACTIVE.value
    assert any(i.path == "state" for i in issues)


def test_autopaused_is_a_SEPARATE_state_from_paused():
    """A paused trigger is a user decision; an autopaused one is the system reporting five failures.
    Collapsing them makes the user look for a switch they never flipped."""
    assert TriggerState.AUTOPAUSED.value != TriggerState.PAUSED.value


# ── the clock spec (everything schedule.py carries) ──


def test_a_cron_clock_needs_an_EXPRESSION():
    issues = validate_spec("clock", {"kind": "cron"})
    assert any(i.path == "spec.expr" and i.severity == "error" for i in issues)


def test_an_at_clock_needs_a_TIME():
    issues = validate_spec("clock", {"kind": "at"})
    assert any(i.path == "spec.at" and i.severity == "error" for i in issues)


def test_a_clock_with_no_kind_is_an_error():
    assert any(i.path == "spec.kind" for i in validate_spec("clock", {}))


def test_an_unknown_clock_kind_suggests_the_real_one():
    issues = validate_spec("clock", {"kind": "chron", "expr": "* * * * *"})
    assert next(i for i in issues if i.path == "spec.kind").closest == "cron"


@pytest.mark.parametrize("key", ["timezone", "skip_dates", "strict", "jitter_secs"])
def test_the_clock_spec_carries_schedule_py_s_semantics(key):
    """These four are the ones a lossy migration loses quietly: a dropped `skip_dates` fires on a
    holiday, a dropped `strict` catches up when the author said not to."""
    assert key in SPEC_KEYS["clock"]


def test_a_spec_key_from_ANOTHER_kind_is_flagged():
    """An unrecognized spec key is the likeliest authoring mistake and has the quietest failure: the
    trigger loads, the service ignores the key, and the automation behaves inexplicably."""
    issues = validate_spec("clock", {"kind": "cron", "expr": "* * * * *", "url": "http://x"})
    assert any(i.path == "spec.url" for i in issues)


def test_the_min_clock_interval_is_declared():
    """A floor rather than a hard rule — the plan makes it overridable — but it has to exist so a
    typed `* * * * *` is not an accident that runs an LLM every minute."""
    assert MIN_CLOCK_INTERVAL_SECS == 900


# ── the other kinds ──


def test_an_event_trigger_needs_a_SOURCE():
    assert any(i.path == "spec.source" for i in validate_spec("event", {}))


def test_a_webhook_with_NO_TOKEN_is_refused_not_defaulted():
    """A generated default would be a secret nobody chose, and an unauthenticated fire endpoint is
    worse than a refusal at author time."""
    issues = validate_spec("webhook", {})
    assert any(i.path == "spec.token_ref" and i.severity == "error" for i in issues)


def test_a_web_watch_needs_a_url():
    assert any(i.path == "spec.url" for i in validate_spec("web_watch", {}))


def test_a_MANUAL_trigger_needs_no_spec():
    assert validate_spec("manual", {}) == []


def test_a_manual_trigger_never_fires_automatically():
    """Run-now/replay/dry-run only. A scheduler that woke a manual trigger would fire something the
    user explicitly said they would start themselves."""
    trigger, _ = parse_trigger(_raw(kind="manual", spec={}))
    assert trigger.fires_automatically is False


def test_a_DISABLED_trigger_never_fires_automatically():
    trigger, _ = parse_trigger(_raw(enabled=False))
    assert trigger.fires_automatically is False


def test_an_AUTOPAUSED_trigger_never_fires_automatically():
    """Checking `enabled` without `state` is how an autopaused trigger keeps firing — so
    the question
    is asked once, in one place."""
    trigger, _ = parse_trigger(_raw(state="autopaused"))
    assert trigger.fires_automatically is False


# ── gates, and their failure modes ──


def test_an_unknown_GATE_is_flagged_as_never_enforced():
    """A gate the service does not read is a safety control the user believes they set."""
    issues = validate_gates({"max_spend": 5})
    assert any("never enforced" in i.message for i in issues)


def test_budget_and_storm_gates_FAIL_OPEN():
    """R3's amendment. A budget probe that hangs must not silently stop every automation on the
    machine."""
    for gate in ("cost_cap", "max_runs_per_hour", "rate_cap", "condition"):
        assert gate_failure_mode(gate) == "open"


def test_an_UNCLASSIFIED_gate_fails_CLOSED():
    """The safe direction for a control whose semantics nobody wrote down: refuse the fire rather
    than wave it through."""
    assert gate_failure_mode("some_new_gate") == "closed"


def test_the_security_relevant_gates_are_NOT_in_the_fail_open_set():
    """Capabilities, the injection screen and fencing fail closed: the cost of skipping them is
    unbounded, while the cost of a skipped budget check is one extra run."""
    from personalclaw.triggers.models import FAIL_OPEN_GATES

    assert "idempotency" not in FAIL_OPEN_GATES


def test_every_declared_gate_is_in_the_vocabulary():
    assert "quiet_hours" in GATE_KEYS
    assert "skip_dates" in GATE_KEYS


# ── fire records: no silent drops (R2) ──


def test_every_non_clean_outcome_MUST_carry_a_reason():
    """The rule is only real if something checks it: a suppression written without a
    reason satisfies
    the type and defeats the purpose."""
    for outcome in FIRE_OUTCOMES:
        record = FireRecord(id="f", trigger_id="t", outcome=outcome)
        needs = require_reason(outcome)
        has_error = any(i.path == "reason" for i in fire_issues(record))
        assert has_error is needs, f"{outcome}: reason-required={needs} but check said {has_error}"


def test_a_clean_run_needs_no_reason():
    assert fire_issues(FireRecord(id="f", trigger_id="t", outcome=Outcome.RAN.value)) == []


def test_ran_late_is_only_meaningful_beside_its_MISSED_SLOT():
    """A run that started 40 minutes after its slot is a different story from one that
    started on time
    and took 40 minutes."""
    record = FireRecord(id="f", trigger_id="t", outcome=Outcome.RAN_LATE.value, reason="woke late")
    assert any(i.path == "scheduled_for" for i in fire_issues(record))
    record.scheduled_for = "2026-08-03T03:00:00Z"
    assert fire_issues(record) == []


def test_an_UNKNOWN_outcome_reads_as_FAILED_not_ran():
    """A row this build cannot classify must not count as a success — a success is what the health
    rollup and the "what did my machine do" view treat as nothing to look at."""
    assert FireRecord.from_dict({"outcome": "vibes"}).outcome == Outcome.FAILED.value


def test_only_a_TRUE_failure_counts_toward_autopause():
    """Five skipped fires because quiet hours held is the configuration working.
    Autopausing for that
    would punish the user for saying "not at night"."""
    assert TRUE_FAILURE_OUTCOMES == {Outcome.FAILED.value}
    assert (
        FireRecord(
            id="f", trigger_id="t", outcome=Outcome.SKIPPED_GATE.value, reason="quiet hours"
        ).counts_toward_autopause
        is False
    )
    assert (
        FireRecord(
            id="f", trigger_id="t", outcome=Outcome.FAILED.value, reason="boom"
        ).counts_toward_autopause
        is True
    )


def test_PRODUCTIVITY_is_the_materiality_predicate_not_the_outcome():
    """§1.3 is explicit: the classification criterion is "did it mutate durable state". A
    view built on
    the outcome alone would show a page of runs that changed nothing."""
    ran_but_inert = FireRecord(id="f", trigger_id="t", outcome=Outcome.RAN.value, mutated=False)
    ran_and_wrote = FireRecord(id="f", trigger_id="t", outcome=Outcome.RAN.value, mutated=True)
    assert ran_but_inert.productive is False
    assert ran_and_wrote.productive is True


def test_the_inert_outcomes_are_the_ones_that_spent_NOTHING():
    assert Outcome.SKIPPED_BUDGET.value in INERT_OUTCOMES
    assert Outcome.FAILED.value not in INERT_OUTCOMES
    assert Outcome.RAN.value not in INERT_OUTCOMES


def test_a_fire_record_ROUND_TRIPS():
    original = FireRecord(
        id="f-1",
        trigger_id="t-1",
        outcome=Outcome.RAN.value,
        weight=RunWeight.FULL.value,
        mutated=True,
        counters={"items": 3},
        incomplete=True,
    )
    assert FireRecord.from_dict(original.to_dict()) == original


def test_the_flywheel_feedback_fields_are_PRE_ALLOCATED():
    """Reserved now because adding them later would mean a migration over existing history."""
    names = {f.name for f in dc.fields(FireRecord)}
    assert {"acted_on", "dismissed"} <= names


def test_incomplete_marks_a_count_that_was_CUT_SHORT():
    """So a reader is never misled by a number that stopped early — "at least N", not "N"."""
    assert FireRecord(id="f", trigger_id="t", outcome=Outcome.RAN.value, incomplete=True).incomplete


# ── record weight ──


def test_a_single_action_fire_is_a_LEDGER_row():
    """This is what keeps a minutely trigger from producing 1440 run directories a day."""
    assert classify_weight(node_count=1, has_llm=False, resumable=False) == RunWeight.LEDGER.value


@pytest.mark.parametrize(
    "kw",
    [
        {"node_count": 2, "has_llm": False, "resumable": False},
        {"node_count": 1, "has_llm": True, "resumable": False},
        {"node_count": 1, "has_llm": False, "resumable": True},
    ],
)
def test_anything_multi_node_llm_or_resumable_is_FULL(kw):
    """Those need a directory and a journal to be diagnosable at all."""
    assert classify_weight(**kw) == RunWeight.FULL.value


# ── the migration map (what makes session 66 lossless) ──


def test_EVERY_ScheduleJob_field_is_accounted_for():
    """The measurement that shaped this session: 31 of 44 legacy fields have no same-named home on
    `Trigger`. A field with no map entry is one a migration drops silently."""
    from personalclaw.schedule import ScheduleJob

    names = [f.name for f in dc.fields(ScheduleJob)]
    assert unmapped_legacy_fields("ScheduleJob", names) == []


def test_EVERY_EventTrigger_field_is_accounted_for():
    from personalclaw.event_triggers import EventTrigger

    names = [f.name for f in dc.fields(EventTrigger)]
    assert unmapped_legacy_fields("EventTrigger", names) == []


def test_a_NEW_legacy_field_fails_here_rather_than_vanishing_later():
    """The point of running the check against the real dataclass: a field added to
    `ScheduleJob` after
    this map was written must break a test, not the migration."""
    assert unmapped_legacy_fields("ScheduleJob", ["a_field_nobody_mapped"]) == [
        "a_field_nobody_mapped"
    ]


@pytest.mark.parametrize("key", ["skip_dates", "strict_schedule", "timezone", "delete_after_run"])
def test_the_QUIETLY_LOSABLE_schedule_fields_map_somewhere_real(key):
    """Each of these fails silently when dropped: a holiday fire, a catch-up the author refused, a
    run in the wrong timezone, a one-shot that resurrects."""
    assert LEGACY_FIELD_MAP["ScheduleJob"][key]


@pytest.mark.parametrize("key", ["content_re", "key_glob", "max_fires", "debounce_secs"])
def test_the_event_trigger_MATCHERS_and_guards_map_somewhere_real(key):
    """A dropped `content_re` makes an event trigger fire on everything — the loudest possible
    quiet failure."""
    assert LEGACY_FIELD_MAP["EventTrigger"][key]


def test_a_DELIBERATE_drop_is_recorded_as_None_with_a_reason():
    """An unexplained omission is indistinguishable from an oversight when someone reads this in six
    months, so the map distinguishes "dropped on purpose" from "not thought about"."""
    assert LEGACY_FIELD_MAP["ScheduleJob"]["last_result"] is None
    assert LEGACY_FIELD_MAP["ScheduleJob"]["acked_items"] is None


# ── the entity round trip ──


def test_a_trigger_round_trips_through_to_dict():
    trigger, _ = parse_trigger(
        _raw(
            gates={"debounce_secs": 30, "quiet_hours": {"from": "22:00", "to": "07:00"}},
            capabilities={"allowed_actions": ["bash"], "network": False},
            workflow={"ref": "nightly-backup"},
            resource_slots=["local-llm"],
            skip_if_active={"dirty_git": ".", "lockfiles": ["run.lock"], "recent_secs": 120},
            catch_up=True,
        )
    )
    reparsed, issues = parse_trigger(trigger.to_dict())
    assert issues == []
    assert reparsed == trigger


def test_skip_if_active_defaults_to_off_and_round_trips():
    """The default is an empty dict — a trigger that does not opt in is never deferred — and any
    declared guard survives `to_dict`→`parse_trigger` so its reader (the `active` gate) sees it."""
    default, _ = parse_trigger(_raw())
    assert default.skip_if_active == {}
    guarded, issues = parse_trigger(_raw(skip_if_active={"paths": ["notes/*"], "recent_secs": 300}))
    assert issues == []
    assert guarded.skip_if_active == {"paths": ["notes/*"], "recent_secs": 300}
    assert parse_trigger(guarded.to_dict())[0] == guarded


def test_failure_delivery_defaults_to_the_INBOX_even_when_delivery_is_none():
    """An automation the user asked to stay quiet still has to be able to say it broke."""
    trigger, _ = parse_trigger(_raw(delivery="none"))
    assert trigger.delivery == "none"
    assert trigger.failure_delivery == "inbox"


def test_the_health_rollups_live_ON_the_row():
    """R7: computing them per render means reading every run of every trigger to draw one page of
    status dots."""
    names = {f.name for f in dc.fields(Trigger)}
    assert {"last_success_at", "last_failure_at", "health_status", "last_error_summary"} <= names
    assert TriggerHealth.OK.value == "ok"
