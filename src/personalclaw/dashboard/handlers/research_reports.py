"""Scheduled research reports — the report-definition API (WF2KNO-12).

::

    GET    /api/knowledge/reports            every definition
    POST   /api/knowledge/reports            create one
    PUT    /api/knowledge/reports/{id}       update one
    DELETE /api/knowledge/reports/{id}       delete one
    POST   /api/knowledge/reports/{id}/run   run one NOW, by hand

**A malformed schedule is refused at the door, never stored.** ``validate_cron_expr`` runs
against the request body, so an unparseable expression is a 400 that names the expression
instead of a stored row the scheduled runner re-reads and re-fails on every tick. That is
half of the atom's rule that a bad expression cannot wedge the runner (the runner refusing
an unparseable row it somehow holds is the other half); refusing it here is the half that
means the row never exists. The same reasoning covers ``citation_policy``: an unknown
policy would reach the report generator as a silent default, and a report that quietly
cited the wrong corpus is worse than one that was never created.

**A manual run is idempotent against a scheduled fire.** Both fires take the SAME claim,
keyed by :func:`report_claim_id` — ``research-report:<report_id>`` in the default claim
store (``config_dir()/trigger-claims/``, the root :class:`TriggerStore` also defaults to).
The claim store is a cross-process sidecar, so this handler can see a fire owned by the
gateway's scheduler loop; a process-local set could not, which is exactly why
``ScheduleService.is_running`` was retired. Pressing Run while a scheduled fire is in
flight is a 409, not a second concurrent generation of the same report.

**The sibling module is imported lazily.** ``personalclaw.knowledge.research_reports`` owns
persistence and the definition dataclass; this module owns only HTTP. The import is inside
the handlers and guarded, so a build without that module answers a clean 503 rather than
failing at gateway boot — an API surface must not be able to take the whole gateway down.

Every write goes through ``rr.from_dict``/``rr.to_dict``, never through the dataclass
constructor: the round-trip is the sibling's own contract, so this module needs no
knowledge of how a schedule or a scope is represented internally, and an update preserves
the fields it does not touch (``created_ts``, ``watermark_ts``, the last-run stamps)
because it edits the serialized form of the row that already exists.
"""

from __future__ import annotations

import logging
from importlib import import_module
from types import ModuleType
from typing import Any

from aiohttp import web

logger = logging.getLogger(__name__)

#: The claim-id namespace a research report's run holds. A sibling runner MUST build its
#: claim id with :func:`report_claim_id` — the whole point of the lease is that two fires
#: for one report collide, and two spellings of the key would never collide.
#: The single-flight key prefix. The OWNING module is
#: ``knowledge.research_reports.CLAIM_ID_PREFIX``; this literal is the fallback for a build
#: that does not ship it, and `test_the_claim_prefix_matches_the_owning_module` fails if the
#: two ever drift — two spellings of the key would make the lease silently unmatchable, i.e.
#: a 409 that can never fire.
CLAIM_ID_PREFIX = "research-report:"


def report_claim_id(report_id: str) -> str:
    """The claim id one report's run holds while in flight.

    Read through the owning module when it is importable, so the runner (which writes the
    claim) and this route (which refuses while it is held) cannot disagree.
    """
    mod = _reports_module()
    prefix = str(getattr(mod, "CLAIM_ID_PREFIX", CLAIM_ID_PREFIX)) if mod else CLAIM_ID_PREFIX
    return f"{prefix}{report_id}"


#: The action provider a report run dispatches. Resolved through the registry rather than
#: imported, so this module carries no dependency on the provider's implementation.
RUN_ACTION_PROVIDER = "knowledge-report"

_SCHEDULE_KINDS = ("every", "at", "cron")


def _reports_module() -> ModuleType | None:
    """The sibling persistence module, or None when this build does not ship it.

    ``import_module`` rather than a ``from … import``: the name may legitimately be absent,
    and a static import of a possibly-absent module is a lie to every reader and type
    checker that follows it.
    """
    try:
        return import_module("personalclaw.knowledge.research_reports")
    except ImportError:
        logger.debug("research_reports module unavailable", exc_info=True)
        return None


