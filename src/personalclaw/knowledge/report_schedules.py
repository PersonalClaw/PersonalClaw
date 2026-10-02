"""A report's schedule is mirrored by an automation in the TriggerStore, so the clock fires it.

**Why a trigger row and not a sweeper.** ``gateway.py``'s ``_clock_loop`` is explicit that a
clock fire and a file fire go through ONE dispatch path "rather than two that drift". A second
loop iterating report definitions would be that second path: its own arming, its own overlap
policy, its own catch-up rule, its own audit trail — four decisions the trigger substrate already
made, re-made slightly differently. So a report's schedule becomes a ``clock`` trigger whose action
is the provider that already exists:

    {"kind": "clock", "spec": {...}, "workflow": {"provider": "knowledge-report",
                                                  "config": {"report_id": <id>}}}

**One schedule, mirrored.** The report owns its schedule: its cadence, the zone it runs in, and its
switch (``research_reports``). The automation mirrors them, and the clock fires the automation; a
fire IS the report's run, and nothing reads the schedule a second time. The automation was once
allowed to be "more eager" than the report, with the runner re-checking the report's own cadence on
every fire — and that made two schedules: an automation moved on the Triggers page fired at its new
time, the re-check found no slot of the report's old time had passed, the run was skipped, and the
history called the skip a success. Saving the report then wrote its old time back over the edit.

So an edit moves both, from either side:

* **The report's side** (the Reports page, the reports API): ``save_report`` re-derives the mirror
  (``sync``). Only what the report owns is written — the cadence and zone in ``spec``, the switch
  (which a restore's hold outlasts: ``triggers.restore_hold``), the action, the name and the grant
  (:func:`to_trigger`). What the automation holds of its own —
  where its result and its failures go, whether a missed time runs late, its skip dates — and what
  the clock wrote on it (its next fire, its runs, its health) stay as they are, so a report edit
  cannot undo a Triggers-page setting.
* **The automation's side** (the Triggers page, the chat's automation tools, the CLI): every
  edit goes through ``triggers.tools``, which asks :func:`edit_refusal` first — an edit the report
  cannot hold is refused in words — and hands the saved row to :func:`adopt`, which writes its
  cadence, zone and switch into the report (not a restore's hold, which is not the report's
  switch). The clock's own autopause hands its paused row to
  :func:`adopt` too, so a report whose runs keep failing reads paused, as its automation is.
  Deleting the automation leaves the report unscheduled (:func:`adopt_removal`): it stays, with its
  findings, and runs when you press Run now.

**The mapping is deliberately narrow.** Only what the clock spec needs
(``models.SPEC_KEYS["clock"]``: kind / expr / at / interval_secs / timezone), because a trigger row
carrying scope or citation policy would be a second copy of the definition, and two copies of a
schedule is the drift this module exists to avoid.
"""

from __future__ import annotations

import copy
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
#: who to talk to about a row; together with the id it is how a row is known for a report's
#: (:func:`owns`).
CREATED_BY = "research-report"

#: The spec keys a report's schedule decides: the clock kind, its cadence, and the zone it runs in.
#: Every other spec key on the automation (its skip dates, strict timing) is the automation's own,
#: and the mirror keeps it.
_SCHEDULE_SPEC_KEYS = frozenset({"kind", "expr", "interval_secs", "at", "timezone"})

