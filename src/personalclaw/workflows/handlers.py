"""HTTP handlers for `/api/workflows` — the REST surface over the v2 engine.

Every handler delegates to `workflows.service`, the SAME module the chat tools call. That
is the whole design: two surfaces over one engine must not grow two behaviours, and "the
tool did X but the API did Y" is a bug class this avoids by construction rather than by
keeping two implementations in sync by hand.

Three conventions this file follows from the surrounding code:

* **The HTTP error envelope is `{"error": {"code": ..., "message": ...}}` with a
  lowercase_snake code** (INTEGRATION-ARCHITECTURE §2.2), which is a DIFFERENT vocabulary
  from the service layer's `WF_*` codes — those are for an LLM reading a tool result, these
  are for a browser branching on a status. `_fail` maps one to the other in one place so
  they cannot drift.
* **Mutations are gated against restricted (incognito/guest) sessions and SEL-audited**,
  matching the artifacts/tasks handlers. A workflow run spends money and touches the world;
  an unaudited start is worse than an unaudited read.
* **The per-run SSE stream is snapshot-then-subscribe.** A client that subscribed first
  would miss everything between connect and the first event, and a terminal run closes
  immediately rather than holding a connection open forever for events that will never come.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aiohttp import web

from personalclaw.dashboard.handlers._shared import _is_restricted_session
from personalclaw.dashboard.sse import stream_response
from personalclaw.sel import sel
from personalclaw.workflows import service, store

logger = logging.getLogger(__name__)

#: service `WF_*` code → (HTTP status, wire code). The wire vocabulary is
#: lowercase_snake per §2.2; the service's codes are the LLM-facing channel. Mapped in ONE
#: place so a new service code cannot silently become a 500.
_STATUS_MAP: dict[str, tuple[int, str]] = {
    "WF_DEF_NOT_FOUND": (404, "not_found"),
    "WF_RUN_NOT_FOUND": (404, "not_found"),
    "WF_NODE_NOT_FOUND": (404, "not_found"),
    "WF_DEF_NAME_REQUIRED": (400, "invalid_request"),
    "WF_DEF_NAME_INVALID": (400, "invalid_request"),
    "WF_DEF_ROOT_REQUIRED": (400, "invalid_request"),
    "WF_DEF_INVALID": (422, "validation_failed"),
    "WF_DEF_INLINE_SECRET": (422, "inline_secret"),
    "WF_DEF_NO_WRITABLE_PROVIDER": (409, "read_only"),
    "WF_DEF_SAVE_FAILED": (500, "save_failed"),
    "WF_DEF_DELETE_FAILED": (500, "delete_failed"),
    "WF_RUN_MISSING_INPUTS": (400, "missing_inputs"),
    "WF_RUN_PREFLIGHT_FAILED": (422, "preflight_failed"),
    "WF_NO_SUPERVISOR": (503, "engine_unavailable"),
    "WF_RUN_LAUNCH_FAILED": (500, "launch_failed"),
    "WF_RUN_NOT_LIVE": (409, "run_not_live"),
    "WF_RUN_ALREADY_TERMINAL": (409, "already_terminal"),
    "WF_RUN_NO_SPEC": (500, "spec_unreadable"),
    "WF_RUN_BAD_SPEC": (500, "spec_unreadable"),
    "WF_NODE_NOT_RUN": (409, "not_produced"),
    "WF_MUT_NO_OPS": (400, "invalid_request"),
    "WF_MUT_VERSION_MISMATCH": (409, "version_mismatch"),
    "WF_MUT_CONFIRM_REQUIRED": (409, "confirmation_required"),
    "WF_NO_PENDING_GATE": (409, "no_pending_gate"),
    "WF_AMBIGUOUS_GATE": (409, "ambiguous_gate"),
    "WF_RESUME_UNKNOWN_TOKEN": (404, "unknown_token"),
    "WF_RESUME_EXPIRED": (410, "token_expired"),
    "WF_RESUME_INVALID_ANSWER": (400, "invalid_answer"),
    "WF_RESUME_ALREADY_USED": (409, "already_used"),
    "WF_RESUME_NOT_OWNER": (403, "not_owner"),
    "WF_RESUME_STALE_EPOCH": (409, "stale_epoch"),
    "WF_FORK_FAILED": (400, "fork_failed"),
}

#: A validation-shaped service code we did not map explicitly still must not read as a
#: server fault — a 500 tells a client to retry something that will never succeed.
_DEFAULT_FAILURE = (400, "bad_request")


def _fail(body: dict[str, Any]) -> web.Response:
    """Render a service failure as the §2.2 HTTP envelope.

    The service's payload rides along under `detail`: a preflight failure's findings or a
    validation issue list is the actionable half, and dropping it would leave a client with
    a status code and nothing to show a user.
    """
    code = str(body.get("code", "") or "")
    status, wire = _STATUS_MAP.get(code, _DEFAULT_FAILURE)
    detail = {k: v for k, v in body.items() if k not in ("ok", "code", "message")}
    envelope: dict[str, Any] = {
        "error": {"code": wire, "message": str(body.get("message", "") or "request failed")}
    }
    if detail:
        envelope["error"]["detail"] = detail
    if code:
        # The service code is kept for a client that wants the finer distinction; the
        # lowercase_snake `code` stays the thing to branch on.
        envelope["error"]["service_code"] = code
    return web.json_response(envelope, status=status)


def _ok(body: dict[str, Any], *, status: int = 200) -> web.Response:
    return web.json_response({k: v for k, v in body.items() if k != "ok"}, status=status)


def _reply(body: dict[str, Any], *, status: int = 200) -> web.Response:
    return _ok(body, status=status) if body.get("ok") else _fail(body)


async def _json_body(request: web.Request) -> dict[str, Any] | web.Response:
    try:
        raw = await request.json()
    except Exception:
        return web.json_response(
            {"error": {"code": "invalid_request", "message": "invalid JSON body"}}, status=400
        )
    if not isinstance(raw, dict):
        return web.json_response(
            {"error": {"code": "invalid_request", "message": "body must be a JSON object"}},
            status=400,
        )
    return raw


def _guard(request: web.Request, operation: str) -> web.Response | None:
    """Refuse a mutation from a restricted session, and audit either way.

    A workflow run spends money and touches the world, so every mutating call is audited —
    an unaudited start is a worse gap than an unaudited read.
    """
    state = request.app.get("state")
    if state is not None and _is_restricted_session(state, request):
        _audit(request, operation, "denied")
        return web.json_response(
            {"error": {"code": "restricted_session", "message": "this session cannot mutate"}},
            status=403,
        )
    return None


def _audit(request: web.Request, operation: str, outcome: str, resources: str = "") -> None:
    try:
        sel().log_api_access(
            caller=request.headers.get("X-Session-Key", "") or "dashboard:ui",
            operation=operation,
            outcome=outcome,
            resources=resources,
        )
    except Exception:
        # An audit failure must never block the operation it is recording.
        logger.debug("workflow api audit skipped", exc_info=True)


def _supervisor(request: web.Request) -> Any:
    """The workflow supervisor from app state, or None.

    Read per-request rather than captured at registration: the gateway wires services after
    routes, and a captured None would make every mutating route permanently inert.
    """
    state = request.app.get("state")
    return getattr(state, "workflows", None) if state is not None else None


# ── definitions ──────────────────────────────────────────────────────────────


async def api_defs_list(request: web.Request) -> web.Response:
    return _reply(
        await service.list_defs(
            tag=request.query.get("tag", ""), source=request.query.get("source", "")
        )
    )


async def api_def_detail(request: web.Request) -> web.Response:
    return _reply(await service.get_def(request.match_info.get("name", "")))


async def api_def_save(request: web.Request) -> web.Response:
    denied = _guard(request, "workflow_def_save")
    if denied is not None:
        return denied
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    root = body.get("root")
    if not isinstance(root, dict):
        return web.json_response(
            {"error": {"code": "invalid_request", "message": "'root' must be an object"}},
            status=400,
        )
    result = await service.author_def(
        name=str(body.get("name", "") or ""),
        root=root,
        description=str(body.get("description", "") or ""),
        inputs=body.get("inputs") if isinstance(body.get("inputs"), dict) else None,
        tags=[str(t) for t in (body.get("tags") or [])],
        save=bool(body.get("save", True)),
        # A def saved through the API is the USER acting, not an agent — so it skips the
        # agent-provenance dry run, which exists for specs a model generated.
        provenance="user",
        strict=bool(body.get("strict", True)),
    )
    _audit(
        request,
        "workflow_def_save",
        "success" if result.get("ok") else "failure",
        str(body.get("name", "")),
    )
    return _reply(result, status=201 if result.get("saved") else 200)


async def api_def_delete(request: web.Request) -> web.Response:
    denied = _guard(request, "workflow_def_delete")
    if denied is not None:
        return denied
    name = request.match_info.get("name", "")
    result = await service.delete_def(name)
    _audit(request, "workflow_def_delete", "success" if result.get("ok") else "failure", name)
    return _reply(result)


# ── runs ─────────────────────────────────────────────────────────────────────


async def api_runs_list(request: web.Request) -> web.Response:
    """Paginated run list. Reads the store directly: this is a projection for a table, not
    an engine operation, and routing it through the service would add nothing."""
    try:
        limit = max(1, min(int(request.query.get("limit", "50")), 200))
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError:
        return web.json_response(
            {"error": {"code": "invalid_request", "message": "limit/offset must be integers"}},
            status=400,
        )
    runs, total = store.list_runs(
        workflow_name=request.query.get("workflow", ""),
        status=request.query.get("status", ""),
        root_run_id=request.query.get("root_run_id", ""),
        limit=limit,
        offset=offset,
    )
    return web.json_response(
        {
            "runs": [r.to_dict() for r in runs],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


async def api_run_start(request: web.Request) -> web.Response:
    denied = _guard(request, "workflow_run_start")
    if denied is not None:
        return denied
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    result = await service.start_run(
        name=str(body.get("name", "") or ""),
        inputs=body.get("inputs") if isinstance(body.get("inputs"), dict) else None,
        mode=str(body.get("mode", "background") or "background"),
        supervisor=_supervisor(request),
        origin_kind=_api_origin(),
        session_key=request.headers.get("X-Session-Key", "") or "",
        project_id=str(body.get("project_id", "") or ""),
        idempotency_key=str(body.get("idempotency_key", "") or ""),
        blocking_timeout=float(body.get("blocking_timeout", 0) or 0),
        skip_preflight=bool(body.get("skip_preflight", False)),
    )
    _audit(
        request,
        "workflow_run_start",
        "success" if result.get("ok") else "failure",
        str(body.get("name", "")),
    )
    return _reply(result, status=202 if result.get("ok") and not result.get("blocking") else 200)


def _api_origin() -> Any:
    from personalclaw.workflows.models import OriginKind

    return OriginKind.API


async def api_run_status(request: web.Request) -> web.Response:
    return _reply(service.status(request.match_info.get("run_id", "")))


async def api_run_output(request: web.Request) -> web.Response:
    return _reply(
        service.output(request.match_info.get("run_id", ""), request.match_info.get("node_id", ""))
    )


async def api_run_edit(request: web.Request) -> web.Response:
    denied = _guard(request, "workflow_run_edit")
    if denied is not None:
        return denied
    run_id = request.match_info.get("run_id", "")
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    ops = body.get("ops")
    if not isinstance(ops, list) or not ops:
        return web.json_response(
            {"error": {"code": "invalid_request", "message": "'ops' must be a non-empty array"}},
            status=400,
        )
    if bool(body.get("preview_only")):
        # A preview is a READ — no guard needed for its own sake, but it is cheap to keep
        # the same path so a client can preview then apply with one shape.
        return _reply(service.preview_edit(run_id, ops))
    expect = body.get("expect_version")
    result = service.edit_run(
        run_id,
        ops,
        supervisor=_supervisor(request),
        expect_version=int(expect) if isinstance(expect, (int, float)) else None,
        confirm_cascade=bool(body.get("confirm_cascade")),
        actor="user",
    )
    _audit(request, "workflow_run_edit", "success" if result.get("ok") else "failure", run_id)
    return _reply(result)


async def api_run_cancel(request: web.Request) -> web.Response:
    denied = _guard(request, "workflow_run_cancel")
    if denied is not None:
        return denied
    run_id = request.match_info.get("run_id", "")
    result = service.cancel_run(run_id, supervisor=_supervisor(request))
    _audit(request, "workflow_run_cancel", "success" if result.get("ok") else "failure", run_id)
    return _reply(result)


async def api_run_pause(request: web.Request) -> web.Response:
    denied = _guard(request, "workflow_run_pause")
    if denied is not None:
        return denied
    run_id = request.match_info.get("run_id", "")
    result = service.pause_run(run_id, supervisor=_supervisor(request))
    _audit(request, "workflow_run_pause", "success" if result.get("ok") else "failure", run_id)
    return _reply(result)


async def api_run_resume(request: web.Request) -> web.Response:
    """Answer a gate, or clear a pause.

    `channel` marks a REMOTE reply, which the engine owner-binds. An HTTP caller is already
    authenticated by the gateway, so it defaults to local — passing a channel through from
    an untrusted body would let a caller claim to be a channel and get the remote path's
    different rules.
    """
    denied = _guard(request, "workflow_run_resume")
    if denied is not None:
        return denied
    run_id = request.match_info.get("run_id", "")
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    result = service.resume_run(
        run_id,
        supervisor=_supervisor(request),
        token=str(body.get("resume_token", "") or ""),
        answer=body.get("answer"),
        always_allow=bool(body.get("always_allow")),
    )
    _audit(request, "workflow_run_resume", "success" if result.get("ok") else "failure", run_id)
    return _reply(result)


async def api_run_rewind(request: web.Request) -> web.Response:
    return await _reentry(request, "workflow_run_rewind", service.rewind_run)


async def api_run_from(request: web.Request) -> web.Response:
    return await _reentry(request, "workflow_run_from", service.run_from)


async def _reentry(request: web.Request, operation: str, fn: Any) -> web.Response:
    denied = _guard(request, operation)
    if denied is not None:
        return denied
    run_id = request.match_info.get("run_id", "")
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    node_id = str(body.get("node_id", "") or "")
    if not node_id:
        return web.json_response(
            {"error": {"code": "invalid_request", "message": "'node_id' is required"}}, status=400
        )
    kwargs: dict[str, Any] = {"supervisor": _supervisor(request)}
    if fn is service.rewind_run:
        kwargs["redo_effects"] = bool(body.get("redo_effects"))
        kwargs["force"] = bool(body.get("force"))
    result = fn(run_id, node_id, **kwargs)
    _audit(request, operation, "success" if result.get("ok") else "failure", run_id)
    return _reply(result)


async def api_run_fork(request: web.Request) -> web.Response:
    denied = _guard(request, "workflow_run_fork")
    if denied is not None:
        return denied
    run_id = request.match_info.get("run_id", "")
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    result = service.fork_run(
        run_id,
        checkpoint_id=str(body.get("checkpoint_id", "") or ""),
        note=str(body.get("note", "") or ""),
        supervisor=_supervisor(request),
    )
    _audit(request, "workflow_run_fork", "success" if result.get("ok") else "failure", run_id)
    return _reply(result, status=201 if result.get("ok") else 200)


async def api_run_continuations(request: web.Request) -> web.Response:
    """The pending resume tokens for a run — what a needs-input inbox renders.

    The ask and the handoff ride along so a client can render the whole card from one call
    rather than fetching per token.
    """
    from personalclaw.workflows.human_input import list_continuations

    run_id = request.match_info.get("run_id", "")
    if store.get(run_id) is None:
        return _fail({"code": "WF_RUN_NOT_FOUND", "message": f"no run {run_id!r}"})
    return web.json_response(
        {
            "continuations": [
                {
                    "resume_token": c.token,
                    "node_id": c.node_id,
                    "instance_path": c.instance_path,
                    "ask": c.ask,
                    "handoff": c.handoff,
                    "expires_at": c.expires_at,
                    "expired": c.expired,
                }
                for c in list_continuations(run_id)
            ]
        }
    )


async def api_audit(request: web.Request) -> web.Response:
    """Diagnose/heal. `dry_run` defaults TRUE — a GET-shaped repair that ran by default
    would be a foot-gun, and the engine's own default agrees."""
    dry_run = request.query.get("dry_run", "true").lower() not in ("false", "0", "no")
    if not dry_run:
        denied = _guard(request, "workflow_audit_heal")
        if denied is not None:
            return denied
    result = service.audit(dry_run=dry_run, supervisor=_supervisor(request))
    if not dry_run:
        _audit(request, "workflow_audit_heal", "success")
    return _reply(result)


