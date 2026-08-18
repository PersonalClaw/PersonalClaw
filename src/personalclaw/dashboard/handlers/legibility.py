"""Legibility endpoints — the Discover section + hub's data (Platform-Legibility §6).

``GET /api/legibility/discover`` returns the curated Discover tips still worth showing
(the hand-authored catalog minus dismissed tips and minus areas the user has already
engaged), grouped by area, or ``enabled: false`` when the ``legibility.discover_tips``
kill switch is off. ``POST /api/legibility/discover/dismiss`` persists a per-tip
dismissal so it never resurfaces. Propose-don't-write: neither endpoint ever enables
or configures anything on the user's behalf.

``GET /api/legibility/always-on`` is the always-on conventions viewer's data (PEP-10): every
``always: true`` skill and project-instruction doc a session receives unconditionally, with
provenance. ``GET /api/legibility/always-on/doc`` returns one body verbatim for the editor and
``PUT`` writes it back. The viewer slices the session's own producer strings rather than
re-deriving the always-on set — see ``legibility/always_on.py`` for why, including why a GET
here must not assemble a full session prompt.
"""

import logging

from aiohttp import web

from personalclaw.legibility.always_on import (
    InstructionWriteError,
    collect_always_on,
    read_instruction,
    write_instruction,
)
from personalclaw.legibility.discover import compute_discover, dismiss

logger = logging.getLogger(__name__)


async def api_discover(request: web.Request) -> web.Response:
    """GET /api/legibility/discover — the curated Discover tips still worth showing.

    Takes the hand-authored catalog, drops tips the user dismissed and areas they've
    already engaged (a cheap read of existing state), and returns the rest grouped by
    area. Never mutates state.
    """
    return web.json_response(compute_discover(request.app["state"]))


async def api_discover_dismiss(request: web.Request) -> web.Response:
    """POST /api/legibility/discover/dismiss — hide a Discover tip forever.

    Body: ``{"id": "<tip-id>"}``. Persists the dismissal in
    ``entity_settings/legibility.json`` and echoes the full dismissed set.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "Body must be a JSON object"}, status=400)
    tip_id = str(body.get("id", "")).strip()
    if not tip_id:
        return web.json_response({"error": "id is required"}, status=400)
    ids = dismiss(tip_id)
    return web.json_response({"ok": True, "dismissed": sorted(ids)})


async def api_always_on(request: web.Request) -> web.Response:
    """GET /api/legibility/always-on — what every session receives, with provenance.

    Query: ``project_id`` (optional — adds the project-instruction tier), ``agent`` (optional —
    resolves the agent-local skill tier the way that agent's turn would). Bodies are previewed
    credential-redacted here; the editor round-trip below serves them verbatim.
    """
    project_id = str(request.query.get("project_id", "")).strip()
    agent = str(request.query.get("agent", "")).strip() or None
    inventory = collect_always_on(project_id=project_id, agent=agent)
    return web.json_response(inventory.to_dict())


async def api_always_on_doc(request: web.Request) -> web.Response:
    """GET /api/legibility/always-on/doc?id=&project_id= — one body, verbatim, for the editor."""
    item_id = str(request.query.get("id", "")).strip()
    if not item_id:
        return web.json_response({"error": "id is required"}, status=400)
    project_id = str(request.query.get("project_id", "")).strip()
    agent = str(request.query.get("agent", "")).strip() or None
    try:
        item = read_instruction(item_id, project_id=project_id, agent=agent)
    except InstructionWriteError as exc:
        return web.json_response({"error": exc.reason}, status=exc.status)
    return web.json_response(item.to_dict(include_body=True))


async def api_always_on_doc_write(request: web.Request) -> web.Response:
    """PUT /api/legibility/always-on/doc — replace an editable project instruction.

    Body: ``{"id": "...", "project_id": "...", "body": "..."}``. A refused or failed write is an
    error response, never a silent success — the underlying store reports failure as a bare
    ``False`` and rendering "Saved" over a discarded edit is the failure this guards.
    """
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"error": "Body must be a JSON object"}, status=400)
    item_id = str(payload.get("id", "")).strip()
    if not item_id:
        return web.json_response({"error": "id is required"}, status=400)
    if "body" not in payload:
        return web.json_response({"error": "body is required"}, status=400)
    body = payload.get("body")
    if not isinstance(body, str):
        return web.json_response({"error": "body must be a string"}, status=400)
    project_id = str(payload.get("project_id", "")).strip()
    try:
        item = write_instruction(item_id, body, project_id=project_id)
    except InstructionWriteError as exc:
        return web.json_response({"error": exc.reason}, status=exc.status)
    return web.json_response({"ok": True, "item": item.to_dict(include_body=True)})
