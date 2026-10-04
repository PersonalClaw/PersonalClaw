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

import logging
from collections.abc import Callable
from typing import Any

from aiohttp import web

from personalclaw.config import loader as config_loader
from personalclaw.config.edit_spec import LOOSEN_TITLE, LooseningAsk
from personalclaw.dashboard.handlers import trigger_callbacks, trigger_revisions, trigger_runs
from personalclaw.dashboard.state import DashboardState
from personalclaw.http_errors import consent_required, json_error
from personalclaw.request_validation import MISSING, bool_field, json_object_body, optional_bool
from personalclaw.security import (
    MaskConflict,
    keep_masked_spans,
    keep_masked_values,
    redact_for_display,
    redact_values_for_display,
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
#: The `trigger_type` the create form sends for a trigger that runs after a run ends: a
#: `kind: "run_completed"` row in the trigger store.
_RUN_COMPLETED = "run_completed"
_STORE = "store"  # unified TriggerStore kinds with no legacy backend (event/file/web_watch/idle/…)
#: A callback the agent registered with ``hook_register`` (`trigger_callbacks`).
_CALLBACK = trigger_callbacks.KIND


def _sel():
    import personalclaw.dashboard.handlers as _pkg  # noqa: F811

    return _pkg.sel()


def _redact(s: str) -> str:
    # `redact_for_display`: a trigger's edit form is seeded from these rows, and the PUT puts back
    # exactly this mask (`_keep_masked_trigger`).
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
    if raw and kind in (_SCHEDULE, _LIFECYCLE, _CALLBACK):
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

    🔴 Named `_runs_store`, not `_run_store`: the manual-fire handler is
    `trigger_runs._run_store(raw, request)` (S94), which lived in this module when the store
    accessor arrived, and a second function with that name silently SHADOWED it — driven, the
    history endpoint raised "_run_store() missing 2 required positional arguments". Python reports a
    same-name redefinition only at the call site, so the two names stay distinct across the split.

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
        # Masked like the list's name, for the week grid shows the same trigger.
        "trigger_name": _redact(trigger.name),
        "start": start,
        "days": days,
        "until": until,
        "gates": getattr(trigger, "gates", None) or {},
        "skip_dates": [str(d) for d in (spec.get("skip_dates") or [])],
        "tz_name": str(spec.get("timezone") or ""),
    }
    if kind in ("interval", "sequence") and interval > 0:
        # 🔴 An UNARMED row must still plot. Measured on the owner's real store: `j-every` is enabled
        # with an empty `next_fire_at` (a re-enable did not arm it then), so
        # reading only `next_fire_at` gave `first_fire_at=0` and `project_occurrences` returned
        # NOTHING — a live 5-minute automation invisible on the week grid. Falling back to
        # `arm.next_fire` computes the same instant the tick will use, so the forecast is honest
        # whether or not the row happens to be armed yet.
        # 🔴 The RAW cadence, not the skip-aware `next_fire`. `project_occurrences` strikes
        # a skipped column ITSELF (AUTO-A3's "struck columns"), so a stepper that already advanced
        # past skipped days would hide exactly the slots the grid exists to show — the user would
        # see a quiet week with no explanation instead of their holiday struck through.
        first = to_epoch(getattr(trigger, "next_fire_at", "")) or raw_next_fire(trigger)
        if first <= 0:
            return [], False
        return project_occurrences(interval_secs=interval, first_fire_at=first, **common)
    # `adaptive` rides the cron branch, not the interval one: it has no `interval_secs`,
    # so the arithmetic path above would read 0 and drop it — and the week view is the OTHER half
    # of the Triggers page, where a live maintenance automation plotting nothing is the same
    # invisible-but-firing defect the interval comment above records. It steps cleanly, because
    # `cadence_next_fire` for an adaptive clock is `after + <the live cadence>`.
    #
    # 🔴 `at` RIDES IT TOO (issue 561). This branch used to end at a guard reading "`at` is a single
    # fire, and an elapsed one is not a forecast. Nothing to plot." — but the `return` was
    # unconditional, so a one-shot armed for THURSDAY plotted nothing either, and the user saw an
    # empty Thursday with no sign their trigger existed. Cron 7 occurrences, interval 167,
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
    check = _last_check(trigger)
    if check is not None and not check["can_fire"]:
        warnings = [*warnings, check["said"]]
    return {
        "kind": _STORE,
        "store_kind": trigger.kind,
        "id": f"{_STORE}:{trigger.id}",
        "raw_id": trigger.id,
        # Masked like a schedule row (`schedule_view.MASKED_FIELDS`): the name, what the trigger
        # watches, and the action with its prompt or command.
        "name": _redact(trigger.name),
        "enabled": trigger.enabled,
        "created_by": trigger.created_by,
        "spec": redact_values_for_display(dict(trigger.spec or {})),
        # The action as `{provider, config}` — the shape the page reads — whichever of the two
        # stored shapes the row uses. The raw `workflow` was sent, so a row whose action nests under
        # `inline` (every row the API, the CLI and the app reconcilers write, a data-event trigger
        # included) read "What it runs: Action" on the page. A workflow ref or resume target, which
        # has no action shape, is still sent as stored.
        "action": redact_values_for_display(
            _inline_action(trigger) or dict(trigger.workflow or {})
        ),
        "health": trigger.health_status,
        # 🔴 THE LIFECYCLE STATE, which this projection omitted. `Trigger.state` carries
        # `active | paused | autopaused | parked | quarantined | retired` and reached NO surface:
        # the list rendered an autopaused automation like a running one, so the states S139
        # (autopause), S159 (park/unpark) and the injection quarantine all decide were invisible
        # on the one page a user manages automations from. `health` cannot substitute — a PARKED
        # trigger is `health: parked` but an AUTOPAUSED one is `health: failing`, and "failing" does
        # not tell the user the automation has STOPPED.
        "state": trigger.state,
        "run_count": trigger.run_count,
        # When it last RAN, and how: the newest of its outcome stamps and its newest run
        # record's status, the pair a schedule row already carries. A store row had neither, so the
        # list could only infer "has it run" from `run_count` — the FIRE meter a Run button
        # deliberately does not spend — and a manual trigger read "never" beside the runs its own
        # history listed.
        "last_run_ts": _last_run_ts(trigger),
        "last_run_status": _last_run_status_for(trigger.id) or None,
        "last_error": _redact(trigger.last_error_summary or ""),
        "broken": errors,
        "warnings": warnings,
        "last_check": check,
        "needs_review": _needs_review(trigger),
        "needs_grant": _needs_grant(trigger),
        "held_back": _held_back(trigger),
        # Where the snapshot came from, when a restore holds it (`triggers.restore_hold`).
        "restore_hold": trigger.restore_hold,
        **_attribution(trigger, owner=owner),
    }


