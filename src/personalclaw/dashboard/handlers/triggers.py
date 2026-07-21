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
import time
from typing import Any

from aiohttp import web

from personalclaw.dashboard.state import DashboardState
from personalclaw.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)

_SCHEDULE = "schedule"
_LIFECYCLE = "lifecycle"
_EVENT = "event"  # data-event triggers (#38): memory/content patterns


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
    """``schedule:abc`` → (``schedule``, ``abc``); bare id defaults to schedule."""
    kind, _, raw = trigger_id.partition(":")
    if raw and kind in (_SCHEDULE, _LIFECYCLE, _EVENT):
        return kind, raw
    return _SCHEDULE, trigger_id


# ── serializers ──


def _last_run_status(state: DashboardState, job_id: str) -> str | None:
    """The newest run record's status for the honest UI badge (T7), or None.

    Wraps ScheduleService.last_run_status defensively (returns None on any
    failure / test double) so the serializer stays robust + JSON-safe."""
    try:
        fn = getattr(state.crons, "last_run_status", None)
        status = fn(job_id) if callable(fn) else ""
        return status if isinstance(status, str) and status else None
    except Exception:
        return None


def _serialize_schedule(state: DashboardState, job) -> dict[str, Any]:
    from personalclaw.schedule import compute_next_run_ts, format_schedule, get_local_tz

    now = time.time()
    tz_name, _ = get_local_tz()
    return {
        "kind": _SCHEDULE,
        "id": f"{_SCHEDULE}:{job.id}",
        "raw_id": job.id,
        "name": _redact(job.name),
        "enabled": job.enabled,
        "action": job.action,
        # schedule mechanism
        "message": _redact(job.message),
        "schedule": _redact(format_schedule(job.schedule, tz_name=job.timezone or tz_name)),
        "cron_expr": job.schedule.cron_expr if job.schedule.kind == "cron" else None,
        "every_secs": job.schedule.every_secs if job.schedule.kind == "every" else None,
        "created_ts": job.created_ts or None,
        "last_status": job.last_status,
        # Honest last-run status for the UI badge (T7): the PERSISTENT status of
        # the newest run record (success | failure | timeout | launched). A
        # fire-and-forget run (run-prompt/run-workflow/invoke-agent) only LAUNCHED
        # a background turn, so job.last_status="ok" overstates it — last_run_status
        # surfaces "launched" instead, and unlike the runtime-only last_outcome it
        # survives restarts. "" → None so the badge falls back to last_status.
        "last_run_status": _last_run_status(state, job.id),
        "agent": _redact(job.agent_id or "") or None,
        "model": job.model or None,
        "channel": _redact(job.channel or "") or None,
        "approval_mode": _redact(job.approval_mode or "") or None,
        "silent": job.silent,
        "strict_schedule": job.strict_schedule,
        "timezone": job.timezone or None,
        "skip_dates": list(job.skip_dates) if job.skip_dates else [],
        "script": _redact(job.script or "") or None,
        "command": _redact(job.command or "") or None,
        "last_run_ts": job.last_run_ts,
        "has_result": bool(job.last_result),
        "last_result": _redact(job.last_result or "") or None,
        "last_error": _redact(job.last_error or "") or None,
        "next_run_ts": compute_next_run_ts(job, now=now),
        "is_running": state.crons.is_running(job.id),
        "running_since": state.crons.running_since(job.id),
        "has_session": f"cron-{job.id}" in state._sessions,
    }


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

    lifecycle = [
        {
            "event": e["event"],
            "label": e["label"],
            "desc": e["desc"],
            "vars": list(e["vars"]),
            "blocking": bool(e.get("blocking")),
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
        for job in state.crons.list_jobs(include_disabled=True):
            triggers.append(_serialize_schedule(state, job))
    if want in ("", _LIFECYCLE):
        used_by = _used_by_index()
        for hook in _hook_store(state).list_all():
            triggers.append(_serialize_lifecycle(hook, used_by.get(hook.id, [])))
    if want in ("", _EVENT):
        for t in _event_store().load():
            triggers.append(_serialize_event(t))

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
    return web.json_response(_serialize_event(t), status=201)


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

    kwargs: dict[str, Any] = {"channel": channel, "action": action}
    if every:
        try:
            kwargs["every_secs"] = int(every)
        except (ValueError, TypeError):
            return web.json_response({"error": "'every' must be an integer"}, status=400)
    elif cron_expr:
        kwargs["cron_expr"] = str(cron_expr).strip()
    elif at_ts:
        kwargs["at_ts"] = float(at_ts)
    else:
        return web.json_response({"error": "every, cron, or at required"}, status=400)

    try:
        job = state.crons.add_job(name, **kwargs)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    # Remaining schedule mechanism fields.
    if body.get("silent"):
        job.silent = True
    if timezone_val:
        job.timezone = timezone_val
    if body.get("strict_schedule"):
        job.strict_schedule = True
    if isinstance(body.get("skip_dates"), list):
        job.skip_dates = [str(d) for d in body["skip_dates"]]
    state.crons._save()
    state.push_refresh("crons")
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="trigger.create",
        outcome="success",
        source="dashboard",
        resources=f"trigger:schedule:{job.id}:{name}",
    )
    return web.json_response({"ok": True, "trigger": _serialize_schedule(state, job)})


# ── update / delete ──


async def api_trigger_detail(request: web.Request) -> web.Response:
    """PUT / DELETE /api/triggers/{id}."""
    state: DashboardState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])

    if request.method == "DELETE":
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
        # schedule
        if not state.crons.remove_job(raw):
            return web.json_response({"error": "not found"}, status=404)
        try:
            await state.crons.delete_runs(raw)
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

    if kind == _LIFECYCLE:
        return await _update_lifecycle(state, raw, body)
    return await _update_schedule(state, raw, body)


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
    try:
        job = state.crons.update_job(raw, **kwargs)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not job:
        return web.json_response({"error": "not found"}, status=404)
    state.push_refresh("crons")
    return web.json_response({"ok": True, "trigger": _serialize_schedule(state, job)})


