"""The budget gate, actually supplied — and `max_fires` enforced.

🔴 THE DEFECT. `firepath`'s budget gate reads `ctx.budget_remaining`, and `service.tick` **never set
it**. So `if ctx.budget_remaining is not None` was always False and the budget gate had never
refused
a real fire. Third instance of this exact shape — the `existing_claim`, the `requested`, this —
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
import time

import pytest
from table_rows import row

from personalclaw.triggers import claims, firepath
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


# ── the one admission supplies the budget, and both production callers reach it ──
#
# The gate context is built in `admit_fire`, the ONE admission the tick and the event router both
# call. The property is that the field is POPULATED: a fire that merely passes would pass against a
# gate that happened to allow everything, the exact hole this closes. So the context the fire path
# is handed is watched (the real walk still decides), and each caller is driven with a stand-in
# admission as well, to show the fire is decided there and nowhere else.

_STAND_IN = "refused by the stand-in admission"


def _watch_contexts(monkeypatch) -> list[firepath.FireContext]:
    """Every context `firepath.evaluate` is handed, in order. The real walk still decides."""
    real = firepath.evaluate
    seen: list[firepath.FireContext] = []

    async def watched(ctx):
        seen.append(ctx)
        return await real(ctx)

    monkeypatch.setattr(firepath, "evaluate", watched)
    return seen


def _watch_admissions(monkeypatch) -> list[dict]:
    """Every call made to `service.admit_fire`: its arguments and what it answered. The real
    admission still decides."""
    real = svc.admit_fire
    calls: list[dict] = []

    async def watched(store, trigger, **kw):
        admission = await real(store, trigger, **kw)
        calls.append({"trigger": trigger.id, **kw, "admission": admission})
        return admission

    monkeypatch.setattr(svc, "admit_fire", watched)
    return calls


async def _refuse_everything(store, trigger, **_kw) -> svc.Admission:
    """A stand-in for `service.admit_fire` that refuses every fire, with a reason only it gives."""
    decision = firepath.FireDecision(
        allowed=False, outcome=Outcome.SKIPPED_GATE.value, gate="stand-in", reason=_STAND_IN
    )
    return svc.Admission(
        decision=decision,
        row={"trigger_id": trigger.id, "outcome": decision.outcome, "reason": _STAND_IN},
    )


def _counted(store, tid: str, gates: dict, used: int, **fields) -> None:
    """Store trigger *tid* with *gates* and *used* fires already counted against them."""
    trigger = Trigger(
        id=tid,
        name=tid,
        enabled=True,
        gates=gates,
        workflow={"inline": {"provider": "notify", "config": {}}},
        **fields,
    )
    trigger.run_count = used
    store.upsert(trigger)


def test_the_admission_hands_the_budget_gate_what_is_left(tmp_path, monkeypatch, subtests):
    """`admit_fire` hands the budget gate the fires the trigger may still make: no cap declared is
    no budget, a cap is the cap less the fires already counted, and a cap that does not parse is
    nothing left. The gate decides on what it is handed: a fire with nothing left is refused as a
    budget refusal and not counted, and a granted one is counted."""
    seen = _watch_contexts(monkeypatch)
    # case, the trigger's gates, the fires counted so far, the budget the gate is handed, granted
    table = [
        ("no cap", {}, 4, None, True),
        ("a zero cap is no cap", {"max_fires": 0}, 4, None, True),
        ("a cap with room", {"max_fires": 3}, 1, 2.0, True),
        ("the last allowance", {"max_fires": 2}, 1, 1.0, True),
        ("a spent cap", {"max_fires": 2}, 2, 0.0, False),
        ("a cap lowered below the fires made", {"max_fires": 2}, 5, 0.0, False),
        ("a cap that does not parse", {"max_fires": "lots"}, 0, 0.0, False),
    ]
    assert len(table) == 7
    for i, (case, gates, used, budget, granted) in enumerate(table):
        with row(subtests, case=case):
            here = tmp_path / f"row-{i}"
            here.mkdir()
            store = TriggerStore(base_dir=here)
            _counted(store, "clock:t", gates, used, kind="clock")
            trigger = store.get("clock:t").trigger
            seen.clear()

            admission = asyncio.run(
                svc.admit_fire(store, trigger, now=NOW, base_dir=here, holder="test")
            )

            assert [ctx.budget_remaining for ctx in seen] == [budget], case
            assert admission.allowed is granted, f"{case}: {admission.row}"
            want = Outcome.RAN.value if granted else Outcome.SKIPPED_BUDGET.value
            assert admission.row["outcome"] == want, f"{case}: {admission.row}"
            counted = store.get("clock:t").trigger.run_count
            assert counted == used + granted, f"{case}: {counted} fires counted"


def test_the_tick_decides_each_due_fire_by_the_one_admission(store, tmp_path, monkeypatch):
    """The tick walks every trigger it finds due through `admit_fire`, once, as the tick's own
    holder, so the budget gate is handed what each one has left; it fires exactly what that
    admission granted and records the row it wrote, and a trigger not yet due is not walked. With a
    stand-in admission that refuses everything, the tick fires nothing and records the stand-in's
    rows: it holds no admission of its own."""
    seen = _watch_contexts(monkeypatch)
    calls = _watch_admissions(monkeypatch)
    due = {"spec": {"kind": "interval", "interval_secs": 60}, "next_fire_at": svc.to_iso(NOW)}
    _counted(store, "clock:room", {"max_fires": 3}, 1, kind="clock", **due)
    _counted(store, "clock:spent", {"max_fires": 2}, 2, kind="clock", **due)
    _counted(store, "clock:open", {}, 0, kind="clock", **due)
    later = {**due, "next_fire_at": svc.to_iso(NOW + 3600)}
    _counted(store, "clock:later", {}, 0, kind="clock", **later)

    result = asyncio.run(svc.tick(store, now=NOW, base_dir=tmp_path, persist=True))

    assert sorted(c["trigger"] for c in calls) == ["clock:open", "clock:room", "clock:spent"]
    for call in calls:
        assert call["holder"] == f"tick:{int(NOW)}", call
        where = (call.get("now"), call.get("base_dir"), call.get("persist"))
        assert where == (NOW, tmp_path, True), call
    budgets = {ctx.trigger_id: ctx.budget_remaining for ctx in seen}
    assert budgets == {"clock:room": 2.0, "clock:spent": 0.0, "clock:open": None}, budgets
    granted = sorted(c["trigger"] for c in calls if c["admission"].allowed)
    assert granted == ["clock:open", "clock:room"]
    assert sorted(f.trigger.id for f in result.fires) == granted
    rows = {r["trigger_id"]: r for r in result.ledger_rows}
    assert sorted(rows) == sorted(c["trigger"] for c in calls)
    for call in calls:
        mine, its = rows[call["trigger"]], call["admission"].row
        assert (mine["outcome"], mine["reason"]) == (its["outcome"], its["reason"]), call
    assert rows["clock:spent"]["outcome"] == Outcome.SKIPPED_BUDGET.value

    for tid in granted:
        claims.release_claim(tid, base_dir=tmp_path)
    monkeypatch.setattr(svc, "admit_fire", _refuse_everything)
    result = asyncio.run(svc.tick(store, now=NOW + 120, base_dir=tmp_path, persist=True))
    assert result.fires == []
    assert sorted(r["trigger_id"] for r in result.ledger_rows) == [
        "clock:open",
        "clock:room",
        "clock:spent",
    ]
    assert {r["reason"] for r in result.ledger_rows} == {_STAND_IN}


def test_the_event_router_decides_each_match_by_the_one_admission(tmp_path, monkeypatch):
    """The event router walks every event trigger an event matches through `admit_fire`, once, as
    an event's holder, so the budget gate is handed what each one has left; it dispatches exactly
    what that admission granted, retires the trigger whose last allowance it spent, and records the
    refusal, and a trigger the event does not match is not walked. With a stand-in admission that
    refuses everything, the router dispatches nothing."""
    from personalclaw.event_triggers import (
        MEMORY_KEY_PATTERN,
        MEMORY_UPDATE,
        SOURCE_MEMORY,
        BusEvent,
        event_spec,
    )
    from personalclaw.schedule_history import ScheduleRunStore
    from personalclaw.triggers.event_fire import EventRouter

    store = TriggerStore(base_dir=tmp_path)
    on_update = {"kind": "event", "spec": event_spec(MEMORY_UPDATE)}
    _counted(store, "event:room", {"max_fires": 3}, 1, **on_update)
    _counted(store, "event:last", {"max_fires": 2}, 1, **on_update)
    _counted(store, "event:spent", {"max_fires": 2}, 2, **on_update)
    _counted(store, "event:open", {}, 0, **on_update)
    other_key = {"kind": "event", "spec": event_spec(MEMORY_KEY_PATTERN, "project.*")}
    _counted(store, "event:other", {}, 0, **other_key)
    seen = _watch_contexts(monkeypatch)
    calls = _watch_admissions(monkeypatch)
    dispatched: list[str] = []

    async def dispatch(trigger, _payload, **_kw) -> str:
        dispatched.append(trigger.id)
        return ""

    def event() -> BusEvent:
        return BusEvent(
            source=SOURCE_MEMORY, event_type="update", key="notes.today", value="v", now=time.time()
        )

    async def deliver() -> list[str]:
        router = EventRouter(dispatch=dispatch, loop=asyncio.get_running_loop(), base_dir=tmp_path)
        fired = await router.deliver(event())
        await router.settle()
        return fired

    fired = asyncio.run(deliver())

    matched = ["event:last", "event:open", "event:room", "event:spent"]
    assert sorted(c["trigger"] for c in calls) == matched
    for call in calls:
        assert call["holder"].startswith("event:") and call.get("base_dir") == tmp_path, call
    budgets = {ctx.trigger_id: ctx.budget_remaining for ctx in seen}
    assert budgets == {
        "event:room": 2.0,
        "event:last": 1.0,
        "event:spent": 0.0,
        "event:open": None,
    }, budgets
    granted = sorted(c["trigger"] for c in calls if c["admission"].allowed)
    assert granted == ["event:last", "event:open", "event:room"]
    assert sorted(fired) == sorted(dispatched) == granted
    enabled = {t: store.get(t).trigger.enabled for t in matched}
    assert enabled == {t: t != "event:last" for t in matched}, "only the spent allowance retires"
    runs, total = asyncio.run(ScheduleRunStore(tmp_path).list_for_job("event:spent", 0, 5))
    assert total == 1 and runs[0]["status"] == Outcome.SKIPPED_BUDGET.value, runs

    dispatched.clear()
    monkeypatch.setattr(svc, "admit_fire", _refuse_everything)
    assert asyncio.run(deliver()) == []
    assert dispatched == []


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


# ── the refusal is legible ──


def test_the_refusal_is_a_TYPED_ledger_row(store, tmp_path):
    """Every suppressed fire is a typed row with a reason — zero silent drops."""
    _due(store, tmp_path, {"max_fires": 1})
    _fires, rows = _run_slots(store, tmp_path, 3)
    refusals = [r for r in rows if r["outcome"] == Outcome.SKIPPED_BUDGET.value]
    assert len(refusals) == 2
    assert all(r["outcome"] in FIRE_OUTCOMES for r in rows)
    assert all(r["reason"] for r in refusals)


def test_the_budget_refusal_ARCHIVES(store, tmp_path):
    """`skipped_budget` is inert, so a storm of cap refusals folds out of the default runs
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
    """🔴 The honest half. A stored row can still carry a cap nothing reads — here the per-window
    `cost_cap`, which is no gate any more and which its row says is never enforced — and an
    automation that sets one is NOT bounded by it, so the doctor says so instead of implying it
    works."""
    from personalclaw.triggers.calendar import diagnose

    rows = [{"id": "schedule:clock:x", "gates": {"cost_cap": 5.0, "max_cost_usd_per_run": 1.0}}]
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
    """A regression guard on the set itself: adding `max_fires` here would tell users the cap the
    fire path enforces does not work."""
    from personalclaw.triggers.calendar import UNMETERED_CAPS

    assert "max_fires" not in UNMETERED_CAPS


