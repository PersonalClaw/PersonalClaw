"""Artifacts tool category — operate the Artifacts entity (save/get/update/list/
versions/delete) as a native tool group.

One of the cohesive native tool-provider categories. Exposes ``_list_tools`` / ``_call_tool`` — the
same shape ``mcp_core`` and ``mcp_schedule`` use — so the in-process
``InProcessMcpToolProvider`` and the aggregating ``mcp-core`` MCP server both consume it
through one path. Tools operate the artifact provider in-process (no HTTP hop),
attributed as the agent so updates snapshot + emit lifecycle events.
"""

from typing import Any

from personalclaw.mcp_core import _resolve_session_key


def _current_project_id() -> str:
    """The Project id bound for this turn (S5) — lazily read the native runtime's
    per-turn contextvar. Returns "" when unbound (unscoped session, or a caller
    outside the native runtime), so an unscoped save simply carries no project."""
    try:
        from personalclaw.agents.native.builtin_tools import current_project_id

        return current_project_id() or ""
    except Exception:
        return ""


def _list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "artifact_save",
            "description": (
                "Save content as a named, versioned artifact so it persists beyond "
                "chat scrollback and can be iterated on by name in a later session. "
                "Use for widgets/HTML tools/dashboards (kind='widget'/'html'), live "
                "React components (kind='react' — content is JSX defining a top-level "
                "`App` component authored against the window React/ReactDOM globals; "
                "renders in a sandboxed canvas), infographics (kind='infographic' — "
                "content is AntV declarative DSL, see the infographic-syntax skill), "
                "editorial long-form documents (kind='document' — the content must be "
                "semantic HTML, NOT markdown; see the editorial-document skill), or "
                "docs (kind='markdown' for markdown/prose — headings, lists, tables, "
                "code fences; or 'json'/'svg'/'text'). Rule of thumb: markdown body → "
                "kind='markdown', HTML body → kind='document'. Returns the slug — the "
                "stable handle to reference it later. "
                "Pass an explicit slug to re-save/overwrite a known artifact."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Display name"},
                    "content": {"type": "string", "description": "Artifact body (inline)"},
                    "content_file": {
                        "type": "string",
                        "description": "Absolute path to read content from instead of inline content",  # noqa: E501
                    },
                    "kind": {
                        "type": "string",
                        "enum": [
                            "widget",
                            "html",
                            "react",
                            "markdown",
                            "svg",
                            "json",
                            "text",
                            "infographic",
                            "document",
                        ],
                        "description": "Content kind (default widget). Use 'markdown' for prose/markdown bodies (# headings, **bold**, tables, lists); 'document' ONLY for semantic HTML editorial docs, never for markdown.",  # noqa: E501
                    },
                    "slug": {
                        "type": "string",
                        "description": "Explicit slug (else derived from name)",
                    },
                    "description": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name"],
            },
        },
        {
            "name": "artifact_get",
            "description": (
                "Fetch a saved artifact's content by slug. Pass version=N for a "
                "historical snapshot; omit for the live version."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "version": {
                        "type": "integer",
                        "description": "Snapshot number (omit for live)",
                    },
                },
                "required": ["slug"],
            },
        },
        {
            "name": "artifact_update",
            "description": (
                "Update a saved artifact by slug, creating a new version snapshot "
                "(each agent update is a checkpoint, like a commit). Pass new content "
                "inline or via content_file; or update metadata only (description/tags)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "content": {"type": "string"},
                    "content_file": {
                        "type": "string",
                        "description": "Absolute path to read new content from",
                    },
                    "description": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["slug"],
            },
        },
        {
            "name": "artifact_list",
            "description": "List saved artifacts (name/slug/kind/version/tags). Filter by tag, kind, or a text query q.",  # noqa: E501
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "widget",
                            "html",
                            "react",
                            "markdown",
                            "svg",
                            "json",
                            "text",
                            "infographic",
                            "document",
                        ],
                    },
                    "q": {"type": "string"},
                },
            },
        },
        {
            "name": "artifact_versions",
            "description": "List the numbered snapshot versions of an artifact by slug.",
            "inputSchema": {
                "type": "object",
                "properties": {"slug": {"type": "string"}},
                "required": ["slug"],
            },
        },
        {
            "name": "artifact_delete",
            "description": "Delete a saved artifact (and its version history) by slug. The source file/widget is not touched.",  # noqa: E501
            "inputSchema": {
                "type": "object",
                "properties": {"slug": {"type": "string"}},
                "required": ["slug"],
            },
        },
        {
            "name": "image_generate",
            "description": (
                "Generate an image from a text prompt (or edit an existing one), using "
                "the model bound to the 'image_gen' use-case in Settings → Models. The "
                "result is saved as a versioned kind='image' artifact; returns its slug "
                "so it can be shown, referenced, or embedded in a document. Pass "
                "edit_artifact=<slug> to edit a prior generated image in place (a new "
                "version on that artifact) instead of creating a new one. Requires an "
                "image_gen model to be configured; if none is, it says so."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "What to generate / how to edit"},
                    "size": {
                        "type": "string",
                        "description": "e.g. '1024x1024' (provider-specific; omit for default)",
                    },
                    "name": {
                        "type": "string",
                        "description": "Artifact display name (else derived from the prompt)",
                    },
                    "edit_artifact": {
                        "type": "string",
                        "description": "Slug of a prior kind:image artifact to edit in place",
                    },
                },
                "required": ["prompt"],
            },
        },
        {
            "name": "video_generate",
            "description": (
                "Generate a video from a text prompt, using the model bound to the "
                "'video_gen' use-case in Settings → Models. The result is saved as a "
                "versioned kind='video' artifact; returns its slug so it can be "
                "referenced or embedded. Video generation is asynchronous and may take "
                "1-3 minutes. Requires a video_gen model to be configured; if none is, "
                "it says so."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "What to generate (scene description)",
                    },
                    "duration_seconds": {
                        "type": "number",
                        "description": "Target video duration in seconds (default 5; provider may cap)",  # noqa: E501
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "description": "e.g. '16:9', '9:16', '1:1' (provider-specific; omit for default)",  # noqa: E501
                    },
                    "name": {
                        "type": "string",
                        "description": "Artifact display name (else derived from the prompt)",
                    },
                },
                "required": ["prompt"],
            },
        },
    ]


