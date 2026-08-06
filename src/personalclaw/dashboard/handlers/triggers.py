"""Unified Trigger API — /api/triggers/*.

A **Trigger** is "when something happens, run an action". Two kinds share one
surface:

- ``schedule`` — a clock tick fires (every / cron / at). Backed by
  :class:`personalclaw.schedule.ScheduleService` (``state.crons``).
- ``lifecycle`` — an agent-loop event fires (PreToolUse, Stop, …). Backed by
  :class:`personalclaw.hooks.ScriptHookStore`.

This handler is a **facade**: there is no ``triggers.json`` and no migration. It
presents both stores through one ``Trigger`` shape and routes each mutation to the
owning store by a namespaced id (``schedule:<rawId>`` / ``lifecycle:<rawId>``).

Every trigger carries ``action: {provider, config}`` chosen from the action
provider catalog (``/api/action-providers``). For lifecycle triggers the action
is the hook's ``provider`` + ``provider_config``; for schedule triggers it is
``ScheduleJob.action`` — the sole source of what the job runs. The schedule
executor dispatches every provider straight from that action (``invoke-agent``
runs an LLM turn, every other provider runs through the action registry).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from personalclaw.config.loader import config_dir
from personalclaw.dashboard.state import DashboardState
from personalclaw.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)

_SCHEDULE = "schedule"
_LIFECYCLE = "lifecycle"
_EVENT = "event"  # data-event triggers (#38): memory/content patterns
_STORE = "store"  # unified TriggerStore kinds with no legacy backend (file/web_watch/idle/…)

#: The `TriggerStore` kinds the three legacy backends do NOT already surface. `clock` is the
#: schedule backend's, `event` is the event-trigger store's, `manual` has no autonomous surface;
#: everything else (file/web_watch/idle/run_completed/view/webhook) can ONLY be created through the
#: `automation_*` chat tools (S92) and, until now, was invisible on the Automations page — created,
#: fired (S93 for `file`), and unlistable. This is the additive read-plus-safe-mutation slice, not
#: the §6 class-B re-point of the schedule/event backends onto the store.
_STORE_ONLY_KINDS: frozenset[str] = frozenset(
    {"file", "web_watch", "idle", "run_completed", "view", "webhook"}
)


def _event_store():
    from personalclaw.config.loader import config_dir
    from personalclaw.event_triggers import EventTriggerStore

    return EventTriggerStore(config_dir() / "event_triggers.json")


def _serialize_event(t) -> dict[str, Any]:
    return {
        "kind": _EVENT,
        "id": f"{_EVENT}:{t.id}",
        "name": t.id,
        "enabled": t.enabled,
        "pattern": t.pattern,
        "key_glob": t.key_glob,
        "content_re": t.content_re,
        "max_fires": t.max_fires,
        "fire_count": t.fire_count,
        "action": {"provider": t.action_provider, "config": t.action_config},
    }


def _sel():
    import personalclaw.dashboard.handlers as _pkg  # noqa: F811

    return _pkg.sel()


def _redact(s: str) -> str:
    return redact_credentials(redact_exfiltration_urls(s or "")[0])[0]


def _split_id(trigger_id: str) -> tuple[str, str]:
    """``schedule:abc`` → (``schedule``, ``abc``); bare id defaults to schedule.

    A `store` id keeps its own `<kind>:<slug>` form (e.g. `file:my-notes`) as the RAW id, because
    that IS the id in `TriggerStore` — splitting it would break the lookup. So `store:` is stripped
    once and the remainder handed to the store verbatim.
    """
    kind, _, raw = trigger_id.partition(":")
    if raw and kind == _STORE:
        return _STORE, raw
    if raw and kind in (_SCHEDULE, _LIFECYCLE, _EVENT):
        return kind, raw
    return _SCHEDULE, trigger_id


def _trigger_store():
    """The unified store, rooted at the active home.

    Resolved through this module's `config_dir` so there is exactly ONE place to redirect the
    handler's store — which is what `tests/conftest.py::_isolate_trigger_store` patches. Importing
    it inside the function instead would defeat that fixture, and S98 already paid for that
    lesson: the boot migration took `config_dir()` from its caller and wrote to the real home.
    """
    from personalclaw.triggers.store import TriggerStore

    return TriggerStore(base_dir=config_dir())


def _job_shim_for(state: DashboardState, raw: str) -> Any:
    """The minimal job-shaped object `inject_schedule_result_to_session` needs (S104).

    Measured: the injection reads exactly `job.id`, `job.name` and `job.agent_id` — nothing else. So
    a store row is projected onto that tiny surface rather than the whole legacy entity, and the
    handler stops needing `ScheduleService` at all. Returns None when neither store nor legacy
    service knows the id, so the caller can still fall back to a history-only session.
    """
    from personalclaw.schedule import ScheduleJob

    row = _trigger_store().get(raw)
    if row is not None:
        config = {}
        workflow = row.trigger.workflow or {}
        inline = workflow.get("inline") if isinstance(workflow.get("inline"), dict) else None
        raw_config = (inline or workflow).get("config")
        if isinstance(raw_config, dict):
            config = raw_config
        return ScheduleJob(
            id=row.trigger.id,
            name=row.trigger.name,
            action={"provider": (inline or workflow).get("provider", ""), "config": config},
        )
    return None


def _trigger_names(state: DashboardState) -> dict[str, str]:
    """`{trigger_id: name}` for labelling run rows, from the store.

    Includes EVERY kind, not just clock: the unified history feed carries file/web_watch/event runs
    too, and a name map that only knew about schedules would blank exactly the rows the new kinds
    contribute.

    Store-only since S110: the boot migration imports every legacy job, INCLUDING the ones it
    refuses (which it now writes disabled rather than dropping), so there is no id the legacy
    service could name that the store cannot.
    """
    names: dict[str, str] = {}
    for row in _trigger_store().load():
        names[row.trigger.id] = row.trigger.name
    return names


async def _last_result_for(state: DashboardState, raw: str) -> str:
    """The newest run's output for a trigger, or "".

    Reads `ScheduleRunStore` rather than a `last_result` field: `LEGACY_FIELD_MAP` maps that field
    to None deliberately — the RUN RECORD owns a run's output, and a copy on the trigger was a
    second truth that could disagree with it. The run store is keyed by a plain id, so it serves a
    store-backed trigger and a legacy job identically.
    """
    try:
        runs, _total = await _runs_store().list_for_job(raw, 0, 1)
    except Exception:
        logger.debug("could not read the last run for %s", raw, exc_info=True)
        return ""
    if not runs:
        return ""
    newest = runs[0] if isinstance(runs[0], dict) else {}
    return str(newest.get("summary") or newest.get("error") or "")


def _runs_store() -> Any:
    """The run-record store, held DIRECTLY rather than through `ScheduleService` (S105).

    🔴 Named `_runs_store`, not `_run_store`: this module ALREADY has an
    `async def _run_store(raw, request)` handler (S94's manual-fire path), and defining a second
    function with that name silently SHADOWED it — driven, the history endpoint raised
    "_run_store() missing 2 required positional arguments". A same-name redefinition is a real
    hazard in a 1400-line handler module, and Python reports it only at the call site.

    🔴 Measured: all four run-record methods on `ScheduleService` are one-line passthroughs to
    `ScheduleRunStore` (`list_runs` → `list_for_job`, `list_all_runs` → `list_all`, `get_run`,
    `delete_runs` → `delete_for_job`), and the store constructs and answers standalone from a bare
    `base_dir`. So the facade's dependency on the legacy service for run HISTORY was pure
    indirection, and this removes it without changing a single stored byte.

    Keyed by a plain id string, which is why this store survives the whole cutover unchanged: a
    store-created trigger's runs and a legacy job's runs live in the same place, addressed the same
    way. Rooted through this module's `config_dir` so the test fixture redirects it with everything
    else.
    """
    from personalclaw.schedule_history import ScheduleRunStore

    return ScheduleRunStore(config_dir())


def _last_run_status_for(trigger_id: str) -> str:
    """The newest run's PERSISTENT status, or "" — sync, for the list serializer.

    Same contract `ScheduleService.last_run_status` documented and for the same reason (T7): the
    honest status survives restarts and distinguishes `launched` from `ok`, where a trigger's own
    field would report a fire-and-forget run as a success. Reads the store's own sync path, so the
    list serializer stays cheap.
    """
    try:
        rows, _total = _runs_store()._list_for_job_sync(trigger_id, 0, 1)
    except Exception:
        logger.debug("last-run status unavailable for %s", trigger_id, exc_info=True)
        return ""
    return str(rows[0].get("status", "")) if rows else ""


def _week_triggers(state: DashboardState) -> list[Any]:
    """Enabled clock triggers to plot, from the store (S103).

    Only ENABLED ones: a disabled trigger has no fires, and drawing them would make the grid a wish
    list rather than a forecast. Broken rows are excluded too — a row the entity refuses has no
    knowable schedule, and plotting a guess is worse than an absence.

    Store-only since S110: the legacy translation retired with `ScheduleService`'s CRUD, because the
    boot migration imports every legacy job — including the ones it refuses, which it now writes
    disabled rather than dropping.
    """
    store = _trigger_store()
    rows = [
        row.trigger
        for row in store.load()
        if row.trigger.kind == "clock" and row.trigger.enabled and row.ok
    ]
    return rows


def _project_one(trigger: Any, *, start: Any, days: int) -> tuple[list[Any], bool]:
    """Project ONE clock trigger's fires across the window.

    🔴 A CRON NOW PLOTS. The old caller skipped every non-interval trigger with its own admission
    ("a cron trigger is omitted rather than mis-plotted"), which made the week view a forecast of
    only half a user's automations — silently. S96's `arm.next_fire` can step a cron, so it is
    passed to `project_occurrences` as `next_after`. An interval keeps the arithmetic path, because
    a constant step is cheaper and exactly right for it.

    `skip_dates` and `tz_name` are read off the trigger for the reason AUTO-A3 requires: the
    SCHEDULER compares skip dates against the date in the trigger's OWN zone, so a grid on server
    time would strike the wrong column for any job that declares one.
    """
    from personalclaw.triggers.arm import cadence_next_fire as raw_next_fire
    from personalclaw.triggers.calendar import project_occurrences
    from personalclaw.triggers.service import to_epoch

    spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
    kind = str(spec.get("kind") or "")
    interval = float(spec.get("interval_secs") or 0)
    common = {
        "trigger_id": f"{_SCHEDULE}:{trigger.id}",
        "trigger_name": trigger.name,
        "start": start,
        "days": days,
        "gates": getattr(trigger, "gates", None) or {},
        "skip_dates": [str(d) for d in (spec.get("skip_dates") or [])],
        "tz_name": str(spec.get("timezone") or ""),
    }
    if kind in ("interval", "sequence") and interval > 0:
        # 🔴 An UNARMED row must still plot. Measured on the owner's real store: `j-every` is enabled
        # with an empty `next_fire_at` (a re-enable does not arm until the next boot sweep), so
        # reading only `next_fire_at` gave `first_fire_at=0` and `project_occurrences` returned
        # NOTHING — a live 5-minute automation invisible on the week grid. Falling back to
        # `arm.next_fire` computes the same instant the tick will use, so the forecast is honest
        # whether or not the row happens to be armed yet.
        # 🔴 The RAW cadence, not the skip-aware `next_fire` (S112). `project_occurrences` strikes
        # a skipped column ITSELF (AUTO-A3's "struck columns"), so a stepper that already advanced
        # past skipped days would hide exactly the slots the grid exists to show — the user would
        # see a quiet week with no explanation instead of their holiday struck through.
        first = to_epoch(getattr(trigger, "next_fire_at", "")) or raw_next_fire(trigger)
        if first <= 0:
            return [], False
        return project_occurrences(interval_secs=interval, first_fire_at=first, **common)
    if kind == "cron" and spec.get("expr"):
        return project_occurrences(
            interval_secs=0,
            first_fire_at=0,
            next_after=lambda after: raw_next_fire(trigger, now=after),
            **common,
        )
    # `at` is a single fire, and an elapsed one is not a forecast. Nothing to plot.
    return [], False


def _arm_if_needed(store: Any, trigger_id: str) -> None:
    """Arm a clock trigger that has no next fire (S101).

    Called after any write that can make a row newly firable — a create, or a re-enable. Without it
    the row sits `enabled=True` with an empty `next_fire_at`, and `service.due_ids` only surfaces
    rows that HAVE one: enabled and inert until the next boot sweep. `arm.needs_arming` selects
    exactly that population, so a row already carrying a next fire is left alone (re-arming a live
    schedule mid-flight is how a fire gets skipped or doubled).
    """
    from personalclaw.triggers.arm import arm, needs_arming

    row = store.get(trigger_id)
    if row is None or not needs_arming(row.trigger):
        return
    when = arm(row.trigger)
    if not when:
        return  # unarmable (invalid cron, elapsed one-shot) — refuse rather than guess a cadence
    row.trigger.next_fire_at = when
    store.upsert(row.trigger)


def _serialize_store(trigger: Any, *, broken: list[str] | None = None) -> dict[str, Any]:
    """A `TriggerStore` trigger in the shared list shape. Id is `store:<kind>:<slug>` so the
    mutation routes back to the store; `raw_id` is the store's own id."""
    return {
        "kind": _STORE,
        "store_kind": trigger.kind,
        "id": f"{_STORE}:{trigger.id}",
        "raw_id": trigger.id,
        "name": trigger.name,
        "enabled": trigger.enabled,
        "created_by": trigger.created_by,
        "spec": dict(trigger.spec or {}),
        "action": dict(trigger.workflow or {}),
        "health": trigger.health_status,
        # 🔴 THE LIFECYCLE STATE, which this projection omitted (S164). `Trigger.state` carries
        # `active | paused | autopaused | parked | quarantined | retired` and reached NO surface:
        # the list rendered an autopaused automation like a running one, so the states S139
        # (autopause), S159 (park/unpark) and the injection quarantine all decide were invisible
        # on the one page a user manages automations from. `health` cannot substitute — a PARKED
        # trigger is `health: parked` but an AUTOPAUSED one is `health: failing`, and "failing" does
        # not tell the user the automation has STOPPED.
        "state": trigger.state,
        "run_count": trigger.run_count,
        "last_error": _redact(trigger.last_error_summary or ""),
        "broken": list(broken or []),
    }


# ── serializers ──


def _last_run_status(state: DashboardState, job_id: str) -> str | None:
    """The newest run record's status for the honest UI badge (T7), or None.

    Reads the RUN STORE directly (S105). `ScheduleService.last_run_status` was itself a two-line
    read of the same store's sync path, so going through the service was pure indirection — and it
    meant a dashboard whose legacy service was a test double or absent showed no badge at all.
    Still defensive (None on any failure) so the serializer stays robust + JSON-safe.
    """
    status = _last_run_status_for(job_id)
    return status or None


def _schedule_rows(state: DashboardState) -> list[dict[str, Any]]:
    """Every schedule trigger, read from the unified store (§6 re-point — S99).

    The store is the source of truth once the boot migration has run (S98). The legacy service is
    consulted ONLY when the store holds no clock rows, which happens on a home whose migration has
    not run yet — reading the old file for one more boot is strictly better than showing a user zero
    schedules. That fallback is what retires when `ScheduleService` does.

    Names/results are redacted on the way out exactly as `_serialize_schedule` did: the projection
    is a data mapping and knows nothing about credential scrubbing.
    """
    store = _trigger_store()
    clock_rows = [row for row in store.load() if row.trigger.kind == "clock"]
    if clock_rows:
        return [
            _schedule_row_for(state, row.trigger, issues=[i.message for i in row.errors])
            for row in clock_rows
        ]
    return []


def _schedule_row_for(
    state: DashboardState, trigger: Any, *, issues: list[str] | None = None
) -> dict[str, Any]:
    """ONE schedule row, projected and redacted (S101).

    Factored out of `_schedule_rows` so the list and the single-row write responses (create,
    update) answer in exactly the same shape. Two projections would drift, and a create that
    returned a different shape than the list is how a UI ends up with two ideas of one trigger.
    """
    import time as _time

    from personalclaw.triggers.schedule_view import to_schedule_row

    store = _trigger_store()
    projected = to_schedule_row(
        trigger,
        now=_time.time(),
        base_dir=store.base_dir,
        last_run_status=_last_run_status(state, trigger.id) or "",
    )
    projected["name"] = _redact(projected.get("name") or "")
    for key in ("message", "last_error", "schedule"):
        if projected.get(key):
            projected[key] = _redact(str(projected[key]))
    projected["broken"] = list(issues or [])
    return projected


def _serialize_lifecycle(hook, used_by: list[str]) -> dict[str, Any]:
    return {
        "kind": _LIFECYCLE,
        "id": f"{_LIFECYCLE}:{hook.id}",
        "raw_id": hook.id,
        "name": hook.name,
        "enabled": hook.enabled,
        "action": {"provider": hook.provider, "config": hook.provider_config},
        # lifecycle mechanism
        "event": hook.event,
        "matcher": hook.matcher,
        "timeout": hook.timeout,
        "last_run": hook.last_run,
        "last_status": hook.last_status,
        "run_count": hook.run_count,
        "used_by": sorted(used_by),
    }


def _hook_store(state: DashboardState):
    from personalclaw.dashboard.handlers.hooks import _get_hook_store

    return _get_hook_store(state)


def _used_by_index() -> dict[str, list[str]]:
    """hook_id → [agent names that reference it] (agents are lifecycle-scoped)."""
    from personalclaw.config.loader import AppConfig

    idx: dict[str, list[str]] = {}
    try:
        cfg = AppConfig.load()
        for agent_name, prof in (cfg.agents or {}).items():
            for tid in getattr(prof, "triggers", []) or []:
                idx.setdefault(str(tid), []).append(agent_name)
    except Exception:
        logger.debug("triggers used_by index failed", exc_info=True)
    return idx


# ── variable catalog ──


async def api_trigger_variables(request: web.Request) -> web.Response:
    """GET /api/triggers/variables — the ``$variables`` each trigger kind exposes.

    The single server-sourced catalog both UIs read instead of mirroring it:
    ``{schedule: [...], lifecycle: [{event, label, desc, vars, blocking?}, ...]}``.
    Lifecycle entries come from :data:`personalclaw.hooks.LIFECYCLE_EVENT_CATALOG`
    (co-located with the payload assembly that produces those vars); schedule vars
    from :data:`personalclaw.schedule.SCHEDULE_VARS`.
    """
    from personalclaw.hooks import LIFECYCLE_EVENT_CATALOG
    from personalclaw.schedule import SCHEDULE_VARS
    from personalclaw.triggers.events import DORMANCY_NOTES, DORMANT_EVENTS

    lifecycle = [
        {
            "event": e["event"],
            "label": e["label"],
            "desc": e["desc"],
            "vars": list(e["vars"]),
            "blocking": bool(e.get("blocking")),
            # S67: 7 of the 15 declared events have no fire site — they are configurable and never
            # run. The catalog is the only server-sourced list both UIs read, so the badge has to
            # ride here or a user cannot tell a working event from a dead one until they wait for a
            # hook that never fires.
            "dormant": e["event"] in DORMANT_EVENTS,
            "dormant_reason": DORMANCY_NOTES.get(e["event"], ""),
        }
        for e in LIFECYCLE_EVENT_CATALOG
    ]
    return web.json_response({"schedule": list(SCHEDULE_VARS), "lifecycle": lifecycle})


# ── list ──


async def api_triggers(request: web.Request) -> web.Response:
    """GET /api/triggers?type=schedule|lifecycle — every trigger, both kinds.

    ``?type=`` filters to one kind. The response also carries ``server_tz`` for
    the schedule cadence rendering the list does client-side.
    """
    state: DashboardState = request.app["state"]
    want = request.query.get("type", "").strip().lower()

    triggers: list[dict[str, Any]] = []
    if want in ("", _SCHEDULE):
        # 🔴 §6's re-point: the schedule list is read from the UNIFIED STORE, not `state.crons`.
        # Verified before switching — after the boot migration (S98) the store lists exactly the
        # same job ids the legacy service does, so nothing vanishes from the page. Falls back to
        # the legacy service only when the store holds no clock rows (a home whose migration has
        # not run yet): showing a user zero schedules would be worse than reading the old file
        # for one more boot.
        triggers.extend(_schedule_rows(state))
    if want in ("", _LIFECYCLE):
        used_by = _used_by_index()
        for hook in _hook_store(state).list_all():
            triggers.append(_serialize_lifecycle(hook, used_by.get(hook.id, [])))
    if want in ("", _EVENT):
        for t in _event_store().load():
            triggers.append(_serialize_event(t))
    if want in ("", _STORE):
        # Store-only kinds (file/web_watch/idle/…) have no legacy backend. Without this they are
        # created and fired but never listed — the present-and-inert gap S92/S93 opened. Broken
        # rows (S87 lenient parse) are shown, not hidden: a broken automation invisible on its own
        # page is undebuggable.
        for row in _trigger_store().load():
            if row.trigger.kind in _STORE_ONLY_KINDS:
                triggers.append(
                    _serialize_store(row.trigger, broken=[i.message for i in row.errors])
                )

    from personalclaw.schedule import get_local_tz

    tz_name, _ = get_local_tz()
    return web.json_response({"triggers": triggers, "server_tz": tz_name})


# ── create ──


async def api_trigger_create(request: web.Request) -> web.Response:
    """POST /api/triggers — create a schedule or lifecycle trigger.

    Body: ``{trigger_type, name, action: {provider, config}, ...}``. Schedule
    triggers also take the schedule mechanism (``cron``/``every``/``at`` +
    delivery); lifecycle triggers take ``event`` + ``matcher``.
    """
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    trigger_type = str(body.get("trigger_type") or "").strip().lower()
    if trigger_type == _LIFECYCLE:
        return await _create_lifecycle(state, body, request)
    if trigger_type == _SCHEDULE:
        return await _create_schedule(state, body, request)
    if trigger_type == _EVENT:
        return _create_event(body)
    return web.json_response(
        {"error": "trigger_type must be 'schedule', 'lifecycle', or 'event'"}, status=400
    )


def _create_event(body: dict) -> web.Response:
    """Create a data-event trigger (#38)."""
    import uuid

    from personalclaw.event_triggers import EVENT_PATTERNS, EventTrigger

    pattern = str(body.get("pattern") or "").strip()
    if pattern not in EVENT_PATTERNS:
        return web.json_response(
            {"error": f"pattern must be one of {list(EVENT_PATTERNS)}"}, status=400
        )
    action = body.get("action") or {}
    t = EventTrigger(
        id=str(body.get("name") or uuid.uuid4().hex[:8]).strip(),
        pattern=pattern,
        action_provider=str(action.get("provider") or "notify"),
        action_config=dict(action.get("config") or {}),
        key_glob=str(body.get("key_glob") or ""),
        content_re=str(body.get("content_re") or ""),
        max_fires=int(body.get("max_fires", 0) or 0),
    )
    _event_store().upsert(t)
    # A catastrophic `content_re` warns rather than refuses (§7/R4 rule d — S128). It runs on the
    # MEMORY WRITE path, where `(a+)+` costs ~40s on a 30-char value; refusing would break triggers
    # people already have, so the row is created and the risk is named where the author will see it.
    payload = _serialize_event(t)
    hint = _regex_hint(t.content_re)
    if hint:
        payload["warning"] = hint
    return web.json_response(payload, status=201)


def _regex_hint(pattern: str) -> str:
    """The catastrophic-backtracking warning for a `content_re`, or "".

    Thin wrapper so both the create and update handlers ask the same question of the same function —
    a per-handler copy is how one of them ends up not warning.
    """
    from personalclaw.event_triggers import catastrophic_regex_hint

    return catastrophic_regex_hint(pattern or "")


async def _create_lifecycle(
    state: DashboardState, body: dict, request: web.Request
) -> web.Response:
    from personalclaw.validation import HOOK_CREATE_SCHEMA, ValidationError, validate_tool_args

    action = body.get("action") or {}
    payload = {
        "name": body.get("name", ""),
        "event": body.get("event", ""),
        "matcher": body.get("matcher", ""),
        "provider": action.get("provider", ""),
        "provider_config": action.get("config") or {},
    }
    if "timeout" in body:
        payload["timeout"] = body["timeout"]
    try:
        validated = validate_tool_args(payload, HOOK_CREATE_SCHEMA)
    except ValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    hook = _hook_store(state).create(validated)
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="trigger.create",
        outcome="success",
        source="dashboard",
        resources=f"trigger:lifecycle:{hook.id}:{hook.name}:{hook.event}",
    )
    return web.json_response({"ok": True, "trigger": _serialize_lifecycle(hook, [])})


