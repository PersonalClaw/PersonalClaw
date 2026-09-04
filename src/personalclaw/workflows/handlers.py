"""HTTP handlers for `/api/workflows` — the REST surface over the v2 engine.

Every handler delegates to `workflows.service`, the SAME module the chat tools call. That
is the whole design: two surfaces over one engine must not grow two behaviours, and "the
tool did X but the API did Y" is a bug class this avoids by construction rather than by
keeping two implementations in sync by hand.

Three conventions this file follows from the surrounding code:

* **The HTTP error envelope is `{"error": {"code": ..., "message": ...}}` with a
  lowercase_snake code** (`AGENTS.md` §"Shared conventions"; emitted by
  `personalclaw.http_errors.json_error`), which is a DIFFERENT vocabulary
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

import asyncio
import json
import logging
import time
from typing import Any

from aiohttp import web
from aiohttp.multipart import BodyPartReader

from personalclaw.dashboard.handlers._shared import _is_restricted_session
from personalclaw.dashboard.sse import stream_response
from personalclaw.safety_flags import strict_bool
from personalclaw.sel import sel
from personalclaw.workflows import service, store
from personalclaw.workflows.review_service import apply_triage, review_findings

logger = logging.getLogger(__name__)

#: service `WF_*` code → (HTTP status, wire code). The wire vocabulary is
#: lowercase_snake per the shared convention; the service's codes are the LLM-facing
#: channel. Mapped in ONE place so a new service code cannot silently become a 500.
_STATUS_MAP: dict[str, tuple[int, str]] = {
    "WF_DEF_NOT_FOUND": (404, "not_found"),
    "WF_RUN_NOT_FOUND": (404, "not_found"),
    "WF_NODE_NOT_FOUND": (404, "not_found"),
    "WF_DEF_NAME_REQUIRED": (400, "invalid_request"),
    "WF_DEF_NAME_INVALID": (400, "invalid_request"),
    # 409, not 400: the request is well-formed and the name is valid — it is the existing state
    # (a read-only provider already serving that name) that conflicts. Same status as
    # `WF_DEF_NO_WRITABLE_PROVIDER`, which is the same class of refusal.
    "WF_DEF_NAME_RESERVED": (409, "name_reserved"),
    "WF_DEF_ROOT_REQUIRED": (400, "invalid_request"),
    "WF_DEF_INVALID": (422, "validation_failed"),
    # 422 like a validation failure, because that is what it is: the spec is well-formed JSON
    # whose macro invocation cannot be expanded. A 400 would suggest a malformed request body.
    "WF_DEF_MACRO_INVALID": (422, "macro_invalid"),
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
    # A node the inspect endpoint was asked about that exists in the spec but has not reached a
    # terminal state. 409, not 404 (the node IS known) and not 400 (the request is well-formed):
    # the state is the problem, and it resolves itself as the run advances — a client can retry.
    "WF_NODE_NOT_TERMINAL": (409, "not_terminal"),
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
    # 409, not 400: the request is well-formed and the state is the problem — a client can fix
    # it by cancelling first, which a 400 ("you sent nonsense") would not suggest.
    "WF_RUN_NOT_TERMINAL": (409, "not_terminal"),
    "WF_RUN_DELETE_REFUSED": (400, "delete_refused"),
    # 500: the run and its recorded workspace both exist, so an unreadable one is OUR fault (a
    # git call that failed, a permission problem) — not a bad request the client can fix.
    "WF_WORKSPACE_UNREADABLE": (500, "workspace_unreadable"),
    # 409, not 404: the RUN exists and the route exists — the workflow declared no file drop. A 404
    # would tell the client the endpoint is wrong and send it looking for a different path.
    "WF_DROP_DISABLED": (409, "drop_disabled"),
    # 428 Precondition Required is the one status that says "resend this with the missing
    # precondition", which is exactly the retry an approval gate wants: same request, plus confirm.
    "WF_DROP_APPROVAL_REQUIRED": (428, "approval_required"),
    "WF_DROP_LIMIT": (409, "drop_limit"),
    "WF_DROP_WRITE_FAILED": (500, "drop_write_failed"),
    # A malformed triage decision list (EI-9). 400: the request is the problem and the
    # client can fix it — and it must NOT be defaulted through, because a decision whose
    # outcome we could not read is neither an accept nor a reject.
    "WF_TRIAGE_BAD_DECISIONS": (400, "invalid_request"),
}

#: A validation-shaped service code we did not map explicitly still must not read as a
#: server fault — a 500 tells a client to retry something that will never succeed.
_DEFAULT_FAILURE = (400, "bad_request")


def _fail(body: dict[str, Any]) -> web.Response:
    """Render a service failure as the shared HTTP error envelope.

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


