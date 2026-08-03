"""S70 — quiet windows, the duty-gate seam, the week grid, and `automation doctor` (AUTO-A1/A2).

`gates.quiet_hours` has been a RESERVED key with no semantics since S62: declared in `GATE_KEYS`,
accepted by validation, consulted by nothing. So the first test here is the one that matters most —
`test_wrap_semantics_match_the_shipped_notification_matcher`.
`providers/entity_routes._in_quiet_window`
already answers "is 23:00 inside 22:00→08:00" for notifications, and two different answers to that
question on one machine would be a bug nobody could explain.

What the shipped matcher CANNOT express, measured: no day-of-week, one window per call, server-local
minutes with no timezone, and a bare bool with no catch-up-or-skip resolution. That gap is this
session's scope, and the wrap semantics are preserved verbatim across it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from personalclaw.triggers.calendar import (
    BROAD_GLOB_SEGMENTS,
    DAY_ALIASES,
    DAYS,
    DUTY_GATE_TIMEOUT_SECS,
    MAX_OCCURRENCES_PER_TRIGGER,
    DutyVerdict,
    GateOutcome,
    QuietResolution,
    QuietWindow,
    _is_broad_glob,
    apply_defaults,
    clear_duty_gates,
    diagnose,
    duty_gate_names,
    evaluate_duty,
    evaluate_quiet,
    in_quiet_window,
    parse_default_window,
    parse_hhmm,
    parse_windows,
    project_occurrences,
    register_duty_gate,
    resolution_of,
    window_closes_at,
)

MONDAY = datetime(2024, 1, 1)  # a Monday, so weekday() arithmetic is readable
FRIDAY = datetime(2024, 1, 5)
SATURDAY = datetime(2024, 1, 6)


@pytest.fixture(autouse=True)
def _restore_duty_gates():
    """The duty-gate registry is process-global.

    A test that registers a gate and does not restore leaks it into every later test on the same
    xdist
    worker — the exact class of failure this program has already paid for twice (the SEL
    singleton and
    the provider registry).
    """
    import personalclaw.triggers.calendar as cal

    saved = dict(cal._DUTY_GATES)
    yield
    cal._DUTY_GATES.clear()
    cal._DUTY_GATES.update(saved)


# ── the contract with the shipped matcher ──


def test_wrap_semantics_match_the_shipped_notification_matcher():
    """THE compatibility test.

    `providers/entity_routes._in_quiet_window` is the shipped answer for notifications. Two
    different
    answers to "is 23:00 inside 22:00→08:00" on one machine would be a bug nobody could explain, so
    the wrap rule is asserted identical across every half hour of the day.
    """
    from personalclaw.providers.entity_routes import _in_quiet_window

    windows = [QuietWindow(start="22:00", end="08:00")]
    for hour in range(24):
        for minute in (0, 30):
            mine = in_quiet_window(windows, MONDAY.replace(hour=hour, minute=minute)) is not None
            theirs = _in_quiet_window("22:00", "08:00", hour * 60 + minute)
            assert mine == theirs, f"divergence at {hour:02d}:{minute:02d}"


def test_a_zero_length_window_never_matches():
    """Same rule as the shipped matcher: start == end suppresses nothing."""
    assert in_quiet_window([QuietWindow(start="22:00", end="22:00")], MONDAY) is None


def test_a_normal_window_is_half_open():
    """`[start, end)` — the end minute is NOT suppressed.

    Matches the shipped matcher. An inclusive end would suppress one extra minute, which is the kind
    of off-by-one that surfaces as "my 17:00 job never runs".
    """
    windows = [QuietWindow(start="09:00", end="17:00")]
    assert in_quiet_window(windows, MONDAY.replace(hour=9)) is not None
    assert in_quiet_window(windows, MONDAY.replace(hour=16, minute=59)) is not None
    assert in_quiet_window(windows, MONDAY.replace(hour=17)) is None


# ── the three dimensions the shipped matcher lacks ──


def test_day_of_week_restricts_a_window():
    windows = [QuietWindow(start="09:00", end="17:00", days=("sat", "sun"))]
    assert in_quiet_window(windows, SATURDAY.replace(hour=10)) is not None
    assert in_quiet_window(windows, MONDAY.replace(hour=10)) is None


def test_a_friday_night_window_carries_into_saturday_morning():
    """The day check applies to the day the window STARTED on.

    For a Friday-night 22:00→08:00 band, 02:00 Saturday is still inside it. Reading the Saturday
    date
    instead would end the suppression at midnight, which is not what "Friday night" means to anyone.
    """
    windows = [QuietWindow(start="22:00", end="08:00", days=("fri",))]
    assert in_quiet_window(windows, FRIDAY.replace(hour=23)) is not None
    assert in_quiet_window(windows, SATURDAY.replace(hour=2)) is not None
    # But Saturday NIGHT is not a Friday window.
    assert in_quiet_window(windows, SATURDAY.replace(hour=23)) is None


def test_several_windows_can_apply_to_one_trigger():
    windows, issues = parse_windows(
        [{"start": "12:00", "end": "13:00"}, {"start": "22:00", "end": "08:00"}]
    )
    assert not issues and len(windows) == 2
    assert in_quiet_window(windows, MONDAY.replace(hour=12, minute=30)) is not None
    assert in_quiet_window(windows, MONDAY.replace(hour=23)) is not None
    assert in_quiet_window(windows, MONDAY.replace(hour=15)) is None


# ── parsing ──


@pytest.mark.parametrize(
    "value,expected",
    [("00:00", 0), ("09:30", 570), ("23:59", 1439), ("7:05", 425)],
)
def test_parse_hhmm(value, expected):
    assert parse_hhmm(value) == expected


@pytest.mark.parametrize("value", ["", "24:00", "22:60", "10", "abc", "22:0", None])
def test_parse_hhmm_returns_none_rather_than_defaulting_to_midnight(value):
    """A malformed time that silently became 00:00 would suppress a band nobody configured."""
    assert parse_hhmm(value) is None


def test_parse_windows_accepts_all_three_shapes():
    """The reserved key had no established form; all three are things a person plausibly writes."""
    single, _ = parse_windows({"start": "22:00", "end": "08:00"})
    listed, _ = parse_windows([{"start": "22:00", "end": "08:00"}])
    full, _ = parse_windows({"windows": [{"start": "22:00", "end": "08:00"}], "resolution": "skip"})
    assert len(single) == len(listed) == len(full) == 1


def test_an_invalid_window_is_dropped_with_an_issue():
    """Never promoted to "suppress everything".

    A malformed band that accidentally matched all day would look exactly like a broken scheduler.
    """
    windows, issues = parse_windows({"start": "25:00", "end": "08:00"})
    assert windows == []
    assert issues and "invalid" in issues[0]


def test_a_resolution_only_block_is_not_a_malformed_window():
    """Found while probing the doctor.

    `{"resolution": "catch_up"}` declares no window at all. Treating it as one malformed window
    reported a spurious `invalid_quiet_window` on top of the real `catch_up_without_quiet_hours`
    finding — two complaints for one mistake, with the wrong one first.
    """
    windows, issues = parse_windows({"resolution": "catch_up"})
    assert windows == [] and issues == []


def test_day_aliases_expand_at_parse_time():
    windows, issues = parse_windows({"start": "09:00", "end": "17:00", "days": ["weekends"]})
    assert not issues
    assert windows[0].days == DAY_ALIASES["weekends"]


def test_an_unknown_day_is_reported_and_the_window_still_applies():
    windows, issues = parse_windows({"start": "09:00", "end": "17:00", "days": ["mon", "funday"]})
    assert windows and windows[0].days == ("mon",)
    assert any("funday" in issue for issue in issues)


def test_no_valid_days_falls_back_to_every_day():
    """A window that matched nothing is a config that silently does nothing."""
    windows, issues = parse_windows({"start": "09:00", "end": "17:00", "days": ["nope"]})
    assert windows and windows[0].days == DAYS
    assert issues


def test_a_non_object_quiet_hours_is_reported():
    windows, issues = parse_windows("22:00-08:00")
    assert windows == [] and issues


# ── resolution: skip vs catch_up ──


def test_skip_is_the_default_because_it_is_reversible():
    """A skipped fire is one missing run the user can trigger by hand; an unwanted catch-up is an
    action already taken."""
    assert resolution_of({}) == QuietResolution.SKIP.value
    assert resolution_of({"start": "22:00", "end": "08:00"}) == QuietResolution.SKIP.value
    assert resolution_of({"resolution": "nonsense"}) == QuietResolution.SKIP.value


def test_catch_up_is_honoured_when_asked_for():
    assert resolution_of({"resolution": "catch_up"}) == QuietResolution.CATCH_UP.value


def test_a_catch_up_lands_after_the_window_closes_never_inside_it():
    """The single most likely bug in this file.

    A catch-up scheduled as "now + an hour" from inside a 10-hour window lands back inside it, so
    the
    fire never happens. Computed from the window's end instead.
    """
    gates = {"quiet_hours": {"start": "22:00", "end": "08:00", "resolution": "catch_up"}}
    decision, _issues = evaluate_quiet(gates, MONDAY.replace(hour=23, minute=30))
    assert decision.outcome == GateOutcome.QUIET.value
    assert decision.catch_up_at > 0
    landing = datetime.fromtimestamp(decision.catch_up_at)
    assert in_quiet_window([QuietWindow("22:00", "08:00")], landing) is None


def test_window_closes_at_rolls_to_tomorrow_when_the_end_has_passed():
    window = QuietWindow(start="22:00", end="08:00")
    close = window_closes_at(window, MONDAY.replace(hour=23))
    assert close.day == MONDAY.day + 1 and close.hour == 8


def test_a_skip_resolution_records_no_catch_up_time():
    gates = {"quiet_hours": {"start": "22:00", "end": "08:00"}}
    decision, _ = evaluate_quiet(gates, MONDAY.replace(hour=23))
    assert decision.catch_up_at == 0.0
    assert "dropped" in decision.reason


def test_a_suppression_reason_names_the_window():
    """S62's `require_reason`: "skipped_gate" alone does not say WHICH gate or which window."""
    gates = {"quiet_hours": {"start": "22:00", "end": "08:00"}}
    decision, _ = evaluate_quiet(gates, MONDAY.replace(hour=23))
    assert "22:00" in decision.reason and "08:00" in decision.reason