async def _create_schedule(state: DashboardState, body: dict, request: web.Request) -> web.Response:
    from zoneinfo import available_timezones

    from personalclaw.schedule import normalize_action
    from personalclaw.validation import CHANNEL_ID_RE, CHANNEL_MAX_LEN

    name = str(body.get("name", "")).strip()
    if not name:
        return web.json_response({"error": "name required"}, status=400)
    try:
        action = normalize_action(body.get("action"))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    every = body.get("every")
    cron_expr = body.get("cron")
    at_ts = body.get("at")
    channel = str(body.get("channel", "")).strip() or None
    if channel and (len(channel) > CHANNEL_MAX_LEN or not CHANNEL_ID_RE.match(channel)):
        return web.json_response({"error": "invalid channel ID format"}, status=400)
    timezone_val = str(body.get("timezone") or "").strip()
    if timezone_val and timezone_val not in available_timezones():
        return web.json_response(
            {"error": f"invalid timezone: {_redact(timezone_val)!r}"}, status=400
        )

    # 🔴 §6's write re-point (S101): the clock spec is built for the STORE, not for `add_job`. The
    # store's spellings are `expr`/`interval_secs`/`at` (the legacy `cron_expr`/`every_secs`/`at_ts`
    # live on the wire only), and every validation above is unchanged — the re-point moves where the
    # row is PERSISTED, never what the API accepts.
    spec: dict[str, Any] = {}
    if every:
        try:
            spec = {"kind": "interval", "interval_secs": int(every)}
        except (ValueError, TypeError):
            return web.json_response({"error": "'every' must be an integer"}, status=400)
    elif cron_expr:
        spec = {"kind": "cron", "expr": str(cron_expr).strip()}
    elif at_ts:
        try:
            spec = {"kind": "at", "at": float(at_ts), "delete_after_run": True}
        except (ValueError, TypeError):
            return web.json_response(
                {"error": "'at' must be a Unix timestamp in seconds"}, status=400
            )
    else:
        return web.json_response({"error": "every, cron, or at required"}, status=400)

    if timezone_val:
        spec["timezone"] = timezone_val
    if body.get("strict_schedule"):
        spec["strict"] = True
    if isinstance(body.get("skip_dates"), list):
        spec["skip_dates"] = [str(d) for d in body["skip_dates"]]

    from personalclaw.triggers import tools as _tools

    store = _trigger_store()
    result = _tools.create(
        store,
        name=name,
        kind="clock",
        spec=spec,
        # `workflow.inline` is the migrated shape, which `schedule_view` and the gateway's shared
        # dispatch both read — so an API-created row and a migrated one are indistinguishable
        # downstream.
        workflow={"inline": action},
        # `channel`/`silent` are DELIVERY on the entity, not action config (LEGACY_FIELD_MAP:
        # `channel → delivery`, `silent → delivery == none`).
        created_by="user",
    )
    if not result.ok:
        return web.json_response({"error": result.text}, status=400)

    raw_id = str((result.data.get("trigger") or {}).get("id") or "")
    row = store.get(raw_id)
    if row is not None:
        trigger = row.trigger
        trigger.delivery = (
            "none" if body.get("silent") else (f"channel:{channel}" if channel else "")
        )
        store.upsert(trigger)
        _arm_if_needed(store, raw_id)
        row = store.get(raw_id)

    state.push_refresh("crons")
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="trigger.create",
        outcome="success",
        source="dashboard",
        resources=f"trigger:schedule:{raw_id}:{name}",
    )
    projected = _schedule_row_for(state, row.trigger) if row is not None else {}
    return web.json_response({"ok": True, "trigger": projected})


