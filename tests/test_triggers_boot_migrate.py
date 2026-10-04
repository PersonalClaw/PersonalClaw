"""The boot migration + the schedule projection.

**🔴 THE GAP, measured before writing.** `store.migrate_from_crons()` exists, is documented as
idempotent, and was called by **nothing outside tests**. So on a real machine `triggers.json` is
EMPTY — every cron lives only in `crons.json`. Two consequences that block the rest of the cutover:

* Re-pointing `/api/triggers`' schedule backend at the store would show the user **zero schedules**
  while their crons kept firing from the legacy service.
* The tick has nothing to fire: S96 armed the clock and S97 made `overlap` enforce, but both act on
  rows that were never imported.

**🔴 AND THE MIGRATION WAS NOT ACTUALLY IDEMPOTENT.** Driven against a copy of the owner's real
`crons.json`: boot armed `j-cron`, and the NEXT boot's migration blanked the arm — a plain `upsert`
of the freshly converted row overwrote `next_fire_at`, `run_count` and the health fields with the
empty values a conversion produces. So every boot re-armed the trigger, which re-phases a schedule
(a 9am job armed at 03:00 becomes "next 9am from now") and loses the run history the UI reads.

The import now reads `crons.json` once per home and never over a row the store already has
(`triggers/legacy_import.py`), so a later boot has nothing to re-migrate at all; the properties
the idempotency fix protected are still pinned below as what a restart must not undo. Those tests
use a READ-ONLY action on purpose: the import carries a read-only job's switch as it was, while a
job that would run anything needing a grant arrives off to wait for the owner
(`test_legacy_trigger_import.py`), and a test of arming run against an off row would pass by
arming nothing.
"""

from __future__ import annotations

import json
import time

import pytest

from personalclaw.triggers import boot_migrate as BM
from personalclaw.triggers import schedule_view as SV
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z

_ACTION = {"provider": "notify", "config": {"title_template": "Nightly"}}

#: An agent job, for the projection test: the fields a schedule row publishes for an agent.
_AGENT_ACTION = {
    "provider": "invoke-agent",
    "config": {"task_template": "go", "agent": "coder", "model": "m", "approval_mode": "auto"},
}


def _crons(tmp_path, *jobs):
    (tmp_path / "crons.json").write_text(json.dumps({"version": 1, "jobs": list(jobs)}))


def _job(jid="j", kind="cron", **over):
    schedule = {
        "cron": {"kind": "cron", "cron_expr": "0 9 * * *"},
        "every": {"kind": "every", "every_secs": 300},
        "at": {"kind": "at", "at_ts": NOW + 3600},
    }[kind]
    job = {"id": jid, "name": f"J-{jid}", "enabled": True, "schedule": schedule, "action": _ACTION}
    job.update(over)
    return job


# ── 🔴 the migration now runs, and arms ──


def test_boot_imports_crons_into_the_store(tmp_path):
    """🔴 THE gap. Nothing called this before, so the unified store was empty on a real machine."""
    _crons(tmp_path, _job("j-cron"))
    report = BM.migrate_and_arm(tmp_path, now=NOW)
    assert report["converted"] == 1
    assert report["written"] == 1
    assert TriggerStore(base_dir=tmp_path).get("j-cron") is not None


def test_an_imported_cron_is_ARMED_not_left_inert(tmp_path):
    """The finding: an imported cron has an empty `next_fire_at`, and `due_ids` only surfaces rows
    that have one. Importing without arming leaves the whole clock half inert."""
    _crons(tmp_path, _job("j-cron"))
    report = BM.migrate_and_arm(tmp_path, now=NOW)
    assert report["armed"] == ["j-cron"]
    armed = TriggerStore(base_dir=tmp_path).get("j-cron").trigger.next_fire_at
    assert armed  # a real timestamp, not ""


def test_an_armed_import_becomes_due(tmp_path):
    """The property that matters: after boot, the tick can actually see it."""
    from personalclaw.triggers import service as SVC

    _crons(tmp_path, _job("j-cron"))
    BM.migrate_and_arm(tmp_path, now=NOW)
    store = TriggerStore(base_dir=tmp_path)
    triggers = [r.trigger for r in store.load()]
    due_now = SVC.due_ids(triggers, now=NOW)
    due_later = SVC.due_ids(triggers, now=NOW + 86_400)
    assert due_now == []
    assert due_later == ["j-cron"]


