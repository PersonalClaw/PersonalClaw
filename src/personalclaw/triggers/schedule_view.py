"""`Trigger` → the schedule wire shape (§6 API re-point — S98).

§6: "**The existing `/api/triggers` facade becomes the single API by re-pointing its three
backends at
one store** — its `kind:<raw>` id namespace is the migration map." This module is the
projection that
makes that possible for the SCHEDULE backend: it renders a clock `Trigger` in exactly the shape
`_serialize_schedule` produced from a `ScheduleJob`, so the API contract and the frontend
consuming it
are unchanged while the backend underneath becomes the unified store.

**Measured before writing — the data is all carried, at different ADDRESSES.**
`_serialize_schedule` reads 21 fields off `ScheduleJob`. A migrated `Trigger` has none of them by
that name, but `models.LEGACY_FIELD_MAP["ScheduleJob"]` declares where each one went, and every
destination was verified against a real migration:

    spec.timezone / spec.skip_dates / spec.strict   ← timezone / skip_dates / strict_schedule
    workflow.inline                                 ← action
    delivery ("channel:C1", "none")                 ← channel / silent
    session  ("pinned:cron:j")                      ← session_key / persistent_session
    capabilities.env                                ← env
    health_status / last_error_summary              ← last_status / last_error
    last_success_at / last_failure_at               ← last_run_ts

Three fields map to `None` — **deliberate drops the plan already decided**, not gaps this module
should paper over: `created_ts` (a display-only timestamp), `last_result` (the run record owns the
output — a copy on the trigger was a second truth), and `acked_items`. The last was verified dead
before dropping it: the `/api/triggers/{id}/ack` route has **zero callers** (no frontend client
method, no MCP tool) and the owner's real store carries **zero acked entries**, so the field is
already-dead weight rather than a live behaviour. `LEGACY_FIELD_MAP` assigns its future owner
("the inbox owns it — Inbox-Unification").

The projection is one-way on purpose. Writes go through `tools.py` / the store, which already own
validation and the honesty contracts (a broken row refuses to enable, a patch allowlist protects the
health fields). A second write path here would be the dual path the clean break forbids.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The delivery string prefix a channel target uses (`channel:C0AP3QR7Z4M`). Matched rather than
#: split blind: `delivery` also carries `none` (a silent job) and `inbox`, and treating those as a
#: channel id would render "none" as a destination in the UI.
_CHANNEL_PREFIX = "channel:"

#: The session string prefix a pinned (persistent) session uses (`pinned:cron:j`). A `fresh` session
#: is the default and carries no key.
_PINNED_PREFIX = "pinned:"


def _inline_action(trigger: Any) -> dict[str, Any]:
    """The trigger's action as `{provider, config}`.

    `workflow` is `{"inline": {...}}` for a migrated cron and `{"provider": ..., "config": ...}` for
    one the chat tools created (S92 builds the flat shape). Both are accepted because both
    exist in a
    real store — reading only one would render an empty action for half the rows.
    """
    workflow = trigger.workflow if isinstance(getattr(trigger, "workflow", None), dict) else {}
    inline = workflow.get("inline")
    if isinstance(inline, dict):
        return inline
    if workflow.get("provider"):
        return {"provider": workflow.get("provider"), "config": workflow.get("config") or {}}
    return {}


def _action_config(trigger: Any) -> dict[str, Any]:
    config = _inline_action(trigger).get("config")
    return config if isinstance(config, dict) else {}


def channel_of(trigger: Any) -> str:
    """The channel id a trigger delivers to, or "" — read from `delivery`, never from the action."""
    delivery = str(getattr(trigger, "delivery", "") or "")
    return delivery[len(_CHANNEL_PREFIX) :] if delivery.startswith(_CHANNEL_PREFIX) else ""


def is_silent(trigger: Any) -> bool:
    """Whether the trigger suppresses automatic delivery. `delivery == "none"` IS silent."""
    return str(getattr(trigger, "delivery", "") or "") == "none"


def session_key_of(trigger: Any) -> str:
    """The pinned session key, or "" for a fresh session."""
    session = str(getattr(trigger, "session", "") or "")
    return session[len(_PINNED_PREFIX) :] if session.startswith(_PINNED_PREFIX) else ""


def cadence_kind(trigger: Any) -> str:
    """The clock spec's kind (`cron` / `interval` / `at` / `sequence`), or ""."""
    spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
    return str(spec.get("kind") or "")


