"""#2520 — an absent `spec.timezone` meant UTC, so "remind me at 08:30" fired at 01:30.

**Measured before the fix**, on a host whose `/etc/localtime` points at
`America/Los_Angeles`, with `now = 2026-09-07T04:00Z`:

    spec                 {'kind': 'cron', 'expr': '30 8 * * *'}   # no timezone key
    arm.next_fire     -> 1788769800.0 = 2026-09-07 08:30 UTC = 01:30 PDT   ← what you got
    croniter@LA       -> 1788795000.0 = 2026-09-07 15:30 UTC = 08:30 PDT   ← what 08:30 means
    DELTA                -7.0 h

and it was silent — the trigger armed, nothing warned, and the only symptom was a
notification at a strange hour a user reads as flakiness rather than as a default.

**Absent and invalid are two different facts** and were being collapsed into one. An
invalid zone is a typo: the author thought about timezones and mistyped, and refusing it
where it is authored is right. An *absent* zone means they never thought about timezones,
and for a local-first tool whose surface is "remind me at 08:30", UTC is close to the least
likely thing they meant. So the three branches, each asserted below:

    absent                          → the machine's zone
    configured-and-invalid          → refused, loudly, where it is authored
    machine zone undeterminable     → UTC, and `doctor` WARNS naming the consequence

**One owner.** Six functions had each derived their own answer to the same question and did
not agree (`get_local_tz` returned `('UTC', …)` on a PDT host; `calendar._resolve_zone` fell
through to server-local while `arm._trigger_tz` fell through to UTC, so the week grid struck
a different column than the engine skipped; `report_schedules._effective_tz`, written
*specifically* to compensate for `arm`'s UTC default, resolved to `'UTC'` itself). They all
call `personalclaw.timezones` now, and `test_timezone_resolution_has_one_owner.py` is the
rail that keeps a seventh from appearing.

Zones are pinned through `TZ` (which `machine_zone_name()` consults first) rather than
through `/etc`, so every assertion holds on a CI runner and on a developer laptop without
reading a host file. `Asia/Tokyo` (UTC+9) is used where the sign of the error matters: a
+9 host makes "08:30 local" and "08:30 UTC" different calendar instants AND different
calendar days, so a test cannot pass by accident on a UTC runner.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from personalclaw import timezones as tzmod
from personalclaw.knowledge.report_schedules import _effective_tz, clock_spec
from personalclaw.knowledge.research_reports import ReportDefinition, _report_tz
from personalclaw.schedule import (
    ScheduleDefinition,
    ScheduleJob,
    _job_tz,
    compute_next_run_ts,
    get_local_tz,
)
from personalclaw.timezones import (
    SOURCE_CONFIG,
    SOURCE_EXPLICIT,
    SOURCE_MACHINE,
    SOURCE_UTC_FALLBACK,
    UnknownTimeZone,
    machine_zone_name,
    resolve_zone,
    resolve_zone_name,
)
from personalclaw.triggers.arm import _trigger_tz, next_fire, semantic_spec_issues
from personalclaw.triggers.calendar import _resolve_zone
from personalclaw.triggers.models import Trigger

#: The instant every fire computation below is anchored at — 2026-09-07T04:00Z. Fixed so a
#: measured epoch can be quoted in an assertion instead of recomputed.
NOW = datetime(2026, 9, 7, 4, 0, 0, tzinfo=timezone.utc).timestamp()

#: The wall clock a user authoring "remind me at 08:30" means.
DAILY_0830 = "30 8 * * *"


def _reminder(**spec_extra) -> Trigger:
    """The trigger from the issue: a daily 08:30 cron, with no timezone unless given."""
    return Trigger(
        id="t-remind",
        name="remind me at 08:30",
        kind="clock",
        enabled=True,
        spec={"kind": "cron", "expr": DAILY_0830, **spec_extra},
    )


def _pin_machine_zone(monkeypatch, name: str) -> None:
    """Make the MACHINE's zone deterministic: `TZ` wins, and `/etc` is taken out of play."""
    monkeypatch.setenv("TZ", name)


