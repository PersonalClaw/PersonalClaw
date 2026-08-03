"""The boot migration + the schedule projection (§7 step 2 / §6 — S98).

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
(a 9am job armed at 03:00 becomes "next 9am from now") and loses the run history the UI reads. The
store's own docstring claimed idempotency, and it held for config only.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.triggers import boot_migrate as BM
from personalclaw.triggers import schedule_view as SV
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z

_ACTION = {
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
    """S96's finding: an imported cron has an empty `next_fire_at`, and `due_ids` only surfaces rows
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


def test_a_re_migration_still_picks_up_a_CONFIG_change(tmp_path):
    """The other direction — runtime state is carried, but CONFIG is refreshed from the legacy
    file, which is what keeps `crons.json` authoritative for what the job IS this release.
    Renaming it there and re-running boot must move the name; carrying everything would freeze
    the config."""
    _crons(tmp_path, _job("j-cron", name="Original"))
    BM.migrate_and_arm(tmp_path, now=NOW)
    assert TriggerStore(base_dir=tmp_path).get("j-cron").trigger.name == "Original"

    _crons(tmp_path, _job("j-cron", name="Renamed"))
    BM.migrate_and_arm(tmp_path, now=NOW + 10)
    assert TriggerStore(base_dir=tmp_path).get("j-cron").trigger.name == "Renamed"


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


def test_the_legacy_file_is_left_on_disk(tmp_path):
    """§6: "old file read-only one release" — `verify-migration` needs both sides to diff."""
    _crons(tmp_path, _job("j-cron"))
    before = (tmp_path / "crons.json").read_text()
    BM.migrate_and_arm(tmp_path, now=NOW)
    assert (tmp_path / "crons.json").read_text() == before


def test_verify_runs_at_boot_and_reports_paused_rows(tmp_path):
    """S91's finding, surfaced where it is actionable: `lossless: true` is NOT the bar, because
    a row can migrate lossless AND disabled."""
    _crons(tmp_path, _job("j-every", "every"))
    BM.migrate_and_arm(tmp_path, now=NOW)
    report = BM.verify_report(tmp_path)
    assert report.get("paused") == ["j-every"]
    assert report.get("ok") is False


# ── the schedule projection (§6's re-point precondition) ──


def test_the_projection_covers_every_field_the_api_publishes(tmp_path):
    """🔴 The re-point's real contract: a store-backed row must render in the SAME wire shape the API
    already publishes from a `ScheduleJob`, or the frontend silently loses fields. Compared
    field-for-field against the live serializer while building this."""
    _crons(
        tmp_path,
        _job(
            "j-cron",
            channel="C1",
            silent=False,
            timezone="America/New_York",
            skip_dates=["2027-12-25"],
            strict_schedule=True,
            last_status="ok",
            last_error="boom",
        ),
    )
    BM.migrate_and_arm(tmp_path, now=NOW)
    trigger = TriggerStore(base_dir=tmp_path).get("j-cron").trigger
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
    """🔴 Delegates to `schedule.format_schedule`. Measured: a hand-rolled version produced
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
    """S97's whole point: `is_running` is answerable from an API process that does not own the
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
    while S92's chat tools write a FLAT `{provider, config}`. Reading only one would render an empty
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
