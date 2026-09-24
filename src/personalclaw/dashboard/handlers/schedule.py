"""Lessons CRUD API handlers.

Schedule CRUD moved to the unified Trigger surface (dashboard/handlers/triggers.py);
this module now owns only the ``/api/lessons*`` endpoints. Every lesson read/write
goes through the memory service onto memory.db ``lesson.*`` records — there is no
JSONL fallback (the legacy JSONL lesson store was retired in WF2LEA-3).
``_get_memory`` always returns a store with an attached record layer (the gateway
attaches one; the API-only path attaches one in ``_get_memory``), so
``service_for(...).has_vector`` is always true here and lessons persist even with
no embedder configured.
"""

import json
import logging

from aiohttp import web

from personalclaw.dashboard.state import DashboardState

from ._shared import (
    _blocks_reads_session,
    _get_memory,
    _is_restricted_session,
    _session_has_persisted_history,
)

logger = logging.getLogger(__name__)


def _sel():
    """Late-binding _sel() for test monkeypatch compatibility."""
    import personalclaw.dashboard.handlers as _pkg  # noqa: F811

    return _pkg.sel()


async def api_lessons_create(request: web.Request) -> web.Response:
    """POST /api/lessons — add a lesson to memory.db ``lesson.*``."""
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    # Block lesson writes from restricted (incognito/temporary/guest) sessions.
    sk = request.headers.get("X-Session-Key", "")
    if not sk:
        _sel().log_api_access(
            caller="anonymous",
            operation="memory_remember",
            outcome="denied",
            source="dashboard",
            resources="missing_session_key",
        )
        return web.json_response({"error": "missing X-Session-Key"}, status=400)
    if sk != "dashboard:ui":
        session_name = sk.split(":", 1)[-1] if ":" in sk else sk
        in_sessions = session_name in state._sessions
        in_restricted = sk in state._restricted_keys
        is_channel_ns = sk.startswith("channel:")
        # Only consult the on-disk JSONL when the cheaper in-memory
        # checks all fail. ``_session_has_persisted_history()`` performs
        # synchronous filesystem I/O (up to two ``Path.exists()`` calls),
        # so evaluating it eagerly on every ``memory_remember`` request would
        # block the event loop on the common (live-session) path. Deferring
        # it keeps the fallback semantics identical while making the
        # happy path allocation-free.
        if not (in_sessions or in_restricted or is_channel_ns):
            if not _session_has_persisted_history(session_name):
                # Session may have been evicted from memory (idle sweep,
                # gateway restart) while the MCP subprocess keeps its
                # original PERSONALCLAW_SESSION_KEY env var. Ephemeral
                # (incognito/temporary) sessions never write JSONL, so
                # the absence of a session JSONL here means the key
                # genuinely does not belong to any established session.
                _sel().log_api_access(
                    caller=sk,
                    operation="memory_remember",
                    outcome="denied",
                    source="dashboard",
                    resources="unknown_session",
                )
                return web.json_response({"error": "unknown session"}, status=400)
            # JSONL-fallback is the sole reason the call is permitted.
            # Audit it as an allow decision so session-recovery
            # authorization is traceable alongside the deny path above.
            _sel().log_api_access(
                caller=sk,
                operation="memory_remember",
                outcome="allowed",
                source="dashboard",
                resources="jsonl_fallback_recovery",
            )
        elif in_sessions:
            # Live in-memory session — the common happy path. Audit so that
            # every ``memory_remember`` permission decision on this branch is
            # traceable (security-controls rule).
            _sel().log_api_access(
                caller=sk,
                operation="memory_remember",
                outcome="allowed",
                source="dashboard",
                resources="live_session",
            )
        elif in_restricted:
            _sel().log_api_access(
                caller=sk,
                operation="memory_remember",
                outcome="allowed",
                source="dashboard",
                resources="restricted_key",
            )
        else:  # is_channel_ns
            _sel().log_api_access(
                caller=sk,
                operation="memory_remember",
                outcome="allowed",
                source="dashboard",
                resources="channel_namespace",
            )
    else:
        # Browser UI's static key — implicitly trusted, but the allow
        # decision itself is still an authorization outcome and must be
        # audited (security-controls rule: every permission decision
        # emits a SEL event).
        _sel().log_api_access(
            caller=sk,
            operation="memory_remember",
            outcome="allowed",
            source="dashboard",
            resources="dashboard_ui",
        )
    if _is_restricted_session(state, request):
        sk = request.headers.get("X-Session-Key", "")
        logger.warning("Blocked memory_remember from restricted session %s", sk)
        _sel().log_api_access(
            caller=sk,
            operation="memory_remember",
            outcome="denied",
            source="dashboard",
            resources="restricted_session_block",
            error="Memory writes are not allowed in this session mode.",
        )
        return web.json_response(
            {"error": "Memory writes are not allowed in this session mode."},
            status=403,
        )
    rule = body.get("rule", "").strip()
    if not rule:
        return web.json_response({"error": "rule is required"}, status=400)
    # Injection gate (S5): a memory write is untrusted content that gets
    # re-injected into future prompts. Scan it with the shared scanner; a
    # dangerous verdict (e.g. a bidi-override or an embedded instruction-
    # override) is refused before it ever lands in the store.
    try:
        from personalclaw.supply_chain import Verdict, default_scanner

        report = default_scanner.scan_text(rule, surface="memory")
        if report.verdict is Verdict.DANGEROUS:
            cats = ", ".join(sorted({f.rule for f in report.findings})) or "dangerous content"
            _sel().log_api_access(
                caller=request.headers.get("X-Session-Key", ""),
                operation="memory_remember",
                outcome="denied",
                source="dashboard",
                resources="injection_scan",
                error=f"scanner flagged memory write: {cats}",
            )
            return web.json_response(
                {"error": f"memory write refused: scanner flagged dangerous content ({cats})"},
                status=400,
            )
    except Exception:
        logger.debug("memory-write injection scan failed (allowing)", exc_info=True)
    category = body.get("category", "knowledge")
    # Write through the memory service onto memory.db ``lesson.*``. The store is
    # always present (see module docstring), so no JSONL fallback is needed; a
    # store with no embedder still persists the lesson (vector optional).
    from personalclaw.memory_service import resolve_lesson_scope, service_for

    # REACH axis. Resolved HERE and not trusted from the caller: `mcp_memory`'s
    # memory_remember advertises scope=global|workspace and validates the pair
    # itself, but this endpoint is reachable without it, and for a while it simply
    # dropped both fields — a caller that carefully asked for a workspace lesson got
    # a global one and was told "ok". An unresolvable pair is refused, never widened:
    # storing a different scope than the one asked for is the defect, wherever it
    # happens.
    try:
        scope, scope_ref = resolve_lesson_scope(body.get("scope"), body.get("workspace"))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    negative = body.get("negative") or None
    svc = service_for(_get_memory(state))
    svc.write_lesson(rule, category, negative, scope=scope, scope_ref=scope_ref)
    state.push_refresh("lessons")
    return web.json_response({"ok": True})


