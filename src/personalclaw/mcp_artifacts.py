"""Artifacts tool category — operate the Artifacts entity (save/get/update/list/
versions/delete) as a native tool group.

One of the cohesive native tool-provider categories. Exposes ``_list_tools`` / ``_call_tool`` — the
same shape ``mcp_core`` and ``mcp_schedule`` use — so the in-process
``InProcessMcpToolProvider`` and the aggregating ``mcp-core`` MCP server both consume it
through one path. Tools operate the artifact provider in-process (no HTTP hop),
attributed as the agent so updates snapshot + emit lifecycle events.
"""

import logging
import re
from typing import Any, NamedTuple

from personalclaw.artifacts import dedupe as artifact_dedupe
from personalclaw.artifacts import retakes
from personalclaw.artifacts.models import ArtifactKindMismatch, is_valid_slug
from personalclaw.mcp_core import _resolve_session_key
from personalclaw.tool_providers.base import BUILDS_META_KEY, ToolFailure, tool_failure
from personalclaw.validation import decode_json_text

logger = logging.getLogger(__name__)


def _current_project_id() -> str:
    """The Project this save scopes under (S5), resolved for BOTH runtimes.

    Two callers, two mechanisms, one answer:

    * the **native** runtime runs in-process and binds a per-turn contextvar, so reading
      it is exact and free;
    * an **ACP** CLI's tools run in a separate ``personalclaw mcp-core`` process, where
      that contextvar is empty by construction. ``provider_bridge`` pops ``project_id``
      unconditionally and hands it only to the native builder, so nothing about the
      binding crosses into the ACP branch. Every ACP ``artifact_save`` therefore stamped
      ``project_id=""`` and the artifact never appeared on its Project page
      (ACP-AGENT-PARITY §2.6 gap 10, atom ``AAP-9``).

    The ACP half resolves SERVER-SIDE from the session key rather than by threading a new
    argument through the protocol: the key already crosses as ``PERSONALCLAW_SESSION_KEY``
    (or the ``session_pid_<pid>.txt`` ancestor walk), which is exactly what
    ``_resolve_session_key`` reads, so the gateway can be asked what that session is bound
    to. No protocol change, and the answer is live rather than a snapshot taken at spawn.

    Returns "" for an unscoped session, and "" on any failure — never a default project.
    Stamping an unscoped save into "Personal" would file work under a project the user
    never chose, which is worse than an unstamped artifact.
    """
    try:
        from personalclaw.agents.native.builtin_tools import current_project_id

        pid = current_project_id() or ""
        if pid:
            return pid
    except Exception:
        pass
    return _session_bound_project_id()


def _session_bound_project_id() -> str:
    """The calling session's bound Project, read from the gateway. "" when unresolvable.

    **Out-of-process callers only, and the guard is load-bearing.** The in-process native
    runtime lives INSIDE the gateway, so a blocking ``urllib`` GET from here would have
    the gateway waiting on itself — at best a wasted round trip, at worst a request
    issued from the event loop that cannot be served until it returns. Its session key
    arrives via ``mcp_core._CURRENT_SESSION_KEY`` (a contextvar), so that contextvar
    being set is the exact signal for "I am in-process": in that case the native project
    contextvar is authoritative and its emptiness means unscoped, full stop.

    An ACP CLI's ``mcp-core`` process has no such contextvar — its key comes from
    ``PERSONALCLAW_SESSION_KEY`` or the ``session_pid_<pid>.txt`` ancestor walk — so it
    falls through and asks.
    """
    from personalclaw.mcp_core import _CURRENT_SESSION_KEY, _get

    if _CURRENT_SESSION_KEY.get():
        return ""  # in-process native turn: the contextvar already answered
    if not _resolve_session_key():
        return ""  # no session identity → nothing to look up
    try:
        got = _get("/api/chat/sessions/bound-project")
        if not isinstance(got, dict) or got.get("error"):
            return ""
        return str(got.get("project_id") or "")
    except Exception:
        logger.debug("session project lookup failed", exc_info=True)
        return ""


