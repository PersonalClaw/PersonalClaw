"""Arming the clock — spec → next fire, and the two storms it prevents (§3.1 — S96).

**🔴 THE BLOCKER THESE TESTS PIN.** Measured before writing a line, against a REAL migrated store:

    store.migrate_from_crons()   # lossless: true, enabled: true
    SVC.boot(store, ...)         # {'rearmed': [], 'total': 1}
    # next_fire_at after boot:   '(none)'   → due_ids() == []  forever

A migrated cron was **permanently inert**: it reported migrated-and-enabled and could never fire,
because `due_ids` only surfaces triggers that HAVE a `next_fire_at` and nothing computed a FIRST
one. `scheduling.recompute_from_completion` handled intervals only; `boot_recovery` needs an
existing fire (handed 0.0 it returns 0.0); `next_after_completion` returned 0.0 for every
non-interval kind on the premise that a "recurrence engine" owned them — and no such engine
existed. That is the real reason the clock cutover could not proceed.

Fixing the first half exposed a second, worse one: a cron that fired kept `next_fire_at` at its
ELAPSED slot, so every later tick read it as still-due — a **fire storm on one past slot**, not
merely an inert row. Both directions are pinned here.

Semantics are inherited from the shipped `schedule.compute_next_run_ts`, not invented: a cron is
evaluated in the trigger's OWN timezone, and an elapsed one-shot never re-arms.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from personalclaw.triggers import arm as A
from personalclaw.triggers import service as SVC
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z


def _clock(spec, *, enabled=True, tid="t"):
    return Trigger(
        id=tid,
        name="T",
        kind="clock",
        enabled=enabled,
        spec=spec,
        workflow={"provider": "run-prompt", "config": {}},
    )


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# ── 🔴 the inert-migration blocker ──


def test_a_migrated_cron_is_armed_by_boot(tmp_path):
    """🔴 THE blocker, reproduced end to end on a real migrated store. Before this, boot reported
    `rearmed: []` and the trigger's `next_fire_at` stayed empty — migrated, enabled, and unable to
    ever fire."""
    (tmp_path / "crons.json").write_text(
        json.dumps(
            {
                "version": 1,
                "jobs": [
                    {
                        "id": "j-cron",
                        "name": "Nightly",
                        "enabled": True,
                        "schedule": {"kind": "cron", "cron_expr": "0 9 * * *"},
                        "action": {"provider": "run-prompt", "config": {}},
                    }
                ],
            }
        )
    )
    store = TriggerStore(base_dir=tmp_path)
    store.migrate_from_crons()
    assert store.get("j-cron").trigger.next_fire_at == ""  # the inert state

    report = SVC.boot(store, now=NOW)
    assert [r["id"] for r in report["rearmed"]] == ["j-cron"]
    assert report["rearmed"][0]["reason"] == "armed from spec"
    armed = store.get("j-cron").trigger.next_fire_at
    assert armed == _iso(1_800_003_600.0)  # 09:00 UTC, the next slot


def test_an_armed_migrated_cron_becomes_due(tmp_path):
    """The property that actually matters: after arming, `due_ids` surfaces it. Arming that did not
    make a trigger due would be a field write, not a fix."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock({"kind": "cron", "expr": "0 9 * * *"}, tid="j"))
    SVC.boot(store, now=NOW)
    triggers = [r.trigger for r in store.load()]
    assert SVC.due_ids(triggers, now=NOW) == []  # not yet
    assert SVC.due_ids(triggers, now=1_800_003_601.0) == ["j"]  # after its slot


def test_boot_leaves_an_already_armed_trigger_alone(tmp_path):
    """🔴 Re-arming a live schedule mid-flight is how a fire gets skipped or doubled. An existing
    `next_fire_at` goes through `boot_recovery` (stagger), never through a fresh spec computation.
    """
    store = TriggerStore(base_dir=tmp_path)
    t = _clock({"kind": "cron", "expr": "0 9 * * *"}, tid="j")
    t.next_fire_at = SVC.to_iso(NOW + 7200)
    store.upsert(t)
    report = SVC.boot(store, now=NOW)
    reasons = {r["reason"] for r in report["rearmed"]}
    assert "armed from spec" not in reasons