# ── update / delete ──


async def api_trigger_detail(request: web.Request) -> web.Response:
    """PUT / DELETE /api/triggers/{id}."""
    state: DashboardState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])

    if request.method == "DELETE":
        if kind == _STORE:
            store = _trigger_store()
            if store.get(raw) is None:
                return web.json_response({"error": "not found"}, status=404)
            store.delete(raw)
            _sel().log_api_access(
                caller=request.get("user", "dashboard"),
                operation="trigger.delete",
                outcome="success",
                source="dashboard",
                resources=f"trigger:store:{raw}",
            )
            return web.json_response({"ok": True})
        if kind == _EVENT:
            if not _event_store().delete(raw):
                return web.json_response({"error": "not found"}, status=404)
            _sel().log_api_access(
                caller=request.get("user", "dashboard"),
                operation="trigger.delete",
                outcome="success",
                source="dashboard",
                resources=f"trigger:event:{raw}",
            )
            return web.json_response({"ok": True})
        if kind == _LIFECYCLE:
            store = _hook_store(state)
            hook = store.get(raw)
            if not store.delete(raw):
                return web.json_response({"error": "not found"}, status=404)
            _sel().log_api_access(
                caller=request.get("user", "dashboard"),
                operation="trigger.delete",
                outcome="success",
                source="dashboard",
                resources=f"trigger:lifecycle:{raw}:{hook.name if hook else 'unknown'}",
            )
            return web.json_response({"ok": True})
        # schedule — the store owns the row (§6 write re-point, S101). Run HISTORY still lives in
        # `ScheduleRunStore` (keyed by a plain id, so it survives the cutover unchanged), so the
        # delete has two halves: drop the trigger, then drop its runs.
        store = _trigger_store()
        if store.get(raw) is None:
            return web.json_response({"error": "not found"}, status=404)
        store.delete(raw)
        try:
            await _runs_store().delete_for_job(raw)
        except Exception:
            logger.debug("Failed to delete run history for %s", raw, exc_info=True)
        state.push_refresh("crons")
        _sel().log_api_access(
            caller=request.get("user", "dashboard"),
            operation="trigger.delete",
            outcome="success",
            source="dashboard",
            resources=f"trigger:schedule:{raw}",
        )
        return web.json_response({"ok": True})

    # PUT
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    if kind == _EVENT:
        return _update_event(raw, body)
    if kind == _LIFECYCLE:
        return await _update_lifecycle(state, raw, body)
    return await _update_schedule(state, raw, body)


