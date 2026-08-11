"""Suggested-organization endpoints (SESSION-MANAGEMENT T2.1).

Three routes over :mod:`personalclaw.session_organize`: read a suggestion, accept it,
decline it. The GET is the chip's data source and is strictly read-only — a user who
opens a chat and never touches the chip leaves the session exactly as it was.

Uses the INTEGRATION-ARCHITECTURE §2.2 error envelope (``{"error": {"code", "message"}}``).
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw import session_organize
from personalclaw.dashboard.chat_persistence import resolve_session
from personalclaw.dashboard.state import DashboardState
from personalclaw.sel import sel

logger = logging.getLogger(__name__)


def _err(message: str, code: str = "bad_request", status: int = 400) -> web.Response:
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


async def api_session_organize_suggest(request: web.Request) -> web.Response:
    """GET /api/chat/sessions/{session}/organize — the proposal, or ``{"proposal": null}``.

    Read-only by construction: it calls ``propose_for_session``, which computes and returns
    a proposal and writes nothing. ``llm=0`` restricts it to the deterministic signals,
    which is what a list-view caller wants — no model roundtrip per row.
    """
    state: DashboardState = request.app["state"]
    session = resolve_session(state, request.match_info["session"])
    if not session:
        return _err("session not found", code="not_found", status=404)
    allow_llm = request.query.get("llm", "1") not in ("0", "false", "no")
    proposal = await session_organize.propose_for_session(state, session, allow_llm=allow_llm)
    return web.json_response({"proposal": proposal.to_dict() if proposal else None})


async def _proposal_from_body(
    request: web.Request, state: DashboardState
) -> tuple[object, session_organize.OrganizeProposal | None, web.Response | None]:
    """Resolve the session and rebuild the proposal the client is answering.

    The client echoes back the proposal it was shown rather than the server re-deriving it:
    re-deriving would let the applied value differ from the value the user actually saw
    (the vocabulary can change between the GET and the click). Every field is still
    validated at apply time against the live folder/tag lists, so echoing is not trust.
    """
    session = resolve_session(state, request.match_info["session"])
    if not session:
        return None, None, _err("session not found", code="not_found", status=404)
    try:
        body = await request.json()
    except Exception:
        return None, None, _err("invalid JSON body")
    if not isinstance(body, dict):
        return None, None, _err("body must be an object")
    raw_tags = body.get("tags")
    tag_names = (
        [str(t) for t in raw_tags if isinstance(t, str) and t] if isinstance(raw_tags, list) else []
    )
    proposal = session_organize.OrganizeProposal(
        session_key=str(getattr(session, "key", "")),
        folder_id=str(body.get("folder_id") or ""),
        folder_name=str(body.get("folder_name") or ""),
        tag_names=tag_names,
        source=str(body.get("source") or ""),
    )
    if proposal.is_empty:
        return None, None, _err("proposal must name a folder or at least one tag")
    return session, proposal, None


async def api_session_organize_accept(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/organize/accept — apply the proposal.

    This is the ONLY path that mutates folder/tags from a proposal, and it exists behind an
    explicit user click.
    """
    state: DashboardState = request.app["state"]
    session, proposal, err = await _proposal_from_body(request, state)
    if err is not None:
        return err
    assert proposal is not None  # narrowed by err is None
    applied = session_organize.apply_proposal(state, session, proposal)
    session_organize.resolve_inbox_item(state, proposal, "handled")
    sel().log_api_access(
        caller="dashboard",
        operation="chat.session_organize_accept",
        outcome="allowed",
        source="dashboard",
        resources=proposal.session_key,
    )
    return web.json_response({"ok": True, **applied})


async def api_session_organize_decline(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/organize/decline — remember the refusal.

    Leaves the session untouched. Recording the decline is the whole point: without it the
    next scan would propose the same thing again, which is how a helpful suggestion becomes
    a nag.
    """
    state: DashboardState = request.app["state"]
    _session, proposal, err = await _proposal_from_body(request, state)
    if err is not None:
        return err
    assert proposal is not None  # narrowed by err is None
    session_organize.record_decline(proposal)
    session_organize.resolve_inbox_item(state, proposal, "dismissed")
    return web.json_response({"ok": True, "declined": True})
