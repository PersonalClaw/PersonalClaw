"""Unified Trigger API — /api/triggers/*.

A **Trigger** is "when something happens, run an action". Two stores back one surface:

- the trigger store (``triggers.json``, :class:`personalclaw.triggers.store.TriggerStore`), which
  holds every kind the substrate fires — its ``clock`` rows listed as ``schedule:<id>``, and every
  other kind (``event``, ``file``, ``web_watch``, ``idle``, ``manual``, …) as ``store:<id>``;
- ``lifecycle`` — an agent-loop event fires (PreToolUse, Stop, …). Backed by
  :class:`personalclaw.hooks.ScriptHookStore`, listed as ``lifecycle:<id>``.

The namespaced id routes each mutation to the owning store. A data-event trigger is created here
with ``trigger_type: "event"`` and lives in the trigger store as a ``kind: "event"`` row.

Every trigger carries ``action: {provider, config}`` chosen from the action
provider catalog (``/api/action-providers``). For lifecycle triggers the action
is the hook's ``provider`` + ``provider_config``; for a store row it is ``workflow.inline``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from aiohttp import web

from personalclaw.config import loader as config_loader
from personalclaw.dashboard.handlers import trigger_revisions
from personalclaw.dashboard.state import DashboardState
from personalclaw.http_errors import consent_required, json_error
from personalclaw.request_validation import json_object_body
from personalclaw.security import (
    MaskConflict,
    keep_masked_spans,
    keep_masked_values,
    redact_for_display,
)


def config_dir():
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

_SCHEDULE = "schedule"
_LIFECYCLE = "lifecycle"
#: The `trigger_type` the create form sends for a data-event trigger. Not a namespace: the row lands
#: in the trigger store as `kind: "event"` and is addressed as `store:<id>` like every other kind.
_EVENT = "event"
_STORE = "store"  # unified TriggerStore kinds with no legacy backend (event/file/web_watch/idle/…)


def _sel():
    import personalclaw.dashboard.handlers as _pkg  # noqa: F811

    return _pkg.sel()


def _redact(s: str) -> str:
    # `redact_for_display`: a schedule's edit form is seeded from these rows, and the PUT puts back
    # exactly this mask (`_keep_masked_schedule`).
    return redact_for_display(s or "")


def _split_id(trigger_id: str) -> tuple[str, str]:
    """``schedule:abc`` → (``schedule``, ``abc``); bare id defaults to schedule.

    A `store` id keeps its own `<kind>:<slug>` form (e.g. `file:my-notes`) as the RAW id, because
    that IS the id in `TriggerStore` — splitting it would break the lookup. So `store:` is stripped
    once and the remainder handed to the store verbatim.
    """
    kind, _, raw = trigger_id.partition(":")
    if raw and kind == _STORE:
        return _STORE, raw
    if raw and kind in (_SCHEDULE, _LIFECYCLE):
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


def _project_one(
    trigger: Any, *, start: Any, days: int, until: Any = None
) -> tuple[list[Any], bool]:
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
        "until": until,
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
    # `adaptive` (PR2-8) rides the cron branch, not the interval one: it has no `interval_secs`,
    # so the arithmetic path above would read 0 and drop it — and the week view is the OTHER half
    # of the Triggers page, where a live maintenance automation plotting nothing is the same
    # invisible-but-firing defect the interval comment above records. It steps cleanly, because
    # `cadence_next_fire` for an adaptive clock is `after + <the live cadence>`.
    #
    # 🔴 `at` RIDES IT TOO (issue 561). This branch used to end at a guard reading "`at` is a single
    # fire, and an elapsed one is not a forecast. Nothing to plot." — but the `return` was
    # unconditional, so a one-shot armed for THURSDAY plotted nothing either, and the user saw an
    # empty Thursday with no sign their trigger existed. Measured: cron 7 occurrences, interval 167,
    # every `at` 0, including one two days inside the window.
    #
    # It needs no machinery of its own, because `cadence_next_fire` already IS the one-shot stepper:
    # for kind `at` it answers `at if at > now else 0.0`. Fed to `project_occurrences` that produces
    # exactly the right three behaviours with no special case — the entry call returns 0 for an
    # elapsed one-shot (nothing plotted, which is what the old comment was right about), the window
    # bound drops one beyond the week, and the step after the single fire returns 0 so the loop ends
    # after one occurrence.
    if (kind == "cron" and spec.get("expr")) or kind in ("adaptive", "at"):
        return project_occurrences(
            interval_secs=0,
            first_fire_at=0,
            next_after=lambda after: raw_next_fire(trigger, now=after),
            **common,
        )
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


def _attribution(trigger: Any, *, owner: str) -> dict[str, Any]:
    """The two attribution keys every store-backed projection carries (TSE-4).

    `read_only` is the FRONTEND's whole instruction for a foreign row (§2.2: "rendered read-only —
    author chip, no enable/edit/delete"). Computed server-side from the same
    `ownership.is_owner_authored` predicate the arm path uses, so the page can never offer a control
    for a row the service would refuse to arm — a UI that derived it from a string comparison of its
    own would be a second opinion about who owns a trigger, and the two would drift.
    """
    from personalclaw.triggers.ownership import is_owner_authored

    return {
        "author": str(getattr(trigger, "author", "") or ""),
        "read_only": not is_owner_authored(trigger, owner=owner),
    }


def _issue_messages(row: Any) -> tuple[list[str], list[str]]:
    """A loaded row's ``(errors, warnings)`` as plain messages, for the wire.

    🔴 ONE OWNER FOR BOTH SEVERITIES (issue 531). `LoadedTrigger` has carried `errors` AND `warnings`
    since S87 — `validate_spec` raises the `MIN_CLOCK_INTERVAL_SECS` warning for any interval under
    900s, and its own comment promises "it fires, and it is visibly flagged". It was not flagged:
    every projection below passed `row.errors` only, so the wire had a `broken` key and no
    `warnings` key at all. Measured on a live gateway, on the trigger the create page produces for
    Interval / 1 / minutes::

        row.warnings           ['60s is below the 900s floor for an LLM-invoking trigger; …']
        wire 'broken'          []
        'warnings' on the wire False

    A computed-then-discarded signal is a producer with no consumer, and a floor warning nobody can
    see is the same as no floor at all. Derived here from the row rather than passed in per call
    site, because four call sites each remembering to forward a second list is how one of them
    doesn't — three of them already forwarded NO errors (the create, update and toggle responses all
    answered `broken: []` for a row the list showed as broken).
    """
    return ([i.message for i in row.errors], [i.message for i in row.warnings])


def _serialize_store(row: Any, *, owner: str = "") -> dict[str, Any]:
    """A `TriggerStore` row in the shared list shape. Id is `store:<kind>:<slug>` so the
    mutation routes back to the store; `raw_id` is the store's own id.

    Takes the `LoadedTrigger`, not its `.trigger`: the issues belong to the READ (see
    `LoadedTrigger`'s own docstring), so a projection handed a bare entity cannot report them and
    has to be told — which is how the write responses ended up reporting every row as clean.
    """
    from personalclaw.triggers.schedule_view import _inline_action, _last_run_ts

    trigger = row.trigger
    errors, warnings = _issue_messages(row)
    return {
        "kind": _STORE,
        "store_kind": trigger.kind,
        "id": f"{_STORE}:{trigger.id}",
        "raw_id": trigger.id,
        "name": trigger.name,
        "enabled": trigger.enabled,
        "created_by": trigger.created_by,
        "spec": dict(trigger.spec or {}),
        # The action as `{provider, config}` — the shape the page reads — whichever of the two
        # stored shapes the row uses. The raw `workflow` was sent, so a row whose action nests under
        # `inline` (every row the API, the CLI and the app reconcilers write, a data-event trigger
        # included) read "What it runs: Action" on the page. A workflow ref or resume target, which
        # has no action shape, is still sent as stored.
        "action": _inline_action(trigger) or dict(trigger.workflow or {}),
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
        # When it last RAN, and how: the newest of its success/failure stamps and its newest run
        # record's status, the pair a schedule row already carries. A store row had neither, so the
        # list could only infer "has it run" from `run_count` — the FIRE meter a Run button
        # deliberately does not spend — and a manual trigger read "never" beside the runs its own
        # history listed.
        "last_run_ts": _last_run_ts(trigger),
        "last_run_status": _last_run_status_for(trigger.id) or None,
        "last_error": _redact(trigger.last_error_summary or ""),
        "broken": errors,
        "warnings": warnings,
        "needs_review": _needs_review(trigger),
        "needs_grant": _needs_grant(trigger),
        **_attribution(trigger, owner=owner),
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


def _schedule_row_for(state: DashboardState, row: Any, *, owner: str = "") -> dict[str, Any]:
    """ONE schedule row, projected and redacted (S101).

    Shared by the list (`api_triggers`) and the single-row write responses (create, update), so
    they answer in exactly the same shape. Two projections would drift, and a create that
    returned a different shape than the list is how a UI ends up with two ideas of one trigger.

    Takes the `LoadedTrigger` for the reason `_issue_messages` explains: `issues` used to be a
    caller-supplied list, and the two write responses that did not supply it answered `broken: []`
    for a row the list showed as broken — so "the same shape" held for the KEYS and not for their
    contents. Deriving both severities from the row makes that impossible to get wrong again.
    """
    import time as _time

    from personalclaw.triggers.schedule_view import to_schedule_row

    trigger = row.trigger
    errors, warnings = _issue_messages(row)
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
    projected["broken"] = errors
    projected["warnings"] = warnings
    projected["needs_review"] = _needs_review(trigger)
    projected["needs_grant"] = _needs_grant(trigger)
    projected.update(_attribution(trigger, owner=owner))
    return trigger_revisions.with_revision(projected, schedule=True)


def _needs_review(trigger: Any) -> bool:
    """Whether the row is one a legacy import brought over and the owner has not switched on — the
    page badges it and says what switching it on will ask (`triggers.legacy_import`)."""
    from personalclaw.triggers.legacy_import import needs_review

    return needs_review(trigger)


def _needs_grant(trigger: Any) -> list[str]:
    """The actions the row runs that it is not allowed to, by display name — `[]` when it may run.
    The page badges the row and offers Allow; both dispatches refuse it (`triggers.grants`)."""
    from personalclaw.triggers.grants import labels

    return labels(trigger)


def _serialize_lifecycle(hook, used_by: list[str]) -> dict[str, Any]:
    from personalclaw.hooks import BLOCKING_EVENTS, hook_enforcement

    row = {
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
        # G40: whether THIS hook can block, not just whether its event could. `used_by` alone made
        # the user derive it, and `run_count` actively argued against them — an unbound PreToolUse
        # hook still fires on the informational path, so a policy hook that enforced nothing showed
        # "Ran 3×". `blocking` is the event's capability; `enforcement` is this row's live state.
        # See `hooks.hook_enforcement` for the measurement.
        "blocking": hook.event in BLOCKING_EVENTS,
        "enforcement": hook_enforcement(
            hook.event, enabled=bool(hook.enabled), bound=bool(used_by)
        ),
        # What it is not allowed to use — the same verdict a store trigger carries: the page badges
        # the row and offers Allow, and its fires are refused (`hooks.run_script_hook`).
        "needs_grant": _needs_grant(hook),
    }
    return trigger_revisions.with_revision(row, schedule=False)


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
    ``{schedule: [...], event: [...], lifecycle: [{event, label, desc, vars, blocking?}, ...],
    app_sources: [{app, label, events: [{event, source_event}]}]}``.
    Lifecycle entries come from :data:`personalclaw.hooks.LIFECYCLE_EVENT_CATALOG`
    (co-located with the payload assembly that produces those vars); schedule vars
    from :data:`personalclaw.schedule.SCHEDULE_VARS`; data-event vars from
    :data:`personalclaw.event_triggers.EVENT_VARS`, beside `fire_payload`, which builds them.

    ``app_sources`` (AUTO-A4) is the LIVE app-contributed event vocabulary, read from the
    ``trigger_sources`` registry rather than from manifests: a declared source whose app is
    disabled is not registered, and offering its events would let a user author a trigger that
    cannot fire until they realise the app is off. Served here rather than on a new route for the
    same reason the lifecycle dormancy badge rides here — one catalog fetch, one source of truth.
    """
    from personalclaw.event_triggers import EVENT_VARS
    from personalclaw.hooks import LIFECYCLE_EVENT_CATALOG
    from personalclaw.schedule import SCHEDULE_VARS
    from personalclaw.triggers.events import AGENT_SCOPED_EVENTS, DORMANCY_NOTES, DORMANT_EVENTS

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
            # Issue 610: which fire path reaches this event. Agent-scoped events run through
            # `fire_for_ids` against an agent's own `triggers` list — a hook on one fires for
            # no one until an agent references it. Global events fire for every enabled hook.
            # The badge and the create form both hang off this, so it rides the same catalog
            # the dormancy flag does — one server-sourced list, no second vocabulary.
            "agent_scoped": e["event"] in AGENT_SCOPED_EVENTS,
        }
        for e in LIFECYCLE_EVENT_CATALOG
    ]
    return web.json_response(
        {
            "schedule": list(SCHEDULE_VARS),
            "event": list(EVENT_VARS),
            "lifecycle": lifecycle,
            "app_sources": _app_source_catalog(),
        }
    )