def _list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "artifact_save",
            "annotations": {"readOnlyHint": False},
            "_meta": {BUILDS_META_KEY: True},
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
                    "collection": {
                        "type": "string",
                        "description": "Optional library collection label to group this artifact under.",  # noqa: E501
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Save a NEW artifact even if one with the same name exists (skip the dedup hint).",  # noqa: E501
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "artifact_get",
            "annotations": {"readOnlyHint": True},
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
            "annotations": {"readOnlyHint": False},
            "_meta": {BUILDS_META_KEY: True},
            "description": (
                "Update a saved artifact by slug, creating a new version snapshot "
                "(each agent update is a checkpoint, like a commit). Pass new content "
                "inline or via content_file; or update metadata only (description/tags). "
                "An image, video, PDF or office document is not text: only its metadata "
                "changes here, and its next version comes from the tool that made it "
                "(image_generate, document_create, sheet_create or deck_create with slug)."
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
                    "collection": {
                        "type": "string",
                        "description": "Reassign the library collection label (metadata-only).",
                    },
                },
                "required": ["slug"],
            },
        },
        {
            "name": "artifact_list",
            "annotations": {"readOnlyHint": True},
            "description": "List saved artifacts (name/slug/kind/version/tags). Filter by tag, kind, collection, or a text query q.",  # noqa: E501
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
                    "collection": {"type": "string"},
                },
            },
        },
        {
            "name": "artifact_versions",
            "annotations": {"readOnlyHint": True},
            "description": "List the numbered snapshot versions of an artifact by slug.",
            "inputSchema": {
                "type": "object",
                "properties": {"slug": {"type": "string"}},
                "required": ["slug"],
            },
        },
        {
            "name": "artifact_delete",
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
            "description": "Delete a saved artifact (and its version history) by slug. The source file/widget is not touched.",  # noqa: E501
            "inputSchema": {
                "type": "object",
                "properties": {"slug": {"type": "string"}},
                "required": ["slug"],
            },
        },
        {
            "name": "image_generate",
            "annotations": {"readOnlyHint": False},
            "_meta": {BUILDS_META_KEY: True},
            "description": (
                "Generate an image from a text prompt, using the model bound to the "
                "'image_gen' use-case in Settings → Models, and save it as a versioned "
                "kind='image' artifact; returns its slug so it can be shown, referenced, or "
                "embedded in a document. To change an existing image, pass its slug: the image "
                "is saved as that artifact's next version instead of as a new artifact. With "
                "slug alone the model makes a new image from the prompt and never sees the "
                "current one, so the prompt describes the whole picture with the change made. "
                "Add edit=true to send the current image to the model to change as the prompt "
                "says, which only a model that edits images can do: each result says which way "
                "the bound model takes. Requires an image_gen model to be configured; if none "
                "is, it says so."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "What to generate (with edit=true: what to change)",
                    },
                    "size": {
                        "type": "string",
                        "description": "e.g. '1024x1024' (provider-specific; omit for default)",
                    },
                    "name": {
                        "type": "string",
                        "description": "Display name of a new image (else derived from the prompt)",  # noqa: E501
                    },
                    "slug": {
                        "type": "string",
                        "description": "Slug of the existing kind:image artifact this image is the next version of",  # noqa: E501
                    },
                    "edit": {
                        "type": "boolean",
                        "description": "With slug: send that image to the model to change, for a model that edits images",  # noqa: E501
                    },
                },
                "required": ["prompt"],
            },
        },
        {
            "name": "video_generate",
            "annotations": {"readOnlyHint": False},
            "_meta": {BUILDS_META_KEY: True},
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
        {
            "name": "document_create",
            "annotations": {"readOnlyHint": False},
            "_meta": {BUILDS_META_KEY: True},
            "description": (
                "Generate a real Word document (.docx) from MARKDOWN and save it as a "
                "versioned artifact the user can download. Write ordinary markdown — "
                "headings, paragraphs, bullet and numbered lists, tables, fenced code, "
                "`---` for a page break — and it is rendered into the document. Do NOT "
                "attempt to emit OOXML or base64. Use this when the user wants a file to "
                "send, print or hand to someone; use artifact_save with kind='markdown' "
                "or 'document' when they just want to read it in the app. Re-running with "
                "the same `name` (or the same `slug`) updates that document and bumps its "
                "version instead of creating a near-duplicate — to make a SEPARATE "
                "document, give it a different name. Returns the slug and a download URL."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Display name for the document"},
                    "markdown": {
                        "type": "string",
                        "description": "The document body as markdown (the primary input)",
                    },
                    "html": {
                        "type": "string",
                        "description": "Alternative to markdown: HTML (sanitized before use)",
                    },
                    "source": {
                        "type": "string",
                        "description": "Instead of markdown: a knowledge item id or TEXT artifact slug to export as a document",  # noqa: E501
                    },
                    "title": {
                        "type": "string",
                        "description": "Document title; a leading markdown H1 is used when omitted",  # noqa: E501
                    },
                    "format": {
                        "type": "string",
                        "description": "Output format (default 'docx'). Call document_formats to see what is available.",  # noqa: E501
                    },
                    "slug": {
                        "type": "string",
                        "description": "Existing artifact slug to update in place (bumps a version)",  # noqa: E501
                    },
                    "description": {"type": "string", "description": "Optional short description"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name"],
            },
        },
        {
            "name": "sheet_create",
            "annotations": {"readOnlyHint": False},
            "_meta": {BUILDS_META_KEY: True},
            "description": (
                "Generate a real spreadsheet (.xlsx) and save it as a versioned artifact. "
                "Supply `sheets` (JSON text: {sheet name: rows}) for multiple tabs, or `rows` "
                "(JSON text: an array of row arrays) for a single tab, or `csv` text. Row 0 is "
                "treated as the header. KEEP NUMBERS "
                "AS NUMBERS (not strings) so the result can be summed and charted — that "
                "is the main reason to produce a spreadsheet rather than a table. "
                "Re-running with the same `name` (or the same `slug`) updates that "
                "spreadsheet and bumps its version instead of creating a near-duplicate — "
                "to make a SEPARATE spreadsheet, give it a different name. Returns "
                "the slug and a download URL."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Display name for the spreadsheet"},
                    # JSON TEXT, not structured: a sheet map has no portable schema, and a cell
                    # is a number OR a string — carried as JSON, a number stays a number.
                    "sheets": {
                        "type": "string",
                        "description": "Several tabs, as JSON text: an object mapping each sheet name to its rows (an array of row arrays; row 0 = header)",  # noqa: E501
                    },
                    "rows": {
                        "type": "string",
                        "description": "A single tab's rows, as JSON text: an array of row arrays (row 0 = header)",  # noqa: E501
                    },
                    "csv": {"type": "string", "description": "Single-sheet CSV text"},
                    "format": {"type": "string", "description": "Output format (default 'xlsx')"},
                    "slug": {
                        "type": "string",
                        "description": "Existing artifact slug to update in place (bumps a version)",  # noqa: E501
                    },
                    "description": {"type": "string", "description": "Optional short description"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name"],
            },
        },
        {
            "name": "deck_create",
            "annotations": {"readOnlyHint": False},
            "_meta": {BUILDS_META_KEY: True},
            "description": (
                "Generate a real PowerPoint deck (.pptx) from a markdown OUTLINE and save "
                "it as a versioned artifact. Each `##` heading starts a slide, the lines "
                "under it become bullets, and `<!-- notes: ... -->` becomes that slide's "
                "speaker notes. A leading `#` titles the deck. Write an outline, not "
                "prose — paragraphs on a slide are what makes generated decks unreadable. "
                "Re-running with the same `name` (or the same `slug`) updates that deck and "
                "bumps its version instead of creating a near-duplicate — to make a "
                "SEPARATE deck, give it a different name. "
                "Returns the slug and a download URL."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Display name for the deck"},
                    "markdown": {
                        "type": "string",
                        "description": "Outline: `##` per slide, bullets beneath (indent two spaces per sub-level), `<!-- notes: -->` for notes",  # noqa: E501
                    },
                    # Declared down to the bullet: an array must say what its items are, or a
                    # strict provider rejects the whole request (tool_providers.portable_schema).
                    "slides": {
                        "type": "array",
                        "description": "Alternative to markdown: one object per slide — a bullet's `level` is its indent depth (0 = top)",  # noqa: E501
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "body": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "text": {"type": "string"},
                                            "level": {"type": "integer", "minimum": 0},
                                        },
                                        "required": ["text"],
                                    },
                                },
                                "notes": {"type": "string"},
                            },
                        },
                    },
                    "title": {"type": "string", "description": "Deck title slide"},
                    "format": {"type": "string", "description": "Output format (default 'pptx')"},
                    "slug": {
                        "type": "string",
                        "description": "Existing artifact slug to update in place (bumps a version)",  # noqa: E501
                    },
                    "description": {"type": "string", "description": "Optional short description"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name"],
            },
        },
        {
            "name": "document_formats",
            "annotations": {"readOnlyHint": True},
            "description": (
                "List the document formats this instance can actually generate right now. "
                "Check before promising the user a format."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "visualize",
            "annotations": {"readOnlyHint": True},
            "description": (
                "Turn structured DATA into a generative-UI widget (charts, stat tiles, "
                "tables, callouts) rendered inline — the agency-free two-step pattern: you "
                "produce the data, this separate no-tools step renders it. Pass `data` (JSON "
                "text for an object/array, or plain text) and an optional `hint` describing how to present "  # noqa: E501
                "it (e.g. 'show the monthly totals as a bar chart'). Returns a "
                '`<widget kind="genui">` block to embed directly in your reply. Use this '
                "instead of hand-writing a widget when you have data to show; it emits ONLY "
                "registered components, so invalid output is dropped, never rendered."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    # A string: an untyped value has no portable schema, and this data reaches the
                    # rendering model as text anyway (`visualize._coerce_data`).
                    "data": {
                        "type": "string",
                        "description": "The data to visualize: JSON text (an object or array), or plain text",  # noqa: E501
                    },
                    "hint": {
                        "type": "string",
                        "description": "How to present it (chart type, framing, emphasis)",
                    },
                    "title": {
                        "type": "string",
                        "description": "Widget title (default 'Visualization')",
                    },
                },
                "required": ["data"],
            },
        },
    ]