def _read_artifact_content(args: dict[str, Any]) -> tuple[str | None, str | None]:
    """Resolve artifact content from inline ``content`` or a ``content_file``.

    Returns ``(content, error)``. A ``content_file`` is gated by
    ``is_sensitive_path`` before reading (mirrors notify_attachment). ``content`` is None
    when neither was supplied (a metadata-only update).
    """
    from pathlib import Path

    from personalclaw.hooks import FileTooLargeError, safe_read_file_bytes
    from personalclaw.security import is_sensitive_path

    cfile = args.get("content_file", "")
    if cfile:
        if is_sensitive_path(cfile):
            return None, "content_file resolves to a sensitive path"
        try:
            raw = safe_read_file_bytes(str(Path(cfile)))
        except FileTooLargeError as e:
            return None, str(e)
        if raw is None:
            return None, f"content_file not found or access denied: {cfile}"
        try:
            return raw.decode("utf-8"), None
        except UnicodeDecodeError:
            return None, "content_file must be UTF-8 text"
    if "content" in args:
        return str(args["content"]), None
    return None, None


def _run_async(coro: Any) -> Any:
    """Drive an async coroutine from this sync MCP handler.

    ``_call_tool`` runs in a thread-pool executor (off the event loop), so there's
    normally no running loop and ``asyncio.run`` is safe; the fallback covers the
    rare case a loop IS running (mirrors mcp_schedule). Bounded by the provider's
    own per-call timeout, so no extra timeout here.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, coro).result()


def _materialize_image(result: Any) -> tuple[bytes, str] | None:
    """Turn an ImageResult into ``(bytes, mime)``, fetching/decoding as needed.

    A provider returns one of: inline b64 (decode), a (possibly expiring) url
    (fetch through the egress chokepoint immediately so delivery survives expiry),
    or a local_path (read). Returns None if nothing resolved.
    """
    import base64
    from pathlib import Path

    mime = getattr(result, "mime", "") or "image/png"
    b64 = getattr(result, "b64", "") or ""
    if b64:
        try:
            return base64.b64decode(b64), mime
        except (ValueError, TypeError):
            return None
    url = getattr(result, "url", "") or ""
    if url:
        from personalclaw.net import CONNECTOR, fetch

        resp = _run_async(fetch(url, policy=CONNECTOR))
        if resp.status == 200 and resp.body:
            ct = resp.headers.get("Content-Type", "").split(";")[0].strip()
            return resp.body, (ct or mime)
        return None
    local = getattr(result, "local_path", "") or ""
    if local:
        try:
            return Path(local).read_bytes(), mime
        except OSError:
            return None
    return None


def _call_tool_inner(name: str, args: dict[str, Any]) -> str:
    """Dispatch artifact_* tools directly against the native provider entity.

    In-process (no HTTP round-trip); attributed as the agent so updates snapshot
    and emit 'iterated' lifecycle events.
    """
    import json as _json

    from personalclaw.artifacts import registry
    from personalclaw.security import redact
    from personalclaw.sel import sel

    prov = registry.get_provider("native")
    if prov is None:
        return "Error: artifact provider unavailable"
    sk = _resolve_session_key()

    def _audit(outcome: str, slug: str = "", error: str = "") -> None:
        sel().log_tool_invocation(
            session_key=sk,
            source="mcp",
            tool_name=name,
            outcome=outcome,
            metadata={"slug": slug} if slug else None,
            error=error,
        )

    try:
        if name == "artifact_save":
            content, err = _read_artifact_content(args)
            if err:
                _audit("denied", error=err)
                return f"Error: {err}"
            if content is None:
                _audit("denied", error="no content")
                return "Error: provide content or content_file"
            art = prov.create(
                name=args["name"],
                content=content or "",
                kind=args.get("kind", "widget"),
                source="chat",
                slug=args.get("slug"),
                description=args.get("description", ""),
                tags=args.get("tags"),
                actor="agent",
                session_id=sk,
                # Tie the artifact to the active Project (S5) so it surfaces in the
                # Project detail page. Bound per-turn by the native runtime; "" when
                # the session isn't scoped to a project.
                project_id=_current_project_id(),
            )
            _audit("success", art.slug)
            return f"Saved artifact '{art.name}' (slug: {art.slug}, version {art.version})."

        if name == "artifact_get":
            got = prov.get(args["slug"], version=args.get("version"))
            if got is None:
                _audit("not_found", args["slug"])
                return f"Artifact not found: {args['slug']}"
            _audit("success", got.slug)
            return redact(got.content or "")

        if name == "artifact_update":
            content, err = _read_artifact_content(args)
            if err:
                _audit("denied", args.get("slug", ""), err)
                return f"Error: {err}"
            upd = prov.update(
                args["slug"],
                content=content,
                snapshot=True,  # every agent update is a checkpoint
                description=args.get("description"),
                tags=args.get("tags"),
                actor="agent",
                session_id=sk,
            )
            if upd is None:
                _audit("not_found", args["slug"])
                return f"Artifact not found: {args['slug']}"
            _audit("success", upd.slug)
            return f"Updated artifact '{upd.name}' → version {upd.version}."

        if name == "artifact_list":
            arts = prov.list(tag=args.get("tag"), kind=args.get("kind"), q=args.get("q"))
            _audit("success")
            if not arts:
                return "No artifacts found."
            rows = [
                {
                    "slug": a.slug,
                    "name": redact(a.name),
                    "kind": a.kind,
                    "version": a.version,
                    "tags": a.tags,
                }
                for a in arts
            ]
            return _json.dumps(rows, indent=2)

        if name == "artifact_versions":
            versions = prov.list_versions(args["slug"])
            _audit("success", args["slug"])
            return _json.dumps({"slug": args["slug"], "versions": versions})

        if name == "artifact_delete":
            ok = prov.delete(args["slug"])
            _audit("success" if ok else "not_found", args["slug"])
            return (
                f"Deleted artifact: {args['slug']}" if ok else f"Artifact not found: {args['slug']}"
            )

        if name == "image_generate":
            return _image_generate(prov, args, sk, _audit)

        if name == "video_generate":
            return _video_generate(prov, args, sk, _audit)

    except (ValueError, PermissionError) as e:
        _audit("error", args.get("slug", ""), str(e))
        return f"Error: {e}"

    return f"Unknown artifact tool: {name}"


def _image_generate(prov: Any, args: dict[str, Any], sk: str | None, _audit: Any) -> str:
    """image_generate: resolve the image_gen capability, generate/edit, save kind:image.

    Thin wrapper over the capability (image_gen/registry.active_image_gen) — the
    real work is the provider's; this materializes the result + lands a versioned
    binary artifact + returns the slug.
    """
    import tempfile
    from pathlib import Path

    from personalclaw.image_gen.provider import ImageGenError
    from personalclaw.image_gen.registry import active_image_gen

    resolved = active_image_gen()
    if resolved is None:
        _audit("denied", error="no image_gen model configured")
        return (
            "Error: no image-generation model is configured. Bind one to the "
            "'image_gen' use-case in Settings → Models (e.g. an OpenAI gpt-image-1 "
            "or a FAL model)."
        )
    provider, model_id = resolved
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        _audit("denied", error="empty prompt")
        return "Error: provide a non-empty prompt."
    size = str(args.get("size", "")).strip()
    edit_slug = str(args.get("edit_artifact", "")).strip()

    try:
        if edit_slug:
            src = prov.get(edit_slug)
            if src is None or src.kind != "image":
                _audit("denied", edit_slug, "edit source not an image artifact")
                return f"Error: {edit_slug!r} is not an existing image artifact to edit."
            raw = prov.raw_bytes(edit_slug)
            if raw is None:
                return f"Error: could not read source image {edit_slug!r}."
            src_bytes, src_mime = raw
            from personalclaw.artifacts.models import ext_for_mime

            with tempfile.NamedTemporaryFile(
                suffix=f".{ext_for_mime(src_mime)}", delete=False
            ) as tf:
                tf.write(src_bytes)
                src_path = tf.name
            try:
                results = _run_async(
                    provider.edit(prompt, source_image=src_path, model=model_id, size=size)
                )
            finally:
                with __import__("contextlib").suppress(OSError):
                    Path(src_path).unlink()
        else:
            results = _run_async(provider.generate(prompt, model=model_id, size=size))
    except ImageGenError as e:
        _audit("error", edit_slug, str(e))
        return f"Error: {e}"

    if not results:
        _audit("error", edit_slug, "no image returned")
        return "Error: the image provider returned no image."

    materialized = _materialize_image(results[0])
    if materialized is None:
        _audit("error", edit_slug, "could not materialize image")
        return "Error: generated image could not be saved (no resolvable bytes)."
    data, mime = materialized

    display_name = str(args.get("name", "")).strip() or prompt[:60]
    if edit_slug:
        art = prov.update_binary(edit_slug, data=data, mime=mime, actor="agent", session_id=sk)
        if art is None:
            return f"Error: could not update image artifact {edit_slug!r}."
        _audit("success", art.slug)
        # Pin the inline image to THIS version (not live /raw) so the chat message
        # keeps showing the image it produced even after a later edit.
        raw_url = f"/api/artifacts/{art.slug}/raw?version={art.version}"
        return (
            f"Edited image artifact '{art.name}' → version {art.version} (slug: {art.slug}).\n\n"
            f"Show the result by embedding this markdown image in your reply:\n"
            f"![{art.name}]({raw_url})"
        )
    art = prov.create_binary(
        name=display_name,
        data=data,
        mime=mime,
        kind="image",
        source="chat",
        actor="agent",
        session_id=sk,
    )
    _audit("success", art.slug)
    revised = getattr(results[0], "revised_prompt", "") or ""
    note = f" The provider revised the prompt to: {revised}." if revised else ""
    # Pin to the exact version produced (?version=N) so each chat message stays
    # bound to the image it generated — an immutable transcript — and the versioned
    # /raw is hard-cacheable. (Live /raw would silently change after a later edit.)
    raw_url = f"/api/artifacts/{art.slug}/raw?version={art.version}"
    # Hand the model a ready-to-embed markdown image so the picture shows inline in
    # chat (the image renderer gates the src + styles it), plus the slug for later
    # reference/editing. This is the primary delivery surface; for a channel
    # (channels/etc.) the on-disk artifact body is the materialized cache.
    return (
        f"Generated image '{art.name}' (slug: {art.slug}) via {provider.name}:{model_id}.{note}\n\n"
        f"Show it to the user by embedding this markdown image in your reply:\n"
        f"![{art.name}]({raw_url})"
    )


def _materialize_video(result: Any) -> tuple[bytes, str] | None:
    """Turn a VideoResult into ``(bytes, mime)``, fetching as needed.

    A provider returns a (possibly expiring) url or a local_path. Fetch through
    the egress chokepoint immediately so delivery survives expiry. Returns None if
    nothing resolved.
    """
    from pathlib import Path

    mime = getattr(result, "mime", "") or "video/mp4"
    url = getattr(result, "url", "") or ""
    if url:
        from personalclaw.net import CONNECTOR, fetch

        resp = _run_async(fetch(url, policy=CONNECTOR))
        if resp.status == 200 and resp.body:
            ct = resp.headers.get("Content-Type", "").split(";")[0].strip()
            return resp.body, (ct or mime)
        return None
    local = getattr(result, "local_path", "") or ""
    if local:
        try:
            return Path(local).read_bytes(), mime
        except OSError:
            return None
    return None


def _video_generate(prov: Any, args: dict[str, Any], sk: str | None, _audit: Any) -> str:
    """video_generate: resolve the video_gen capability, generate, save kind:video.

    Thin wrapper over the capability (video_gen/registry.active_video_gen) — the
    real work is the provider's; this materializes the result + lands a versioned
    binary artifact + returns the slug.
    """
    from personalclaw.video_gen.provider import VideoGenError
    from personalclaw.video_gen.registry import active_video_gen

    resolved = active_video_gen()
    if resolved is None:
        _audit("denied", error="no video_gen model configured")
        return (
            "Error: no video-generation model is configured. Bind one to the "
            "'video_gen' use-case in Settings → Models (e.g. a FAL Kling or Veo "
            "model)."
        )
    provider, model_id = resolved
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        _audit("denied", error="empty prompt")
        return "Error: provide a non-empty prompt."
    duration_seconds = float(args.get("duration_seconds", 5.0))
    aspect_ratio = str(args.get("aspect_ratio", "")).strip()

    try:
        results = _run_async(
            provider.generate(
                prompt,
                model=model_id,
                duration_seconds=duration_seconds,
                aspect_ratio=aspect_ratio,
            )
        )
    except VideoGenError as e:
        _audit("error", "", str(e))
        return f"Error: {e}"

    if not results:
        _audit("error", "", "no video returned")
        return "Error: the video provider returned no video."

    materialized = _materialize_video(results[0])
    if materialized is None:
        _audit("error", "", "could not materialize video")
        return "Error: generated video could not be saved (no resolvable bytes)."
    data, mime = materialized

    display_name = str(args.get("name", "")).strip() or prompt[:60]
    art = prov.create_binary(
        name=display_name,
        data=data,
        mime=mime,
        kind="video",
        source="chat",
        actor="agent",
        session_id=sk,
    )
    _audit("success", art.slug)
    duration_info = getattr(results[0], "duration_s", 0) or ""
    note = f" Duration: {duration_info}s." if duration_info else ""
    raw_url = f"/api/artifacts/{art.slug}/raw?version={art.version}"
    return (
        f"Generated video '{art.name}' (slug: {art.slug}) "
        f"via {provider.name}:{model_id}.{note}\n\n"
        f"Show it to the user by embedding this video tag in your reply:\n"
        f'<video src="{raw_url}" controls preload="auto" '
        f'style="max-width:100%;border-radius:12px;margin:8px 0">'
        f"</video>"
    )


def regenerate_image_at_slug(
    prov: Any, slug: str, prompt: str, *, size: str = "", session_id: str | None = None
) -> tuple[bool, str]:
    """Re-run image generation for an EXISTING slug, in the background (no chat turn).

    Backs the chat placeholder's "Regenerate" affordance: a generated image whose
    artifact was deleted leaves the transcript's ``/api/artifacts/<slug>/raw`` ref
    dangling. This re-runs the original prompt through the active image_gen model
    and lands the bytes back at the SAME slug so the existing ref resolves again —
    recreating the artifact at v1 if it was deleted, or appending a version if it
    still exists. No new chat message, no LLM turn, no new slug.

    Returns ``(ok, message)``.
    """
    from personalclaw.image_gen.provider import ImageGenError
    from personalclaw.image_gen.registry import active_image_gen

    prompt = (prompt or "").strip()
    if not prompt:
        return False, "no prompt to regenerate from"
    resolved = active_image_gen()
    if resolved is None:
        return False, "no image-generation model is configured"
    provider, model_id = resolved
    try:
        results = _run_async(provider.generate(prompt, model=model_id, size=(size or "").strip()))
    except ImageGenError as e:
        return False, str(e)
    if not results:
        return False, "the image provider returned no image"
    materialized = _materialize_image(results[0])
    if materialized is None:
        return False, "generated image could not be saved (no resolvable bytes)"
    data, mime = materialized

    existing = prov.get(slug)
    if existing is not None and existing.kind == "image":
        # Artifact still present — append a fresh version (keeps history).
        art = prov.update_binary(slug, data=data, mime=mime, actor="user", session_id=session_id)
    else:
        # Deleted (the common case for a broken transcript image): recreate at the
        # SAME slug → version 1, so a transcript ref pinned to ?version=1 resolves.
        art = prov.create_binary(
            name=prompt[:60],
            data=data,
            mime=mime,
            kind="image",
            source="chat",
            slug=slug,
            actor="user",
            session_id=session_id,
        )
        if art is not None and art.slug != slug:
            # Slug collided unexpectedly (shouldn't happen for a deleted artifact);
            # the transcript ref wouldn't resolve, so report failure rather than
            # silently landing the image somewhere the message can't see.
            prov.delete(art.slug)
            return False, "could not restore the image at its original location"
    if art is None:
        return False, "could not write the regenerated image"
    return True, art.slug


def _validate_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate tool arguments against the shared MCP schema (enforces e.g. the
    artifact_save ``kind`` enum). Tools without a schema pass through."""
    from personalclaw.validation import MCP_CORE_SCHEMAS, validate_tool_args

    schema = MCP_CORE_SCHEMAS.get(name)
    if schema:
        return validate_tool_args(args, schema)
    return args


def _call_tool(name: str, raw_args: dict[str, Any]) -> str:
    from personalclaw.mcp_shared import call_tool_with_logging

    return call_tool_with_logging(
        name,
        raw_args,
        _validate_args,
        _call_tool_inner,
        session_key="mcp_core",
        downstream_service="personalclaw-artifacts",
    )