def test_an_unarmable_trigger_is_skipped_not_armed_to_now(tmp_path):
    """🔴 A broken cron must not be armed to `now`: firing on a guessed cadence is worse than not
    firing, and the row is already visible as broken."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock({"kind": "cron", "expr": "not a cron"}, tid="bad"))
    report = SVC.boot(store, now=NOW)
    assert report["rearmed"] == []
    assert store.get("bad").trigger.next_fire_at == ""


# ── 🔴 the fire storm ──


def test_a_fired_cron_rearms_to_its_next_slot(tmp_path):
    """🔴 THE second defect. A cron that fired kept its ELAPSED `next_fire_at`, so every later tick
    read it as still-due — a storm on one past slot."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock({"kind": "cron", "expr": "0 9 * * *"}, tid="j"))
    SVC.boot(store, now=NOW)
    fired_at = 1_800_003_601.0
    result = asyncio.run(SVC.tick(store, now=fired_at))
    assert [f.trigger.id for f in result.fires] == ["j"]
    assert result.rescheduled == ["j"]
    # The next slot is TOMORROW's 09:00, computed from the expression — not from completion, which
    # would drift a 9am job later every day.
    assert store.get("j").trigger.next_fire_at == _iso(1_800_090_000.0)


def test_a_second_tick_at_the_same_instant_fires_nothing(tmp_path):
    """🔴 The storm check. This is the assertion that would have caught both defects."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock({"kind": "cron", "expr": "0 9 * * *"}, tid="j"))
    SVC.boot(store, now=NOW)
    fired_at = 1_800_003_601.0
    asyncio.run(SVC.tick(store, now=fired_at))
    again = asyncio.run(SVC.tick(store, now=fired_at))
    assert again.fires == []


def test_a_one_shot_is_retired_after_firing(tmp_path):
    """🔴 An `at` has no next fire, so leaving its elapsed `next_fire_at` in place re-fires the same
    past slot forever. `delete_after_run` (declared in the spec, defaulting True for a migrated
    `at`, and consumed by NOTHING before this) decides how it retires."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock({"kind": "at", "at": NOW + 3600, "delete_after_run": True}, tid="once"))
    SVC.boot(store, now=NOW)
    result = asyncio.run(SVC.tick(store, now=NOW + 3601))
    assert [f.trigger.id for f in result.fires] == ["once"]
    assert result.retired == ["once"]
    assert store.get("once") is None  # deleted


def test_a_one_shot_that_keeps_its_row_is_disabled_not_left_armed(tmp_path):
    """`delete_after_run: False` keeps the row visible in the UI, but it must be DISABLED with no
    next fire — an enabled row holding a past timestamp is the storm."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock({"kind": "at", "at": NOW + 3600, "delete_after_run": False}, tid="keep"))
    SVC.boot(store, now=NOW)
    result = asyncio.run(SVC.tick(store, now=NOW + 3601))
    assert result.retired == ["keep"]
    row = store.get("keep")
    assert row is not None
    assert row.trigger.enabled is False
    assert row.trigger.next_fire_at == ""


def test_retirement_is_reported_not_silent(tmp_path):
    """ "It stopped existing" is the state change a user most needs explained."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock({"kind": "at", "at": NOW + 60, "delete_after_run": True}, tid="once"))
    SVC.boot(store, now=NOW)
    result = asyncio.run(SVC.tick(store, now=NOW + 61))
    assert "retired" in result.to_dict()
    assert result.to_dict()["retired"] == ["once"]


# ── the primitive: every clock kind ──


def test_cron_is_evaluated_in_the_triggers_own_timezone():
    """🔴 Inherited verbatim from `schedule.compute_next_run_ts`: croniter interprets the expression
    in the BASE's tz. Evaluating in UTC instead silently shifts every tz-bearing job by the offset —
    a moving target across a DST boundary."""
    utc = A.next_fire(_clock({"kind": "cron", "expr": "0 9 * * *", "timezone": "UTC"}), now=NOW)
    ny = A.next_fire(
        _clock({"kind": "cron", "expr": "0 9 * * *", "timezone": "America/New_York"}), now=NOW
    )
    assert utc == 1_800_003_600.0  # 09:00Z
    assert ny == 1_800_021_600.0  # 14:00Z == 09:00 EST
    assert ny != utc


def test_an_unknown_timezone_falls_back_to_utc_rather_than_refusing():
    """A typo'd zone must not make a trigger unarmable — UTC is the one zone that always exists."""
    got = A.next_fire(
        _clock({"kind": "cron", "expr": "0 9 * * *", "timezone": "Mars/Olympus"}), now=NOW
    )
    assert got == 1_800_003_600.0