def _err(message: str, status: int = 400) -> web.Response:
    """The handler-family error envelope: a flat, human-readable ``error`` string."""
    return web.json_response({"error": message}, status=status)


def _unavailable() -> web.Response:
    """503 for a build without the reports module — honest, and never a 500."""
    return _err("scheduled research reports are not available in this build", 503)


def _sel_log(operation: str, resources: str) -> None:
    """SEL-log the write. Best-effort: an audit failure never breaks the response."""
    try:
        import personalclaw.dashboard.handlers as _pkg

        _pkg.sel().log_api_access(
            caller="dashboard",
            operation=operation,
            outcome="success",
            source="dashboard",
            resources=resources,
        )
    except Exception:
        logger.debug("research-report SEL audit failed", exc_info=True)


async def _body(request: web.Request) -> tuple[dict[str, Any] | None, web.Response | None]:
    """Parse a JSON object body, or return the 400 that says why it is not one."""
    try:
        raw = await request.json()
    except Exception:
        return None, _err("invalid JSON")
    if not isinstance(raw, dict):
        return None, _err("JSON body must be an object")
    return raw, None


# Every integer check below spells out `isinstance(x, bool)` alongside `isinstance(x, int)`:
# in Python a bool IS an int, so `{"iteration_cap": true}` would otherwise validate as 1 and
# store a cap the caller never asked for.


def _schedule(raw: Any) -> tuple[dict[str, Any] | None, web.Response | None]:
    """Validate + normalize a schedule payload into the sibling's serialized shape.

    The cron branch is the load-bearing one: ``validate_cron_expr`` is the SAME check the
    scheduler uses, so an expression this accepts is one the runner can parse.
    """
    from personalclaw.schedule import validate_cron_expr

    if not isinstance(raw, dict):
        return None, _err("schedule must be an object")
    kind = str(raw.get("kind") or "")
    if kind not in _SCHEDULE_KINDS:
        return None, _err(f"schedule.kind must be one of: {', '.join(_SCHEDULE_KINDS)}")
    out: dict[str, Any] = {"kind": kind, "every_secs": None, "at_ts": None, "cron_expr": None}
    if kind == "every":
        every = raw.get("every_secs")
        if not isinstance(every, int) or isinstance(every, bool) or every <= 0:
            return None, _err("schedule.every_secs must be a positive integer")
        out["every_secs"] = int(every)
    elif kind == "at":
        at_ts = raw.get("at_ts")
        if isinstance(at_ts, bool) or not isinstance(at_ts, (int, float)) or float(at_ts) <= 0:
            return None, _err("schedule.at_ts must be a positive epoch timestamp")
        out["at_ts"] = float(at_ts)
    else:
        expr = raw.get("cron_expr")
        if not isinstance(expr, str) or not expr.strip():
            return None, _err("schedule.cron_expr is required for a cron schedule")
        expr = expr.strip()
        if not validate_cron_expr(expr):
            return None, _err(
                f"invalid cron expression {expr!r} — expected 5 fields "
                "(minute hour day-of-month month day-of-week)"
            )
        out["cron_expr"] = expr
    return out, None


def _scope(raw: Any, label: str) -> tuple[dict[str, Any] | None, web.Response | None]:
    """Validate + normalize a source/context scope."""
    if not isinstance(raw, dict):
        return None, _err(f"{label} must be an object")
    tags = raw.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        return None, _err(f"{label}.tags must be a list of strings")
    window = raw.get("window_secs", 0)
    if not isinstance(window, int) or isinstance(window, bool) or window < 0:
        return None, _err(f"{label}.window_secs must be a non-negative integer")
    return {"tags": [str(t) for t in tags], "window_secs": int(window)}, None