async def api_lessons_delete(request: web.Request) -> web.Response:
    """DELETE /api/lessons — remove lessons by substring."""
    state: DashboardState = request.app["state"]
    # Block lesson deletes from temporary sessions only.
    # Incognito allows memory_forget (active user action).
    if _blocks_reads_session(state, request):
        sk = request.headers.get("X-Session-Key", "")
        _sel().log_api_access(
            caller=sk,
            operation="lessons.delete",
            outcome="denied",
            source="dashboard",
            resources=sk,
        )
        return web.json_response(
            {"error": "Memory writes are not allowed in this session mode."}, status=403
        )
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    rule_sub = body.get("rule", "").strip()
    if not rule_sub:
        return web.json_response({"error": "rule substring required"}, status=400)
    # Delete through the memory service onto memory.db ``lesson.*``.
    from personalclaw.memory_service import service_for

    svc = service_for(_get_memory(state))
    ok = svc.delete_lesson(rule_sub)
    if ok:
        state.push_refresh("lessons")
    return web.json_response({"ok": ok})


async def api_lessons(request: web.Request) -> web.Response:
    state: DashboardState = request.app["state"]
    # Block lesson reads only for temporary sessions (blocks_reads=True).
    # Incognito sessions can read lessons (memory context is already injected).
    if _blocks_reads_session(state, request):
        sk = request.headers.get("X-Session-Key", "")
        _sel().log_api_access(
            caller=sk,
            operation="lessons.list",
            outcome="denied",
            source="dashboard",
            resources=sk,
        )
        return web.json_response({"lessons": []})
    # Read through the memory service onto memory.db ``lesson.*``.
    from personalclaw.memory_service import resolve_lesson_scope, service_for

    svc = service_for(_get_memory(state))
    # `?workspace=` makes this the VISIBILITY view (global + that workspace); absent, it
    # is the full inventory across every scope. The list path takes the workspace
    # explicitly because the dashboard has no ambient working directory to infer one
    # from — the caller is the only thing that knows. Each row carries its own scope so
    # a workspace lesson never reads as a global one in a management surface.
    ws_param = request.query.get("workspace", "").strip()
    if ws_param:
        try:
            _scope, ws_ref = resolve_lesson_scope("workspace", ws_param)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        rows = svc.lessons_visible_in(ws_ref)
    else:
        rows = svc.get_lessons()
    data = []
    shown = rows[-50:]
    # Confidence + standing per row (WF2LEA-15). Read from the SAME derivation the
    # prompt filter uses, so "why is it still doing that" / "why did it stop doing
    # that" are answered with the number the gate actually compared rather than a
    # second estimate computed for the UI.
    standings = svc.lesson_standings(shown)
    for e in shown:
        try:
            rule = json.loads(e["value_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        verdict = standings.get(str(e.get("key") or ""))
        evidence = getattr(verdict, "evidence", None)
        data.append(
            {
                "rule": rule,
                "category": "knowledge",
                "ts": e.get("updated_at", ""),
                "scope": e.get("scope") or "global",
                "workspace": e.get("scope_ref") or "",
                "standing": getattr(getattr(verdict, "standing", None), "value", "injected"),
                "confidence": float(getattr(verdict, "confidence", 1.0)),
                "confidence_reason": str(getattr(verdict, "reason", "")),
                "observations": int(getattr(evidence, "observations", 0)),
                "contradictions": int(getattr(evidence, "contradictions", 0)),
                "reversals": int(getattr(evidence, "reversals", 0)),
            }
        )
    return web.json_response({"lessons": data})