def test_an_interval_advances_on_its_own_grid():
    """§3.1's anchoring rule: a long-running job must not re-phase its own schedule, and elapsed
    slots are skipped rather than fired as a backlog."""
    fresh = A.next_fire(_clock({"kind": "interval", "interval_secs": 300}), now=NOW)
    anchored = A.next_fire(
        _clock({"kind": "interval", "interval_secs": 300, "created_at": NOW - 3600}), now=NOW
    )
    assert fresh == NOW + 300
    assert anchored == NOW + 300  # next slot on the grid, not 12 backlogged fires


def test_a_sequence_arms_like_an_interval():
    assert A.next_fire(_clock({"kind": "sequence", "interval_secs": 600}), now=NOW) == NOW + 600


def test_a_future_one_shot_arms_to_its_timestamp():
    assert A.next_fire(_clock({"kind": "at", "at": NOW + 900}), now=NOW) == NOW + 900


def test_an_elapsed_one_shot_never_rearms():
    """🔴 0.0, not `now`. Re-arming an elapsed appointment turns a missed fire into an immediate
    surprise."""
    assert A.next_fire(_clock({"kind": "at", "at": NOW - 900}), now=NOW) == 0.0


@pytest.mark.parametrize("kind", ["cron", "interval", "at", "sequence"])
def test_every_declared_clock_kind_is_handled(kind):
    """🔴 A clock kind the primitive does not know returns 0.0 and is silently inert — exactly the
    bug this module exists to fix. Derived from `CLOCK_KINDS` so a new kind fails here."""
    from personalclaw.triggers.models import CLOCK_KINDS

    assert kind in CLOCK_KINDS
    spec = {
        "cron": {"kind": "cron", "expr": "0 9 * * *"},
        "interval": {"kind": "interval", "interval_secs": 300},
        "at": {"kind": "at", "at": NOW + 60},
        "sequence": {"kind": "sequence", "interval_secs": 300},
    }[kind]
    assert A.next_fire(_clock(spec), now=NOW) > NOW


def test_the_parametrized_kinds_cover_the_whole_union():
    """The guard that keeps the test above honest as the union grows."""
    from personalclaw.triggers.models import CLOCK_KINDS

    assert set(CLOCK_KINDS) == {"cron", "interval", "at", "sequence"}


# ── refusals ──


def test_a_disabled_trigger_is_never_armed():
    assert A.next_fire(_clock({"kind": "cron", "expr": "0 9 * * *"}, enabled=False), now=NOW) == 0.0


def test_a_non_clock_kind_is_never_armed():
    """Inventing a schedule for a file/event trigger would turn it into a poll."""
    t = Trigger(id="f", name="F", kind="file", enabled=True, spec={"paths": ["~/x/**"]})
    assert A.next_fire(t, now=NOW) == 0.0


def test_an_invalid_cron_expression_does_not_arm():
    assert A.next_fire(_clock({"kind": "cron", "expr": "not a cron"}), now=NOW) == 0.0


def test_an_empty_or_missing_spec_does_not_arm():
    assert A.next_fire(_clock({}), now=NOW) == 0.0
    assert A.next_fire(_clock({"kind": "cron", "expr": ""}), now=NOW) == 0.0
    assert A.next_fire(_clock({"kind": "interval", "interval_secs": 0}), now=NOW) == 0.0


def test_arm_returns_the_stores_own_empty_spelling():
    """One spelling for "no next fire" — the store already uses "", so a sentinel date here would
    create a second meaning for the same state."""
    assert A.arm(_clock({"kind": "at", "at": NOW - 1}), now=NOW) == ""
    assert A.arm(_clock({"kind": "cron", "expr": "0 9 * * *"}), now=NOW).startswith("2027-")


def test_needs_arming_selects_exactly_the_inert_population():
    live = _clock({"kind": "cron", "expr": "0 9 * * *"})
    assert A.needs_arming(live) is True
    live.next_fire_at = SVC.to_iso(NOW + 60)
    assert A.needs_arming(live) is False
    assert A.needs_arming(_clock({"kind": "cron", "expr": "0 9 * * *"}, enabled=False)) is False
    assert A.needs_arming(Trigger(id="f", name="F", kind="file", enabled=True)) is False