def _fields(
    body: dict[str, Any], policies: tuple[str, ...], *, required: bool
) -> tuple[dict[str, Any] | None, web.Response | None]:
    """The shared create/update field validator.

    ``required=True`` (create) demands ``name``/``prompt``/``schedule``; an update validates
    only the keys the caller actually sent, so a PATCH-shaped PUT of one field cannot blank
    the rest. Every optional field is validated identically in both directions — a value
    that could not be created must not be reachable by editing.
    """
    out: dict[str, Any] = {}

    for key in ("name", "prompt"):
        if key in body:
            value = body.get(key)
            if not isinstance(value, str) or not value.strip():
                return None, _err(f"{key} must be a non-empty string")
            out[key] = value.strip()
        elif required:
            return None, _err(f"{key} is required")

    if "schedule" in body:
        schedule, error = _schedule(body.get("schedule"))
        if error is not None:
            return None, error
        out["schedule"] = schedule
    elif required:
        return None, _err("schedule is required")

    if "tz" in body:
        tz = body.get("tz")
        if not isinstance(tz, str):
            return None, _err("tz must be a string")
        out["tz"] = tz.strip()

    if "source" in body:
        scope, error = _scope(body.get("source"), "source")
        if error is not None:
            return None, error
        out["source"] = scope

    if "context" in body:
        raw_context = body.get("context")
        if raw_context is None:
            out["context"] = None
        else:
            scope, error = _scope(raw_context, "context")
            if error is not None:
                return None, error
            out["context"] = scope

    if "citation_policy" in body:
        policy = body.get("citation_policy")
        if not isinstance(policy, str) or policy not in policies:
            return None, _err(
                f"unknown citation_policy {policy!r} — must be one of: {', '.join(policies)}"
            )
        out["citation_policy"] = policy

    if "iteration_cap" in body:
        cap = body.get("iteration_cap")
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
            return None, _err("iteration_cap must be an integer >= 1")
        out["iteration_cap"] = int(cap)

    if "enabled" in body:
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            return None, _err("enabled must be a boolean")
        out["enabled"] = enabled

    return out, None


def _policies(rr: ModuleType) -> tuple[str, ...]:
    """The legal citation policies, read from the sibling so there is ONE list."""
    return tuple(str(p) for p in getattr(rr, "CITATION_POLICIES", ()))


async def api_reports_list(request: web.Request) -> web.Response:
    """GET /api/knowledge/reports — every definition, newest state as persisted."""
    rr = _reports_module()
    if rr is None:
        return _unavailable()
    return web.json_response({"reports": [rr.to_dict(d) for d in rr.load_reports()]})


async def api_report_create(request: web.Request) -> web.Response:
    """POST /api/knowledge/reports — create one definition."""
    rr = _reports_module()
    if rr is None:
        return _unavailable()
    body, error = await _body(request)
    if error is not None or body is None:
        return error or _err("invalid JSON")
    fields, error = _fields(body, _policies(rr), required=True)
    if error is not None or fields is None:
        return error or _err("invalid request")
    saved = rr.save_report(rr.from_dict(fields))
    _sel_log("knowledge_report.create", f"report_id={getattr(saved, 'id', '')}")
    return web.json_response({"report": rr.to_dict(saved)})


async def api_report_update(request: web.Request) -> web.Response:
    """PUT /api/knowledge/reports/{id} — update one definition.

    Edits the SERIALIZED existing row so untouched fields (``created_ts``, the last-run
    stamps, ``watermark_ts``) survive; rebuilding from the body alone would silently reset
    the watermark and make the next scheduled run re-read the whole window.
    """
    rr = _reports_module()
    if rr is None:
        return _unavailable()
    report_id = request.match_info["id"]
    existing = rr.get_report(report_id)
    if existing is None:
        return _err("not found", 404)
    body, error = await _body(request)
    if error is not None or body is None:
        return error or _err("invalid JSON")
    fields, error = _fields(body, _policies(rr), required=False)
    if error is not None or fields is None:
        return error or _err("invalid request")
    raw = dict(rr.to_dict(existing))
    raw.update(fields)
    raw["id"] = report_id
    saved = rr.save_report(rr.from_dict(raw))
    _sel_log("knowledge_report.update", f"report_id={report_id} fields={sorted(fields)}")
    return web.json_response({"report": rr.to_dict(saved)})


async def api_report_delete(request: web.Request) -> web.Response:
    """DELETE /api/knowledge/reports/{id} — remove one definition."""
    rr = _reports_module()
    if rr is None:
        return _unavailable()
    report_id = request.match_info["id"]
    if not rr.delete_report(report_id):
        return _err("not found", 404)
    _sel_log("knowledge_report.delete", f"report_id={report_id}")
    return web.json_response({"ok": True})


