"""The browse mirror + kill-switch + per-task-grant HTTP routes (BA-5, BA-9).

The aiohttp surface the browse panel talks to: the read model it polls (``GET
/api/browse/status``), the kill switch (``POST /api/browse/kill`` + ``/release``) and the human's
answer to a pending per-task grant (``POST /api/browse/grants/{request_id}/{action}``). Owner-
authenticated by the ordinary dashboard middleware, like every other ``/api/*`` route here.

The relays these routes drive — the ``browse_step`` / ``browse_kill`` / ``browse_auth_expired`` /
``browse_grant`` WS broadcasts and the expired-session surfacing — live one layer DOWN in
:mod:`personalclaw.browse.mirror`, so the domain (the action provider) can call them without
importing this HTTP module. This module imports DOWN into that one (the allowed direction);
:func:`broadcast_kill` is re-driven here only so a kill issued over HTTP updates the panel at once.

**The grant routes reach the EXISTING gate; they do not mint a second one.** The pending-grant
store and the fail-closed :class:`~personalclaw.agents.native.approval.ApprovalGate` both live in
:mod:`personalclaw.browse.grant`, whose ``request_grant`` is the only writer. These routes are a
read (:func:`~personalclaw.browse.grant.pending_grants`) and two resolvers
(:func:`~personalclaw.browse.grant.approve_grant` / ``reject_grant``) over that one store, so there
is no second copy of "what is waiting" to drift. They are deliberately NOT folded into
``GET /api/approvals`` / ``POST /api/approvals/{id}/{action}``: that pair reads
``DashboardState._pending_approvals``, the native-session TOOL-approval dict, whose rows are keyed
by tool + tool_input and whose timeout is origin-aware — routing a browse grant through it would
let ``_approval_timeout_for`` silently redefine the 300s ceiling this control declares, and would
put a browse grant's site scope into a row shape that has nowhere to keep it.
"""

from __future__ import annotations

from aiohttp import web

from personalclaw.browse.mirror import broadcast_kill
from personalclaw.http_errors import json_error
from personalclaw.request_validation import json_object_body
from personalclaw.safety_flags import confirm_granted

#: The two answers a human may give a pending grant. A closed set, matched against the path segment,
#: so an unknown verb is a 400 rather than something that quietly resolves the gate either way.
_GRANT_ACTIONS = ("approve", "reject")


async def api_browse_status(request: web.Request) -> web.Response:
    """GET /api/browse/status — the mirror's read model: kill state, expired sites, pending grants.

    One read so the panel's kill button, its persistent banner and its grant prompt cannot show a
    stale set against each other — a browse that was killed while a grant is pending is a real pair,
    and two reads could disagree about it. It is also why the pending grants ride here rather than
    on a ``GET /api/browse/grants`` of their own: a second read of the same store is how the two
    drift, and the panel already refetches THIS one on reconnect and on every browse signal.

    Values never cross this boundary — ``expired`` carries site slugs and a key-PRESENCE boolean,
    never the profile-encryption key itself; a grant carries its task label, host scope and the
    fail-closed deadline, never a credential, cookie or token (§5.2).
    """
    from personalclaw.browse import killswitch
    from personalclaw.browse.grant import pending_grants
    from personalclaw.browse.handoff import expired_sites

    kill = killswitch.get_kill()
    return web.json_response(
        {
            "kill": {"active": kill.active, "reason": kill.reason, "started_at": kill.started_at},
            "expired": expired_sites(),
            "grants": pending_grants(),
        }
    )


async def api_browse_grant_resolve(request: web.Request) -> web.Response:
    """POST /api/browse/grants/{request_id}/{action} — answer one pending per-task browse grant.

    ``action`` is ``approve`` or ``reject``. Resolves the waiting
    :class:`~personalclaw.agents.native.approval.ApprovalGate` future in
    :mod:`personalclaw.browse.grant`, which is what lets the authorized run proceed (or refuses it
    at once instead of making the operator wait out the 300s ceiling).

    404 when nothing is waiting on that id — the honest answer for a grant that already resolved,
    timed out, or never existed, and NOT an error the caller should retry. Unlike the kill-switch
    release this needs no ``{confirm: true}``: the request IS the confirmation, a reject is the
    fail-closed direction, and an approve is only reachable with the owner's own credentials.

    NOT separately SEL-audited. :func:`~personalclaw.browse.grant.request_grant` writes the
    ``browser_grant`` decision row when the gate resolves, so a row here would double-count one
    answer — and the audit trail would then show two events for one human decision.
    """
    from personalclaw.browse.grant import approve_grant, reject_grant

    action = request.match_info["action"]
    if action not in _GRANT_ACTIONS:
        return json_error(
            "browse_grant_action_invalid",
            status=400,
            message=f"action must be one of {', '.join(_GRANT_ACTIONS)}",
        )
    request_id = str(request.match_info["request_id"] or "").strip()
    resolved = approve_grant(request_id) if action == "approve" else reject_grant(request_id)
    if not resolved:
        return json_error(
            "browse_grant_not_pending",
            status=404,
            message="no browse grant is waiting on that id — it may have already been answered",
        )
    return web.json_response({"ok": True, "request_id": request_id, "action": action})


async def api_browse_kill(request: web.Request) -> web.Response:
    """POST /api/browse/kill — stop unattended browsing. Body: ``{reason?: str}``.

    SEL-audited in :func:`killswitch.engage`. A running loop parks within one step; a new run
    refuses to start. Interactive chat is untouched.
    """
    from personalclaw.browse import killswitch

    body = await json_object_body(request)
    reason = str(body.get("reason", "")) if isinstance(body, dict) else ""
    kill = killswitch.engage(reason)
    broadcast_kill(kill, state=request.app.get("state"))
    return web.json_response(
        {"kill": {"active": kill.active, "reason": kill.reason, "started_at": kill.started_at}}
    )


async def api_browse_kill_release(request: web.Request) -> web.Response:
    """POST /api/browse/kill/release — re-enable unattended browsing.

    EXPLICIT, like incident resume: requires ``{confirm: true}`` so a stray request cannot silently
    re-enable browsing a human deliberately stopped. SEL-audited.
    """
    from personalclaw.browse import killswitch

    body = await json_object_body(request)
    if not confirm_granted(body):
        return json_error(
            "confirmation_required", message='release requires {"confirm": true}', status=400
        )
    kill = killswitch.release()
    broadcast_kill(kill, state=request.app.get("state"))
    return web.json_response(
        {"kill": {"active": kill.active, "reason": kill.reason, "started_at": kill.started_at}}
    )


def register_browse_mirror_routes(app: web.Application) -> None:
    """Wire the browse mirror + kill-switch + grant-answer routes (owner-authenticated by the
    ordinary dashboard middleware, like the rest of ``/api/*``)."""
    app.router.add_get("/api/browse/status", api_browse_status)
    app.router.add_post("/api/browse/kill", api_browse_kill)
    app.router.add_post("/api/browse/kill/release", api_browse_kill_release)
    app.router.add_post("/api/browse/grants/{request_id}/{action}", api_browse_grant_resolve)


__all__ = [
    "api_browse_status",
    "api_browse_kill",
    "api_browse_kill_release",
    "api_browse_grant_resolve",
    "register_browse_mirror_routes",
]