def test_an_unarmable_row_is_skipped_not_armed_to_now(tmp_path):
    """An ELAPSED one-shot must not be armed — that would fire a missed appointment immediately."""
    _crons(tmp_path, _job("j-past", "at", schedule={"kind": "at", "at_ts": NOW - 3600}))
    report = BM.migrate_and_arm(tmp_path, now=NOW)
    assert report["armed"] == []
    assert TriggerStore(base_dir=tmp_path).get("j-past").trigger.next_fire_at == ""


def test_a_disabled_row_is_not_armed(tmp_path):
    _crons(tmp_path, _job("j-off", enabled=False))
    assert BM.migrate_and_arm(tmp_path, now=NOW)["armed"] == []


# ── 🔴 the idempotency defect ──


def test_a_second_boot_arms_nothing(tmp_path):
    """🔴 THE defect. The second boot's migration blanked the arm, so every boot re-armed — which
    re-phases a schedule (a 9am job armed at 03:00 becomes "next 9am from now")."""
    _crons(tmp_path, _job("j-cron"))
    BM.migrate_and_arm(tmp_path, now=NOW)
    armed = TriggerStore(base_dir=tmp_path).get("j-cron").trigger.next_fire_at
    second = BM.migrate_and_arm(tmp_path, now=NOW + 100)
    assert second["armed"] == []
    assert TriggerStore(base_dir=tmp_path).get("j-cron").trigger.next_fire_at == armed


def test_a_re_migration_preserves_run_history(tmp_path):
    """🔴 The same defect's other half: `run_count`/`last_run_id`/health are what has HAPPENED to the
    trigger. `crons.json` is the source of truth for what the job IS, not for its history."""
    _crons(tmp_path, _job("j-cron"))
    BM.migrate_and_arm(tmp_path, now=NOW)
    store = TriggerStore(base_dir=tmp_path)
    trigger = store.get("j-cron").trigger
    trigger.run_count = 7
    trigger.last_run_id = "run-7"
    trigger.last_success_at = "2027-01-14T09:00:00Z"
    store.upsert(trigger)

    BM.migrate_and_arm(tmp_path, now=NOW + 200)
    after = TriggerStore(base_dir=tmp_path).get("j-cron").trigger
    assert after.run_count == 7
    assert after.last_run_id == "run-7"
    assert after.last_success_at == "2027-01-14T09:00:00Z"


def test_a_trigger_authored_directly_in_the_store_survives_a_migration(tmp_path):
    """The store's own promise: an import upserts by id rather than replacing the store."""
    from personalclaw.triggers.models import Trigger

    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id="file:mine",
            name="Mine",
            kind="file",
            enabled=True,
            spec={"paths": ["~/notes/**"]},
            workflow={"provider": "run-prompt", "config": {}},
        )
    )
    _crons(tmp_path, _job("j-cron"))
    BM.migrate_and_arm(tmp_path, now=NOW)
    assert TriggerStore(base_dir=tmp_path).get("file:mine") is not None


# ── boot safety ──


def test_a_missing_crons_file_is_not_an_error(tmp_path):
    report = BM.migrate_and_arm(tmp_path, now=NOW)
    assert report["converted"] == 0
    assert report["reason"] == "no crons.json"


def test_an_unreadable_crons_file_does_not_raise(tmp_path):
    """🔴 This runs during gateway boot. A gateway that refused to start because a cron file had a
    typo would be far worse than one that starts and reports the problem."""
    (tmp_path / "crons.json").write_text("{not json")
    report = BM.migrate_and_arm(tmp_path, now=NOW)
    assert report["converted"] == 0
    assert "unreadable" in report["reason"]


def test_the_legacy_file_is_renamed_never_deleted(tmp_path):
    """Imported once, then renamed `crons.json.imported-<date>` with its bytes intact:
    `verify-migration` diffs against that copy, and renaming it back gives an older build its
    store."""
    _crons(tmp_path, _job("j-cron"))
    before = (tmp_path / "crons.json").read_text()
    BM.migrate_and_arm(tmp_path, now=NOW)
    day = time.strftime("%Y-%m-%d", time.localtime(NOW))
    assert not (tmp_path / "crons.json").exists()
    assert (tmp_path / f"crons.json.imported-{day}").read_text() == before


