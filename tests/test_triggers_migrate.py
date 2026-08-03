"""The lossless cron migration (AUTOMATION-SUBSTRATE §2/§6 — S66).

The step that cannot be redone cheaply. Once `crons.json` is converted
and the legacy service retired,
a dropped field is a behaviour nobody can recover without the user's old file — and the failures are
quiet: a lost `skip_dates` fires on a holiday, a lost `strict_schedule`
catches up when the author said
not to, a lost `timezone` runs at the wrong hour for half the year.

**Driven against a real file, not a hand-written fixture.** The tests
below build jobs with the actual
shipped `ScheduleService` and let IT write `crons.json`, then migrate
what is on disk. A fixture I wrote
myself would encode my belief about the format; the service encodes the format.

**Two measurements that changed the implementation.**

* `ScheduleService._save` persists 33 of the dataclass's 35 fields — **`dry_run` and `last_outcome`
  never reach disk.** They are runtime-only, so the migration cannot read
  them and must not claim to.
  `NEVER_PERSISTED` records that, or the audit would lie in the other direction.
* `ScheduleDefinition`'s three kinds do NOT line up with the trigger
clock's three. Legacy `every` has
  no equivalent, and mapping it onto `at` — the tempting shape match, since both carry one number —
  would turn every recurring interval job into a one-shot that fires once and dies.
"""

import json
import time

import pytest

from personalclaw.triggers.migrate import (
    NEVER_PERSISTED,
    clock_spec,
    convert_job,
    migrate_crons,
    unconverted_fields,
)
from personalclaw.triggers.models import LEGACY_FIELD_MAP