def test_no_quiet_hours_allows_every_moment():
    for hour in range(24):
        decision, _ = evaluate_quiet({}, MONDAY.replace(hour=hour))
        assert decision.allowed


# ── the duty gate: fail-open, time-boxed ──


def test_the_builtin_manual_gate_is_registered():
    assert "manual" in duty_gate_names()


@pytest.mark.asyncio
async def test_the_manual_gate_defaults_to_on_duty():
    """Default-ON because this gate ships enabled-by-name in core.

    Defaulting to off-duty would silence every automation of anyone who named the gate without
    setting the flag.
    """
    decision = await evaluate_duty({"duty_gate": {"provider": "manual"}}, MONDAY)
    assert decision.allowed


@pytest.mark.asyncio
async def test_the_manual_gate_suppresses_when_toggled_off():
    decision = await evaluate_duty(
        {"duty_gate": {"provider": "manual", "config": {"on_duty": False}}}, MONDAY
    )
    assert decision.outcome == GateOutcome.OFF_DUTY.value
    assert decision.reason


@pytest.mark.asyncio
async def test_no_gate_configured_allows():
    assert (await evaluate_duty({}, MONDAY)).allowed
    assert (await evaluate_duty({"duty_gate": {}}, MONDAY)).allowed
    assert (await evaluate_duty({"duty_gate": "manual"}, MONDAY)).allowed  # wrong shape