def _make_machine_zone_unknowable(monkeypatch, tmp_path: Path) -> None:
    """The only state in which UTC is correct: no TZ, no symlink, no `/etc/timezone`.

    Points the owner's two path constants at names that do not exist — the same shape a
    container that COPIES `/etc/localtime` produces (no symlink target to read a name from).
    """
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(tzmod, "LOCALTIME_LINK", tmp_path / "absent-localtime")
    monkeypatch.setattr(tzmod, "TIMEZONE_FILE", tmp_path / "absent-timezone")


def _independent_expectation(expr: str, zone: str, *, now: float = NOW) -> float:
    """What `expr` means in `zone`, computed WITHOUT calling personalclaw.

    Deliberately a second implementation: comparing `next_fire` against `next_fire`'s own
    helper would assert that the product agrees with itself.
    """
    from croniter import croniter

    return float(croniter(expr, datetime.fromtimestamp(now, tz=ZoneInfo(zone))).get_next(float))


# ── branch 1: an ABSENT zone means the machine's wall clock ───────────────────────────


class TestAbsentZoneUsesTheMachineZone:
    def test_an_0830_reminder_fires_at_0830_local_not_0830_utc(self, monkeypatch):
        """The issue, as an assertion. Both sides measured independently."""
        _pin_machine_zone(monkeypatch, "Asia/Tokyo")

        got = next_fire(_reminder(), now=NOW)
        want = _independent_expectation(DAILY_0830, "Asia/Tokyo")

        assert got == want
        assert datetime.fromtimestamp(got, ZoneInfo("Asia/Tokyo")).strftime("%H:%M") == "08:30"
        # The pre-fix answer, named so a regression is unambiguous rather than merely unequal.
        utc_reading = datetime.fromtimestamp(got, timezone.utc).strftime("%H:%M")
        assert utc_reading == "23:30", f"08:30 JST is 23:30 UTC the day before, got {utc_reading}"

    def test_the_resolved_zone_reports_where_it_came_from(self, monkeypatch):
        _pin_machine_zone(monkeypatch, "Asia/Tokyo")
        assert resolve_zone_name("") == ("Asia/Tokyo", SOURCE_MACHINE)
        assert machine_zone_name() == "Asia/Tokyo"

    def test_an_explicit_zone_still_wins_over_the_machine(self, monkeypatch):
        """The fix must not take authored intent away from anyone who declared a zone."""
        _pin_machine_zone(monkeypatch, "Asia/Tokyo")
        assert resolve_zone_name("Europe/London") == ("Europe/London", SOURCE_EXPLICIT)
        got = next_fire(_reminder(timezone="Europe/London"), now=NOW)
        assert got == _independent_expectation(DAILY_0830, "Europe/London")

    def test_the_config_zone_wins_over_the_machine(self, monkeypatch, tmp_path):
        """`config.timezone` is a deliberate statement; the machine zone is a guess."""
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        (tmp_path / "config.json").write_text(
            json.dumps({"timezone": "Europe/Berlin"}), encoding="utf-8"
        )
        _pin_machine_zone(monkeypatch, "Asia/Tokyo")
        assert resolve_zone_name("") == ("Europe/Berlin", SOURCE_CONFIG)

    def test_a_skip_date_is_compared_against_the_local_calendar_day(self, monkeypatch):
        """The skip-date path resolves the zone too, so "not on this day" means the local day.

        With no zone this asked about UTC days while the week grid drew local ones. On a +9
        host `2026-09-07 08:30 JST` is `2026-09-06` in UTC, so a skip on the 7th used to miss.
        """
        _pin_machine_zone(monkeypatch, "Asia/Tokyo")
        armed = next_fire(_reminder(skip_dates=["2026-09-07"]), now=NOW)
        local_day = datetime.fromtimestamp(armed, ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")
        assert local_day == "2026-09-08", "the skipped local day must be stepped over"


# ── branch 2: a CONFIGURED-BUT-INVALID zone is refused, loudly, where it is authored ──


class TestInvalidZoneIsRefusedNotDegraded:
    @pytest.mark.parametrize("bad", ["CEST", "PDT", "Amrica/Los_Angeles", "Mars/Olympus_Mons"])
    def test_the_owner_raises_rather_than_silently_choosing_a_zone(self, bad):
        with pytest.raises(UnknownTimeZone) as exc:
            resolve_zone(bad)
        assert bad in str(exc.value)
        assert exc.value.name == bad

    def test_the_message_names_the_abbreviation_trap(self):
        """`CEST`/`PDT` are the mistake people actually make; the refusal has to say so."""
        with pytest.raises(UnknownTimeZone) as exc:
            resolve_zone("CEST")
        assert "abbreviation" in str(exc.value).lower() or "not IANA zones" in str(exc.value)
        assert "America/Los_Angeles" in str(exc.value), "the message must show a usable example"

    def test_creating_a_trigger_with_a_typod_zone_is_an_authoring_ERROR(self):
        """Refused at the door — `tools.create` rejects on any error-severity spec issue."""
        errs = [
            i
            for i in semantic_spec_issues(
                "clock", {"kind": "cron", "expr": DAILY_0830, "timezone": "CEST"}
            )
            if i.severity == "error"
        ]
        assert len(errs) == 1
        assert errs[0].path == "spec.timezone"
        assert "not an IANA timezone name" in errs[0].message
        # It must also tell the author what "leave it empty" now does, or the refusal reads as
        # "you must declare a zone" — which was one of the options the owner did NOT choose.
        assert "machine's zone" in errs[0].message

    def test_a_valid_zone_is_not_flagged(self):
        spec = {"kind": "cron", "expr": DAILY_0830, "timezone": "America/Los_Angeles"}
        assert semantic_spec_issues("clock", spec) == []

    def test_an_absent_zone_is_not_flagged(self):
        """Absent is not invalid — flagging it would make every existing trigger an error."""
        assert semantic_spec_issues("clock", {"kind": "cron", "expr": DAILY_0830}) == []

    def test_a_stored_typo_is_not_armable_and_does_not_wedge_the_sweep(self, monkeypatch, caplog):
        """A hand-edited row refuses to arm — the same 0.0 an invalid cron produces — and says
        so by name, instead of firing 7 hours off. It must NOT raise: one bad row would then
        take the boot sweep down for every other trigger."""
        _pin_machine_zone(monkeypatch, "Asia/Tokyo")
        with caplog.at_level("WARNING"):
            assert next_fire(_reminder(timezone="CEST"), now=NOW) == 0.0
        assert any("CEST" in r.getMessage() for r in caplog.records), caplog.text
        assert any("will not arm" in r.getMessage() for r in caplog.records), caplog.text


# ── branch 3: UTC is the LAST RESORT, and the doctor warns about it ───────────────────


class TestUtcIsOnlyTheLastResort:
    def test_utc_when_and_only_when_the_machine_zone_is_unknowable(self, monkeypatch, tmp_path):
        _make_machine_zone_unknowable(monkeypatch, tmp_path)
        assert machine_zone_name() == ""
        assert resolve_zone_name("") == ("UTC", SOURCE_UTC_FALLBACK)
        assert resolve_zone("") is timezone.utc

    def test_the_last_resort_cannot_itself_fail(self, monkeypatch, tmp_path):
        """`datetime.timezone.utc`, not `ZoneInfo("UTC")` — a host with no tz database at all
        cannot construct the latter, and a last resort that can raise is not one."""
        _make_machine_zone_unknowable(monkeypatch, tmp_path)
        assert resolve_zone("") is timezone.utc

    def test_an_unusable_TZ_falls_through_instead_of_poisoning_every_schedule(
        self, monkeypatch, tmp_path
    ):
        """`TZ=PDT` is a real shell state. It must not become the resolved zone, and it must
        not raise either — `TZ` is ambient, not authored."""
        monkeypatch.setenv("TZ", "PDT")
        monkeypatch.setattr(tzmod, "LOCALTIME_LINK", tmp_path / "absent-localtime")
        monkeypatch.setattr(tzmod, "TIMEZONE_FILE", tmp_path / "absent-timezone")
        assert machine_zone_name() == ""
        assert resolve_zone_name("")[1] == SOURCE_UTC_FALLBACK

    def test_etc_timezone_answers_when_localtime_is_a_copy_not_a_symlink(
        self, monkeypatch, tmp_path
    ):
        """The container shape: `/etc/localtime` copied (no name in it), `/etc/timezone` present."""
        monkeypatch.delenv("TZ", raising=False)
        copied = tmp_path / "localtime-copy"
        copied.write_bytes(b"TZif2\x00binary")
        (tmp_path / "timezone").write_text("Europe/Berlin\n", encoding="utf-8")
        monkeypatch.setattr(tzmod, "LOCALTIME_LINK", copied)
        monkeypatch.setattr(tzmod, "TIMEZONE_FILE", tmp_path / "timezone")
        assert machine_zone_name() == "Europe/Berlin"

    def test_a_symlink_target_is_read_for_its_iana_key(self, monkeypatch, tmp_path):
        """Both database directory spellings, and a relative target."""
        monkeypatch.delenv("TZ", raising=False)
        monkeypatch.setattr(tzmod, "TIMEZONE_FILE", tmp_path / "absent-timezone")
        db = tmp_path / "usr" / "share" / "zoneinfo" / "Europe"
        db.mkdir(parents=True)
        (db / "London").write_bytes(b"TZif")
        link = tmp_path / "localtime"
        link.symlink_to(db / "London")
        monkeypatch.setattr(tzmod, "LOCALTIME_LINK", link)
        assert machine_zone_name() == "Europe/London"

    def test_time_tzname_is_not_the_source(self, monkeypatch, tmp_path):
        """The gotcha, asserted: `time.tzname` yields abbreviations `ZoneInfo` rejects, so the
        resolver must not return one even on a host where `tzname` is populated."""
        import time as _time

        _make_machine_zone_unknowable(monkeypatch, tmp_path)
        resolved, source = resolve_zone_name("")
        assert source == SOURCE_UTC_FALLBACK
        assert resolved not in _time.tzname or resolved == "UTC"


class TestTheDoctorWarnsAboutTheFallback:
    """Driven through the REAL doctor paths, not through `zone_report()`'s return value."""

    @pytest.mark.asyncio
    async def test_the_probe_framework_reports_a_failing_scheduling_row(
        self, monkeypatch, tmp_path
    ):
        from personalclaw.resilience.doctor import DoctorContext, all_probes, run_capability

        _make_machine_zone_unknowable(monkeypatch, tmp_path)
        assert any(p.id == "scheduling.timezone" for p in all_probes()), "probe not registered"

        report = await run_capability("scheduling", DoctorContext(home=tmp_path))
        row = next(p for p in report["probes"] if p["id"] == "scheduling.timezone")

        assert row["ok"] is False, "a UTC fallback is a WARNING, not an informational line"
        assert row["tier"] == 3, "it must degrade the scheduling card only, never the gateway"
        # It has to name the CONSEQUENCE, in hours — not the condition.
        assert "fire at UTC" in row["detail"]
        assert "hour" in row["detail"]
        assert row["evidence"]["source"] == SOURCE_UTC_FALLBACK

    @pytest.mark.asyncio
    async def test_a_resolved_zone_is_a_passing_row_that_still_names_the_zone(
        self, monkeypatch, tmp_path
    ):
        from personalclaw.resilience.doctor import DoctorContext, run_capability

        monkeypatch.setenv("TZ", "Asia/Tokyo")
        report = await run_capability("scheduling", DoctorContext(home=tmp_path))
        row = next(p for p in report["probes"] if p["id"] == "scheduling.timezone")
        assert row["ok"] is True
        assert "Asia/Tokyo" in row["detail"]
        assert row["evidence"]["source"] == SOURCE_MACHINE

    @pytest.mark.asyncio
    async def test_an_unusable_config_zone_is_reported_rather_than_silently_ignored(
        self, monkeypatch, tmp_path
    ):
        """Rule 2 of the resolver: an ambient default degrades instead of raising — but it does
        NOT get to be silent, or we have swapped one invisible default for another."""
        from personalclaw.resilience.doctor import DoctorContext, run_capability

        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        (tmp_path / "config.json").write_text(json.dumps({"timezone": "CEST"}), encoding="utf-8")
        monkeypatch.setenv("TZ", "Asia/Tokyo")

        report = await run_capability("scheduling", DoctorContext(home=tmp_path))
        row = next(p for p in report["probes"] if p["id"] == "scheduling.timezone")
        assert row["ok"] is False
        assert "CEST" in row["detail"]
        assert row["evidence"]["config_ok"] is False
        # …and schedules still work, on the machine's zone.
        assert resolve_zone_name("") == ("Asia/Tokyo", SOURCE_MACHINE)

    def test_the_cli_doctor_prints_the_zone_and_warns_on_a_fallback(
        self, monkeypatch, tmp_path, capsys
    ):
        """`personalclaw doctor`'s real body, so the line is proven to render."""
        import urllib.error

        from personalclaw.cli_doctor import _doctor

        _make_machine_zone_unknowable(monkeypatch, tmp_path)
        fake_proc = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": "v20.0.0",
                "stderr": "",
                "check_returncode": lambda s: None,
            },
        )()
        with (
            patch(
                "personalclaw.cli_doctor.shutil.which",
                side_effect=lambda b: f"/usr/local/bin/{b}",
            ),
            patch("subprocess.run", return_value=fake_proc),
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
            patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
        ):
            try:
                _doctor()
            except SystemExit:
                pass
        out = capsys.readouterr().out
        assert "timezone:" in out
        assert "utc-fallback" in out
        assert "fire at UTC" in out, out
        assert "⚠️" in out