@pytest.fixture
def real_store(tmp_path, monkeypatch):
    """A `crons.json` written by the REAL shipped service.

    The point of the fixture: a hand-written dict encodes what I think
    the format is, and this program
    has repeatedly found that belief wrong. `_save`'s own projection is the format.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.schedule import ScheduleDefinition, ScheduleJob, ScheduleService

    service = ScheduleService()
    service._jobs = [
        ScheduleJob(
            id="j-cron",
            name="nightly backup",
            schedule=ScheduleDefinition(kind="cron", cron_expr="0 3 * * *"),
            action={"provider": "run-prompt", "config": {"prompt": "back up"}},
            timezone="Europe/London",
            skip_dates=["2026-12-25"],
            strict_schedule=True,
            enabled=True,
            channel="C123",
            last_run_ts=time.time() - 3600,
            last_status="ok",
        ),
        ScheduleJob(
            id="j-every",
            name="poll feed",
            schedule=ScheduleDefinition(kind="every", every_secs=300),
            action={"provider": "bash", "config": {"command": "true"}},
            enabled=True,
            silent=True,
            session_key="cron:j-every",
            persistent_session=True,
        ),
        ScheduleJob(
            id="j-at",
            name="one shot",
            schedule=ScheduleDefinition(kind="at", at_ts=time.time() + 7200),
            action={"provider": "notify", "config": {}},
            delete_after_run=True,
            enabled=True,
        ),
        ScheduleJob(
            id="j-seq",
            name="multi step",
            schedule=ScheduleDefinition(kind="cron", cron_expr="0 9 * * 1"),
            agent_sequence=["research", "draft", "review"],
            enabled=True,
            last_status="error",
            last_error="boom",
        ),
    ]
    service._save()
    # Read via the SERVICE's own path, not `tmp_path / "crons.json"`. On macOS the temp dir resolves
    # through a symlink (`/var` → `/private/var`), so a hand-built path
    # can miss the file the service
    # just wrote — measured while writing this fixture.
    return json.loads(service._path.read_text(encoding="utf-8"))


# ── the losslessness bar ──


def test_a_REAL_store_migrates_with_NOTHING_unaccounted(real_store):
    """The bar for this session. Not "looks right" — nothing left the building unaccounted for."""
    report = migrate_crons(real_store)
    assert report.to_dict()["unaccounted"] == []
    assert report.lossless is True


def test_every_row_converts(real_store):
    report = migrate_crons(real_store)
    assert len(report.converted) == len(real_store["jobs"])
    assert report.refused == []


def test_the_audit_is_PER_FIELD_not_per_row(real_store):
    """S62 wrote `LEGACY_FIELD_MAP` precisely so this check is possible; running it per row is what
    makes "lossless" a measurement rather than a claim."""
    for row in real_store["jobs"]:
        assert unconverted_fields(row) == []


def test_an_UNKNOWN_field_is_reported_not_swallowed():
    """A field the map does not know is one the migration would carry into the void."""
    assert unconverted_fields({"id": "j", "brand_new_field": 1}) == ["brand_new_field"]


def test_the_NEVER_PERSISTED_fields_are_not_reported_as_lost():
    """`dry_run` and `last_outcome` are on the dataclass but absent from `_save`'s projection, so a
    `crons.json` row cannot carry them. Reporting them would make the audit lie the other way."""
    assert unconverted_fields({"id": "j", "dry_run": True, "last_outcome": "ok"}) == []
    assert NEVER_PERSISTED == {"dry_run", "last_outcome"}


def test_the_measurement_behind_NEVER_PERSISTED_still_holds():
    """Pinned against the real `_save`: if a future edit starts persisting these, this fails and the
    exclusion should be revisited rather than silently becoming wrong."""
    import dataclasses as dc
    import inspect
    import re

    from personalclaw.schedule import ScheduleJob, ScheduleService

    saved = set(re.findall(r'"(\w+)": j\.', inspect.getsource(ScheduleService._save))) | {
        "schedule"
    }
    fields = {f.name for f in dc.fields(ScheduleJob)}
    assert fields - saved == NEVER_PERSISTED


# ── the clock spec: the three kinds do not line up ──


def test_a_cron_job_keeps_its_EXPRESSION(real_store):
    spec = next(
        c.trigger["spec"]
        for c in migrate_crons(real_store).converted
        if c.trigger["id"] == "j-cron"
    )
    assert spec["kind"] == "cron"
    assert spec["expr"] == "0 3 * * *"


@pytest.mark.parametrize("key,expected", [("timezone", "Europe/London"), ("strict", True)])
def test_the_QUIETLY_LOSABLE_fields_survive(real_store, key, expected):
    """A lost `timezone` runs at the wrong hour for half the year; a lost
    `strict` catches up when the
    author said not to. Neither failure names itself."""
    spec = next(
        c.trigger["spec"]
        for c in migrate_crons(real_store).converted
        if c.trigger["id"] == "j-cron"
    )
    assert spec[key] == expected


def test_skip_dates_survive(real_store):
    """The loudest quiet failure: the trigger fires on Christmas and nobody knows why."""
    spec = next(
        c.trigger["spec"]
        for c in migrate_crons(real_store).converted
        if c.trigger["id"] == "j-cron"
    )
    assert spec["skip_dates"] == ["2026-12-25"]


def test_an_INTERVAL_job_does_NOT_become_a_one_shot(real_store):
    """The single most destructive possible mistranslation in this file.
    `every` and `at` both carry one
    number, so the shape match is tempting — and it would turn every recurring interval job into a
    one-shot that fires once and dies."""
    converted = next(c for c in migrate_crons(real_store).converted if c.trigger["id"] == "j-every")
    assert converted.trigger["spec"]["kind"] != "at"
    assert converted.trigger["spec"]["interval_secs"] == 300
    assert any("one-shot" in note for note in converted.notes)


def test_a_ONE_SHOT_keeps_the_user_s_delete_choice(real_store):
    """§1.2 makes `delete_after_run` the default for `at`, but a one-shot
    the user marked to KEEP must
    not be deleted because the new default says otherwise."""
    spec = next(
        c.trigger["spec"] for c in migrate_crons(real_store).converted if c.trigger["id"] == "j-at"
    )
    assert spec["kind"] == "at"
    assert spec["delete_after_run"] is True


def test_a_kept_one_shot_is_carried_as_kept():
    spec, _notes = clock_spec({"kind": "at", "at_ts": 1.0}, {"delete_after_run": False})
    assert spec["delete_after_run"] is False


def test_an_UNKNOWN_schedule_kind_is_flagged_not_guessed():
    """Guessing would schedule something the author did not write."""
    spec, notes = clock_spec({"kind": "lunar"}, {})
    assert spec == {"kind": ""}
    assert any("unknown legacy schedule kind" in n for n in notes)


def test_a_cron_job_with_NO_expression_is_flagged():
    _spec, notes = clock_spec({"kind": "cron", "cron_expr": ""}, {})
    assert any("cannot be scheduled" in n for n in notes)


# ── delivery, session, and what it runs ──


def test_SILENT_wins_over_a_channel():
    """The legacy flag means the agent sends via send_message itself, so a trigger that ALSO auto-
    delivered would double-post — the symptom that makes someone distrust the whole migration."""
    converted = convert_job({"id": "j", "name": "n", "silent": True, "channel": "C1"})
    assert converted.trigger["delivery"] == "none"


def test_a_channel_becomes_a_channel_route():
    converted = convert_job({"id": "j", "name": "n", "channel": "C1"})
    assert converted.trigger["delivery"] == "channel:C1"


def test_PERSISTENT_session_becomes_pinned(real_store):
    converted = next(c for c in migrate_crons(real_store).converted if c.trigger["id"] == "j-every")
    assert converted.trigger["session"] == "pinned:cron:j-every"


def test_a_session_key_WITHOUT_the_flag_stays_fresh():
    """The stateless convention is a per-fire key. Pinning it would
    silently make every fire share one
    growing session, and the drift shows up as an automation that gets
    slower and stranger over weeks.
    """
    converted = convert_job({"id": "j", "name": "n", "session_key": "cron:j:abc123"})
    assert converted.trigger["session"] == "fresh"


def test_an_action_becomes_an_INLINE_workflow():
    converted = convert_job(
        {"id": "j", "name": "n", "action": {"provider": "bash", "config": {"command": "true"}}}
    )
    assert converted.trigger["workflow"]["inline"]["provider"] == "bash"


def test_an_agent_SEQUENCE_is_not_flattened_to_its_first_step(real_store):
    """Silently flattening a three-step sequence into one inline action
    would run only step one — and
    the user would see a "successful" automation doing a third of the work."""
    converted = next(c for c in migrate_crons(real_store).converted if c.trigger["id"] == "j-seq")
    assert converted.trigger["workflow"] == {}
    assert any("workflow DEF" in note for note in converted.notes)
    assert any("research" in note for note in converted.notes)


# ── the safe-by-default posture ──


def test_a_row_with_NOTES_loads_DISABLED_even_if_it_was_enabled(real_store):
    """Nothing fires on a schedule the migration could not fully
    interpret. The opposite default would
    run a half-understood automation unattended."""
    for tid in ("j-every", "j-seq"):
        converted = next(c for c in migrate_crons(real_store).converted if c.trigger["id"] == tid)
        assert converted.notes
        assert converted.trigger["enabled"] is False
        assert converted.trigger["state"] == "paused"


def test_a_CLEAN_row_stays_enabled(real_store):
    """The migration must not pause everything out of caution — that is its own kind of breakage."""
    converted = next(c for c in migrate_crons(real_store).converted if c.trigger["id"] == "j-cron")
    assert converted.notes == []
    assert converted.trigger["enabled"] is True
    assert converted.trigger["state"] == "active"


def test_a_row_with_NO_ID_is_refused_rather_than_given_one():
    """A generated id would be un-recognizable against the user's own file, and "which of my jobs is
    this" is the first question they would ask."""
    report = migrate_crons({"jobs": [{"name": "nameless"}]})
    assert report.converted == []
    assert len(report.refused) == 1
    assert report.lossless is False


