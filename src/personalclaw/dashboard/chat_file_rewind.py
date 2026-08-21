"""Filesystem rewind — ``/rewind-to-turn N`` (EXECUTION-ISOLATION §6).

**Naming, because "rewind" is already taken.** ``chat_regenerate``'s ``rewind: true``
(edit-resend, CHAT-CRAFT S1) rewinds the TRANSCRIPT. This module rewinds the FILES and
nothing else — hence ``chat_file_rewind``. The two never share state.

The deliberate other half of :mod:`personalclaw.dashboard.chat_undo`. ``/undo`` rolls back
the CONVERSATION and says in its own response that files written were not reverted; this
rolls back the FILES and does not touch the transcript. Keeping them separate is the point:
the transcript is the record of what happened, and a rewind that erased it would destroy the
evidence a user needs to decide whether the rewind was right.

Two routes, because a destructive action needs a readable preview before anything is written:

* ``GET  /api/chat/sessions/{session}/rewind?turn=N`` — read-only preview: exactly which
  files would be restored, deleted, or (honestly) *not* restored because they were never
  captured, with a unified diff per file and current-vs-restored hashes.
* ``POST /api/chat/sessions/{session}/rewind {turn, confirm: true}`` — apply. Without
  ``confirm: true`` this returns ``409 confirmation_required`` **and the preview**, so a
  client that forgets the flag gets the preview rather than a surprise write.
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw import turn_checkpoints
from personalclaw.dashboard.state import DashboardState
from personalclaw.http_errors import json_error
from personalclaw.sel import sel

logger = logging.getLogger(__name__)


def _resolve_session(request: web.Request, operation: str):
    """(session, error_response). Mirrors chat_undo's 404 + app-isolation 403."""
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    session = state._sessions.get(name)
    request_app = request.get("app", "")
    if not session:
        return None, json_error("not_found", message=f"no such session: {name}", status=404)
    if request_app and session._app != request_app:
        sel().log_api_access(
            caller=request_app,
            operation=operation,
            outcome="denied",
            source="app_isolation",
            resources=f"session={name}",
            error="app does not own this session",
        )
        return None, json_error("forbidden", message="app does not own this session", status=403)
    return session, None


def _turn_arg(raw: object) -> tuple[int, web.Response | None]:
    if raw is None or raw == "":
        return 0, json_error(
            "invalid_turn", message="turn is required (an integer >= 0)", status=400
        )
    # `True` is an int in Python; a JSON `true` for a turn number is a client bug, not
    # turn 1, so it is refused rather than coerced.
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return 0, json_error("invalid_turn", message="turn must be an integer", status=400)
    try:
        turn = int(raw)
    except (TypeError, ValueError):
        return 0, json_error("invalid_turn", message="turn must be an integer", status=400)
    if turn < 0:
        return 0, json_error("invalid_turn", message="turn must be an integer >= 0", status=400)
    return turn, None


async def api_chat_session_rewind_preview(request: web.Request) -> web.Response:
    """GET /api/chat/sessions/{session}/rewind?turn=N — what a rewind would do. Read-only."""
    session, err = _resolve_session(request, "chat.session_rewind_preview")
    if err is not None:
        return err
    turn, terr = _turn_arg(request.query.get("turn"))
    if terr is not None:
        return terr
    # Finish any rewind that died between staging and commit before reporting current
    # state — otherwise the preview would diff against a half-committed tree.
    resumed = turn_checkpoints.resume_incomplete_rewind(session.key)
    pv = turn_checkpoints.preview_rewind(session.key, turn)
    payload = pv.to_dict()
    payload["resumed"] = resumed["resumed"]
    payload["notice"] = (
        "Filesystem only. This does NOT rewind the conversation — the transcript stays as "
        "the record of what happened. Use /undo for that."
    )
    return web.json_response(payload)


async def api_chat_session_rewind(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/rewind — restore files to the end of turn N.

    Body: ``{turn: int, confirm: bool}``. ``confirm`` must be ``true``; anything else
    returns 409 with the preview attached.
    """
    session, err = _resolve_session(request, "chat.session_rewind")
    if err is not None:
        return err
    request_app = request.get("app", "")

    body: dict = {}
    if request.body_exists:
        try:
            parsed = await request.json()
        except Exception:
            return json_error("invalid_body", message="invalid JSON body", status=400)
        if isinstance(parsed, dict):
            body = parsed
    turn, terr = _turn_arg(body.get("turn"))
    if terr is not None:
        return terr

    # Never rewind under a live turn: the agent may be mid-write, and replacing a file it
    # is about to read produces a state neither side asked for.
    if getattr(session, "running", False):
        return json_error(
            "turn_running", message="cannot rewind while a turn is running", status=409
        )

    turn_checkpoints.resume_incomplete_rewind(session.key)
    pv = turn_checkpoints.preview_rewind(session.key, turn)
    if body.get("confirm") is not True:
        return json_error(
            "confirmation_required",
            message="a rewind overwrites files on disk — resend with confirm: true",
            status=409,
            preview=pv.to_dict(),
        )

    res = turn_checkpoints.apply_rewind(session.key, turn, preview=pv)
    sel().log_api_access(
        caller=request_app or "dashboard",
        operation="chat.session_rewind",
        outcome="success" if res.ok else "failure",
        source="dashboard",
        resources=(
            f"session={session.key},to_turn={turn},"
            f"restored={len(res.restored)},deleted={len(res.deleted)}"
        ),
        error="; ".join(res.errors)[:400],
    )
    payload = res.to_dict()
    payload["turn"] = turn
    payload["preview"] = pv.to_dict()
    payload["notice"] = (
        f"Restored {len(res.restored)} file(s) to their state at the end of turn {turn}. "
        "The conversation was NOT rewound. The files' previous contents were themselves "
        f"checkpointed as turn {res.safety_turn}, so this rewind is reversible."
    )
    if not res.ok:
        payload["error"] = {
            "code": "rewind_incomplete",
            "message": "; ".join(res.errors)[:400] or "rewind did not complete",
        }
        return web.json_response(payload, status=500)
    return web.json_response(payload)