def _update_event(raw: str, body: dict) -> web.Response:
    """PUT an ``event`` trigger (S67 parity).

    Measured before writing: every field a caller could send returned 400 "no fields to update" or
    404 "not found" and wrote NOTHING — `enabled`, `pattern`, `max_fires` and `action` all silently
    failed because the PUT fell through to `_update_schedule`, which looked for a cron job with this
    id and did not find one. A user toggling an event trigger off was told it does not exist while
    it kept firing.

    `pattern` is validated against `EVENT_PATTERNS` rather than accepted: an unrecognized pattern
    matches nothing, so a typo would silently retire a working trigger — the exact failure the
    create path already guards.
    """
    from personalclaw.event_triggers import EVENT_PATTERNS

    store = _event_store()
    trigger = next((t for t in store.load() if t.id == raw), None)
    if trigger is None:
        return web.json_response({"error": "not found"}, status=404)

    if "pattern" in body:
        pattern = str(body.get("pattern") or "").strip()
        if pattern not in EVENT_PATTERNS:
            return web.json_response(
                {"error": f"pattern must be one of {list(EVENT_PATTERNS)}"}, status=400
            )
        trigger.pattern = pattern
    if "enabled" in body:
        trigger.enabled = bool(body["enabled"])
    if "key_glob" in body:
        trigger.key_glob = str(body.get("key_glob") or "")
    if "content_re" in body:
        trigger.content_re = str(body.get("content_re") or "")
    if "max_fires" in body:
        try:
            trigger.max_fires = max(0, int(body.get("max_fires") or 0))
        except (TypeError, ValueError):
            return web.json_response({"error": "max_fires must be an integer"}, status=400)
    if "debounce_secs" in body:
        try:
            trigger.debounce_secs = max(0.0, float(body.get("debounce_secs") or 0.0))
        except (TypeError, ValueError):
            return web.json_response({"error": "debounce_secs must be a number"}, status=400)
    if isinstance(body.get("action"), dict):
        action = body["action"]
        if action.get("provider"):
            trigger.action_provider = str(action["provider"])
        if "config" in action:
            trigger.action_config = dict(action["config"] or {})

    store.upsert(trigger)
    # Same warn-not-refuse treatment as the create path: an edit that INTRODUCES a catastrophic
    # pattern must say so, or the author only learns about it when their memory writes get slow.
    result: dict[str, Any] = {"ok": True, "trigger": _serialize_event(trigger)}
    hint = _regex_hint(trigger.content_re)
    if hint:
        result["warning"] = hint
    return web.json_response(result)


async def _update_lifecycle(state: DashboardState, raw: str, body: dict) -> web.Response:
    from personalclaw.validation import HOOK_UPDATE_SCHEMA, ValidationError, validate_tool_args

    patch: dict[str, Any] = {}
    for k in ("name", "event", "matcher", "timeout", "enabled"):
        if k in body:
            patch[k] = body[k]
    if "action" in body and isinstance(body["action"], dict):
        if body["action"].get("provider"):
            patch["provider"] = body["action"]["provider"]
        if "config" in body["action"]:
            patch["provider_config"] = body["action"]["config"] or {}
    try:
        validated = validate_tool_args(patch, HOOK_UPDATE_SCHEMA)
    except ValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    try:
        hook = _hook_store(state).update(raw, validated)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not hook:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(
        {"ok": True, "trigger": _serialize_lifecycle(hook, _used_by_index().get(raw, []))}
    )


async def _update_schedule(state: DashboardState, raw: str, body: dict) -> web.Response:
    from zoneinfo import available_timezones

    from personalclaw.validation import CHANNEL_ID_RE, CHANNEL_MAX_LEN

    kwargs: dict[str, Any] = {}
    for key in ("name", "channel", "silent", "strict_schedule"):
        if key in body:
            kwargs[key] = body[key]
    if "action" in body and isinstance(body["action"], dict):
        kwargs["action"] = body["action"]  # validated + canonicalized in update_job
    if "channel" in kwargs:
        ch = (kwargs["channel"] or "").strip() or None
        kwargs["channel"] = ch
        if ch and (len(ch) > CHANNEL_MAX_LEN or not CHANNEL_ID_RE.match(ch)):
            return web.json_response({"error": "invalid channel ID format"}, status=400)
    if "cron" in body:
        kwargs["cron_expr"] = body["cron"]
    if "every" in body:
        kwargs["every_secs"] = body["every"]
    if "timezone" in body:
        tz_val = (body["timezone"] or "").strip()
        if tz_val and tz_val not in available_timezones():
            return web.json_response(
                {"error": f"invalid timezone: {_redact(tz_val)!r}"}, status=400
            )
        kwargs["timezone"] = tz_val
    if not kwargs:
        return web.json_response({"error": "no fields to update"}, status=400)

    # 🔴 §6's write re-point (S101): the store owns the row. Legacy kwargs are translated onto the
    # entity's own addresses (`LEGACY_FIELD_MAP`) — cadence into `spec`, channel/silent into
    # `delivery`, the action into `workflow.inline` — and applied through `tools.update`, whose
    # allowlist protects the health fields §3.7 autopauses on.
    store = _trigger_store()
    row = store.get(raw)
    if row is not None:
        from personalclaw.triggers import tools as _tools
        from personalclaw.triggers.schedule_view import channel_of

        spec = dict(row.trigger.spec or {})
        cadence_changed = False
        if "cron_expr" in kwargs and kwargs["cron_expr"]:
            spec = {"kind": "cron", "expr": str(kwargs["cron_expr"]).strip(), **_carried(spec)}
            cadence_changed = True
        elif "every_secs" in kwargs and kwargs["every_secs"]:
            spec = {
                "kind": "interval",
                "interval_secs": int(kwargs["every_secs"]),
                **_carried(spec),
            }
            cadence_changed = True
        if "timezone" in kwargs:
            spec["timezone"] = kwargs["timezone"]
            cadence_changed = True
        if "strict_schedule" in kwargs:
            spec["strict"] = bool(kwargs["strict_schedule"])

        patch: dict[str, Any] = {"spec": spec}
        if "name" in kwargs:
            patch["name"] = str(kwargs["name"])
        if "action" in kwargs and isinstance(kwargs["action"], dict):
            patch["workflow"] = {"inline": kwargs["action"]}
        if "channel" in kwargs or "silent" in kwargs:
            silent = bool(kwargs.get("silent", row.trigger.delivery == "none"))
            channel_id = kwargs.get("channel", channel_of(row.trigger))
            patch["delivery"] = (
                "none" if silent else (f"channel:{channel_id}" if channel_id else "")
            )

        result = _tools.update(store, trigger_id=raw, patch=patch)
        if not result.ok:
            return web.json_response({"error": result.text}, status=400)
        if cadence_changed:
            # A NEW cadence invalidates the armed fire — keeping the old one would fire on the
            # previous schedule after the user changed it. Clear, then re-arm from the new spec.
            updated = store.get(raw).trigger
            updated.next_fire_at = ""
            store.upsert(updated)
            _arm_if_needed(store, raw)
        state.push_refresh("crons")
        return web.json_response(
            {"ok": True, "trigger": _schedule_row_for(state, store.get(raw).trigger)}
        )

    return web.json_response({"error": "not found"}, status=404)


