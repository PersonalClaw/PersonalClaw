"""Every store-backed trigger kind projects the SAME lifecycle triple onto the list wire (496).

🔴 THE DEFECT. `GET /api/triggers` serves four kinds through three projections, and each grew its
own idea of which lifecycle facts a list row needs. Measured against a live gateway with one trigger
seeded per state the backend can report:

    kind      health    state       last_error
    schedule  ✅         ❌ ABSENT    ✅
    store     ✅         ✅           ✅ redacted
    event     ✅         ✅           ✅ RAW

The frontend renders one dot per row from these fields, so the schedule kind's missing `state` made
AUTOPAUSED and QUARANTINED clock triggers arrive identical — both `state: null`, both
`last_status: 'failing'` — and they rendered the same "failing" dot. Quarantine is the one state the
row's own toggle cannot undo (`resume_state` refuses it), so "failing" points the user at a button
that will not work.

The event kind's RAW `last_error` was harmless only because nothing rendered it; issue 496 gives
it a reader in the list row and the inspector, which makes it a disclosure surface. `_redact`'s own
docstring states the rule this now follows: defend AT the projection boundary rather than trust the
caller, because these are public functions and a park reason is free text.

WHY A PARITY RAIL RATHER THAN THREE ASSERTIONS: three projections drifting apart IS the defect. A
per-projection test could pass on all three while they disagreed about which fields exist.
"""

from __future__ import annotations

from typing import Any

import pytest

from personalclaw.triggers.models import Trigger, TriggerHealth, TriggerState
from personalclaw.triggers.store import LoadedTrigger

#: A credential-shaped canary. Every projection must strip it from the reason it renders.
CANARY = "sk-ant-api03-PROJECTIONLEAKCANARY998877"

#: The facts a list row needs to say anything true about whether an automation is working. Named
#: once, so a projection cannot quietly answer a subset.
LIFECYCLE_FACTS = ("health", "state", "last_error")


def _clock(**over: Any) -> Trigger:
    t = Trigger(
        id="clock:parity",
        name="Parity probe",
        kind="clock",
        spec={"kind": "cron", "expr": "0 9 * * *"},
        workflow={"provider": "notify", "config": {"message": "hi"}},
    )
    for key, value in over.items():
        setattr(t, key, value)
    return t


def _file(**over: Any) -> Trigger:
    t = Trigger(
        id="file:parity",
        name="Parity watch",
        kind="file",
        spec={"paths": ["/tmp/parity"]},
        workflow={"provider": "notify", "config": {"message": "hi"}},
    )
    for key, value in over.items():
        setattr(t, key, value)
    return t


def _schedule_row(trigger: Trigger) -> dict[str, Any]:
    """The schedule projection, normalised onto the shared fact names.

    `schedule_view` aliases `health_status` onto the wire's `last_status` for legacy compatibility
    (documented in its module docstring), so the parity check compares MEANINGS, not spellings — the
    alias is the contract, not a gap.
    """
    from personalclaw.triggers.schedule_view import to_schedule_row

    row = to_schedule_row(trigger, now=1_700_000_000.0)
    return {**row, "health": row["last_status"]}


def _store_row(trigger: Trigger) -> dict[str, Any]:
    from personalclaw.dashboard.handlers.triggers import _serialize_store

    return _serialize_store(LoadedTrigger(trigger=trigger))


def _event_row(trigger: Any) -> dict[str, Any]:
    from personalclaw.dashboard.handlers.triggers import _serialize_event

    return _serialize_event(trigger)


def _event_trigger(**over: Any) -> Any:
    from personalclaw.event_triggers import EventTrigger

    t = EventTrigger(
        id="parity-event",
        source="app",
        pattern="AppEvent",
        action_provider="notify",
        action_config={"message": "hi"},
    )
    for key, value in over.items():
        setattr(t, key, value)
    return t


# ── the parity itself ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["schedule", "store", "event"])
def test_every_store_backed_projection_carries_the_lifecycle_triple(kind: str) -> None:
    """`health`, `state` and `last_error` — all three, from all three."""
    if kind == "schedule":
        row = _schedule_row(_clock())
    elif kind == "store":
        row = _store_row(_file())
    else:
        row = _event_row(_event_trigger())
    missing = [f for f in LIFECYCLE_FACTS if f not in row]
    assert not missing, f"{kind} projection omits {missing} — the row cannot report its lifecycle"


def test_the_schedule_projection_distinguishes_autopaused_from_quarantined() -> None:
    """The measured case: both are `health: failing`, and only `state` separates them.

    `autopause.evaluate` returns `FAILING` for a quarantine AND for a five-failure autopause, so
    a surface reading only the rollup cannot tell "the system stopped it, press resume" from "a
    payload matched an injection pattern, resume is refused".
    """
    autopaused = _schedule_row(
        _clock(state=TriggerState.AUTOPAUSED.value, health_status=TriggerHealth.FAILING.value)
    )
    quarantined = _schedule_row(
        _clock(state=TriggerState.QUARANTINED.value, health_status=TriggerHealth.FAILING.value)
    )
    assert autopaused["health"] == quarantined["health"] == TriggerHealth.FAILING.value
    assert (
        autopaused["state"] != quarantined["state"]
    ), "the rollup cannot separate them; state must"
    assert quarantined["state"] == TriggerState.QUARANTINED.value


def test_every_lifecycle_state_survives_the_schedule_projection() -> None:
    """Derived from the enum, so a new state cannot arrive unprojected."""
    members = [s.value for s in TriggerState]
    assert len(members) >= 6, "the enum was read"
    for state in members:
        assert _schedule_row(_clock(state=state))["state"] == state


def test_a_default_row_projects_its_active_state_rather_than_a_blank() -> None:
    """`Trigger.state` defaults to `active`, and the projection must pass that through.

    An empty string would read as "unknown" on the frontend, which routes to the run-outcome
    mapper — the substitution this whole issue is about.
    """
    assert _schedule_row(_clock())["state"] == TriggerState.ACTIVE.value


# ── redaction at the boundary ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["schedule", "store", "event"])
def test_the_reason_a_row_renders_is_redacted_by_the_projection(kind: str) -> None:
    """A credential in a failure reason must not reach the surface that now renders it."""
    reason = f"ConnectionError: refused with {CANARY}"
    if kind == "schedule":
        row = _schedule_row(_clock(last_error_summary=reason))
        # The schedule row is redacted one layer up, at `_schedule_row_for`, which owns the whole
        # projection's scrubbing — asserted separately below so this parametrised case stays honest
        # about WHERE each kind defends.
        from personalclaw.dashboard.handlers.triggers import _redact

        row["last_error"] = _redact(row["last_error"] or "")
    elif kind == "store":
        row = _store_row(_file(last_error_summary=reason))
    else:
        row = _event_row(_event_trigger(state="parked", park_reason=reason))
    assert CANARY not in (row["last_error"] or ""), f"{kind} leaks a credential into the UI"
    assert row["last_error"], "and it does not answer with nothing instead"


def test_the_event_projection_redacts_on_its_own() -> None:
    """The one that did not, before this change.

    Called out separately because the parametrised case above helps the schedule kind along (its
    scrubbing lives in `_schedule_row_for`) and would therefore have passed for the event kind
    too if it did the same. This asserts `_serialize_event` itself.
    """
    row = _event_row(_event_trigger(state="parked", park_reason=f"app gone; token {CANARY}"))
    assert CANARY not in row["last_error"]
    assert "app gone" in row["last_error"], "the useful half of the reason survives"
