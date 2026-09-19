"""Suggested-organization endpoints (SESSION-MANAGEMENT T2.1).

Three routes over :mod:`personalclaw.session_organize`: read a suggestion, accept it,
decline it. The GET is the chip's data source and is strictly read-only — a user who
opens a chat and never touches the chip leaves the session exactly as it was.

Uses the `AGENTS.md` §"Shared conventions" error envelope
(``{"error": {"code", "message"}}``), emitted by
:func:`personalclaw.http_errors.json_error`.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from personalclaw import session_organize
from personalclaw.dashboard.chat_persistence import resolve_session
from personalclaw.dashboard.state import DashboardState
from personalclaw.http_errors import json_error
from personalclaw.request_validation import json_object_body, string_field
from personalclaw.sel import sel

logger = logging.getLogger(__name__)


async def api_session_organize_suggest(request: web.Request) -> web.Response:
    """GET /api/chat/sessions/{session}/organize — the proposal, or ``{"proposal": null}``.

    Read-only by construction: it calls ``propose_for_session``, which computes and returns
    a proposal and writes nothing. ``llm=0`` restricts it to the deterministic signals,
    which is what a list-view caller wants — no model roundtrip per row.
    """
    state: DashboardState = request.app["state"]
    session = resolve_session(state, request.match_info["session"])
    if not session:
        return json_error("not_found", message="session not found", status=404)
    allow_llm = request.query.get("llm", "1") not in ("0", "false", "no")
    proposal = await session_organize.propose_for_session(state, session, allow_llm=allow_llm)
    return web.json_response({"proposal": proposal.to_dict() if proposal else None})


def _proposal_from_body(
    request: web.Request, state: DashboardState, body: dict[str, Any]
) -> tuple[object, session_organize.OrganizeProposal | None, web.Response | None]:
    """Resolve the session and rebuild the proposal the client is answering.

    The client echoes back the proposal it was shown rather than the server re-deriving it:
    re-deriving would let the applied value differ from the value the user actually saw
    (the vocabulary can change between the GET and the click). Every field is still
    validated at apply time against the live folder/tag lists, so echoing is not trust.

    Takes the already-read *body* and is therefore SYNC: reading the request here made this
    the module's own body reader, with its own two spellings of the malformed-body refusal.
    Both moved up to :func:`json_object_body`, which is why the two remaining early returns
    are the ones genuinely specific to this route (unknown session, empty proposal).
    """
    session = resolve_session(state, request.match_info["session"])
    if not session:
        return None, None, json_error("not_found", message="session not found", status=404)
    raw_tags = body.get("tags")
    tag_names = (
        [str(t) for t in raw_tags if isinstance(t, str) and t] if isinstance(raw_tags, list) else []
    )
    # `string_field`, not `str(body.get(...) or "")`: the old coercion could not fail, so
    # `{"folder_name": {"a": "b"}}` echoed back Python's repr as the folder to apply —
    # #3001's defect on the organize path. A non-string is now refused by name.
    proposal = session_organize.OrganizeProposal(
        session_key=str(getattr(session, "key", "")),
        folder_id=string_field(body, "folder_id"),
        folder_name=string_field(body, "folder_name"),
        tag_names=tag_names,
        source=string_field(body, "source"),
    )
    if proposal.is_empty:
        return (
            None,
            None,
            json_error(
                "bad_request", message="proposal must name a folder or at least one tag", status=400
            ),
        )
    return session, proposal, None


async def api_session_organize_accept(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/organize/accept — apply the proposal.

    This is the ONLY path that mutates folder/tags from a proposal, and it exists behind an
    explicit user click.
    """
    state: DashboardState = request.app["state"]
    session, proposal, err = _proposal_from_body(request, state, await json_object_body(request))
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
    _session, proposal, err = _proposal_from_body(request, state, await json_object_body(request))
    if err is not None:
        return err
    assert proposal is not None  # narrowed by err is None
    session_organize.record_decline(proposal)
    session_organize.resolve_inbox_item(state, proposal, "dismissed")
    return web.json_response({"ok": True, "declined": True})