def _last_check(trigger: Any) -> dict[str, Any] | None:
    """A web watch's last check of its page (`web_poll.last_check`), masked like `last_error`; None
    for a kind that keeps none, or a watch not checked yet.

    A watch whose checks were refused or found nothing to track read "Firing on its own" with
    `warnings: []`: every check's outcome was computed, logged below the owner's level and served
    nowhere. The sentence the check left is what the row's warning says while it cannot fire.
    """
    if trigger.kind != "web_watch":
        return None
    from personalclaw.triggers.web_poll import last_check

    check = last_check(trigger, base_dir=config_dir())
    if check is not None:
        check["said"] = _redact(check["said"])
    return check


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
    """ONE schedule row, projected and masked (S101; the masking is the projection's own).

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
    projected["broken"] = errors
    projected["warnings"] = warnings
    projected["needs_review"] = _needs_review(trigger)
    projected["needs_grant"] = _needs_grant(trigger)
    projected["held_back"] = _held_back(trigger)
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


def _held_back(trigger: Any) -> dict[str, str] | None:
    """Why the row's agent may do less than its step asks, as things stand, and the working folder
    to trust for it (`triggers.grants.held_back`). The page says it with the trigger and offers
    Trust; its runs say it in their history (`triggers.settle`)."""
    from personalclaw.triggers.grants import held_back

    return held_back(trigger)


def _serialize_lifecycle(hook, used_by: list[str]) -> dict[str, Any]:
    from personalclaw.hooks import BLOCKING_EVENTS, hook_enforcement

    row = {
        "kind": _LIFECYCLE,
        "id": f"{_LIFECYCLE}:{hook.id}",
        "raw_id": hook.id,
        # Masked like a schedule row: the name, the matcher, and the action's config with its
        # prompt or command. The editor sends them back, and `_keep_masked_trigger` restores them.
        "name": _redact(hook.name),
        "enabled": hook.enabled,
        "action": {
            "provider": hook.provider,
            "config": redact_values_for_display(hook.provider_config),
        },
        # lifecycle mechanism
        "event": hook.event,
        "matcher": _redact(hook.matcher),
        "timeout": hook.timeout,
        "last_run": hook.last_run,
        "last_status": hook.last_status,
        "run_count": hook.run_count,
        "used_by": sorted(used_by),
        # Whether THIS hook can block, not just whether its event could. `used_by` alone made
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
        # Why the agent its action starts may do less than its step asks, as a store trigger says.
        "held_back": _held_back(hook),
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
    ``{schedule: [...], event: [...], run_completed: [...], lifecycle: [{event, label, desc, vars,
    blocking?}, ...], app_sources: [{app, label, events: [{event, source_event}]}]}``.
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
    from personalclaw.triggers.chain import RUN_COMPLETED_VARS
    from personalclaw.triggers.events import AGENT_SCOPED_EVENTS, DORMANCY_NOTES, DORMANT_EVENTS

    lifecycle = [
        {
            "event": e["event"],
            "label": e["label"],
            "desc": e["desc"],
            "vars": list(e["vars"]),
            "blocking": bool(e.get("blocking")),
            # 7 of the 15 declared events have no fire site — they are configurable and never
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
            "run_completed": list(RUN_COMPLETED_VARS),
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
_LIST_KINDS: tuple[str, ...] = (_SCHEDULE, _LIFECYCLE, _STORE, _CALLBACK)


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
    * ``callback``: every callback the agent registered (`webhook_callbacks`).

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
    if kind == _CALLBACK:
        from personalclaw import webhook_callbacks

        return webhook_callbacks.list_all()
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
    """GET /api/triggers?type=schedule|lifecycle|store|callback — every trigger.

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
        elif kind == _CALLBACK:
            triggers.extend(trigger_callbacks.serialize(c) for c in rows)
        else:
            triggers.extend(_serialize_store(row, owner=owner) for row in rows)

    tz_name, _ = get_local_tz()
    # `owner` mirrors the tasks seam's list response: the page labels a foreign row with its
    # author, and needs to know whose name is not worth showing.
    return web.json_response(
        {"triggers": triggers, "server_tz": tz_name, "owner": owner_username()}
    )


# ── create ──