async def api_defs_surfacing(request: web.Request) -> web.Response:
    """The templates list with its surfacing state — what the UX renders.

    A separate route from `GET /api/workflows` rather than a widened one: the thin list is on the
    hot path for the planner's picker, and making every caller pay for a per-def run-history lookup
    to render a name would be a cost nobody asked for.
    """
    return _reply(await service.list_defs_surfacing(now=time.time()))


async def api_def_detail(request: web.Request) -> web.Response:
    return _reply(await service.get_def(request.match_info.get("name", "")))


async def api_template_trajectory(request: web.Request) -> web.Response:
    """The trajectory-signature distribution and regression signal for one template (PP-7).

    Queryable per template WITHOUT a run in hand: it projects each of the template's recent runs to
    its decision-path signature, reports the distribution of signature classes, and carries the
    sample-gated regression signal — has the template shifted to a path that fails more often. A
    pure read over ledgers already on disk; the run projection at `/runs/{run_id}/introspect` shows
    the same signal for one run in the context of its siblings.
    """
    return _reply(service.template_trajectory(request.match_info.get("name", "")))


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
        metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else None,
        save=bool(body.get("save", True)),
        # A def saved through the API is the USER acting, not an agent — so it skips the
        # agent-provenance dry run, which exists for specs a model generated.
        provenance="user",
        strict=bool(body.get("strict", True)),
        # The §4.1 `workspace:` block. Threaded so a caller that declares isolation actually gets
        # it: without this the key is dropped before the save and the run-start applier finds
        # nothing to provision.
        workspace=body.get("workspace") if isinstance(body.get("workspace"), dict) else None,
    )
    _audit(
        request,
        "workflow_def_save",
        "success" if result.get("ok") else "failure",
        str(body.get("name", "")),
    )
    return _reply(result, status=201 if result.get("saved") else 200)


async def api_def_a2a_publish(request: web.Request) -> web.Response:
    """POST /api/workflows/{name}/a2a-publish — the template detail UI's publish toggle.

    Its own route rather than a field on the def save (EXTERNAL-ACCESS §5): the detail UI holds
    the SECRET-STRIPPED def, so re-saving that document to carry one bool would persist the
    stripped bindings. Guarded as a def SAVE because that is what it is — a write to the stored
    template — and because publishing a workflow to an external protocol is not a read.
    """
    denied = _guard(request, "workflow_def_save")
    if denied is not None:
        return denied
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    name = request.match_info.get("name", "")
    result = await service.set_a2a_published(name, body.get("published") is True)
    _audit(request, "workflow_def_a2a_publish", "success" if result.get("ok") else "failure", name)
    return _reply(result)


async def api_def_delete(request: web.Request) -> web.Response:
    denied = _guard(request, "workflow_def_delete")
    if denied is not None:
        return denied
    name = request.match_info.get("name", "")
    result = await service.delete_def(name)
    _audit(request, "workflow_def_delete", "success" if result.get("ok") else "failure", name)
    return _reply(result)


# ── versions + refiner (WF2LEA-6) ──────────────────────────────────────────────


