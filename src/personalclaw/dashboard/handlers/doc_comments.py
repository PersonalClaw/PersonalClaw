"""HTTP handlers for /api/doc-comments — the annotation layer over files and artifacts.

The store these serve is :mod:`personalclaw.doc_comments`; its module docstring carries
why the layer moved off ``localStorage``. Refusals answer with the ONE wire envelope
(``json_error``) rather than the flat ``{"error": "<prose>"}`` several older handlers still
carry — the flat population is shrink-only
(``tests/test_wire_error_envelope_census.py``), so a new surface must not add to it.

The body read goes through the ONE reader, ``request_validation.json_object_body``, rather
than a private ``_body`` helper — the population of private readers is shrink-only too
(``tests/test_one_request_body_reader.py``). ``empty_ok=False`` because every route below
has a required field, so absent bytes are a broken caller rather than "all defaults", and
the refusal it raises is turned into the envelope once by
:mod:`personalclaw.dashboard.request_boundary`, which is why no route here carries a
``try``/``except`` around the read.
"""

from aiohttp import web

from personalclaw import doc_comments
from personalclaw.http_errors import json_error
from personalclaw.request_validation import json_object_body


async def api_doc_comments_list(request: web.Request) -> web.Response:
    """GET /api/doc-comments — the whole cross-document deck, oldest first."""
    return web.json_response({"comments": doc_comments.list_comments()})


async def api_doc_comments_create(request: web.Request) -> web.Response:
    """POST /api/doc-comments — append one comment.

    ``id`` and ``ts`` are assigned SERVER-side and a supplied value is ignored rather than
    honored: the browser used to mint both, and letting a client choose an id is how two
    devices collide on one row.
    """
    body = await json_object_body(request, empty_ok=False)
    for field in ("doc_id", "comment"):
        value = body.get(field)
        if value is not None and not isinstance(value, str):
            return json_error(
                "field_not_a_string", message=f"{field} must be a JSON string", status=400
            )
    try:
        row = doc_comments.add(
            doc_id=body.get("doc_id") or "",
            doc_label=body.get("doc_label") or "",
            doc_path=body.get("doc_path") or "",
            quote=body.get("quote") or "",
            comment=body.get("comment") or "",
            line=body.get("line"),
            column=body.get("column"),
            context=body.get("context") or "",
        )
    except ValueError as exc:
        return json_error("field_required", message=str(exc), status=400)
    return web.json_response({"comment": row.to_dict()}, status=201)


async def api_doc_comments_update(request: web.Request) -> web.Response:
    """PATCH /api/doc-comments/{comment_id} — edit one comment's body."""
    comment_id = request.match_info["comment_id"]
    body = await json_object_body(request, empty_ok=False)
    raw = body.get("comment")
    if raw is not None and not isinstance(raw, str):
        return json_error("field_not_a_string", message="comment must be a JSON string", status=400)
    try:
        row = doc_comments.update(comment_id, comment=raw or "")
    except ValueError as exc:
        return json_error("field_required", message=str(exc), status=400)
    if row is None:
        return json_error("not_found", message="no comment with that id", status=404)
    return web.json_response({"comment": row.to_dict()})


async def api_doc_comments_delete(request: web.Request) -> web.Response:
    """DELETE /api/doc-comments/{comment_id} — drop one comment.

    404 for an unknown id rather than a 200 no-op: the caller asked for a row to be gone
    and needs to learn that the row it named was not the row it thought.
    """
    comment_id = request.match_info["comment_id"]
    if not doc_comments.remove([comment_id]):
        return json_error("not_found", message="no comment with that id", status=404)
    return web.json_response({"ok": True, "removed": 1})


async def api_doc_comments_delete_many(request: web.Request) -> web.Response:
    """POST /api/doc-comments/delete — drop several by id.

    The deck's own bulk verb (dismiss the cards just handed to the assistant). One call
    rather than N so a partial failure cannot leave the deck half-cleared, and it reports
    ``removed`` so a caller can see its list did not all exist.
    """
    body = await json_object_body(request, empty_ok=False)
    ids = body.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return json_error("invalid_request", message="ids must be an array of strings", status=400)
    return web.json_response({"ok": True, "removed": doc_comments.remove(ids)})


async def api_doc_comments_clear(request: web.Request) -> web.Response:
    """DELETE /api/doc-comments — empty the deck."""
    return web.json_response({"ok": True, "removed": doc_comments.clear()})


def register_doc_comment_routes(app: web.Application) -> None:
    app.router.add_get("/api/doc-comments", api_doc_comments_list)
    app.router.add_post("/api/doc-comments", api_doc_comments_create)
    app.router.add_delete("/api/doc-comments", api_doc_comments_clear)
    # Registered BEFORE the `{comment_id}` routes so the literal wins the match.
    app.router.add_post("/api/doc-comments/delete", api_doc_comments_delete_many)
    app.router.add_patch("/api/doc-comments/{comment_id}", api_doc_comments_update)
    app.router.add_delete("/api/doc-comments/{comment_id}", api_doc_comments_delete)
