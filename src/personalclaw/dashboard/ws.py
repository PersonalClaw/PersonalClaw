"""WebSocket endpoint — multiplexes all real-time events over a single connection."""

import asyncio
import json
import logging

from aiohttp import WSMsgType, web

from personalclaw.dashboard.origin import check_origin
from personalclaw.dashboard.state import DashboardState
from personalclaw.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)


def _check_ws_origin(request: web.Request) -> None:
    """Reject cross-origin WebSocket upgrades.

    Browsers always send an Origin header on WebSocket handshakes.
    We allow only the dashboard's own origins and reject everything else,
    including missing Origin (non-browser clients are not expected).
    """
    if not check_origin(request, require=True):
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
