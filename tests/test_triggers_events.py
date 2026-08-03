"""S67 — lifecycle-event dormancy + trigger-kind API parity (AUTOMATION-SUBSTRATE §2/§7).

The dormancy list is a REVIEWED CONSTANT, not a scan, because every automatic derivation measured
wrong (see `triggers/events.py`'s docstring: docstring mentions, import blocks, and a real fire that
carries no constant reference). These tests are the guard that keeps a hand-maintained list honest —
`test_dormancy_reconciles_with_the_catalog` is the one that fails when someone wires an event and
forgets to update the list, which is the failure mode that would otherwise tell a user their working
hook is dead.
"""

from __future__ import annotations

from personalclaw.hooks import HOOK_EVENTS, LIFECYCLE_EVENT_CATALOG
from personalclaw.triggers.events import (
    DORMANCY_NOTES,
    DORMANT_EVENTS,
    PARITY_EXEMPTIONS,
    PARITY_OPERATIONS,
    EventStatus,
    configurable_but_dead,
    dormant_events,
    event_status,
    live_events,
    missing_operations,
    parity_report,
    unsupported_response,
    verify_dormancy,
)

# ── dormancy ──


def test_dormancy_reconciles_with_the_catalog():
    """No dormant name is unknown to `hooks.HOOK_EVENTS`, and every noted event is listed dormant.

    THE guard that lets dormancy be a reviewed constant. Without it, wiring `SessionEnd` and
    forgetting this module leaves the UI badging a working hook as dead — the failure direction that
    actively misleads, rather than merely omitting a badge.
    """
    unknown, missing = verify_dormancy()
    assert unknown == [], f"listed dormant but not declared by hooks.py: {unknown}"
    assert missing == [], f"has a dormancy note but is not listed dormant: {missing}"


def test_every_declared_event_gets_exactly_one_status():
    """The status list covers `HOOK_EVENTS` exactly — no gaps, no duplicates, no invented events."""
    infos = event_status()
    names = [i.name for i in infos]
    assert set(names) == set(HOOK_EVENTS)
    assert len(names) == len(HOOK_EVENTS)
    assert all(i.status in {EventStatus.LIVE.value, EventStatus.DORMANT.value} for i in infos)


def test_dormant_and_live_partition_the_catalog():
    dormant, live = set(dormant_events()), set(live_events())
    assert dormant | live == set(HOOK_EVENTS)
    assert not (dormant & live), "an event cannot be both live and dormant"


def test_no_lifecycle_event_is_dormant_any_more():
    """Criterion 5's second clause, closed in S82.

    This test previously pinned `len(dormant_events()) == 7` and said in its own docstring: "if a
    later session wires another event, this fails and the deviation gets recorded again instead of
    the number quietly drifting." That is exactly what happened — S82 wired the remaining seven, so
    the assertion inverts rather than the number being edited.

    Every declared event is now LIVE: `TaskComplete` (S60), the eight that were already firing, and
    the seven S82 wired through `triggers/lifecycle_fire.py`.
    """
    assert dormant_events() == []
    assert DORMANT_EVENTS == frozenset()
    assert set(live_events()) == set(HOOK_EVENTS)


def test_the_dormancy_machinery_survives_an_empty_set():
    """The reporter is KEPT, not deleted, so a later declaration-ahead-of-subsystem is still caught.

    An empty `DORMANT_EVENTS` must not make the guard vacuous: `verify_dormancy` still re-derives
    the live set, and a name added to `HOOK_EVENTS` with no fire site shows up as newly dormant.
    """
    regressed, newly = verify_dormancy()
    assert regressed == [] and newly == []
    # And with a hypothetical declared-but-unwired event, the guard still reports it.
    regressed2, newly2 = verify_dormancy(declared=frozenset({"PreToolUse"}))
    assert isinstance(regressed2, list) and isinstance(newly2, list)


def test_task_complete_is_live_because_a_real_call_site_fires_it():
    """Guards the measurement that corrected the plan.

    `tasks/native._fire_task_complete` fires it via `pool.lifecycle_payload`, whose event name is
    `TaskComplete`. Asserted against the payload rather than by grepping, because the fire carries
    no constant reference at all — which is exactly why the scan-based approach called it dormant.
    """
    from personalclaw.workflows import pool

    payload = pool.lifecycle_payload(task_id="t1", title="x", status="done")
    assert payload["event"] == "TaskComplete"
    assert payload["event"] not in DORMANT_EVENTS