def _app_source_catalog() -> list[dict[str, Any]]:
    """The live app-contributed event vocabulary (AUTO-A4), sorted by app then by event.

    Each event carries BOTH its bare name and its full namespaced form, because those answer
    different questions: the bare name is what the app's own docs call it, and `source_event` is the
    literal string a trigger's `event_glob` matches. Handing the UI only the bare name would make it
    re-derive the prefix — a second place for the namespace rule to drift from
    `trigger_sources.namespace`.

    Sorted here rather than in the UI so there is ONE ordering rule: a list the server describes and
    a list the user scans that disagree is a small thing that costs a real minute to reconcile.
    """
    from personalclaw.trigger_sources import declared_events, get_source, namespace

    declared = declared_events()
    out: list[dict[str, Any]] = []
    for app in sorted(declared):
        provider = get_source(app)
        out.append(
            {
                "app": app,
                "label": str(getattr(provider, "display_name", "") or app),
                "events": [
                    {"event": event, "source_event": namespace(app, event)}
                    for event in sorted(declared[app])
                ],
            }
        )
    return out


# ── list ──


#: The kinds `GET /api/triggers` lists, in the order it lists them.
_LIST_KINDS: tuple[str, ...] = (_SCHEDULE, _LIFECYCLE, _STORE)


def _gather(state: DashboardState, kind: str) -> list[Any]:
    """Every trigger of one listed *kind*, unserialized: THE one gathering.

    The Triggers page lists it and the status strip counts it, and both read it HERE. The count was
    a hand-copied duplicate of the list's gathering, and the strip said "6 triggers" over a page
    that listed 5 (day 8).

    * ``schedule``: the unified store's ``clock`` rows (§6's re-point, S99).
    * ``lifecycle``: every hook in the hook store.
    * ``store``: every OTHER unified-store row — data events, file and web watches, idle, manual,
      … Broken rows (S87 lenient parse) are included, not hidden: a broken automation invisible on
      its own page is undebuggable.

    ``all_rows``, not ``store.load()``: a registered ``trigger`` provider's rows belong on this page
    too (TSE-4). Raises on a source that cannot be read; the caller decides what that means.
    """
    if kind in (_SCHEDULE, _STORE):
        from personalclaw.triggers.provider import all_rows

        rows = all_rows(_trigger_store())
        clock = kind == _SCHEDULE
        return [row for row in rows if (row.trigger.kind == "clock") is clock]
    if kind == _LIFECYCLE:
        return list(_hook_store(state).list_all())
    raise ValueError(f"not a listed trigger kind: {kind!r}")


def unified_trigger_count(state: DashboardState) -> int:
    """How many triggers ``GET /api/triggers`` lists: the status strip's "triggers" (#773).

    The same :func:`_gather`, counted instead of serialized. Never raises: ``GET /api/status`` is
    what a user opens when something is already wrong, so a kind that cannot be read contributes 0
    rather than failing the surface.
    """
    total = 0
    for kind in _LIST_KINDS:
        try:
            total += len(_gather(state, kind))
        except Exception:  # noqa: BLE001 - a status read must never fail on one source
            logger.debug("trigger count: %s unavailable", kind, exc_info=True)
    return total


async def api_triggers(request: web.Request) -> web.Response:
    """GET /api/triggers?type=schedule|lifecycle|store — every trigger.

    ``?type=`` filters to one kind. The response also carries ``server_tz`` for
    the schedule cadence rendering the list does client-side.
    """
    from personalclaw.schedule import get_local_tz
    from personalclaw.triggers.ownership import owner_username

    state: DashboardState = request.app["state"]
    want = request.query.get("type", "").strip().lower()
    # Resolved ONCE for the whole list rather than per row: the owner is one config read, and a
    # per-row read would make a 40-automation page do 40 of them.
    owner = owner_username()

    triggers: list[dict[str, Any]] = []
    for kind in _LIST_KINDS:
        if want not in ("", kind):
            continue
        rows = _gather(state, kind)
        if kind == _SCHEDULE:
            triggers.extend(_schedule_row_for(state, row, owner=owner) for row in rows)
        elif kind == _LIFECYCLE:
            used_by = _used_by_index()
            triggers.extend(_serialize_lifecycle(h, used_by.get(h.id, [])) for h in rows)
        else:
            triggers.extend(_serialize_store(row, owner=owner) for row in rows)

    tz_name, _ = get_local_tz()
    # `owner` mirrors the tasks seam's list response (§2.1): the page labels a foreign row with its
    # author, and needs to know whose name is not worth showing.
    return web.json_response(
        {"triggers": triggers, "server_tz": tz_name, "owner": owner_username()}
    )


# ── create ──


def _stored_action(state: DashboardState, kind: str, raw: str) -> dict[str, Any]:
    """The action trigger *raw* runs now as ``{provider, config}``, or empty values."""
    if kind == _LIFECYCLE:
        hook = _hook_store(state).get(raw)
        if hook is None:
            return {"provider": "", "config": {}}
        return {"provider": hook.provider, "config": dict(hook.provider_config or {})}
    row = _trigger_store().get(raw)
    inline = (row.trigger.workflow or {}).get("inline") if row is not None else None
    inline = inline if isinstance(inline, dict) else {}
    config = inline.get("config")
    return {
        "provider": str(inline.get("provider") or ""),
        "config": dict(config) if isinstance(config, dict) else {},
    }


def _stored_action_config(state: DashboardState, kind: str, raw: str) -> dict[str, Any]:
    """The config of the action trigger *raw* runs now, or ``{}`` — what a write is compared to
    when deciding whether it loosens the trigger's approval posture."""
    return dict(_stored_action(state, kind, raw)["config"])


async def _action_problem(action: Any, *, stored: dict[str, Any] | None = None) -> str:
    """Why a trigger's action could not run as written, asked when it is SAVED; "" when it could.

    One question for every trigger kind's create and edit, because the form that writes the
    action is one form. A `run-workflow` action saved with no workflow, or with one its inputs
    cannot start, failed at every fire instead (`run_workflow_provider.config_problem`). An edit
    that sends only the config is checked against the provider the trigger already runs.
    """
    if not isinstance(action, dict):
        return ""
    stored = stored or {}
    provider = str(action.get("provider") or stored.get("provider") or "")
    config = action.get("config") if "config" in action else stored.get("config")
    if provider == "run-workflow":
        from personalclaw.action_providers.run_workflow_provider import config_problem

        return await config_problem(config if isinstance(config, dict) else {})
    return ""


def _unconsented_loosening(
    request: web.Request, body: dict, *, where: str, stored: dict[str, Any]
) -> tuple[str, str] | None:
    """``(field, consent)`` when *body*'s action loosens whether the trigger's agent asks you — an
    ``approval_mode: "auto"``, a ``capability: "mutating"`` write grant — over the *stored* action
    config (``{}`` for a new trigger) without ``confirm: true``; ``None`` otherwise. The refusal is
    written to the security audit; the caller answers ``consent_required``.

    The owner's half of the rule; an app cannot define a trigger at all
    (``apps/permissions.ROUTE_AUTHZ``). The Schedule form's "Auto-approve tools" switch is the
    common case, and the SPA asks in the sentence this carries (``withSecurityConsent``).
    """
    from personalclaw.automation_posture import unconsented_step_loosening

    action = body.get("action")
    if not isinstance(action, dict):
        return None
    new = action.get("config") if "config" in action else stored
    loosened = unconsented_step_loosening(
        where, current=stored, new=new if isinstance(new, dict) else {}, body=body
    )
    if loosened is None:
        return None
    field, _consent = loosened
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="trigger.write",
        outcome="denied",
        source="dashboard",
        resources=f"{field}: loosening without confirm",
    )
    return loosened


def _grant_for_save(
    state: DashboardState, body: dict, *, kind: str, raw: str
) -> tuple[list[str], str] | None:
    """``(providers, sentence)`` when saving *body*'s action needs the owner to allow it, else
    ``None`` (`triggers.grants.question`).

    The editor is where the owner re-points an action or rewrites what it runs, so it is where they
    are asked: an edit that saved `bash` into a trigger allowed only `notify`, or a new command into
    a trigger allowed to run the old one, used to save with nothing asked. With the owner's yes the
    save grants it (`tools.update`), so Run now works straight away.
    """
    import copy

    from personalclaw.triggers import grants

    action = body.get("action")
    if kind == _LIFECYCLE:
        # A lifecycle save may also send `enabled: true`, which is the toggle's switch-on — or its
        # Allow, when the trigger is on already — and is asked the toggle's question. Not asking it
        # of a trigger that is on would leave `_update_lifecycle` to switch it off, the opposite of
        # what the save asked for.
        hook = _hook_store(state).get(raw)
        if hook is None:
            return None
        candidate = copy.copy(hook)
        if isinstance(action, dict):
            _apply_hook_action(candidate, action)
            return grants.question(candidate, before=hook)
        need = grants.missing(candidate) if body.get("enabled") is True else []
        return (need, grants.consent(candidate, need)) if need else None
    if not isinstance(action, dict):
        return None
    row = _trigger_store().get(raw)
    if row is None:
        return None
    # The shape `_update_schedule` writes, so the question is about the row the save would store.
    candidate = copy.copy(row.trigger)
    candidate.workflow = {"inline": action}
    return grants.question(candidate, before=row.trigger)