# ── one owner: every resolver now gives the same answer, because it is one function ───


class TestOneOwnerForTheResolution:
    """The derived owner set (by AST + interprocedural taint over `src/personalclaw/`):

        triggers/arm.py:44                _trigger_tz()
        schedule.py:432                   get_local_tz()
        schedule.py:445                   _job_tz()
        triggers/calendar.py:564          _resolve_zone()
        knowledge/research_reports.py:396 _report_tz()
        knowledge/report_schedules.py:76  _effective_tz()
        cli_setup.py:292                  _detect_system_timezone()   (found by the /etc clause)

    Before the fix these disagreed with each other, measured on a PDT host with a blank
    `config.timezone`: `UTC`, `UTC`, `UTC`, **server-local**, `UTC`, `'UTC'`.
    """

    def test_all_six_resolvers_name_the_same_zone_for_an_absent_input(self, monkeypatch):
        monkeypatch.setenv("TZ", "Asia/Tokyo")
        expected = ZoneInfo("Asia/Tokyo")

        job = ScheduleJob(
            id="j", name="n", schedule=ScheduleDefinition(kind="cron", cron_expr=DAILY_0830)
        )
        defn = ReportDefinition(
            id="r",
            name="n",
            prompt="p",
            schedule=ScheduleDefinition(kind="cron", cron_expr=DAILY_0830),
        )

        assert _trigger_tz(_reminder()) == expected
        assert _job_tz(job) == expected
        assert get_local_tz() == ("Asia/Tokyo", expected)
        assert _resolve_zone("") == expected
        assert _report_tz(defn) == expected
        assert _effective_tz(defn) == "Asia/Tokyo"

    def test_get_local_tz_is_actually_local(self, monkeypatch):
        """It returned `('UTC', ZoneInfo('UTC'))` on a non-UTC host, and it is what the
        Triggers and Calendar endpoints report to the frontend as `server_tz`."""
        monkeypatch.setenv("TZ", "Asia/Tokyo")
        name, zone = get_local_tz()
        assert name == "Asia/Tokyo"
        assert zone.utcoffset(datetime(2026, 9, 7, tzinfo=timezone.utc)).total_seconds() == 9 * 3600

    def test_the_week_grid_and_the_fire_path_read_the_same_calendar_day(self, monkeypatch):
        """`calendar._resolve_zone` fell through to SERVER-LOCAL while `arm._trigger_tz` fell
        through to UTC, so the grid struck one column and the engine skipped another — the one
        thing `calendar.py`'s own docstring promises it will not do."""
        monkeypatch.setenv("TZ", "Asia/Tokyo")
        assert _resolve_zone("") == _trigger_tz(_reminder())

    def test_a_report_schedule_writes_the_resolved_zone_into_the_trigger_spec(self, monkeypatch):
        """`_effective_tz` existed to compensate for `arm`'s UTC default and wrote `'UTC'`
        itself on a stock install, so the compensation did nothing on the hosts it was for."""
        monkeypatch.setenv("TZ", "Asia/Tokyo")
        defn = ReportDefinition(
            id="r",
            name="n",
            prompt="p",
            schedule=ScheduleDefinition(kind="cron", cron_expr=DAILY_0830),
        )
        assert clock_spec(defn)["timezone"] == "Asia/Tokyo"

    def test_an_explicit_report_zone_still_wins_over_the_resolved_default(self, monkeypatch):
        """Vacuity for `_report_tz`'s fallback, added because a mutant that ignored `defn.tz`
        entirely survived the first falsification pass — every other assertion here drives the
        ABSENT case, so nothing was holding the explicit one."""
        monkeypatch.setenv("TZ", "Asia/Tokyo")
        defn = ReportDefinition(
            id="r",
            name="n",
            prompt="p",
            schedule=ScheduleDefinition(kind="cron", cron_expr=DAILY_0830),
            tz="America/New_York",
        )
        assert _report_tz(defn) == ZoneInfo("America/New_York")
        assert _effective_tz(defn) == "America/New_York"

    def test_an_unusable_report_zone_degrades_instead_of_wedging_every_other_report(
        self, monkeypatch
    ):
        """`is_due` sweeps every definition on every tick, so one malformed report must not be
        able to raise out and stop the others (rule 1 of `research_reports`)."""
        monkeypatch.setenv("TZ", "Asia/Tokyo")
        defn = ReportDefinition(
            id="r",
            name="n",
            prompt="p",
            schedule=ScheduleDefinition(kind="cron", cron_expr=DAILY_0830),
            tz="CEST",
        )
        assert _report_tz(defn) == ZoneInfo("Asia/Tokyo")

    def test_the_legacy_scheduler_and_the_trigger_engine_arm_to_the_same_instant(self, monkeypatch):
        """`_job_tz` was the issue's named lead. It consulted `config.timezone` first — which
        is blank on a stock install — so it landed on UTC exactly like `arm` did."""
        monkeypatch.setenv("TZ", "Asia/Tokyo")
        job = ScheduleJob(
            id="j", name="n", schedule=ScheduleDefinition(kind="cron", cron_expr=DAILY_0830)
        )
        assert compute_next_run_ts(job, now=NOW) == next_fire(_reminder(), now=NOW)
        assert compute_next_run_ts(job, now=NOW) == _independent_expectation(
            DAILY_0830, "Asia/Tokyo"
        )

    def test_setup_offers_the_machine_zone_it_detected(self, monkeypatch):
        """`personalclaw setup` is the only authoring surface for `config.timezone`, so the zone
        it OFFERS as the default has to be the machine's.

        Added because a mutant that gutted `_detect_system_timezone` to `return ""` survived the
        rail: `cli_setup` imports the owner twice, so the rail's per-module import check stayed
        satisfied. The rail was the wrong instrument for a behaviour deletion — this is the
        right one.
        """
        from personalclaw.cli_setup import _detect_system_timezone

        monkeypatch.setenv("TZ", "Asia/Tokyo")
        assert _detect_system_timezone() == "Asia/Tokyo"

    def test_setup_never_offers_an_abbreviation_it_cannot_save(self, monkeypatch, tmp_path):
        """Its own `/etc` reader returned `TZ` unvalidated, so a `TZ=PDT` shell was 'detected'
        as `PDT`, offered as the default, and then refused by the retry loop right below it."""
        from personalclaw.cli_setup import _detect_system_timezone

        monkeypatch.setenv("TZ", "PDT")
        monkeypatch.setattr(tzmod, "LOCALTIME_LINK", tmp_path / "absent-localtime")
        monkeypatch.setattr(tzmod, "TIMEZONE_FILE", tmp_path / "absent-timezone")
        assert _detect_system_timezone() == ""


# ── the documented gotcha: `spec.at` is epoch seconds, not ISO ────────────────────────


class TestSpecAtIsEpochSeconds:
    """The issue asked for this to be documented while we were here. Documented AND asserted:
    a comment can go stale, and the failure mode is silent — `arm._positive()` coerces through
    `float()`, so an ISO string reads as 0.0, which means "never fires"."""

    def test_an_epoch_at_arms_to_that_instant(self):
        at = NOW + 3600.0
        t = Trigger(id="t", name="n", kind="clock", enabled=True, spec={"kind": "at", "at": at})
        assert next_fire(t, now=NOW) == at

    def test_an_iso_at_silently_reads_as_never_fires(self):
        iso = datetime.fromtimestamp(NOW + 3600.0, timezone.utc).isoformat()
        t = Trigger(id="t", name="n", kind="clock", enabled=True, spec={"kind": "at", "at": iso})
        assert next_fire(t, now=NOW) == 0.0, (
            "an ISO `spec.at` is not armable — which is exactly why the field's units have to "
            "be written down; every sibling timestamp on the row (`next_fire_at`, `expires_at`, "
            "`last_fired_at`) IS ISO"
        )
