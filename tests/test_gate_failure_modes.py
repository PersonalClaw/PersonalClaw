"""The per-gate fail-open/fail-closed classification, verified against real behaviour (S130).

§1.4 decision 1 (R3 am.): *"Fail-open vs fail-closed is classified **per gate**: budget/storm-guard
checks time-box and fail-open; security fences (capabilities, injection screen, fencing) stay
fail-closed."*

🔴 THE DEFECT. The classification existed as data (`FAIL_OPEN_GATES` + `gate_failure_mode`) and was
read by **no production code** — only tests. Measured:

    set(firepath.GATE_ORDER) & FAIL_OPEN_GATES  ==  set()

Two vocabularies that never intersected. The set held per-trigger CAP KEYS a person edits
(`cost_cap`, `rate_cap`, `duty_gate` — the `GATE_KEYS` vocabulary), while the fire path walks GATE
names (`screen`, `quiet`, `duty`, `budget`, `claim`, `yield`, `capability`, `incident`). So **every
gate the engine actually runs resolved to "closed"** — including `duty`, which §1.4 and
`calendar.evaluate_duty` both require to fail OPEN.

**The gates were right; the classifier was wrong.** Driven: an unregistered duty provider allows the
fire ("duty gate 'no-such-calendar-app' is not registered; the fire proceeds"), and an unreadable
budget refuses it. The table describing that behaviour disagreed with it in both directions, and
nothing outside tests read the table, so nothing caught the drift.

These tests therefore assert the classification against **what the gates do**, not against the table
itself. A test that only compared the table to a hardcoded list would have passed before the fix.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.triggers.firepath import GATE_ORDER, FireContext, evaluate
from personalclaw.triggers.models import (
    FAIL_CLOSED_GATES,
    FAIL_OPEN_GATES,
    GATE_KEYS,
    gate_failure_mode,
)

# ── the namespace bug itself ──


def test_the_classifier_COVERS_the_fire_paths_own_gate_names():
    """🔴 THE DEFECT, pinned. This intersection was EMPTY: the classifier spoke a different
    vocabulary from the walk it described, so every real gate read "closed"."""
    classified = FAIL_OPEN_GATES | FAIL_CLOSED_GATES
    unclassified = [g for g in GATE_ORDER if g not in classified]
    assert not unclassified, (
        f"these fire-path gates have no classification: {unclassified}. "
        "A gate the engine walks must have a stated fail direction."
    )


def test_BOTH_vocabularies_resolve():
    """A person's trigger config says `duty_gate`; the fire path's gate is `duty`. Both are correct
    in their own surface, so the classifier answers for both rather than renaming one side."""
    assert gate_failure_mode("duty_gate") == "open"
    assert gate_failure_mode("duty") == "open"


def test_the_two_sets_do_not_OVERLAP():
    """A gate in both sets would resolve by lookup order — the ambiguity S71 found in `fuse`."""
    assert not (FAIL_OPEN_GATES & FAIL_CLOSED_GATES)


def test_an_UNCLASSIFIED_gate_still_defaults_to_CLOSED():
    """The safe direction for a control whose semantics nobody stated."""
    assert gate_failure_mode("some_brand_new_gate") == "closed"
    assert gate_failure_mode("") == "closed"


# ── verified against what the gates ACTUALLY do ──


def test_the_DUTY_gate_really_fails_OPEN(monkeypatch):
    """🔴 The classification checked against behaviour. §1.4 is explicit: the duty gate calls out to
    a provider, and uninstalling the calendar app that supplied it must not silently stop every
    automation that referenced it."""
    gates = {"duty_gate": {"provider": "no-such-calendar-app"}}
    decision = asyncio.run(evaluate(FireContext(trigger_id="t", gates=gates)))
    assert decision.allowed is True, "an unregistered duty provider must not block the fire"
    assert gate_failure_mode("duty") == "open", "and the classifier must agree"


def test_the_BUDGET_gate_really_fails_CLOSED():
    """§3.6 is more specific than §1.4's "budget/storm-guard … fail-open" prose, and the
    code follows
    it: "an unreadable budget is not an unlimited one". The classifier now matches §3.6."""
    decision = asyncio.run(evaluate(FireContext(trigger_id="t", budget_readable=False)))
    assert decision.allowed is False
    assert decision.gate == "budget"
    assert gate_failure_mode("budget") == "closed"


def test_the_INCIDENT_gate_really_fails_OPEN(monkeypatch):
    """The kill switch inherits `incident_active()`'s deliberate fail-open contract: an unreadable
    flag must not halt every automation on a filesystem hiccup (S117)."""
    from personalclaw.guardrails import incident

    monkeypatch.setattr(
        "personalclaw.guardrails.incident._read_file",
        lambda: (_ for _ in ()).throw(OSError("unreadable")),
    )
    incident.reset_incident_mirror()
    try:
        decision = asyncio.run(evaluate(FireContext(trigger_id="t")))
        assert decision.allowed is True
    finally:
        incident.reset_incident_mirror()
    assert gate_failure_mode("incident") == "open"


def test_the_CAPABILITY_fence_really_fails_CLOSED():
    """A security fence: the cost of skipping it is unbounded, while the cost of a skipped budget
    check is one extra run. An empty capability block denies (S116)."""
    decision = asyncio.run(
        evaluate(FireContext(trigger_id="t", requested={"providers": ["bash"]}, capabilities={}))
    )
    assert decision.allowed is False
    assert decision.gate == "capability"
    assert gate_failure_mode("capability") == "closed"


# ── the security fences are never fail-open ──


@pytest.mark.parametrize("fence", ["screen", "capability", "claim", "budget"])
def test_a_SECURITY_FENCE_is_never_classified_open(fence):
    """🔴 The invariant that matters most. A fence quietly moved into the fail-open set would make
    the trust boundary optional under load — and it would read as a small convenience in a diff."""
    assert fence not in FAIL_OPEN_GATES
    assert gate_failure_mode(fence) == "closed"


def test_IDEMPOTENCY_is_never_fail_open():
    """Pre-existing invariant, kept: failing open on idempotency means running the same work
    twice."""
    assert "idempotency" not in FAIL_OPEN_GATES


# ── the cap keys keep their documented direction ──


@pytest.mark.parametrize(
    "cap", ["cost_cap", "rate_cap", "max_runs_per_hour", "max_actions_per_hour", "condition"]
)
def test_a_STORM_GUARD_cap_stays_fail_open(cap):
    """R3's amendment: a budget probe that hangs must not silently stop every automation on the
    machine. The cost of a skipped cap check is one extra run."""
    assert gate_failure_mode(cap) == "open"


def test_every_classified_cap_key_is_a_REAL_gate_key():
    """A classification for a key nobody can author is dead weight that makes the set read as
    covering more than it does."""
    cap_names = {
        name
        for name in FAIL_OPEN_GATES
        if name not in GATE_ORDER  # the fire-path names are checked above
    }
    unknown = sorted(cap_names - set(GATE_KEYS))
    assert not unknown, f"classified names that are not authorable gate keys: {unknown}"