#: The trigger fields a report decides besides its schedule: the switch is written through
#: (:func:`adopt`); these are what makes the row the report's run, and an edit to one of them on the
#: automation's side is refused (:func:`edit_refusal`).
_REPORT_FIELDS = ("kind", "created_by", "workflow", "overlap", "session", "model_tier")


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

    The spec this is written into is what arms the fire, and an ABSENT `spec["timezone"]` means
    something of its own on the trigger side, so a report whose own zone is blank (`""`, "follow
    the machine") is written with the zone that blank resolves to here and now.

    🔴 IT ONCE DID NOT ACTUALLY RESOLVE, measured (#2520): it went through `get_local_tz()[0]`,
    which answered `'UTC'` whenever `config.timezone` was blank — the stock install — so the
    report ran at the UTC hour on the very hosts this was written for. It now asks
    `personalclaw.timezones.resolve_zone_name`, the SAME function `arm._trigger_tz` calls, so
    the two cannot disagree about what "resolved" means. An unusable `defn.tz` still writes no key
    rather than raising: a schedule must stay writable while its zone is being corrected, and
    `arm.semantic_spec_issues` names the bad zone.

    A `defn.tz` that cannot be checked because the timezone database is unreadable is kept as
    written. What this returns is STORED in the trigger spec, so the UTC the fire path uses
    meanwhile (`timezones.resolve_zone`) must not be: it would outlive the outage.
    """
    from personalclaw.timezones import (
        TimeZoneDatabaseUnavailable,
        UnknownTimeZone,
        resolve_zone_name,
    )

    declared = str(getattr(defn, "tz", "") or "").strip()
    try:
        return resolve_zone_name(declared)[0]
    except UnknownTimeZone as exc:
        logger.warning("report %s: %s", getattr(defn, "id", ""), exc)
        return ""
    except TimeZoneDatabaseUnavailable as exc:
        logger.warning("report %s: %s", getattr(defn, "id", ""), exc)
        return declared


def clock_spec(defn: ReportDefinition) -> dict[str, Any]:
    """The `clock` spec for a report's cadence, or `{}` when it has none.

    `{}` is returned rather than a guessed cadence: a report with no usable schedule has no
    automation and runs when you press Run now, and inventing a default here would give a report
    a cadence nobody chose.
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


def _action(report_id: str) -> dict[str, Any]:
    return {"provider": ACTION_PROVIDER, "config": {"report_id": report_id}}


def _title(defn: ReportDefinition, report_id: str) -> str:
    # `name`, not `title`: `ReportDefinition` has no `title`, so reading one produced an
    # empty string that fell back to the id for every report — a defaulted field is an
    # unsupplied input, and this one was invisible because the fallback looked deliberate.
    name = str(getattr(defn, "name", "") or "").strip() or report_id
    return f"Research report: {name}"


def to_trigger(
    defn: ReportDefinition, *, stored: Trigger | None = None, now: float = 0.0
) -> Trigger | None:
    """The trigger row carrying this report's schedule, or None when it has no cadence.

    *stored* is the row as it is now, when there is one: the result is that row with what the
    report owns written onto it (its cadence and zone, its switch, its action, its name and its
    grant) and everything else kept — see the module docstring. With no row, a new one.

    `now` is injectable so a test can assert the armed instant rather than race the clock.
    """
    from personalclaw.triggers.models import Trigger

    spec = clock_spec(defn)
    if not spec:
        return None
    report_id = str(getattr(defn, "id", "") or "")
    if not report_id:
        return None
    # The report's own `enabled` is the single switch. A paused report whose trigger
    # stayed enabled would keep waking the runner for a run its owner switched off.
    enabled = bool(getattr(defn, "enabled", True))
    if stored is None:
        trigger = Trigger(
            id=trigger_id_for(report_id),
            name=_title(defn, report_id),
            kind="clock",
            enabled=enabled,
            created_by=CREATED_BY,
            spec=spec,
            workflow=_action(report_id),
            # `skip` matches the runner's own single-flight claim: the claim already refuses a
            # second concurrent run, and `queue` would pile up fires waiting for a lock the
            # previous run holds.
            overlap="skip",
            session="fresh",
            model_tier="background",
            # The finding is delivered by the runner through `inbox.emit_attention_item`; a
            # delivery here would announce the same finding twice. The automation's own setting
            # from here on: the Triggers page may route it elsewhere, and the mirror keeps that.
            delivery="none",
            failure_delivery="inbox",
        )
    else:
        from personalclaw.triggers.restore_hold import switch_from_config

        trigger = copy.deepcopy(stored)
        trigger.name = _title(defn, report_id)
        trigger.kind = "clock"
        # The report's switch, unless a restore holds the row: a save says nothing about that.
        switch_from_config(trigger, enabled)
        trigger.created_by = CREATED_BY
        trigger.workflow = _action(report_id)
        trigger.overlap, trigger.session, trigger.model_tier = "skip", "fresh", "background"
        kept = {k: v for k, v in (stored.spec or {}).items() if k not in _SCHEDULE_SPEC_KEYS}
        trigger.spec = {**kept, **spec}
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
    #    is up would not fire until the next restart. A row that exists re-arms by the one rule
    #    every edit follows (`arm.next_fire_after_edit`): a moved cadence re-arms, a row switched
    #    on is armed, and anything else keeps the next fire it has.
    from personalclaw.triggers import screen
    from personalclaw.triggers.arm import arm, next_fire_after_edit

    trigger.capabilities = screen.capabilities_for_action(trigger)
    if stored is None:
        if trigger.enabled:
            # An unarmable cadence yields "" and is left unarmed — `arm` already refuses to
            # guess, and firing on a guessed cadence is worse than not firing.
            trigger.next_fire_at = arm(trigger, now=now or time.time())
    else:
        rearmed = next_fire_after_edit(stored, trigger)
        if rearmed is not None:
            trigger.next_fire_at = rearmed
    return trigger


def _stored(report_id: str) -> Trigger | None:
    """The row this report's schedule is mirrored in, as stored now, or None."""
    from personalclaw.triggers.store import TriggerStore

    row = TriggerStore().get(trigger_id_for(report_id)) if report_id else None
    return None if row is None else row.trigger


def shown(defn: ReportDefinition, *, now: float = 0.0) -> dict[str, Any]:
    """How the Reports page states this report's schedule: ``{"words", "timezone",
    "next_run_at", "restore_hold"}``.

    ``words`` is the sentence the Triggers page shows for the same schedule ("At 8:00 AM EDT, only
    on Monday", `schedule_view.describe_cadence`), ``timezone`` the zone it runs in, and
    ``next_run_at`` its next run (ISO, UTC), ``""`` while the report is paused or has none. Read off
    the trigger row this report's schedule is mirrored in — the stored one, when there is one — so
    the page and the fire cannot disagree about when that is.

    ``restore_hold`` is where the snapshot came from while a restore holds that row
    (`triggers.restore_hold`), ``""`` otherwise. Held, the report has no next run: the report still
    says it is on, and the row that fires it waits for a Resume on the Triggers page."""
    from personalclaw.triggers.schedule_view import describe_cadence

    trigger = to_trigger(defn, stored=_stored(str(getattr(defn, "id", "") or "")), now=now)
    if trigger is None:
        return {"words": "", "timezone": "", "next_run_at": "", "restore_hold": ""}
    return {
        "words": describe_cadence(trigger),
        "timezone": str(trigger.spec.get("timezone") or ""),
        "next_run_at": str(trigger.next_fire_at or "") if trigger.enabled else "",
        "restore_hold": "" if trigger.enabled else str(trigger.restore_hold or ""),
    }


def sync(defn: ReportDefinition) -> str:
    """Create/update the trigger row for `defn`. Returns "" on success, else the reason.

    The row is re-derived from the one as stored now (:func:`to_trigger`), and written only when
    that changes it: an edit the automation's side already wrote and then handed to :func:`adopt`
    comes back here as no change at all.

    Never raises. A trigger-store failure must not lose a definition the user just wrote, so
    the save stands and this returns a reason the caller logs — the report is then defined
    but unscheduled: nothing fires, rather than something firing at the wrong time.
    """
    from personalclaw.triggers.store import TriggerStore

    report_id = str(getattr(defn, "id", "") or "")
    try:
        stored = _stored(report_id)
        trigger = to_trigger(defn, stored=stored)
        if trigger is None:
            # No usable cadence: remove any row from a previous save rather than leaving one
            # that fires on a schedule the definition no longer carries.
            return remove(report_id)
        if stored is None or trigger.to_dict() != stored.to_dict():
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


# ── an edit made on the automation's side ──


def owns(trigger: Any) -> bool:
    """Whether *trigger* is the automation a report's schedule is mirrored in."""
    return bool(report_id_for(getattr(trigger, "id", ""))) and (
        getattr(trigger, "created_by", "") == CREATED_BY
    )


def _schedule_of(spec: dict[str, Any]) -> Any:
    """The report schedule a clock spec states, or None when a report cannot hold it: the inverse
    of :func:`clock_spec`, for the three cadences a report runs on."""
    from personalclaw.schedule import ScheduleDefinition

    kind = str(spec.get("kind") or "")
    try:
        if kind == "cron":
            expr = str(spec.get("expr") or "").strip()
            return ScheduleDefinition(kind="cron", cron_expr=expr) if expr else None
        if kind == "interval":
            secs = int(spec.get("interval_secs") or 0)
            return ScheduleDefinition(kind="every", every_secs=secs) if secs > 0 else None
        if kind == "at":
            at_ts = float(spec.get("at") or 0)
            return ScheduleDefinition(kind="at", at_ts=at_ts) if at_ts > 0 else None
    except (TypeError, ValueError):
        return None
    return None


def _report_name(trigger: Any) -> str:
    from personalclaw.knowledge import research_reports

    defn = research_reports.get_report(report_id_for(getattr(trigger, "id", "")))
    return str(getattr(defn, "name", "") or "") or str(getattr(trigger, "name", "") or "")


def edit_refusal(before: Any, after: Any) -> str:
    """Why an edit that turns the report automation *before* into *after* cannot be the report's,
    or "" when it can. Asked BEFORE the edit is saved, so a refused one changes nothing.

    A report's automation takes its name, its action and how it runs from the report, and its
    schedule must be one a report holds (a cron schedule, an interval, or once). Everything else on
    it is the automation's own.
    """
    if not owns(before):
        return ""
    label = str(getattr(before, "name", "") or "")
    report = _report_name(before)
    if str(getattr(after, "name", "") or "") != label:
        return (
            f"“{label}” is the schedule of the report “{report}” and takes its name from it. "
            "Rename the report in Knowledge › Reports."
        )
    if any(getattr(after, key, None) != getattr(before, key, None) for key in _REPORT_FIELDS):
        return (
            f"“{label}” runs the report “{report}”: what it runs, and how, is the report's. Here "
            "you can change when it runs and switch it on or off; the report itself is edited in "
            "Knowledge › Reports."
        )
    spec = getattr(after, "spec", None)
    spec = spec if isinstance(spec, dict) else {}
    if _schedule_of(spec) is None:
        kind = str(spec.get("kind") or "")
        return (
            f"“{label}” is the schedule of the report “{report}”, and a report runs on a cron "
            f"schedule, every so many seconds, or once — not on a {kind or 'blank'} schedule."
        )
    return ""


def adopt(trigger: Any) -> str:
    """Write what an edit on a report's automation changed into the report: its cadence, the zone
    it runs in, and its switch. Returns "" or why the report could not be written.

    The report's save then re-derives the automation (:func:`sync`), which finds it already says
    the same and writes nothing. A zone the automation shows only because the report left its own
    blank (the machine's, resolved into the spec) is not copied in: the report keeps following the
    machine, and a zone blanked on the automation makes the report follow it too. Never raises —
    the automation's edit is saved, and a report that could not be written is logged and said."""
    if not owns(trigger):
        return ""
    from personalclaw.knowledge import research_reports

    report_id = report_id_for(trigger.id)
    try:
        defn = research_reports.get_report(report_id)
        if defn is None:
            return ""
        changed = False
        spec = trigger.spec if isinstance(trigger.spec, dict) else {}
        mine = clock_spec(defn)
        if {k: v for k, v in spec.items() if k in _SCHEDULE_SPEC_KEYS - {"timezone"}} != {
            k: v for k, v in mine.items() if k != "timezone"
        }:
            schedule = _schedule_of(spec)
            if schedule is not None:
                defn.schedule = schedule
                changed = True
        zone = str(spec.get("timezone") or "").strip()
        if "timezone" in spec and zone != str(mine.get("timezone") or "") and zone != defn.tz:
            defn.tz = zone
            changed = True
        # A row a restore holds is off by the restore, not by anyone here: the report keeps its own.
        if not getattr(trigger, "restore_hold", "") and bool(trigger.enabled) != bool(defn.enabled):
            defn.enabled = bool(trigger.enabled)
            changed = True
        if changed:
            research_reports.save_report(defn)
    except Exception as exc:  # noqa: BLE001 — the automation's edit stands; say what did not
        logger.warning(
            "research report %s: the edit to its automation was not written into it (%s)",
            report_id,
            exc,
        )
        return f"the report was not updated: {exc}"
    return ""


def adopt_removal(trigger: Any) -> str:
    """The automation of a report was deleted: the report stays, with no schedule, and runs when
    you press Run now. Its next save then writes no automation back. Returns "" or why not."""
    if not owns(trigger):
        return ""
    report_id = report_id_for(trigger.id)
    from personalclaw.knowledge import research_reports
    from personalclaw.schedule import ScheduleDefinition

    try:
        defn = research_reports.get_report(report_id)
        if defn is None or not clock_spec(defn):
            return ""
        defn.schedule = ScheduleDefinition(kind="")
        research_reports.save_report(defn)
    except Exception as exc:  # noqa: BLE001 — the deletion stands; say what did not follow it
        logger.warning("research report %s: its schedule was not cleared (%s)", report_id, exc)
        return f"the report was not updated: {exc}"
    return ""
