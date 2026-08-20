"""A report's schedule lives in the TriggerStore, so the existing clock fires it.

WF2KNO-12 shipped everything a scheduled research report needs except the thing that makes
it *scheduled*: the definition store, the runner, the API, the UI, the `research-finding`
kind and the delivery path were all live, and **nothing called ``research_reports.is_due``**,
so a due report never fired. Measured before writing: ``grep -rn 'is_due' src/`` returned no
caller of this module's ``is_due`` outside the module's own docstrings and its tests.

**Why a trigger row and not a sweeper.** ``gateway.py``'s ``_clock_loop`` is explicit that a
clock fire and a file fire go through ONE dispatch path "rather than two that drift". A
second loop iterating report definitions would be that second path: its own arming, its own
overlap policy, its own catch-up rule, its own audit trail — four decisions the trigger
substrate already made, re-made slightly differently. So a report's schedule becomes a
``clock`` trigger whose action is the provider that already exists:

    {"kind": "clock", "spec": {...}, "workflow": {"provider": "knowledge-report",
                                                  "config": {"report_id": <id>}}}

**Two schedules, one authority.** The trigger row decides *when the runner is invoked*; the
report's own ``is_due`` decides *whether this invocation counts as its window* — it owns the
four hardening rules (fail-closed parse, first-fire anchoring, fifty-skipped-windows-fire-
once, a failed run advancing neither stamp nor watermark), and those rules cannot be
re-derived from a cron expression. So the trigger is allowed to be *more eager* than the
report and the pre-flight absorbs the difference. Getting that backwards — trusting the
trigger and dropping ``is_due`` — would silently discard every rule the last session
falsified.

**The mapping is deliberately narrow.** Only what the clock spec needs
(``models.SPEC_KEYS["clock"]``: kind / expr / at / interval_secs / timezone), because a
trigger row carrying scope or citation policy would be a second copy of the definition, and
two copies of a schedule is the drift this module exists to avoid.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.knowledge.research_reports import ReportDefinition
    from personalclaw.triggers.models import Trigger

logger = logging.getLogger(__name__)

#: The action provider a report's trigger dispatches to. One spelling, imported by the
#: handler and asserted equal in the tests — the manual route and the scheduled route reaching
#: different providers is the shape that made the lease's 409 unreachable last session.
ACTION_PROVIDER = "knowledge-report"

#: Prefix for the trigger id that carries a report's schedule. Distinct from
#: `research_reports.CLAIM_ID_PREFIX` (`research-report:`) on purpose: that one names a
#: single-flight CLAIM and this one names a persisted row, and a shared prefix would make a
#: stale claim look like a trigger to anything scanning ids.
TRIGGER_ID_PREFIX = "report-schedule:"

#: A report's trigger is machine-owned. `created_by` is what the Automations UI reads to say
#: who to talk to about a row, and "user" would invite someone to hand-edit a row that the
#: next `save_report` overwrites.
CREATED_BY = "research-report"


def trigger_id_for(report_id: str) -> str:
    """The trigger id that carries `report_id`'s schedule. Deterministic, so a re-save
    updates the same row instead of accumulating one per edit."""
    return f"{TRIGGER_ID_PREFIX}{report_id}"


def report_id_for(trigger_id: str) -> str:
    """The report a trigger id belongs to, or "" — the inverse, so a sweep over trigger rows
    can find orphans without parsing ids by hand at each call site."""
    tid = str(trigger_id or "")
    return tid[len(TRIGGER_ID_PREFIX) :] if tid.startswith(TRIGGER_ID_PREFIX) else ""


def _effective_tz(defn: ReportDefinition) -> str:
    """The zone this report's cron is evaluated in — RESOLVED, never left blank.

    🔴 The drift this closes, found by a test that expected a `timezone` key and got none.
    `ReportDefinition.tz` documents `"" == host local (get_local_tz)`, and `_report_tz` honours
    that. But an ABSENT `spec["timezone"]` means something different on the trigger side:
    `arm._tz` falls back to **UTC**. So a report with no explicit zone on a non-UTC host would
    have its trigger armed for the UTC hour while `is_due` waited for the local one — the fire
    would arrive and be skipped as not-due, and the report would run late or not that day.
    The pre-flight makes that safe rather than wrong, which is exactly why it would have gone
    unnoticed.

    Resolved through `get_local_tz()[0]` — the same source `_report_tz` falls back to — so the
    two sides cannot disagree about what "host local" means.
    """
    explicit = str(getattr(defn, "tz", "") or "").strip()
    if explicit:
        return explicit
    try:
        from personalclaw.schedule import get_local_tz

        return get_local_tz()[0]
    except Exception:  # noqa: BLE001 — a config read must not stop a schedule being written
        logger.debug("report %s: local timezone unresolved; leaving UTC", getattr(defn, "id", ""))
        return ""


def clock_spec(defn: ReportDefinition) -> dict[str, Any]:
    """The `clock` spec for a report's cadence, or `{}` when it has none.

    `{}` is returned rather than a guessed cadence: `is_due` already fails closed on an
    unusable schedule with a named reason, and inventing a default here would give a report
    the user could not schedule a cadence they never chose.
    """
    sched = getattr(defn, "schedule", None)
    kind = str(getattr(sched, "kind", "") or "")
    tz = _effective_tz(defn)
    spec: dict[str, Any] = {}
    if kind == "cron":
        expr = str(getattr(sched, "cron_expr", "") or "").strip()
        if not expr:
            return {}
        spec = {"kind": "cron", "expr": expr}
    elif kind == "every":
        secs = getattr(sched, "every_secs", None)
        if not secs or int(secs) <= 0:
            return {}
        # `interval`, not `every`: `CLOCK_KINDS` is {cron, at, sequence, interval} and a
        # spec naming a kind outside that set is an ERROR from `validate_spec`, so the row
        # would persist broken and never arm.
        spec = {"kind": "interval", "interval_secs": int(secs)}
    elif kind == "at":
        at_ts = getattr(sched, "at_ts", None)
        if not at_ts or float(at_ts) <= 0:
            return {}
        # `arm.next_fire` reads `spec["at"]` through `_positive()` — an epoch float, not an
        # ISO string, unlike `next_fire_at`/`expires_at` on the row itself.
        spec = {"kind": "at", "at": float(at_ts)}
    else:
        return {}
    if tz:
        spec["timezone"] = tz
    return spec


def to_trigger(defn: ReportDefinition, *, now: float = 0.0) -> Trigger | None:
    """The trigger row carrying this report's schedule, or None when it has no cadence.

    `now` is injectable so a test can assert the armed instant rather than race the clock.
    """
    from personalclaw.triggers.models import Trigger

    spec = clock_spec(defn)
    if not spec:
        return None
    report_id = str(getattr(defn, "id", "") or "")
    if not report_id:
        return None
    # `name`, not `title`: `ReportDefinition` has no `title`, so reading one produced an
    # empty string that fell back to the id for every report — a defaulted field is an
    # unsupplied input, and this one was invisible because the fallback looked deliberate.
    title = str(getattr(defn, "name", "") or "").strip() or report_id
    trigger = Trigger(
        id=trigger_id_for(report_id),
        name=f"Research report: {title}",
        kind="clock",
        # The report's own `enabled` is the single switch. A paused report whose trigger
        # stayed enabled would keep waking the runner to be told "disabled" — a fire that
        # exists only to be skipped, and a `.last_status` row that says nothing happened.
        enabled=bool(getattr(defn, "enabled", True)),
        created_by=CREATED_BY,
        spec=spec,
        workflow={"provider": ACTION_PROVIDER, "config": {"report_id": report_id}},
        # `skip` matches the runner's own single-flight claim: the claim already refuses a
        # second concurrent run, and `queue` would pile up fires waiting for a lock the
        # previous run holds.
        overlap="skip",
        session="fresh",
        model_tier="background",
        # The finding is delivered by the runner through `inbox.emit_attention_item`; a
        # delivery here would announce the same finding twice.
        delivery="none",
        failure_delivery="inbox",
    )
    # TWO steps a hand-built row cannot skip, both measured before writing:
    #
    # 1. **The frozen-capability fence.** `screen.py` classifies `knowledge-report` as
    #    write-capable, and `EMPTY_MEANS = "deny"` — "a trigger that declared nothing gets
    #    nothing" — so a row with no `capabilities` block is REFUSED at fire time. Derived
    #    through `capabilities_for_action` rather than hand-written as
    #    `{"providers": [...]}`, so this row is granted exactly what the shipped deriver
    #    grants every other writer's row and cannot drift from it.
    # 2. **Arming.** Measured: a freshly upserted clock row carries `next_fire_at=""` and
    #    `arm.py`'s own docstring calls that state "permanently inert … due_ids STILL []".
    #    `boot_migrate.arm_unarmed` only runs at BOOT, so a report created while the gateway
    #    is up would not fire until the next restart — which is the same never-fires defect
    #    this atom exists to close, moved one step later.
    from personalclaw.triggers import screen
    from personalclaw.triggers.arm import arm

    trigger.capabilities = screen.capabilities_for_action(trigger)
    if trigger.enabled:
        # An unarmable cadence yields "" and is left unarmed — `arm` already refuses to guess,
        # and firing on a guessed cadence is worse than not firing.
        trigger.next_fire_at = arm(trigger, now=now or time.time())
    return trigger


def sync(defn: ReportDefinition) -> str:
    """Create/update the trigger row for `defn`. Returns "" on success, else the reason.

    Never raises. A trigger-store failure must not lose a definition the user just wrote, so
    the save stands and this returns a reason the caller logs — the report is then defined
    but unscheduled, which the pre-flight makes safe (nothing fires) rather than wrong
    (something fires at the wrong time).
    """
    from personalclaw.triggers.store import TriggerStore

    trigger = to_trigger(defn)
    report_id = str(getattr(defn, "id", "") or "")
    if trigger is None:
        # No usable cadence: remove any row from a previous save rather than leaving one that
        # fires on a schedule the definition no longer carries.
        return remove(report_id)
    try:
        TriggerStore().upsert(trigger)
    except Exception as exc:  # noqa: BLE001 — see the docstring: the save must stand
        logger.warning(
            "research report %s saved but NOT scheduled (%s) — it will not fire until the "
            "trigger row is written",
            report_id,
            exc,
        )
        return f"schedule not written: {exc}"
    return ""


def remove(report_id: str) -> str:
    """Delete the trigger row for `report_id`. Returns "" on success or when absent."""
    from personalclaw.triggers.store import TriggerStore

    if not report_id:
        return ""
    try:
        TriggerStore().delete(trigger_id_for(report_id))
    except Exception as exc:  # noqa: BLE001 — a stale row is worse than a logged failure
        logger.warning(
            "research report %s: schedule row not removed (%s) — it may still fire",
            report_id,
            exc,
        )
        return f"schedule not removed: {exc}"
    return ""