@pytest.mark.asyncio
async def test_an_unknown_gate_FAILS_OPEN():
    """Uninstalling a calendar app must not silently stop every automation that referenced it."""
    decision = await evaluate_duty({"duty_gate": {"provider": "ghost"}}, MONDAY)
    assert decision.allowed
    assert "not registered" in decision.reason


@pytest.mark.asyncio
async def test_a_raising_gate_FAILS_OPEN():
    """A broken third-party gate must not become a global kill switch."""

    async def boom(now, config):
        raise RuntimeError("calendar app exploded")

    register_duty_gate("boom", boom)
    decision = await evaluate_duty({"duty_gate": {"provider": "boom"}}, MONDAY)
    assert decision.allowed
    assert "failed" in decision.reason


@pytest.mark.asyncio
async def test_a_hanging_gate_TIMES_OUT_AND_FAILS_OPEN():
    """This runs on EVERY fire, so a hanging gate would stall the whole automation surface."""
    import asyncio

    async def hangs(now, config):
        await asyncio.sleep(30)
        return DutyVerdict(on_duty=False)

    register_duty_gate("hangs", hangs)
    decision = await evaluate_duty({"duty_gate": {"provider": "hangs"}}, MONDAY, timeout=0.1)
    assert decision.allowed
    assert "did not answer" in decision.reason