def test_verify_runs_at_boot_and_reports_paused_rows(tmp_path):
    """The finding, surfaced where it is actionable: `lossless: true` is NOT the bar, because
    a row can migrate lossless AND disabled."""
    _crons(tmp_path, _job("j-every", "every"))
    BM.migrate_and_arm(tmp_path, now=NOW)
    report = BM.verify_report(tmp_path)
    assert report.get("paused") == ["j-every"]
    assert report.get("ok") is False


# ── the schedule projection (the re-point precondition) ──


def _converted_into_the_store(tmp_path, job):
    """A converted cron written straight into the store and armed — the projection's input.

    Not through the boot import: that switches an agent job off and drops its `approval_mode`
    until the owner reviews it (`legacy_import.admit`), and this test is about what a schedule row
    PUBLISHES for every field a converted row can carry, the agent's included."""
    from personalclaw.triggers.migrate import convert_job
    from personalclaw.triggers.models import parse_trigger

    trigger, _issues = parse_trigger(convert_job(job).trigger)
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(trigger)
    BM.arm_unarmed(store, now=NOW)
    return store.get(job["id"]).trigger


def test_the_projection_covers_every_field_the_api_publishes(tmp_path):
    """🔴 The re-point's real contract: a store-backed row must render in the SAME wire shape the API
    already publishes from a `ScheduleJob`, or the frontend silently loses fields. Compared
    field-for-field against the live serializer while building this."""
    trigger = _converted_into_the_store(
        tmp_path,
        _job(
            "j-cron",
            action=_AGENT_ACTION,
            channel="C1",
            silent=False,
            timezone="America/New_York",
            skip_dates=["2027-12-25"],
            strict_schedule=True,
            last_status="ok",
            last_error="boom",
        ),
    )
    row = SV.to_schedule_row(trigger, now=NOW, base_dir=tmp_path)

    assert row["kind"] == "schedule"
    assert row["id"] == "schedule:j-cron"
    assert row["raw_id"] == "j-cron"
    assert row["enabled"] is True
    assert row["cron_expr"] == "0 9 * * *"
    assert row["timezone"] == "America/New_York"
    assert row["skip_dates"] == ["2027-12-25"]
    assert row["strict_schedule"] is True
    assert row["channel"] == "C1"
    assert row["silent"] is False
    assert row["agent"] == "coder"
    assert row["model"] == "m"
    assert row["approval_mode"] == "auto"
    assert row["action"]["provider"] == "invoke-agent"
    assert row["last_status"] == "ok"
    assert row["last_error"] == "boom"
    assert row["next_run_ts"] and row["next_run_ts"] > NOW


def test_the_cadence_string_matches_the_shipped_formatter(tmp_path):
    """🔴 Delegates to `schedule.format_schedule`. A hand-rolled version produced
    `0 9 * * * (America/New_York)` where the live API produces `At 9:00 AM EDT` — worse prose AND a
    second formatter that would drift from the one the rest of the UI reads."""
    from personalclaw.schedule import ScheduleDefinition, format_schedule

    _crons(tmp_path, _job("j-cron", timezone="America/New_York"))
    BM.migrate_and_arm(tmp_path, now=NOW)
    trigger = TriggerStore(base_dir=tmp_path).get("j-cron").trigger
    expected = format_schedule(
        ScheduleDefinition(kind="cron", cron_expr="0 9 * * *"), tz_name="America/New_York"
    )
    assert SV.describe_cadence(trigger) == expected


def test_delivery_and_session_are_read_from_their_new_addresses(tmp_path):
    """`LEGACY_FIELD_MAP` moved `channel`→`delivery` and `session_key`→`session`. Reading the action
    config for them (where they used to be) would render empty."""
    _crons(tmp_path, _job("j", channel="C9", persistent_session=True, session_key="cron:j"))
    BM.migrate_and_arm(tmp_path, now=NOW)
    trigger = TriggerStore(base_dir=tmp_path).get("j").trigger
    assert SV.channel_of(trigger) == "C9"
    assert SV.session_key_of(trigger) == "cron:j"
    assert SV.to_schedule_row(trigger, now=NOW, base_dir=tmp_path)["has_session"] is True