def _carried(spec: dict[str, Any]) -> dict[str, Any]:
    """Spec keys that survive a CADENCE change (S101).

    Replacing `{kind, expr}` wholesale would silently drop `timezone`/`skip_dates`/`strict` — the
    quietly-losable class §1.3 warns about, and the exact fields S91's `verify-migration` exists to
    catch going missing. A user changing `0 9 * * *` to `0 10 * * *` must not lose their holidays.
    """
    return {k: v for k, v in spec.items() if k in ("timezone", "skip_dates", "strict")}


# ── toggle / run / test ──


async def api_trigger_toggle(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/toggle — enable/disable."""
    state: DashboardState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])
    if kind == _STORE:
        # Route through S92's tool functions, which already refuse to enable a broken row (S87) and
        # report WHY — reusing them keeps the API and the chat tool answering identically.
        from personalclaw.triggers import tools as T

        store = _trigger_store()
        row = store.get(raw)
        if row is None:
            return web.json_response({"error": "not found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            body = {}
        want = body.get("enabled") if isinstance(body, dict) else None
        paused = row.trigger.enabled if want is None else (not bool(want))
        result = T.set_paused(store, trigger_id=raw, paused=paused)
        if not result.ok:
            return web.json_response({"error": result.text}, status=400)
        return web.json_response({"ok": True, "trigger": _serialize_store(store.get(raw).trigger)})
    if kind == _LIFECYCLE:
        hook = _hook_store(state).toggle(raw)
        if not hook:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(
            {"ok": True, "trigger": _serialize_lifecycle(hook, _used_by_index().get(raw, []))}
        )
    if kind == _EVENT:
        # Measured (S67): this fell through to the schedule branch, which looked for a cron job
        # with this id, missed, and answered 404 "not found" — the off switch reporting that the
        # trigger the user is looking at does not exist, while it kept firing.
        store = _event_store()
        trigger = next((t for t in store.load() if t.id == raw), None)
        if trigger is None:
            return web.json_response({"error": "not found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            body = {}
        want = body.get("enabled") if isinstance(body, dict) else None
        trigger.enabled = (not trigger.enabled) if want is None else bool(want)
        # An exhausted trigger (`fire_count >= max_fires`) self-retired. Re-enabling it without
        # clearing the count would flip `enabled` to True and change nothing — `record_fire`
        # disables it again on the next fire. So a deliberate re-enable resets the budget.
        if trigger.enabled and trigger.max_fires and trigger.fire_count >= trigger.max_fires:
            trigger.fire_count = 0
        store.upsert(trigger)
        return web.json_response({"ok": True, "trigger": _serialize_event(trigger)})
    # schedule
    try:
        body = await request.json()
    except Exception:
        body = {}
    enabled = body.get("enabled")
    # 🔴 §6's write re-point (S101): the store owns the row. Routed through `tools.set_paused`, which
    # already refuses to enable a row that failed to parse (S87) and reports WHY — so the API and a
    # chat command cannot answer differently about the same trigger.
    store = _trigger_store()
    row = store.get(raw)
    if row is not None:
        from personalclaw.triggers import tools as _tools

        want = (not row.trigger.enabled) if enabled is None else bool(enabled)
        result = _tools.set_paused(store, trigger_id=raw, paused=not want)
        if not result.ok:
            return web.json_response({"error": result.text}, status=400)
        # Re-ENABLING must ARM, or the trigger sits enabled and inert until the next boot sweep —
        # `due_ids` only surfaces rows that carry a `next_fire_at`.
        if want:
            _arm_if_needed(store, raw)
        state.push_refresh("crons")
        return web.json_response({"ok": True})
    return web.json_response({"error": "not found"}, status=404)


async def api_trigger_run(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/run — fire now.

    Schedule triggers run via the schedule service (non-blocking). This is also
    the path the ``schedule_trigger`` MCP tool posts to with the internal secret.
    Lifecycle triggers have no standalone "run" (they fire on agent events) — use
    the test endpoint instead.

    ``?dry_run=1`` (or JSON ``{"dry_run": true}``) runs a **dry-run replay** (T9):
    write-capable tools don't execute, so it previews what the trigger's current
    action WOULD do with no side effects — tagged ``trigger="replay"`` in history.

    Reads no `state` at all since S110 — the clearest evidence the manual-run path is fully
    store-backed.
    """
    kind, raw = _split_id(request.match_info["id"])
    if kind == _STORE:
        return await _run_store(raw, request)
    # 🔴 A STORE trigger DOES have run records (S166). This branch was `kind != _SCHEDULE`, so
    # every store trigger — web_watch, file, idle, run_completed, view, webhook — was told
    # `supported: false` with a reason naming LIFECYCLE triggers, a kind it is not. Measured: three
    # fires of a `web_watch` trigger persisted three rows under `job_id="web_watch:feed"` via
    # `_record_fire_outcome` (S139), and the endpoint reported none, so the detail panel showed "no
    # runs recorded yet" for an automation that had run three times.
    #
    # The store key is the FULL trigger id, which is exactly what `_split_id` returns as `raw` for a
    # store trigger (`store:web_watch:feed` → `web_watch:feed`) — so the same `list_for_job(raw, …)`
    # call the schedule branch makes already works. Nothing new to plumb; the branch was simply
    # written before store triggers had a run store.
    #
    # No catch-all for an unrecognised kind, deliberately: `_split_id` defaults an unknown prefix to
    # `_SCHEDULE` (a bare id is a schedule id, for backwards compatibility), so `kind` can only ever
    # be one of the four constants here — a third branch would be unreachable. Verified by driving
    # `mystery:x`, which resolves to `("schedule", "mystery:x")` and answers an empty schedule
    # history rather than a fabricated "unsupported".
    if kind == _LIFECYCLE:
        return web.json_response(
            {"error": "lifecycle triggers fire on events; use /test"}, status=400
        )
    if kind == _EVENT:
        # Measured (S67): this fell through to the schedule branch and answered 404 "not found",
        # while /test answered 400 "use /run" — a circular dead end with no way to fire an event
        # trigger by hand at all.
        return await _run_event(raw, request)
    # 🔴 §6's manual-run re-point (S102). A store-backed clock trigger fires through the SAME path
    # `_run_store` uses for every other store kind, so a Run button and an autonomous tick fire
    # the same action the same way. `is_running` comes from S97's CLAIM store — cross-process, so
    # an API worker that does not own the scheduler loop can still answer it (the legacy
    # `is_running` read a process-local dict and was simply wrong here).
    store = _trigger_store()
    if store.get(raw) is not None:
        from personalclaw.triggers import claims as _claims

        if _claims.is_running(raw, base_dir=store.base_dir):
            return web.json_response({"error": "already running", "running": True}, status=409)
        return await _run_store(raw, request)

    return web.json_response({"error": "not found"}, status=404)


async def _run_store(raw: str, request: web.Request) -> web.Response:
    """Fire one store-backed trigger (file/web_watch/idle/…) by hand.

    A `dry_run` reports S92's gate plan (which gates a manual fire enforces vs bypasses) without
    executing — that reuses `tools.run`, so the API and the chat tool answer identically. A real
    run dispatches the trigger's declared action through the SAME action-provider registry the
    live file-watch path (`_fire_file_trigger`) uses, so a Run button and an autonomous fire
    execute the same action the same way.

    Manual runs bypass quiet-hours + duty limits but never the injection screen, capability
    allowlist, or budget — the boundary `tools.MANUAL_NEVER_BYPASSES` pins.
    """
    from personalclaw.triggers import tools as T

    store = _trigger_store()
    row = store.get(raw)
    if row is None:
        return web.json_response({"error": "not found"}, status=404)

    dry_run = request.query.get("dry_run", "") in ("1", "true", "yes")
    if not dry_run:
        try:
            body = await request.json()
            dry_run = bool(body.get("dry_run", False)) if isinstance(body, dict) else False
        except Exception:
            dry_run = False

    if dry_run:
        # Reuse tools.run for the gate plan — the API and the chat tool report identically.
        result = T.run(store, trigger_id=raw, dry_run=True)
        return web.json_response({"ok": result.ok, "result": result.data, "text": result.text})

    # A real run: mirror tools.run's guards (broken row refused; a PAUSED trigger still runnable by
    # hand — pausing means "stop firing on your own", and refusing a hand-driven run would remove
    # the main way a user tests one before re-enabling), then dispatch async-native. tools.run's
    # own runner seam is sync, so a coroutine runner would be stringified rather than awaited.
    if row.errors:
        return web.json_response(
            {"error": f"{raw} has a parse error and cannot run ({row.errors[0].message})"},
            status=400,
        )
    # 🔴 The kill switch, on the API's manual path too. This handler dispatches directly rather than
    # through `tools.run`, so enforcing it only there would leave the Run button in the UI firing
    # during an incident — the exact surface an operator is most likely to hit. 200, not 4xx: a
    # guardrail decision is not a malformed request (the rule the event-trigger `/test` follows).
    refusal = T.manual_refusal()
    if refusal:
        return web.json_response({"ok": False, "name": row.trigger.name, "refused": refusal})
    # 🔴 `ok` REPORTS WHETHER THE ACTION RAN (#395). This answered `ok: True` unconditionally, with
    # the failure carried as prose in `result` — so "no action provider configured" arrived as an
    # HTTP 200 success and every caller that checks a status code or an `ok` flag (the two Run
    # buttons, `schedule_trigger`, the `automation_run` MCP runner) read a no-op as a completed run.
    # Still 200, not 4xx: the request was understood and answered honestly, and a trigger whose
    # action cannot be resolved is not a malformed request — the same rule the kill-switch refusal
    # above and the event-trigger `/test` already follow.
    ran, note = await _dispatch_store_action(row.trigger, {"trigger_id": raw, "manual": True})
    paused_note = "" if row.trigger.enabled else " (paused — this run does not re-enable it)"
    return web.json_response({"ok": ran, "name": row.trigger.name, "result": note + paused_note})


async def _dispatch_store_action(
    trigger: Any, payload: dict[str, Any], *, event: str = "manual.run"
) -> tuple[bool, str]:
    """Run a store trigger's declared action through the action-provider registry.

    The same path `gateway._fire_file_trigger` uses — a manual Run and an autonomous fire share one
    dispatch so their behaviour cannot drift. Returns `(ran, note)`: whether the action actually
    executed, and a short status string for the run result.

    `event` labels the source to the action provider the way `gateway._fire_store_trigger` does
    (`file.changed`, `trigger.chained`): a manual Run keeps the default `manual.run`, a pull-on-view
    refresh passes `view.rendered`. It is a label only — the dispatch is the ONE store-action path,
    not a per-caller fork.

    🔴 BOTH ACTION SHAPES, because a real store holds both (#395). This read the FLAT
    `workflow["provider"]` only, and every trigger the API/CLI/app-reconciler/digest writes nests
    its action under `workflow["inline"]` — so `provider_name` was None for essentially every stored
    row and the Run button was a silent no-op on all of them. The docstring above claimed this path
    "cannot drift" from the autonomous fire while `gateway._fire_store_trigger` unwrapped `inline`
    and this one did not. `schedule_view._inline_action` and `screen.requested_capabilities` both
    document the same two-shape contract; this now matches the idiom all three use.

    Provider AND config come from the SAME resolved dict. Taking the provider from `inline` and the
    config from the outer dict would run the right action with an empty config — a worse failure
    than the no-op, because it looks like it worked.

    `ran` is returned rather than folded into the note because the caller answers HTTP `ok` with it:
    a run that resolved no provider is not a success, and reporting `ok: true` for it is what let
    this bug hide behind a 200 for a whole release.
    """
    import time

    from personalclaw.action_providers import ActionContext, get_action_provider
    from personalclaw.action_providers.registry import _ensure_default_providers_registered

    workflow = trigger.workflow or {}
    inline = workflow.get("inline") if isinstance(workflow.get("inline"), dict) else None
    action = inline or workflow
    provider_name = str(action.get("provider") or "")
    if not provider_name:
        return False, "no action provider configured"
    _ensure_default_providers_registered()
    provider = get_action_provider(provider_name)
    if provider is None:
        return False, f"unknown action provider {provider_name!r}"
    # 🔴 RECORD THE RUN (#308). #702 made this path resolve and dispatch the nested action, but it
    # recorded NOTHING — no `ScheduleRunStore` row, no `last_run_ts` stamp. So the action ran while
    # `GET .../history` gained no row and the trigger's last-run stamp never moved, and the UI's
    # completion watcher (`ScheduleDetail`/`StoreTriggerDetail`) waited on a `last_run_ts` that
    # would never change — the "Running…" pill stuck forever. The autonomous fire path records via
    # `gateway._record_fire_outcome`; the docstring above claims the two "share one dispatch so
    # their behaviour cannot drift", and recording is exactly where it had drifted.
    # `_record_manual_run` reuses the SAME `ScheduleRunStore` ledger and the SAME
    # `last_success_at`/`last_failure_at` stamp, tagged `manual` — see its docstring for why
    # `run_count` (the fire budget) is not spent. A `view.rendered` refresh (WF2AUT-6) flows through
    # this same recorder, so a pull-on-view fire leaves the same run evidence a manual Run does.
    ctx = ActionContext(event=event, context="", payload=payload)
    started = time.time()
    try:
        result = await provider.execute(action.get("config") or {}, ctx)
    except Exception as exc:  # noqa: BLE001 - a failed manual run is RECORDED, not raised (#308)
        await _record_manual_run(trigger, started=started, exc=exc)
        return False, f"failed: {type(exc).__name__}: {exc}"
    await _record_manual_run(trigger, started=started, result=result)
    if result is not None and not bool(getattr(result, "success", True)):
        note = str(getattr(result, "error", "") or "") or "the action reported failure"
        return False, f"failed: {note}"
    return True, "ran"


async def _record_manual_run(
    trigger: Any, *, started: float, result: Any = None, exc: BaseException | None = None
) -> None:
    """Append a MANUAL run record and advance the trigger's last-run stamp (#308).

    Reuses the SAME ledger the autonomous fire path appends to — `ScheduleRunStore`, keyed by the
    trigger id (via this module's `_runs_store()`) — and the SAME
    `last_success_at`/`last_failure_at` stamp `gateway._record_fire_outcome` writes, so a Run button
    and an autonomous tick leave the same evidence that a run happened. This is not a parallel
    recorder: it writes the identical `ScheduleRun` shape to the identical store, and stamps the
    identical trigger fields. The read surfaces (`/history`, `_last_run_ts`, the completion watcher)
    already work — they were simply reading a store nothing wrote to on this path.

    Tagged `trigger="manual"`, not the autonomous exit type, for two behaviours the run store
    already depends on: `ScheduleRunStore.count_since` excludes `manual` rows from the hourly cap (a
    person clicking Run is not the machine running away), and `autopause.consecutive_failures_from`
    treats a `manual` exit as transparent — so testing a broken automation by hand can neither
    autopause it nor reset a real failure streak.

    🔴 `run_count` is deliberately NOT incremented and the autopause engine is deliberately NOT run
    — this records the run HISTORY the manual path was missing, never the fire ALLOWANCE it
    correctly skips. `Trigger.run_count` is the `max_fires` fire-budget meter
    (`service._budget_remaining` reads it, written only at the autonomous fire-GRANT in
    `service.tick`), and `tools.MANUAL_NEVER_BYPASSES` pins `budget` among the gates a manual fire
    never spends — the same reason `_run_event` skips `record_fire` and `count_since` excludes
    manual rows. Spending the budget from a Run button would let a user lock themselves out of their
    own automation by testing it. Likewise a manual run must not drive `state`/`health`/`enabled`: a
    hand-run of a healthy trigger that fails once is not the machine deciding to autopause itself.

    Never raises: a bookkeeping failure must not turn a completed manual run into a crashed request,
    the same contract `_record_fire_outcome` holds. Losing a run record is recoverable; losing the
    response is not.
    """
    try:
        import time
        from datetime import datetime, timezone

        from personalclaw.schedule_history import ScheduleRun

        trigger_id = str(getattr(trigger, "id", "") or "")
        if not trigger_id:
            return
        finished = time.time()

        if exc is not None:
            status = "failure"
            error = f"{type(exc).__name__}: {exc}"
            summary = error
        elif result is not None and not bool(getattr(result, "success", True)):
            status = "failure"
            error = str(getattr(result, "error", "") or "") or "the action reported failure"
            summary = error
        elif result is not None and str(getattr(result, "outcome", "") or "") == "launched":
            # T7: the action only STARTED background work; its real outcome is its OWN run's, so the
            # honest status is "launched", not "success" — matching `_record_fire_outcome`.
            status = "launched"
            error = ""
            summary = str(getattr(result, "stdout", "") or "")
        else:
            status = "success"
            error = ""
            summary = str(getattr(result, "stdout", "") or "") if result is not None else ""

        run_id = f"manual-{int(finished * 1000)}"
        # The same store the autonomous recorder appends to; `_append_sync` credential-redacts
        # summary/trace/error on write, so no redaction is owed here.
        await _runs_store().append(
            ScheduleRun(
                run_id=run_id,
                job_id=trigger_id,
                trigger="manual",
                started_at=started,
                finished_at=finished,
                duration_ms=int(max(0.0, finished - started) * 1000),
                status=status,
                summary=summary,
                trace=summary,
                error=error,
            )
        )

        # Advance the SAME last-run stamp the autonomous recorder writes, so `_last_run_ts` moves
        # and the completion watcher clears the pill. `state`/`health`/`enabled` are left untouched
        # — a manual run reports that it ran; it does not drive the lifecycle the autonomous path
        # does.
        store = _trigger_store()
        row = store.get(trigger_id)
        if row is None:
            return
        live = row.trigger
        live.last_run_id = run_id
        stamp = datetime.now(timezone.utc).isoformat()
        if status == "failure":
            live.last_failure_at = stamp
            # Serializers redact this on the way out (`_serialize_store` / `_schedule_row_for`),
            # exactly as `_record_fire_outcome` relies on.
            live.last_error_summary = (error or "manual run failed")[:200]
        else:
            live.last_success_at = stamp
        store.upsert(live)
    except Exception:  # noqa: BLE001 - see the docstring: recording must never fail the run
        logger.debug("could not record the manual run for %s", trigger, exc_info=True)


async def api_trigger_view_render(request: web.Request) -> web.Response:
    """POST /api/triggers/view/render — the `view` kind's production render caller (WF2AUT-6).

    🔴 THE WIRING THIS CLOSES. `pull_on_view` ships a complete `view`-kind runtime — TTL decide,
    freshness sidecar, render fan-out — whose ONLY caller was its own tests, so `surface_binding`
    was set by authors and read by nothing: a `view` trigger could never actually fire. A real
    render surface (an artifact opening, a dashboard tile mounting) POSTs `{surface}` here as it
    renders; every bound `view` trigger past its TTL refreshes, the rest serve cache.

    It is NOT a poll. §3/R10: a `view` trigger must cost nothing when nobody is looking, so the
    runtime is a function a RENDER calls — a background loop would reintroduce the 1440-run-dirs-a-
    day cost the kind exists to avoid. The `pull_on_view` import is function-local for exactly that
    reason: the gateway module must never import it as a loop (the `test_triggers_chain` runtime map
    and `test_NO_background_loop_polls_this_kind` guard depend on it).

    FIRE-AND-FORGET. A synchronous HTTP render must never block on an LLM turn, so each refresh is
    scheduled on the event loop and the decision (what refreshed, what served cache) returns
    immediately — the same background-task idiom the webhook-agent and MCP-probe handlers use.

    A surface with no bound `view` triggers is a 200 with empty lists, not an error: most renders in
    the product bind no trigger, and a 4xx there would make every artifact-open log a failure.
    """
    import time as _time

    from personalclaw.triggers import pull_on_view as _view

    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    surface = str((body or {}).get("surface", "") or "").strip() if isinstance(body, dict) else ""
    if not surface:
        return web.json_response({"refreshed": [], "served_cache": []})

    store = _trigger_store()
    payloads, cached = _view.renders(store, surface=surface, now=_time.time())

    refreshed: list[str] = []
    for payload in payloads:
        row = store.get(str(payload.get("trigger_id") or ""))
        if row is None:
            continue
        # Schedule the dispatch and return — never await the LLM turn in the request. Tracked on
        # `state._background_tasks` so a fire-and-forget refresh is not garbage-collected mid-run,
        # the idiom every other fire-and-forget handler here follows.
        task = asyncio.create_task(
            _dispatch_store_action(row.trigger, payload, event="view.rendered")
        )
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)
        refreshed.append(row.trigger.id)

    return web.json_response({"refreshed": refreshed, "served_cache": cached})