@pytest.mark.asyncio
async def test_only_an_explicit_off_duty_suppresses():
    """The asymmetry that IS the fail-open classification.

    The gate can refine WHEN automations run; it cannot become the reason they all stopped.
    """

    async def off(now, config):
        return DutyVerdict(on_duty=False, reason="in a meeting until 11")

    register_duty_gate("off", off)
    decision = await evaluate_duty({"duty_gate": {"provider": "off"}}, MONDAY)
    assert not decision.allowed
    assert decision.reason == "in a meeting until 11"


@pytest.mark.asyncio
async def test_an_off_duty_verdict_without_a_reason_still_gets_one():
    """An unexplained suppression is indistinguishable from a bug."""

    async def terse(now, config):
        return DutyVerdict(on_duty=False)

    register_duty_gate("terse", terse)
    decision = await evaluate_duty({"duty_gate": {"provider": "terse"}}, MONDAY)
    assert decision.reason


def test_the_timeout_is_short_enough_to_run_on_every_fire():
    assert DUTY_GATE_TIMEOUT_SECS <= 5.0


def test_clear_duty_gates_is_available_for_tests():
    clear_duty_gates()
    assert duty_gate_names() == []


# ── the #47 rule: PROVIDER_TYPES and the handler land together ──


def test_duty_gate_is_a_declarable_provider_type():
    from personalclaw.apps.manifest import PROVIDER_TYPES

    assert "duty_gate" in PROVIDER_TYPES


def test_duty_gate_has_a_live_type_handler():
    """A manifest type with no runtime handler installs successfully and then does nothing."""
    import inspect

    from personalclaw.providers import registry

    src = inspect.getsource(registry)
    assert 'register_type_handler("duty_gate"' in src
    assert "class DutyGateTypeHandler" in src


def test_a_duty_gate_provider_must_expose_on_duty():
    """Refused at registration, not at fire time.

    A gate that registers and then fails on every fire fails OPEN — so the automation runs
    unfiltered, which is the opposite of what its author asked for. Catching the shape here makes it
    an install error instead.
    """
    from personalclaw.apps.manifest import ProviderConfig
    from personalclaw.providers.registry import DutyGateTypeHandler, RegisteredProvider

    handler = DutyGateTypeHandler()
    ext = RegisteredProvider(
        name="broken",
        manifest=None,  # type: ignore[arg-type]
        provider_config=ProviderConfig(type="duty_gate", implementation="mod:make"),
    )
    with pytest.raises(ValueError, match="on_duty"):
        handler.register(ext, object())


# ── gate classification ──


def test_duty_gate_is_a_recognized_gate_key():
    from personalclaw.triggers.models import GATE_KEYS

    assert "duty_gate" in GATE_KEYS


def test_duty_gate_is_classified_FAIL_OPEN():
    """§1.4: it calls out to a provider, so it must not become a kill switch."""
    from personalclaw.triggers.models import FAIL_OPEN_GATES, gate_failure_mode

    assert "duty_gate" in FAIL_OPEN_GATES
    assert gate_failure_mode("duty_gate") == "open"


def test_quiet_hours_is_not_fail_open_because_it_cannot_hang():
    """Local arithmetic always produces an answer, so there is no failure mode to classify."""
    from personalclaw.triggers.models import gate_failure_mode

    assert gate_failure_mode("quiet_hours") == "closed"


# ── config defaults (the fifth config point) ──


@pytest.mark.parametrize(
    "value,expected",
    [
        ("22:00-08:00", ("22:00", "08:00")),
        ("22:00 to 08:00", ("22:00", "08:00")),
        ("09:00–17:00", ("09:00", "17:00")),  # en dash
    ],
)
def test_parse_default_window_accepts_the_compact_config_form(value, expected):
    window = parse_default_window(value)
    assert window is not None
    assert (window.start, window.end) == expected