def _stored_action(state: DashboardState, kind: str, raw: str) -> dict[str, Any]:
    """The action trigger *raw* runs now as ``{provider, config}``, or empty values. A store row's
    is read in either stored shape (`action_edit.action_in`): the flat one the chat's tools write
    read as no action here, so its masked values were not restored and its checks saw nothing."""
    from personalclaw.triggers.action_edit import action_in

    if kind == _LIFECYCLE:
        hook = _hook_store(state).get(raw)
        if hook is None:
            return {"provider": "", "config": {}}
        return {"provider": hook.provider, "config": dict(hook.provider_config or {})}
    row = _trigger_store().get(raw)
    inline = action_in(row.trigger.workflow if row is not None else None)
    config = inline.get("config")
    return {
        "provider": str(inline.get("provider") or ""),
        "config": dict(config) if isinstance(config, dict) else {},
    }


def _saving_action(state: DashboardState, kind: str, raw: str, body: dict) -> dict[str, Any] | None:
    """The action an edit's save stores: the settings *body*'s action sends put over the trigger's
    action as stored (`triggers.action_edit`), or ``None`` when it sends no action. Raises
    ``ValueError`` for a config that is not an object."""
    from personalclaw.triggers.action_edit import edited_action

    action = body.get("action")
    if not isinstance(action, dict):
        return None
    return edited_action(_stored_action(state, kind, raw), action)


async def _action_problem(action: Any) -> str:
    """Why a trigger's action could not run as written, asked when it is SAVED; "" when it could.

    One question for every trigger kind's create and edit, because the form that writes the
    action is one form. An edit's is asked of the action as it will be saved (`_saving_action`),
    the settings it did not send included. A `run-workflow` action saved with no workflow, or with
    one its inputs cannot start, failed at every fire instead
    (`run_workflow_provider.config_problem`), and so did a `send-message` naming a chat channel not
    set up here, or an id no channel, or more than one, takes
    (`send_message_provider.config_problem`). So did the working folder an agent it starts works in
    (`invoke-agent`, `run-prompt`), when the owner did not allow it.
    """
    if not isinstance(action, dict):
        return ""
    provider = str(action.get("provider") or "")
    config = action.get("config")
    if provider in ("invoke-agent", "run-prompt") and isinstance(config, dict):
        from personalclaw.action_providers.services import validate_spawn_cwd
        from personalclaw.automation_posture import step_problem

        cwd = str(config.get("cwd") or "").strip()
        refused = validate_spawn_cwd(cwd)
        if refused:
            return f"The working folder {cwd} can't be used: {refused}"
        scope_refused = step_problem(config)
        if scope_refused:
            return f"The files it may change can't be saved: {scope_refused}."
    if provider == "run-workflow":
        from personalclaw.action_providers.run_workflow_provider import config_problem

        return await config_problem(config if isinstance(config, dict) else {})
    if provider == "send-message":
        from personalclaw.action_providers import send_message_provider

        return send_message_provider.config_problem(config if isinstance(config, dict) else {})
    if provider == "bash":
        from personalclaw.action_providers import bash_provider

        return bash_provider.config_problem(config if isinstance(config, dict) else {})
    return ""


def _unconsented_loosening(
    request: web.Request, body: dict, *, where: str, stored: dict[str, Any], saving: Any
) -> tuple[str, LooseningAsk] | None:
    """``(field, what the owner is asked)`` when the action *saving* — the write's action as it
    will be saved — loosens whether the trigger's agent asks you (an ``approval_mode: "auto"``, a
    ``capability: "mutating"`` write grant) over the *stored* action (``{provider, config}``,
    ``{}`` for a new trigger) and *body* carries no ``confirm: true``; ``None`` otherwise. The
    refusal is written to the security audit; the caller answers ``consent_required``.

    The owner's half of the rule; an app cannot define a trigger at all
    (``apps/permissions.ROUTE_AUTHZ``). The Schedule form's "Auto-approve tools" switch is the
    common case, and the SPA asks in the sentence this carries (``withSecurityConsent``).
    """
    from personalclaw.automation_posture import unconsented_step_loosening

    if not isinstance(saving, dict):
        return None
    raw = stored.get("config")
    stored_config: dict[str, Any] = raw if isinstance(raw, dict) else {}
    new = saving.get("config")
    loosened = unconsented_step_loosening(
        where,
        current=stored_config,
        new=new if isinstance(new, dict) else {},
        body=body,
        provider=str(saving.get("provider") or stored.get("provider") or ""),
    )
    if loosened is None:
        return None
    field, _loosening = loosened
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="trigger.write",
        outcome="denied",
        source="dashboard",
        resources=f"{field}: loosening without confirm",
    )
    return loosened


def _grant_for_save(state: DashboardState, body: dict, *, kind: str, raw: str, saving: Any) -> Any:
    """The question (`triggers.grants.Question`) saving *body* needs the owner to answer, else
    ``None`` (`triggers.grants.question`). *saving* is its action as the save stores it
    (`_saving_action`), ``None`` when it sends none.

    The editor is where the owner re-points an action or rewrites what it runs, so it is where they
    are asked: an edit that saved `bash` into a trigger allowed only `notify`, or a new command into
    a trigger allowed to run the old one, used to save with nothing asked. With the owner's yes the
    save grants it (`tools.update`), so Run now works straight away.
    """
    import copy

    from personalclaw.triggers import grants

    if kind == _LIFECYCLE:
        # A lifecycle save may also send `enabled: true`, which is the toggle's switch-on — or its
        # Allow, when the trigger is on already — and is asked the toggle's question. Not asking it
        # of a trigger that is on would leave `_update_lifecycle` to switch it off, the opposite of
        # what the save asked for.
        hook = _hook_store(state).get(raw)
        if hook is None:
            return None
        candidate = copy.copy(hook)
        if isinstance(saving, dict):
            _apply_hook_action(candidate, saving)
            return grants.question(candidate, before=hook)
        need = grants.missing(candidate) if bool_field(body, "enabled", default=False) else []
        if not need:
            return None
        return grants.Question(need, grants.consent(candidate, need), grants.title(candidate, need))
    if not isinstance(saving, dict):
        return None
    row = _trigger_store().get(raw)
    if row is None:
        return None
    # The action the save stores (`tools.update` puts the edit over the stored one by the same
    # rule), so the question is about the row the save would store.
    candidate = copy.copy(row.trigger)
    candidate.workflow = {"inline": saving}
    return grants.question(candidate, before=row.trigger)