def _grant_for_create(body: dict, *, trigger_type: str) -> tuple[list[str], str] | None:
    """``(providers, sentence)`` when creating *body*'s trigger needs the owner to allow its action,
    else ``None``. The create dialog asks it with the rest, so creating one stays a single step."""
    from personalclaw.hooks import ScriptHook
    from personalclaw.triggers import grants
    from personalclaw.triggers.models import Trigger

    action = body.get("action")
    if not isinstance(action, dict):
        return None
    name = str(body.get("name") or "").strip()
    if trigger_type == _LIFECYCLE:
        candidate: Any = ScriptHook(name=name)
        _apply_hook_action(candidate, action)
    elif trigger_type in (_SCHEDULE, _EVENT):
        kind = "clock" if trigger_type == _SCHEDULE else "event"
        candidate = Trigger(id="", name=name, kind=kind, workflow={"inline": action})
    else:
        return None
    return grants.question(candidate)


def _apply_hook_action(hook: Any, action: dict) -> None:
    """Put *action* on *hook* the way `_update_lifecycle` does: the provider when one is named, the
    config when one is sent."""
    if action.get("provider"):
        hook.provider = str(action["provider"])
    if "config" in action:
        hook.provider_config = dict(action.get("config") or {})


async def api_trigger_create(request: web.Request) -> web.Response:
    """POST /api/triggers — create a schedule, lifecycle or data-event trigger.

    Body: ``{trigger_type, name, action: {provider, config}, ...}``. Schedule
    triggers also take the schedule mechanism (``cron``/``every``/``at`` +
    delivery); lifecycle triggers take ``event`` + ``matcher``; data-event triggers take
    ``pattern`` + that pattern's one matcher field (and optionally ``max_fires`` /
    ``debounce_secs``).

    Creating one is a single step with the owner's yes in it: once the body is valid, an action
    that needs a grant and a posture that loosens whether its agent asks are asked about in one
    ``confirmation_required`` before anything is written (:func:`_creation_consent`), and the
    resend with ``confirm: true`` creates it allowed to run (`triggers.grants`). A read-only action
    asks nothing.
    """
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    trigger_type = str(body.get("trigger_type") or "").strip().lower()
    if trigger_type not in (_LIFECYCLE, _SCHEDULE, _EVENT):
        return web.json_response(
            {"error": "trigger_type must be 'schedule', 'lifecycle', or 'event'"}, status=400
        )
    problem = await _action_problem(body.get("action"))
    if problem:
        return json_error("invalid_request", message=problem, status=400)
    if trigger_type == _LIFECYCLE:
        return await _create_lifecycle(state, body, request)
    if trigger_type == _SCHEDULE:
        return await _create_schedule(state, body, request)
    return _create_event(state, body, request)


def _creation_consent(
    request: web.Request, body: dict, *, trigger_type: str
) -> web.Response | None:
    """The one question creating *body*'s trigger asks the owner, or None when it needs no yes.

    Asked by each create path after its own validation and before it writes, so the owner is never
    asked about a trigger the next line would refuse: the grant its action needs
    (`_grant_for_create`) and a loosened posture for its agent, in one ``confirmation_required``.
    The consent names the trigger it is about; a name that is not a string only labels it "new"
    here, and refusing that is the create path's job.
    """
    from personalclaw.safety_flags import confirm_granted

    name = body.get("name")
    label = name if isinstance(name, str) and name else "new"
    grant = _grant_for_create(body, trigger_type=trigger_type)
    field = f"triggers.{label}.capabilities"
    asks: list[tuple[str, str]] = []
    if grant is not None and not confirm_granted(body):
        caller = request.get("user", "dashboard")
        _audit_grant(caller, "denied", f"{field}: creating without confirm")
        asks.append((field, grant[1]))
    loosened = _unconsented_loosening(request, body, where=f"triggers.{label}.action", stored={})
    if loosened is not None:
        asks.append(loosened)
    if not asks:
        return None
    return consent_required(asks[0][0], " ".join(consent for _field, consent in asks))


def _audit_created_grant(request: web.Request, trigger_id: str, granted: Any) -> None:
    """The security-audit row for the grant a create gave with the owner's yes, if it gave one."""
    if isinstance(granted, (list, tuple)) and granted:
        _audit_grant(
            request.get("user", "dashboard"),
            "success",
            f"trigger:{trigger_id}: {', '.join(str(p) for p in granted)}",
        )


def _create_event(state: DashboardState, body: dict, request: web.Request) -> web.Response:
    """Create a data-event trigger (#38): a `kind: "event"` row in the one trigger store.

    🔴 THE STORE IT LANDS IN is the whole fix. This used to write `event_triggers.json`, a second
    store with its own engine: the trigger fired, but no run was ever recorded and nothing else in
    the substrate could see the row. Written through `tools.create` now — the path the chat's
    `automation_create` and the schedule form already take — so the row gets the same validation
    (a spec that could never fire is refused here), the same frozen capability set, and the same
    fire path as every other store trigger.

    The body carries ``pattern`` and that pattern's ONE matcher field (the form sends exactly that);
    the source is DERIVED from the pattern, never taken from the wire — a client-supplied source
    could contradict the pattern and defeat the isolation the source gate exists to enforce. A
    catastrophic ``content_re`` warns rather than refuses (§7/R4 rule d — S128): it runs on the
    memory-write path, so the risk is named where the author will see it.
    """
    from personalclaw.event_triggers import (
        EVENT_PATTERNS,
        PATTERN_MATCHER,
        catastrophic_regex_hint,
        event_spec,
    )
    from personalclaw.schedule import normalize_action
    from personalclaw.triggers import tools as _tools
    from personalclaw.triggers.ownership import owner_username

    name = str(body.get("name") or "").strip()
    if not name:
        return json_error("invalid_request", message="name required", status=400)
    pattern = str(body.get("pattern") or "").strip()
    if pattern not in EVENT_PATTERNS:
        return json_error(
            "invalid_request",
            message=f"pattern must be one of {list(EVENT_PATTERNS)}",
            status=400,
        )
    try:
        action = normalize_action(body.get("action"))
    except ValueError as exc:
        return json_error("invalid_request", message=str(exc), status=400)
    gates: dict[str, Any] = {}
    try:
        if int(body.get("max_fires", 0) or 0) > 0:
            gates["max_fires"] = int(body["max_fires"])
        if "debounce_secs" in body:
            gates["debounce_secs"] = max(0.0, float(body.get("debounce_secs") or 0.0))
    except (TypeError, ValueError):
        return json_error(
            "invalid_request",
            message="max_fires must be an integer and debounce_secs a number",
            status=400,
        )
    field = PATTERN_MATCHER[pattern]
    spec = event_spec(pattern, str(body.get(field) or "").strip() if field else "")
    asked = _creation_consent(request, body, trigger_type=_EVENT)
    if asked is not None:
        return asked

    from personalclaw.safety_flags import confirm_granted

    store = _trigger_store()
    result = _tools.create(
        store,
        name=name,
        kind="event",
        spec=spec,
        gates=gates,
        workflow={"inline": action},
        created_by="user",
        # `_creation_consent` asked the owner first, so `confirm: true` is their yes to it.
        owner_consented=confirm_granted(body),
    )
    if not result.ok:
        return json_error(
            "invalid_request", message=result.text.removeprefix("Error: "), status=400
        )
    made = result.data.get("trigger") or {}
    raw_id = str(made.get("id") or "")
    _audit_created_grant(request, raw_id, (made.get("capabilities") or {}).get("providers"))
    state.push_refresh("crons")
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="trigger.create",
        outcome="success",
        source="dashboard",
        resources=f"trigger:event:{raw_id}:{pattern}",
    )
    row = store.get(raw_id)
    payload: dict[str, Any] = {
        "ok": True,
        "trigger": _serialize_store(row, owner=owner_username()) if row is not None else {},
    }
    hint = catastrophic_regex_hint(spec.get("content_re", ""))
    if hint:
        payload["warning"] = hint
    return web.json_response(payload, status=201)


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
    asked = _creation_consent(request, body, trigger_type=_LIFECYCLE)
    if asked is not None:
        return asked
    from personalclaw.safety_flags import confirm_granted
    from personalclaw.triggers import grants

    store = _hook_store(state)
    hook = store.create(validated)
    # `_creation_consent` asked the owner first, so `confirm: true` is their yes to what it runs.
    granted = grants.give(hook) if confirm_granted(body) else []
    if granted:
        store.update(hook.id, {"capabilities": hook.capabilities})
        _audit_created_grant(request, f"{_LIFECYCLE}:{hook.id}", granted)
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="trigger.create",
        outcome="success",
        source="dashboard",
        resources=f"trigger:lifecycle:{hook.id}:{hook.name}:{hook.event}",
    )
    return web.json_response({"ok": True, "trigger": _serialize_lifecycle(hook, [])})


#: What a failure route may be, said when a caller sends something else.
_FAILURE_ROUTE_RULE = (
    "'failure_delivery' must be 'inbox', 'none', 'channel:<name>', 'channel:<name>:<id>', or '' to "
    "follow the result route"
)


def _channel_problem(channel: str | None) -> str:
    """Why a schedule's ``channel`` can't be delivered to, or ``""``.

    ``channel`` is the chat channel's name, for the owner's DM there, or ``<name>:<target>`` for a
    chat on it: the route without its ``channel:`` prefix, which is how a schedule row shows it.
    The channel checks its own ids (``validate_target``). The one rule core used to apply was a
    single platform's channel-id shape, which refused every other channel's chats.
    """
    if not channel:
        return ""
    from personalclaw.triggers import delivery as _delivery

    return _delivery.channel_route_problem(f"{_delivery.CHANNEL_ROUTE_PREFIX}{channel}")