async def api_manifest(request: web.Request) -> web.Response:
    return _reply(service.manifest())


# ── per-run SSE ──────────────────────────────────────────────────────────────


async def api_run_events(request: web.Request) -> web.Response | web.StreamResponse:
    """Per-run event stream, snapshot-then-subscribe.

    The snapshot goes out BEFORE subscribing (WF2-R11): a client that subscribed first would
    miss everything between connect and the first event, and then render a run that looks
    stalled. A TERMINAL run closes immediately instead of holding a connection open forever
    waiting for events that will never arrive.

    The snapshot is schema-validated before transmission (`projection.project`): the widget
    builds its entire view-model from this one frame, so a malformed field does not degrade
    the widget, it corrupts it — and the failure would surface in a browser console rather
    than here where it is one line to fix.
    """
    from personalclaw.workflows.models import TERMINAL_RUN_STATUSES
    from personalclaw.workflows.projection import project
    from personalclaw.workflows.watchdog import registry_key

    run_id = request.match_info.get("run_id", "")
    run = store.get(run_id)
    if run is None:
        return _fail({"code": "WF_RUN_NOT_FOUND", "message": f"no run {run_id!r}"})

    state = request.app.get("state")
    registry = None
    if state is not None:
        getter = getattr(state, "workflow_sse", None)
        registry = getter() if callable(getter) else getter
    if registry is None:
        return web.json_response(
            {"error": {"code": "engine_unavailable", "message": "no event registry"}}, status=503
        )

    key = registry_key(run_id)
    snap, _issues = project(run_id)
    snapshot = json.dumps(snap)
    return await stream_response(
        request,
        registry.hub(key),
        on_connect=[("workflow_snapshot", snapshot)],
        registry_evict=(registry, key),
        close_after_connect=run.status in TERMINAL_RUN_STATUSES,
    )