def _grant_for_create(body: dict, *, trigger_type: str) -> Any:
    """The question (`triggers.grants.Question`) creating *body*'s trigger needs the owner to answer
    for its action, else ``None``. The create dialog asks it with the rest, so creating one stays a
    single step."""
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
    elif trigger_type in (_SCHEDULE, _EVENT, _RUN_COMPLETED):
        kind = "clock" if trigger_type == _SCHEDULE else trigger_type
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
    if trigger_type not in (_LIFECYCLE, _SCHEDULE, _EVENT, _RUN_COMPLETED):
        return web.json_response(
            {"error": "trigger_type must be 'schedule', 'lifecycle', 'event' or 'run_completed'"},
            status=400,
        )
    problem = await _action_problem(body.get("action"))
    if problem:
        return json_error("invalid_request", message=problem, status=400)
    if trigger_type == _LIFECYCLE:
        return await _create_lifecycle(state, body, request)
    if trigger_type == _SCHEDULE:
        return await _create_schedule(state, body, request)
    if trigger_type == _RUN_COMPLETED:
        return _create_run_completed(state, body, request)
    return _create_event(state, body, request)


def _create_run_completed(state: DashboardState, body: dict, request: web.Request) -> web.Response:
    """Create a trigger that runs after a run ends: on any run of a workflow (``source_def``) or
    on one run going now (``source_run``). Written through `tools.create`, as the chat's
    `automation_create` makes one, so what it waits on is checked the same way: a run that has
    already ended, or one that does not exist, is refused before anything is saved."""
    from personalclaw.safety_flags import confirm_granted
    from personalclaw.schedule import normalize_action
    from personalclaw.triggers import tools as _tools
    from personalclaw.triggers.ownership import owner_username

    name = str(body.get("name") or "").strip()
    if not name:
        return json_error("invalid_request", message="name required", status=400)
    spec = {
        key: str(body.get(key) or "").strip()
        for key in ("source_def", "source_run")
        if str(body.get(key) or "").strip()
    }
    if not spec:
        return json_error(
            "invalid_request", message="Pick the workflow or the run it runs after.", status=400
        )
    try:
        action = normalize_action(body.get("action"))
    except ValueError as exc:
        return json_error("invalid_request", message=str(exc), status=400)
    asked = _creation_consent(request, body, trigger_type=_RUN_COMPLETED)
    if asked is not None:
        return asked
    store = _trigger_store()
    result = _tools.create(
        store,
        name=name,
        kind="run_completed",
        spec=spec,
        workflow={"inline": action},
        created_by="user",
        # `_creation_consent` asked the owner first, so `confirm: true` is their yes to it.
        owner_consented=confirm_granted(body),
    )
    if not result.ok:
        return json_error(
            "invalid_request", message=result.text.removeprefix("Error: "), status=400
        )
    raw_id = str((result.data.get("trigger") or {}).get("id") or "")
    made = result.data.get("trigger") or {}
    _audit_created_grant(request, raw_id, (made.get("capabilities") or {}).get("providers"))
    state.push_refresh("crons")
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="trigger.create",
        outcome="success",
        source="dashboard",
        resources=f"trigger:run_completed:{raw_id}",
    )
    row = store.get(raw_id)
    return web.json_response(
        {"ok": True, "trigger": _serialize_store(row, owner=owner_username()) if row else {}},
        status=201,
    )


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
    asks: list[tuple[str, str, str]] = []
    if grant is not None and not confirm_granted(body):
        caller = request.get("user", "dashboard")
        _audit_grant(caller, "denied", f"{field}: creating without confirm")
        asks.append((field, grant.sentence, grant.title))
    loosened = _unconsented_loosening(
        request, body, where=f"triggers.{label}.action", stored={}, saving=body.get("action")
    )
    if loosened is not None:
        asks.append((loosened[0], loosened[1].consent, LOOSEN_TITLE))
    return _asked(asks, loosened[1] if loosened else None)


#: The heading of the one question a write asks when its action needs a grant AND it loosens
#: whether the action's agent asks you: both sentences are in it, so the heading names both.
_GRANT_AND_LOOSEN_TITLE = "Allow what it runs, and loosen a security setting?"


