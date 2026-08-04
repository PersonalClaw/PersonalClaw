"""`TriggerService`'s tick — §3 / §3.1 (S88).

§3: "One asyncio loop — the existing single re-armed `_arm_timer` task generalized … computes the
earliest `next_fire_at` … sleeps until it (capped at 30s for external-edit pickup via mtime
`_sync`),
coalescing same-second firings."

**Buildable because S87 shipped the store.** S83/S86 recorded "the store and the service are one
unbuilt foundation"; that was half wrong — the service needs the store, not the reverse. Every
dependency was verified importable before this file existed.

**The boundary this file defends:** §3.2 says "the scheduler never executes directly".
`tick()` returns
the fires that passed every gate; a WakeupDispatcher runs them. A service that both decided
and executed
would make crash-safety untestable, since §3.2's safety comes from the payload surviving in an
inbox.

Every test drives a REAL `TriggerStore` on `tmp_path`. The type-seam defect this session found
(`next_fire_at` is `str` on the entity, `float` in `scheduling`) is invisible to a mocked store.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest

from personalclaw.triggers import service as SVC
from personalclaw.triggers.models import Outcome, Trigger
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


def _trigger(tid="t1", *, next_at=0.0, interval=3600, enabled=True, **over):
    base = dict(
        id=tid,
        name=f"T-{tid}",
        kind="clock",
        enabled=enabled,
        spec={"kind": "interval", "interval_secs": interval},
        workflow={"provider": "run-prompt", "config": {"message": "go"}},
        capabilities={"providers": ["run-prompt"]},
        next_fire_at=SVC.to_iso(next_at) if next_at else "",
    )
    base.update(over)
    return Trigger(**base)


def _tick(store, **over):
    return asyncio.run(SVC.tick(store, now=over.pop("now", NOW), **over))


# ── the type seam ──


def test_an_iso_timestamp_converts_to_an_epoch():
    """🔴 THE defect this session found, by driving a tick against a real store.

    `Trigger.next_fire_at` is declared `str` — the entity keeps every timestamp as ISO, which
    is right
    for a JSON row a human may edit. But `scheduling.is_due`/`boot_recovery`/`next_wake_delay`
    all take
    `float` epochs, and nothing converted. A round-tripped trigger came back with `'1234.5'`
    and every
    comparison against `now` raised `TypeError: '>' not supported between instances of 'str' and
    'float'`.
    """
    iso = datetime.fromtimestamp(NOW, tz=timezone.utc).isoformat()
    assert SVC.to_epoch(iso) == pytest.approx(NOW)


def test_a_stringified_epoch_also_converts():
    """`Trigger.to_dict()` round-trips a float assigned to the `str` field as `'1234.5'`, so
    the seam
    has to accept that shape too — it is what the store actually contained."""
    assert SVC.to_epoch("1234.5") == 1234.5


def test_a_numeric_passes_through():
    """A caller holding a fresh epoch should not have to stringify it first."""
    assert SVC.to_epoch(NOW) == NOW


@pytest.mark.parametrize("value", ["", None, "not-a-time", "2026-13-45T99:99:99"])
def test_an_unusable_timestamp_is_treated_as_unset(value):
    """0.0 rather than raising: an unparseable timestamp on ONE row must not stop the tick
    that serves
    every other trigger."""
    assert SVC.to_epoch(value) == 0.0


def test_the_round_trip_is_lossless_to_the_second():
    assert SVC.to_epoch(SVC.to_iso(NOW)) == pytest.approx(NOW)


def test_an_empty_epoch_yields_an_empty_string_not_epoch_zero():
    """`to_iso(0)` must be "" — writing `1970-01-01` into `next_fire_at` would make an unarmed
    trigger
    look permanently overdue."""
    assert SVC.to_iso(0) == ""
    assert SVC.to_iso(-5) == ""


# ── an empty / quiet store ──


def test_an_empty_store_ticks_harmlessly(store):
    result = _tick(store)
    assert result.fires == []
    assert result.ledger_rows == []
    assert result.next_sleep == SVC.MAX_SLEEP_SECS


def test_a_not_yet_due_trigger_does_not_fire(store):
    store.save_all([_trigger(next_at=NOW + 3600)])
    result = _tick(store)
    assert result.fires == []


def test_a_disabled_trigger_is_never_due(store):
    store.save_all([_trigger(next_at=NOW - 10, enabled=False)])
    assert _tick(store).fires == []


def test_an_unarmed_trigger_is_not_treated_as_due_now(store):
    """A trigger with no `next_fire_at` means boot has not planned it. Firing it immediately would
    ignore the stagger that exists to stop a restart stampede."""
    store.save_all([_trigger(next_at=0)])
    assert _tick(store).fires == []


# ── a due trigger ──


def test_a_due_trigger_fires_with_a_ran_row(store):
    store.save_all([_trigger(next_at=NOW - 10)])
    result = _tick(store)
    assert [f.trigger.id for f in result.fires] == ["t1"]
    assert result.ledger_rows[0]["outcome"] == Outcome.RAN.value


def test_the_fire_carries_the_claim_for_the_DISPATCHER_to_release(store):
    """§3.2: the scheduler never executes. A tick that released the claim itself would let a second
    fire in while the first run is still going."""
    store.save_all([_trigger(next_at=NOW - 10)])
    assert _tick(store).fires[0].claim is not None


def test_the_row_records_the_slot_it_was_scheduled_for(store):
    """`scheduled_for` alongside the fire is what makes `ran_late` measurable rather than an
    impression."""
    store.save_all([_trigger(next_at=NOW - 40)])
    row = _tick(store).ledger_rows[0]
    assert row["scheduled_for"] == pytest.approx(NOW - 40, abs=1)


# ── §3.1: persist-before-execute ──


def test_the_next_fire_is_PERSISTED_before_the_fire_is_handed_out(store):
    """🔴 §3.1's rule. A crash between the tick and the dispatch loses ONE fire; a crash with the old
    `next_fire_at` still on disk fires TWICE, and a double-fire is the failure a user cannot undo.
    """
    store.save_all([_trigger(next_at=NOW - 10, interval=3600)])
    result = _tick(store)
    assert result.rescheduled == ["t1"]
    persisted = SVC.to_epoch(store.get("t1").trigger.next_fire_at)
    assert persisted > NOW  # advanced past now, not left in the past


def test_the_persisted_value_is_the_ISO_the_schema_declares(store):
    """Leaving a float in a `str` field would hand the next reader the same `TypeError` this session
    fixed."""
    store.save_all([_trigger(next_at=NOW - 10)])
    _tick(store)
    raw = store.get("t1").trigger.next_fire_at
    assert isinstance(raw, str) and "T" in raw


def test_a_dry_run_changes_nothing_on_disk(store):
    """`persist=False` for `automation doctor` and tests. The fire path still runs, so a dry
    run reports
    exactly what a real one would do."""
    store.save_all([_trigger(next_at=NOW - 10)])
    before = store.get("t1").trigger.next_fire_at
    result = _tick(store, persist=False)
    assert len(result.fires) == 1
    assert store.get("t1").trigger.next_fire_at == before
    assert result.rescheduled == []


# ── §3.1: recompute from completion, anchored to creation ──


def test_the_next_fire_is_computed_from_COMPLETION(store):
    """Not from the missed slot: a run that overruns its interval would otherwise produce a catch-up
    storm."""
    trigger = _trigger(next_at=NOW - 10, interval=3600)
    assert SVC.next_after_completion(trigger, completed_at=NOW, now=NOW) == pytest.approx(
        NOW + 3600, abs=2
    )


def test_a_cron_recomputes_from_its_own_expression():
    """🔴 SUPERSEDED CONTRACT (S96). This test previously asserted `== 0.0` on the premise that
    "`cron`/`at`/`sequence` recurrences belong to the recurrence engine" — but no recurrence engine
    ever existed, so the 0.0 meant a cron that fired kept its ELAPSED `next_fire_at` and every later
    tick re-fired the same past slot. `arm.next_fire` is now that engine, and it owns all four clock
    kinds, so there is exactly one answer to "when next" rather than two that can disagree.

    A cron recomputes from its EXPRESSION, never from completion — recomputing from completion would
    drift a 9am job later every day."""
    cron = Trigger(
        id="c",
        name="c",
        kind="clock",
        enabled=True,
        spec={"kind": "cron", "expr": "0 9 * * *"},
        workflow={"provider": "run-prompt", "config": {}},
    )
    # NOW is 08:00Z, so the next slot is today's 09:00Z regardless of when the run completed.
    assert SVC.next_after_completion(cron, completed_at=NOW, now=NOW) == 1_800_003_600.0
    late = SVC.next_after_completion(cron, completed_at=NOW + 1800, now=NOW + 1800)
    assert late == 1_800_003_600.0  # a 30-min-late completion does not push the 9am slot


def test_an_elapsed_one_shot_yields_no_recompute():
    """An `at` genuinely has no next fire. 0.0 is correct here — and the tick RETIRES the row rather
    than leaving it holding a past timestamp (which was the storm)."""
    once = Trigger(
        id="o",
        name="o",
        kind="clock",
        enabled=True,
        spec={"kind": "at", "at": NOW - 60},
        workflow={"provider": "run-prompt", "config": {}},
    )
    assert SVC.next_after_completion(once, completed_at=NOW, now=NOW) == 0.0


def test_a_zero_interval_does_not_schedule_an_immediate_refire():
    broken = _trigger(next_at=NOW - 10, interval=0)
    assert SVC.next_after_completion(broken, completed_at=NOW, now=NOW) == 0.0


# ── §3: the sleep contract ──


def test_the_sleep_is_capped_for_external_edit_pickup(store):
    """§3 caps at 30s "for external-edit pickup via mtime `_sync`" — the cap IS the propagation
    contract for a store another process can write, not a scheduling nicety."""
    store.save_all([_trigger(next_at=NOW + 86_400)])
    assert _tick(store).next_sleep == SVC.MAX_SLEEP_SECS


def test_the_sleep_is_floored_so_the_loop_cannot_spin(store):
    store.save_all([_trigger(next_at=NOW + 0.001)])
    assert _tick(store).next_sleep >= SVC.MIN_SLEEP_SECS


def test_the_sleep_targets_the_earliest_upcoming_fire(store):
    store.save_all([_trigger("a", next_at=NOW + 20), _trigger("b", next_at=NOW + 600)])
    assert _tick(store).next_sleep <= 20.0


def test_a_disabled_trigger_does_not_hold_the_loop_awake(store):
    store.save_all([_trigger("off", next_at=NOW + 5, enabled=False)])
    assert _tick(store).next_sleep == SVC.MAX_SLEEP_SECS


# ── §3: coalescing ──


def test_same_second_triggers_coalesce_into_one_wake(store):
    """§3: "coalescing same-second firings so N triggers replacing one 60s heartbeat don't wake the
    laptop N times". All five are still DUE — coalescing is about the wake, not about dropping
    fires.
    """
    store.save_all([_trigger(f"t{i}", next_at=NOW - 1) for i in range(5)])
    result = _tick(store)
    assert len(result.fires) == 5
    assert len(result.ledger_rows) == 5


# ── §7 crit 8: zero silent drops ──


def test_a_suppressed_trigger_still_produces_a_typed_row(store):
    """The tick owns "zero silent drops" — not the caller remembering to log."""
    store.save_all(
        [
            _trigger("ok", next_at=NOW - 1),
            _trigger(
                "quiet",
                next_at=NOW - 1,
                gates={"quiet_hours": [{"start": "00:00", "end": "23:59"}]},
            ),
        ]
    )
    result = _tick(store)
    outcomes = {row["trigger_id"]: row["outcome"] for row in result.ledger_rows}
    assert outcomes["ok"] == Outcome.RAN.value
    assert outcomes["quiet"] == Outcome.SKIPPED_GATE.value
    assert result.suppressed == 1
    assert [f.trigger.id for f in result.fires] == ["ok"]


def test_every_row_carries_a_reason_when_suppressed(store):
    store.save_all(
        [
            _trigger(
                "quiet",
                next_at=NOW - 1,
                gates={"quiet_hours": [{"start": "00:00", "end": "23:59"}]},
            )
        ]
    )
    assert _tick(store).ledger_rows[0]["reason"]


def test_a_broken_store_row_is_skipped_not_fired(store):
    """A row `parse_trigger` refused is `enabled=False` and must never reach the fire path."""
    import json

    store.save_all([_trigger(next_at=NOW - 10)])
    raw = json.loads(store.path.read_text())
    raw["triggers"].append({"id": "bad", "name": "b", "kind": "clok"})
    store.path.write_text(json.dumps(raw))
    result = _tick(store)
    assert [f.trigger.id for f in result.fires] == ["t1"]


# ── §3.1: boot ──


def test_boot_rearms_an_overdue_trigger(store):
    store.save_all([_trigger(next_at=NOW - 7200)])
    report = SVC.boot(store, now=NOW)
    assert report["total"] == 1
    assert report["rearmed"]
    assert SVC.to_epoch(store.get("t1").trigger.next_fire_at) > NOW


def test_boot_STAGGERS_so_a_restart_does_not_stampede(store):
    """§3.1's boot stagger. All three were overdue by the same amount; if they came back with one
    timestamp, a restart would fire every automation in the same second."""
    store.save_all([_trigger(f"t{i}", next_at=NOW - 7200) for i in range(6)])
    report = SVC.boot(store, now=NOW)
    stamps = {row["next_fire_at"] for row in report["rearmed"]}
    assert len(stamps) > 1


def test_boot_leaves_a_disabled_trigger_unarmed(store):
    """Arming one would resurrect it at the next tick."""
    store.save_all([_trigger(next_at=0, enabled=False)])
    report = SVC.boot(store, now=NOW)
    assert report["rearmed"] == []


def test_boot_returns_the_missed_REVIEW_rather_than_catching_up(store):
    """§3.4 is "review, don't lie and don't storm". A boot that silently caught up would BE the
    storm."""
    store.save_all([_trigger(next_at=NOW - 86_400)])
    report = SVC.boot(store, now=NOW)
    assert "review" in report


def test_boot_is_a_dry_run_when_asked(store):
    store.save_all([_trigger(next_at=NOW - 7200)])
    before = store.get("t1").trigger.next_fire_at
    SVC.boot(store, now=NOW, persist=False)
    assert store.get("t1").trigger.next_fire_at == before


# ── §3.2: the spool is a separate wake source ──


def test_the_spool_drain_is_exposed_separately_from_the_tick():
    """§3: sync-context fires spool to disk, "drained on next tick". Exposed rather than
    buried inside
    the due-set walk because a tick with NO due clock trigger must still drain it — burying it would
    skip the spool exactly when the machine was otherwise idle."""
    envelopes, dropped = SVC.drain_spooled_fires(limit=10)
    assert isinstance(envelopes, list)
    assert isinstance(dropped, int)


# ── the store-changed signal ──


def test_the_tick_reports_when_another_process_wrote_the_store(tmp_path):
    """§6's MCP gotcha: another process writes `triggers.json`. A tick acting on a stale view would
    fire a trigger the user just disabled."""
    mine = TriggerStore(base_dir=tmp_path)
    theirs = TriggerStore(base_dir=tmp_path)
    mine.save_all([_trigger(next_at=NOW + 600)])
    mine.load()
    time.sleep(0.01)
    theirs.upsert(_trigger("from-chat", next_at=NOW + 600))
    assert _tick(mine).store_changed is True


def test_the_result_serializes_for_a_surface(store):
    store.save_all([_trigger(next_at=NOW - 10)])
    payload = _tick(store).to_dict()
    assert payload["fires"][0]["trigger_id"] == "t1"
    assert payload["next_sleep"] > 0
    assert "suppressed" in payload
