"""`automation verify-migration` — the row-for-row diff (§7 step 2 / §8 — S91).

§7 step 2 names it: "row-for-row cron migration (old file read-only one release; `personalclaw
automation verify-migration` diff command)". §8 lists it as the mitigation for "Migration trust
(crons are the most-loved automations)".

**🔴 WHAT DRIVING IT AGAINST THE OWNER'S REAL STORE FOUND.** Four jobs migrate `lossless:
true`, and TWO come out disabled — `j-every` (a 5-minute interval) and `j-seq` (an
`agent_sequence`) were `enabled=True` in `crons.json` and land `enabled=False`.

That is not a bug: `migrate.convert_job` pauses any row that produced a note, and its comment is
right ("nothing fires on a schedule the migration could not fully interpret … the opposite
default would run a half-understood automation unattended"). But `lossless: true` beside two
silently-stopped automations is technically accurate and practically misleading, and closing
exactly that gap is why the plan put a diff command in the same breath as the migration.

So `VerifyReport.ok` is FALSE for a paused row while `migrate_crons`' `lossless` is TRUE — the
deliberate difference these tests pin.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.triggers import verify as V
from personalclaw.triggers.store import TriggerStore


def _crons(*jobs):
    return {"version": 1, "jobs": list(jobs)}


def _job(jid, kind="cron", *, enabled=True, **over):
    schedule = {
        "cron": {"kind": "cron", "cron_expr": "0 9 * * *"},
        "every": {"kind": "every", "every_secs": 300},
        "at": {"kind": "at", "at_ts": 1_893_456_000},
    }[kind]
    job = {
        "id": jid,
        "name": f"J-{jid}",
        "enabled": enabled,
        "schedule": schedule,
        "action": {"provider": "run-prompt", "config": {"message": "go"}},
    }
    job.update(over)
    return job


@pytest.fixture
def home(tmp_path):
    return tmp_path


def _write_crons(home, payload):
    path = home / "crons.json"
    path.write_text(json.dumps(payload))
    return path


def _migrated(home, payload):
    """A home with `crons.json` written and the migration run — the normal post-migration state."""
    _write_crons(home, payload)
    store = TriggerStore(base_dir=home)
    store.migrate_from_crons()
    return store


# ── before the migration: everything is missing ──


def test_an_unmigrated_store_reports_every_job_missing(home):
    """The one true data-loss class, and what a row-for-row diff exists to make impossible
    to miss."""
    _write_crons(home, _crons(_job("a"), _job("b")))
    report = V.verify_home(home)
    assert report.missing == ["a", "b"]
    assert report.ok is False


def test_the_render_tells_the_user_crons_json_is_intact(home):
    """A user seeing "missing" needs to know their source file survived before they panic."""
    _write_crons(home, _crons(_job("a")))
    text = V.render(V.verify_home(home))
    assert "MISSING" in text
    assert "crons.json is still intact" in text


# ── 🔴 the paused-but-lossless gap ──


def test_a_paused_row_makes_verify_NOT_ok_even_though_the_migration_was_lossless(home):
    """🔴 THE finding, reproduced. `migrate_crons` reports `lossless: true` for an `every` cron
    because nothing was LOST — the data is all there, the automation just is not running. A user
    asking "did my migration work" means "are my automations still running", and answering the
    narrower question is how someone's 5-minute job stops silently.
    """
    from personalclaw.triggers.migrate import migrate_crons

    payload = _crons(_job("j-every", "every"))
    migration = migrate_crons(payload)
    assert migration.lossless is True  # the migration's own verdict

    _migrated(home, payload)
    report = V.verify_home(home)
    assert report.paused == ["j-every"]
    assert report.ok is False  # verify's verdict differs, deliberately


def test_the_paused_row_carries_the_migrations_own_note(home):
    """ "2 need review" sends a user hunting; the note tells them what to do. Taken verbatim from
    the migration rather than re-worded, so the explanation cannot drift from the decision.
    """
    _migrated(home, _crons(_job("j-every", "every")))
    report = V.verify_home(home)
    row = next(r for r in report.rows if r.job_id == "j-every")
    assert "interval" in row.note
    assert "one-shot" in row.note


def test_the_render_lists_each_paused_row_with_its_reason(home):
    _migrated(home, _crons(_job("j-every", "every"), _job("ok")))
    text = V.render(V.verify_home(home))
    assert "PAUSED by the migration (1)" in text
    assert "j-every:" in text
    assert "re-enable" in text


def test_a_row_that_was_ALREADY_disabled_is_not_reported_as_paused(home):
    """Only a row that WAS running and now is not deserves attention. Reporting an already-off
    automation would train the user to ignore the section.
    """
    _migrated(home, _crons(_job("off", "every", enabled=False)))
    assert V.verify_home(home).paused == []


def test_re_enabling_makes_the_report_clean(home):
    """The workflow the command exists to serve: read the note, re-enable, verify green."""
    store = _migrated(home, _crons(_job("j-every", "every")))
    assert V.verify_home(home).ok is False
    store.set_enabled("j-every", True)
    report = V.verify_home(home)
    assert report.ok is True
    assert "migrated cleanly" in V.render(report)


# ── field drift ──


def test_a_dropped_timing_field_is_reported(home):
    """§1.3's quietly-losable class: "a dropped `skip_dates` fires on a holiday and nobody knows
    why".
    """
    store = _migrated(home, _crons(_job("a", skip_dates=["2026-12-25"])))
    row = next(r for r in V.verify_home(home).rows if r.job_id == "a")
    # The migration DOES carry skip_dates, so this asserts the check does not false-positive.
    assert row.field_drift == []
    assert store.get("a").trigger.spec.get("skip_dates") == ["2026-12-25"]


def test_drift_compares_presence_not_equality(home):
    """The migration legitimately renames (`strict_schedule` → `strict`) and re-types (an epoch
    `at_ts` → an `at`). Demanding equal values would report drift on every correctly-converted
    row.
    """
    _migrated(home, _crons(_job("a", strict_schedule=True)))
    row = next(r for r in V.verify_home(home).rows if r.job_id == "a")
    assert row.field_drift == []


def test_a_field_the_user_never_set_is_not_drift(home):
    _migrated(home, _crons(_job("a")))
    row = next(r for r in V.verify_home(home).rows if r.job_id == "a")
    assert row.field_drift == []


def test_drift_is_detected_when_a_field_really_is_absent(home):
    """Driven by comparing against a trigger whose spec genuinely lacks the field, so the check is
    shown to be capable of failing rather than merely never firing.
    """
    from personalclaw.triggers.models import Trigger

    trigger = Trigger(id="a", name="A", kind="clock", spec={"kind": "cron", "expr": "0 9 * * *"})
    drift = V._field_drift({"skip_dates": ["2026-12-25"], "timezone": "UTC"}, trigger)
    assert sorted(drift) == ["skip_dates", "timezone"]


# ── damage after the fact ──


def test_a_row_deleted_from_the_new_store_shows_up_as_missing(home):
    """The diff is the safety net for anything that happens AFTER the migration too."""
    store = _migrated(home, _crons(_job("a"), _job("b")))
    store.delete("a")
    report = V.verify_home(home)
    assert report.missing == ["a"]
    assert report.ok is False


def test_a_broken_migrated_row_is_reported_as_unparseable(home):
    """A row the entity refuses is `enabled=False` and invisible to the paused check (it was never
    enabled), so it needs its own line.
    """
    _write_crons(home, _crons(_job("a")))
    store = TriggerStore(base_dir=home)
    store.path.write_text(
        json.dumps({"version": 1, "triggers": [{"id": "a", "name": "A", "kind": "clok"}]})
    )
    report = V.verify_home(home)
    assert report.broken == ["a"]
    assert "UNPARSEABLE" in V.render(report)


# ── degradation, never a false green ──


def test_a_missing_crons_file_is_not_a_clean_migration(home):
    """🔴 An unreadable legacy file must not report as ok. Distinct from "no differences": a check
    that never ran is not a check that passed.
    """
    report = V.verify_home(home)
    assert report.ok is False
    assert report.unreadable
    assert "cannot verify" in V.render(report)


def test_an_unreadable_crons_file_reports_why(home):
    (home / "crons.json").write_text("{not json")
    report = V.verify_home(home)
    assert report.ok is False
    assert "unreadable" in report.unreadable


def test_a_legacy_row_with_no_id_is_reported_not_skipped(home):
    """`migrate_crons` refuses an id-less row ("a generated id would be un-recognizable against the
    user's file"), so the diff has to say one existed.
    """
    _write_crons(home, _crons({"name": "nameless", "enabled": True}))
    report = V.verify_home(home)
    assert report.missing == ["<no id>"]


def test_a_bare_list_crons_payload_is_accepted(home):
    """A hand-edited file that dropped the envelope still verifies."""
    (home / "crons.json").write_text(json.dumps([_job("a")]))
    assert V.verify_home(home).missing == ["a"]


def test_verify_writes_nothing(home):
    """A verify that mutated would be a migration; the whole point is being safe to run before
    deciding.
    """
    store = _migrated(home, _crons(_job("j-every", "every")))
    before = store.path.read_text()
    crons_before = (home / "crons.json").read_text()
    V.verify_home(home)
    assert store.path.read_text() == before
    assert (home / "crons.json").read_text() == crons_before


def test_the_render_states_the_legacy_file_was_not_modified(home):
    """§7: "old file read-only one release". The user should not have to trust that silently."""
    _migrated(home, _crons(_job("j-every", "every")))
    assert "READ-ONLY" in V.render(V.verify_home(home))


# ── the whole legacy vocabulary, in the owner's real shape ──


def test_every_legacy_clock_kind_verifies(home):
    """The shape of the owner's real store: a cron, an interval, a one-shot, and a sequence."""
    store = _migrated(
        home,
        _crons(
            _job("j-cron", "cron", skip_dates=["2026-12-25"], timezone="Europe/London"),
            _job("j-every", "every"),
            _job("j-at", "at", delete_after_run=True),
            _job("j-seq", "cron", agent_sequence=["research", "draft"]),
        ),
    )
    report = V.verify_home(home)
    assert report.missing == []
    assert report.drifted == []
    # The two that need review are exactly the interval and the sequence.
    assert sorted(report.paused) == ["j-every", "j-seq"]
    for tid in report.paused:
        store.set_enabled(tid, True)
    assert V.verify_home(home).ok is True


def test_the_report_order_follows_the_legacy_file(home):
    """So a user can read it next to their own `crons.json`, top to bottom — the same reason
    `migrate_crons` preserves order.
    """
    _migrated(home, _crons(_job("z"), _job("a"), _job("m")))
    assert [r.job_id for r in V.verify_home(home).rows] == ["z", "a", "m"]


def test_the_report_serializes(home):
    _migrated(home, _crons(_job("j-every", "every")))
    payload = V.verify_home(home).to_dict()
    assert payload["ok"] is False
    assert payload["paused"] == ["j-every"]
    assert payload["rows"][0]["note"]


# ── the CLI command ──


def _run_cli(home, *args):
    """Drive the real console entry point, not `python -m`.

    Measured: `python -m personalclaw.cli` exits 0 doing NOTHING — the module has no `__main__`
    guard, so `main()` never runs. A test invoking it that way would pass against a command that
    does not exist.
    """
    import os
    import subprocess
    import sys

    binary = Path(sys.executable).parent / "personalclaw"
    result = subprocess.run(
        [str(binary), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "PERSONALCLAW_HOME": str(home)},
    )
    return result.returncode, result.stdout + result.stderr


@pytest.mark.skipif(
    not (Path(__import__("sys").executable).parent / "personalclaw").exists(),
    reason="console entry point not installed in this environment",
)
def test_the_cli_exits_nonzero_when_the_migration_needs_attention(home):
    """🔴 A read-only diff that always exited 0 could not gate anything, and gating the cutover is
    why §8 lists this command as the migration-trust mitigation.
    """
    _migrated(home, _crons(_job("j-every", "every")))
    code, out = _run_cli(home, "automation", "verify-migration")
    assert code == 1
    assert "PAUSED" in out


@pytest.mark.skipif(
    not (Path(__import__("sys").executable).parent / "personalclaw").exists(),
    reason="console entry point not installed in this environment",
)
def test_the_cli_exits_zero_when_clean(home):
    store = _migrated(home, _crons(_job("j-every", "every")))
    store.set_enabled("j-every", True)
    code, out = _run_cli(home, "automation", "verify-migration")
    assert code == 0
    assert "migrated cleanly" in out


@pytest.mark.skipif(
    not (Path(__import__("sys").executable).parent / "personalclaw").exists(),
    reason="console entry point not installed in this environment",
)
def test_the_cli_emits_json_for_a_script(home):
    _migrated(home, _crons(_job("j-every", "every")))
    code, out = _run_cli(home, "automation", "verify-migration", "--json")
    payload = json.loads(out)
    assert code == 1
    assert payload["paused"] == ["j-every"]