def _asked(
    asks: list[tuple[str, str, str]], loosening: LooseningAsk | None = None
) -> web.Response | None:
    """One ``confirmation_required`` for everything a write needs the owner's yes for, or None.

    *asks* holds ``(field, sentence, title)`` per question — the grant for what the action runs,
    a loosened posture — so a single Allow is never consent to a sentence the dialog did not show,
    and its heading names what the owner is agreeing to: the question's own title when there is
    one, both halves when there are two. *loosening* is the posture question's own, when it is one
    of them: the dialog says what it changes from and to after the sentences, the last of which is
    its own.
    """
    if not asks:
        return None
    title = asks[0][2] if len(asks) == 1 else _GRANT_AND_LOOSEN_TITLE
    return consent_required(
        asks[0][0],
        " ".join(sentence for _field, sentence, _title in asks),
        title=title,
        change=loosening.change if loosening else "",
        caution=loosening.caution if loosening else "",
    )


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

    # 🔴 FAILURE ROUTING at CREATE too. Read on both paths deliberately: issue 272 was a
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
    failure_dedupe = bool_field(body, "failure_dedupe", default=False)
    # What a missed slot does: off (the default), it waits on the review; on, it runs once, late.
    catch_up = bool_field(body, "catch_up", default=False)
    # Read with the rest, before anything is written: `silent` is set on the row after it is made.
    silent = bool_field(body, "silent", default=False)
    strict_schedule = bool_field(body, "strict_schedule", default=False)

    # 🔴 the write re-point: the clock spec is built for the STORE, not for `add_job`. The
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
        at, problem = _one_shot_time(at_ts)
        if problem:
            return json_error("invalid_request", message=problem, status=400)
        spec = {"kind": "at", "at": at, "delete_after_run": True}
    else:
        return web.json_response({"error": "every, cron, or at required"}, status=400)

    if timezone_val:
        spec["timezone"] = timezone_val
    if strict_schedule:
        spec["strict"] = True
    if isinstance(body.get("skip_dates"), list):
        spec["skip_dates"] = [str(d) for d in body["skip_dates"]]

    from personalclaw.triggers import tools as _tools

    # `enabled` is OPTIONAL and defaults to on — but when it is sent it is honored. It used to be
    # read by nobody on this path, so a caller asking for a trigger created switched off got a live,
    # armed one and no indication otherwise (#587). A value that is not a JSON boolean is refused,
    # as the toggle refuses it: the string "false" is truthy, so a coercion would ARM a trigger the
    # caller asked to be created off.
    enabled = bool_field(body, "enabled", default=True)
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
        enabled=enabled,
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
        trigger.delivery = "none" if silent else (f"channel:{channel}" if channel else "inbox")
        # Set here rather than through `tools.create`, for the same reason `delivery` is: the
        # constructor takes the schedule mechanism and the action, and delivery is what the entity
        # calls this pair. `failure_policy` is BUILT, not merged, because the row was created one
        # statement ago and has no other policy key to preserve.
        trigger.failure_delivery = str(failure_delivery or "").strip()
        trigger.failure_policy = {
            **dict(trigger.failure_policy or {}),
            "dedupe_hash": failure_dedupe,
        }
        trigger.catch_up = catch_up
        store.upsert(trigger)
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
        if kind == _CALLBACK:
            return trigger_callbacks.delete(request, state, raw)
        if kind == _STORE:
            store = _trigger_store()
            if (gone := store.get(raw)) is None:
                return web.json_response({"error": "not found"}, status=404)
            store.delete(raw)
            _report_unscheduled(gone.trigger)
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
        # schedule — the store owns the row (§6 write re-point). Run HISTORY still lives in
        # `ScheduleRunStore` (keyed by a plain id, so it survives the cutover unchanged), so the
        # delete has two halves: drop the trigger, then drop its runs.
        store = _trigger_store()
        if (gone := store.get(raw)) is None:
            return web.json_response({"error": "not found"}, status=404)
        store.delete(raw)
        _report_unscheduled(gone.trigger)
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
    if kind == _CALLBACK:
        return json_error(
            "invalid_request",
            message=(
                "A callback is saved by the agent that registered it. Here it is allowed, switched "
                "off or deleted."
            ),
            status=400,
        )

    submitted = body
    # An edit's action is the settings it sends over the action as stored (`_saving_action`), and
    # every check below judges that: the action the save stores, not the part of it the form drew.
    try:
        body = _keep_masked_trigger(state, kind, raw, submitted)
        saving = _saving_action(state, kind, raw, body)
    except MaskConflict as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except ValueError as exc:
        return json_error("invalid_request", message=str(exc), status=400)
    # 🔴 Anything that awaits runs BEFORE the revision check (`trigger_revisions`), never after.
    problem = await _action_problem(saving)
    if not problem:
        # Before any consent question: an app's scheduled job runs at the app's agent tier, so no
        # posture is asked about for it, and none is saved (`app_crons.posture_refusal`).
        from personalclaw.apps.app_crons import posture_refusal as app_job_posture

        problem = app_job_posture(raw, saving)
    if problem:
        return json_error("invalid_request", message=problem, status=400)
    stale = trigger_revisions.refusal(
        request, body, schedule=kind == _SCHEDULE, current=lambda: _row_now(state, kind, raw)
    )
    if stale is not None:
        return stale
    # Restored again from the trigger as stored now, with nothing awaited before the write. The
    # check compares masked rows, so a save since that changed only a hidden value passes it, and
    # the copy restored before the await would put the old value back.
    try:
        body = _keep_masked_trigger(state, kind, raw, submitted)
        saving = _saving_action(state, kind, raw, body)
    except MaskConflict as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except ValueError as exc:
        return json_error("invalid_request", message=str(exc), status=400)
    # One question for everything this save needs the owner's yes for, so a single "Allow" is never
    # consent to a sentence the dialog did not show: a grant for the action as it is saved — a new
    # provider, or what a granted one runs changed — and a loosened approval posture for its agent.
    from personalclaw.safety_flags import confirm_granted

    caller = request.get("user", "dashboard")
    grant = _grant_for_save(state, body, kind=kind, raw=raw, saving=saving)
    grant_field = f"triggers.{request.match_info['id']}.capabilities"
    asks: list[tuple[str, str, str]] = []
    if grant is not None and not confirm_granted(body):
        _audit_grant(caller, "denied", f"{grant_field}: saving without confirm")
        asks.append((grant_field, grant.sentence, grant.title))
    loosened = _unconsented_loosening(
        request,
        body,
        where=f"triggers.{request.match_info['id']}.action",
        stored=_stored_action(state, kind, raw),
        saving=saving,
    )
    if loosened is not None:
        asks.append((loosened[0], loosened[1].consent, LOOSEN_TITLE))
    asked = _asked(asks, loosened[1] if loosened else None)
    if asked is not None:
        return asked

    if kind == _LIFECYCLE:
        saved = _update_lifecycle(state, raw, body, saving)
    else:
        saved = _update_schedule(state, raw, body)
    if grant is not None and saved.status == 200:
        # The save carried the owner's yes and the action it was asked about, so the save granted
        # exactly what the question named.
        _audit_grant(caller, "success", f"trigger:{raw}: {', '.join(grant.providers)}")
    return saved