async def _run_event(raw: str, request: web.Request) -> web.Response:
    """Fire one event trigger by hand, through the SAME executor the live path uses.

    A manual fire does NOT call `record_fire`. The fire budget (`max_fires`) exists to bound
    UNATTENDED firing — spending it from a Run button would let a user exhaust and self-retire their
    own trigger by testing it, which is the same asymmetry S65 established for the hourly cap
    (`within_rate_window(manual=True)`). Debounce is skipped for the same reason: it protects
    against event storms, and a person clicking Run is not a storm.
    """
    from personalclaw.event_triggers import execute_event_action
    from personalclaw.validation import sanitize_string

    store = _event_store()
    trigger = next((t for t in store.load() if t.id == raw), None)
    if trigger is None:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    key = sanitize_string(str(body.get("key", "") or "manual"))[:500]
    value = sanitize_string(str(body.get("value", "") or "manual fire"))[:10000]

    outcome = await execute_event_action(
        trigger,
        event_type=str(body.get("event_type", "") or "MemoryUpdate"),
        key=key,
        value=value,
        test=bool(body.get("test")),
    )
    payload = outcome.to_dict()
    for field_name in ("stdout", "stderr", "error"):
        if payload.get(field_name):
            payload[field_name] = _redact(str(payload[field_name]))
    if payload.get("reason"):
        payload["reason"] = _redact(str(payload["reason"]))
    # 200 even for a refusal: the request was understood and answered honestly. A refused fire is
    # not a client error, and returning 4xx would make a denylist block look like a bad request.
    return web.json_response({"ok": outcome.ran, "result": payload})