# ── registration ─────────────────────────────────────────────────────────────


def register_workflow_routes(app: web.Application) -> None:
    """Mount `/api/workflows/*`.

    Route order matters in aiohttp: the literal `/runs` paths are registered BEFORE
    `/{name}`, or a request for `/api/workflows/runs` would match the def-detail route and
    look for a definition named "runs".
    """
    app.router.add_get("/api/workflows/manifest", api_manifest)
    app.router.add_get("/api/workflows/audit", api_audit)

    # Runs — before the def wildcard.
    app.router.add_get("/api/workflows/runs", api_runs_list)
    app.router.add_post("/api/workflows/runs", api_run_start)
    app.router.add_get("/api/workflows/runs/{run_id}", api_run_status)
    app.router.add_get("/api/workflows/runs/{run_id}/events", api_run_events)
    app.router.add_get("/api/workflows/runs/{run_id}/continuations", api_run_continuations)
    app.router.add_get("/api/workflows/runs/{run_id}/outputs/{node_id}", api_run_output)
    app.router.add_post("/api/workflows/runs/{run_id}/edit", api_run_edit)
    app.router.add_post("/api/workflows/runs/{run_id}/cancel", api_run_cancel)
    app.router.add_post("/api/workflows/runs/{run_id}/pause", api_run_pause)
    app.router.add_post("/api/workflows/runs/{run_id}/resume", api_run_resume)
    app.router.add_post("/api/workflows/runs/{run_id}/rewind", api_run_rewind)
    app.router.add_post("/api/workflows/runs/{run_id}/run-from", api_run_from)
    app.router.add_post("/api/workflows/runs/{run_id}/fork", api_run_fork)

    # Definitions.
    app.router.add_get("/api/workflows", api_defs_list)
    app.router.add_post("/api/workflows", api_def_save)
    app.router.add_get("/api/workflows/{name}", api_def_detail)
    app.router.add_delete("/api/workflows/{name}", api_def_delete)