# ── toggle / run / test ──


async def api_trigger_toggle(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/toggle — enable/disable."""
    state: DashboardState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])
    if kind == _LIFECYCLE:
        hook = _hook_store(state).toggle(raw)
        if not hook:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(
            {"ok": True, "trigger": _serialize_lifecycle(hook, _used_by_index().get(raw, []))}
        )
    # schedule
    try:
        body = await request.json()
    except Exception:
        body = {}
    enabled = body.get("enabled")
    if enabled is None:
        cur = next((j for j in state.crons.list_jobs(include_disabled=True) if j.id == raw), None)
        enabled = not cur.enabled if cur else True
    if not state.crons.enable_job(raw, enabled=bool(enabled)):
        return web.json_response({"error": "not found"}, status=404)
    state.push_refresh("crons")
    return web.json_response({"ok": True})


async def api_trigger_run(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/run — fire now.

    Schedule triggers run via the schedule service (non-blocking). This is also
    the path the ``schedule_trigger`` MCP tool posts to with the internal secret.
    Lifecycle triggers have no standalone "run" (they fire on agent events) — use
    the test endpoint instead.

    ``?dry_run=1`` (or JSON ``{"dry_run": true}``) runs a **dry-run replay** (T9):
    write-capable tools don't execute, so it previews what the trigger's current
    action WOULD do with no side effects — tagged ``trigger="replay"`` in history.
    """
    state: DashboardState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])
    if kind == _LIFECYCLE:
        return web.json_response(
            {"error": "lifecycle triggers fire on events; use /test"}, status=400
        )
    job = next((j for j in state.crons.list_jobs(include_disabled=True) if j.id == raw), None)
    if not job:
        return web.json_response({"error": "not found"}, status=404)
    if state.crons.is_running(raw):
        return web.json_response({"error": "already running", "running": True}, status=409)

    dry_run = request.query.get("dry_run", "") in ("1", "true", "yes")
    if not dry_run:
        try:
            body = await request.json()
            dry_run = bool(body.get("dry_run", False)) if isinstance(body, dict) else False
        except Exception:
            dry_run = False

    async def _run_and_refresh() -> None:
        try:
            await state.crons.run_job(raw, dry_run=dry_run)
        finally:
            state.push_refresh("crons", "cron_history")

    task = asyncio.create_task(_run_and_refresh())
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    state.push_refresh("crons")
    return web.json_response({"ok": True, "name": job.name, "dry_run": dry_run})


