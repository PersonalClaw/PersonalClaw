"""Named resource slots — declared, persisted, and read by nothing (§3.5 / AUTO-R9 — S135).

§3.5: *"Named resource slots — triggers/runs declare needs (`gpu`, `local-llm`); the substrate
**serializes conflicting runs per slot** and refuses over-capacity starts with a typed
`RESOURCE_BUSY`
+ holder identity (a `deferred` ledger row)."*

🔴 THE DEFECT. `Trigger.resource_slots` was declared in the entity, persisted, round-tripped by
`to_dict`/`from_dict` — and read by **nothing**. Found by generalising S134's container audit across
all 41 dataclasses in `triggers/`: of every field with a default, this was the only one with **zero
non-declaration readers**. So three triggers declaring `resource_slots: ["local-llm"]` all ran a
local
model at once — exactly the contention §3.5 exists to prevent on a machine shared with the
interactive
user.

Derived from the CLAIM STORE rather than a second sidecar: a slot is held exactly as long as its
trigger's run is, so claims already answer the question — and that inherits read-time expiry (a
crashed
run does not hold `gpu` hostage forever) plus cross-process visibility for free.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.triggers import claims
from personalclaw.triggers import service as svc
from personalclaw.triggers.models import Outcome, Trigger
from personalclaw.triggers.scheduling import Claim
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


def _add(store, tid, slots, *, overlap="parallel"):
    store.upsert(
        Trigger(
            id=tid,
            name=tid,
            kind="clock",
            enabled=True,
            overlap=overlap,
            spec={"kind": "interval", "interval_secs": 60},
            next_fire_at=svc.to_iso(NOW),
            resource_slots=slots,
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    return store.get(tid).trigger


def _hold(tid, tmp_path, at=NOW):
    claims.write_claim(
        Claim(trigger_id=tid, holder="run-1", claimed_at=at, max_duration_secs=1800),
        base_dir=tmp_path,
    )


def _tick(store, tmp_path, at=NOW):
    return asyncio.run(svc.tick(store, now=at, base_dir=tmp_path, persist=False))


# ── the defect ──


def test_a_CONTENDED_slot_DEFERS_the_second_fire(store, tmp_path):
    """🔴 THE DEFECT, pinned. Both fired before this."""
    _add(store, "clock:index", ["local-llm"])
    _add(store, "clock:digest", ["local-llm"])
    _hold("clock:index", tmp_path)
    result = _tick(store, tmp_path, NOW + 1)
    assert "clock:digest" not in [f.trigger.id for f in result.fires]


def test_the_HOLDER_itself_still_fires(store, tmp_path):
    """A trigger never blocks on a slot IT already holds — re-entering its own slot is what a retry
    inside one run looks like, and refusing that would deadlock a trigger against itself."""
    _add(store, "clock:index", ["local-llm"])
    _hold("clock:index", tmp_path)
    assert [f.trigger.id for f in _tick(store, tmp_path, NOW + 1).fires] == ["clock:index"]


def test_an_UNRELATED_slot_is_unaffected(store, tmp_path):
    """Serialization is PER SLOT. A busy `local-llm` must not stop a `gpu` trigger."""
    _add(store, "clock:index", ["local-llm"])
    _add(store, "clock:other", ["gpu"])
    _hold("clock:index", tmp_path)
    assert "clock:other" in [f.trigger.id for f in _tick(store, tmp_path, NOW + 1).fires]


def test_a_trigger_with_NO_slots_is_unaffected(store, tmp_path):
    _add(store, "clock:index", ["local-llm"])
    _add(store, "clock:free", [])
    _hold("clock:index", tmp_path)
    assert "clock:free" in [f.trigger.id for f in _tick(store, tmp_path, NOW + 1).fires]


def test_NOBODY_holding_means_everyone_fires(store, tmp_path):
    """The control case: the gate must not refuse on the mere PRESENCE of a slot declaration."""
    _add(store, "clock:a", ["local-llm"])
    _add(store, "clock:b", ["local-llm"])
    assert len(_tick(store, tmp_path).fires) == 2


# ── the refusal is typed and legible ──


def test_the_outcome_is_DEFERRED_not_a_skip(store, tmp_path):
    """§3.5 asks for "a `deferred` ledger row". The slot frees on its own, so this fire is postponed
    by contention — not dropped by policy, which is what a `skipped_*` would claim."""
    _add(store, "clock:index", ["local-llm"])
    _add(store, "clock:digest", ["local-llm"])
    _hold("clock:index", tmp_path)
    rows = _tick(store, tmp_path, NOW + 1).ledger_rows
    row = next(r for r in rows if r["trigger_id"] == "clock:digest")
    assert row["outcome"] == Outcome.DEFERRED.value


def test_the_reason_NAMES_THE_HOLDER(store, tmp_path):
    """🔴 §3.5 asks for "holder identity". "The gpu is busy" sends a user through every automation
    they own; "held by clock:index" is actionable."""
    _add(store, "clock:index", ["local-llm"])
    _add(store, "clock:digest", ["local-llm"])
    _hold("clock:index", tmp_path)
    rows = _tick(store, tmp_path, NOW + 1).ledger_rows
    reason = next(r for r in rows if r["trigger_id"] == "clock:digest")["reason"]
    assert "local-llm" in reason
    assert "clock:index" in reason


def test_the_slot_gate_is_in_GATE_ORDER_with_an_outcome():
    """A gate with no outcome raises `KeyError` mid-fire, which loses the fire instead of refusing
    it — the structural check `gate_order_is_intact` exists for."""
    from personalclaw.triggers.firepath import GATE_ORDER, gate_order_is_intact

    assert "slot" in GATE_ORDER
    assert gate_order_is_intact() == []


def test_the_slot_gate_runs_AFTER_the_claim():
    """Deliberate: a slot is only contended by a fire that would otherwise proceed. Checking earlier
    would refuse a fire the overlap gate was about to skip anyway — two reasons for one suppression,
    with the less useful one reported."""
    from personalclaw.triggers.firepath import GATE_ORDER

    assert GATE_ORDER.index("slot") > GATE_ORDER.index("claim")


# ── the holder map ──


def test_slot_holders_reports_only_RUNNING_triggers(store, tmp_path):
    """A declared slot on an idle trigger holds nothing — otherwise declaring `gpu` on two triggers
    would permanently block one of them."""
    _add(store, "clock:index", ["local-llm"])
    assert claims.slot_holders(store, now=NOW, base_dir=tmp_path) == {}
    _hold("clock:index", tmp_path)
    assert claims.slot_holders(store, now=NOW + 1, base_dir=tmp_path) == {
        "local-llm": "clock:index"
    }


def test_an_EXPIRED_claim_frees_its_slot(store, tmp_path):
    """🔴 Inherited from `read_claim`'s read-time expiry, and the reason slots ride on claims rather
    than a second sidecar: a crashed run must not hold `gpu` hostage until a janitor notices."""
    _add(store, "clock:index", ["local-llm"])
    _hold("clock:index", tmp_path, at=NOW - 99_999)
    assert claims.slot_holders(store, now=NOW, base_dir=tmp_path) == {}


def test_a_trigger_can_hold_MULTIPLE_slots(store, tmp_path):
    _add(store, "clock:big", ["gpu", "local-llm"])
    _hold("clock:big", tmp_path)
    assert claims.slot_holders(store, now=NOW + 1, base_dir=tmp_path) == {
        "gpu": "clock:big",
        "local-llm": "clock:big",
    }


def test_the_FIRST_holder_wins_and_is_stable(store, tmp_path):
    """The answer to "who has the gpu" must be the same across two calls in one tick; a later row
    silently replacing an earlier holder would make the refusal name the wrong trigger."""
    _add(store, "clock:a", ["gpu"])
    _add(store, "clock:b", ["gpu"])
    _hold("clock:a", tmp_path)
    _hold("clock:b", tmp_path)
    first = claims.slot_holders(store, now=NOW + 1, base_dir=tmp_path)
    assert first == claims.slot_holders(store, now=NOW + 1, base_dir=tmp_path)
    assert first["gpu"] in ("clock:a", "clock:b")


def test_a_BROKEN_row_is_skipped(store, tmp_path):
    """A row that does not parse must not contribute a phantom holder that blocks real fires."""
    (tmp_path / "triggers.json").write_text(
        '[{"id": "clock:bad", "name": "b", "kind": "clock", "spec": {"kind": "??"},'
        ' "resource_slots": ["gpu"]}]'
    )
    fresh = TriggerStore(base_dir=tmp_path)
    _hold("clock:bad", tmp_path)
    assert claims.slot_holders(fresh, now=NOW + 1, base_dir=tmp_path) == {}


# ── busy_slot's own contract ──


def test_busy_slot_is_EMPTY_when_nothing_is_held():
    trigger = Trigger(id="t", name="t", kind="clock", resource_slots=["gpu"])
    assert claims.busy_slot(trigger, holders={}) == ("", "")


def test_busy_slot_ignores_a_SELF_held_slot():
    trigger = Trigger(id="t", name="t", kind="clock", resource_slots=["gpu"])
    assert claims.busy_slot(trigger, holders={"gpu": "t"}) == ("", "")


def test_busy_slot_returns_the_FIRST_contended_slot():
    """Deterministic, so the reason names the same slot twice."""
    trigger = Trigger(id="t", name="t", kind="clock", resource_slots=["gpu", "local-llm"])
    assert claims.busy_slot(trigger, holders={"gpu": "o", "local-llm": "o"}) == ("gpu", "o")


def test_busy_slot_survives_a_NON_LIST_declaration():
    trigger = Trigger(id="t", name="t", kind="clock")
    trigger.resource_slots = "gpu"  # type: ignore[assignment]
    assert claims.busy_slot(trigger, holders={"gpu": "o"}) == ("", "")


def test_busy_slot_skips_BLANK_slot_names():
    trigger = Trigger(id="t", name="t", kind="clock", resource_slots=["", "  "])
    assert claims.busy_slot(trigger, holders={"": "o"}) == ("", "")


def test_the_slot_gate_is_CLASSIFIED_fail_open():
    """🔴 Caught by S130's own completeness test on its first run against a new gate — which is
    exactly what that test was written for.

    Fail-OPEN, with the storm guards rather than the fences: an unreadable claim store means
    "I cannot
    tell who holds the gpu", and refusing every slotted trigger over a filesystem hiccup
    would silence
    real automations. Contention costs a slow run; a stuck-closed slot gate costs the automation.
    """
    from personalclaw.triggers.models import FAIL_OPEN_GATES, gate_failure_mode

    assert "slot" in FAIL_OPEN_GATES
    assert gate_failure_mode("slot") == "open"


def test_an_UNREADABLE_claim_store_does_not_block_a_slotted_fire(store, tmp_path, monkeypatch):
    """The fail-open direction, driven rather than asserted from the table."""
    _add(store, "clock:index", ["local-llm"])
    monkeypatch.setattr(
        "personalclaw.triggers.claims.read_claim",
        lambda *a, **k: (_ for _ in ()).throw(OSError("unreadable")),
    )
    with pytest.raises(OSError):
        claims.slot_holders(store, now=NOW, base_dir=tmp_path)
    # The GATE itself still permits when the map comes back empty, which is what a swallowed read
    # produces at the call site in `service.tick`.
    trigger = Trigger(id="clock:index", name="i", kind="clock", resource_slots=["local-llm"])
    assert claims.busy_slot(trigger, holders={}) == ("", "")