def _template_run_stats(name: str) -> dict[str, Any]:
    """Cheap, bounded ledger signals that feed a template's maturity badge.

    ``clean_runs`` = completed runs; ``evaluator_rejected`` = whether any recent run's ledger
    carries a ``gate_rejected`` event ("the evaluator has rejected at least one real bad run",
    the R11 signal that separates a proven gate from one that has never fired). Bounded to the
    most recent runs so this stays a display read, not a scan of all history.
    """
    from personalclaw.workflows import journal
    from personalclaw.workflows.journal import GATE_REJECTED
    from personalclaw.workflows.models import RunStatus

    runs, _ = store.list_runs(workflow_name=name, limit=25)
    clean = sum(1 for r in runs if r.status == RunStatus.COMPLETE)
    rejected = False
    for run in runs[:10]:
        try:
            if journal.ledger(run.id, kinds={GATE_REJECTED}):
                rejected = True
                break
        except Exception:
            continue
    return {"clean_runs": clean, "evaluator_rejected": rejected}


# ── §4.4 human-attention accounting (EVALUATION-SUBSTRATE, atom ES-16) ─────────


def _attention_scopes(
    *, per_scope_runs: int = 15, now: float | None = None
) -> list[dict[str, Any]]:
    """Per-template attention summaries, derived from recent run ledgers.

    A bounded display read, same posture as :func:`_template_run_stats`: the most recent
    runs grouped by template, each run's journal read once. Computed on request, stored
    nowhere — the §4.4 discipline.
    """
    import time as _time

    from personalclaw.workflows import introspection, journal

    now = now if now is not None else _time.time()
    runs, _ = store.list_runs(limit=120)
    by_scope: dict[str, list[tuple[float, list[dict[str, Any]]]]] = {}
    for run in runs:
        scope = run.workflow_name or ""
        if not scope:
            continue
        bucket = by_scope.setdefault(scope, [])
        if len(bucket) >= per_scope_runs:
            continue
        try:
            events = journal.ledger(run.id)
        except Exception:
            continue
        started = introspection._epoch(run.started_at or run.created_at) or 0.0
        bucket.append((started, events))
    out = [
        introspection.attention_stats(scope, pairs, now=now).to_dict()
        for scope, pairs in sorted(by_scope.items())
        if pairs
    ]
    out.sort(key=lambda row: row.get("debt", 0.0), reverse=True)
    return out


def promotion_attention_note(_key: str = "") -> str:
    """The one-line attention citation a promotion proposal carries (§4.4).

    Aggregated across templates and labeled so — run-ledger attention events are
    template-scoped while rungs are action-type-scoped, and until the trust record binds
    the two (ES-14/15) a per-key attribution would be an invention. Empty when there is
    no sample; a proposal never cites a metric that does not exist. Best-effort: a
    citation failure must never block the proposal itself.
    """
    try:
        scopes = _attention_scopes()
    except Exception:
        return ""
    total_runs = sum(int(s.get("runs", 0)) for s in scopes)
    total_events = sum(int(s.get("attention_events", 0)) for s in scopes)
    if not total_runs:
        return ""
    rising = sum(1 for s in scopes if s.get("trend") == "rising")
    rate = round(total_events / total_runs, 2)
    note = f"workflow attention (all templates): {rate}/run over {total_runs} runs"
    if rising:
        note += f", {rising} scope{'s' if rising != 1 else ''} rising"
    return note


async def api_attention(request: web.Request) -> web.Response:
    """GET /api/workflows/attention — per-template §4.4 attention summaries."""
    _audit(request, "workflow_attention", "success", "")
    try:
        scopes = await asyncio.to_thread(_attention_scopes)
    except Exception:
        logger.warning("attention accounting query failed", exc_info=True)
        scopes = []
    return web.json_response({"scopes": scopes})


async def api_def_versions(request: web.Request) -> web.Response:
    """GET /api/workflows/{name}/versions — the monotonic version history + pin + maturity.

    When the store has no recorded versions yet (a bundled template nobody has refined), the
    current def stands in as the single version so the tab is never empty."""
    from personalclaw.workflows import versions

    name = request.match_info.get("name", "")
    detail = await service.get_def(name)
    if not detail.get("ok"):
        return _reply(detail)
    spec = detail.get("definition") or {}

    records = versions.list_versions(name)
    if not records:
        current = int(spec.get("version", 1) or 1)
        rows = [
            {
                "version": current,
                "source": versions.SOURCE_USER,
                "created_at": str(spec.get("updated_at") or spec.get("created_at") or ""),
                "note": "",
                "run_ids": [],
                "ops_count": 0,
            }
        ]
        pinned = current
    else:
        rows = [
            {
                "version": r.version,
                "source": r.source,
                "created_at": r.created_at,
                "note": r.note,
                "run_ids": r.run_ids,
                "ops_count": len(r.ops),
            }
            for r in records
        ]
        pinned = versions.pinned_version(name) or records[-1].version

    stats = _template_run_stats(name)
    maturity = versions.template_maturity(spec, **stats)
    return web.json_response({"versions": rows, "pinned": pinned, "maturity": maturity})