def test_a_silent_job_projects_silent(tmp_path):
    """`delivery == "none"` IS silent — the map's own spelling."""
    _crons(tmp_path, _job("j", silent=True))
    BM.migrate_and_arm(tmp_path, now=NOW)
    trigger = TriggerStore(base_dir=tmp_path).get("j").trigger
    assert SV.is_silent(trigger) is True
    assert SV.to_schedule_row(trigger, now=NOW, base_dir=tmp_path)["silent"] is True


def test_the_deliberate_drops_are_None_not_fabricated(tmp_path):
    """🔴 `created_ts`, `last_result` and `acked_items` map to None in `LEGACY_FIELD_MAP` — decisions
    the plan already made. Inventing a creation date would be a lie the UI renders as fact, and a
    copy of a run's output on the trigger was a second truth that could disagree with the run
    record. `acked_items` was verified DEAD before dropping: the ack route has zero callers and the
    owner's real store carries zero acked entries."""
    _crons(tmp_path, _job("j"))
    BM.migrate_and_arm(tmp_path, now=NOW)
    row = SV.to_schedule_row(TriggerStore(base_dir=tmp_path).get("j").trigger, now=NOW)
    assert row["created_ts"] is None
    assert row["last_result"] is None


def test_running_state_comes_from_the_claim_store(tmp_path):
    """The whole point: `is_running` is answerable from an API process that does not own the
    scheduler loop, because a claim is a file rather than a process-local dict."""
    from personalclaw.triggers import claims
    from personalclaw.triggers.scheduling import Claim

    _crons(tmp_path, _job("j"))
    BM.migrate_and_arm(tmp_path, now=NOW)
    trigger = TriggerStore(base_dir=tmp_path).get("j").trigger
    assert SV.to_schedule_row(trigger, now=NOW, base_dir=tmp_path)["is_running"] is False
    claims.write_claim(Claim(trigger_id="j", holder="tick", claimed_at=NOW), base_dir=tmp_path)
    row = SV.to_schedule_row(trigger, now=NOW, base_dir=tmp_path)
    assert row["is_running"] is True
    assert row["running_since"] == NOW


@pytest.mark.parametrize("kind", ["cron", "every", "at"])
def test_every_legacy_clock_kind_projects(kind, tmp_path):
    """A kind the projection cannot render would show a blank row for a working automation."""
    _crons(tmp_path, _job("j", kind))
    BM.migrate_and_arm(tmp_path, now=NOW)
    row = SV.to_schedule_row(TriggerStore(base_dir=tmp_path).get("j").trigger, now=NOW)
    assert row["schedule"]  # a non-empty cadence string
    assert row["id"] == "schedule:j"


def test_the_chat_created_flat_workflow_shape_also_projects(tmp_path):
    """🔴 Two action shapes exist in a real store: a migrated cron nests under `workflow.inline`,
    while the chat tools write a FLAT `{provider, config}`. Reading only one would render an empty
    action for half the rows."""
    from personalclaw.triggers.models import Trigger

    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id="clock:mine",
            name="Mine",
            kind="clock",
            enabled=True,
            spec={"kind": "cron", "expr": "0 9 * * *"},
            workflow={"provider": "run-prompt", "config": {"message": "go"}},
        )
    )
    row = SV.to_schedule_row(store.get("clock:mine").trigger, now=NOW, base_dir=tmp_path)
    assert row["action"]["provider"] == "run-prompt"
    assert row["message"] == "go"


def test_the_gateway_boots_the_migration(tmp_path):
    """🔴 A migration nothing calls is the defect this session opened with. Assert the boot path
    calls it — the source, since the alternative is a function nobody invokes."""
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    src = inspect.getsource(GatewayOrchestrator._init_cron)
    assert "migrate_and_arm" in src


# ── 🔴 #461: the idempotency defect's THIRD half — the off switch ─────────────
#
# A user disabled a trigger, the next boot re-migrated, and `crons.json`'s stale `enabled: true`
# came back — re-enabled AND re-armed, because `needs_arming` then selected it. Nothing
# re-migrates any more (the file is imported once), and these pin what a restart must still not do.
#
# Whether an automation is switched on is a fact about what has HAPPENED to a trigger (a person
# turned it off), not about what it IS. That is the same line the rest of this section draws.