def _read_artifact_content(args: dict[str, Any]) -> tuple[str | None, str | None]:
    """Resolve artifact content from inline ``content`` or a ``content_file``.

    Returns ``(content, error)``. A ``content_file`` is gated by
    ``is_sensitive_path`` before reading (mirrors notify_attachment). ``content`` is None
    when neither was supplied (a metadata-only update). A ``content: null`` is not supplied
    either: read as ``str(None)`` it saved the four characters ``None`` as the body, and wrote
    them into the file a file-backed artifact points at.
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
    if args.get("content") is not None:
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
    from personalclaw import memory_writes

    with memory_writes.ScopeCarryingExecutor() as pool:
        return pool.submit(asyncio.run, coro).result()


def _metered(
    provider: Any,
    model_id: str,
    unit: str,
    quantity: float | None,
    make: Any,
    session_key: str | None,
    *,
    size: str = "",
) -> Any:
    """Run *make* (a coroutine factory: the call to *provider*'s *model_id*) through the metering
    seam every call billed by its unit goes through (``guardrails.media_call``): weighed against
    the dollar caps first when it is unattended work, charged what it cost after, and counted in
    Usage by how many images or seconds of video it made. Raises the seam's refusal."""
    from personalclaw.guardrails.media_call import MediaCall, metered_media_call
    from personalclaw.providers.engines import binding_name

    call = MediaCall(
        provider=binding_name(provider),
        model=model_id,
        unit=unit,
        quantity=quantity,
        size=size,
    )
    billed = len if unit == "image" else None
    return _run_async(metered_media_call(call, make, session_key=session_key or "", billed=billed))


def _refused(exc: BaseException) -> str | None:
    """The sentence a spend ceiling refused a media call with, or None when *exc* is no such
    refusal (it is then raised on)."""
    from personalclaw.guardrails.budgets import BudgetConfigUnreadable
    from personalclaw.guardrails.failure import BudgetExceededError

    if isinstance(exc, BudgetExceededError):
        return exc.sentence()
    if isinstance(exc, BudgetConfigUnreadable):
        return f"{exc}, so nothing was made."
    return None


class _MediaNotSaved(Exception):
    """A generated image or video that was made and could not be saved; its text is the sentence
    saying why, which the materializers' callers answer with."""


def _fetch_generated(url: str, *, what: str, smaller: str) -> tuple[bytes, str]:
    """The whole file at ``url``, the address a provider's answer gave for the ``what`` it made,
    and the content type it came with.

    Fetched through the egress guard under :data:`~personalclaw.net.policy.MEDIA`, the operator's
    own egress settings layered on. A file larger than that cap is refused, never kept in part:
    under CONNECTOR's 10 MB a longer clip was cut short and the cut file saved as the video.
    ``smaller`` is the next step for one over the cap. Raises :class:`_MediaNotSaved`.
    """
    from urllib.parse import urlsplit

    from personalclaw.net import EgressBlocked, fetch
    from personalclaw.net.guard import EGRESS_SETTINGS
    from personalclaw.net.policy import MEDIA, egress_policy_for
    from personalclaw.providers.failure_copy import sentence_with_detail

    policy = egress_policy_for(MEDIA)
    host = urlsplit(url).hostname or "its address"
    try:
        resp = _run_async(fetch(url, policy=policy))
    except EgressBlocked as e:
        if e.decision.category == "unresolvable":
            sentence = (
                f"The {what} was made, but its host {host} couldn't be found, so it was not "
                f"saved. Check this computer's internet connection, then generate the {what} "
                "again."
            )
        else:
            sentence = (
                f"The {what} was made, but PersonalClaw's network settings refused its download "
                f"from {host}, so it was not saved. Check Allowed hosts and Denied hosts in "
                f"{EGRESS_SETTINGS}, then generate the {what} again."
            )
        raise _MediaNotSaved(sentence_with_detail(sentence, e)) from e
    except Exception as e:  # noqa: BLE001 — a transport failure is said, with its words
        raise _MediaNotSaved(
            sentence_with_detail(
                f"The {what} was made, but downloading it from {host} failed, so it was not "
                f"saved. Check this computer's internet connection, then generate the {what} "
                "again.",
                e,
            )
        ) from e
    if resp.truncated:
        raise _MediaNotSaved(
            f"The {what} is larger than {policy.max_bytes // 1_000_000} MB, the most PersonalClaw "
            f"saves from a generation, so it was not saved. {smaller}"
        )
    if resp.status != 200:
        raise _MediaNotSaved(
            f"The {what} was made, but its download from {host} answered HTTP {resp.status}, so "
            f"it was not saved. Generate the {what} again: the address a provider hands back can "
            "expire."
        )
    if not resp.body:
        raise _MediaNotSaved(
            f"The {what}'s download from {host} came back empty, so nothing was saved. Generate "
            f"the {what} again."
        )
    return resp.body, resp.headers.get("Content-Type", "").split(";")[0].strip()


def _materialize_image(result: Any) -> tuple[bytes, str] | None:
    """Turn an ImageResult into ``(bytes, mime)``, fetching/decoding as needed.

    A provider returns one of: inline b64 (decode), a (possibly expiring) url
    (fetch through the egress chokepoint immediately so delivery survives expiry),
    or a local_path (read). Returns None if nothing resolved; a download that failed raises
    :class:`_MediaNotSaved` saying why.
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
        data, ct = _fetch_generated(url, what="image", smaller="Generate a smaller image.")
        return data, (ct or mime)
    local = getattr(result, "local_path", "") or ""
    if local:
        try:
            return Path(local).read_bytes(), mime
        except OSError:
            return None
    return None


#: How the agent makes the next version of a BINARY artifact: the tool, and what to pass it. Its
#: body is bytes, so `artifact_update` (text) refuses it. An image's depends on the model that
#: makes it (:func:`_image_next_version`). `video` has no entry: no tool makes a video's next
#: version (`video_generate` always saves a new video).
_BINARY_NEXT_VERSION: dict[str, str] = {
    "docx": "call document_create with slug='{slug}' and the new markdown",
    "pdf": "call document_create with slug='{slug}', format='pdf' and the new markdown",
    "xlsx": "call sheet_create with slug='{slug}' and the new rows",
    "pptx": "call deck_create with slug='{slug}' and the new outline",
}

#: An image's next version: the image itself, changed by a model that edits images, or a new image
#: from a prompt, which every image model can make.
_IMAGE_EDIT = "call image_generate with slug='{slug}', edit=true and a prompt saying what to change"
_IMAGE_REMAKE = (
    "call image_generate with slug='{slug}' and a prompt that describes the whole picture with "
    "the change made"
)

#: Why a model that edits no image makes the next version from a prompt that describes it all.
_MAKES_NEW_ONLY = (
    "the model chosen under Image · Generation makes new images from a prompt and edits none, so "
    "it never sees the current one"
)

#: What is true of a kind no tool makes a next version of.
_NO_NEXT_VERSION = "no tool makes a new version of a video: video_generate saves a new one"


def _image_next_version(slug: str, edits: bool | None) -> str:
    """How image *slug*'s next version is made on the bound image model: an edit when that model is
    known to edit images (*edits*), else a new image from a prompt saved as that version, with the
    reason when the model is known to edit none."""
    if edits:
        return _IMAGE_EDIT.format(slug=slug)
    how = _IMAGE_REMAKE.format(slug=slug)
    return f"{how}: {_MAKES_NEW_ONLY}" if edits is False else how


def image_model_edits() -> bool | None:
    """Whether the model bound to Image · Generation edits images
    (:func:`~personalclaw.image_gen.registry.active_model_edits`), asked from this sync tool code.
    """
    from personalclaw.image_gen.registry import active_model_edits

    return _run_async(active_model_edits())


def next_version_instruction(kind: str, slug: str, image_edits: bool | None) -> str:
    """What the agent does to land a change as the next version of artifact *slug* of *kind*, or
    ``""`` when no tool makes one (a video). *image_edits* is whether the bound image model edits
    images (:func:`image_model_edits`); only an image's instruction depends on it.

    One phrase per kind, shared by the refusal ``artifact_update`` gives a binary artifact, each
    image ``image_generate`` saves and the Iterate panel's opening prompt, so none of them can
    name another tool, or a way the bound image model cannot take.
    """
    from personalclaw.artifacts.models import is_binary_kind

    if not is_binary_kind(kind):
        return f"call artifact_update with slug='{slug}' and the new content"
    if kind == "image":
        return _image_next_version(slug, image_edits)
    how = _BINARY_NEXT_VERSION.get(kind)
    return how.format(slug=slug) if how else ""


def _a(word: str) -> str:
    """*word* with its indefinite article: "an image", "a docx"."""
    return f"{'an' if word[:1].lower() in 'aeiou' else 'a'} {word}"


def _change_it(kind: str, slug: str, subject: str = "it") -> str:
    """The sentence saying how to change artifact *slug* of *kind* itself."""
    edits = image_model_edits() if kind == "image" else None
    how = next_version_instruction(kind, slug, edits)
    return f"To change {subject}, {how}." if how else f"And {_NO_NEXT_VERSION}."


def _kind_refusal(slug: str, kind: str) -> str:
    """``artifact_update``'s answer to a text body for a binary artifact, in words to act on."""
    return (
        f"'{slug}' is {_a(kind)} artifact: each of its versions is {_a(kind)}, so text cannot "
        f"be one of them. {_change_it(kind, slug)} To keep this text as well, save it as its own "
        "artifact with artifact_save."
    )


def iterate_instruction(kind: str, slug: str, image_edits: bool | None) -> str:
    """The Iterate panel's opening prompt for artifact *slug* of *kind*: the slug and the tool
    that makes its next version, so the change lands on it rather than as a near-duplicate. For
    an image, the way the bound model takes (*image_edits*, :func:`next_version_instruction`)."""
    how = next_version_instruction(kind, slug, image_edits)
    if not how:
        return (
            f"Iterate on artifact `{slug}`. Note that {_NO_NEXT_VERSION}. "
            "What would you like changed?"
        )
    return (
        f"Iterate on artifact `{slug}`. So the change lands as a new version of it rather than "
        f"a new artifact, {how}. What would you like changed?"
    )


def _call_tool_inner(name: str, args: dict[str, Any]) -> str:
    """Dispatch artifact_* tools directly against the native provider entity.

    In-process (no HTTP round-trip); attributed as the agent so updates snapshot
    and emit 'iterated' lifecycle events.
    """
    import json as _json

    from personalclaw.artifacts import registry

    # The projection every artifact read shows and the store's `update` undoes, so a body the agent
    # was shown and sends back through artifact_update keeps every hidden value.
    from personalclaw.artifacts.models import redacted
    from personalclaw.sel import sel

    prov = registry.get_provider("native")
    if prov is None:
        return tool_failure("artifact provider unavailable")
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
                return tool_failure(f"{err}")
            if content is None:
                _audit("denied", error="no content")
                return tool_failure("provide content or content_file")
            # Same-deliverable dedup (#290): a save tagged `loop:<id>` whose bytes are
            # already in the library under that same tag IS that artifact, whatever the
            # model chose to call it. The framework's completion-time graduation of a
            # loop's deliverable carries the same tag, so without this the two writers
            # produce two byte-identical rows for one document — and `find_similar` below
            # cannot see it, because it matches on the NAME's slug and the two names
            # differ. Updated in place rather than refused with a hint: the worker is
            # ending its turn, so a hint has no next turn to land in.
            if not args.get("slug") and not args.get("force"):
                same = artifact_dedupe.find_same_deliverable(
                    prov, tags=args.get("tags"), content=content or ""
                )
                if same is not None:
                    upd = prov.update(
                        same.slug,
                        content=content,
                        snapshot=True,
                        event_type="iterated",
                        actor="agent",
                        session_id=sk,
                        tags=sorted(set(same.tags) | set(args.get("tags") or [])),
                    )
                    _audit("deduped", same.slug)
                    version = upd.version if upd is not None else same.version
                    return (
                        f"That content is already saved as '{redacted(same.name)}' "
                        f"(slug: {same.slug}) under the same tag — updated it in place "
                        f"(version {version}) instead of saving a duplicate."
                    )
            # List-before-save dedup (ARTIFACTS S1): a fresh save (no explicit slug,
            # not forced) whose name matches an existing artifact refuses with a hint
            # so the agent updates the existing one instead of minting a "-2" twin.
            if not args.get("slug") and not args.get("force"):
                similar = prov.find_similar(args["name"], kind=args.get("kind", "widget"))
                if similar is not None:
                    _audit("deduped", similar.slug)
                    return (
                        f"An artifact named '{redacted(similar.name)}' already exists "
                        f"(slug: {similar.slug}). To revise it, call artifact_update with "
                        f"slug='{similar.slug}'. To save a NEW separate artifact anyway, "
                        f"call artifact_save again with force=true."
                    )
            art = prov.create(
                name=args["name"],
                content=content or "",
                kind=args.get("kind", "widget"),
                source="chat",
                slug=args.get("slug"),
                description=args.get("description", ""),
                tags=args.get("tags"),
                collection=args.get("collection", ""),
                actor="agent",
                session_id=sk,
                # Tie the artifact to the active Project so it surfaces in the
                # Project detail page. Bound per-turn by the native runtime; "" when
                # the session isn't scoped to a project.
                project_id=_current_project_id(),
            )
            _audit("success", art.slug)
            return (
                f"Saved artifact '{redacted(art.name)}' (slug: {art.slug}, version {art.version})."
            )

        if name == "artifact_get":
            got = prov.get(args["slug"], version=args.get("version"))
            if got is None:
                _audit("not_found", args["slug"])
                return tool_failure(f"Artifact not found: {args['slug']}")
            _audit("success", got.slug)
            return redacted(got.content)

        if name == "artifact_update":
            content, err = _read_artifact_content(args)
            if err:
                _audit("denied", args.get("slug", ""), err)
                return tool_failure(f"{err}")
            try:
                upd = prov.update(
                    args["slug"],
                    content=content,
                    snapshot=True,  # every agent update is a checkpoint
                    description=args.get("description"),
                    tags=args.get("tags"),
                    collection=args.get("collection"),
                    actor="agent",
                    session_id=sk,
                )
            except ArtifactKindMismatch as mismatch:
                _audit("denied", mismatch.slug, str(mismatch))
                return tool_failure(_kind_refusal(mismatch.slug, mismatch.kind))
            if upd is None:
                _audit("not_found", args["slug"])
                return tool_failure(f"Artifact not found: {args['slug']}")
            _audit("success", upd.slug)
            return f"Updated artifact '{redacted(upd.name)}' → version {upd.version}."

        if name == "artifact_list":
            arts = prov.list(
                tag=args.get("tag"),
                kind=args.get("kind"),
                q=args.get("q"),
                collection=args.get("collection"),
            )
            _audit("success")
            if not arts:
                return "No artifacts found."
            rows = [
                {
                    "slug": a.slug,
                    "name": redacted(a.name),
                    "kind": a.kind,
                    "version": a.version,
                    # Masked like the REST row's tags (`handlers._serialize`).
                    "tags": [redacted(t) for t in a.tags],
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
                f"Deleted artifact: {args['slug']}"
                if ok
                else tool_failure(f"Artifact not found: {args['slug']}")
            )

        if name == "image_generate":
            return _image_generate(prov, args, sk, _audit)

        if name == "video_generate":
            return _video_generate(prov, args, sk, _audit)

        if name in ("document_create", "sheet_create", "deck_create"):
            return _document_create(prov, name, args, sk, _audit)

        if name == "document_formats":
            from personalclaw.documents import available_formats

            fmts = available_formats()
            _audit("success")
            return "Available document formats: " + (", ".join(fmts) if fmts else "none")

        if name == "visualize":
            return _visualize(args, _audit)

    except (ValueError, PermissionError) as e:
        _audit("error", args.get("slug", ""), str(e))
        return tool_failure(f"{e}")

    return f"Unknown artifact tool: {name}"


#: One of ``image_generate``'s result sentences, with the version it names and its slug: "Generated
#: image '…' (slug: …)" (a new image, so version 1), "Generated image '…' → version N (slug: …)",
#: "Edited image artifact '…' → version N (slug: …)" and "Regenerated image '…' → version N (slug:
#: …)" below. Parsed back out of a chat's tool rows by :func:`images_made_in` and
#: :func:`image_prompt`, so the parser lives beside the sentences it reads.
_IMAGE_RESULT_RE = re.compile(
    r"^(?:Generated image|Edited image artifact|Regenerated image) '.*?'"
    r"(?: → version (?P<version>\d+))? \(slug: (?P<slug>[a-z0-9-]+)\)",
    re.DOTALL,
)


class _MadeImage(NamedTuple):
    """An image ``image_generate`` saved, as a chat's rows record it: its slug, the version it
    saved, whether that version was an edit of the one before, and the call's arguments."""

    slug: str
    version: int
    edited: bool
    args: dict[str, Any]


def _images_made(messages: list[dict]) -> list[_MadeImage]:
    """Each image ``image_generate`` saved in *messages* (a chat's rows), in order.

    An approved call is recorded as two rows that share its ``tool_call_id``: the call, which
    carries its ``input``, and the row its result lands on after the approval, which carries its
    ``output``. A result is read with the input of the nearest call row before it by that id."""
    import json as _json

    inputs: dict[str, Any] = {}
    made: list[_MadeImage] = []
    for msg in messages:
        meta = msg.get("meta") if isinstance(msg, dict) else None
        if not isinstance(meta, dict) or msg.get("role") != "tool":
            continue
        call_id = str(meta.get("tool_call_id") or "")
        if call_id and meta.get("input"):
            inputs[call_id] = meta["input"]
        found = _IMAGE_RESULT_RE.match(str(meta.get("output") or ""))
        if not found or not is_valid_slug(found.group("slug")):
            continue
        raw = meta.get("input") or inputs.get(call_id, "")
        try:
            args = _json.loads(raw) if isinstance(raw, str) and raw else raw
        except ValueError:
            args = {}
        made.append(
            _MadeImage(
                slug=found.group("slug"),
                version=int(found.group("version") or 1),
                edited=found.group(0).startswith("Edited"),
                args=args if isinstance(args, dict) else {},
            )
        )
    return made


def images_made_in(messages: list[dict]) -> list[str]:
    """The image artifacts *messages* (one answer's rows) generated or edited, in order.

    Read from each tool row's recorded result, which names its slug; a slug named twice is
    listed once. What a Regenerate of that answer retakes (:mod:`personalclaw.artifacts.retakes`).
    """
    out: list[str] = []
    for image in _images_made(messages):
        if image.slug not in out:
            out.append(image.slug)
    return out


def _prompt_of(image: _MadeImage) -> str:
    return str(image.args.get("prompt") or "").strip()


def image_prompt(messages: list[dict], slug: str, version: int) -> tuple[str, bool] | None:
    """The prompt ``image_generate`` was given for version *version* of image *slug*, and whether
    that version was an edit of the one before it, read from one chat's rows (*messages*); None
    when no row there made that version."""
    for image in reversed(_images_made(messages)):
        if image.slug == slug and image.version == version:
            return (_prompt_of(image), image.edited) if _prompt_of(image) else None
    return None


def image_remake_args(messages: list[dict], slug: str) -> dict[str, str] | None:
    """The prompt and size to make image *slug* again from scratch, read from one chat's rows
    (*messages*): the newest call that made it new from a prompt, else the newest edit of it,
    whose prompt assumes the image it changed. None when no row there made it."""
    made = [i for i in _images_made(messages) if i.slug == slug and _prompt_of(i)]
    pick = next((i for i in reversed(made) if not i.edited), made[-1] if made else None)
    if pick is None:
        return None
    return {"prompt": _prompt_of(pick), "size": str(pick.args.get("size") or "").strip()}


def _other_model_refusal(model_ref: str) -> str:
    """Why the media model ``model_ref`` may not be handed this work, or ``""`` when it may.

    An image or video model is a model beside the one a chat's turn runs on, so a chat that is
    Incognito or Temporary sends it nothing (:func:`personalclaw.memory_writes.model_may_read`)."""
    from personalclaw import memory_writes

    if memory_writes.model_may_read(model_ref):
        return ""
    return memory_writes.other_model_refusal(model_ref)


def _image_request_refusal(
    prov: Any, args: dict[str, Any], edits: bool | None, model_ref: str
) -> str:
    """What ``image_generate`` refuses of *args* before anything is sent, or ``""``: an edit that
    names no image, a ``slug`` that names no image artifact, and an edit for the bound model
    *model_ref* when it edits none (*edits*, :func:`image_model_edits`).

    The same check runs before anyone is asked to approve the call (:func:`_preflight`) and as the
    call runs, so the two answers cannot drift. A model that edits none was asked anyway, after
    the call was approved, and refused, so the agent saved a second image instead of the next
    version of the one it was asked to change.
    """
    slug = str(args.get("slug") or "").strip()
    edit = args.get("edit") is True
    if edit and not slug:
        return (
            "edit=true changes an existing image: pass its slug too, or leave out edit to make "
            "a new image."
        )
    if slug:
        target = prov.get(slug)
        if target is None:
            return (
                f"'{slug}' is not an existing image artifact. Leave out slug to make a new image."
            )
        if target.kind != "image":
            return (
                f"'{slug}' is {_a(target.kind)} artifact, so an image cannot be its next version. "
                "Leave out slug to make a new image. "
                f"{_change_it(target.kind, slug, f'{slug!r} itself')}"
            )
    if edit and edits is False:
        return (
            f"The model chosen under Image · Generation ({model_ref}) makes new images from a "
            f"prompt and edits none, so nothing was sent. To make the next version of '{slug}', "
            f"{_IMAGE_REMAKE.format(slug=slug)}: the model never sees the current one."
        )
    return ""


def _image_landed(head: str, art: Any, edits: bool | None) -> str:
    """The result of an image ``image_generate`` saved: *head*, the sentence saying what was saved;
    how its next version is made on the bound model (*edits*); and a ready-to-embed markdown image
    so the picture shows inline in chat (the image renderer gates the src and styles it).

    The image is pinned to the version made (``?version=N``), never the live ``/raw``, so each chat
    message stays bound to the image it produced (an immutable transcript) and the versioned
    ``/raw`` is hard-cacheable. This is the primary delivery surface; for a channel the on-disk
    artifact body is the materialized cache.
    """
    raw_url = f"/api/artifacts/{art.slug}/raw?version={art.version}"
    return (
        f"{head} To make its next version, {_image_next_version(art.slug, edits)}.\n\n"
        "Show it to the user by embedding this markdown image in your reply:\n"
        f"![{art.name}]({raw_url})"
    )


def _image_generate(prov: Any, args: dict[str, Any], sk: str | None, _audit: Any) -> str:
    """image_generate: resolve the image_gen capability, generate or edit, save kind:image.

    Thin wrapper over the capability (image_gen/registry.active_image_gen) — the
    real work is the provider's; this materializes the result + lands a versioned
    binary artifact + returns the slug.

    The image lands as the next version of the image artifact ``slug`` names: a new image from
    the prompt, or, with ``edit``, that image changed by a model that edits images. Without
    ``slug`` it is a new image, except in a turn that REGENERATES an answer, where it lands as the
    next version of the image that answer made (:mod:`personalclaw.artifacts.retakes`).
    """
    import tempfile
    from pathlib import Path

    from personalclaw.image_gen.provider import ImageGenError
    from personalclaw.image_gen.registry import active_image_gen

    resolved = active_image_gen()
    if resolved is None:
        _audit("denied", error="no image_gen model configured")
        return tool_failure(
            "no image-generation model is configured. Choose one under Image · Generation "
            "in Settings → Models."
        )
    provider, model_id = resolved
    model_ref = f"{provider.name}:{model_id}"
    refused = _other_model_refusal(model_ref)
    if refused:
        _audit("denied", error="the chat's words go to its own model only")
        return tool_failure(refused)
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        _audit("denied", error="empty prompt")
        return tool_failure("provide a non-empty prompt.")
    size = str(args.get("size", "")).strip()
    slug = str(args.get("slug", "")).strip()
    edit = args.get("edit") is True
    edits = image_model_edits()
    unmade = _image_request_refusal(prov, args, edits, model_ref)
    if unmade:
        _audit("denied", slug, unmade)
        return tool_failure(unmade)

    try:
        if edit:
            raw = prov.raw_bytes(slug)
            if raw is None:
                return tool_failure(f"could not read source image {slug!r}.")
            src_bytes, src_mime = raw
            from personalclaw.artifacts.models import ext_for_mime

            with tempfile.NamedTemporaryFile(
                suffix=f".{ext_for_mime(src_mime)}", delete=False
            ) as tf:
                tf.write(src_bytes)
                src_path = tf.name
            try:
                results = _metered(
                    provider,
                    model_id,
                    "image",
                    1,
                    lambda: provider.edit(prompt, source_image=src_path, model=model_id, size=size),
                    sk,
                    size=size,
                )
            finally:
                with __import__("contextlib").suppress(OSError):
                    Path(src_path).unlink()
        else:
            results = _metered(
                provider,
                model_id,
                "image",
                1,
                lambda: provider.generate(prompt, model=model_id, size=size),
                sk,
                size=size,
            )
    except ImageGenError as e:
        _audit("error", slug, str(e))
        return tool_failure(f"{e}")
    except Exception as e:
        refusal = _refused(e)
        if refusal is None:
            raise
        _audit("denied", slug, refusal)
        return tool_failure(refusal)

    if not results:
        _audit("error", slug, "no image returned")
        return tool_failure("the image provider returned no image.")

    try:
        materialized = _materialize_image(results[0])
    except _MediaNotSaved as e:
        _audit("error", slug, str(e))
        return tool_failure(f"{e}")
    if materialized is None:
        _audit("error", slug, "could not materialize image")
        return tool_failure("generated image could not be saved (no resolvable bytes).")
    data, mime = materialized
    revised = getattr(results[0], "revised_prompt", "") or ""
    note = f" The provider revised the prompt to: {revised}." if revised else ""

    if slug:
        # This call names the image it versions: a regenerated answer's record of that image is
        # taken here, so a later call in the turn that names none cannot land on it too.
        retakes.take_retake(sk or "", named=slug)
        art = prov.update_binary(slug, data=data, mime=mime, actor="agent", session_id=sk)
        if art is None:
            return tool_failure(f"could not save the image as the next version of {slug!r}.")
        _audit("success", art.slug)
        if edit:
            head = f"Edited image artifact '{art.name}' → version {art.version} (slug: {art.slug})."
        else:
            head = (
                f"Generated image '{art.name}' → version {art.version} (slug: {art.slug}) via "
                f"{model_ref}: a new image from the prompt, saved as that artifact's next "
                f"version.{note}"
            )
        return _image_landed(head, art, edits)
    retake = retakes.take_retake(sk or "")
    retaken = prov.get(retake) if retake else None
    if retaken is not None and retaken.kind == "image":
        art = prov.update_binary(retake, data=data, mime=mime, actor="agent", session_id=sk)
        if art is not None:
            _audit("success", art.slug)
            return _image_landed(
                f"Regenerated image '{art.name}' → version {art.version} (slug: {art.slug}). "
                "This turn regenerates an earlier answer, so the image is saved as the next "
                "version of the one that answer made.",
                art,
                edits,
            )
    art = prov.create_binary(
        name=str(args.get("name", "")).strip() or prompt[:60],
        data=data,
        mime=mime,
        kind="image",
        source="chat",
        actor="agent",
        session_id=sk,
    )
    _audit("success", art.slug)
    return _image_landed(
        f"Generated image '{art.name}' (slug: {art.slug}) via {model_ref}.{note}", art, edits
    )


def _materialize_video(result: Any) -> tuple[bytes, str] | None:
    """Turn a VideoResult into ``(bytes, mime)``, fetching as needed.

    A provider returns a (possibly expiring) url or a local_path. Fetch through
    the egress chokepoint immediately so delivery survives expiry. Returns None if
    nothing resolved; a download that failed raises :class:`_MediaNotSaved` saying why.
    """
    from pathlib import Path

    mime = getattr(result, "mime", "") or "video/mp4"
    url = getattr(result, "url", "") or ""
    if url:
        data, ct = _fetch_generated(
            url, what="video", smaller="Generate a shorter or lower-resolution video."
        )
        return data, (ct or mime)
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
        return tool_failure(
            "no video-generation model is configured. Choose one under Video · Generation "
            "in Settings → Models."
        )
    provider, model_id = resolved
    refused = _other_model_refusal(f"{provider.name}:{model_id}")
    if refused:
        _audit("denied", error="the chat's words go to its own model only")
        return tool_failure(refused)
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        _audit("denied", error="empty prompt")
        return tool_failure("provide a non-empty prompt.")
    duration_seconds = float(args.get("duration_seconds", 5.0))
    aspect_ratio = str(args.get("aspect_ratio", "")).strip()

    try:
        results = _metered(
            provider,
            model_id,
            "second",
            duration_seconds,
            lambda: provider.generate(
                prompt,
                model=model_id,
                duration_seconds=duration_seconds,
                aspect_ratio=aspect_ratio,
            ),
            sk,
        )
    except VideoGenError as e:
        _audit("error", "", str(e))
        return tool_failure(f"{e}")
    except Exception as e:
        refusal = _refused(e)
        if refusal is None:
            raise
        _audit("denied", "", refusal)
        return tool_failure(refusal)

    if not results:
        _audit("error", "", "no video returned")
        return tool_failure("the video provider returned no video.")

    try:
        materialized = _materialize_video(results[0])
    except _MediaNotSaved as e:
        _audit("error", "", str(e))
        return tool_failure(f"{e}")
    if materialized is None:
        _audit("error", "", "could not materialize video")
        return tool_failure("generated video could not be saved (no resolvable bytes).")
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
    size = (size or "").strip()
    refused = _other_model_refusal(f"{provider.name}:{model_id}")
    if refused:
        return False, refused
    try:
        results = _metered(
            provider,
            model_id,
            "image",
            1,
            lambda: provider.generate(prompt, model=model_id, size=size),
            session_id,
            size=size,
        )
    except ImageGenError as e:
        return False, str(e)
    except Exception as e:
        refusal = _refused(e)
        if refusal is None:
            raise
        return False, refusal
    if not results:
        return False, "the image provider returned no image"
    try:
        materialized = _materialize_image(results[0])
    except _MediaNotSaved as e:
        return False, str(e)
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


def _visualize(args: dict[str, Any], _audit: Any) -> str:
    """`visualize` — the agency-free data→genui-widget primitive (AMBIENT-SURFACES §5.3).

    Thin wrapper over ``personalclaw.visualize.visualize`` (the ONE reasoning-axis
    ``one_shot_completion`` call site — tools disabled by construction). Its degraded
    floor (assistant_reasoning): no model → no visualization produced, and the caller
    keeps the raw data — so this returns an honest, actionable message rather than
    fabricating a widget.
    """
    from personalclaw.visualize import GenUiDisabled
    from personalclaw.visualize import visualize as _visualize_primitive

    if "data" not in args:
        _audit("denied", error="no data")
        return tool_failure("provide `data` to visualize.")
    hint = str(args.get("hint", "") or "")
    title = str(args.get("title", "") or "Visualization")
    try:
        result = _run_async(_visualize_primitive(args["data"], hint, title=title))
    except GenUiDisabled as e:
        # Named BEFORE the generic arm: the user turned this off, which is not a degraded
        # model stack, and the "bind a reasoning model" advice below would send them to the
        # wrong Settings page for a switch they set themselves.
        _audit("denied", error="genui disabled")
        return tool_failure(str(e))
    except Exception as e:  # noqa: BLE001 — a model/provider failure is a caller-facing refusal
        _audit("error", error=str(e))
        return tool_failure(
            f"could not produce a visualization ({e}). No reasoning model may be "
            "configured — bind one in Settings → Models, or present the data as text."
        )
    if not result.dsl.strip():
        _audit("error", error="empty visualization")
        return tool_failure(
            "the model produced no renderable components. Present the data as text instead."
        )
    _audit("success")
    return (
        "Show this to the user by embedding the widget block below in your reply:\n\n"
        f"{result.widget}"
    )


def _validate_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate tool arguments against the shared MCP schema (enforces e.g. the
    artifact_save ``kind`` enum). Tools without a schema pass through."""
    from personalclaw.validation import MCP_CORE_SCHEMAS, validate_tool_args

    schema = MCP_CORE_SCHEMAS.get(name)
    if schema:
        return validate_tool_args(args, schema)
    return args


def _preflight(name: str, raw_args: dict[str, Any]) -> Any:
    """What these tools refuse before anyone is asked to approve a call: a tool this leaf may not
    call, arguments the tool's schema refuses (``mcp_shared.admitted_arguments``), and an image
    ``image_generate`` will refuse to make (:func:`_image_request_refusal`).

    A call with no image model bound is left to the call, which says so itself."""
    from personalclaw.artifacts import registry
    from personalclaw.image_gen.registry import active_image_gen
    from personalclaw.mcp_shared import admitted_arguments

    args = admitted_arguments(name, raw_args, _validate_args)
    if isinstance(args, ToolFailure):
        return args
    if name != "image_generate":
        return None
    prov = registry.get_provider("native")
    resolved = active_image_gen()
    if prov is None or resolved is None:
        return None
    provider, model_id = resolved
    edits = image_model_edits() if args.get("edit") is True else None
    refusal = _image_request_refusal(prov, args, edits, f"{provider.name}:{model_id}")
    return tool_failure(refusal) if refusal else None


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


#: MIME type for each generated document format.
_DOC_MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
}


def _bullet(entry: Any) -> Any:
    """One `deck_create` body line → a `Bullet`.

    Accepts a plain string OR `{"text": …, "level": n}`, because an agent writing a nested
    outline has to be able to SAY the depth: the model carries it now, and a string-only
    input would have made the flat deck the only reachable one from this tool.
    """
    from personalclaw.documents.model import Bullet

    if isinstance(entry, dict):
        return Bullet(text=str(entry.get("text") or ""), level=int(entry.get("level") or 0))
    return Bullet(text=str(entry))


def _document_create(
    prov: Any, name: str, args: dict[str, Any], sk: str | None, _audit: Any
) -> str:
    """`document_create` / `sheet_create` — render a real file and store it as an artifact.

    The agent supplies markdown (its strongest output) or a declarative model; code does
    the rendering. It never emits OOXML, and no vendor format string appears outside
    ``documents/writers/``.

    The reply carries slug + version + the raw URL — NEVER the bytes and never base64.
    Generated document bytes must not enter a prompt (CONTEXT-ECONOMY); when the agent
    needs the content back it goes through the existing read path.
    """
    from personalclaw.artifacts.models import (
        MAX_BINARY_CONTENT_BYTES,
        MAX_CONTENT_BYTES,
        is_binary_kind,
    )
    from personalclaw.documents import available_formats, get_writer
    from personalclaw.documents.from_markup import document_from_html, document_from_markdown
    from personalclaw.documents.model import SheetModel
    from personalclaw.web.extract import SanitizerUnavailable

    _default_fmt = {"sheet_create": "xlsx", "deck_create": "pptx"}.get(name, "docx")
    fmt = str(args.get("format") or _default_fmt).lower()
    writer = get_writer(fmt)
    if writer is None:
        _audit("denied", error=f"unsupported format {fmt}")
        return tool_failure(
            f"no writer for format {fmt!r}. Available: "
            f"{', '.join(available_formats()) or 'none'}."
        )

    # Build the model from whichever input the caller supplied.
    if name == "deck_create":
        from personalclaw.documents.from_markup import deck_from_markdown
        from personalclaw.documents.model import DeckModel, Slide

        markdown = str(args.get("markdown") or "")
        slides_in = args.get("slides")
        if markdown.strip():
            model: Any = deck_from_markdown(markdown, title=str(args.get("title") or ""))
        elif isinstance(slides_in, list) and slides_in:
            model = DeckModel(
                title=str(args.get("title") or ""),
                slides=[
                    Slide(
                        title=str(sl.get("title") or ""),
                        # A body line is either plain text or `{"text": …, "level": n}`,
                        # so an agent that has a nested outline can say so — the depth is
                        # the model's field now, not something the writer flattens.
                        bullets=[_bullet(b) for b in (sl.get("body") or [])],
                        notes=str(sl.get("notes") or ""),
                        artifact_slug=str(sl.get("artifact_slug") or ""),
                    )
                    for sl in slides_in
                    if isinstance(sl, dict)
                ],
            )
        else:
            _audit("denied", error="no deck input")
            return tool_failure("provide markdown or slides.")
    elif name == "sheet_create":
        # Declared JSON text (a sheet map has no portable schema); a structured value from a
        # caller that is not a model passes through `decode_json_text` untouched.
        sheets = decode_json_text(args.get("sheets"))
        rows = decode_json_text(args.get("rows"))
        csv_text = str(args.get("csv") or "")
        # ``from_rows`` rather than the constructor: the agent supplies DATA, and a raw
        # value that looks like a formula stays a literal. A cell becomes a formula only
        # when something declares it one - see SheetCell.
        if isinstance(sheets, dict) and sheets:
            model = SheetModel.from_rows({str(k): list(v or []) for k, v in sheets.items()})
        elif isinstance(rows, list) and rows:
            model = SheetModel.from_rows({"Sheet1": [list(r) for r in rows]})
        elif csv_text.strip():
            # Annotated as list[object] rows: SheetCell preserves cell TYPES, so its
            # row type is invariant-unfriendly to a narrower list[str].
            parsed: list[list[object]] = [
                [c.strip() for c in line.split(",")]
                for line in csv_text.replace("\r\n", "\n").split("\n")
                if line.strip()
            ]
            model = SheetModel.from_rows({"Sheet1": parsed})
        else:
            _audit("denied", error="no sheet input")
            return tool_failure("provide sheets, rows, or csv.")
    else:
        markdown = str(args.get("markdown") or "")
        html = str(args.get("html") or "")
        source = str(args.get("source") or "").strip()
        if source:
            # The round-trip: an existing knowledge item or text artifact becomes
            # a real document. Resolved to markdown here so it flows through the SAME
            # writer path as everything else — no parallel export pipeline.
            resolved, title = _resolve_document_source(prov, source)
            if resolved is None:
                _audit("denied", error=f"source not found: {source}")
                return tool_failure(
                    f"no knowledge item or text artifact matches {source!r}. "
                    "Pass a knowledge item id or an artifact slug."
                )
            markdown = resolved
            # Pass NO title when the source body already opens with an H1: the markdown
            # parser promotes that H1 to the document title itself, so supplying the
            # item's name as well printed the same heading twice.
            explicit = str(args.get("title") or "")
            starts_with_h1 = markdown.lstrip().startswith("# ")
            model = document_from_markdown(
                markdown, title=explicit or ("" if starts_with_h1 else title)
            )
        elif markdown.strip():
            model = document_from_markdown(markdown, title=str(args.get("title") or ""))
        elif html.strip():
            try:
                model = document_from_html(html, title=str(args.get("title") or ""))
            except SanitizerUnavailable as exc:
                # HTML nothing can sanitize makes no document: a failed call in the refusal's
                # words, like the writer's failure below, and nothing is stored.
                _audit("error", error=str(exc))
                return tool_failure(str(exc))
        else:
            _audit("denied", error="no document input")
            return tool_failure("provide markdown, html, or source.")

    binary = is_binary_kind(fmt)
    try:
        data = writer(model)
        text_content = None if binary else data.decode("utf-8")
    except Exception as e:  # noqa: BLE001 — a writer failure is a caller-facing refusal
        _audit("error", error=str(e))
        return tool_failure(f"could not render the {fmt}: {e}")

    size_cap = MAX_BINARY_CONTENT_BYTES if binary else MAX_CONTENT_BYTES
    if len(data) > size_cap:
        mb, cap = len(data) / 1_048_576, size_cap / 1_048_576
        _audit("denied", error=f"oversized {len(data)}")
        return tool_failure(
            f"the generated {fmt} came to {mb:.1f}MB (cap {cap:.0f}MB). "
            "Reduce the content or split it across documents."
        )

    display_name = str(args.get("name") or "").strip() or f"Untitled {fmt}"
    slug = str(args.get("slug") or "").strip()
    # Re-generating the same document UPDATES in place and bumps a version rather than
    # minting a "-2" twin — the same dedup posture artifact_save takes. An explicit slug
    # names the target directly; WITHOUT one the collision is resolved by NAME, through the
    # very `prov.find_similar` call `artifact_save` makes above, so there is one dedup
    # implementation rather than two that can disagree. Scoped to this format's kind so a
    # regenerated pptx never swallows a same-named markdown, and to this turn's PROJECT so
    # it never swallows another Project's (#3309) — `_current_project_id()` returns a str,
    # so an unscoped session passes "" and dedups against unscoped artifacts only.
    #
    # Updated in place rather than refused with a hint — `artifact_save`'s answer — because
    # the hint has nowhere to land: a document tool's whole job this turn is to produce the
    # file, and a caller who really wants a second document of the same name says so by
    # passing a new `slug`.
    target = ""
    if slug:
        named = prov.get(slug)
        # A slug names the artifact this file becomes the next version OF, and a version is of
        # its artifact's kind: a docx cannot be an image's next version, nor a csv a markdown
        # note's. Said before anything is rendered into the store, with the way to do each.
        if named is not None and named.kind != fmt:
            _audit("denied", slug, f"{slug} is kind {named.kind}, not {fmt}")
            return tool_failure(
                f"'{slug}' is {_a(named.kind)} artifact, so {_a(fmt)} cannot be its next "
                f"version. Leave out slug to make a new {fmt}. "
                f"{_change_it(named.kind, slug, f'{slug!r} itself')}"
            )
        target = slug if named is not None else ""
    if not slug:
        similar = prov.find_similar(display_name, kind=fmt, project_id=_current_project_id())
        if similar is not None:
            target = similar.slug
    if target:
        if binary:
            # No `snapshot=` argument: update_binary ALWAYS bumps the version and writes a
            # snapshot (there is no non-snapshotting binary update, because a binary body
            # has no diffable draft state to hold back).
            art = prov.update_binary(
                target,
                data=data,
                mime=_DOC_MIME.get(fmt, "application/octet-stream"),
                event_type="iterated",
                actor="agent",
                session_id=sk,
            )
        else:
            art = prov.update(
                target,
                content=text_content,
                snapshot=True,
                event_type="iterated",
                actor="agent",
                session_id=sk,
            )
    else:
        if binary:
            art = prov.create_binary(
                name=display_name,
                data=data,
                mime=_DOC_MIME.get(fmt, "application/octet-stream"),
                kind=fmt,
                source="chat",
                slug=slug or None,
                description=str(args.get("description") or ""),
                tags=args.get("tags") or None,
                actor="agent",
                session_id=sk,
                project_id=_current_project_id(),
            )
        else:
            art = prov.create(
                name=display_name,
                content=text_content or "",
                kind=fmt,
                source="chat",
                slug=slug or None,
                description=str(args.get("description") or ""),
                tags=args.get("tags") or None,
                actor="agent",
                session_id=sk,
                project_id=_current_project_id(),
            )
    _audit("success", art.slug)
    # "Created" vs "Updated": name-dedup makes the update path a routine outcome of a plain
    # repeat call, and an agent told it "Created" a v2 has been told the one thing that is
    # not true about what just happened — it would go looking for a second file.
    verb = "Updated" if target else "Created"
    return (
        f"{verb} {fmt}: {art.slug} (v{art.version}, {len(data) / 1024:.0f}KB). "
        f"Download at /api/artifacts/{art.slug}/raw"
    )


def _resolve_document_source(prov: Any, source: str) -> tuple[str | None, str]:
    """Resolve a knowledge-item id or text-artifact slug to ``(markdown, title)``.

    Powers the round trip: the platform could already READ these formats, and this is what
    finally lets something already in the library come back OUT as a real file. Deliberately
    routed through the existing stores rather than a new export endpoint — the writer path
    is identical to a fresh generation, so a document exported from knowledge is not a
    second-class citizen with its own bugs.

    Knowledge is tried first: its ids are uuids while artifact slugs are kebab-case, so the
    two namespaces don't realistically collide.
    """
    try:
        from personalclaw.knowledge import get_knowledge_store

        item = get_knowledge_store().get_item(source)
        if item:
            title = str(item.get("title") or item.get("url_title") or "")
            body = str(item.get("content") or "")
            summary = str(item.get("summary") or "")
            # A knowledge item's summary is genuinely useful context in an exported
            # document, so lead with it when the body doesn't already start with it.
            if summary and not body.startswith(summary):
                body = f"{summary}\n\n{body}"
            return body, title
    except Exception:  # noqa: BLE001 — a knowledge miss falls through to artifacts
        logger.debug("knowledge lookup failed for %r", source, exc_info=True)

    try:
        art = prov.get(source)
    except (ValueError, PermissionError):
        art = None
    if art is None:
        return None, ""
    from personalclaw.artifacts.models import is_binary_kind

    # A binary artifact's `content` is a raw URL, not text — exporting one would write
    # the URL into the document body. Refuse rather than produce that.
    if is_binary_kind(art.kind):
        return None, ""
    return str(art.content or ""), str(art.name or "")