async def _create_schedule(state: DashboardState, body: dict, request: web.Request) -> web.Response:
    from zoneinfo import available_timezones

    from personalclaw.schedule import normalize_action
    from personalclaw.triggers import delivery as _delivery
    from personalclaw.triggers.models import Trigger

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
    problem = _channel_problem(channel)
    if problem:
        return json_error("invalid_request", message=problem, status=400)
    timezone_val = str(body.get("timezone") or "").strip()
    if timezone_val and timezone_val not in available_timezones():
        return web.json_response(
            {"error": f"invalid timezone: {_redact(timezone_val)!r}"}, status=400
        )

    # 🔴 FAILURE ROUTING at CREATE too (WF2AUT-15). Read on both paths deliberately: issue 272 was a
    # field the create path read and the update path did not, so it could be set once and never
    # changed, and `test_trigger_wire_field_census` now fails on either half being missing.
    # Validated character-for-character the same way in both, because two endpoints that accept one
    # field must accept it identically.
    failure_delivery = body.get("failure_delivery", Trigger.failure_delivery)
    if not _delivery.is_valid_route(failure_delivery):
        return json_error("invalid_request", message=_FAILURE_ROUTE_RULE, status=400)
    problem = _delivery.channel_route_problem(failure_delivery)
    if problem:
        return json_error("invalid_request", message=problem, status=400)
    failure_dedupe = body.get("failure_dedupe", False)
    if not isinstance(failure_dedupe, bool):
        return json_error(
            "invalid_request", message="'failure_dedupe' must be a boolean", status=400
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

    # `enabled` is OPTIONAL and defaults to on — but when it is sent it is honored. It used to be
    # read by nobody on this path, so a caller asking for a trigger created switched off got a live,
    # armed one and no indication otherwise (#587).
    #
    # A non-bool is a 400, not a coercion, and deliberately the same rule
    # `POST /api/triggers/{id}/toggle` already applies: the JSON string "false" is truthy under
    # `bool()`, so coercing here would silently ARM a trigger a caller asked to be created off —
    # inverting the request. Two endpoints that take the same field answer about it the same way.
    #
    # Structured envelope (`json_error`), unlike its flat siblings a few lines up. `AGENTS.md`
    # §"Shared conventions" declares the structured shape as THE wire error, and
    # `test_wire_error_envelope_census` ratchets the flat population down — so a NEW refusal joins
    # the shape the project is converging on rather than the one it is retiring. Converting this
    # function's existing flat errors is a separate change; growing their number is not allowed.
    enabled_raw = body.get("enabled", True)
    if not isinstance(enabled_raw, bool):
        return json_error("invalid_request", message="'enabled' must be a boolean", status=400)
    asked = _creation_consent(request, body, trigger_type=_SCHEDULE)
    if asked is not None:
        return asked

    from personalclaw.safety_flags import confirm_granted

    store = _trigger_store()
    result = _tools.create(
        store,
        name=name,
        kind="clock",
        spec=spec,
        enabled=enabled_raw,
        # `workflow.inline` is the migrated shape, which `schedule_view` and the gateway's shared
        # dispatch both read — so an API-created row and a migrated one are indistinguishable
        # downstream.
        workflow={"inline": action},
        # `channel`/`silent` are DELIVERY on the entity, not action config (LEGACY_FIELD_MAP:
        # `channel → delivery`, `silent → delivery == none`).
        created_by="user",
        # `_creation_consent` asked the owner first, so `confirm: true` is their yes to it.
        owner_consented=confirm_granted(body),
    )
    if not result.ok:
        return web.json_response({"error": result.text}, status=400)

    made = result.data.get("trigger") or {}
    raw_id = str(made.get("id") or "")
    _audit_created_grant(request, raw_id, (made.get("capabilities") or {}).get("providers"))
    row = store.get(raw_id)
    if row is not None:
        trigger = row.trigger
        # 🔴 THE FALSY BRANCH NAMES THE ROUTE (#450). It used to mint `""`, which is not a member of
        # the `delivery` vocabulary, and every reader coerced that blank back to `"none"` — the
        # SILENT value — so "Silent OFF, no channel" created a trigger the user could not un-mute.
        # `"inbox"` is the vocabulary's "deliver normally", and it is what the same field's failure
        # peer already defaults to (`Trigger.failure_delivery`). Written identically in
        # `_update_schedule`: two endpoints that accept one field must encode it the same way.
        trigger.delivery = (
            "none" if body.get("silent") else (f"channel:{channel}" if channel else "inbox")
        )
        # Set here rather than through `tools.create`, for the same reason `delivery` is: the
        # constructor takes the schedule mechanism and the action, and delivery is what the entity
        # calls this pair. `failure_policy` is BUILT, not merged, because the row was created one
        # statement ago and has no other policy key to preserve.
        trigger.failure_delivery = str(failure_delivery or "").strip()
        trigger.failure_policy = {
            **dict(trigger.failure_policy or {}),
            "dedupe_hash": failure_dedupe,
        }
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
    projected = _schedule_row_for(state, row) if row is not None else {}
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
            from personalclaw.triggers import review as _review

            _review.forget(raw, base_dir=store.base_dir)
            _sel().log_api_access(
                caller=request.get("user", "dashboard"),
                operation="trigger.delete",
                outcome="success",
                source="dashboard",
                resources=f"trigger:store:{raw}",
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
        from personalclaw.triggers import review as _review

        _review.forget(raw, base_dir=store.base_dir)
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

    if kind != _LIFECYCLE:
        try:
            body = _keep_masked_schedule(state, kind, raw, body)
        except MaskConflict as exc:
            return web.json_response({"error": str(exc)}, status=409)
    # 🔴 Anything that awaits runs BEFORE the revision check (`trigger_revisions`), never after.
    problem = await _action_problem(body.get("action"), stored=_stored_action(state, kind, raw))
    if problem:
        return json_error("invalid_request", message=problem, status=400)
    stale = trigger_revisions.refusal(
        request, body, schedule=kind == _SCHEDULE, current=lambda: _row_now(state, kind, raw)
    )
    if stale is not None:
        return stale
    # One question for everything this save needs the owner's yes for, so a single "Allow" is never
    # consent to a sentence the dialog did not show: a grant for the action as it is saved — a new
    # provider, or what a granted one runs changed — and a loosened approval posture for its agent.
    from personalclaw.safety_flags import confirm_granted

    caller = request.get("user", "dashboard")
    grant = _grant_for_save(state, body, kind=kind, raw=raw)
    grant_field = f"triggers.{request.match_info['id']}.capabilities"
    asks: list[tuple[str, str]] = []
    if grant is not None and not confirm_granted(body):
        _audit_grant(caller, "denied", f"{grant_field}: saving without confirm")
        asks.append((grant_field, grant[1]))
    loosened = _unconsented_loosening(
        request,
        body,
        where=f"triggers.{request.match_info['id']}.action",
        stored=_stored_action_config(state, kind, raw),
    )
    if loosened is not None:
        asks.append(loosened)
    if asks:
        return consent_required(asks[0][0], " ".join(consent for _field, consent in asks))

    if kind == _LIFECYCLE:
        saved = _update_lifecycle(state, raw, body)
    else:
        saved = _update_schedule(state, raw, body)
    if grant is not None and saved.status == 200:
        # The save carried the owner's yes and the action it was asked about, so the save granted
        # exactly what the question named.
        _audit_grant(caller, "success", f"trigger:{raw}: {', '.join(grant[0])}")
    return saved


def _row_now(state: DashboardState, kind: str, raw: str) -> dict[str, Any] | None:
    """The row a fresh read hands out for the trigger as stored now, or ``None`` when absent."""
    if kind == _SCHEDULE:
        return None if (row := _trigger_store().get(raw)) is None else _schedule_row_for(state, row)
    hook = _hook_store(state).get(raw)
    return None if hook is None else _serialize_lifecycle(hook, _used_by_index().get(raw, []))


def _keep_masked_schedule(state: DashboardState, kind: str, raw: str, body: dict) -> dict:
    """*body* with each hidden value it echoes back restored from the stored schedule.

    The edit form is seeded from :func:`_schedule_row_for`, which masks the name and the prompt,
    and renaming an agent schedule sends both back, so the prompt would be stored as the marker.
    Restored BEFORE the consent and action checks, so they judge what is actually saved.
    """
    out = dict(body)
    if isinstance(out.get("name"), str):
        row = _trigger_store().get(raw)
        if row is not None:
            out["name"] = keep_masked_spans(out["name"], row.trigger.name or "")
    if isinstance(out.get("action"), dict):
        out["action"] = keep_masked_values(out["action"], _stored_action(state, kind, raw))
    return out


def _update_lifecycle(state: DashboardState, raw: str, body: dict) -> web.Response:
    """Save a lifecycle trigger's edit, and settle its grant the way `tools.update` settles a store
    trigger's: what the edit changed keeps no grant (`grants.narrow`), and `api_trigger_detail`
    asked the owner about it first, so `confirm: true` gives it. A save that would leave the trigger
    on without the grant it needs is switched off rather than left running unallowed."""
    import copy

    from personalclaw.safety_flags import confirm_granted
    from personalclaw.triggers import grants
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
    store = _hook_store(state)
    stored = store.get(raw)
    before = copy.deepcopy(stored) if stored is not None else None
    try:
        hook = store.update(raw, validated)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not hook:
        return web.json_response({"error": "not found"}, status=404)
    reshaped = "provider" in validated or "provider_config" in validated
    if reshaped or validated.get("enabled") is True:
        if reshaped:
            grants.narrow(hook, before)
        if grants.missing(hook):
            if confirm_granted(body):
                grants.give(hook)
            else:
                hook.enabled = False
        hook = store.update(raw, {"capabilities": hook.capabilities, "enabled": hook.enabled})
    return web.json_response(
        {"ok": True, "trigger": _serialize_lifecycle(hook, _used_by_index().get(raw, []))}
    )


def _update_schedule(state: DashboardState, raw: str, body: dict) -> web.Response:
    from zoneinfo import available_timezones

    from personalclaw.triggers import delivery as _delivery

    kwargs: dict[str, Any] = {}
    # 🔴 `failure_delivery`/`failure_dedupe` join this allowlist (WF2AUT-15). The delivery contract
    # was fully wired on the fire path — `delivery.route_for` picks the route per outcome,
    # `gateway._dedupe_repeat_failure` gates on `failure_policy.dedupe_hash` — and NEITHER field was
    # readable or writable from any surface. `test_trigger_wire_field_census` is what keeps them
    # readable by BOTH this path and `_create_schedule`, which is the omission issue 272 was.
    for key in (
        "name",
        "channel",
        "silent",
        "strict_schedule",
        "failure_delivery",
        "failure_dedupe",
    ):
        if key in body:
            kwargs[key] = body[key]
    if "failure_delivery" in kwargs:
        if not _delivery.is_valid_route(kwargs["failure_delivery"]):
            return json_error("invalid_request", message=_FAILURE_ROUTE_RULE, status=400)
        problem = _delivery.channel_route_problem(kwargs["failure_delivery"])
        if problem:
            return json_error("invalid_request", message=problem, status=400)
    if "failure_dedupe" in kwargs and not isinstance(kwargs["failure_dedupe"], bool):
        # A 400, not a coercion, and for the reason `enabled` gives on the create path: the JSON
        # string "false" is truthy under `bool()`, so coercing would silently turn dedup ON for a
        # caller asking to turn it off — inverting the request.
        return json_error(
            "invalid_request", message="'failure_dedupe' must be a boolean", status=400
        )
    if "action" in body and isinstance(body["action"], dict):
        kwargs["action"] = body["action"]  # validated + canonicalized in update_job
    if "channel" in kwargs:
        ch = (kwargs["channel"] or "").strip() or None
        kwargs["channel"] = ch
        problem = _channel_problem(ch)
        if problem:
            return json_error("invalid_request", message=problem, status=400)
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
    # `skip_dates` used to be absent from this whole function while `_create_schedule` read it and
    # `_carried` went out of its way to preserve it across a cadence change — so the field could be
    # set at creation and preserved forever, but never CHANGED. Editing a holiday list returned
    # 200 with the old list intact (issue 272). The coercion is character-for-character the create
    # path's, because two endpoints that accept the same field must accept it identically: a
    # non-list is ignored rather than rejected there, so it is ignored here too. (Validating the
    # date STRINGS is issue 270 and belongs to both paths at once, not to this one.)
    if isinstance(body.get("skip_dates"), list):
        kwargs["skip_dates"] = [str(d) for d in body["skip_dates"]]
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

        before = dict(row.trigger.spec or {})
        spec = dict(before)
        if "cron_expr" in kwargs and kwargs["cron_expr"]:
            spec = {"kind": "cron", "expr": str(kwargs["cron_expr"]).strip(), **_carried(spec)}
        elif "every_secs" in kwargs and kwargs["every_secs"]:
            spec = {
                "kind": "interval",
                "interval_secs": int(kwargs["every_secs"]),
                **_carried(spec),
            }
        if "timezone" in kwargs:
            spec["timezone"] = kwargs["timezone"]
        if "strict_schedule" in kwargs:
            spec["strict"] = bool(kwargs["strict_schedule"])
        if "skip_dates" in kwargs:
            spec["skip_dates"] = kwargs["skip_dates"]
        # 🔴 DERIVED FROM THE VALUES, not from which keys the body happened to carry (issue 531).
        # Four separate `cadence_changed = True` lines used to fire on PRESENCE, and the edit form
        # sends `timezone` on every save — so a name-only edit cleared `next_fire_at` and re-armed.
        # Measured live: two consecutive renames of one 3600s trigger, changing nothing but the
        # name, moved its next fire 04:33:08 → 04:34:21, each save re-phasing the interval by the
        # wall time since the last one. A trigger 59 minutes into an hourly cadence lost the hour.
        #
        # Comparing the resulting spec against the one on disk means only a real change re-arms, and
        # it holds for every field at once instead of four hand-set flags. The EXCLUSION is the
        # list, not the inclusion: `strict` is the one spec key that does not move the armed instant
        # (it governs jitter at fire time, matching the old code, which never flagged it), so a NEW
        # spec key defaults to "re-arm" — the safe direction, since a stale armed fire is a wrong
        # fire while a redundant re-arm only re-phases a cadence the user just changed anyway.
        cadence_changed = _cadence_fingerprint(spec) != _cadence_fingerprint(before)

        patch: dict[str, Any] = {"spec": spec}
        if "name" in kwargs:
            patch["name"] = str(kwargs["name"])
        if "action" in kwargs and isinstance(kwargs["action"], dict):
            patch["workflow"] = {"inline": kwargs["action"]}
        if "channel" in kwargs or "silent" in kwargs:
            silent = bool(kwargs.get("silent", row.trigger.delivery == "none"))
            channel_id = kwargs.get("channel", channel_of(row.trigger))
            # 🔴 `"inbox"`, not `""` — the same encoding `_create_schedule` writes (#450). This is
            # where the latch bit hardest: on a `"none"` row, `channel_of` returns `""`
            # (prefix-only), so `PUT {"silent": false}` derived `silent=False, channel_id=""` and
            # landed in this else branch, which minted the blank that read back as `"none"`. Silent
            # OFF therefore never took, and the only escape was to also supply a channel.
            patch["delivery"] = (
                "none" if silent else (f"channel:{channel_id}" if channel_id else "inbox")
            )
        if "failure_delivery" in kwargs:
            patch["failure_delivery"] = str(kwargs["failure_delivery"] or "").strip()
        if "failure_dedupe" in kwargs:
            # 🔴 MERGED, never replaced. `failure_policy` also holds `autopause_after`, the §3.7
            # threshold `autopause.evaluate` reads, and the form owns exactly one of its keys.
            # Sending `{"dedupe_hash": …}` alone would silently reset a user's tuned failure budget
            # to the default — the quietly-losable class `_carried` exists for one field up.
            policy = dict(row.trigger.failure_policy or {})
            policy["dedupe_hash"] = bool(kwargs["failure_dedupe"])
            patch["failure_policy"] = policy

        from personalclaw.safety_flags import confirm_granted

        # `api_trigger_detail` asked for the grant this save needs (`_grant_for_save`), so a body
        # carrying `confirm: true` is the owner's yes, and the save gives it.
        result = _tools.update(
            store, trigger_id=raw, patch=patch, owner_consented=confirm_granted(body)
        )
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
        return web.json_response({"ok": True, "trigger": _schedule_row_for(state, store.get(raw))})

    return web.json_response({"error": "not found"}, status=404)


#: Spec keys that do NOT move a trigger's armed instant, and so must not force a re-arm. `strict`
#: governs jitter at FIRE time (`arm.cadence_next_fire` never reads it when it computes
#: `next_fire_at`), which is why the pre-issue-531 code already left it out of its flags. Everything
#: else — `kind`, `expr`, `interval_secs`, `at`, `timezone`, `skip_dates` and any key added later —
#: changes when the next fire lands, so it belongs on the re-arm side by default.
_NON_CADENCE_SPEC_KEYS: frozenset[str] = frozenset({"strict"})


def _cadence_fingerprint(spec: dict[str, Any]) -> dict[str, Any]:
    """The part of a clock spec that decides WHEN the next fire lands, canonicalized.

    Used to answer "did this edit actually change the cadence?" by comparing before against after,
    rather than by asking which keys the request body happened to carry (issue 531).

    🔴 AN ABSENT KEY AND ITS EMPTY VALUE ARE THE SAME STATE, and collapsing them is the whole reason
    this is a function. The edit form posts `timezone: ""` and `skip_dates: []` on every save, so a
    row stored as `{kind, interval_secs}` comes back as `{kind, interval_secs, timezone: "",
    skip_dates: []}` — different dicts, identical schedules. A raw `!=` would call that a cadence
    change and re-arm on every cosmetic edit, which is the defect. Sound because it matches how the
    ARM path reads them: `arm.cadence_next_fire` resolves the zone through
    `str(spec.get("timezone", "") or "")` and the skip list through `list(spec.get(...) or [])`, so
    missing and empty are indistinguishable there too. `0` is deliberately NOT collapsed — an
    `interval_secs` of 0 is a broken value, not an absent one.
    """
    return {
        key: value
        for key, value in spec.items()
        if key not in _NON_CADENCE_SPEC_KEYS and value is not None and value != "" and value != []
    }


def _carried(spec: dict[str, Any]) -> dict[str, Any]:
    """Spec keys that survive a CADENCE change (S101).

    Replacing `{kind, expr}` wholesale would silently drop `timezone`/`skip_dates`/`strict` — the
    quietly-losable class §1.3 warns about, and the exact fields S91's `verify-migration` exists to
    catch going missing. A user changing `0 9 * * *` to `0 10 * * *` must not lose their holidays.
    """
    return {k: v for k, v in spec.items() if k in ("timezone", "skip_dates", "strict")}


# ── toggle / run / test ──


def _switch_on_grant(
    request: web.Request,
    body: Any,
    trigger: Any,
    *,
    persist: Callable[[], Any],
    broken: bool = False,
) -> web.Response | None:
    """Give a trigger what switching it on needs, asking the owner first. None when it may go on.

    A trigger whose action runs a write-capable provider its frozen block does not permit — a row a
    legacy import brought over (`triggers.legacy_import`), an edit saved without the owner's yes, a
    row the chat made, a lifecycle trigger made before hooks carried a grant — is refused by every
    dispatch (`triggers.grants`). Switching it on is the moment to ask, and so is Allow on a trigger
    that is already on, which the panel sends here as ``enabled: true``: without ``confirm: true``
    this answers ``400 confirmation_required`` in the gateway's own words, which the page's
    ``withSecurityConsent`` turns into the consent dialog; with it, the providers are granted and an
    imported row becomes the owner's (`grants.give`), *persist* stores that, and both the refusal
    and the grant are written to the security audit. An imported nudge needs no grant, only the
    owner's switch, so it is adopted without a question. A *broken* row (parse errors) is left to
    `set_paused`, which refuses it with the reason rather than asking about a trigger that cannot
    run anyway.
    """
    from personalclaw.safety_flags import confirm_granted
    from personalclaw.triggers import grants, legacy_import

    if broken:
        return None
    missing = grants.missing(trigger)
    if not missing and not legacy_import.needs_review(trigger):
        return None
    field = f"triggers.{request.match_info['id']}.capabilities"
    caller = request.get("user", "dashboard")
    if missing and not confirm_granted(body):
        _audit_grant(caller, "denied", f"{field}: switching on without confirm")
        return consent_required(field, grants.consent(trigger, missing))
    granted = grants.give(trigger)
    persist()
    if granted:
        _audit_grant(caller, "success", f"trigger:{trigger.id}: {', '.join(granted)}")
    return None


def _audit_grant(caller: str, outcome: str, resources: str) -> None:
    """The security-audit row for one grant decision: asked and refused, or given."""
    _sel().log_api_access(
        caller=caller,
        operation="trigger.grant",
        outcome=outcome,
        source="dashboard",
        resources=resources,
    )


async def api_trigger_toggle(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/toggle — enable/disable.

    Switching a store-backed trigger ON first gives it what its action needs, with the owner's
    consent (`_switch_on_grant`); switching one off never asks.
    """
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
        body = await json_object_body(request)
        want = body.get("enabled") if isinstance(body, dict) else None
        paused = row.trigger.enabled if want is None else (not bool(want))
        if not paused:
            asked = _switch_on_grant(
                request,
                body,
                row.trigger,
                persist=lambda: store.upsert(row.trigger),
                broken=bool(row.errors),
            )
            if asked is not None:
                return asked
        result = T.set_paused(store, trigger_id=raw, paused=paused)
        if not result.ok:
            return web.json_response({"error": result.text}, status=400)
        return web.json_response({"ok": True, "trigger": _serialize_store(store.get(raw))})
    if kind == _LIFECYCLE:
        # A lifecycle trigger is switched on the way a store trigger is: asked first when its action
        # needs a grant, and Allow is the switch sent on again (`enabled: true` on one that is on).
        hooks = _hook_store(state)
        hook = hooks.get(raw)
        if not hook:
            return web.json_response({"error": "not found"}, status=404)
        body = await json_object_body(request)
        want = body.get("enabled") if isinstance(body, dict) else None
        on = (not hook.enabled) if want is None else bool(want)
        if on:
            asked = _switch_on_grant(
                request,
                body,
                hook,
                persist=lambda: hooks.update(hook.id, {"capabilities": hook.capabilities}),
            )
            if asked is not None:
                return asked
        if hook.enabled != on:
            hook = hooks.toggle(raw)
        return web.json_response(
            {"ok": True, "trigger": _serialize_lifecycle(hook, _used_by_index().get(raw, []))}
        )
    # schedule
    body = await json_object_body(request)
    enabled = body.get("enabled")
    # 🔴 §6's write re-point (S101): the store owns the row. Routed through `tools.set_paused`, which
    # already refuses to enable a row that failed to parse (S87) and reports WHY — so the API and a
    # chat command cannot answer differently about the same trigger.
    store = _trigger_store()
    row = store.get(raw)
    if row is not None:
        from personalclaw.triggers import tools as _tools

        want = (not row.trigger.enabled) if enabled is None else bool(enabled)
        if want:
            asked = _switch_on_grant(
                request,
                body,
                row.trigger,
                persist=lambda: store.upsert(row.trigger),
                broken=bool(row.errors),
            )
            if asked is not None:
                return asked
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

    ``?dry_run=1`` (or JSON ``{"dry_run": true}``) is a **dry run**: nothing executes and
    nothing is recorded — the answer carries the gate plan and ``would_run``, the action a real
    run would dispatch, and that answer is the whole result (see ``_run_store``).

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


#: Inbound answers may carry the user's own data; never cache them (mirrors `inbound.mcp_http`).
_NO_STORE = {"Cache-Control": "no-store"}

#: The client-surface identity for the external webhook fire endpoint. Deliberately a string that is
#: NOT one of the five `EXTERNAL_ACCESS_SURFACES`: `clients.lookup_by_token` gates on
#: `client.may_use(surface)`, so a bearer scoped to `mcp`/`a2a`/`capture`/… can never fire a webhook
#: — the surface-binding isolation the client registry exists to provide. It is not a mountable
#: config surface (this is an always-registered dashboard route), so the surface-mount kill switches
#: do not apply; the global incident switch and the per-client `disabled` flag do.
_WEBHOOK_SURFACE = "webhook"


async def api_trigger_fire(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/fire — fire a `webhook` trigger from an EXTERNAL caller (WF2AUT-12).

    The external twin of `/run`. `/run` is the OWNER's dashboard-authenticated "fire now" button;
    `/fire` admits an OUTSIDE caller that presents a per-client **scoped** bearer token, fences the
    inbound body as untrusted data, and dispatches the trigger's action fire-and-forget.

    The gate, in order — the inbound-surface discipline `inbound/mcp_http.py` follows:

    1. **incident kill switch** (`gate.incident_problem` → 503): an active incident suspends all
       unattended inbound. The surface-mount switches (master/per-surface) do NOT apply: this is an
       always-registered dashboard route, not one of the five `EXTERNAL_ACCESS_SURFACES`, and the
       per-integration on/off switch is the scoped client's own `disabled` flag (enforced inside
       `lookup_by_token`) plus revocation.
    2. **token → client** (`clients.lookup_by_token` → 401): verifies the bearer against the
       SHA-256-hash registry, honouring the client's `disabled` flag and its `"webhook"` surface
       binding. There is deliberately NO surface-token fallback (unlike `/mcp`): the Done-when
       requires a *scoped* token, which an un-scoped operator token is not.
    3. **scope pin** (→ 403 + SEL): the client must be pinned to THIS trigger
       (`scope.trigger == <id>`). `check_bindings` refuses a DISAGREEING pin; the explicit equality
       below also refuses an ABSENT pin, so a scope-less client cannot fire an arbitrary webhook
       (fail-closed). A violation is a security event — logged and audited, never a silent
       substitution.
    4. **rate cap** (→ 429): per client, so one noisy integration cannot starve another.
    5. **resolve** (→ 404): only a `webhook`-kind store trigger that is switched on is fireable
       here; an unknown id, a non-webhook kind or a paused trigger answers 404 rather than
       confirming a non-webhook trigger's existence or saying why it will not fire — the answer the
       inbound gate gives a surface that is switched off. Done AFTER auth+scope, so a misscoped
       caller learns nothing about which triggers exist. Then the trigger's own grant (→ 403).
    6. **fence + fire**: the raw body is capped and fenced (`framing.fence_payload`) so it reaches
       the agent as data and never instructions, then the action is dispatched fire-and-forget (202)
       — a webhook sender must not block on an LLM turn (the `view`-render idiom).

    Network reachability (loopback vs remote) is governed by the dashboard server's own binding and
    by the deferred owner E4 remote-exposure decision, not by this handler; the scoped bearer is the
    admission gate wherever the route is reachable.
    """
    from personalclaw.inbound import audit as audit_mod
    from personalclaw.inbound import caps as caps_mod
    from personalclaw.inbound import clients as clients_mod
    from personalclaw.inbound import framing
    from personalclaw.inbound.gate import incident_problem

    trigger_id = request.match_info["id"]
    route = "POST /api/triggers/{id}/fire"

    # 1) Incident kill switch — the global unattended-inbound suspension.
    incident = incident_problem()
    if incident:
        audit_mod.audit(_WEBHOOK_SURFACE, route=route, status=503, refused=incident)
        return json_error("service_unavailable", status=503, headers=_NO_STORE)

    # 2) Token → client. Scoped per-client bearer only; no surface-token fallback.
    presented = ""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        presented = header[len("Bearer ") :].strip()
    client, reason = clients_mod.lookup_by_token(presented, _WEBHOOK_SURFACE)
    if client is None:
        audit_mod.audit(
            _WEBHOOK_SURFACE,
            route=route,
            status=401,
            refused=reason or "bad or missing bearer token",
        )
        return json_error("unauthorized", status=401, headers=_NO_STORE)
    client_id = client.client_id

    # 3) Scope pin — the client must be pinned to THIS trigger. `check_bindings` refuses a
    #    disagreeing pin; the explicit equality refuses an absent one (fail-closed).
    violation = clients_mod.check_bindings(client, {"scope": {"trigger": trigger_id}})
    if not violation and str(client.scope.get("trigger", "")) != trigger_id:
        violation = (
            f"client {client_id} is not scoped to trigger {trigger_id!r} "
            f"(scope.trigger={str(client.scope.get('trigger', ''))!r})"
        )
    if violation:
        clients_mod.log_binding_violation(client_id, violation)
        audit_mod.audit(
            _WEBHOOK_SURFACE, route=route, status=403, refused=violation, client_id=client_id
        )
        return json_error(
            "forbidden",
            status=403,
            headers=_NO_STORE,
            error_extra={"detail": "request conflicts with a client binding"},
        )

    # 4) Rate cap, per client.
    caps = caps_mod.caps_for(client)
    peer_fallback = request.headers.get("Host", "") + "|" + (request.remote or "")
    if not caps_mod.check_rate_for_client(_WEBHOOK_SURFACE, client_id, peer_fallback, caps):
        audit_mod.audit(
            _WEBHOOK_SURFACE,
            route=route,
            status=429,
            refused="rate limit",
            client_id=client_id,
            rate_limited=True,
        )
        return json_error(
            "rate_limited",
            status=429,
            headers={
                **_NO_STORE,
                "Retry-After": str(
                    caps_mod.retry_after_for_client(
                        _WEBHOOK_SURFACE, client_id, peer_fallback, caps
                    )
                ),
            },
        )
    clients_mod.touch_last_seen(client_id)

    # 5) Resolve the trigger. Only a `webhook`-kind store trigger that is switched on is fireable
    #    here. A paused one answers exactly as an unknown one does: 404 is what the inbound gate
    #    answers for a surface that is switched off (`inbound.gate.admission_problem`), so the
    #    caller learns no more than that there is nothing to fire. The audit row says which.
    kind, raw = _split_id(trigger_id)
    store = _trigger_store()
    row = store.get(raw) if kind == _STORE else None
    if row is None or row.trigger.kind != "webhook" or not row.trigger.fires_automatically:
        audit_mod.audit(
            _WEBHOOK_SURFACE,
            route=route,
            status=404,
            refused=(
                "unknown or non-webhook trigger"
                if row is None or row.trigger.kind != "webhook"
                else "the trigger is switched off or paused"
            ),
            client_id=client_id,
        )
        return json_error("not_found", status=404, headers=_NO_STORE)

    # 5b) The trigger's own grant (`triggers.grants`): a scoped token lets a caller fire THIS
    #     trigger, and says nothing about what its action may run. Refused here rather than after a
    #     202, so the caller learns the fire did not happen; the action is not named to an outside
    #     caller, and the owner sees the grant on the Triggers page.
    from personalclaw.triggers import grants

    if grants.missing(row.trigger):
        audit_mod.audit(
            _WEBHOOK_SURFACE,
            route=route,
            status=403,
            refused="the trigger's action is not allowed to run",
            client_id=client_id,
        )
        return json_error(
            "forbidden",
            message="This automation is not allowed to run its action until its owner allows it.",
            status=403,
            headers=_NO_STORE,
        )

    # 6) Fence the untrusted body, then fire the trigger's action fire-and-forget.
    declared = request.content_length or 0
    if declared > caps.body_bytes:
        audit_mod.audit(
            _WEBHOOK_SURFACE,
            route=route,
            status=413,
            refused="body cap (declared)",
            client_id=client_id,
        )
        return json_error("request_too_large", status=413, headers=_NO_STORE)
    body_bytes = await request.content.read(caps.body_bytes + 1)
    if len(body_bytes) > caps.body_bytes:
        audit_mod.audit(
            _WEBHOOK_SURFACE, route=route, status=413, refused="body cap", client_id=client_id
        )
        return json_error("request_too_large", status=413, headers=_NO_STORE)

    fenced = framing.fence_payload(
        body_bytes.decode("utf-8", errors="replace"),
        surface=_WEBHOOK_SURFACE,
        client_id=client_id,
        detail=raw,
        caps=caps,
    )
    payload = {"trigger_id": raw, "body": fenced, "source": "webhook.fire"}

    # Fire-and-forget: a webhook sender must not block on an LLM turn. Tracked on
    # `state._background_tasks` so the task is not garbage-collected mid-run — the idiom
    # `api_trigger_view_render` and the webhook-agent handler already follow.
    state: DashboardState = request.app["state"]
    task = asyncio.create_task(_dispatch_store_action(row.trigger, payload, event="webhook.fire"))
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)

    audit_mod.audit(
        _WEBHOOK_SURFACE, route=route, status=202, bytes_in=len(body_bytes), client_id=client_id
    )
    return web.json_response(
        {"ok": True, "accepted": True, "trigger": row.trigger.id}, status=202, headers=_NO_STORE
    )


async def _run_store(raw: str, request: web.Request) -> web.Response:
    """Fire one store-backed trigger (file/web_watch/idle/…) by hand.

    A `dry_run` reports S92's gate plan (which gates a manual fire enforces vs bypasses) without
    executing — that reuses `tools.run`, so the API and the chat tool answer identically. A real
    run dispatches the trigger's declared action through the SAME action-provider registry the
    live file-watch path (`_fire_file_trigger`) uses, so a Run button and an autonomous fire
    execute the same action the same way.

    Manual runs bypass quiet-hours + duty limits but never the injection screen, capability
    allowlist, or budget — the boundary `tools.MANUAL_NEVER_BYPASSES` pins. The capability
    allowlist is enforced here and in `_dispatch_store_action`, which is what makes that true.
    """
    from personalclaw.triggers import tools as T

    store = _trigger_store()
    row = store.get(raw)
    if row is None:
        return web.json_response({"error": "not found"}, status=404)

    dry_run = request.query.get("dry_run", "") in ("1", "true", "yes")
    if not dry_run:
        body = await json_object_body(request)
        dry_run = bool(body.get("dry_run", False))

    if dry_run:
        # Reuse tools.run for the gate plan — the API and the chat tool report identically.
        result = T.run(store, trigger_id=raw, dry_run=True)
        # 🔴 THE RESPONSE IS THE RESULT. A dry run executes nothing, records no run and moves no
        # `last_run_ts`, so this answer is the only place its outcome will ever exist. The Run
        # button used to treat it as a started run and wait for a history row that never came —
        # "Running…" for as long as the panel stayed open. `would_run` is the resolved action in
        # the one canonical `{provider, config}` shape, so a surface can say what a real run would
        # do without re-deriving the two stored action shapes itself.
        from personalclaw.triggers.schedule_view import _inline_action

        return web.json_response(
            {
                "ok": result.ok,
                "name": row.trigger.name,
                "result": result.data,
                "text": result.text,
                "would_run": _inline_action(row.trigger),
            }
        )

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
    # 🔴 THE GRANT, for every trigger (`triggers.grants`). This route is not only the owner's Run
    # button: the chat's `automation_run` and `schedule_trigger` post here too. Measured on `main`:
    # an enabled `bash` schedule with an empty capability block ran its command from here. The
    # dispatch refuses the same row, so no caller can forget; asked here as well so the answer is
    # the refusal, in words that say which grant and how the owner gives it.
    from personalclaw.triggers import grants

    missing = grants.missing(row.trigger)
    if missing:
        return web.json_response(
            {
                "ok": False,
                "name": row.trigger.name,
                "refused": grants.refusal(row.trigger, missing),
            }
        )
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


async def api_trigger_answer(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/answer — answer the question a trigger's action stopped on.

    Body ``{resume_token, answer}``, ``answer`` a boolean. The token is the park's
    (`triggers.parks`), single-use: a double click runs nothing twice. Approve runs the trigger's
    action once more, now, through the Run button's own dispatch — the same grants, the same
    capability fence — with the answer on that dispatch (`ActionContext.answer`), so the browse
    action you confirmed a sign-in for goes on to the run. Deny closes the question; the trigger
    asks again the next time its action stops. The refusals a Run button honours are read BEFORE
    the token is spent, so a refused answer leaves the question answerable.
    """
    from personalclaw.triggers import parks
    from personalclaw.triggers import tools as T

    _kind, raw = _split_id(request.match_info["id"])
    body = await json_object_body(request)
    answer = body.get("answer")
    if not isinstance(answer, bool):
        return json_error("invalid_request", message="'answer' must be true or false", status=400)
    token = str(body.get("resume_token", "") or "")
    row = _trigger_store().get(raw)
    if row is None:
        # Deleted since it asked: there is nothing left to run, so the question goes too.
        parks.withdraw(raw, state=request.app["state"])
        return json_error(
            "not_found",
            message="This trigger no longer exists, so there is nothing to run.",
            status=404,
        )
    if answer:
        refusal = T.manual_refusal()
        if refusal:
            return web.json_response({"ok": False, "name": row.trigger.name, "refused": refusal})
    park = parks.claim(raw, token)
    if park is None:
        return json_error(
            "trigger_park_gone",
            message=(
                "This question was already answered, or the trigger no longer waits on it — "
                "run it again to be asked afresh."
            ),
            status=409,
        )
    parks.close_row(request.app["state"], raw)
    if not answer:
        return web.json_response(
            {"ok": True, "approved": False, "name": row.trigger.name, "result": "declined"}
        )
    ran, note = await _dispatch_store_action(
        row.trigger, {"trigger_id": raw, "manual": True}, event="manual.answer", answer=True
    )
    return web.json_response(
        {
            "ok": ran,
            "approved": True,
            "name": row.trigger.name,
            "result": note,
            # The run it started stopped for you again (a sign-in page mid-run): a NEW question,
            # with its own row — the answer's surface says so rather than "it ran".
            "waiting": parks.load(raw) is not None,
        }
    )


async def _dispatch_store_action(
    trigger: Any,
    payload: dict[str, Any],
    *,
    event: str = "manual.run",
    late: str = "",
    answer: Any = None,
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

    `late` is the review's reason when this run stands in for a slot that did not run (a missed
    fire, or a run a restart interrupted): the recorded row then says the run was late, and why.

    🔴 NOTHING RUNS WITHOUT ITS GRANT. Every attended run reaches its action here — Run now, the
    restart review's Run now, a view refresh, a webhook fire — and none of them walks
    `service.admit_fire`, where the fence lives for a clock fire. So the grant is checked HERE, the
    one place they share, and a refusal is `(False, <what is missing and how to allow it>)`.
    Measured on `main`: every one of those four ran an ungranted `bash` action.
    """
    import time

    from personalclaw.action_providers import ActionContext, get_action_provider
    from personalclaw.action_providers.registry import _ensure_default_providers_registered
    from personalclaw.triggers import grants

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
    missing = grants.missing(trigger)
    if missing:
        refusal = grants.refusal(trigger, missing)
        logger.info("trigger %s not run (%s): %s", getattr(trigger, "id", ""), event, refusal)
        return False, refusal
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
    from personalclaw.triggers.delivery import status_url

    # The same `status_url` the autonomous path hands the provider, so a hand-run notify links back
    # to its trigger exactly as a scheduled one does.
    ctx = ActionContext(
        event=event,
        context="",
        payload=payload,
        status_url=status_url(trigger_id=str(getattr(trigger, "id", "") or "")),
        trigger_id=str(getattr(trigger, "id", "") or ""),
        # A person's answer to this trigger's park (`api_trigger_answer`), on the one dispatch it
        # starts: the browse action you confirmed a sign-in for goes on to the run.
        answer=answer,
    )
    from personalclaw.triggers.firepath import action_timeout

    started = time.time()
    try:
        # The same floor a scheduled fire gets (`firepath.action_timeout`): this passed none, so a
        # `bash` Run now was cut off at 30s where its scheduled fire had 300s.
        result = await provider.execute(
            action.get("config") or {}, ctx, timeout=action_timeout(provider_name)
        )
    except Exception as exc:  # noqa: BLE001 - a failed manual run is RECORDED, not raised (#308)
        await _record_manual_run(trigger, started=started, exc=exc)
        return False, f"failed: {type(exc).__name__}: {exc}"
    await _record_manual_run(trigger, started=started, result=result, late=late)
    if result is not None and not bool(getattr(result, "success", True)):
        note = str(getattr(result, "error", "") or "") or "the action reported failure"
        return False, f"failed: {note}"
    return True, "ran"


async def _record_manual_run(
    trigger: Any,
    *,
    started: float,
    result: Any = None,
    exc: BaseException | None = None,
    late: str = "",
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
    `service.admit_fire`), and `tools.MANUAL_NEVER_BYPASSES` pins `budget` among the gates a manual
    fire never spends — the same reason `count_since` excludes manual rows. Spending the budget
    from a Run button would let a user lock themselves out of their own automation by testing it.
    Likewise a manual run must not drive `state`/`health`/`enabled`: a
    hand-run of a healthy trigger that fails once is not the machine deciding to autopause itself.

    Never raises: a bookkeeping failure must not turn a completed manual run into a crashed request,
    the same contract `_record_fire_outcome` holds. Losing a run record is recoverable; losing the
    response is not.
    """
    try:
        import time
        from datetime import datetime, timezone

        from personalclaw.schedule_history import ScheduleRun, status_for_result
        from personalclaw.triggers import parks

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
        else:
            # What the action reported, the same answer `_record_fire_outcome` records for a fire
            # (`status_for_result`): T7's `launched` when it only STARTED background work whose
            # real outcome is its OWN run's, `queued` (WV-14) when it is held behind a run in
            # flight, and the inert `skipped_noop` when it had nothing to do.
            status = status_for_result(result)
            # A run standing in for a slot that did not run (the review's Run now) finished late,
            # and the row says so: `missed.resolve_missed` names the outcome and the reason.
            if late and status == "success":
                status = "ran_late"
            error = ""
            summary = str(getattr(result, "stdout", "") or "") if result is not None else ""
            if status == "waiting":
                # A park's row says it waits on you and on what, not the payload it parked with.
                summary = parks.waiting_line(result)
        if late and status != "failure":
            summary = f"{late[:1].upper()}{late[1:]}." + (f" {summary}" if summary else "")

        run_id = f"manual-{int(finished * 1000)}"
        # The same store the autonomous recorder appends to; `append_sync` credential-redacts
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
        # A park asks you, once, with the action's own card; a run that went through withdraws the
        # question an earlier one asked (ledger 248).
        parks.settle(trigger, result)

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
    body = await json_object_body(request)
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


async def api_trigger_test(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/test — execute a lifecycle trigger's action once.

    A store trigger — a data event included — is run by hand through `/run` (and previewed with
    `/run?dry_run=1`), the same manual path every store kind takes.
    """
    from personalclaw.hooks import run_script_hook
    from personalclaw.validation import sanitize_string

    state: DashboardState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])
    if kind != _LIFECYCLE:
        # Worded for every kind that reaches it: this answered "schedule triggers run their
        # action" to a data-event trigger too, once event rows moved into the store.
        return web.json_response(
            {
                "error": "only a lifecycle trigger has a test run; this trigger's action is its "
                "run — use /run?dry_run=1 to preview it"
            },
            status=400,
        )
    hook = _hook_store(state).get(raw)
    if not hook:
        return web.json_response({"error": "not found"}, status=404)
    body = await json_object_body(request)
    context = sanitize_string(body.get("context", "test"))[:10000]
    # A rehearsal, not a fire (#609): gates all hold and the action really executes,
    # but the payload is tagged and the hook's real run_count/last_run/last_status
    # stay untouched — mirroring the event-trigger /test path.
    result = await run_script_hook(hook, context, test=True)
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
    if kind == _LIFECYCLE:
        # Resolve the hook first: `supported: false` used to be returned for ANY lifecycle id,
        # including one that is not a hook at all (#2940). "This kind keeps no run store" and
        # "there is no such trigger" are different answers.
        if _hook_store(request.app["state"]).get(raw) is None:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(
            {
                "runs": [],
                "total": 0,
                "supported": False,
                "reason": "lifecycle triggers run inline with the agent loop and keep no run store",
            }
        )
    # Resolve the PARENT trigger before reading its runs (#2940). `ScheduleRunStore` is keyed by a
    # plain job id and answers `{"runs": [], "total": 0}` for ANY id, so a mistyped or deleted
    # trigger read as one that exists and has never run — while `DELETE /api/triggers/{id}` on the
    # same id 404s off exactly this lookup. Covers the remaining two kinds: `schedule`, which a
    # bare id defaults to, and `store`, which shares the row store and falls through to here.
    #
    # Placed BEFORE the limit/offset parse to match the EVENT branch above, which resolves its
    # trigger without parsing either: the id addresses the resource, so a bogus trigger is a 404
    # whatever the paging says.
    if _trigger_store().get(raw) is None:
        return web.json_response({"error": "not found"}, status=404)
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
    # A lifecycle trigger still 404s, and correctly: it has no run store to open a record from, and
    # 404 is the honest answer for a record that does not exist.
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
    from datetime import datetime, timedelta, timezone

    from personalclaw.schedule import get_local_tz

    state: DashboardState = request.app["state"]
    tz_name, server_zone = get_local_tz()

    def normalized_bound(value: datetime) -> datetime:
        """Resolve offset-free API values in ``server_tz`` and compare/project in UTC."""
        if value.tzinfo is None:
            value = value.replace(tzinfo=server_zone)
        return value.astimezone(timezone.utc)

    raw_start = (request.query.get("start") or "").strip()
    try:
        parsed_start = datetime.fromisoformat(raw_start) if raw_start else datetime.now(server_zone)
        start = normalized_bound(parsed_start)
    except ValueError:
        return web.json_response({"error": "start must be an ISO date"}, status=400)
    try:
        days = max(1, min(int(request.query.get("days", "7")), 31))
    except ValueError:
        return web.json_response({"error": "days must be an integer"}, status=400)
    # Optional exact window end (issue 608): the grid's 7 LOCAL days are 167h/169h across a
    # DST transition while `days` arithmetic is fixed wall-clock fields — the client that
    # draws the columns is the only party that knows their true end, so it may name it.
    # Bounded to the same 31-day cap as `days` so the parameter cannot widen the projection.
    until = None
    raw_until = (request.query.get("until") or "").strip()
    if raw_until:
        try:
            until = normalized_bound(datetime.fromisoformat(raw_until))
        except ValueError:
            return web.json_response({"error": "until must be an ISO date"}, status=400)
        if until <= start or until > start + timedelta(days=31):
            return web.json_response(
                {"error": "until must be after start and within 31 days"}, status=400
            )

    occurrences: list[dict[str, Any]] = []
    truncated: list[str] = []
    for trigger in _week_triggers(state):
        rows, cut = _project_one(trigger, start=start, days=days, until=until)
        occurrences.extend(row.to_dict() for row in rows)
        if cut:
            truncated.append(f"{_SCHEDULE}:{trigger.id}")

    return web.json_response(
        {
            "start": start.isoformat(),
            "end": (until or (start + timedelta(days=days))).isoformat(),
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
    loaded_rows = store.load()
    store_rows = [row for row in loaded_rows if row.trigger.kind == "clock"]
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
    # Data-event rows, diagnosed as the store rows they are: their action, gates, spec and frozen
    # capabilities go through every check a clock row does (orphaned workflow, unknown provider,
    # unfenced write action, quiet windows, an `agent_scope` no fire path enforces). Their memory
    # key glob is deliberately NOT fed to the broad-glob check, which the legacy projection did:
    # that check is a FILE-watch finding ("matches nearly every file"), and a key glob of `*` is
    # simply MemoryUpdate — not a path, and not a problem.
    for row in loaded_rows:
        if row.trigger.kind != "event":
            continue
        rows.append(
            {
                "id": f"{_STORE}:{row.trigger.id}",
                "gates": row.trigger.gates or {},
                "workflow": row.trigger.workflow or {},
                "spec": dict(row.trigger.spec or {}),
                "capabilities": dict(row.trigger.capabilities or {}),
            }
        )

    # 🔴 #779: the set that makes `unknown_action_provider` real. Injected rather than read
    # inside `diagnose`, matching `known_workflows` above, so the function stays pure — and
    # `None` would suppress the check, so a failed read must not silently become "every
    # provider is fine". `dispatchable_action_providers` ensures the built-ins are registered
    # first; skipping that on this read-only surface would report EVERY automation as unknown.
    from personalclaw.action_providers.registry import dispatchable_action_providers

    report = diagnose(
        rows,
        known_workflows=known_workflows,
        known_action_providers=dispatchable_action_providers(),
    )
    # Semantic spec findings (#560/#612): the structural doctor above cannot see an
    # invalid-but-present cron or an inert skip date — `semantic_spec_issues` lives beside
    # the fire path and mirrors its exact matching rules, so "the doctor says healthy"
    # and "it never fires / never skips" can no longer both be true.
    from personalclaw.triggers.arm import semantic_spec_issues
    from personalclaw.triggers.calendar import Finding

    for row in store_rows:
        for issue in semantic_spec_issues(row.trigger.kind, row.trigger.spec, row.trigger.workflow):
            is_error = issue.severity == "error"
            report.findings.append(
                Finding(
                    trigger_id=f"{_SCHEDULE}:{row.trigger.id}",
                    code="unfireable_spec" if is_error else "inert_spec_entry",
                    detail=f"{issue.path}: {issue.message}",
                    fix=(
                        "correct the expression or date — as authored, this part of the "
                        "trigger cannot do what it says"
                        if is_error
                        else "confirm this is intended, or adjust the schedule/skip date"
                    ),
                )
            )
    # 🔴 THE LOAD-TIME ISSUES, which this doctor could not see either (issue 531). `diagnose` reads
    # projected dicts and `semantic_spec_issues` owns the fire-path semantics; NEITHER re-runs
    # `models.validate_spec`, so the whole structural half — every unknown spec key, every missing
    # required field, and the `MIN_CLOCK_INTERVAL_SECS` floor warning — stopped at the store. The
    # doctor answered `healthy: true, findings: []` on a home holding a 60-second LLM-invoking
    # trigger whose own `row.warnings` named the problem.
    #
    # Folded from `row.issues` rather than by re-deriving the checks here: a second copy of the
    # floor rule is a second owner, and the two would drift the first time the number moved. Every
    # kind, not just `clock` — the store-only kinds validate through the same function, and a doctor
    # that covered one kind's parse issues would be a doctor whose silence means nothing.
    for loaded in loaded_rows:
        # The same namespace the LIST route gives this row, so a UI can join a finding back onto the
        # trigger it is about: `_gather` lists clock rows under `schedule:` and every other store
        # kind under `store:`.
        ns = _SCHEDULE if loaded.trigger.kind == "clock" else _STORE
        for issue in loaded.issues:
            is_error = issue.severity == "error"
            report.findings.append(
                Finding(
                    trigger_id=f"{ns}:{loaded.trigger.id}",
                    code="invalid_spec" if is_error else "spec_warning",
                    detail=f"{issue.path}: {issue.message}",
                    fix=(
                        "correct the field named above — the store kept this row but refuses to "
                        "arm it, so the automation exists and cannot fire"
                        if is_error
                        else "confirm this is intended — the row runs as authored, this is an "
                        "advisory the store recorded and no surface used to show"
                    ),
                )
            )
    return web.json_response(report.to_dict())


async def api_trigger_history_all(request: web.Request) -> web.Response:
    """GET /api/triggers/history — the run feed across every kind (AUTO crit 4).

    Criterion 4: "a hook, an event trigger, and a cron all show run history in the same
    feed with the same record shape and typed outcomes". This route existed and was
    **schedule-only** — its own docstring said "(schedule runs)" — so the feed a user opens
    to answer "what did my machine do" showed one kind of automation and silently omitted
    the others. Every trigger-store row — a cron, an event trigger, a file watch — writes its runs
    to the one run ledger; hooks, which keep none, are projected.

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

    # Hooks contribute only when the caller has not filtered to a specific trigger of another
    # kind — a `?trigger_id=schedule:x` request asking for one cron must not gain rows for every
    # hook on the machine.
    hooks: list[Any] = []
    if not raw_filter or kind_filter == _LIFECYCLE:
        try:
            store = _hook_store(state)
            # `list_all()`, not `list_hooks()` — checked against the class. A wrong name here would
            # have been caught by nothing: the `except` below swallows the AttributeError and the
            # feed would quietly contain zero hooks — the defect this session exists to fix.
            hooks = [h for h in store.list_all() if not raw_filter or h.id == raw_filter]
        except Exception:
            logger.debug("unified history: hook store unavailable", exc_info=True)

    # A `store:` filter reads the same ledger a `schedule:` one does: every trigger-store row, clock
    # or not, writes its runs there, so filtering to one of them must keep its rows.
    records = H.unified_feed(
        schedule_runs=enriched if (not raw_filter or kind_filter in (_SCHEDULE, _STORE)) else [],
        hooks=hooks,
        limit=limit,
    )
    payload = H.feed_response(records)
    # `total` stays the LEDGER total: it is the only source with a real paginated store, so a sum
    # mixing it with projected hook rows would make the pager overshoot. The projected rows are
    # counted separately in the response.
    payload["schedule_total"] = total
    payload["outcomes"] = H.outcome_counts(records)
    return web.json_response(payload)


async def api_trigger_review(request: web.Request) -> web.Response:
    """GET / POST /api/triggers/review — what a restart left for you to decide (§3.4).

    GET lists the cards `triggers/review.py` keeps: each automation's missed runs, and each run a
    restart interrupted. POST ``{trigger_id, kind, action}`` decides one: ``run_now`` runs the
    trigger's action now through the same dispatch as its Run button and records the run as late;
    ``dismiss`` records ``skipped_missed``. Both go through `missed.resolve_missed`, which names the
    outcome and the reason, so the decision is a row in the trigger's history either way.
    """
    from personalclaw.triggers import review as _review
    from personalclaw.triggers import service as _service
    from personalclaw.triggers import tools as T
    from personalclaw.triggers.missed import resolve_missed

    state: DashboardState = request.app["state"]
    store = _trigger_store()
    if request.method == "GET":
        cards = []
        for card in _review.pending(base_dir=store.base_dir):
            row = store.get(card.trigger_id)
            if row is None:
                # The automation was deleted after the boot that found this; nothing to decide.
                _review.forget(card.trigger_id, base_dir=store.base_dir)
                continue
            prefix = _SCHEDULE if row.trigger.kind == "clock" else _STORE
            cards.append(
                {
                    **card.to_dict(),
                    "name": _redact(row.trigger.name or card.trigger_id),
                    "open_id": f"{prefix}:{card.trigger_id}",
                }
            )
        return web.json_response({"cards": cards})

    body = await json_object_body(request)
    trigger_id = str(body.get("trigger_id") or "").strip()
    kind = str(body.get("kind") or "").strip()
    action = str(body.get("action") or "").strip()
    if not trigger_id or kind not in _review.KINDS or action not in ("run_now", "dismiss"):
        return json_error(
            "invalid_request",
            message=(
                "send {trigger_id, kind, action}: kind is 'missed' or 'interrupted', and action is "
                "'run_now' or 'dismiss'"
            ),
            status=400,
        )
    row = store.get(trigger_id)
    if row is None:
        _review.forget(trigger_id, base_dir=store.base_dir)
        return json_error("not_found", message=f"no automation {trigger_id!r}", status=404)
    if action == "run_now":
        # The kill switch holds a Run now exactly as it holds the Run button — and before the card
        # is taken, so a refused run leaves the decision still waiting for you.
        refusal = T.manual_refusal()
        if refusal:
            return web.json_response({"ok": False, "refused": refusal})
        # And so does a missing grant (`triggers.grants`), for the same reason: the card waits.
        from personalclaw.triggers import grants

        missing = grants.missing(row.trigger)
        if missing:
            return web.json_response({"ok": False, "refused": grants.refusal(row.trigger, missing)})
        from personalclaw.triggers import claims as _claims

        # And so does a run already in flight, the Run button's 409: a second run beside it is the
        # overlap the trigger's own policy refuses.
        if _claims.is_running(trigger_id, base_dir=store.base_dir):
            return web.json_response(
                {"ok": False, "refused": "it is running now; decide once this run finishes"}
            )
    taken = _review.take(trigger_id, kind, base_dir=store.base_dir)
    if taken is None:
        return json_error(
            "not_found",
            message=f"nothing is waiting for a decision on {trigger_id!r} ({kind})",
            status=404,
        )
    outcome, reason = resolve_missed(action, kind=kind)
    if action == "dismiss":
        await _service.record_dismissal(trigger_id, outcome, reason, base_dir=store.base_dir)
        state.push_refresh("crons")
        return web.json_response({"ok": True, "outcome": outcome, "reason": reason})
    ran, note = await _dispatch_store_action(
        row.trigger,
        {"trigger_id": trigger_id, "manual": True, "review": kind, "scheduled_for": taken.latest},
        event="review.run_now",
        late=reason,
    )
    state.push_refresh("crons")
    return web.json_response(
        {"ok": ran, "outcome": outcome if ran else "failed", "reason": reason, "result": note}
    )


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
    # The restart review (§3.4). Literal path, registered BEFORE `/{id}` for the same reason.
    app.router.add_get("/api/triggers/review", api_trigger_review)
    app.router.add_post("/api/triggers/review", api_trigger_review)
    app.router.add_put("/api/triggers/{id}", api_trigger_detail)
    app.router.add_delete("/api/triggers/{id}", api_trigger_detail)
    app.router.add_post("/api/triggers/{id}/toggle", api_trigger_toggle)
    app.router.add_post("/api/triggers/{id}/run", api_trigger_run)
    app.router.add_post("/api/triggers/{id}/answer", api_trigger_answer)
    # The external webhook fire endpoint (WF2AUT-12). Beside `/run`, same `{id}` shape, so it needs
    # no special ordering relative to the literal `/week`/`/doctor`/`/view/render` segments above.
    app.router.add_post("/api/triggers/{id}/fire", api_trigger_fire)
    app.router.add_post("/api/triggers/{id}/test", api_trigger_test)
    app.router.add_post("/api/triggers/{id}/to-chat", api_trigger_to_chat)
    app.router.add_get("/api/triggers/{id}/history", api_trigger_history)
    app.router.add_get("/api/triggers/{id}/history/{run_id}", api_trigger_history_detail)