async def api_def_version_diff(request: web.Request) -> web.Response:
    """GET /api/workflows/{name}/versions/diff?a=&b= — the typed-op diff between two versions."""
    from personalclaw.workflows import versions

    name = request.match_info.get("name", "")
    try:
        a = int(request.query.get("a", "0"))
        b = int(request.query.get("b", "0"))
    except ValueError:
        return web.json_response(
            {"error": {"code": "invalid_request", "message": "a/b must be integers"}}, status=400
        )
    return web.json_response({"a": a, "b": b, "ops": versions.diff(name, a, b)})


async def api_def_repin(request: web.Request) -> web.Response:
    """POST /api/workflows/{name}/versions/repin {version} — rollback / re-pin the active version.

    Moves only the pinned pointer; history is never rewritten. A version that was never recorded
    is a 404 rather than a silent no-op — you cannot pin what does not exist."""
    denied = _guard(request, "workflow_version_repin")
    if denied is not None:
        return denied
    from personalclaw.workflows import versions

    name = request.match_info.get("name", "")
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    try:
        version = int(str(body.get("version", "")).strip())
    except ValueError:
        return web.json_response(
            {"error": {"code": "invalid_request", "message": "'version' must be an integer"}},
            status=400,
        )
    if not versions.repin(name, version):
        _audit(request, "workflow_version_repin", "failure", f"{name}:v{version}")
        return web.json_response(
            {"error": {"code": "not_found", "message": f"no version {version} for {name!r}"}},
            status=404,
        )
    _audit(request, "workflow_version_repin", "success", f"{name}:v{version}")
    return web.json_response({"ok": True, "name": name, "pinned": version})


async def api_def_ledger(request: web.Request) -> web.Response:
    """GET /api/workflows/{name}/ledger — recent runs of this template with their ledger totals.

    The Run Ledger tab: what this template actually did, run by run, with tokens/cost/step counts
    so a reviewer can see whether it is healthy before refining it."""
    from personalclaw.workflows import journal

    name = request.match_info.get("name", "")
    try:
        limit = max(1, min(int(request.query.get("limit", "20")), 100))
    except ValueError:
        limit = 20
    runs, total = store.list_runs(workflow_name=name, limit=limit)
    rows = []
    for run in runs:
        try:
            totals = journal.run_totals(run.id)
        except Exception:
            totals = {}
        rows.append(
            {
                "run_id": run.id,
                "status": str(run.status.value if hasattr(run.status, "value") else run.status),
                "spec_version": int(getattr(run, "spec_version", 1) or 1),
                "created_at": getattr(run, "created_at", ""),
                "totals": totals,
            }
        )
    return web.json_response({"name": name, "runs": rows, "total": total})


async def api_def_refine(request: web.Request) -> web.Response:
    """POST /api/workflows/{name}/refine — fire the refiner over this template on demand.

    The "Refine now" button: launches the bundled propose-only `refine-template` workflow with
    this template as its input. It proposes a diff for review; it never edits the template."""
    denied = _guard(request, "workflow_refine")
    if denied is not None:
        return denied
    name = request.match_info.get("name", "")
    result = await service.start_run(
        name="refine-template",
        inputs={"workflow_name": name},
        mode="background",
        supervisor=_supervisor(request),
        origin_kind=_api_origin(),
        session_key=request.headers.get("X-Session-Key", "") or "",
    )
    _audit(request, "workflow_refine", "success" if result.get("ok") else "failure", name)
    return _reply(result, status=202 if result.get("ok") else 200)


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
        skip_preflight=strict_bool(body.get("skip_preflight"), field="skip_preflight"),
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