def _report_unscheduled(trigger: Any) -> None:
    """A deleted automation that was a report's schedule leaves the report unscheduled, as the
    chat's delete does (`knowledge.report_schedules.adopt_removal`)."""
    from personalclaw.knowledge import report_schedules

    report_schedules.adopt_removal(trigger)


def _row_now(state: DashboardState, kind: str, raw: str) -> dict[str, Any] | None:
    """The row a fresh read hands out for the trigger as stored now, or ``None`` when absent."""
    if kind == _SCHEDULE:
        return None if (row := _trigger_store().get(raw)) is None else _schedule_row_for(state, row)
    hook = _hook_store(state).get(raw)
    return None if hook is None else _serialize_lifecycle(hook, _used_by_index().get(raw, []))


def _keep_masked_trigger(state: DashboardState, kind: str, raw: str, body: dict) -> dict:
    """*body* with each hidden value it echoes back restored from the stored trigger.

    Every kind's edit form is seeded from a masked row (:func:`_schedule_row_for`,
    :func:`_serialize_store`, :func:`_serialize_lifecycle`) and sends its name and action back, and
    a lifecycle form its matcher too, so each would be stored as the marker. Restored BEFORE the
    consent and action checks, so they judge what is actually saved.
    """
    out = dict(body)
    if kind == _LIFECYCLE:
        hook = _hook_store(state).get(raw)
        name, matcher = (hook.name, hook.matcher) if hook is not None else ("", "")
    else:
        row = _trigger_store().get(raw)
        name, matcher = (row.trigger.name if row is not None else ""), ""
    if isinstance(out.get("name"), str):
        out["name"] = keep_masked_spans(out["name"], name or "")
    if kind == _LIFECYCLE and isinstance(out.get("matcher"), str):
        out["matcher"] = keep_masked_spans(out["matcher"], matcher or "")
    if isinstance(out.get("action"), dict):
        out["action"] = keep_masked_values(out["action"], _stored_action(state, kind, raw))
    return out


def _update_lifecycle(
    state: DashboardState, raw: str, body: dict, saving: dict[str, Any] | None
) -> web.Response:
    """Save a lifecycle trigger's edit, and settle its grant the way `tools.update` settles a store
    trigger's: what the edit changed keeps no grant (`grants.narrow`), and `api_trigger_detail`
    asked the owner about it first, so `confirm: true` gives it. A save that would leave the trigger
    on without the grant it needs is switched off rather than left running unallowed.

    *saving* is the edit's action as it is saved (`_saving_action`): the settings it sent over the
    hook's own, so a setting the form did not send stays as it was."""
    import copy

    from personalclaw.safety_flags import confirm_granted
    from personalclaw.triggers import grants
    from personalclaw.validation import HOOK_UPDATE_SCHEMA, ValidationError, validate_tool_args

    patch: dict[str, Any] = {}
    for k in ("name", "event", "matcher", "timeout", "enabled"):
        if k in body:
            patch[k] = body[k]
    if saving is not None:
        if str(body["action"].get("provider") or "").strip():
            patch["provider"] = saving["provider"]
        patch["provider_config"] = saving["config"]
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
    # 🔴 `failure_delivery`/`failure_dedupe` join this allowlist. The delivery contract
    # was fully wired on the fire path — `delivery.route_for` picks the route per outcome,
    # `delivery.repeats_last_failure` gates on `failure_policy.dedupe_hash` — and NEITHER field was
    # readable or writable from any surface. `test_trigger_wire_field_census` is what keeps them
    # readable by BOTH this path and `_create_schedule`, which is the omission issue 272 was.
    for key in ("name", "channel", "failure_delivery"):
        if key in body:
            kwargs[key] = body[key]
    # The switches are JSON booleans, refused as anything else: the text "false" is truthy, so a
    # coercion would mute a schedule asked to deliver, or turn on what a caller asked to turn off.
    for key in ("silent", "strict_schedule", "failure_dedupe", "catch_up"):
        if (switch := optional_bool(body, key)) is not MISSING:
            kwargs[key] = switch
    if "failure_delivery" in kwargs:
        if not _delivery.is_valid_route(kwargs["failure_delivery"]):
            return json_error("invalid_request", message=_FAILURE_ROUTE_RULE, status=400)
        problem = _delivery.channel_route_problem(kwargs["failure_delivery"])
        if problem:
            return json_error("invalid_request", message=problem, status=400)
    if "action" in body and isinstance(body["action"], dict):
        # The settings it sends, put over the action as stored in `tools.update` (`action_edit`).
        kwargs["action"] = body["action"]
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
    if body.get("at"):
        # A one-shot's time. The edit form sends it on every save of a one-shot, and this function
        # never read it, so a moved time answered 200 and the row kept the old one. Read below,
        # against the time the row already has.
        kwargs["at_ts"] = body["at"]
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

    # 🔴 the write re-point: the store owns the row. Legacy kwargs are translated onto the
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
        if "at_ts" in kwargs:
            was_once = str(before.get("kind") or "") == "at"
            at_ts, problem = _one_shot_time(
                kwargs["at_ts"], kept=before.get("at") if was_once else None
            )
            if problem:
                return json_error("invalid_request", message=problem, status=400)
            kwargs["at_ts"] = at_ts
        if "cron_expr" in kwargs and kwargs["cron_expr"]:
            spec = {"kind": "cron", "expr": str(kwargs["cron_expr"]).strip(), **_carried(spec)}
        elif "every_secs" in kwargs and kwargs["every_secs"]:
            spec = {
                "kind": "interval",
                "interval_secs": int(kwargs["every_secs"]),
                **_carried(spec),
            }
        elif "at_ts" in kwargs:
            # A one-shot keeps what it was made as while its time moves: the page's leaves the list
            # after its run and the chat's stays (`service.retire_after_run`). A cadence changed
            # into a one-shot here is the page's, as `_create_schedule` makes one.
            was_once = str(before.get("kind") or "") == "at"
            spec = {
                "kind": "at",
                "at": kwargs["at_ts"],
                "delete_after_run": bool(before.get("delete_after_run")) if was_once else True,
                **_carried(spec),
            }
        if "timezone" in kwargs:
            spec["timezone"] = kwargs["timezone"]
        if "strict_schedule" in kwargs:
            spec["strict"] = bool(kwargs["strict_schedule"])
        if "skip_dates" in kwargs:
            spec["skip_dates"] = kwargs["skip_dates"]
        # The next fire follows in `tools.update` (`arm.next_fire_after_edit`), the one rule the
        # chat's edit goes through too. It compares the spec this builds with the stored one, never
        # which keys the body carried: the form sends `timezone` on every save, and re-arming on
        # presence re-phased an hourly trigger by the wall time since the last rename (issue 531).

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
        if "catch_up" in kwargs:
            patch["catch_up"] = kwargs["catch_up"]

        from personalclaw.safety_flags import confirm_granted

        # `api_trigger_detail` asked for the grant this save needs (`_grant_for_save`), so a body
        # carrying `confirm: true` is the owner's yes, and the save gives it.
        result = _tools.update(
            store, trigger_id=raw, patch=patch, owner_consented=confirm_granted(body)
        )
        if not result.ok:
            return web.json_response({"error": result.text}, status=400)
        state.push_refresh("crons")
        return web.json_response({"ok": True, "trigger": _schedule_row_for(state, store.get(raw))})

    return web.json_response({"error": "not found"}, status=404)