def test_a_NON_DICT_row_is_refused_not_crashed():
    report = migrate_crons({"jobs": ["nonsense", 42, None]})
    assert len(report.refused) == 3


def test_an_EMPTY_store_migrates_to_nothing():
    report = migrate_crons({"version": 1, "jobs": []})
    assert report.converted == []
    assert report.lossless is True


def test_a_MALFORMED_store_does_not_raise():
    for garbage in ({}, {"jobs": None}, {"jobs": "no"}):
        assert migrate_crons(garbage).converted == []


# ── health rollups ──


def test_an_ERRORED_job_migrates_as_FAILING(real_store):
    converted = next(c for c in migrate_crons(real_store).converted if c.trigger["id"] == "j-seq")
    assert converted.trigger["health_status"] == "failing"
    assert converted.trigger["last_error_summary"] == "boom"


def test_a_NEVER_RUN_job_is_OK_not_unhealthy():
    """ "Never ran" is not "unhealthy". Showing a fresh job as failing
    would train the user to ignore
    the health column."""
    converted = convert_job({"id": "j", "name": "n", "last_status": ""})
    assert converted.trigger["health_status"] == "ok"


def test_a_never_run_job_has_NO_fabricated_timestamps():
    """Rendering epoch 0 as 1970-01-01 puts a date on screen that reads
    as a real event — the kind of
    thing a user tries to explain rather than dismiss."""
    converted = convert_job({"id": "j", "name": "n", "last_run_ts": 0, "last_failure_at": 0})
    assert converted.trigger["last_success_at"] == ""
    assert converted.trigger["last_failure_at"] == ""


# ── the report is reviewable before anything is replaced ──


def test_the_report_names_what_NEEDS_REVIEW(real_store):
    """So the conversion can be run as a dry run and read. A migration
    whose only output is a rewritten
    store is one nobody can check until it is too late."""
    report = migrate_crons(real_store)
    assert set(report.needs_review) == {"j-every", "j-seq"}


def test_DELIBERATE_drops_are_reported_separately_from_unaccounted_ones():
    """A dropped field was deliberately not carried and the map says why;
    an unaccounted one is a bug in
    the migration rather than a decision about the data."""
    converted = convert_job({"id": "j", "name": "n", "acked_items": ["a"], "last_result": "x"})
    assert set(converted.dropped) >= {"acked_items", "last_result"}
    assert converted.unaccounted == []
    assert converted.lossless is True


def test_duplicate_suppression_INTENT_survives_as_a_gate():
    """The legacy state is delivery-layer and not carried, but the job
    WAS deduping its output — so the
    trigger declares idempotency rather than losing the behaviour."""
    converted = convert_job({"id": "j", "name": "n", "last_posted_hash": "abc"})
    assert converted.trigger["gates"]["idempotency"] is True


def test_per_job_ENV_becomes_a_capability():
    converted = convert_job({"id": "j", "name": "n", "env": {"TOKEN": "x"}})
    assert converted.trigger["capabilities"]["env"] == {"TOKEN": "x"}


def test_the_map_still_covers_every_ScheduleJob_field():
    """Belt and braces with S62's own test: if a field is added to `ScheduleJob`, the migration must
    fail here rather than dropping it quietly."""
    import dataclasses as dc

    from personalclaw.schedule import ScheduleJob

    names = {f.name for f in dc.fields(ScheduleJob)}
    assert names <= set(LEGACY_FIELD_MAP["ScheduleJob"])
