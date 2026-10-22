"""Whether the microphone is live, as the desktop shell says it (#3833).

A window reports to the shell when one of its capture sources holds the microphone: the composer's
dictation, hands-free, or the Knowledge recorder (``desktop/pushToTalk.js``). The shell tells every
window, and tells the gateway here, so the owner's pages hear it too: ``POST /api/listening`` with
``{"on", "sources"}``, which sends a ``listening`` frame when what it says changed, and
``GET /api/listening`` for a page that missed it. Listening is the shell's system state, with the
sources that hold the microphone, not a phase of any one chat: a dictation is not yet anyone's
message. A plain browser tab has no shell and reports nothing.

Both routes are the owner's (``apps.permissions.OWNER_ONLY_API_PATHS``), and the frame reaches
the owner's sockets only (``ws_state.OWNER_FRAMES``): what the microphone is doing is never an
app's to hear.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.instants import utc_now_iso

#: The capture sources a window may report.
CAPTURE_SOURCES = frozenset({"dictation", "hands-free", "recorder"})

#: The frame the owner's pages hear it on.
FRAME = "listening"

_IDLE: dict[str, Any] = {"on": False, "sources": [], "since": None}


def listening_of(state: Any) -> dict[str, Any]:
    """What the shell last said: ``{on, sources, since}``; not listening when it said nothing."""
    return dict(getattr(state, "_listening", None) or _IDLE)


async def api_listening(request: web.Request) -> web.Response:
    """GET /api/listening — whether the microphone is live, and which sources hold it."""
    return web.json_response(listening_of(request.app["state"]))


async def api_listening_report(request: web.Request) -> web.Response:
    """POST /api/listening — the shell's report: ``{"on": bool, "sources": [...]}``."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — any unreadable body is the same refusal
        body = None
    on = body.get("on") if isinstance(body, dict) else None
    sources = body.get("sources", []) if isinstance(body, dict) else None
    if (
        not isinstance(on, bool)
        or not isinstance(sources, list)
        or not all(isinstance(s, str) and s in CAPTURE_SOURCES for s in sources)
    ):
        return json_error(
            "bad_request",
            message='Send {"on": true or false, "sources": [...]}, each source one of '
            + ", ".join(sorted(CAPTURE_SOURCES))
            + ".",
            status=400,
        )
    state = request.app["state"]
    said = sorted(set(sources)) if on else []
    was = listening_of(state)
    if was["on"] == on and was["sources"] == said:
        return web.json_response(was)
    now = {"on": on, "sources": said, "since": utc_now_iso()}
    state._listening = now
    state.broadcast_ws(FRAME, now)
    return web.json_response(now)


def register_listening_routes(app: web.Application) -> None:
    app.router.add_get("/api/listening", api_listening)
    app.router.add_post("/api/listening", api_listening_report)