def _carried(spec: dict[str, Any]) -> dict[str, Any]:
    """Spec keys that survive a CADENCE change (S101).

    Replacing `{kind, expr}` wholesale would silently drop `timezone`/`skip_dates`/`strict` — the
    quietly-losable class §1.3 warns about, and the exact fields S91's `verify-migration` exists to
    catch going missing. A user changing `0 9 * * *` to `0 10 * * *` must not lose their holidays.
    """
    return {k: v for k, v in spec.items() if k in ("timezone", "skip_dates", "strict")}


def _one_shot_time(value: Any, *, kept: Any = None) -> tuple[float, str]:
    """A one-shot's ``at`` as a request sends it: epoch seconds still to come, or why not.

    One reading for the create and the edit, so a time is taken or refused alike wherever it is
    typed. A time already gone is refused rather than saved: the row would sit listed, switched on,
    and never fire, which is why the chat's one-time task refuses it too, in these words. *kept* is
    the time an edited row already has, which its form sends back with every save: taken as it is,
    so a one-shot whose time has passed can still be renamed.
    """
    import math
    import time as _time

    try:
        at = float(value)
    except (TypeError, ValueError):
        return 0.0, "'at' must be a Unix timestamp in seconds"
    if not math.isfinite(at):
        return 0.0, "'at' must be a Unix timestamp in seconds"
    try:
        unchanged = kept is not None and at == float(kept)
    except (TypeError, ValueError):
        unchanged = False
    if at <= _time.time() and not unchanged:
        return 0.0, "That time has already passed. Give a time that is still to come."
    return at, ""


# ── toggle (run, fire, answer and test are `trigger_runs`) ──


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
        return consent_required(
            field, grants.consent(trigger, missing), title=grants.title(trigger, missing)
        )
    granted = grants.give(trigger)
    persist()
    if granted:
        _audit_grant(caller, "success", f"trigger:{trigger.id}: {', '.join(granted)}")
    return None