def test_a_re_migration_PRESERVES_a_user_disable(tmp_path):
    """🔴 THE defect. Turning an automation off and restarting turned it back on."""
    _crons(tmp_path, _job("j-cron"))  # legacy row says enabled: true
    BM.migrate_and_arm(tmp_path, now=NOW)
    store = TriggerStore(base_dir=tmp_path)
    assert store.set_enabled("j-cron", False) is not None

    BM.migrate_and_arm(tmp_path, now=NOW + 200)
    after = TriggerStore(base_dir=tmp_path).get("j-cron").trigger
    assert after.enabled is False, "the boot migration re-asserted the legacy enabled value"


def test_a_re_enabled_trigger_is_ALSO_preserved(tmp_path):
    """The mirror, and not redundant: a test that only pinned `False` would pass against a restart
    that switched everything off."""
    _crons(tmp_path, _job("j-off", enabled=False))
    BM.migrate_and_arm(tmp_path, now=NOW)
    store = TriggerStore(base_dir=tmp_path)
    assert store.set_enabled("j-off", True) is not None

    BM.migrate_and_arm(tmp_path, now=NOW + 200)
    assert TriggerStore(base_dir=tmp_path).get("j-off").trigger.enabled is True


def test_a_preserved_disable_is_not_re_armed(tmp_path):
    """The user-visible consequence, stated as a behaviour. A trigger that came back enabled also
    came back ARMED, so it advertised a countdown and then fired — which is the harm, not the flag.
    """
    from personalclaw.triggers import service as SVC

    _crons(tmp_path, _job("j-cron"))
    BM.migrate_and_arm(tmp_path, now=NOW)
    TriggerStore(base_dir=tmp_path).set_enabled("j-cron", False)

    BM.migrate_and_arm(tmp_path, now=NOW + 200)
    triggers = [r.trigger for r in TriggerStore(base_dir=tmp_path).load()]
    assert SVC.due_ids(triggers, now=NOW + 86_400) == [], "a disabled trigger became due again"


def test_a_first_import_keeps_a_legacy_job_that_was_off_OFF(tmp_path):
    """Vacuity floor: the import takes the legacy file's word for a switch it carries, so a job
    that was off in the older version is still off here."""
    _crons(tmp_path, _job("j-new", enabled=False))
    BM.migrate_and_arm(tmp_path, now=NOW)
    assert TriggerStore(base_dir=tmp_path).get("j-new").trigger.enabled is False


def test_a_start_takes_the_retry_nothing_read_out_of_every_automation(tmp_path):
    """Every row an earlier release wrote carries `retry`, which nothing ever read: a step's
    retries are its own. The next start takes it out and leaves the rest of each row as it was,
    so no automation reads on the Triggers page as carrying a field nothing knows; the start after
    finds nothing to take."""
    from personalclaw.triggers.models import Trigger

    store = TriggerStore(base_dir=tmp_path)
    for tid in ("clock:nightly", "clock:weekly"):
        store.upsert(
            Trigger(
                id=tid,
                name=tid,
                kind="clock",
                created_by="user",
                spec={"kind": "cron", "expr": "0 9 * * *"},
                workflow={"inline": _ACTION},
            )
        )
    path = tmp_path / "triggers.json"
    stored = json.loads(path.read_text())
    for row in stored["triggers"]:
        row["retry"] = {}
    path.write_text(json.dumps(stored))
    assert all(r.warnings for r in TriggerStore(base_dir=tmp_path).load())
    before = {r.trigger.id: r.trigger.to_dict() for r in TriggerStore(base_dir=tmp_path).load()}

    BM.migrate_and_arm(tmp_path, now=NOW)
    BM.migrate_and_arm(tmp_path, now=NOW)

    assert all("retry" not in row for row in json.loads(path.read_text())["triggers"])
    after = TriggerStore(base_dir=tmp_path).load()
    assert sorted(r.trigger.id for r in after) == sorted(before)
    for row in after:
        assert row.ok and not row.warnings, row.issues
        assert {k: v for k, v in row.trigger.to_dict().items() if k != "next_fire_at"} == {
            k: v for k, v in before[row.trigger.id].items() if k != "next_fire_at"
        }
