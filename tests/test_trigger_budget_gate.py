"""The budget gate, actually supplied — and `max_fires` enforced (§3.6 / crit 8 — S133).

🔴 THE DEFECT. `firepath`'s budget gate reads `ctx.budget_remaining`, and `service.tick` **never set
it**. So `if ctx.budget_remaining is not None` was always False and the budget gate had never
refused
a real fire. Third instance of this exact shape — S97's `existing_claim`, S116's `requested`, this —
which is why the fix ships with a test that drives `tick`, not `evaluate`.

The user-visible cost was `gates.max_fires`: declared in `GATE_KEYS`, validated, carried by
`LEGACY_FIELD_MAP`, and bounding nothing. Measured, with the claim released each tick so the overlap
gate could not mask the question:

    max_fires=2   →  8 fires over 8 slots
    no gates      →  8 fires over 8 slots       (identical: the cap did nothing)

A second inert piece underneath: **nothing incremented `run_count`** on this path, so even a wired
budget would have compared against a permanent zero. A cap needs a meter.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.triggers import claims
from personalclaw.triggers import service as svc
from personalclaw.triggers.models import FIRE_OUTCOMES, INERT_OUTCOMES, Outcome, Trigger
from personalclaw.triggers.service import _budget_remaining
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


def _due(store, tmp_path, gates=None, tid="clock:t"):
    store.upsert(
        Trigger(
            id=tid,
            name=tid,
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 60},
            next_fire_at=svc.to_iso(NOW),
            gates=gates or {},
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    return store.get(tid).trigger


def _run_slots(store, tmp_path, count, tid="clock:t"):
    """Tick `count` times, RELEASING the claim between ticks the way the dispatcher does.

    Without the release the overlap gate refuses every slot after the first, which masks whether the
    budget did anything — that masking is what made the original defect hard to see.
    """
    fires = 0
    rows = []
    for i in range(count):
        result = asyncio.run(svc.tick(store, now=NOW + i * 120, base_dir=tmp_path, persist=True))
        fires += len(result.fires)
        rows += result.ledger_rows
        claims.release_claim(tid, base_dir=tmp_path)
    return fires, rows


# ── the defect ──


def test_MAX_FIRES_actually_bounds_the_fires(store, tmp_path):
    """🔴 THE DEFECT, pinned. This produced 8 before the fix."""
    _due(store, tmp_path, {"max_fires": 2})
    fires, _ = _run_slots(store, tmp_path, 8)
    assert fires == 2


def test_NO_cap_still_fires_every_slot(store, tmp_path):
    """The control case that makes the assertion above mean something — and the guard against a fix
    that simply broke firing."""
    _due(store, tmp_path)
    fires, _ = _run_slots(store, tmp_path, 8)
    assert fires == 8


def test_the_production_tick_SUPPLIES_the_budget():
    """A source check, because the property is that the field is POPULATED. A behavioural test alone
    would pass against a gate that happened to allow everything — the exact hole this closes."""
    import inspect

    assert "budget_remaining=" in inspect.getsource(svc.tick)


def test_RUN_COUNT_is_incremented_and_PERSISTED(store, tmp_path):
    """The meter the cap reads. Nothing incremented it on this path, so a wired budget would have
    compared against a permanent zero."""
    _due(store, tmp_path, {"max_fires": 5})
    _run_slots(store, tmp_path, 3)
    assert store.get("clock:t").trigger.run_count == 3


def test_the_counter_increments_on_a_GRANTED_fire_not_on_completion(store, tmp_path):
    """Deliberate: `max_fires` bounds attempts the substrate AUTHORISED. Deferring the increment to
    completion would let a storm of in-flight fires all pass a cap of one."""
    _due(store, tmp_path, {"max_fires": 1})
    result = asyncio.run(svc.tick(store, now=NOW, base_dir=tmp_path, persist=True))
    assert len(result.fires) == 1
    assert store.get("clock:t").trigger.run_count == 1, "counted before any dispatch reported back"


# ── the refusal is legible (criterion 8) ──


def test_the_refusal_is_a_TYPED_ledger_row(store, tmp_path):
    """§7 criterion 8: every suppressed fire is a typed row with a reason — zero silent drops."""
    _due(store, tmp_path, {"max_fires": 1})
    _fires, rows = _run_slots(store, tmp_path, 3)
    refusals = [r for r in rows if r["outcome"] == Outcome.SKIPPED_BUDGET.value]
    assert len(refusals) == 2
    assert all(r["outcome"] in FIRE_OUTCOMES for r in rows)
    assert all(r["reason"] for r in refusals)


def test_the_budget_refusal_ARCHIVES(store, tmp_path):
    """`skipped_budget` is inert (S132), so a storm of cap refusals folds out of the default runs
    view instead of burying real work."""
    assert Outcome.SKIPPED_BUDGET.value in INERT_OUTCOMES


def test_a_refused_fire_does_NOT_spend_more_budget(store, tmp_path):
    """The counter must not advance on a refusal, or a cap of 2 would silently become a cap of 1 the
    next time anyone read it."""
    _due(store, tmp_path, {"max_fires": 1})
    _run_slots(store, tmp_path, 5)
    assert store.get("clock:t").trigger.run_count == 1


# ── the helper's contract ──


def test_NO_cap_means_NO_budget_not_infinity():
    """None and a large number are different: the gate distinguishes "no budget configured" from
    "budget exhausted", and a sentinel would make an unset cap indistinguishable from a huge one."""
    trigger = Trigger(id="t", name="t", kind="clock", gates={})
    assert _budget_remaining(trigger) is None


def test_a_ZERO_cap_means_no_cap():
    """`max_fires: 0` is the documented "unlimited" spelling in `LEGACY_FIELD_MAP`'s source
    entity."""
    assert (
        _budget_remaining(Trigger(id="t", name="t", kind="clock", gates={"max_fires": 0})) is None
    )


def test_a_MALFORMED_cap_fails_CLOSED(store, tmp_path):
    """🔴 A cap that cannot be parsed is NOT unlimited. "I asked for a limit and typed it wrong"
    reads as zero allowance — refusing visibly rather than running unbounded. `validate_gates`
    reports the shape separately."""
    _due(store, tmp_path, {"max_fires": "lots"})
    result = asyncio.run(svc.tick(store, now=NOW, base_dir=tmp_path, persist=False))
    assert result.fires == []
    assert result.ledger_rows[0]["outcome"] == Outcome.SKIPPED_BUDGET.value


def test_remaining_never_goes_NEGATIVE():
    trigger = Trigger(id="t", name="t", kind="clock", gates={"max_fires": 2})
    trigger.run_count = 9
    assert _budget_remaining(trigger) == 0.0


def test_a_non_dict_gates_block_is_survived():
    trigger = Trigger(id="t", name="t", kind="clock")
    trigger.gates = "nope"  # type: ignore[assignment]
    assert _budget_remaining(trigger) is None


# ── the caps that are still UNMETERED are named, not implied ──


def test_the_doctor_names_an_UNMETERED_cap():
    """🔴 The honest half. `cost_cap` needs per-run spend attribution and `max_runs_per_hour` needs a
    windowed history query — neither meter exists on this path. Inventing one to satisfy the cap
    would be the inverted dependency S119 and S129 both refused, so the doctor says so instead."""
    from personalclaw.triggers.calendar import diagnose

    rows = [{"id": "schedule:clock:x", "gates": {"cost_cap": 5.0, "max_runs_per_hour": 10}}]
    finding = next(
        f for f in diagnose(rows, known_workflows=None).findings if f.code == "unmetered_cap"
    )
    assert "NOT bounded" in finding.detail
    assert "max_fires" in finding.fix, "and it points at the cap that DOES work"


def test_the_doctor_is_SILENT_for_max_fires():
    """The fix for a finding must never trip the finding."""
    from personalclaw.triggers.calendar import diagnose

    rows = [{"id": "schedule:clock:y", "gates": {"max_fires": 5}}]
    assert not [
        f for f in diagnose(rows, known_workflows=None).findings if f.code == "unmetered_cap"
    ]


def test_MAX_FIRES_is_not_in_the_unmetered_set():
    """A regression guard on the set itself: adding `max_fires` here would tell users the cap this
    session wired does not work."""
    from personalclaw.triggers.calendar import UNMETERED_CAPS

    assert "max_fires" not in UNMETERED_CAPS


def test_the_STORM_SPACING_gates_are_named_too():
    """🔴 S150. A `GATE_KEYS` sweep found five declared gate keys with no reader on the fire path,
    and the asymmetry made it worth a session: a user setting `cost_cap` was honestly told it is
    unmetered, while one setting `debounce_secs: 300` got SILENCE — and believed their automation
    was spacing its fires.

    `firepath`'s own module docstring names the order as "debounce/quiet/cooldown/condition", so
    three of the four gates it advertises are absent from `GATE_ORDER`.
    """
    from personalclaw.triggers.calendar import diagnose

    rows = [
        {
            "id": "schedule:clock:storm",
            "gates": {
                "debounce_secs": 300,
                "cooldown_secs": 600,
                "rate_cap": 5,
                "idempotency": True,
                "threshold": 3,
            },
        }
    ]
    finding = next(
        f for f in diagnose(rows, known_workflows=None).findings if f.code == "unmetered_cap"
    )
    # `debounce_secs`/`cooldown_secs` were in this list at S150 and were WIRED at S151, so they must
    # no longer be reported as unmetered — reporting a working gate as broken is the same class of
    # lie as the silence S150 fixed, pointing the other way.
    for key in ("rate_cap", "idempotency", "threshold"):
        assert key in finding.detail, key
    for wired in ("debounce_secs", "cooldown_secs"):
        assert wired not in finding.detail, f"{wired} is enforced as of S151"
    assert "NOT bounded" in finding.detail


def test_every_ENFORCED_gate_stays_silent():
    """A rule that flagged a working gate would be worse than the gap it closes — it would train the
    user to ignore the doctor. These four are genuinely enforced on the fire path."""
    from personalclaw.triggers.calendar import diagnose

    enforced = {
        "max_fires": 5,
        "quiet_hours": [{"days": ["sat"], "start": "22:00", "end": "23:00"}],
        "skip_dates": ["2026-08-05"],
        "condition": {"checkType": "always"},
    }
    for key, value in enforced.items():
        rows = [{"id": "schedule:clock:ok", "gates": {key: value}}]
        assert not [
            f for f in diagnose(rows, known_workflows=None).findings if f.code == "unmetered_cap"
        ], key


def test_the_unmetered_set_and_the_gate_vocabulary_stay_in_step():
    """The completeness guard, so this list shrinks for a REASON rather than by guesswork.

    Every declared gate key must be either ENFORCED on the fire path or named as unmetered. A key
    in neither bucket is the defect this session closed: declared, unread, and silent about it.
    """
    from personalclaw.triggers.calendar import UNMETERED_CAPS
    from personalclaw.triggers.models import GATE_KEYS

    # `debounce_secs`/`cooldown_secs` joined the enforced set at S151 (the `spacing` gate), which is
    # why they are no longer in UNMETERED_CAPS — a key must move buckets, never sit in both.
    enforced = {
        "max_fires",
        "quiet_hours",
        "skip_dates",
        "duty_gate",
        "condition",
        "debounce_secs",
        "cooldown_secs",
    }
    unclassified = set(GATE_KEYS) - enforced - set(UNMETERED_CAPS)
    assert not unclassified, (
        f"gate key(s) {sorted(unclassified)} are neither enforced nor named unmetered — wire "
        "them, or add them to UNMETERED_CAPS so a user is not told a cap works when it does not"
    )
    assert not (enforced & set(UNMETERED_CAPS)), "a gate cannot be both enforced and unmetered"


# ── the 24h storm (criterion 8) ──


def test_a_24H_STORM_drops_NOTHING(store, tmp_path):
    """🔴 Criterion 8's named bar: "zero silent drops under a 24h storm test", which did not exist.

    1440 slots of a per-minute trigger suppressed by quiet hours: every slot must produce
    exactly one
    typed row with a reason. Runs the real `tick` against a real store.
    """
    _due(store, tmp_path, {"quiet_hours": {"start": "00:00", "end": "23:59"}})
    rows = 0
    fires = 0
    for i in range(1440):
        result = asyncio.run(svc.tick(store, now=NOW + i * 60, base_dir=tmp_path, persist=True))
        rows += len(result.ledger_rows)
        fires += len(result.fires)
    assert fires == 0
    assert rows == 1440, "one typed row per slot — zero silent drops"


def test_the_counter_does_NOT_RESURRECT_a_retired_one_shot(store, tmp_path):
    """🔴 Found by a red test, not by reading. The retirement branch `store.delete()`s a
    `delete_after_run` one-shot; an unconditional counter upsert RESURRECTED the row it had just
    removed — turning a retired one-shot back into a live trigger holding an elapsed slot, which is
    exactly the storm S112's retirement exists to prevent.

    The counter increment and the retirement both write the store in the same iteration, so their
    ORDER is a real contract rather than an implementation detail.
    """
    store.upsert(
        Trigger(
            id="clock:once",
            name="once",
            kind="clock",
            enabled=True,
            spec={"kind": "at", "at": NOW + 3600, "delete_after_run": True},
            next_fire_at=svc.to_iso(NOW + 3600),
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    result = asyncio.run(svc.tick(store, now=NOW + 3601, base_dir=tmp_path, persist=True))
    assert [f.trigger.id for f in result.fires] == ["clock:once"]
    assert result.retired == ["clock:once"]
    assert store.get("clock:once") is None, "a retired one-shot must stay deleted"


def test_a_one_shot_that_KEEPS_its_row_is_still_disabled(store, tmp_path):
    """The other retirement path: the row stays visible but must not remain armed, and the counter
    write must not re-enable it."""
    store.upsert(
        Trigger(
            id="clock:keep",
            name="keep",
            kind="clock",
            enabled=True,
            spec={"kind": "at", "at": NOW + 3600, "delete_after_run": False},
            next_fire_at=svc.to_iso(NOW + 3600),
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    asyncio.run(svc.tick(store, now=NOW + 3601, base_dir=tmp_path, persist=True))
    row = store.get("clock:keep")
    assert row is not None
    assert row.trigger.enabled is False
    assert row.trigger.next_fire_at == ""