async def api_run_delete(request: web.Request) -> web.Response:
    """Delete a terminal run and its artifacts, tearing its workspace down first.

    SEL-audited like the other mutations: a delete is the one workflow action with no undo, so
    an audit trail is what makes "where did that run go?" answerable.

    `keep_open=true` keeps the workspace directory when the workspace IS the deliverable (§4.1).
    A query flag rather than a second route: it is one deletion with two dispositions for the
    workspace, and a second route would be a second place to keep the ordering rule right.
    """
    denied = _guard(request, "workflow_run_delete")
    if denied is not None:
        return denied
    run_id = request.match_info.get("run_id", "")
    keep_open = str(request.query.get("keep_open", "")).lower() in ("1", "true", "yes")
    result = await service.delete_run(run_id, supervisor=_supervisor(request), keep_open=keep_open)
    _audit(request, "workflow_run_delete", "success" if result.get("ok") else "failure", run_id)
    return _reply(result)


async def api_run_workspace(request: web.Request) -> web.Response:
    """GET the run's workspace review: changed files + the two reintegration verbs (§4.1).

    A READ, deliberately — reintegration is offered, never performed. There is no POST companion
    here: `Apply Locally` and `Checkout Branch` are commands the USER runs in their own shell
    (the offer carries the branch name), so the gateway never merges into the user's working tree
    on their behalf. That is the plan's ruling, not a limitation — "apply this" that silently
    stomps an unrelated local edit is exactly what isolating the run was for.

    Not `_guard`ed: a read is not a mutation, matching `api_run_status` and `api_run_output`. The
    payload carries no secrets — `WorkspaceSpec.to_dict` serializes env PRESENCE only, and the
    changed-file list is paths.
    """
    return _reply(service.workspace_review(request.match_info.get("run_id", "")))


#: Per-file ceiling on a run's inbound drop. The route buffers each part to enforce it, so the cap
#: is also the memory bound — deliberately far below the resumable-upload ceiling, because a drop
#: zone is for reference material and anything larger belongs in the workspace via `/api/uploads/*`.
MAX_DROP_BYTES = 16 * 1024 * 1024


async def api_run_drop_status(request: web.Request) -> web.Response:
    """GET the run's file-drop policy + what has been dropped (WORK-CONTAINERS §2.5).

    A read, so not `_guard`ed — the same rule `api_run_workspace` follows. The payload is filenames,
    sizes and digests; no dropped CONTENT is served here, because a drop is untrusted input and the
    one sanctioned read path fences it (`filedrop.read_dropped_text`).
    """
    return _reply(service.drop_status(request.match_info.get("run_id", "")))


