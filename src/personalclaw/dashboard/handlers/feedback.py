"""Feedback Signal routes (plan 58) — capture + attribution + threshold actions.

POST /api/feedback                      record one verdict (👍/👎)
GET  /api/feedback/target/{kind}/{id}   current verdict (FE hydration)
GET  /api/feedback/producers            per-producer accuracy (min-N filtered)
POST /api/feedback/producers/snooze     30-day snooze for one producer
POST /api/feedback/producers/clear      un-suppress after the user edits the artifact

The ``feedback.enabled`` kill-switch 404s every route (thumbs never render when
off — the FE hides on config, this is the backend rail). An app-scoped token's
records get ``source_app`` stamped server-side from ``request["app"]`` and the
producer forcibly namespaced to ``("app", "<app>:<producer>")`` — an app can
never impersonate a core producer.
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw import feedback as fb

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    try:
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().feedback.enabled)
    except Exception:  # noqa: BLE001 — a config fault must not kill the surface
        return True


def _disabled_response() -> web.Response:
    return web.json_response(
        {"error": {"code": "disabled", "message": "feedback is disabled in Settings"}},
        status=404,
    )


async def api_feedback_record(request: web.Request) -> web.Response:
    """POST /api/feedback — record one verdict."""
    if not _enabled():
        return _disabled_response()
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": {"code": "bad_request", "message": "invalid JSON body"}}, status=400
        )
    if not isinstance(body, dict):
        return web.json_response(
            {"error": {"code": "bad_request", "message": "body must be an object"}}, status=400
        )

    target_kind = str(body.get("target_kind", ""))
    target_id = str(body.get("target_id", ""))
    verdict = str(body.get("verdict", ""))
    if target_kind not in fb.TARGET_KINDS:
        return web.json_response(
            {"error": {"code": "bad_request", "message": f"unknown target_kind {target_kind!r}"}},
            status=400,
        )
    if not target_id:
        return web.json_response(
            {"error": {"code": "bad_request", "message": "target_id required"}}, status=400
        )
    if verdict not in ("up", "down"):
        return web.json_response(
            {"error": {"code": "bad_request", "message": "verdict must be 'up' or 'down'"}},
            status=400,
        )

    producer_kind = str(body.get("producer_kind", ""))
    producer_id = str(body.get("producer_id", ""))
    # App callers: source_app is stamped server-side (never client-claimed) and
    # the producer is forced into the app namespace.
    source_app = str(request.get("app") or "")
    if source_app:
        producer_id = f"{source_app}:{producer_id or 'default'}"
        producer_kind = "app"
        target_kind = "app_judgment" if target_kind not in fb.TARGET_KINDS else target_kind

    snapshot = body.get("snapshot")
    rec = fb.record_feedback(
        target_kind=target_kind,
        target_id=target_id,
        verdict=verdict,
        reason=str(body.get("reason", "") or ""),
        snapshot=snapshot if isinstance(snapshot, dict) else None,
        producer_kind=producer_kind,
        producer_id=producer_id,
        source_app=source_app,
        session_key=str(body.get("session_key", "") or ""),
        state=request.app.get("state"),
    )
    if rec is None:
        return web.json_response(
            {"error": {"code": "internal", "message": "feedback could not be recorded"}},
            status=500,
        )
    return web.json_response({"ok": True, "id": rec.id, "verdict": rec.verdict})


async def api_feedback_target(request: web.Request) -> web.Response:
    """GET /api/feedback/target/{kind}/{id} — the current verdict for hydration."""
    if not _enabled():
        return _disabled_response()
    kind = request.match_info.get("kind", "")
    target_id = request.match_info.get("id", "")
    rec = fb.current_verdict(kind, target_id)
    if rec is None:
        return web.json_response({"verdict": None})
    return web.json_response({"verdict": rec.verdict, "reason": rec.reason, "id": rec.id})


async def api_feedback_producers(request: web.Request) -> web.Response:
    """GET /api/feedback/producers?window_days=N — per-producer accuracy.

    Rows below min_n carry ``"collecting": true`` and no accuracy number (the
    honest-counts rule: nothing is shown before the sample is meaningful).
    """
    if not _enabled():
        return _disabled_response()
    try:
        window_days = int(request.query.get("window_days", 0)) or None
    except ValueError:
        window_days = None
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load().feedback
    suppressed = fb.suppressed_producers()
    rows = []
    for (kind, pid), row in sorted(fb.producer_stats(window_days=window_days).items()):
        entry: dict = {
            "producer_kind": kind,
            "producer_id": pid,
            "ups": row["ups"],
            "downs": row["downs"],
            "n": row["n"],
        }
        if row["n"] >= cfg.min_n:
            entry["accuracy"] = round(row["accuracy"], 3)
            entry["suppressed"] = (kind, pid) in suppressed
        else:
            entry["collecting"] = True
        rows.append(entry)
    return web.json_response(
        {"producers": rows, "min_n": cfg.min_n, "window_days": window_days or cfg.window_days}
    )


def _producer_body(body: dict) -> tuple[str, str] | None:
    kind = str(body.get("producer_kind", ""))
    pid = str(body.get("producer_id", ""))
    if not kind or not pid:
        return None
    return kind, pid


async def api_feedback_snooze(request: web.Request) -> web.Response:
    """POST /api/feedback/producers/snooze — 30-day snooze for one producer."""
    if not _enabled():
        return _disabled_response()
    try:
        body = await request.json()
    except Exception:
        body = {}
    parsed = _producer_body(body if isinstance(body, dict) else {})
    if parsed is None:
        return web.json_response(
            {"error": {"code": "bad_request", "message": "producer_kind + producer_id required"}},
            status=400,
        )
    fb.snooze_producer(*parsed)
    return web.json_response({"ok": True})


async def api_feedback_clear(request: web.Request) -> web.Response:
    """POST /api/feedback/producers/clear — un-suppress after an artifact edit."""
    if not _enabled():
        return _disabled_response()
    try:
        body = await request.json()
    except Exception:
        body = {}
    parsed = _producer_body(body if isinstance(body, dict) else {})
    if parsed is None:
        return web.json_response(
            {"error": {"code": "bad_request", "message": "producer_kind + producer_id required"}},
            status=400,
        )
    fb.clear_producer(*parsed)
    return web.json_response({"ok": True})


def register_feedback_routes(app: web.Application) -> None:
    app.router.add_post("/api/feedback", api_feedback_record)
    app.router.add_get("/api/feedback/target/{kind}/{id}", api_feedback_target)
    app.router.add_get("/api/feedback/producers", api_feedback_producers)
    app.router.add_post("/api/feedback/producers/snooze", api_feedback_snooze)
    app.router.add_post("/api/feedback/producers/clear", api_feedback_clear)