@pytest.mark.parametrize("value", ["", "garbage", "25:00-08:00", "22:00", "-", None])
def test_a_malformed_default_window_means_NO_default(value):
    """Fail-safe: a window that accidentally matched all day would suppress every automation and
    look exactly like a broken scheduler."""
    assert parse_default_window(value) is None


def test_a_triggers_own_setting_always_beats_the_default(monkeypatch):
    """An explicitly EMPTY value counts as a setting.

    Someone who cleared their quiet hours meant to clear them; re-applying the global default would
    override a deliberate choice with a fallback.
    """
    monkeypatch.setattr(
        "personalclaw.triggers.calendar.default_quiet_window",
        lambda: QuietWindow(start="22:00", end="08:00"),
    )
    monkeypatch.setattr("personalclaw.triggers.calendar.default_duty_gate", lambda: "manual")
    assert apply_defaults({"quiet_hours": {}})["quiet_hours"] == {}
    assert apply_defaults({"duty_gate": {}})["duty_gate"] == {}


def test_defaults_fill_only_an_absent_key(monkeypatch):
    monkeypatch.setattr(
        "personalclaw.triggers.calendar.default_quiet_window",
        lambda: QuietWindow(start="22:00", end="08:00"),
    )
    monkeypatch.setattr("personalclaw.triggers.calendar.default_duty_gate", lambda: "manual")
    filled = apply_defaults({"debounce_secs": 5})
    assert filled["debounce_secs"] == 5
    assert filled["quiet_hours"]["start"] == "22:00"
    assert filled["duty_gate"] == {"provider": "manual", "config": {}}


def test_the_config_fields_round_trip():
    """The standard four-point contract."""
    from personalclaw.config.loader import WorkflowsConfig

    config = WorkflowsConfig()
    assert config.default_quiet_windows == ""
    assert config.duty_gate_default == ""
    assert "default_quiet_windows" in WorkflowsConfig.__dataclass_fields__
    assert "duty_gate_default" in WorkflowsConfig.__dataclass_fields__


def test_the_config_fields_are_patchable():
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    assert "workflows.default_quiet_windows" in _EDITABLE_CONFIG
    assert "workflows.duty_gate_default" in _EDITABLE_CONFIG


# ── the week grid ──


def test_suppressed_slots_are_annotated_not_filtered():
    """A grid that hid suppressed fires would show a schedule the user does not have.

    Explaining why a trigger is NOT firing when they expect it to is the whole point of the view.
    """
    occurrences, truncated = project_occurrences(
        trigger_id="t",
        trigger_name="Hourly",
        interval_secs=3600,
        first_fire_at=MONDAY.timestamp(),
        start=MONDAY,
        days=1,
        gates={"quiet_hours": {"start": "22:00", "end": "08:00"}},
    )
    assert not truncated
    assert len(occurrences) == 24
    suppressed = [o for o in occurrences if o.suppressed_by]
    assert len(suppressed) == 10  # 22,23,00..07
    assert all(o.reason for o in suppressed)


def test_the_grid_is_capped_and_reports_truncation():
    """A 1-minute trigger generates 10,080 fires a week.

    Rendering them is meaningless and computing them makes the endpoint slow — and a silently
    partial
    week would read as an accurate forecast (the S65 rule, applied to a different surface).
    """
    occurrences, truncated = project_occurrences(
        trigger_id="t",
        trigger_name="Minutely",
        interval_secs=60,
        first_fire_at=MONDAY.timestamp(),
        start=MONDAY,
        days=7,
    )
    assert truncated is True
    assert len(occurrences) == MAX_OCCURRENCES_PER_TRIGGER


def test_an_old_trigger_does_not_iterate_from_its_first_fire():
    """Advancing arithmetically rather than stepping.

    A 60s trigger created a year ago would otherwise take ~525,600 no-op iterations to reach the
    window — a slow endpoint for no reason.
    """
    old = MONDAY - timedelta(days=365)
    occurrences, _ = project_occurrences(
        trigger_id="t",
        trigger_name="Old",
        interval_secs=3600,
        first_fire_at=old.timestamp(),
        start=MONDAY,
        days=1,
    )
    assert occurrences
    assert all(o.at >= MONDAY.timestamp() for o in occurrences)