async def api_run_drop(request: web.Request) -> web.Response:
    """POST multipart to the run's approval-gated file drop (WORK-CONTAINERS §2.5, R17).

    Multipart with `file` parts, matching `/api/upload/file` — one ingestion convention, not a
    second. Approval rides in the SAME request as `confirm=true` (the `body.get("confirm") is True`
    shape the
    destructive dashboard routes use) rather than in a pending-upload record: a two-phase drop would
    have to hold unapproved bytes somewhere, and unapproved untrusted input parked on disk is the
    thing the gate exists to prevent. So an unapproved drop is REFUSED with what it would have
    accepted (name, size, MIME), the UI shows that, and the confirmed retry carries the file again.

    `_guard`ed as a mutation and SEL-audited PER FILE (§2.5): ingesting untrusted content into a
    run's
    reference zone is exactly the event an operator reconstructing "where did this instruction come
    from" needs to find.
    """
    denied = _guard(request, "workflow_run_drop")
    if denied is not None:
        return denied
    run_id = request.match_info.get("run_id", "")
    ctype = request.headers.get("Content-Type", "")
    if not ctype.lower().startswith("multipart/"):
        return web.json_response(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "multipart/form-data with one or more 'file' parts is required",
                }
            },
            status=400,
        )
    try:
        reader = await request.multipart()
    except (ValueError, AssertionError, RuntimeError) as exc:
        return web.json_response(
            {"error": {"code": "invalid_request", "message": f"unreadable multipart body: {exc}"}},
            status=400,
        )
    # `confirm` may arrive as a form field ahead of the files (a browser sends parts in order), so
    # it is read as the stream advances rather than from a pre-parsed body — request.post() would
    # buffer every file into memory to give the same answer.
    confirmed = request.query.get("confirm", "").lower() == "true"
    accepted: list[dict[str, Any]] = []
    while True:
        try:
            part = await reader.next()
        except (ValueError, AssertionError, RuntimeError) as exc:
            return web.json_response(
                {"error": {"code": "invalid_request", "message": f"malformed part: {exc}"}},
                status=400,
            )
        if part is None:
            break
        # A nested multipart reader is not a body part — skipped rather than duck-typed, the same
        # narrowing `api_upload_file` uses. Reading `.read_chunk` off it would be a runtime error.
        if not isinstance(part, BodyPartReader):
            continue
        name = part.name or ""
        if name == "confirm":
            confirmed = confirmed or (await part.text()).strip().lower() == "true"
            continue
        if name != "file":
            continue
        data = bytearray()
        over = False
        while True:
            chunk = await part.read_chunk(65536)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_DROP_BYTES:
                over = True
                break
        if over:
            _audit(request, "workflow_run_drop", "rejected", f"{run_id}:oversize")
            return web.json_response(
                {
                    "error": {
                        "code": "file_too_large",
                        "message": f"a dropped file may not exceed {MAX_DROP_BYTES} bytes",
                    }
                },
                status=413,
            )
        result = service.accept_dropped_file(
            run_id,
            filename=part.filename or "dropped",
            data=bytes(data),
            mime=part.headers.get("Content-Type", "") if part.headers else "",
            confirmed=confirmed,
        )
        if not result.get("ok"):
            _audit(
                request,
                "workflow_run_drop",
                "denied",
                f"{run_id}:{result.get('code', '')}",
            )
            return _fail(result)
        _audit(
            request,
            "workflow_run_drop",
            "success",
            f"{run_id}:{(result.get('file') or {}).get('filename', '')}",
        )
        accepted.append(result.get("file") or {})
    if not accepted:
        return web.json_response(
            {"error": {"code": "invalid_request", "message": "no 'file' part in the request"}},
            status=400,
        )
    return _reply(service.drop_status(run_id) | {"accepted": accepted})


async def api_run_outbox(request: web.Request) -> web.Response:
    """GET the run's published-artifact listing — the §2.5 outbox half of R17."""
    return _reply(service.outbox(request.match_info.get("run_id", "")))


async def api_run_introspect(request: web.Request) -> web.Response:
    """The §6.4 nine-question introspection projection for one run (WORK-CONTAINERS R6).

    A pure read over the journal the run already wrote — the cost/latency strip, the template
    p50/p95 card, the said-no gate table with its fake-check warnings, the journal timeline and
    attempt ledger, and the Proof section, in ONE response.

    One response rather than five routes on purpose: the checklist is a property of the whole
    surface, not of any single number, and `checklist_gaps` can only report a hole in a payload
    it can see in full. Five routes would let the cockpit render eight answers and never learn
    that the ninth was missing.
    """
    return _reply(service.introspect(request.match_info.get("run_id", "")))


async def api_run_output(request: web.Request) -> web.Response:
    return _reply(
        service.output(request.match_info.get("run_id", ""), request.match_info.get("node_id", ""))
    )


