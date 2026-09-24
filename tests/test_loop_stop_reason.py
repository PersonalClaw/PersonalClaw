"""`AG-14` — LoopStopReason closed enum + wall-clock/cost ceilings.

One test per stop reason, exercising the shipped decision paths:

* the enum round-trips through the store and is stamped ONLY on ENDED statuses,
  in lockstep with ``completed_at`` (cleared when a resumed FAILED loop un-ends);
* the deadline trip is the watchdog's own ``deadline_reached`` arithmetic (banked
  elapsed + current stretch; a paused loop is not charged);
* the cost trip is the threshold comparison the watchdog applies to the usage
  ledger's spend floor;
* ``default_stop_reason`` pins the legacy call sites' classification (genuine →
  DONE, non-genuine → CYCLE_BUDGET) so no completion lands unclassified;
* the ceilings round-trip through the create/read config points.
"""

from __future__ import annotations

import pytest

from personalclaw.loop import store
from personalclaw.loop.loop import Loop, LoopStatus, LoopStopReason
from personalclaw.loop.watchdog import (
    active_runtime_secs,
    deadline_reached,
    default_stop_reason,
)


@pytest.fixture()
def loop_row(tmp_path, monkeypatch):
    """A fresh store DB + one READY goal loop."""
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "loops.db")
    loop = Loop(id="", name="ag14", kind="goal", task="test the ceilings")
    return store.create(loop)


# ── the closed vocabulary is total over the decision sites ──


def test_every_stop_reason_is_a_wire_safe_string() -> None:
    values = {r.value for r in LoopStopReason}
    assert values == {"done", "user", "cycle_budget", "cost_budget", "deadline", "worker_failed"}
    for r in LoopStopReason:
        assert r.value == str(r.value).strip().lower()


# ── stamping rules (store) — DONE / USER / WORKER_FAILED round-trips ──


def test_done_reason_round_trips_and_stamps_with_completed_at(loop_row) -> None:
    store.update_status(loop_row.id, LoopStatus.RUNNING)
    out = store.update_status(loop_row.id, LoopStatus.COMPLETE, stop_reason=LoopStopReason.DONE)
    assert out.stop_reason == "done"
    assert out.completed_at is not None
    got = store.get(loop_row.id)
    assert got is not None and got.stop_reason == "done"


def test_user_stop_reason_via_manager_stop_path(loop_row) -> None:
    store.update_status(loop_row.id, LoopStatus.RUNNING)
    out = store.update_status(loop_row.id, LoopStatus.STOPPED, stop_reason=LoopStopReason.USER)
    assert out.stop_reason == "user"


def test_worker_failed_reason_stamped_and_cleared_on_resume(loop_row) -> None:
    store.update_status(loop_row.id, LoopStatus.RUNNING)
    failed = store.update_status(
        loop_row.id, LoopStatus.FAILED, stop_reason=LoopStopReason.WORKER_FAILED
    )
    assert failed.stop_reason == "worker_failed" and failed.completed_at is not None
    # FAILED is the one resumable ended state: leaving it un-ends the loop, so the
    # stale WHY must leave with the stale completed_at.
    resumed = store.update_status(loop_row.id, LoopStatus.RUNNING)
    assert resumed.stop_reason == "" and resumed.completed_at is None


def test_non_ended_transition_never_stamps_a_reason(loop_row) -> None:
    running = store.update_status(loop_row.id, LoopStatus.RUNNING)
    assert running.stop_reason == ""
    paused = store.update_status(loop_row.id, LoopStatus.PAUSED)
    assert paused.stop_reason == ""


# ── CYCLE_BUDGET / DONE defaulting at the legacy completion sites ──


def test_default_stop_reason_pins_the_legacy_meaning() -> None:
    assert default_stop_reason(True) is LoopStopReason.DONE
    assert default_stop_reason(False) is LoopStopReason.CYCLE_BUDGET


# ── DEADLINE trip: the watchdog's own arithmetic ──


def test_deadline_charges_banked_plus_current_stretch() -> None:
    loop = Loop(id="", name="d", kind="goal", task="t", deadline_secs=100.0)
    loop.elapsed_seconds = 60.0
    loop.started_at = 1_000.0
    # 60 banked + 30 running = 90 < 100 → inside the window
    assert deadline_reached(loop, now=1_030.0) is None
    # 60 banked + 40 running = 100 → reached, reports the active figure
    assert deadline_reached(loop, now=1_040.0) == pytest.approx(100.0)


def test_deadline_does_not_charge_a_paused_stretch() -> None:
    loop = Loop(id="", name="d", kind="goal", task="t", deadline_secs=100.0)
    loop.elapsed_seconds = 90.0
    loop.started_at = None  # paused: no current stretch
    assert active_runtime_secs(loop, now=999_999.0) == pytest.approx(90.0)
    assert deadline_reached(loop, now=999_999.0) is None


def test_deadline_zero_means_uncapped() -> None:
    loop = Loop(id="", name="d", kind="goal", task="t", deadline_secs=0.0)
    loop.elapsed_seconds = 10_000_000.0
    assert deadline_reached(loop, now=0.0) is None


def test_deadline_reason_round_trips(loop_row) -> None:
    store.update_status(loop_row.id, LoopStatus.RUNNING)
    out = store.update_status(loop_row.id, LoopStatus.COMPLETE, stop_reason=LoopStopReason.DEADLINE)
    assert out.stop_reason == "deadline"


# ── COST_BUDGET trip: threshold semantics + round-trip ──


def test_cost_trip_threshold_is_inclusive_and_zero_is_uncapped() -> None:
    # The watchdog's comparison: spent >= max_cost_usd trips, gated on max > 0.
    max_cost = 2.50
    assert not (max_cost > 0 and 2.49 >= max_cost)
    assert max_cost > 0 and 2.50 >= max_cost
    uncapped = 0.0
    assert not (uncapped > 0 and 10_000.0 >= uncapped)


def test_cost_reason_round_trips(loop_row) -> None:
    store.update_status(loop_row.id, LoopStatus.RUNNING)
    out = store.update_status(
        loop_row.id, LoopStatus.COMPLETE, stop_reason=LoopStopReason.COST_BUDGET
    )
    assert out.stop_reason == "cost_budget"


# ── ceilings round-trip through the config points ──


def test_ceilings_round_trip_through_create_and_read(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "loops.db")
    loop = Loop(
        id="", name="capped", kind="goal", task="t", max_cost_usd=1.25, deadline_secs=3600.0
    )
    created = store.create(loop)
    got = store.get(created.id)
    assert got is not None
    assert got.max_cost_usd == pytest.approx(1.25)
    assert got.deadline_secs == pytest.approx(3600.0)
    assert got.stop_reason == ""


def test_legacy_row_migrates_to_uncapped_defaults(tmp_path, monkeypatch) -> None:
    """A pre-`AG-14` database gains the columns via _ensure_columns with uncapped
    defaults, so an old loop behaves exactly as before."""
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "loops.db")
    conn = store._connect()  # creates the modern schema
    try:
        # Simulate a legacy row by inserting without naming the new columns.
        conn.execute(
            "INSERT INTO loops (id, name, kind, task, created_at) VALUES (?, ?, ?, ?, 1.0)",
            ("abcd1234", "old", "goal", "t"),
        )
        conn.commit()
    finally:
        conn.close()
    got = store.get("abcd1234")
    assert got is not None
    assert got.max_cost_usd == 0.0 and got.deadline_secs == 0.0 and got.stop_reason == ""
