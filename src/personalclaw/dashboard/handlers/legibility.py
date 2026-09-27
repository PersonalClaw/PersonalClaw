"""Legibility endpoints — the Discover section + hub's data.

``GET /api/legibility/discover`` returns the curated Discover tips still worth showing
(the hand-authored catalog minus dismissed tips and minus areas the user has already
engaged), grouped by area, or ``enabled: false`` when the ``legibility.discover_tips``
kill switch is off. ``POST /api/legibility/discover/dismiss`` persists a per-tip
dismissal so it never resurfaces, and ``DELETE`` on that same path clears every dismissal
(#452 — dismiss is one unconfirmed click, so it needs a way back). Propose-don't-write:
no endpoint here ever enables or configures anything on the user's behalf.

``GET /api/legibility/always-on`` is the always-on conventions viewer's data: every
``always: true`` skill and project-instruction doc a session receives unconditionally, with
provenance. ``GET /api/legibility/always-on/doc`` returns one body verbatim for the editor and
``PUT`` writes it back. The viewer slices the session's own producer strings rather than
re-deriving the always-on set — see ``legibility/always_on.py`` for why, including why a GET
here must not assemble a full session prompt.
"""

import logging

from aiohttp import web

from personalclaw.legibility.always_on import (
    AlwaysOnItem,
    InstructionWriteError,
    collect_always_on,
    read_instruction,
    write_instruction,
)
from personalclaw.legibility.discover import (
    UnknownTipError,
    clear_dismissed,
    compute_discover,
    dismiss,
)
from personalclaw.stale_write import claimed_revision, revision_of, stale_write_refusal

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

    The id must be one the catalog defines. This validated only that ``id`` was *present*,
    so any string at all was persisted forever into a settings file with no UI that can
    inspect or clear it — and, since one POST may carry the app's whole body ceiling, a
    single request could leave megabytes there for every later Discover read to parse.
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
    try:
        ids = dismiss(tip_id)
    except UnknownTipError:
        # %r, and truncated: the id is caller-supplied, and repr escapes the newlines that
        # would otherwise let it forge a second log line.
        logger.info("discover dismiss refused: %r is not a catalog tip id", tip_id[:80])
        return web.json_response({"error": "unknown tip id"}, status=400)
    return web.json_response({"ok": True, "dismissed": sorted(ids)})


async def api_discover_dismiss_clear(request: web.Request) -> web.Response:
    """DELETE /api/legibility/discover/dismiss — undo every Discover dismissal.

    No body and no id: this is clear-ALL (#452). The user has no way to see which ids are
    stored, so a per-id restore would ask them to choose from a set they were never shown.

    DELETE on the POST's own path, not a new ``/restore`` noun, because the resource being
    removed is the dismissal the POST created — one path, two verbs, nothing new to learn.

    Answers ``restored`` so the caller can say what happened. That count is of the ids that
    were STORED; it is deliberately not the payload's ``restorable_count``, which is how many
    tips will become VISIBLE again (a dismissed tip whose area the user has since used stays
    auto-hidden). Still propose-don't-write: this only un-hides what the user hid.
    """
    removed = clear_dismissed()
    return web.json_response({"ok": True, "restored": removed})


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


def _item_with_revision(item: AlwaysOnItem) -> dict:
    """An item as the editor round-trip serves it: the verbatim body, plus the revision of that
    body — what a PUT replacing it must name in ``If-Match`` (``personalclaw/stale_write.py``)."""
    return {**item.to_dict(include_body=True), "revision": revision_of(item.body)}


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
    return web.json_response(_item_with_revision(item))


async def api_always_on_doc_write(request: web.Request) -> web.Response:
    """PUT /api/legibility/always-on/doc — replace an editable project instruction.

    Body: ``{"id": "...", "project_id": "...", "body": "..."}``. A refused or failed write is an
    error response, never a silent success — the underlying store reports failure as a bare
    ``False`` and rendering "Saved" over a discarded edit is the failure this guards.

    Replacing a document that exists names the revision its read reported in ``If-Match``, and a
    stale one is refused with ``409 stale_write``. Creating the overview — a project with none
    yet, which no read can hand a revision for — needs none.
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
    stale = _stale_instruction_refusal(request, item_id, project_id)
    if stale is not None:
        return stale
    try:
        item = write_instruction(item_id, body, project_id=project_id)
    except InstructionWriteError as exc:
        return web.json_response({"error": exc.reason}, status=exc.status)
    return web.json_response({"ok": True, "item": _item_with_revision(item)})


def _stale_instruction_refusal(
    request: web.Request, item_id: str, project_id: str
) -> web.Response | None:
    """The refusal a replace from a stale copy gets, else ``None`` (``write_instruction`` still
    decides whether the item may be written at all).

    🔴 THE OVERVIEW IS REPLACED ONLY OVER THE COPY IT WAS BUILT FROM. It is not the page's alone:
    every workflow run that completes in the project appends a line to it
    (``RunController._revise_project_overview``), so an editor opened before a run finished used to
    save its old copy over that line. Compared against the body the GET serves, with no await
    between this check and the write.

    An item not in effect is one with no content yet — the overview a PUT may create — so a PUT
    naming no revision passes. A PUT that DOES name one was built from a document that has since
    been emptied, and is refused like any other stale copy. A read-only item, and a request
    naming no project, are left to the write's own refusal, which says what is wrong.
    """
    if not project_id:
        return None
    try:
        current = read_instruction(item_id, project_id=project_id)
    except InstructionWriteError:
        if not claimed_revision(request):
            return None
        name = item_id.split(":", 1)[-1]
        return stale_write_refusal(request, None, what=f"the project instruction {name!r}")
    if not current.editable:
        return None
    return stale_write_refusal(
        request, current.body, what=f"the project instruction {current.name!r}"
    )