def test_a_trigger_with_no_recurrence_projects_nothing():
    assert project_occurrences(
        trigger_id="t", trigger_name="x", interval_secs=0, first_fire_at=1.0, start=MONDAY
    ) == ([], False)
    assert project_occurrences(
        trigger_id="t", trigger_name="x", interval_secs=60, first_fire_at=0, start=MONDAY
    ) == ([], False)


# ── skip dates: AUTO-A3's struck columns (S81) ──


def _daily(**over):
    """A daily projection over one week, from MONDAY."""
    kwargs = dict(
        trigger_id="t",
        trigger_name="Nightly",
        interval_secs=86400,
        first_fire_at=MONDAY.timestamp(),
        start=MONDAY,
        days=7,
    )
    kwargs.update(over)
    return project_occurrences(**kwargs)


def test_a_skip_date_is_struck_not_silently_fired():
    """🔴 The measured gap: `project_occurrences` did not read `skip_dates` at ALL.

    AUTO-A3 requires skip dates to render as struck columns. Driven with a daily trigger and one day
    declared a skip date, the projection returned that fire completely UNANNOTATED while
    `SchedulerService._should_run` would refuse it — a grid confidently showing a fire that will not
    happen.
    """
    day = (MONDAY + timedelta(days=2)).strftime("%Y-%m-%d")
    occurrences, _ = _daily(skip_dates=[day])
    struck = [o for o in occurrences if o.suppressed_by == GateOutcome.SKIPPED.value]
    assert len(struck) == 1
    assert struck[0].reason == f"skip date {day}"
    # And nothing else moved: the other six fires are untouched.
    assert len([o for o in occurrences if not o.suppressed_by]) == 6


def test_the_grid_agrees_with_the_scheduler_about_the_calendar_date():
    """The two halves had to land together, and this is why.

    `SchedulerService` resolves the date through `_job_tz(job)` — the JOB's timezone — while the
    projection converted with `.astimezone()`, the SERVER's. For an `Asia/Tokyo` job on a UTC host
    the same instant is a different calendar date, so honouring `skip_dates` against server time
    would have struck the WRONG column: a grid that is confidently wrong, which is worse than one
    that was merely silent.
    """
    # 21:30 UTC is the 5th in UTC and the 6th in Tokyo.
    inst = datetime(2026, 8, 5, 21, 30, tzinfo=timezone.utc)
    start = datetime.fromtimestamp(inst.timestamp() - 3600)
    for tz_name in ("UTC", "Asia/Tokyo", "America/Los_Angeles"):
        job_date = datetime.fromtimestamp(inst.timestamp(), ZoneInfo(tz_name)).strftime("%Y-%m-%d")
        occurrences, _ = project_occurrences(
            trigger_id="t",
            trigger_name="x",
            interval_secs=86400,
            first_fire_at=inst.timestamp(),
            start=start,
            days=3,
            skip_dates=[job_date],
            tz_name=tz_name,
        )
        first = [o for o in occurrences if abs(o.at - inst.timestamp()) < 1]
        assert first, f"no fire projected at the instant under test for {tz_name}"
        assert (
            first[0].suppressed_by == GateOutcome.SKIPPED.value
        ), f"{tz_name}: the grid did not strike the date the scheduler skips"


def test_a_skip_date_read_in_server_time_would_miss_a_foreign_zone():
    """The pre-fix failure mode, pinned so the tz argument cannot be quietly dropped.

    With no `tz_name`, a Tokyo job's own skip date does NOT match — the server's calendar date for
    that instant is the previous day. This is the exact silent miss the paired fix prevents.
    """
    inst = datetime(2026, 8, 5, 21, 30, tzinfo=timezone.utc)
    tokyo_date = datetime.fromtimestamp(inst.timestamp(), ZoneInfo("Asia/Tokyo")).strftime(
        "%Y-%m-%d"
    )
    occurrences, _ = project_occurrences(
        trigger_id="t",
        trigger_name="x",
        interval_secs=86400,
        first_fire_at=inst.timestamp(),
        start=datetime.fromtimestamp(inst.timestamp() - 3600),
        days=3,
        skip_dates=[tokyo_date],
        tz_name="",  # server-local
    )
    first = [o for o in occurrences if abs(o.at - inst.timestamp()) < 1]
    assert first
    # Only meaningful when the host is not already on Tokyo time; CI runs UTC.
    server_date = datetime.fromtimestamp(inst.timestamp()).strftime("%Y-%m-%d")
    if server_date != tokyo_date:
        assert first[0].suppressed_by != GateOutcome.SKIPPED.value