async def api_report_run(request: web.Request) -> web.Response:
    """POST /api/knowledge/reports/{id}/run — run one report NOW, by hand.

    The lease check comes FIRST, before any dispatch work: a scheduled fire already in
    flight holds ``report_claim_id(id)``, and a second generation of the same report would
    double-spend the model budget and race the watermark write. 409 with a machine-readable
    ``reason`` so the button can say "already running" rather than "something failed".

    A resolvable-provider failure answers 200 with ``ok: false`` — the rule the store-trigger
    Run path pins: the request was understood and answered honestly, and a provider that is
    not registered is not a malformed request. The FE branches on ``ok``, not on the status.
    """
    rr = _reports_module()
    if rr is None:
        return _unavailable()
    report_id = request.match_info["id"]
    defn = rr.get_report(report_id)
    if defn is None:
        return _err("not found", 404)

    from personalclaw.triggers import claims as _claims

    # The kill switch, on this manual path too. This handler dispatches a provider directly
    # rather than through `triggers.tools.run`, so enforcing it only there would leave "Run now"
    # firing during an incident — and `test_action_provider_chokepoints` requires every module
    # that reaches a provider to carry one of the named policy checks for exactly this reason.
    # 200 with `ok: false`, not a 4xx: a guardrail decision is not a malformed request (the rule
    # the trigger Run path and the event-trigger `/test` both follow).
    from personalclaw.triggers.tools import manual_refusal

    refusal = manual_refusal()
    if refusal:
        return web.json_response({"ok": False, "report_id": report_id, "refused": refusal})
    if _claims.is_running(report_claim_id(report_id)):
        return web.json_response(
            {"error": "a run for this report is already in flight", "reason": "already_running"},
            status=409,
        )

    ran, note = await _dispatch_report(report_id)
    _sel_log("knowledge_report.run", f"report_id={report_id} ran={ran}")
    return web.json_response({"ok": ran, "report_id": report_id, "result": note})


async def _dispatch_report(report_id: str) -> tuple[bool, str]:
    """Dispatch the ``knowledge-report`` action through the action-provider registry.

    The SAME seam the store-trigger Run button and the autonomous fire use, so a manual run
    and a scheduled fire execute the same action the same way. ``ran`` is returned separately
    from the note because the caller answers ``ok`` with it: a run that resolved no provider
    is not a success, and folding it into prose behind a 200 is how a no-op hides.
    """
    from personalclaw.action_providers import ActionContext, get_action_provider
    from personalclaw.action_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()
    provider = get_action_provider(RUN_ACTION_PROVIDER)
    if provider is None:
        return False, f"unknown action provider {RUN_ACTION_PROVIDER!r}"
    ctx = ActionContext(
        event="manual.run",
        context="",
        payload={"report_id": report_id, "manual": True},
    )
    try:
        # `manual: True` in the ACTION CONFIG, not only in `ctx.payload`: the provider's
        # dueness pre-flight reads its config, because that is the surface a trigger row also
        # fills — and a trigger row never sets this key, so a scheduled fire cannot skip the
        # window check by accident. The user clicking Run now is the authority for that fire;
        # refusing it as "not due" would make the button lie.
        result = await provider.execute({"report_id": report_id, "manual": True}, ctx)
    except Exception as exc:  # noqa: BLE001 - a failed manual run is REPORTED, not raised
        logger.warning("research report run failed for %s", report_id, exc_info=True)
        return False, f"failed: {type(exc).__name__}: {exc}"
    if result is not None and not bool(getattr(result, "success", True)):
        return False, str(getattr(result, "error", "") or "the report action failed")
    return True, "the report run started"


def setup_research_report_routes(app: web.Application) -> None:
    """Register /api/knowledge/reports* — the report-definition CRUD + manual run."""
    app.router.add_get("/api/knowledge/reports", api_reports_list)
    app.router.add_post("/api/knowledge/reports", api_report_create)
    app.router.add_put("/api/knowledge/reports/{id}", api_report_update)
    app.router.add_delete("/api/knowledge/reports/{id}", api_report_delete)
    app.router.add_post("/api/knowledge/reports/{id}/run", api_report_run)