def test_the_STORM_SPACING_gates_are_named_too():
    """🔴 A `GATE_KEYS` sweep found five declared gate keys with no reader on the fire path,
    and the asymmetry made it worth fixing: a user setting a per-window cost cap was honestly
    told it is unmetered, while one setting `debounce_secs: 300` got SILENCE — and believed their
    automation was spacing its fires.

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
    # `debounce_secs`/`cooldown_secs` were once in this list and have since been WIRED, so they must
    # no longer be reported as unmetered — reporting a working gate as broken is the same class of
    # lie as the silence the sweep fixed, pointing the other way.
    for key in ("idempotency", "threshold"):
        assert key in finding.detail, key
    # Wired since the sweep named them: debounce/cooldown, then the three hourly caps. A key
    # that has been wired must STOP being reported, or the doctor lies in the opposite direction.
    for wired in ("debounce_secs", "cooldown_secs", "rate_cap"):
        assert wired not in finding.detail, f"{wired} is enforced now"
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
    in neither bucket is the defect this guard closes: declared, unread, and silent about it.
    """
    from personalclaw.triggers.calendar import UNMETERED_CAPS
    from personalclaw.triggers.models import GATE_KEYS

    # `debounce_secs`/`cooldown_secs` joined the enforced set with the `spacing` gate, which is
    # why they are no longer in UNMETERED_CAPS — a key must move buckets, never sit in both.
    enforced = {
        "max_fires",
        "quiet_hours",
        "skip_dates",
        "duty_gate",
        "condition",
        "debounce_secs",
        "cooldown_secs",
        # …and the three hourly caps, wired by `ScheduleRunStore.count_since`.
        "rate_cap",
        "max_runs_per_hour",
        "max_actions_per_hour",
        # `max_cost_usd_per_run` is enforced too: `ModelCallGuard` checks the ambient run scope
        # against the ceiling `calendar.run_budget_for` derives from these gates. Enforced in the
        # guard rather than on the fire path because run spend accrues DURING the run — a pre-fire
        # gate reads $0.00 on a freshly bound per-fire key and is inert by construction.
        "max_cost_usd_per_run",
    }
    unclassified = set(GATE_KEYS) - enforced - set(UNMETERED_CAPS)
    assert not unclassified, (
        f"gate key(s) {sorted(unclassified)} are neither enforced nor named unmetered — wire "
        "them, or add them to UNMETERED_CAPS so a user is not told a cap works when it does not"
    )
    assert not (enforced & set(UNMETERED_CAPS)), "a gate cannot be both enforced and unmetered"