def test_a_skip_date_wins_over_a_quiet_window():
    """Both suppress the fire, but they are different promises.

    A quiet window defers a time of day and may catch up; a skip date removes the whole day and
    never does. Reporting "quiet hours" for a date that is struck anyway would send the user to
    change the wrong setting.
    """
    day = (MONDAY + timedelta(days=1)).strftime("%Y-%m-%d")
    occurrences, _ = _daily(
        skip_dates=[day],
        gates={"quiet_hours": {"start": "00:00", "end": "23:59"}},
    )
    on_day = [o for o in occurrences if datetime.fromtimestamp(o.at).strftime("%Y-%m-%d") == day]
    assert on_day
    assert on_day[0].suppressed_by == GateOutcome.SKIPPED.value
    assert "skip date" in on_day[0].reason


def test_skip_dates_are_read_from_gates_too():
    """§1.1 reserves `gates.skip_dates` on the unified Trigger entity, while a legacy `ScheduleJob`
    carries the list as a top-level field. Accepting only one would have quietly ignored half the
    triggers."""
    day = (MONDAY + timedelta(days=3)).strftime("%Y-%m-%d")
    occurrences, _ = _daily(gates={"skip_dates": [day]})
    assert any(o.suppressed_by == GateOutcome.SKIPPED.value for o in occurrences)


def test_the_explicit_argument_beats_the_gates_key():
    """One trigger cannot have two skip lists; the explicit field is the caller's answer."""
    explicit = (MONDAY + timedelta(days=1)).strftime("%Y-%m-%d")
    in_gates = (MONDAY + timedelta(days=4)).strftime("%Y-%m-%d")
    occurrences, _ = _daily(skip_dates=[explicit], gates={"skip_dates": [in_gates]})
    struck = {
        datetime.fromtimestamp(o.at).strftime("%Y-%m-%d")
        for o in occurrences
        if o.suppressed_by == GateOutcome.SKIPPED.value
    }
    assert struck == {explicit}


def test_malformed_skip_dates_never_break_the_projection():
    """A trigger with a typo'd date still has real fires, and a grid that 500s shows nothing."""
    occurrences, _ = _daily(skip_dates=["", "  ", "not-a-date", None])  # type: ignore[list-item]
    assert len(occurrences) == 7
    assert not any(o.suppressed_by for o in occurrences)


def test_an_unparseable_timezone_falls_back_instead_of_raising():
    occurrences, _ = _daily(tz_name="Mars/Olympus_Mons")
    assert len(occurrences) == 7


def test_skipped_is_a_distinct_gate_outcome():
    """Not folded into QUIET: the UI colours them differently because they mean different things."""
    assert GateOutcome.SKIPPED.value == "skipped"
    assert GateOutcome.SKIPPED.value != GateOutcome.QUIET.value


# ── automation doctor (§7 criterion 12) ──


def test_an_orphaned_workflow_ref_is_reported():
    """§7 criterion 12 names this one by hand. It fires and fails forever, silently."""
    report = diagnose(
        [{"id": "t1", "workflow": {"def": "nightly-backup"}}], known_workflows={"digest"}
    )
    codes = [f.code for f in report.findings]
    assert "orphaned_workflow_ref" in codes


def test_a_known_workflow_ref_is_not_reported():
    report = diagnose([{"id": "t1", "workflow": {"def": "digest"}}], known_workflows={"digest"})
    assert report.healthy


def test_the_orphan_check_is_skipped_when_the_registry_cannot_be_read():
    """`known_workflows=None` means "cannot verify".

    A doctor that cries wolf when it cannot read the registry is worse than one that stays quiet
    about that dimension.
    """
    report = diagnose([{"id": "t1", "workflow": {"def": "anything"}}], known_workflows=None)
    assert report.healthy


