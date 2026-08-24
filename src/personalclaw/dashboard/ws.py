"""WebSocket endpoint — multiplexes all real-time events over a single connection."""

import asyncio
import json
import logging

from aiohttp import WSMsgType, web

from personalclaw.dashboard.origin import check_origin
from personalclaw.dashboard.state import DashboardState
from personalclaw.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)


def _paired_device_session(request: web.Request) -> str:
    """The paired-device id behind this request's session, or ``""``.

    FAIL-CLOSED by construction: every unknown answers ``""``. An absent nonce (no token
    middleware ran, or its payload would not decode), an unreadable session store, a session
    with no ``device`` row — all of them return "not a device", and the caller then applies the
    strict origin rule. There is no branch here that can widen on an error.

    Reads the store on each call, which is a **once-per-connection** cost: this runs on the
    `/api/ws` upgrade, not on the messages that follow.
    """
    nonce = request.get("session_nonce") or ""
    if not isinstance(nonce, str) or not nonce:
        return ""
    try:
        from personalclaw.dashboard.session_store import device_sessions

        record = device_sessions().get(nonce)
    except Exception:  # noqa: BLE001 — an unreadable registry cannot vouch for anything
        logger.warning("ws: device registry unreadable; applying the strict origin rule")
        return ""
    if record is None or record.device is None:
        return ""
    return str(record.device.id or "")


def _check_ws_origin(request: web.Request) -> None:
    """Reject cross-origin WebSocket upgrades.

    Browsers always send an Origin header on WebSocket handshakes, so the allowlist is the
    rule for anything that presents one, and that path is unchanged.

    **A NATIVE client presents no Origin at all (CA-7).** A desktop or mobile shell that opens
    the socket itself — rather than loading the SPA into a WebView — has no document origin to
    send. Refusing it was not buying protection, and that is measurable rather than arguable:
    the check only constrains clients that *cannot* choose their own headers. Any non-browser
    caller that wants past it today simply sends ``Origin: http://localhost:{port}``, which is
    in the allowlist unconditionally. So the old rule stopped honest native clients and nobody
    else.

    What replaces it is narrower than an origin exemption and stronger than the header it
    trusts: the request must carry **no Origin at all** AND be authorized by a session the
    owner deliberately paired (a ``sessions.json`` row with a ``device``). That session is
    revocable per-device from Settings → Devices, its ``last_seen`` is stamped on every
    authorized request, and a revoked row stops authenticating upstream in the token
    middleware — so this admission is attributable and reversible in a way a widened origin
    list would not be.

    **No new origin exemption:** ``build_allowed_origins`` is untouched and the allowed set is
    byte-identical. A client that DOES send an Origin still has to be in it, device session or
    not — a paired device gets no help forging an origin it does not have.
    """
    if check_origin(request, require=True):
        return
    # Deliberate order: prove the absence of an Origin FIRST, so a present-but-disallowed
    # origin can never reach the device branch.
    if not request.headers.get("Origin"):
        device_id = _paired_device_session(request)
        if device_id:
            logger.info("ws: origin-less upgrade admitted for paired device %s", device_id)
            return
    raise web.HTTPForbidden(text="WebSocket origin not allowed")


async def api_ws(request: web.Request) -> web.WebSocketResponse:
    """GET /api/ws — single multiplexed WebSocket for all real-time events."""
    _check_ws_origin(request)

    from personalclaw.dashboard.handlers import _log_ring

    state: DashboardState = request.app["state"]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    # request["app"] is set by the token middleware when the handshake carried an
    # app-scoped token (?app_token=…). Scope this connection so broadcast_ws filters
    # its events to the app's declared permissions.events (untrusted-app sandbox P1).
    state.register_ws(ws, app=request.get("app", ""))

    # Push current sessions immediately so sidebar populates without waiting
    try:
        sessions_data = [s.to_dict() for s in state._sessions.values()]
        await ws.send_json(
            {"type": "sessions", "data": sessions_data, "yolo": state.is_yolo_active()}
        )
    except Exception:
        pass

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get("type", "")
                    if msg_type == "subscribe_logs":
                        state.subscribe_logs(ws)
                        # Replay log ring buffer
                        for entry in list(_log_ring):
                            try:
                                parsed = json.loads(entry)
                                await ws.send_json({"type": "log", "data": parsed})
                            except Exception:
                                pass
                    elif msg_type == "unsubscribe_logs":
                        state.unsubscribe_logs(ws)
                    elif msg_type == "subscribe_subagents":
                        state.subscribe_subagents(ws)
                        # Send snapshot of active subagents + done events for completed ones
                        if state.subagents:

                            def _r(t: str) -> str:
                                t, _ = redact_exfiltration_urls(t)
                                t, _ = redact_credentials(t)
                                return t

                            for a in state.subagents.running:
                                try:
                                    session = a.parent_session_key.removeprefix("dashboard:")
                                    await ws.send_json(
                                        {
                                            "type": "subagent_snapshot",
                                            "data": {
                                                "id": a.id,
                                                "session": session,
                                                "task": _r(a.task),
                                                "agent": _r(a.agent),
                                                "streaming": _r(a.streaming_text),
                                                "last_tool": _r(a.last_tool),
                                                "started": a.started,
                                            },
                                        }
                                    )
                                except Exception:
                                    pass
                            # Send done events for completed subagents so
                            # reconnecting clients can transition stale cards.
                            for a in state.subagents.all_agents:
                                if not a.done:
                                    continue
                                session = a.parent_session_key.removeprefix("dashboard:")
                                try:
                                    await ws.send_json(
                                        {
                                            "type": "subagent_done",
                                            "data": {
                                                "id": a.id,
                                                "session": session,
                                                "elapsed": a.elapsed,
                                                "error": _r(a.error) if a.error else None,
                                                "task": _r(a.task),
                                                "agent": _r(a.agent),
                                            },
                                        }
                                    )
                                except Exception:
                                    pass
                    elif msg_type == "unsubscribe_subagents":
                        state.unsubscribe_subagents(ws)
                except Exception:
                    pass
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    except (asyncio.CancelledError, Exception):
        pass
    finally:
        state.unsubscribe_logs(ws)
        state.unsubscribe_subagents(ws)
        state.unregister_ws(ws)
    return ws