def to_schedule_row(
    trigger: Any,
    *,
    now: float = 0.0,
    base_dir: Any = None,
    last_run_status: str = "",
) -> dict[str, Any]:
    """A clock `Trigger` in the schedule wire shape the API already publishes.

    Field-for-field compatible with what `_serialize_schedule` produced from a `ScheduleJob`, so the
    frontend's `ScheduleJob` type and every consumer keep working while the backend becomes
    the store.

    `is_running` / `running_since` come from the CLAIM store (S97), not a process-local dict — which
    is why they are answerable at all from an API process that does not own the scheduler loop.
    """
    from personalclaw.triggers import claims

    spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
    action = _inline_action(trigger)
    config = _action_config(trigger)
    kind = cadence_kind(trigger)

    return {
        "kind": "schedule",
        "id": f"schedule:{trigger.id}",
        "raw_id": trigger.id,
        "name": trigger.name,
        "enabled": bool(trigger.enabled),
        "action": action,
        # The schedule mechanism. `expr`/`interval_secs`/`at` are the store's spellings of
        # the legacy
        # `cron_expr`/`every_secs`/`at_ts`; the wire keeps the legacy names so the FE is unchanged.
        "message": str(config.get("task_template") or config.get("message") or ""),
        "schedule": describe_cadence(trigger),
        "cron_expr": str(spec.get("expr") or "") if kind == "cron" else None,
        "every_secs": _int_or_none(spec.get("interval_secs")) if kind == "interval" else None,
        # A deliberate LEGACY_FIELD_MAP drop: `created_ts` was display-only. None rather than a
        # fabricated value — inventing a creation date would be a lie the UI renders as fact.
        "created_ts": None,
        "last_status": str(getattr(trigger, "health_status", "") or ""),
        "last_run_status": last_run_status or None,
        "agent": str(config.get("agent") or "") or None,
        "model": str(config.get("model") or "") or None,
        "channel": channel_of(trigger) or None,
        "approval_mode": str(config.get("approval_mode") or "") or None,
        "silent": is_silent(trigger),
        "strict_schedule": bool(spec.get("strict", False)),
        "timezone": str(spec.get("timezone") or "") or None,
        "skip_dates": list(spec.get("skip_dates") or []),
        "script": str(config.get("script") or "") or None,
        "command": str(config.get("command") or "") or None,
        "last_run_ts": _last_run_ts(trigger),
        # `last_result` is a deliberate drop too — the RUN RECORD owns a run's output, and a copy on
        # the trigger was a second truth that could disagree with it. `has_result` therefore reports
        # whether a run exists to open, which is what the UI actually branches on.
        "has_result": bool(getattr(trigger, "last_run_id", "")),
        "last_result": None,
        "last_error": str(getattr(trigger, "last_error_summary", "") or "") or None,
        "next_run_ts": _next_run_ts(trigger, now=now),
        "is_running": claims.is_running(trigger.id, now=now, base_dir=base_dir),
        "running_since": claims.running_since(trigger.id, now=now, base_dir=base_dir),
        "has_session": bool(session_key_of(trigger)),
        "run_count": int(getattr(trigger, "run_count", 0) or 0),
        # The store's own armed timestamp, so a caller can see what the tick will act on without
        # re-deriving it. `next_run_ts` is the same instant as an epoch for the existing UI.
        "next_fire_at": str(getattr(trigger, "next_fire_at", "") or ""),
    }


def _int_or_none(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _last_run_ts(trigger: Any) -> float | None:
    """The newest of the trigger's success/failure stamps, as an epoch, or None.

    `LEGACY_FIELD_MAP` splits `last_run_ts` into `last_success_at` / `last_failure_at`, which is the
    better model (a failure is not a success), but the wire field means "when did it last RUN" — so
    the newest of the two is the honest answer rather than only the successful one.
    """
    from personalclaw.triggers.service import to_epoch

    stamps = [
        to_epoch(getattr(trigger, "last_success_at", "")),
        to_epoch(getattr(trigger, "last_failure_at", "")),
    ]
    newest = max(stamps)
    return newest if newest > 0 else None


def _next_run_ts(trigger: Any, *, now: float) -> float | None:
    """The persisted next fire, or a freshly computed one when the row is not armed yet.

    Reads `next_fire_at` FIRST — that is what the tick will actually act on, and a UI that
    recomputed its own answer could disagree with the scheduler (the drift S96's one-engine rule
    exists to prevent). Falls back to `arm.next_fire` only for an unarmed row, so a just-created
    trigger still shows when it will run instead of a blank.
    """
    from personalclaw.triggers.arm import next_fire
    from personalclaw.triggers.service import to_epoch

    persisted = to_epoch(getattr(trigger, "next_fire_at", ""))
    if persisted > 0:
        return persisted
    computed = next_fire(trigger, now=now)
    return computed if computed > 0 else None


def describe_cadence(trigger: Any) -> str:
    """The human cadence string for a list row ("At 9:00 AM EDT", "every 300s", "at 01:00 AM …").

    🔴 DELEGATES to the shipped `schedule.format_schedule` rather than formatting here.
    Measured: a hand-rolled version produced `0 9 * * * (America/New_York)` where the live API
    produces `At 9:00 AM EDT` — worse prose, and a SECOND formatter that would drift from the
    one the rest of the UI reads. The store's spellings (`expr`/`interval_secs`/`at`) are
    translated back into a `ScheduleDefinition` to call it: a projection, not a second truth.
    """
    from personalclaw.schedule import ScheduleDefinition, format_schedule

    spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
    kind = cadence_kind(trigger)
    tz = str(spec.get("timezone") or "")
    try:
        if kind == "cron":
            definition = ScheduleDefinition(kind="cron", cron_expr=str(spec.get("expr") or ""))
        elif kind in ("interval", "sequence"):
            definition = ScheduleDefinition(
                kind="every", every_secs=_int_or_none(spec.get("interval_secs"))
            )
        elif kind == "at":
            definition = ScheduleDefinition(kind="at", at_ts=_float_or_none(spec.get("at")))
        else:
            return kind or "unknown"
        return format_schedule(definition, tz_name=tz)
    except Exception:  # noqa: BLE001 - a describable row must never fail a whole list render
        logger.debug(
            "could not describe cadence for %s", getattr(trigger, "id", "?"), exc_info=True
        )
        return kind or "unknown"


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
