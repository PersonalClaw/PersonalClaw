"""HTTP handlers for /api/artifacts — provider-agnostic artifact entity endpoints.

Every handler resolves a provider via ``registry.get_provider(?provider)`` and
calls ``provider.<method>(...)`` — never a singleton. All LLM-authored string
fields are redacted on the way out (``_serialize``); mutations are gated against
restricted (incognito/guest) sessions and SEL-audited.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

from aiohttp import web

from personalclaw.artifacts import registry, source_files
from personalclaw.artifacts.build import (
    ArtifactBuildError,
    BuildResult,
    build_react_artifact,
    needs_build,
)
from personalclaw.artifacts.deploy import (
    DEPLOYABLE_KINDS,
    SERVE_HEADERS,
    SERVE_URL_PREFIX,
    ArtifactDeployStore,
    content_type_for,
    rejects_path,
    resolve_served_file,
)
from personalclaw.artifacts.folders import ArtifactFolder, ArtifactFolderStore, delete_folder
from personalclaw.artifacts.models import (
    MAX_BINARY_CONTENT_BYTES,
    MAX_CONTENT_BYTES,
    Artifact,
    ArtifactKindMismatch,
    ArtifactStaleWrite,
    ArtifactVersionConflict,
    ext_for_mime,
    is_binary_kind,
    kind_for_mime,
    redacted,
)
from personalclaw.dashboard.handlers._shared import _is_restricted_session
from personalclaw.file_view import read_head, whole_text
from personalclaw.http_errors import json_error
from personalclaw.request_validation import (
    RequestValidationError,
    bool_field,
    json_object_body,
    require_string,
    string_field,
)
from personalclaw.security import MaskConflict, is_sensitive_path
from personalclaw.sel import sel
from personalclaw.stale_write import (
    REVISION_HEADER,
    claimed_revision,
    refusal_outcome,
    revision_of,
    stale_write_refusal,
)
from personalclaw.uploads.content_scan import ContentRefused, scan_upload
from personalclaw.uploads.policy import category_for

logger = logging.getLogger(__name__)

# Browser client's literal session key — never a real chat session for the
# timeline deep-link, so drop it to None.
_UI_SESSION_KEY = "dashboard:ui"


def _serialize(art: Artifact, *, include_content: bool = False) -> dict[str, Any]:
    """Serialize an artifact for the API, redacting every LLM-authored field.

    A content-bearing response carries ``content_revision``: the revision of the body in THIS
    response, as redacted (`personalclaw/stale_write.py`). It is what a body save names in
    ``If-Match`` — a page saves the whole body it edited, so it must say which body that was.
    Metadata is not in it: a name, tag or collection edit elsewhere does not make a body stale.
    """
    d = art.to_dict(persist=False)
    d["name"] = redacted(d.get("name", ""))
    d["description"] = redacted(d.get("description", ""))
    d["collection"] = redacted(d.get("collection", ""))
    d["tags"] = [redacted(t) for t in d.get("tags", [])]
    if include_content and d.get("content") is not None:
        d["content"] = redacted(d["content"])
        d["content_revision"] = revision_of(d["content"])
    else:
        d.pop("content", None)
        # live_dirty is content's computed-per-read twin (models.to_dict's own
        # docstring pairs them): get() computes it against the live source, while
        # a list row comes off _read_meta, where the field is never persisted —
        # so a list-shaped live_dirty was always a FABRICATED False, not even
        # stale data. Serving it made the same artifact report False in the list
        # and True in the detail. Absent beats wrong: a reader that needs the
        # flag must take it from a content-bearing response, which every current
        # consumer already does (#630).
        d.pop("live_dirty", None)
    return d


def _session_key(request: web.Request) -> str | None:
    sk = request.headers.get("X-Session-Key", "")
    if not sk or sk == _UI_SESSION_KEY:
        return None
    return sk.split(":", 1)[-1] if ":" in sk else sk


def _project_for_source_path(source_path: str) -> str:
    """The project a file-backed save belongs to when the caller names none: the one active
    project whose workspace or context directory holds the file, else ``""``.

    Measured on a project with a bound workspace: saving ``launch-brief.md`` from that workspace
    through Files → Save as artifact produced an artifact its own project page never listed. The
    agent's ``artifact_save`` stamps the turn's bound project; the dashboard's three manual file
    saves (Files, a chat's file panel, the Code cockpit) stamped nothing, because none of them
    knows about projects. The server does, and the file's location says which project it is.

    Fail-OPEN: an inference that cannot be made leaves the artifact unscoped, which is exactly
    what every such save produced before — never a default project.
    """
    if not source_path:
        return ""
    try:
        from personalclaw.tasks.hierarchy import HierarchyStore

        project = HierarchyStore().project_for_path(source_path)
    except Exception:  # noqa: BLE001 — fail-open to "unscoped", the pre-existing outcome
        logger.warning("could not resolve a project for artifact source %s", source_path)
        return ""
    return project.id if project else ""


def _source_file_refusal(
    request: web.Request, source_path: str, *, writes: bool
) -> web.Response | None:
    """The refusal a file-backed save gets when it names no copy of the file, or a stale one,
    else ``None``.

    Saving an artifact that points at a file with a body (*writes*) writes that body THROUGH to
    the file (``native._try_write_source_path``), and the dashboard's three "Save as artifact"
    doors send the file viewer's draft — a copy of the file as the explorer read it. So when
    *source_path* is a file the save overwrites, the request names the revision that copy was read
    at (the ``file-read`` ETag) and is checked exactly as ``POST /api/file-write`` checks it,
    against the file as the explorer reads it now.

    A save with no text of its own writes nothing to the file: the artifact starts as the file.
    It names the copy it read all the same, because that copy is what the artifact starts as, and
    because a pointer at an existing file is taken only by a caller who has read it. Its refusals
    say so, since nothing of the file is replaced or could be undone. A path no save reads or
    writes through — relative, missing, sensitive, unreadable — needs no base.
    """
    try:
        path = Path(source_path)
        resolved = path.resolve() if path.is_absolute() else None
    except (OSError, ValueError):
        return None
    if resolved is None or is_sensitive_path(str(resolved)) or not resolved.is_file():
        return None
    try:
        head = read_head(str(resolved))
    except OSError:
        return None  # the write-through opens the same file, and fails the same way
    current = whole_text(head)
    if writes:
        return stale_write_refusal(request, current, what=f"the file {source_path!r}")
    claimed = claimed_revision(request)
    if not claimed:
        return json_error(
            "revision_required",
            message=(
                f"An artifact made from the file {source_path!r} starts as the copy of it you "
                f"read, so the request must name that copy: read the file and send its revision "
                f"in {REVISION_HEADER}."
            ),
            status=428,
        )
    if claimed != revision_of(current):
        return json_error(
            "stale_write",
            message=(
                f"The file {source_path!r} changed after the copy this request names was read, "
                "so an artifact made from it now would not start as what you read. Nothing was "
                "saved, and the file is unchanged: read it again and save."
            ),
            status=409,
        )
    return None


def _audit(request: web.Request, operation: str, outcome: str, resources: str = "") -> None:
    try:
        sel().log_api_access(
            caller=request.headers.get("X-Session-Key", "") or "dashboard:ui",
            operation=operation,
            outcome=outcome,
            source="dashboard",
            resources=resources,
        )
    except Exception:
        logger.debug("SEL audit failed for %s", operation, exc_info=True)


def _provider(request: web.Request):
    return registry.get_provider(request.query.get("provider") or "native")


async def api_artifacts_list(request: web.Request) -> web.Response:
    """GET /api/artifacts — metadata-only rows; ``q`` searches metadata and body.

    Filters: tag, kind, source, source_path, project_id, collection, folder.
    ``q`` matches name, slug, description, tags, collection and body content —
    everything the card shows — while the returned rows remain content-free.
    """
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    arts = prov.list(
        tag=request.query.get("tag"),
        kind=request.query.get("kind"),
        q=request.query.get("q"),
        source=request.query.get("source"),
        source_path=request.query.get("source_path"),
        project_id=request.query.get("project_id"),
        collection=request.query.get("collection"),
        # `.get` (not `.get(..., "")`): an ABSENT ?folder means every folder, while
        # an empty `?folder=` means the unfiled bucket. See provider.list.
        folder=request.query.get("folder"),
    )
    return web.json_response({"artifacts": [_serialize(a) for a in arts]})


def _save_file_copy(
    request: web.Request,
    prov: Any,
    body: dict[str, Any],
    *,
    name: str,
    kind: str,
    content: str | None,
    source_path: str,
    session_id: str | None,
) -> web.Response | tuple[Artifact, int]:
    """``POST /api/artifacts`` for a binary kind: the artifact is a COPY of an image or PDF file.

    A JSON body cannot carry bytes, so the body is the file's (``source_path``), admitted like a
    text artifact's pointer (``source_files``) and read and checked by ``artifacts.file_copy``:
    its bytes decide what it is, its name must agree, and its size is decided before it is read.
    Nothing is written to the file, so the request names no revision of it. Saving the same file
    again adds its next version when its bytes changed and answers the artifact as it is when they
    did not, as a text file's re-save bumps its artifact rather than making a second one.

    Returns the refusal, or the saved artifact and its status for the route to answer with.
    """
    from personalclaw.apps.permissions import request_app
    from personalclaw.artifacts import file_copy

    if content is not None:
        return json_error(
            "bad_request",
            message=f"a {kind} artifact's body is its file's bytes, so its save sends no content",
            status=400,
        )
    if not source_path:
        return json_error(
            "bad_request",
            message=f"a {kind} artifact is saved from a file: name the file in source_path",
            status=400,
        )
    try:
        canonical = source_files.admit(source_path)
    except ValueError as exc:
        _audit(request, "artifact.create", "denied", "source_path outside the allowed places")
        if request_app():
            return json_error("forbidden", message=str(exc), status=403)
        return json_error("bad_request", message=str(exc), status=400)
    try:
        data, file_kind, mime = file_copy.read_file_body(Path(canonical))
    except file_copy.FileCopyRefused as refused:
        _audit(request, "artifact.create", "denied", f"source_path={canonical} {refused.reason}")
        if refused.reason == file_copy.TOO_LARGE:
            return json_error("file_too_large", message=str(refused), status=413)
        if refused.reason == file_copy.UNSUPPORTED:
            return json_error("file_type_unsupported", message=str(refused), status=415)
        return json_error("not_found", message=str(refused), status=404)
    if file_kind != kind:
        _audit(request, "artifact.create", "denied", f"source_path={canonical} is {file_kind}")
        return json_error(
            "file_type_unsupported",
            message=f"{Path(canonical).name} is a {file_kind} file by its contents, not {kind}.",
            status=415,
        )
    existing = prov.find_by_source_path(canonical)
    if existing is not None:
        current = prov.raw_bytes(existing.slug)
        if current is not None and current[0] == data:
            unchanged = prov.get(existing.slug)
            if unchanged is None:
                return json_error("not_found", message=f"no artifact {existing.slug!r}", status=404)
            _audit(request, "artifact.create", "deduped", f"slug={existing.slug}")
            return unchanged, 200
        try:
            updated = prov.update_binary(
                existing.slug, data=data, mime=mime, actor="user", session_id=session_id
            )
        except ArtifactKindMismatch as mismatch:
            return json_error(
                "mime_kind_mismatch",
                message=(
                    f"{Path(canonical).name} is saved as the {mismatch.kind} artifact "
                    f"{existing.slug!r}, and a {file_kind} cannot be its next version"
                ),
                status=409,
                error_extra={"kind": mismatch.kind, "declared_kind": file_kind},
            )
        if updated is None:
            return json_error("not_found", message=f"no artifact {existing.slug!r}", status=404)
        _audit(
            request,
            "artifact.update",
            "ok",
            f"slug={updated.slug} version={updated.version} bytes={len(data)} mime={mime}",
        )
        return updated, 200
    if "project_id" in body:
        project_id = str(body.get("project_id", "")).strip()
    else:
        project_id = _project_for_source_path(canonical)
    art = prov.create_binary(
        name=name,
        data=data,
        mime=mime,
        kind=kind,
        source=str(body.get("source", "manual")),
        description=str(body.get("description", "")),
        tags=body.get("tags"),
        actor="user",
        session_id=session_id,
        project_id=project_id,
        source_path=canonical,
    )
    _audit(request, "artifact.create", "ok", f"slug={art.slug} bytes={len(data)} mime={mime}")
    return art, 201


async def api_artifacts_create(request: web.Request) -> web.Response:
    """POST /api/artifacts — create (or bump an existing file-backed artifact).

    A save whose ``source_path`` names an existing file overwrites that file with ``content``, so
    it names the revision of the copy it was built from in ``If-Match`` — the file-read ETag —
    and a stale one is refused before anything is written (:func:`_source_file_refusal`).

    No ``content`` (absent, or ``null``) is no text of its own, not an empty body: the artifact
    starts as the file and the file is never written. It names the copy it read the same way, and
    an artifact that already points at the file is answered as it is.

    A ``source_path`` outside the places an artifact may point (``source_files``) is refused
    with the sentence saying where it may point: ``400`` for the owner, ``403`` for an app.

    A binary ``kind`` (an image or a PDF) is saved from its file as a copy of the file's bytes
    (:func:`_save_file_copy`), since a JSON body carries no bytes.
    """
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.create", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    if prov.readonly:
        return web.json_response({"error": f"provider '{prov.name}' is read-only"}, status=400)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    # Shared validator, this door's envelope. Most of its text save's refusals are flat (`unknown
    # provider`, `read-only`, `invalid slug`, `slug already exists`, …), so the `name` refusal
    # answers in their shape. Only the wrapper is local; the type rule is the shared one.
    try:
        name = require_string(body, "name")
        # 🔴 ABSENT (or null) IS NO TEXT, NOT AN EMPTY BODY. Read as "" it was written through to
        # the file a file-backed artifact points at, and wiped it; "" itself is a body the caller
        # chose, and is written like any other.
        content = (
            None if body.get("content") is None else string_field(body, "content", strip=False)
        )
    except RequestValidationError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    source_path = str(body.get("source_path", "")).strip()
    session_id = _session_key(request)
    kind = str(body.get("kind", "widget"))
    save = _save_file_copy if is_binary_kind(kind) else _save_text
    saved = save(
        request,
        prov,
        body,
        name=name,
        kind=kind,
        content=content,
        source_path=source_path,
        session_id=session_id,
    )
    if isinstance(saved, web.Response):
        return saved
    art, status = saved
    # One answer for every kind: the artifact as saved, 201 when it is new and 200 when the save
    # answered with, or bumped, the one already there.
    return web.json_response(_serialize(art, include_content=True), status=status)


def _save_text(
    request: web.Request,
    prov: Any,
    body: dict[str, Any],
    *,
    name: str,
    kind: str,
    content: str | None,
    source_path: str,
    session_id: str | None,
) -> web.Response | tuple[Artifact, int]:
    """``POST /api/artifacts`` for a text kind (see :func:`api_artifacts_create`).

    Returns the refusal, or the saved artifact and its status for the route to answer with.
    """
    if source_path:
        # FIRST, before anything opens the file: a refused pointer is never read, so neither its
        # existence nor its content shows through the revision check below, and it cannot bump an
        # artifact that already names it (one recorded before this check existed). The store
        # refuses it as well.
        try:
            source_path = source_files.admit(source_path)
        except ValueError as exc:
            from personalclaw.apps.permissions import request_app

            _audit(request, "artifact.create", "denied", "source_path outside the allowed places")
            return web.json_response({"error": str(exc)}, status=403 if request_app() else 400)
        # Checked with no await between it and the provider write below (create or bump), so
        # nothing in this process lands on the file in between.
        stale = _source_file_refusal(request, source_path, writes=content is not None)
        if stale is not None:
            _audit(request, "artifact.create", refusal_outcome(stale), f"source_path={source_path}")
            return stale

    # Dedup by source_path: re-saving a file-backed artifact bumps the existing
    # one rather than creating a duplicate.
    if source_path:
        existing = prov.find_by_source_path(source_path)
        if existing is not None:
            if content is None:
                # No text to bump it with: the artifact that already points at this file is the
                # answer, as it is, and neither it nor the file is written.
                updated = prov.get(existing.slug)
                operation, outcome = "artifact.create", "deduped"
            else:
                try:
                    updated = prov.update(
                        existing.slug,
                        content=content,
                        snapshot=False,
                        actor="user",
                        session_id=session_id,
                    )
                except MaskConflict as exc:
                    return web.json_response({"error": str(exc)}, status=409)
                operation, outcome = "artifact.update", "ok"
            if updated is None:
                # Gone since the lookup: there is no artifact to answer with, or to bump.
                return json_error("not_found", message=f"no artifact {existing.slug!r}", status=404)
            _audit(request, operation, outcome, f"slug={existing.slug}")
            return updated, 200

    requested_slug = str(body.get("slug", "")).strip() or None
    if requested_slug:
        try:
            existing = prov.get(requested_slug)
        except ValueError:
            return web.json_response({"error": "invalid slug"}, status=400)
        if existing is not None:
            return web.json_response({"error": "slug already exists"}, status=409)
    # Server-backed dedup hint (ARTIFACTS S1): a fresh save (no slug, no source_path)
    # whose name matches an existing artifact 409s with the existing slug so the UI can
    # offer "open it / save anyway". `?force=1` bypasses (mint a new artifact anyway).
    force = request.query.get("force") in ("1", "true")
    if not requested_slug and not source_path and not force:
        similar = prov.find_similar(name, kind=kind)
        if similar is not None:
            _audit(request, "artifact.create", "deduped", f"similar={similar.slug}")
            return web.json_response(
                {
                    "error": "similar_artifact_exists",
                    # Named as the list names it (`_serialize`), masked.
                    "similar": {
                        "slug": similar.slug,
                        "name": redacted(similar.name),
                        "kind": similar.kind,
                    },
                },
                status=409,
            )
    # A caller that names a project (even "", unscoped) is obeyed; one that names none gets the
    # project whose directory holds the file it is saving.
    if "project_id" in body:
        project_id = str(body.get("project_id", "")).strip()
    else:
        project_id = _project_for_source_path(source_path)
    try:
        art = prov.create(
            name=name,
            content=content,
            kind=kind,
            source=str(body.get("source", "chat")),
            slug=requested_slug,
            source_path=source_path,
            description=str(body.get("description", "")),
            tags=body.get("tags"),
            actor="user",
            session_id=session_id,
            project_id=project_id,
            collection=str(body.get("collection", "")).strip(),
        )
    except MaskConflict as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except (ValueError, PermissionError) as e:
        return web.json_response({"error": str(e)}, status=400)
    _audit(request, "artifact.create", "ok", f"slug={art.slug}")
    return art, 201


async def api_artifact_detail(request: web.Request) -> web.Response:
    """GET /api/artifacts/{slug} — full content (live-pointer read for file-backed).

    With ``?probe=1`` it's an existence check: returns 200 ``{exists: bool}``
    (never 404), so callers that only ask "is this saved?" (e.g. the widget
    bookmark toggle) don't spam the browser console with expected 404s.
    """
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    slug = request.match_info["slug"]
    try:
        art = prov.get(slug)
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    if request.query.get("probe"):
        return web.json_response({"exists": art is not None})
    if art is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(_serialize(art, include_content=True))


async def api_artifact_update(request: web.Request) -> web.Response:
    """PATCH /api/artifacts/{slug} — save (silent) or snapshot; or metadata-only.

    ``content`` replaces the WHOLE body, so it is written only over the body it was built from:
    the request names that body's ``content_revision`` in ``If-Match``, compared under the
    provider's lock (``update(expect_revision=…)``). None is ``428 revision_required``, a stale
    one ``409 stale_write``, and nothing is written (`personalclaw/stale_write.py`).

    Tags are edited one name at a time — ``add_tags`` / ``remove_tags``, applied to the tags stored
    when the write lands — so they need no revision. There is no whole-list ``tags``: it replaced
    the list with the page's copy of it, dropping any tag added elsewhere since.
    """
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.update", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    if prov.readonly:
        return web.json_response({"error": f"provider '{prov.name}' is read-only"}, status=400)
    slug = request.match_info["slug"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    # A revert is its own operation: restore version N's body (text or binary)
    # server-side rather than round-tripping content from the client (which can't
    # carry binary bytes — the FE only holds a raw-URL ref).
    if body.get("event_type") == "reverted":
        try:
            from_version = int(body.get("from_version") or 0)
        except (TypeError, ValueError):
            return web.json_response({"error": "from_version must be an integer"}, status=400)
        if from_version <= 0:
            return web.json_response({"error": "from_version is required to revert"}, status=400)
        try:
            art = prov.revert(slug, from_version, actor="user", session_id=_session_key(request))
        except (ValueError, PermissionError, NotImplementedError) as e:
            return web.json_response({"error": str(e)}, status=400)
        if art is None:
            return web.json_response({"error": "not found"}, status=404)
        _audit(request, "artifact.update", "ok", f"slug={slug} reverted->{from_version}")
        return web.json_response(_serialize(art, include_content=True))
    if "tags" in body:
        return web.json_response(
            {"error": "send add_tags / remove_tags — the names to add or remove — not tags"},
            status=400,
        )
    tag_edits: dict[str, list[str]] = {}
    for key in ("add_tags", "remove_tags"):
        if key in body:
            names = body[key]
            if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                return web.json_response(
                    {"error": f"{key} must be a list of tag names"}, status=400
                )
            tag_edits[key] = names
    content = body.get("content")
    what = f"the body of the artifact {slug!r}"
    try:
        art = prov.update(
            slug,
            content=content,
            snapshot=bool_field(body, "snapshot", default=False),
            event_type=body.get("event_type"),
            actor="user",
            session_id=_session_key(request),
            name=body.get("name"),
            description=body.get("description"),
            collection=body.get("collection"),
            # "" when the request names no revision, which no body has — the refusal below then
            # words it as the missing precondition (428), not a stale one.
            expect_revision=claimed_revision(request) if content is not None else None,
            add_tags=tag_edits.get("add_tags"),
            remove_tags=tag_edits.get("remove_tags"),
        )
    except ArtifactStaleWrite as stale:
        # The provider found the claimed revision is not `stale.current`'s — the comparison this
        # makes — so it is never None here.
        refusal = cast(web.Response, stale_write_refusal(request, stale.current, what=what))
        _audit(request, "artifact.update", refusal_outcome(refusal), f"slug={slug}")
        return refusal
    except ArtifactKindMismatch as mismatch:
        # The twin of `kind_not_binary` on the raw write: a binary artifact's body is its bytes,
        # and this route writes text.
        _audit(request, "artifact.update", "denied", f"slug={slug} kind={mismatch.kind}")
        return json_error(
            "kind_is_binary",
            message=(
                f"artifact {slug!r} is kind {mismatch.kind!r}, whose body is bytes, not text — "
                f"PUT /api/artifacts/{slug}/raw replaces it"
            ),
            status=409,
            error_extra={"kind": mismatch.kind},
        )
    except MaskConflict as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except (ValueError, PermissionError) as e:
        return web.json_response({"error": str(e)}, status=400)
    if art is None:
        return web.json_response({"error": "not found"}, status=404)
    _audit(request, "artifact.update", "ok", f"slug={slug}")
    return web.json_response(_serialize(art, include_content=True))


async def api_artifact_delete(request: web.Request) -> web.Response:
    """DELETE /api/artifacts/{slug}."""
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.delete", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    if prov.readonly:
        return web.json_response({"error": f"provider '{prov.name}' is read-only"}, status=400)
    slug = request.match_info["slug"]
    try:
        deleted = prov.delete(slug)
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    if not deleted:
        return web.json_response({"error": "not found"}, status=404)
    # Deleting the content must also un-publish it: a deployment left behind would be a
    # deleted artifact that is still reachable at its serve URL (the teardown clause).
    _deploy_store(prov).teardown(slug)
    _audit(request, "artifact.delete", "ok", f"slug={slug}")
    return web.json_response({"ok": True})


async def api_artifact_raw(request: web.Request) -> web.Response:
    """GET /api/artifacts/{slug}/raw — stream a binary artifact's bytes.

    Backs ``kind:image`` rendering: the JSON ``content`` carries this URL (not the
    bytes), and the renderer/<img> fetches the actual image here. ``?version=N``
    serves an immutable snapshot. Bytes are NOT redacted (they're not LLM text).
    """
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    slug = request.match_info["slug"]
    version: int | None = None
    if request.query.get("version"):
        try:
            version = int(request.query["version"])
        except ValueError:
            return web.json_response({"error": "invalid version"}, status=400)
    try:
        result = prov.raw_bytes(slug, version=version)
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    if result is None:
        return web.json_response({"error": "not found"}, status=404)
    data, mime = result
    # Immutable per (slug, version): a versioned read is content-addressable, the
    # live body changes only on edit — cache the version hard, revalidate live.
    cache = "public, max-age=31536000, immutable" if version is not None else "no-cache"
    return web.Response(
        body=data,
        content_type=mime or "application/octet-stream",
        headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"},
    )


# ── The binary write path + the document model ──
#
# Three routes, one guard chain. `PUT …/raw` takes bytes; `GET …/model` hands the
# browser structure; `PUT …/model` takes structure back and re-renders it here. The
# last two are why the browser never constructs OOXML: the only thing that crosses the
# wire for an edit is the model.

#: Artifact kinds with a shipped bytes→model→bytes round trip. A kind is listed when BOTH
#: its parser and its writer exist — never before. Advertising a kind whose parser is
#: missing would answer ``GET …/model`` with an empty model, and an empty model reads
#: exactly like an empty document, so the editor would offer to save the user's file away.
#: The list lives with the codecs (``documents/model_codec.py``) rather than here, so the
#: route cannot claim a capability the documents package does not have. DFE-3 shipped the
#: .docx parser and DFE-7 the .xlsx one; pptx/pdf have writers but no parser yet.


def _binary_target(
    request: web.Request, prov: Any, slug: str
) -> tuple[Artifact | None, web.Response | None]:
    """Resolve *slug* as an existing BINARY artifact, or the refusal to return.

    One resolver for all three routes, so "which kinds may be written as bytes" is
    answered by :func:`is_binary_kind` in exactly one place.
    """
    try:
        art = prov.get(slug)
    except ValueError:
        return None, json_error("bad_request", message="invalid slug", status=400)
    if art is None:
        return None, json_error("not_found", message=f"no artifact {slug!r}", status=404)
    if not is_binary_kind(art.kind):
        _audit(request, "artifact.raw_write", "denied", f"slug={slug} kind={art.kind}")
        return None, json_error(
            "kind_not_binary",
            message=(
                f"artifact {slug!r} is kind {art.kind!r}, whose body is text — "
                f"PATCH /api/artifacts/{slug} edits it"
            ),
            status=409,
        )
    return art, None


def _refuse_if_oversized(request: web.Request, cap: int) -> web.Response | None:
    """Refuse an over-cap body from the HEADERS, before one byte is buffered.

    This is the whole point of the clause: the cap is only a defense if it is decided
    from ``Content-Length``, while the body is still on the wire. A handler that reads
    first and measures after has already spent the memory it was meant to protect, and
    ``provider._write_bytes`` would silently TRUNCATE to the cap rather than refuse —
    so a late check produces a corrupt document instead of an error.

    A body with no declared length (chunked) is refused for the same reason: there is
    nothing to check before reading, and a streaming counter is not what §C3 asks for.
    """
    declared = request.content_length
    if declared is None:
        return json_error("content_length_required", status=411, error_extra={"cap_bytes": cap})
    if declared > cap:
        return json_error(
            "request_too_large",
            message=f"the body declares {declared} bytes; the cap is {cap}",
            status=413,
            error_extra={"cap_bytes": cap, "declared_bytes": declared},
        )
    return None


def _if_match(request: web.Request, art: Artifact) -> tuple[int | None, web.Response | None]:
    """The REQUIRED ``If-Match`` precondition — the artifact's VERSION is the validator.

    There is no ETag anywhere in the artifact store, no CRDT and no OT: the provider
    holds one coarse lock and writes last-wins. So this defines the convention rather
    than reusing one — ``If-Match: <version>`` carries the integer the artifact reports
    verbatim (``If-Match: 3``). A quoted or weak form (``"3"``, ``W/"3"``) is accepted
    because HTTP clients add those on their own, but nothing here mints an opaque tag: a
    second identifier for a thing that already has a monotonic version would be a second
    thing to keep in sync.

    Returns ``(expected_version, None)`` on success. The version is handed back rather
    than acted on here because the comparison that MATTERS happens inside the provider's
    lock (``update_binary(expect_version=…)``); this check is the early, well-worded
    refusal, not the guarantee.
    """
    raw = (request.headers.get("If-Match") or "").strip()
    if not raw:
        return None, json_error(
            "if_match_required", status=428, error_extra={"version": art.version}
        )
    token = raw[2:] if raw.startswith("W/") else raw
    try:
        claimed = int(token.strip('"').strip())
    except ValueError:
        return None, json_error(
            "if_match_malformed",
            message=f"If-Match {raw!r} is not a version number",
            status=400,
            error_extra={"version": art.version},
        )
    if claimed != art.version:
        return None, json_error(
            "version_conflict",
            message=(
                f"artifact {art.slug!r} is at version {art.version}, not {claimed} — "
                "reload before saving"
            ),
            status=409,
            error_extra={"expected": art.version, "supplied": claimed},
        )
    return claimed, None


def _writable_provider(request: web.Request) -> tuple[Any, web.Response | None]:
    """The addressed provider, refused if unknown or read-only."""
    prov = _provider(request)
    if prov is None:
        return None, json_error("bad_request", message="unknown provider", status=400)
    if prov.readonly:
        return None, json_error(
            "forbidden", message=f"provider '{prov.name}' is read-only", status=400
        )
    return prov, None


def _store_binary(
    request: web.Request,
    prov: Any,
    art: Artifact,
    data: bytes,
    *,
    mime: str,
    expect_version: int,
    operation: str,
) -> web.Response:
    """Store *data* as the artifact's new body — the one exit both write routes share.

    Always bumps a version and snapshots, because a binary body has no held-back draft
    state to hold back: there is no silent-save mode to offer, and the version it bumps
    is what makes a lossy edit revertible (§C5) rather than destructive.
    """
    try:
        updated = prov.update_binary(
            art.slug,
            data=data,
            mime=mime,
            actor="user",
            session_id=_session_key(request),
            expect_version=expect_version,
        )
    except ArtifactVersionConflict as conflict:
        _audit(request, operation, "denied", f"slug={art.slug} version_conflict")
        return json_error(
            "version_conflict",
            message=str(conflict),
            status=409,
            error_extra={"expected": conflict.expected, "supplied": conflict.supplied},
        )
    except (ValueError, PermissionError) as exc:
        _audit(request, operation, "error", f"slug={art.slug}: {exc}")
        return json_error("bad_request", message=str(exc), status=400)
    if updated is None:
        return json_error("not_found", message=f"no artifact {art.slug!r}", status=404)
    # ONE audit row per accepted write, carrying the byte count — the size is the part an
    # operator cannot recover later, because the body itself is not in the log.
    _audit(
        request,
        operation,
        "ok",
        f"slug={art.slug} version={updated.version} bytes={len(data)} mime={updated.mime}",
    )
    return web.json_response(
        {"slug": updated.slug, "version": updated.version, "mime": updated.mime}
    )


async def api_artifact_raw_write(request: web.Request) -> web.Response:
    """PUT /api/artifacts/{slug}/raw — replace a binary artifact's bytes (§C3).

    The body IS the bytes and ``Content-Type`` declares their MIME — no multipart
    envelope, deliberately: the cap has to be decided from ``Content-Length`` before
    anything is buffered, and a multipart frame's declared length is the frame's, not
    the part's. ``If-Match: <version>`` is REQUIRED — a whole-document save is exactly
    the write that can silently destroy another tab's work.

    Guard order is the cheap-and-safe one: authorization, then the size refusal from
    headers alone, then the format, then the artifact, then the precondition. The body
    is touched last, and gets the content scan (:mod:`personalclaw.uploads.content_scan`)
    before it is stored.
    """
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.raw_write", "denied", "restricted_session")
        return json_error("forbidden", message="restricted session", status=403)
    prov, refusal = _writable_provider(request)
    if refusal is not None:
        return refusal
    # BEFORE the body: see _refuse_if_oversized. Also before the artifact lookup, so an
    # over-cap upload costs no disk read at all.
    refusal = _refuse_if_oversized(request, MAX_BINARY_CONTENT_BYTES)
    if refusal is not None:
        _audit(request, "artifact.raw_write", "denied", "over_cap_or_unsized")
        return refusal
    mime = (request.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    declared_kind = kind_for_mime(mime)
    if not declared_kind:
        return json_error(
            "unsupported_media_type",
            message=f"Content-Type {mime!r} is not a storable binary artifact format",
            status=415,
        )
    slug = request.match_info["slug"]
    art, refusal = _binary_target(request, prov, slug)
    if refusal is not None or art is None:
        return refusal or json_error("not_found", status=404)
    if declared_kind != art.kind:
        _audit(request, "artifact.raw_write", "denied", f"slug={slug} mime_kind_mismatch")
        return json_error(
            "mime_kind_mismatch",
            message=f"{mime!r} is a {declared_kind!r} body; artifact {slug!r} is {art.kind!r}",
            status=409,
            error_extra={"kind": art.kind, "declared_kind": declared_kind},
        )
    expect_version, refusal = _if_match(request, art)
    if refusal is not None or expect_version is None:
        _audit(request, "artifact.raw_write", "denied", f"slug={slug} if_match")
        return refusal or json_error("if_match_required", status=428)
    data = await request.read()
    if not data:
        return json_error("bad_request", message="the request body is empty", status=400)
    # A body that outran its own Content-Length. aiohttp will not deliver more than the
    # declared length, so this cannot normally fire — it is here because the cap must not
    # depend on that being true, and the provider TRUNCATES rather than refusing.
    if len(data) > MAX_BINARY_CONTENT_BYTES:
        return json_error(
            "request_too_large",
            message=f"the body is {len(data)} bytes; the cap is {MAX_BINARY_CONTENT_BYTES}",
            status=413,
            error_extra={"cap_bytes": MAX_BINARY_CONTENT_BYTES},
        )
    # Before it is stored: the content scan every stored upload gets.
    category = category_for(f"{art.slug}.{ext_for_mime(mime)}", mime)
    try:
        await scan_upload(data, category, surface="artifact")
    except ContentRefused as exc:
        _audit(request, "artifact.raw_write", "denied", f"slug={slug} {exc.code}")
        return exc.response()
    return _store_binary(
        request,
        prov,
        art,
        data,
        mime=mime,
        expect_version=expect_version,
        operation="artifact.raw_write",
    )


def _model_target(
    request: web.Request, prov: Any, slug: str
) -> tuple[Artifact | None, web.Response | None]:
    """Resolve *slug* as an artifact with an editable document model, or the refusal."""
    from personalclaw.documents.model_codec import MODEL_KINDS

    art, refusal = _binary_target(request, prov, slug)
    if refusal is not None or art is None:
        return None, refusal or json_error("not_found", status=404)
    if art.kind not in MODEL_KINDS:
        return None, json_error(
            "model_unavailable",
            message=f"no document model ships for kind {art.kind!r}",
            status=415,
            error_extra={"kind": art.kind, "model_kinds": list(MODEL_KINDS)},
        )
    return art, None


async def api_artifact_model(request: web.Request) -> web.Response:
    """GET /api/artifacts/{slug}/model — the parsed document model + its loss report.

    The editor's READ half (§C4): the browser receives structure — blocks, runs, cells,
    page setup; sheets, cells, formulas — and never a byte of OOXML. The parse is the SAME
    shipped parser the round-trip proofs pin (``docx_parser.parse_docx`` /
    ``xlsx_parser.parse_xlsx``, resolved by kind); a second parser here would be a second
    fidelity story, and only one of them would be tested.

    Not redacted, unlike ``/extract``'s preview. This is an EDIT surface: whatever came
    back has to be what gets written again, and a redacted model saved back would
    replace the user's content with the redaction marker. ``GET …/raw`` already serves
    these same bytes to this same browser unredacted, so there is nothing withheld here
    that is not already available one route over.
    """
    from personalclaw.documents.model_codec import get_codec

    prov = _provider(request)
    if prov is None:
        return json_error("bad_request", message="unknown provider", status=400)
    slug = request.match_info["slug"]
    art, refusal = _model_target(request, prov, slug)
    if refusal is not None or art is None:
        return refusal or json_error("not_found", status=404)
    codec = get_codec(art.kind)
    if codec is None:  # unreachable while MODEL_KINDS agrees with the codec table
        return json_error(
            "model_unavailable",
            message=f"no document model ships for kind {art.kind!r}",
            status=415,
        )
    try:
        result = prov.raw_bytes(slug)
    except ValueError:
        return json_error("bad_request", message="invalid slug", status=400)
    if result is None:
        return json_error("not_found", message=f"artifact {slug!r} has no body", status=404)
    data, mime = result
    from personalclaw.documents.limits import DocumentTooLarge

    try:
        # Parsing walks a zip + the whole document tree; off the event loop like every
        # other CPU-bound artifact operation.
        model, loss = await asyncio.to_thread(codec.parse, data)
    except DocumentTooLarge as exc:
        # #2747. Its OWN code, ahead of the generic catch: the bytes are a perfectly good
        # document that this build refuses to open, and answering `model_parse_failed`
        # would tell the user their file is corrupt. The message names the way out (the
        # raw route serves the same bytes), because a refusal a user cannot get past is
        # the outage the cap was meant to prevent.
        logger.info("artifact model refused as too large for %s: %s", slug, exc)
        return json_error(
            "document_too_large",
            message=(
                f"artifact {slug!r} is too large to open in the editor — {exc}. "
                f"Read the whole file from /api/artifacts/{slug}/raw instead."
            ),
            status=413,
            error_extra={"kind": art.kind},
        )
    except Exception:  # noqa: BLE001 — an unparseable document is a 400, not a 500
        logger.info("artifact model parse failed for %s", slug, exc_info=True)
        return json_error(
            "model_parse_failed",
            message=f"artifact {slug!r} could not be parsed as {art.kind}",
            status=400,
        )
    return web.json_response(
        {
            "slug": slug,
            "kind": art.kind,
            "version": art.version,
            "mime": mime,
            "model": codec.to_dict(model),
            "loss": loss.to_dict(),
        }
    )


async def api_artifact_model_write(request: web.Request) -> web.Response:
    """PUT /api/artifacts/{slug}/model — re-render a posted model into the artifact (§C3).

    The editor's SAVE half, and the mechanism behind "the browser never sees OOXML":
    the client posts back the model it was given, the SHIPPED writer renders it here,
    and the resulting bytes take the same guarded path as ``PUT …/raw`` — same required
    ``If-Match``, same byte cap, same single version bump, same audit row.

    ``{"model": {...}}`` rather than a bare model so the body has room for the save-time
    fields §C5 will need (a lossy-edit acknowledgement) without changing shape later.

    **``dashboard.document_editing`` is enforced HERE, not only in the UI** (§C6). This
    route is the ONLY way an edit can reach the bytes, and re-rendering a document is
    lossy by construction — so with the switch off the write is refused for every client,
    not just for the one that hides its editor. Read per request, so turning the consent
    off closes the path on the very next save rather than at the next restart.
    """
    from personalclaw.config import AppConfig
    from personalclaw.documents.model_codec import get_codec
    from personalclaw.documents.registry import get_writer

    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.model_write", "denied", "restricted_session")
        return json_error("forbidden", message="restricted session", status=403)
    if not AppConfig.load().dashboard.document_editing:
        _audit(request, "artifact.model_write", "denied", "document_editing_off")
        return json_error(
            "document_editing_off",
            message=(
                "in-place document editing is off; turn on Settings › Documents › "
                "'Edit documents in place' to allow a re-render of this file"
            ),
            status=403,
        )
    prov, refusal = _writable_provider(request)
    if refusal is not None:
        return refusal
    # The posted MODEL is text, so it is capped by the text cap — the binary cap applies
    # to the bytes the writer produces, checked after rendering.
    refusal = _refuse_if_oversized(request, MAX_CONTENT_BYTES)
    if refusal is not None:
        _audit(request, "artifact.model_write", "denied", "over_cap_or_unsized")
        return refusal
    slug = request.match_info["slug"]
    art, refusal = _model_target(request, prov, slug)
    if refusal is not None or art is None:
        return refusal or json_error("not_found", status=404)
    expect_version, refusal = _if_match(request, art)
    if refusal is not None or expect_version is None:
        _audit(request, "artifact.model_write", "denied", f"slug={slug} if_match")
        return refusal or json_error("if_match_required", status=428)
    writer = get_writer(art.kind)
    codec = get_codec(art.kind)
    if writer is None or codec is None:
        return json_error(
            "model_unavailable",
            message=f"no writer is available for kind {art.kind!r} in this build",
            status=415,
        )
    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_json", status=400)
    if not isinstance(body, dict):
        return json_error("invalid_body", status=400)
    try:
        model = codec.from_dict(body.get("model"))
    except ValueError as exc:
        return json_error("invalid_model", message=str(exc), status=400)
    try:
        data = await asyncio.to_thread(writer, model)
    except Exception as exc:  # noqa: BLE001 — a writer fault is ours, not the caller's
        logger.warning("artifact model render failed for %s", slug, exc_info=True)
        _audit(request, "artifact.model_write", "error", f"slug={slug}: {exc}")
        return json_error(
            "render_failed", message=f"rendering {art.kind} for {slug!r} failed", status=500
        )
    if len(data) > MAX_BINARY_CONTENT_BYTES:
        return json_error(
            "request_too_large",
            message=(
                f"the rendered document is {len(data)} bytes; "
                f"the cap is {MAX_BINARY_CONTENT_BYTES}"
            ),
            status=413,
            error_extra={"cap_bytes": MAX_BINARY_CONTENT_BYTES},
        )
    # mime="" keeps the artifact's declared MIME: the writer emits the format the
    # artifact already is, so a save must never be able to change its type.
    return _store_binary(
        request,
        prov,
        art,
        data,
        mime="",
        expect_version=expect_version,
        operation="artifact.model_write",
    )


#: Extracted-text preview cap. A generated document can be long, and this powers a
#: PREVIEW beside a download — not the authoritative read path (that is the knowledge
#: reader, which stores the whole thing).
_EXTRACT_PREVIEW_CHARS = 20_000


async def api_artifact_extract(request: web.Request) -> web.Response:
    """GET /api/artifacts/{slug}/extract — extracted text for a binary document artifact.

    Backs the honest "text preview — download for full formatting" surface for generated
    docx/xlsx/pdf. Reuses the SAME reader that ingests uploaded documents rather than a
    second extraction path, so a generated file is read exactly like a user's own.
    """
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    slug = request.match_info["slug"]
    try:
        result = prov.raw_bytes(slug)
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    if result is None:
        return web.json_response({"error": "not found"}, status=404)
    data, mime = result
    ext = ext_for_mime(mime or "")
    if not ext:
        return web.json_response(
            {"error": {"code": "not_extractable", "message": f"no reader for {mime!r}"}},
            status=400,
        )

    import tempfile
    from pathlib import Path as _Path

    from personalclaw.knowledge.readers import FileReader

    def extract() -> str | None:
        # The readers take a path, so the bytes land in a temp file that is removed
        # immediately — the artifact store stays the only durable copy.
        with tempfile.TemporaryDirectory() as tmp:
            scratch = _Path(tmp) / f"artifact.{ext}"
            scratch.write_bytes(data)
            try:
                text, _meta = FileReader().read(str(scratch))
            except Exception:  # noqa: BLE001 — an unreadable document is a 400, not a 500
                logger.info("artifact extract failed for %s", slug, exc_info=True)
                return None
            return text

    # Writing the document out and reading it whole runs in a worker thread: a generated PDF or
    # workbook of up to 16 MiB takes as long as its pages to read, and the gateway answers
    # meanwhile.
    text = await asyncio.to_thread(extract)
    if text is None:
        return web.json_response(
            {"error": {"code": "extract_failed", "message": "could not read the document"}},
            status=400,
        )
    truncated = len(text) > _EXTRACT_PREVIEW_CHARS
    # Masked like every other read of an artifact (`models.redacted`): a generated document can
    # contain whatever was in the prompt that produced it.
    body = redacted(text[:_EXTRACT_PREVIEW_CHARS])
    return web.json_response({"slug": slug, "text": body, "truncated": truncated})


def _recover_image_gen_args(session_key: str, slug: str) -> dict[str, str] | None:
    """Recover the image_generate args (prompt/size) to make *slug* again from scratch, from a
    session's history: the newest call that made it new from a prompt, else the newest edit of it
    (``mcp_artifacts.image_remake_args``). Returns ``{"prompt", "size"}`` or None if not found.

    An edit's prompt is the fallback only because it assumes the image it changed ("add a glow"):
    regenerating recreates the image from scratch, so a prompt that made it new is the right one.
    """
    from personalclaw.dashboard.chat_utils import _history_key_for
    from personalclaw.history import ConversationLog
    from personalclaw.mcp_artifacts import image_remake_args

    try:
        # The FE passes the dashboard session id (e.g. "chat-1-…"); the history file
        # is keyed "dashboard:chat-1-…". Normalize so the lookup hits the log.
        msgs = ConversationLog().read_messages(_history_key_for(session_key))
    except Exception:
        return None
    return image_remake_args(msgs, slug)


async def api_artifact_regenerate(request: web.Request) -> web.Response:
    """POST /api/artifacts/{slug}/regenerate — re-run image generation at this slug.

    Backs the chat placeholder's "Regenerate" button for a deleted/missing inline
    image. Recovers the original prompt from the session's tool-call history (or a
    ``prompt`` in the body as fallback) and re-runs generation, landing the bytes
    back at the SAME slug so the transcript's existing ``/raw`` reference resolves —
    no new chat message, no LLM turn. Mutating → gated + audited.
    """
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.regenerate", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    if prov.readonly:
        return web.json_response({"error": f"provider '{prov.name}' is read-only"}, status=400)
    slug = request.match_info["slug"]
    body = await json_object_body(request)
    if not isinstance(body, dict):
        body = {}

    session_key = str(body.get("session", "")).strip()
    args = _recover_image_gen_args(session_key, slug) if session_key else None
    # Fallback: the FE may pass the prompt (the placeholder's caption) when history
    # lookup misses (e.g. an old/rotated log).
    prompt = (args or {}).get("prompt") or str(body.get("prompt", "")).strip()
    size = (args or {}).get("size", "") or str(body.get("size", "")).strip()
    if not prompt:
        _audit(request, "artifact.regenerate", "error", f"slug={slug} no prompt recoverable")
        return web.json_response({"error": "could not recover the original prompt"}, status=422)

    from personalclaw.mcp_artifacts import regenerate_image_at_slug

    ok, msg = await asyncio.to_thread(
        regenerate_image_at_slug,
        prov,
        slug,
        prompt,
        size=size,
        session_id=session_key or None,
    )
    if not ok:
        _audit(request, "artifact.regenerate", "error", f"slug={slug}: {msg}")
        return web.json_response({"error": msg}, status=502)
    _audit(request, "artifact.regenerate", "ok", f"slug={slug}")
    return web.json_response({"ok": True, "slug": slug})


async def api_artifact_versions(request: web.Request) -> web.Response:
    """GET /api/artifacts/{slug}/versions."""
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    slug = request.match_info["slug"]
    try:
        # The PARENT artifact first (#2940). `list_versions` answers `[]` for a slug that was
        # never stored, which is indistinguishable from a real artifact whose history was
        # trimmed — while `GET`/`DELETE /api/artifacts/{slug}` on the same slug 404 "not found".
        # Resolved through the provider's own `get`, so a provider that stores versions
        # separately cannot disagree with itself about whether the artifact exists.
        if prov.get(slug) is None:
            return web.json_response({"error": "not found"}, status=404)
        versions = prov.list_versions(slug)
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    return web.json_response({"slug": slug, "versions": versions})


async def api_artifact_version_detail(request: web.Request) -> web.Response:
    """GET /api/artifacts/{slug}/versions/{version} — immutable historical content."""
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    slug = request.match_info["slug"]
    try:
        version = int(request.match_info["version"])
    except ValueError:
        return web.json_response({"error": "invalid version"}, status=400)
    try:
        art = prov.get(slug, version=version)
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    if art is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(_serialize(art, include_content=True))


async def api_artifact_events(request: web.Request) -> web.Response:
    """GET /api/artifacts/{slug}/events — activity timeline (drops dashboard:ui)."""
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    slug = request.match_info["slug"]
    try:
        art = prov.get(slug)
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    if art is None:
        return web.json_response({"error": "not found"}, status=404)
    events = []
    for e in art.events:
        d = e.to_dict()
        if d.get("session_id") == _UI_SESSION_KEY:
            d["session_id"] = ""
        events.append(d)
    return web.json_response({"slug": slug, "events": events})


async def api_artifact_record_event(request: web.Request) -> web.Response:
    """POST /api/artifacts/{slug}/events — record a 'referenced' impression."""
    state = request.app["state"]
    if _is_restricted_session(state, request):
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    slug = request.match_info["slug"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict) or body.get("type") != "referenced":
        return web.json_response({"error": "only type 'referenced' allowed"}, status=400)
    try:
        art, appended = prov.record_impression(
            slug,
            by="user",
            session_id=_session_key(request),
            message_ts=str(body.get("message_ts", "")) or None,
            widget_index=body.get("widget_index"),
        )
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    if art is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"ok": True, "appended": appended})


async def api_artifacts_pinned(request: web.Request) -> web.Response:
    """GET /api/artifacts/pinned — the dashboard pin list (WORK-CONTAINERS §6.5d).

    REFERENCES only. Each row is a slug plus when it was pinned; the widget resolves the artifact
    itself through the list route. Returning denormalized names here would go stale on the next
    rename, and a dashboard card showing a title the artifact no longer has is confidently wrong.
    """
    from personalclaw.workflows import pinned

    return web.json_response({"pins": pinned.list_pins()})


async def api_artifacts_pin(request: web.Request) -> web.Response:
    """POST /api/artifacts/{slug}/pin — pin or unpin (``{"pinned": bool}``).

    ONE route for both directions rather than a pin route and an unpin route: it is one piece of
    state with two values, and two endpoints would be two places to keep the cap and the
    dedup-by-slug rule right.

    Gated like every other artifact mutation — a restricted (incognito/guest) session must not
    write durable dashboard state, which is exactly what a pin is.
    """
    from personalclaw.workflows import pinned

    state = request.app.get("state")
    if _is_restricted_session(state, request):
        _audit(request, "artifact.pin", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    slug = request.match_info.get("slug", "")
    body = await json_object_body(request)
    if not isinstance(body, dict):
        body = {}
    # Default TRUE: the route is reached from a Pin control, so the common call carries no body.
    # Defaulting to unpin would make the bodyless call silently do the opposite of its name.
    want = bool_field(body, "pinned", default=True)
    pins = pinned.pin(slug, run_id=str(body.get("run_id") or "")) if want else pinned.unpin(slug)
    _audit(request, "artifact.pin" if want else "artifact.unpin", "allowed", slug)
    return web.json_response({"ok": True, "pinned": want, "pins": pins})


def _folder_store(prov: Any) -> ArtifactFolderStore:
    """A folder store rooted at the SAME tree the provider owns, so a provider on a
    custom root (tests, a dev home) never writes folders into the default home."""
    return ArtifactFolderStore(getattr(prov, "root", None))


def _serialize_folder(folder: ArtifactFolder) -> dict[str, Any]:
    d = folder.to_dict()
    d["name"] = redacted(d.get("name", ""))
    return d


async def api_artifact_folders(request: web.Request) -> web.Response:
    """GET /api/artifacts/folders — the library folder tree (flat, parent_id-linked)."""
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    folders = _folder_store(prov).list()
    return web.json_response({"folders": [_serialize_folder(f) for f in folders]})


async def api_artifact_folder_create(request: web.Request) -> web.Response:
    """POST /api/artifacts/folders — create a folder (``{name, parent_id?, icon?}``)."""
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.folder_create", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    body = await json_object_body(request)
    try:
        folder = _folder_store(prov).create(
            require_string(body, "name"),
            parent_id=string_field(body, "parent_id"),
            icon=string_field(body, "icon"),
        )
    except ValueError as exc:
        _audit(request, "artifact.folder_create", "denied", str(exc))
        return web.json_response({"error": str(exc)}, status=400)
    _audit(request, "artifact.folder_create", "allowed", folder.id)
    return web.json_response(_serialize_folder(folder), status=201)


async def api_artifact_folder_update(request: web.Request) -> web.Response:
    """PATCH /api/artifacts/folders/{id} — rename / re-nest / reorder. No artifact is touched."""
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.folder_update", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    fid = request.match_info["id"]
    try:
        body = await json_object_body(request)
        name = require_string(body, "name") if "name" in body else None
    except RequestValidationError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    try:
        folder = _folder_store(prov).update(
            fid,
            name=name,
            parent_id=str(body["parent_id"] or "") if "parent_id" in body else None,
            order=int(body["order"]) if "order" in body else None,
            icon=str(body["icon"] or "") if "icon" in body else None,
        )
    except (ValueError, TypeError) as exc:
        _audit(request, "artifact.folder_update", "denied", str(exc))
        return web.json_response({"error": str(exc)}, status=400)
    if folder is None:
        return web.json_response({"error": "not found"}, status=404)
    _audit(request, "artifact.folder_update", "allowed", fid)
    return web.json_response(_serialize_folder(folder))


async def api_artifact_folder_delete(request: web.Request) -> web.Response:
    """DELETE /api/artifacts/folders/{id} — members fall back to unfiled; nothing is destroyed."""
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.folder_delete", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    if prov.readonly:
        return web.json_response({"error": f"provider '{prov.name}' is read-only"}, status=400)
    fid = request.match_info["id"]
    deleted, unfiled = delete_folder(_folder_store(prov), prov, fid)
    if not deleted:
        return web.json_response({"error": "not found"}, status=404)
    _audit(request, "artifact.folder_delete", "allowed", fid)
    return web.json_response({"ok": True, "unfiled": unfiled})


async def api_artifact_set_folder(request: web.Request) -> web.Response:
    """PATCH /api/artifacts/{slug}/folder — file an artifact (``{folder_id}``; "" = unfiled).

    Its own route rather than a field on PATCH /{slug}: that handler routes through
    ``provider.update``, which bumps ``updated_at``. Filing must not.
    """
    state = request.app["state"]
    if _is_restricted_session(state, request):
        _audit(request, "artifact.set_folder", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    if prov.readonly:
        return web.json_response({"error": f"provider '{prov.name}' is read-only"}, status=400)
    slug = request.match_info["slug"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    folder_id = str(body.get("folder_id", "") or "").strip()
    if folder_id and not _folder_store(prov).exists(folder_id):
        return web.json_response({"error": "folder not found"}, status=400)
    try:
        art = prov.set_folder(slug, folder_id)
    except (ValueError, PermissionError) as exc:
        _audit(request, "artifact.set_folder", "denied", slug)
        return web.json_response({"error": str(exc)}, status=400)
    if art is None:
        return web.json_response({"error": "not found"}, status=404)
    _audit(request, "artifact.set_folder", "allowed", slug)
    return web.json_response({"ok": True, "folder_id": art.folder_id})


def _deploy_store(prov: Any) -> ArtifactDeployStore:
    """A deploy registry rooted at the SAME tree the provider owns — same reason as
    ``_folder_store``: a provider on a tmp root must not publish into the real home."""
    return ArtifactDeployStore(getattr(prov, "root", None))


async def api_artifacts_deployed(request: web.Request) -> web.Response:
    """GET /api/artifacts/deployed — the deployed-app listing (slug + in-gateway URL)."""
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    return web.json_response({"deployments": [d.to_dict() for d in _deploy_store(prov).list()]})


async def api_artifact_deploy(request: web.Request) -> web.Response:
    """POST /api/artifacts/{slug}/deploy — publish the artifact at its stable serve URL.

    Idempotent: re-deploying an already-deployed slug refreshes its entry rather than
    erroring, because the UI control is "Deploy / Open" and the second press must not fail.

    A kind that needs a build (``react`` — PEP-9) is BUILT FIRST, and a build failure
    answers 422 with the bundler's own reason instead of publishing an app that cannot
    render. Re-deploying therefore also re-builds, which is how an edited component
    reaches its URL under build-once-serve-static.
    """
    state = request.app.get("state")
    if _is_restricted_session(state, request):
        _audit(request, "artifact.deploy", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    slug = request.match_info["slug"]
    try:
        art = prov.get(slug)
    except ValueError:
        return web.json_response({"error": "invalid slug"}, status=400)
    if art is None:
        return web.json_response({"error": "not found"}, status=404)
    if art.kind not in DEPLOYABLE_KINDS:
        _audit(request, "artifact.deploy", "denied", f"slug={slug} kind={art.kind}")
        return web.json_response(
            {"error": f"kind '{art.kind}' is not deployable"},
            status=400,
        )
    body = await json_object_body(request)
    if not isinstance(body, dict):
        body = {}
    store = _deploy_store(prov)
    entry = str(body.get("entry") or "")
    build: BuildResult | None = None
    if needs_build(art.kind):
        # The build runs BEFORE the registry write, so a slug is never published with
        # nothing servable behind it. The error is returned, never swallowed: its
        # sentence is what `errText` puts in front of the user.
        try:
            build = await build_react_artifact(
                slug=slug,
                source=art.content or "",
                files_root=store.files_root(slug),
                title=art.name or slug,
            )
        except ArtifactBuildError as exc:
            _audit(request, "artifact.deploy", "denied", f"slug={slug} build_failed")
            # `message=` carries the builder's WHAT — WHY. Fix: FIX sentence VERBATIM, which is
            # the whole point of raising it: `errText` puts that sentence in front of the user.
            # The structured envelope does not cost us that — it adds a code a client can branch
            # on, which flat `{"error": "<prose>"}` never could.
            return json_error("artifact_build_failed", message=str(exc), status=422)
        except ValueError as exc:  # invalid slug from files_root
            _audit(request, "artifact.deploy", "denied", f"slug={slug}: {exc}")
            return json_error("artifact_slug_invalid", message=str(exc), status=400)
        entry = build.entry
    try:
        dep = store.deploy(slug, entry=entry)
    except (ValueError, PermissionError) as exc:
        _audit(request, "artifact.deploy", "denied", f"slug={slug}: {exc}")
        return web.json_response({"error": str(exc)}, status=400)
    _audit(request, "artifact.deploy", "ok", f"slug={slug}")
    payload: dict[str, Any] = {"ok": True, "deployment": dep.to_dict()}
    if build is not None:
        payload["build"] = {
            "files": build.files,
            "bundle_bytes": build.bundle_bytes,
            "warnings": build.warnings,
        }
    return web.json_response(payload)


async def api_artifact_teardown(request: web.Request) -> web.Response:
    """DELETE /api/artifacts/{slug}/deploy — tear the deployment down.

    Removes the serve route for this slug and nothing else: the artifact and every
    version it owns survive, because un-publishing is not deleting.
    """
    state = request.app.get("state")
    if _is_restricted_session(state, request):
        _audit(request, "artifact.teardown", "denied", "restricted_session")
        return web.json_response({"error": "restricted session"}, status=403)
    prov = _provider(request)
    if prov is None:
        return web.json_response({"error": "unknown provider"}, status=400)
    slug = request.match_info["slug"]
    removed = _deploy_store(prov).teardown(slug)
    _audit(request, "artifact.teardown", "ok" if removed else "noop", f"slug={slug}")
    return web.json_response({"ok": True, "removed": removed})


def _refuse_serve(request: web.Request, slug: str, reason: str, status: int) -> web.Response:
    """One exit for every refusal on the serve path — audited, and never echoing the
    requested path back into the response (that body would render in a browser)."""
    _audit(request, "artifact.serve", "denied", f"slug={slug} {reason}")
    return web.Response(status=status, text="refused", content_type="text/plain")


async def serve_artifact_redirect(request: web.Request) -> web.StreamResponse:
    """GET /artifacts/serve/{slug} → 308 to the canonical trailing-slash URL.

    Relative asset URLs inside the served document resolve against the directory, so
    serving the entry from the slash-less path would break every one of them.
    """
    slug = request.match_info.get("slug", "")
    raise web.HTTPPermanentRedirect(f"{SERVE_URL_PREFIX}/{slug}/")


async def serve_deployed_artifact(request: web.Request) -> web.StreamResponse:
    """GET /artifacts/serve/{slug}/{path:.*} — serve a deployed artifact's own bytes.

    Behind session auth like every non-bypassed gateway path, fenced by
    ``SERVE_HEADERS`` (``connect-src 'none'`` — the page cannot call ``/api``), and
    contained by ``resolve_served_file``. Serves ONLY the artifact's own files: a
    directory never yields an index, and an undeployed or deleted slug 404s.
    """
    prov = registry.get_provider("native")
    if prov is None:  # pragma: no cover - the native provider always registers
        return web.Response(status=404, text="not found", content_type="text/plain")
    slug = request.match_info.get("slug", "")
    rel = request.match_info.get("path", "") or ""
    store = _deploy_store(prov)
    dep = store.get(slug)
    if dep is None:
        # Not deployed, torn down, or an unservable slug — one answer for all three so
        # the route cannot be used to probe which artifacts exist.
        return _refuse_serve(request, slug, "not_deployed", 404)
    try:
        art = prov.get(slug)
    except ValueError:
        return _refuse_serve(request, slug, "invalid_slug", 404)
    if art is None:
        return _refuse_serve(request, slug, "artifact_missing", 404)
    target = rel or dep.entry
    if target.endswith("/"):
        target = target + dep.entry
    try:
        files_root = store.files_root(slug)
    except ValueError:
        return _refuse_serve(request, slug, "invalid_slug", 404)
    resolved = resolve_served_file(files_root, target)
    if resolved is None:
        # Distinguish "you asked for something you may not have" from "not there": a
        # rejected shape is a 403 refusal, a merely absent file falls through to the body.
        if rejects_path(target):
            return _refuse_serve(request, slug, "traversal_refused", 403)
        if target != dep.entry:
            return _refuse_serve(request, slug, "file_missing", 404)
        # Entry with no file on disk: a single-body html/widget artifact IS its entry.
        # A build-required kind is NOT: its body is JSX, and answering it as text/html
        # would hand a browser source it cannot run (and, for a react body, would put
        # unbundled artifact source on the origin). No bundle means nothing to serve.
        if needs_build(art.kind):
            return _refuse_serve(request, slug, "not_built", 404)
        if art.content is None:
            return _refuse_serve(request, slug, "empty_body", 404)
        return web.Response(
            body=art.content.encode("utf-8"),
            content_type="text/html",
            charset="utf-8",
            headers=dict(SERVE_HEADERS),
        )
    try:
        data = resolved.read_bytes()
    except OSError:
        return _refuse_serve(request, slug, "unreadable", 404)
    return web.Response(
        body=data,
        content_type=content_type_for(resolved),
        headers=dict(SERVE_HEADERS),
    )


def register_artifact_routes(app: web.Application) -> None:
    """Register /api/artifacts/* routes plus the artifact static-serve route.

    The native provider self-registers lazily via the registry; no startup
    registration needed."""
    app.router.add_get("/api/artifacts", api_artifacts_list)
    app.router.add_post("/api/artifacts", api_artifacts_create)
    # BEFORE `/{slug}`: aiohttp matches in registration order, so a dynamic `{slug}` registered
    # first would swallow the literal `pinned` path and answer it as an artifact named "pinned".
    app.router.add_get("/api/artifacts/pinned", api_artifacts_pinned)
    # Same reason as `pinned`: the literal `folders` paths must precede `{slug}`, and
    # `/folders/{id}` must precede `/{slug}/folder` so a 12-hex folder id is never
    # read as an artifact slug.
    # Same reason as `pinned`/`folders`: the literal `deployed` path must precede `{slug}`.
    app.router.add_get("/api/artifacts/deployed", api_artifacts_deployed)
    app.router.add_get("/api/artifacts/folders", api_artifact_folders)
    app.router.add_post("/api/artifacts/folders", api_artifact_folder_create)
    app.router.add_patch("/api/artifacts/folders/{id}", api_artifact_folder_update)
    app.router.add_delete("/api/artifacts/folders/{id}", api_artifact_folder_delete)
    app.router.add_get("/api/artifacts/{slug}", api_artifact_detail)
    app.router.add_patch("/api/artifacts/{slug}", api_artifact_update)
    app.router.add_delete("/api/artifacts/{slug}", api_artifact_delete)
    app.router.add_get("/api/artifacts/{slug}/raw", api_artifact_raw)
    # The write half of the same path (DFE §C3) — registered beside its GET so the two
    # halves of one resource cannot drift apart in the table.
    app.router.add_put("/api/artifacts/{slug}/raw", api_artifact_raw_write)
    app.router.add_get("/api/artifacts/{slug}/extract", api_artifact_extract)
    # `/model` beside `/extract`: both are derived READS of a binary document body — one
    # gives an agent text, the other gives the editor structure. The PUT is the save half.
    app.router.add_get("/api/artifacts/{slug}/model", api_artifact_model)
    app.router.add_put("/api/artifacts/{slug}/model", api_artifact_model_write)
    app.router.add_post("/api/artifacts/{slug}/regenerate", api_artifact_regenerate)
    app.router.add_get("/api/artifacts/{slug}/versions", api_artifact_versions)
    app.router.add_get("/api/artifacts/{slug}/versions/{version}", api_artifact_version_detail)
    app.router.add_get("/api/artifacts/{slug}/events", api_artifact_events)
    app.router.add_post("/api/artifacts/{slug}/events", api_artifact_record_event)
    app.router.add_post("/api/artifacts/{slug}/pin", api_artifacts_pin)
    app.router.add_patch("/api/artifacts/{slug}/folder", api_artifact_set_folder)
    app.router.add_post("/api/artifacts/{slug}/deploy", api_artifact_deploy)
    app.router.add_delete("/api/artifacts/{slug}/deploy", api_artifact_teardown)
    # The static-serve route — NOT under /api, so the CSP fence's
    # `connect-src 'none'` reads as "this page cannot reach the gateway API". Registered
    # here rather than in server.py so the artifact routes stay one registration.
    # Slug-only first: it 308s to the trailing-slash form, without which every relative
    # asset URL in the served document would resolve one directory too high.
    app.router.add_get(f"{SERVE_URL_PREFIX}/{{slug}}", serve_artifact_redirect)
    app.router.add_get(f"{SERVE_URL_PREFIX}/{{slug}}/{{path:.*}}", serve_deployed_artifact)