async def api_triggers_resume_restored(request: web.Request) -> web.Response:
    """POST /api/triggers/restore-hold/resume — Resume all: every automation a restore holds.

    A replace restore switches off each automation that ran on its own where its snapshot was taken
    (`triggers.restore_hold`), and the Triggers page says why. This switches each back on the way
    its own switch would (`tools.set_paused`), and answers ``{resumed: [{id, name}], still_held:
    [{id, name, reason}]}``: one that refuses — it no longer parses, or its action needs your yes,
    which its own switch asks for — stays held, with why. One automation's switch resumes it alone.
    """
    from personalclaw.triggers import restore_hold

    state: DashboardState = request.app["state"]
    # In the loop, as the switch writes: a few store writes under its lock, nothing awaited.
    resumed, kept = restore_hold.resume_all(_trigger_store())
    if resumed:
        state.push_refresh("crons")
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="trigger.resume_restored",
        outcome="success",
        source="dashboard",
        resources=f"resumed={len(resumed)} still_held={len(kept)}",
    )
    return web.json_response(
        {
            "ok": True,
            "resumed": [{"id": t.id, "name": _redact(t.name)} for t in resumed],
            "still_held": [{"id": t.id, "name": _redact(t.name), "reason": why} for t, why in kept],
        }
    )


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
    if kind == _CALLBACK:
        return await trigger_callbacks.toggle(request, state, raw)
    if kind == _STORE:
        # Route through the tool functions, which already refuse to enable a broken row and
        # report WHY — reusing them keeps the API and the chat tool answering identically.
        from personalclaw.triggers import tools as T

        store = _trigger_store()
        row = store.get(raw)
        if row is None:
            return web.json_response({"error": "not found"}, status=404)
        body = await json_object_body(request)
        # Left out, the switch flips; sent, it is the JSON true or false (`bool("false")` is True).
        want = bool_field(body, "enabled", default=None)
        paused = row.trigger.enabled if want is None else not want
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
        want = bool_field(body, "enabled", default=None)
        on = (not hook.enabled) if want is None else want
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
    enabled = bool_field(body, "enabled", default=None)
    # 🔴 the write re-point: the store owns the row. Routed through `tools.set_paused`, which
    # already refuses to enable a row that failed to parse and reports WHY — so the API and a
    # chat command cannot answer differently about the same trigger.
    store = _trigger_store()
    row = store.get(raw)
    if row is not None:
        from personalclaw.triggers import tools as _tools

        want = (not row.trigger.enabled) if enabled is None else enabled
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
        # Switching on arms it (`tools.set_paused`), as the chat's resume does.
        result = _tools.set_paused(store, trigger_id=raw, paused=not want)
        if not result.ok:
            return web.json_response({"error": result.text}, status=400)
        state.push_refresh("crons")
        return web.json_response({"ok": True})
    return web.json_response({"error": "not found"}, status=404)


# ── history (schedule-only) ──


def _redact_run(run: dict[str, Any], *, job_name: str | None = None) -> dict[str, Any]:
    out = dict(run)
    for key in ("summary", "trace", "error", "job_name"):
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
    # 🔴 A STORE trigger's run must open too. This 404'd every non-schedule kind, so the
    # list route S166 just fixed hands the UI a `run_id` that the detail route then denies — the
    # expander opens on nothing. `LIST -> total=1 run_id='fire-…'` followed by
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

    # No `state`: the doctor reads the store only.
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
    # 🔴 the doctor re-point: diagnosed from the STORE, where a `Trigger` carries `gates`,
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
                    # 🔴 Required by the `unfenced_write_action` check. Omitting it made the
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
        for issue in semantic_spec_issues(
            row.trigger.kind,
            row.trigger.spec,
            row.trigger.workflow,
            created_by=row.trigger.created_by,
        ):
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
    # 🔴 the history re-point: trigger NAMES come from the store — a trigger is named as it is
    # called now — and joining against the legacy service would label a run of a store-created
    # trigger with a blank, which reads in the UI as a run of a deleted automation. A run whose
    # trigger has left the list (a one-shot retires after its run, a deletion keeps the history) is
    # named by the name it ran under, which its row keeps.
    names = _trigger_names(state)
    enriched = [
        _redact_run(r, job_name=names.get(r.get("job_id", "")) or str(r.get("job_name") or ""))
        for r in runs
    ]

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
    from personalclaw import approval_answer

    ran, note = await trigger_runs._dispatch_store_action(
        row.trigger,
        {"trigger_id": trigger_id, "manual": True, "review": kind, "scheduled_for": taken.latest},
        event="review.run_now",
        late=reason,
        state=state,
        runs_for=approval_answer.of_request(request),
    )
    state.push_refresh("crons")
    return web.json_response(
        {
            "ok": ran,
            "outcome": outcome if ran else "failed",
            "reason": reason,
            "result": note,
            # What the run recorded, as `/run` answers it: a run that stopped for you did not "run".
            "status": _last_run_status_for(trigger_id) if ran else "",
        }
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
    # The `view` kind's render caller. Literal path, registered BEFORE `/{id}` for the
    # same reason as `/week` and `/doctor` — otherwise aiohttp captures `view` as a trigger id.
    app.router.add_post("/api/triggers/view/render", trigger_runs.api_trigger_view_render)
    # The restart review. Literal path, registered BEFORE `/{id}` for the same reason.
    app.router.add_get("/api/triggers/review", api_trigger_review)
    app.router.add_post("/api/triggers/review", api_trigger_review)
    # Resume all, after a restore. Literal path, registered BEFORE `/{id}` for the same reason.
    app.router.add_post("/api/triggers/restore-hold/resume", api_triggers_resume_restored)
    app.router.add_put("/api/triggers/{id}", api_trigger_detail)
    app.router.add_delete("/api/triggers/{id}", api_trigger_detail)
    app.router.add_post("/api/triggers/{id}/toggle", api_trigger_toggle)
    app.router.add_post("/api/triggers/{id}/run", trigger_runs.api_trigger_run)
    app.router.add_post("/api/triggers/{id}/answer", trigger_runs.api_trigger_answer)
    # The external webhook fire endpoint. Beside `/run`, same `{id}` shape, so it needs
    # no special ordering relative to the literal `/week`/`/doctor`/`/view/render` segments above.
    app.router.add_post("/api/triggers/{id}/fire", trigger_runs.api_trigger_fire)
    app.router.add_post("/api/triggers/{id}/test", trigger_runs.api_trigger_test)
    app.router.add_post("/api/triggers/{id}/to-chat", trigger_runs.api_trigger_to_chat)
    app.router.add_get("/api/triggers/{id}/history", api_trigger_history)
    app.router.add_get("/api/triggers/{id}/history/{run_id}", api_trigger_history_detail)