async def api_trigger_test(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/test — execute a lifecycle trigger's action once."""
    from personalclaw.hooks import run_script_hook
    from personalclaw.validation import sanitize_string

    state: DashboardState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])
    if kind != _LIFECYCLE:
        return web.json_response(
            {"error": "only lifecycle triggers support /test; use /run"}, status=400
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
    jobs = state.crons.list_jobs(include_disabled=True)
    job = next((j for j in jobs if j.id == raw), None)

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

    session = inject_schedule_result_to_session(state, job, job.last_result or "", history=history)
    return web.json_response({"ok": True, "session": session.key})


async def api_trigger_ack(request: web.Request) -> web.Response:
    """POST /api/triggers/{id}/ack — acknowledge a schedule trigger notification."""
    state: DashboardState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])
    if kind != _SCHEDULE:
        return web.json_response({"error": "only schedule triggers post notifications"}, status=400)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ok = state.crons.ack_job(raw, body.get("summary", "acknowledged"))
    if body.get("ts"):
        state.ack_notification(body["ts"])
    return web.json_response({"ok": ok})


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
    """GET /api/triggers/{id}/history — per-trigger run records (schedule only)."""
    state: DashboardState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])
    if kind != _SCHEDULE:
        return web.json_response({"runs": [], "total": 0})
    try:
        limit = max(1, min(int(request.query.get("limit", "10")), 100))
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError:
        return web.json_response({"error": "invalid limit/offset"}, status=400)
    try:
        runs, total = await state.crons.list_runs(raw, offset=offset, limit=limit)
    except ValueError:
        return web.json_response({"error": "invalid trigger id"}, status=400)
    return web.json_response({"runs": [_redact_run(r) for r in runs], "total": total})


async def api_trigger_history_detail(request: web.Request) -> web.Response:
    """GET /api/triggers/{id}/history/{run_id} — one full run record."""
    state: DashboardState = request.app["state"]
    kind, raw = _split_id(request.match_info["id"])
    if kind != _SCHEDULE:
        return web.json_response({"error": "not found"}, status=404)
    run_id = request.match_info["run_id"]
    try:
        run = await state.crons.get_run(raw, run_id)
    except ValueError:
        return web.json_response({"error": "invalid trigger id"}, status=400)
    if run is None:
        return web.json_response({"error": "run not found"}, status=404)
    return web.json_response({"run": _redact_run(run)})


async def api_trigger_history_all(request: web.Request) -> web.Response:
    """GET /api/triggers/history — cross-trigger run index (schedule runs)."""
    state: DashboardState = request.app["state"]
    try:
        limit = max(1, min(int(request.query.get("limit", "20")), 100))
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError:
        return web.json_response({"error": "invalid limit/offset"}, status=400)
    raw_filter = request.query.get("trigger_id") or None
    if raw_filter:
        _, raw_filter = _split_id(raw_filter)
    runs, total = await state.crons.list_all_runs(offset=offset, limit=limit, job_id=raw_filter)
    names = {j.id: j.name for j in state.crons.list_jobs(include_disabled=True)}
    enriched = [_redact_run(r, job_name=names.get(r.get("job_id", ""), "")) for r in runs]
    return web.json_response({"runs": enriched, "total": total})


def register_trigger_routes(app: web.Application) -> None:
    """Register /api/triggers/* — the unified Trigger surface."""
    app.router.add_get("/api/triggers", api_triggers)
    app.router.add_post("/api/triggers", api_trigger_create)
    app.router.add_get("/api/triggers/variables", api_trigger_variables)
    app.router.add_get("/api/triggers/history", api_trigger_history_all)
    app.router.add_put("/api/triggers/{id}", api_trigger_detail)
    app.router.add_delete("/api/triggers/{id}", api_trigger_detail)
    app.router.add_post("/api/triggers/{id}/toggle", api_trigger_toggle)
    app.router.add_post("/api/triggers/{id}/run", api_trigger_run)
    app.router.add_post("/api/triggers/{id}/test", api_trigger_test)
    app.router.add_post("/api/triggers/{id}/to-chat", api_trigger_to_chat)
    app.router.add_post("/api/triggers/{id}/ack", api_trigger_ack)
    app.router.add_get("/api/triggers/{id}/history", api_trigger_history)
    app.router.add_get("/api/triggers/{id}/history/{run_id}", api_trigger_history_detail)