async def api_run_node_inspect(request: web.Request) -> web.Response:
    """The §5 reconstructability set for one terminal node (WF2-A2).

    A read-only forensics view over data the controller already persisted: the resolved
    prompt (or a ref), the resolved inputs, the output (or an `artifact_ref` when it was
    offloaded), the attempt records, the ledger slice for this node, and whether the output
    was served from the resume cache. WV-10 renders this as an inspector drawer; this route
    is the sole caller today.

    SECRETS ABSENT is the contract. The service read returns persisted values verbatim, and
    the resolved prompt in particular is stored UN-redacted (`_store_prompt` writes through
    `store.write_output`, not the redacting journal path). So every reconstructability field
    is routed through `journal.redact` — the SAME recursive redactor the journal writer uses,
    reused rather than re-derived so the two cannot drift — before it leaves the process. A
    credential that reached this endpoint would be a credential shipped to a browser, a bug
    report, and (via the drawer) a screenshot.
    """
    from personalclaw.workflows import journal

    result = service.inspect_node(
        request.match_info.get("run_id", ""), request.match_info.get("node_id", "")
    )
    if not result.get("ok"):
        return _fail(result)
    # Redact only the reconstructability payload — not the run_id/node_id/state routing
    # fields, which are engine-controlled identifiers a redactor might otherwise mangle.
    redacted_keys = (
        "resolved_prompt",
        "resolved_inputs",
        "output",
        "attempts",
        "ledger_events",
    )
    safe = {
        k: (journal.redact(v) if k in redacted_keys else v) for k, v in result.items() if k != "ok"
    }
    return web.json_response(safe)


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


async def api_run_steer(request: web.Request) -> web.Response:
    """POST a mid-run steering instruction (LOOPS-EVOLUTION R14).

    Guarded and audited like any other run mutation: injecting an instruction into a
    running autonomous job changes what it does, and a change to an unattended run that
    leaves no audit trail is exactly the kind that is impossible to reconstruct later.
    """
    denied = _guard(request, "workflow_run_steer")
    if denied is not None:
        return denied
    run_id = request.match_info.get("run_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = service.steer_run(run_id, str((body or {}).get("text", "")))
    _audit(request, "workflow_run_steer", "success" if result.get("ok") else "failure", run_id)
    return _reply(result)


async def api_run_review(request: web.Request) -> web.Response:
    """GET this run's review findings, anchored against its workspace diff as it is right now.

    A READ, so it is not `_guard`ed as a mutation — but the anchor verdicts it returns are the
    thing the panel renders, and they are computed here rather than stored, so a stale finding
    can never render as truth (EXECUTION-ISOLATION §7).
    """
    return _reply(await review_findings(request.match_info.get("run_id", "")))


async def api_run_review_triage(request: web.Request) -> web.Response:
    """POST accept/reject decisions; dispatch the accepted subset to the originating worker.

    Guarded and audited like every other run mutation: this is the endpoint that can change what
    a running autonomous worker does next, and it is the one place a review finding becomes an
    instruction. `dry_run: true` computes the same triage and delivers nothing.
    """
    denied = _guard(request, "workflow_run_review_triage")
    if denied is not None:
        return denied
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    run_id = request.match_info.get("run_id", "")
    result = await apply_triage(
        run_id,
        body.get("decisions"),
        dispatch=not strict_bool(body.get("dry_run"), field="dry_run", default=False),
    )
    _audit(
        request,
        "workflow_run_review_triage",
        "success" if result.get("ok") else "failure",
        run_id,
    )
    return _reply(result)


async def api_run_steering(request: web.Request) -> web.Response:
    """GET what is queued but unconsumed — so the UI can show it as pending.

    A queued instruction the user cannot see is indistinguishable from one that was
    dropped, and they will queue it again.
    """
    denied = _guard(request, "workflow_run_status")
    if denied is not None:
        return denied
    return _reply(service.pending_steering(request.match_info.get("run_id", "")))


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
        always_allow=strict_bool(body.get("always_allow"), field="always_allow"),
    )
    _audit(request, "workflow_run_resume", "success" if result.get("ok") else "failure", run_id)
    return _reply(result)