async def api_trigger_test(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/test — execute a lifecycle or event trigger's action once."""
    from personalclaw.hooks import run_script_hook
    from personalclaw.validation import sanitize_string

    state: DashboardState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])
    if kind == _EVENT:
        # An event trigger's test IS its manual fire (same executor, tagged `test`), so /test and
        # /run agree rather than one of them refusing and pointing at the other.
        return await _run_event(raw, request)
    if kind != _LIFECYCLE:
        return web.json_response(
            {"error": "schedule triggers run their action; use /run?dry_run=1 to preview"},
            status=400,
        )
    hook = _hook_store(state).get(raw)
    if not hook:
        return web.json_response({"error": "not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    context = sanitize_string(body.get("context", "test"))[:10000]
    result = await run_script_hook(hook, context)
    return web.json_response(
        {
            "ok": True,
            "result": {
                "stdout": _redact(result.stdout),
                "stderr": _redact(result.stderr),
                "exit_code": result.exit_code,
                "error": _redact(result.error),
                "duration_ms": result.duration_ms,
            },
        }
    )


async def api_trigger_to_chat(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/to-chat — open a schedule trigger as a chat session."""
    from personalclaw.dashboard.schedule_inject import inject_schedule_result_to_session

    state: DashboardState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])
    if kind != _SCHEDULE:
        return web.json_response({"error": "only schedule triggers open as a chat"}, status=400)
    # 🔴 §6's chat-injection re-point (S104). The injection reads only `id`, `name` and `agent_id`
    # off the job, plus a last RESULT — and `LEGACY_FIELD_MAP` maps `last_result` to None on purpose
    # ("the run record owns a run's output; a copy on the trigger was a second truth"). So a store
    # row plus `ScheduleRunStore` serves this completely, and the run store survives the cutover
    # unchanged because it is keyed by a plain id string.
    job = _job_shim_for(state, raw)

    history = None
    if state.conversation_log is not None:
        try:
            history = await asyncio.to_thread(state.conversation_log.read_messages, f"cron:{raw}")
        except Exception:
            history = None

    if job is None:
        if not history:
            return web.json_response({"error": "not found"}, status=404)
        from personalclaw.schedule import ScheduleJob

        job = ScheduleJob(id=raw, name=f"cron-{raw}")

    last_result = await _last_result_for(state, raw)
    session = inject_schedule_result_to_session(state, job, last_result, history=history)
    return web.json_response({"ok": True, "session": session.key})


# ── history (schedule-only) ──


def _redact_run(run: dict[str, Any], *, job_name: str | None = None) -> dict[str, Any]:
    out = dict(run)
    for key in ("summary", "trace", "error"):
        if out.get(key):
            out[key] = _redact(out[key])
    if job_name is not None:
        out["job_name"] = _redact(job_name)
    return out


async def api_trigger_history(request: web.Request) -> web.Response:
    """GET /api/triggers/{id}/history — run records; other kinds answer `supported: false`.

    No longer touches `state` (S105): the run records come straight from `ScheduleRunStore`, so this
    handler is fully decoupled from `ScheduleService`.
    """
    kind, raw = _split_id(request.match_info["id"])
    if kind == _EVENT:
        # An event trigger keeps a fire COUNTER, not run records — there is no per-run store behind
        # it. Returning the counter with `supported: false` is the honest answer: a bare
        # `{"runs": []}` (what every non-schedule kind used to get) renders as "this ran and kept no
        # records", so a user reads an unrecorded trigger as an idle one.
        trigger = next((t for t in _event_store().load() if t.id == raw), None)
        if trigger is None:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(
            {
                "runs": [],
                "total": 0,
                "supported": False,
                "reason": "event triggers record a fire count, not per-run records",
                "fire_count": trigger.fire_count,
                "last_fired_at": trigger.last_fired_at,
            }
        )
    if kind == _LIFECYCLE:
        return web.json_response(
            {
                "runs": [],
                "total": 0,
                "supported": False,
                "reason": "lifecycle triggers run inline with the agent loop and keep no run store",
            }
        )
    try:
        limit = max(1, min(int(request.query.get("limit", "10")), 100))
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError:
        return web.json_response({"error": "invalid limit/offset"}, status=400)
    try:
        runs, total = await _runs_store().list_for_job(raw, offset, limit)
    except ValueError:
        return web.json_response({"error": "invalid trigger id"}, status=400)
    return web.json_response({"runs": [_redact_run(r) for r in runs], "total": total})


async def api_trigger_history_detail(request: web.Request) -> web.Response:
    """GET /api/triggers/{id}/history/{run_id} — one full run record.

    Reads the run store directly (S105), so this handler no longer touches `state` at all — the
    clearest possible evidence that the run-record surface is fully decoupled from
    `ScheduleService`.
    """
    kind, raw = _split_id(request.match_info["id"])
    # 🔴 A STORE trigger's run must open too (S167). This 404'd every non-schedule kind, so the
    # list route S166 just fixed hands the UI a `run_id` that the detail route then denies — the
    # expander opens on nothing. Driven: `LIST -> total=1 run_id='fire-…'` followed by
    # `DETAIL -> 404`. `get_run(raw, run_id)` already works with a store key (verified against a
    # real `file:notes` row), so the gate was the whole defect.
    #
    # A lifecycle/event trigger still 404s, and correctly: it has no run store to open a record
    # from, and 404 is the honest answer for a record that does not exist.
    if kind not in (_SCHEDULE, _STORE):
        return web.json_response({"error": "not found"}, status=404)
    run_id = request.match_info["run_id"]
    try:
        run = await _runs_store().get_run(raw, run_id)
    except ValueError:
        return web.json_response({"error": "invalid trigger id"}, status=400)
    if run is None:
        return web.json_response({"error": "run not found"}, status=404)
    return web.json_response({"run": _redact_run(run)})


async def api_triggers_week(request: web.Request) -> web.Response:
    """GET /api/triggers/week — the week-grid projection, from `?start=` (AUTO-A1 — S70).

    Read-only, and NO store changes: every occurrence is computed from the recurrence the trigger
    already carries. Quiet windows come back as ANNOTATIONS on each slot rather than as filters — a
    grid that hid suppressed fires would show a schedule the user does not have, and explaining why
    a trigger is not firing when they expect it to is the whole point of the view.

    The duty gate is deliberately NOT evaluated. It is async, provider-backed, and answers about a
    moment in time; asking a calendar app about next Thursday 200 times would be both slow and
    meaningless.
    """
    from datetime import datetime, timedelta

    state: DashboardState = request.app["state"]
    raw_start = (request.query.get("start") or "").strip()
    try:
        start = datetime.fromisoformat(raw_start) if raw_start else datetime.now()
    except ValueError:
        return web.json_response({"error": "start must be an ISO date"}, status=400)
    try:
        days = max(1, min(int(request.query.get("days", "7")), 31))
    except ValueError:
        return web.json_response({"error": "days must be an integer"}, status=400)

    occurrences: list[dict[str, Any]] = []
    truncated: list[str] = []
    for trigger in _week_triggers(state):
        rows, cut = _project_one(trigger, start=start, days=days)
        occurrences.extend(row.to_dict() for row in rows)
        if cut:
            truncated.append(f"{_SCHEDULE}:{trigger.id}")

    from personalclaw.schedule import get_local_tz

    tz_name, _ = get_local_tz()
    return web.json_response(
        {
            "start": start.isoformat(),
            "end": (start + timedelta(days=days)).isoformat(),
            "server_tz": tz_name,
            "occurrences": occurrences,
            # Named rather than a bare bool: "some trigger was capped" is not actionable, and a grid
            # that silently showed a partial week would read as an accurate forecast.
            "truncated": truncated,
        }
    )


async def api_triggers_doctor(request: web.Request) -> web.Response:
    """GET /api/triggers/doctor — structural problems across every trigger (§7 criterion 12).

    Every finding here is invisible at runtime: the trigger looks configured and behaves differently
    than its author intended. An orphaned workflow ref fires and fails forever; a broad watch glob
    fires on everything the user owns; an unknown duty gate fails OPEN, so the automation runs
    unfiltered — the opposite of what its author asked for.
    """
    from personalclaw.triggers.calendar import diagnose

    # No `state`: the doctor reads the store only since S110.
    known_workflows: set[str] | None = None
    try:
        from personalclaw.workflows import service as _wf

        # `list_defs` is ASYNC and returns `{"defs": [ {...dict...} ]}` — not objects. Measured:
        # a `{d.name for d in ...}` comprehension over the coroutine fails into the except below,
        # which would silently suppress the orphan check rather than report it.
        listing = await _wf.list_defs()
        known_workflows = {
            str(d.get("name")) for d in (listing.get("defs") or []) if isinstance(d, dict)
        }
    except Exception:
        # None means "cannot verify", which suppresses the orphan check rather than reporting every
        # reference as broken. A doctor that cries wolf when it cannot read the registry is worse
        # than one that stays quiet about that dimension.
        logger.debug("doctor: workflow defs unavailable", exc_info=True)

    rows: list[dict[str, Any]] = []
    # 🔴 §6's doctor re-point (S103): diagnosed from the STORE, where a `Trigger` carries `gates`,
    # `workflow` and `spec` natively — a `ScheduleJob` had none of them by those names, so the old
    # rows read `getattr(job, "workflow")` (always absent → always empty) and a `watch_glob` field
    # that does not exist on a cron at all. The orphan-workflow and broad-glob checks were therefore
    # scanning blanks for every schedule trigger: present, reviewed, and diagnosing nothing.
    store = _trigger_store()
    store_rows = [row for row in store.load() if row.trigger.kind == "clock"]
    if store_rows:
        for row in store_rows:
            rows.append(
                {
                    "id": f"{_SCHEDULE}:{row.trigger.id}",
                    "gates": row.trigger.gates or {},
                    "workflow": row.trigger.workflow or {},
                    "spec": dict(row.trigger.spec or {}),
                    # 🔴 Required by the `unfenced_write_action` check (S116). Omitting it made the
                    # doctor read every trigger as ungranted — a finding on every row, or on none,
                    # depending on which way the check defaulted. The payload has to carry what the
                    # check reads.
                    "capabilities": dict(row.trigger.capabilities or {}),
                }
            )
    for trigger in _event_store().load():
        rows.append(
            {
                "id": f"{_EVENT}:{trigger.id}",
                "gates": {},
                "workflow": {},
                "spec": {"glob": trigger.key_glob or ""},
            }
        )

    report = diagnose(rows, known_workflows=known_workflows)
    return web.json_response(report.to_dict())


async def api_trigger_history_all(request: web.Request) -> web.Response:
    """GET /api/triggers/history — the run feed across ALL THREE kinds (AUTO crit 4).

    Criterion 4: "a hook, an event trigger, and a cron all show run history in the same
    feed with the same record shape and typed outcomes". This route existed and was
    **schedule-only** — its own docstring said "(schedule runs)" — so the feed a user opens
    to answer "what did my machine do" showed one kind of automation and silently omitted
    the other two.

    `?shape=legacy` keeps the raw `ScheduleRun` dicts for the cron-history UI, which renders
    `trace`/`summary` fields the typed row does not carry. The default is the UNIFIED shape:
    a caller asking for history without naming a shape wants the honest cross-kind answer,
    and defaulting to legacy would mean the criterion is met only by a flag nobody sets.
    """
    from personalclaw.triggers import history as H

    state: DashboardState = request.app["state"]
    try:
        limit = max(1, min(int(request.query.get("limit", "20")), 100))
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError:
        return web.json_response({"error": "invalid limit/offset"}, status=400)
    raw_filter = request.query.get("trigger_id") or None
    kind_filter = ""
    if raw_filter:
        kind_filter, raw_filter = _split_id(raw_filter)
    runs, total = await _runs_store().list_all(offset, limit, raw_filter)
    # 🔴 §6's history re-point (S104): trigger NAMES come from the store. A run row carries only a
    # `job_id`, so the name is a join — and joining against the legacy service would label a run of
    # a store-created trigger with a blank, which reads in the UI as a run of a deleted automation.
    names = _trigger_names(state)
    enriched = [_redact_run(r, job_name=names.get(r.get("job_id", ""), "")) for r in runs]

    if (request.query.get("shape") or "").lower() == "legacy":
        return web.json_response({"runs": enriched, "total": total})

    # The other two kinds contribute only when the caller has not filtered to a specific
    # trigger of a
    # different kind — a `?trigger_id=schedule:x` request asking for one cron must not gain rows for
    # every hook on the machine.
    hooks: list[Any] = []
    events: list[Any] = []
    if not raw_filter or kind_filter == _LIFECYCLE:
        try:
            store = _hook_store(state)
            # `list_all()`, not `list_hooks()` — checked against the class. A wrong name here would
            # have been caught by nothing: the `except` below swallows the AttributeError and the
            # feed would quietly contain zero hooks — the defect this session exists to fix.
            hooks = [h for h in store.list_all() if not raw_filter or h.id == raw_filter]
        except Exception:
            logger.debug("unified history: hook store unavailable", exc_info=True)
    if not raw_filter or kind_filter == _EVENT:
        try:
            events = [t for t in _event_store().load() if not raw_filter or t.id == raw_filter]
        except Exception:
            logger.debug("unified history: event store unavailable", exc_info=True)

    records = H.unified_feed(
        schedule_runs=enriched if (not raw_filter or kind_filter == _SCHEDULE) else [],
        hooks=hooks,
        event_triggers=events,
        limit=limit,
    )
    payload = H.feed_response(records)
    # `total` stays the SCHEDULE total: it is the only kind with a real paginated store, so a sum
    # mixing it with two summary rows would make the pager overshoot. The projected rows are counted
    # separately in the response.
    payload["schedule_total"] = total
    payload["outcomes"] = H.outcome_counts(records)
    return web.json_response(payload)


def register_trigger_routes(app: web.Application) -> None:
    """Register /api/triggers/* — the unified Trigger surface."""
    app.router.add_get("/api/triggers", api_triggers)
    app.router.add_post("/api/triggers", api_trigger_create)
    app.router.add_get("/api/triggers/variables", api_trigger_variables)
    app.router.add_get("/api/triggers/history", api_trigger_history_all)
    # Registered BEFORE `/{id}` so aiohttp does not capture the literal segments as trigger ids —
    # the ordering landmine S67 already paid for with `/surfacing`.
    app.router.add_get("/api/triggers/week", api_triggers_week)
    app.router.add_get("/api/triggers/doctor", api_triggers_doctor)
    # The `view` kind's render caller (WF2AUT-6). Literal path, registered BEFORE `/{id}` for the
    # same S67 reason as `/week` and `/doctor` — otherwise aiohttp captures `view` as a trigger id.
    app.router.add_post("/api/triggers/view/render", api_trigger_view_render)
    app.router.add_put("/api/triggers/{id}", api_trigger_detail)
    app.router.add_delete("/api/triggers/{id}", api_trigger_detail)
    app.router.add_post("/api/triggers/{id}/toggle", api_trigger_toggle)
    app.router.add_post("/api/triggers/{id}/run", api_trigger_run)
    app.router.add_post("/api/triggers/{id}/test", api_trigger_test)
    app.router.add_post("/api/triggers/{id}/to-chat", api_trigger_to_chat)
    app.router.add_get("/api/triggers/{id}/history", api_trigger_history)
    app.router.add_get("/api/triggers/{id}/history/{run_id}", api_trigger_history_detail)