def test_a_broad_watch_glob_is_reported():
    """The other finding §7 names. It fires on everything the user owns."""
    report = diagnose([{"id": "t1", "spec": {"glob": "~/**"}}])
    assert [f.code for f in report.findings] == ["broad_watch_glob"]


@pytest.mark.parametrize("glob", ["~/**", "/**", "**/*.py", "*"])
def test_globs_that_match_nearly_everything(glob):
    assert _is_broad_glob(glob) is True


@pytest.mark.parametrize(
    "glob", ["~/projects/**", "~/projects/acme/**/*.py", "/tmp/x/*.log", "~/Documents/*", ""]
)
def test_scoped_globs_are_not_flagged(glob):
    """Measured at 2 segments first, which flagged `~/projects/**` — a reasonable scope for someone
    who keeps all their work in one directory."""
    assert _is_broad_glob(glob) is False


def test_the_broad_glob_line_is_documented():
    assert BROAD_GLOB_SEGMENTS == 1


def test_quiet_windows_covering_the_whole_week_are_reported():
    """A trigger that can never fire looks configured and does nothing."""
    report = diagnose([{"id": "t", "gates": {"quiet_hours": {"start": "00:00", "end": "23:59"}}}])
    assert "quiet_hours_cover_everything" in [f.code for f in report.findings]


def test_a_normal_overnight_window_is_not_reported_as_covering_everything():
    report = diagnose([{"id": "t", "gates": {"quiet_hours": {"start": "22:00", "end": "08:00"}}}])
    assert report.healthy


def test_an_invalid_quiet_window_is_reported_because_the_user_is_not_protected():
    report = diagnose([{"id": "t", "gates": {"quiet_hours": {"start": "25:00", "end": "08:00"}}}])
    findings = [f for f in report.findings if f.code == "invalid_quiet_window"]
    assert findings
    assert "NOT protected" in findings[0].fix


def test_catch_up_without_a_window_is_reported_once():
    """One finding for one mistake — the spurious second one was found by probing."""
    report = diagnose([{"id": "t", "gates": {"quiet_hours": {"resolution": "catch_up"}}}])
    assert [f.code for f in report.findings] == ["catch_up_without_quiet_hours"]


def test_an_unknown_duty_gate_is_reported_because_it_fails_OPEN():
    """The finding that explains a dangerous-looking default.

    The gate failing open means the automation runs UNFILTERED — the opposite of what its author
    asked for — so the doctor has to say so.
    """
    report = diagnose(
        [{"id": "t", "gates": {"duty_gate": {"provider": "acme"}}}], known_duty_gates={"manual"}
    )
    findings = [f for f in report.findings if f.code == "unknown_duty_gate"]
    assert findings and "UNFILTERED" in findings[0].detail


def test_a_registered_duty_gate_is_not_reported():
    report = diagnose(
        [{"id": "t", "gates": {"duty_gate": {"provider": "manual"}}}], known_duty_gates={"manual"}
    )
    assert report.healthy


def test_every_finding_carries_a_fix():
    """A doctor that reports problems without saying what to do is a list of complaints."""
    report = diagnose(
        [
            {"id": "t1", "workflow": {"def": "gone"}},
            {"id": "t2", "spec": {"glob": "~/**"}},
            {"id": "t3", "gates": {"quiet_hours": {"start": "25:00", "end": "08:00"}}},
            {"id": "t4", "gates": {"duty_gate": {"provider": "acme"}}},
        ],
        known_workflows=set(),
        known_duty_gates={"manual"},
    )
    assert len(report.findings) >= 4
    assert all(f.fix and f.detail and f.code for f in report.findings)


def test_a_healthy_fleet_reports_healthy():
    report = diagnose(
        [
            {
                "id": "t",
                "workflow": {"def": "digest"},
                "gates": {
                    "quiet_hours": {"start": "22:00", "end": "08:00"},
                    "duty_gate": {"provider": "manual"},
                },
                "spec": {"glob": "~/projects/acme/**/*.py"},
            }
        ],
        known_workflows={"digest"},
        known_duty_gates={"manual"},
    )
    assert report.healthy
    assert report.to_dict()["count"] == 0


def test_diagnose_never_raises_on_malformed_rows():
    rows = [{}, {"id": None}, {"gates": "nope"}, {"workflow": []}, {"spec": 7}]
    diagnose(rows)  # type: ignore[list-item]