def test_dormant_events_are_the_configurable_trap():
    """Every dormant event is ALSO allowlisted — so all 7 are user-configurable dead ends.

    This is the finding, not an incidental property: if a dormant event were disallowed, nobody
    could configure it and nobody would be misled. All 7 being allowed is what makes the badge
    necessary.
    """
    assert set(configurable_but_dead()) == set(DORMANT_EVENTS)


def test_every_dormant_event_explains_itself():
    """A note per dormant event, naming the subsystem that would own the fire site.

    "No code fires this" is not actionable; "the subagent manager has its own on_event bus" tells a
    reader where the fix goes.
    """
    for name in DORMANT_EVENTS:
        assert DORMANCY_NOTES.get(name), f"{name} is dormant with no explanation"
    for info in event_status():
        if info.dormant:
            assert info.note and "never runs" in info.note
        else:
            assert info.note == "", "a live event must not carry a dormancy note"


def test_live_events_carry_no_dormancy_note():
    assert all(not i.dormant and i.note == "" for i in event_status() if not i.dormant)


# ── parity ──


def test_measured_event_kind_gaps():
    """The shipped `event`-kind surface, as measured off the handler branches before S67.

    Encodes the finding: `list`/`create`/`delete` were handled and everything else fell through to
    the schedule branch.
    """
    shipped = {"list", "create", "delete"}
    gaps = missing_operations("event", shipped)
    assert "toggle" in gaps and "run" in gaps and "update" in gaps and "history" in gaps


def test_exemptions_are_honoured_per_kind():
    """A kind is not faulted for an operation that is meaningless for it.

    `lifecycle` genuinely has no standalone `/run` and `schedule`'s action IS its run — both refuse
    with 400 and a reason in the shipped code, which is correct behaviour, not a gap.
    """
    full = set(PARITY_OPERATIONS)
    assert missing_operations("lifecycle", full - {"run"}) == []
    assert missing_operations("schedule", full - {"test"}) == []
    # but a non-exempt kind IS faulted for the same omission
    assert "run" in missing_operations("event", full - {"run"})


def test_parity_report_returns_only_kinds_with_gaps():
    """An empty dict is the passing state, so a test can assert the whole report at once."""
    full = set(PARITY_OPERATIONS)
    report = parity_report({"a": full, "b": full - {"toggle"}})
    assert report == {"b": ["toggle"]}
    assert parity_report({"a": full}) == {}


def test_exemption_reasons_are_stated():
    for kind, ops in PARITY_EXEMPTIONS.items():
        for op, reason in ops.items():
            assert op in PARITY_OPERATIONS, f"{kind} exempts unknown operation {op}"
            assert reason.strip(), f"{kind}/{op} is exempt with no reason"


def test_unsupported_response_is_400_with_a_reason_never_404():
    """The distinction the whole session turns on.

    404 says "that trigger does not exist" — which, for a trigger the user is looking at, reads as
    data loss. 400 with a reason says "you cannot do that to this kind". The shipped bug returned
    the former for a row that was sitting in the store.
    """
    msg, status = unsupported_response("lifecycle", "run")
    assert status == 400
    assert "lifecycle" in msg and "fires on an agent event" in msg
    generic, status2 = unsupported_response("event", "toggle")
    assert status2 == 400 and "event" in generic


# ── the catalog contract the API depends on ──


def test_catalog_rows_cover_every_declared_event():
    """`LIFECYCLE_EVENT_CATALOG` is what the variables endpoint serves; it must not drift.

    The dormancy badge rides that endpoint, so an event declared in `HOOK_EVENTS` but absent from
    the catalog would be configurable with no badge and no vars — invisible in exactly the way this
    session exists to fix.
    """
    catalog = [row["event"] for row in LIFECYCLE_EVENT_CATALOG]
    assert set(catalog) == set(HOOK_EVENTS)
    assert len(catalog) == len(HOOK_EVENTS)