async def api_run_confirm(request: web.Request) -> web.Response:
    """Resolve a pending confirmation by verb — the seam the DagView's Approve/Deny binds to.

    Guarded by the same operation as `resume`, deliberately: this IS a resume with a verb
    vocabulary on top, and a separate permission would let a caller who may not answer a gate
    answer it through the other door.
    """
    denied = _guard(request, "workflow_run_resume")
    if denied is not None:
        return denied
    run_id = request.match_info.get("run_id", "")
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    result = service.resolve_confirmation(
        run_id,
        supervisor=_supervisor(request),
        verb=str(body.get("verb", "") or ""),
        token=str(body.get("resume_token", "") or ""),
        note=str(body.get("note", "") or ""),
    )
    _audit(
        request,
        "workflow_run_confirm",
        "success" if result.get("ok") else "failure",
        f"{run_id}:{body.get('verb', '')}",
    )
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
    app.router.add_get("/api/workflows/attention", api_attention)

    # Runs — before the def wildcard.
    app.router.add_get("/api/workflows/runs", api_runs_list)
    app.router.add_post("/api/workflows/runs", api_run_start)
    app.router.add_get("/api/workflows/runs/{run_id}", api_run_status)
    app.router.add_delete("/api/workflows/runs/{run_id}", api_run_delete)
    app.router.add_get("/api/workflows/runs/{run_id}/events", api_run_events)
    app.router.add_get("/api/workflows/runs/{run_id}/continuations", api_run_continuations)
    app.router.add_get("/api/workflows/runs/{run_id}/workspace", api_run_workspace)
    app.router.add_get("/api/workflows/runs/{run_id}/drop", api_run_drop_status)
    app.router.add_post("/api/workflows/runs/{run_id}/drop", api_run_drop)
    app.router.add_get("/api/workflows/runs/{run_id}/outbox", api_run_outbox)
    app.router.add_get("/api/workflows/runs/{run_id}/introspect", api_run_introspect)
    app.router.add_get("/api/workflows/runs/{run_id}/outputs/{node_id}", api_run_output)
    app.router.add_get("/api/workflows/runs/{run_id}/nodes/{node_id}/inspect", api_run_node_inspect)
    app.router.add_post("/api/workflows/runs/{run_id}/edit", api_run_edit)
    app.router.add_post("/api/workflows/runs/{run_id}/cancel", api_run_cancel)
    app.router.add_post("/api/workflows/runs/{run_id}/pause", api_run_pause)
    app.router.add_post("/api/workflows/runs/{run_id}/resume", api_run_resume)
    app.router.add_post("/api/workflows/runs/{run_id}/confirm", api_run_confirm)
    app.router.add_post("/api/workflows/runs/{run_id}/steer", api_run_steer)
    app.router.add_get("/api/workflows/runs/{run_id}/steering", api_run_steering)
    app.router.add_get("/api/workflows/runs/{run_id}/review", api_run_review)
    app.router.add_post("/api/workflows/runs/{run_id}/review/triage", api_run_review_triage)
    app.router.add_post("/api/workflows/runs/{run_id}/rewind", api_run_rewind)
    app.router.add_post("/api/workflows/runs/{run_id}/run-from", api_run_from)
    app.router.add_post("/api/workflows/runs/{run_id}/fork", api_run_fork)

    # Definitions. `surfacing` is a literal path and MUST precede `/{name}`, or a request for it
    # would match the def-detail route and look for a definition named "surfacing" — the same
    # ordering hazard this function's docstring records for `/runs`.
    app.router.add_get("/api/workflows/surfacing", api_defs_surfacing)
    app.router.add_get("/api/workflows", api_defs_list)
    app.router.add_post("/api/workflows", api_def_save)
    # Versions + refiner (WF2LEA-6). These carry a segment AFTER `{name}`, so they do not collide
    # with the one-segment def-detail/delete routes below; registered here beside their siblings.
    app.router.add_get("/api/workflows/{name}/versions", api_def_versions)
    app.router.add_get("/api/workflows/{name}/versions/diff", api_def_version_diff)
    app.router.add_post("/api/workflows/{name}/versions/repin", api_def_repin)
    app.router.add_get("/api/workflows/{name}/ledger", api_def_ledger)
    app.router.add_post("/api/workflows/{name}/refine", api_def_refine)
    app.router.add_post("/api/workflows/{name}/a2a-publish", api_def_a2a_publish)
    app.router.add_get("/api/workflows/{name}", api_def_detail)
    app.router.add_get("/api/workflows/{name}/trajectory", api_template_trajectory)
    app.router.add_delete("/api/workflows/{name}", api_def_delete)