# ── the 24h storm ──


def test_a_24H_STORM_drops_NOTHING(store, tmp_path):
    """🔴 The bar is "zero silent drops under a 24h storm", and that test did not exist.

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


def test_the_counter_write_does_NOT_RE_ARM_a_one_shot_whose_slot_was_taken(store, tmp_path):
    """🔴 Found by a red test, not by reading. The counter increment and the slot being taken write
    the same row in the same iteration; a counter upsert of a stale copy would put the one-shot
    back as a live trigger holding an elapsed slot, which is exactly the storm the retirement
    exists to prevent. So the grant's one write carries both: counted, and switched off.
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
    taken = store.get("clock:once").trigger
    assert taken.run_count == 1
    assert taken.enabled is False and taken.next_fire_at == "", "its slot stays taken"
    assert asyncio.run(svc.tick(store, now=NOW + 3700, base_dir=tmp_path)).fires == []


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


def test_the_run_cap_implementation_matches_its_declared_fail_direction():
    """A control's behaviour must agree with the table describing it — once, the inert control
    here was the *description of the controls*, so a newly enforced key gets checked both ways.

    `max_cost_usd_per_run` is classified FAIL-OPEN (the per-trigger cap-key class), and
    `run_budget_for` implements exactly that: a malformed value yields an unlimited Budget rather
    than a $0 ceiling. The opposite reading is the dangerous one — a $0 ceiling refuses the
    trigger's very first model call, which looks exactly like a dead automation.
    """
    from personalclaw.triggers.calendar import run_budget_for
    from personalclaw.triggers.models import gate_failure_mode

    assert gate_failure_mode("max_cost_usd_per_run") == "open"
    for junk in ("ten", None, [], {}, "", -3):
        assert run_budget_for(
            {"max_cost_usd_per_run": junk}
        ).is_unlimited, (
            f"a malformed cap ({junk!r}) must fail OPEN — a $0 ceiling would silence the trigger"
        )
    # …and a well-formed one still binds, so failing open is not a blanket excuse.
    assert run_budget_for({"max_cost_usd_per_run": 0.25}).max_dollars == 0.25
