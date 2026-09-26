"""File I/O, outbox, upload, workspace CRUD, and file search handlers."""

import asyncio
import contextlib
import errno
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import NamedTuple

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError
from aiohttp.multipart import BodyPartReader

from personalclaw.cancellation import kill_timed_out
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.state import DashboardState
from personalclaw.http_download import attachment_disposition
from personalclaw.http_errors import json_error
from personalclaw.providers.failure_copy import relayed_failure_copy
from personalclaw.request_validation import require_string
from personalclaw.security import (
    is_sensitive_path,
    is_system_path,
    redact_credentials,
    redact_exfiltration_urls,
    system_subtrees,
)
from personalclaw.validation import (
    FILE_READ_SCHEMA,
    ValidationError,
    validate_tool_args,
)

logger = logging.getLogger(__name__)

# Ceiling for reading a file fully into memory to SERVE it inline (image/text
# preview download). Bounded so a huge file can't OOM the server on a read —
# distinct from UPLOAD limits, which are per-filetype via personalclaw.uploads.
_MAX_INLINE_READ_BYTES = 50 * 1024 * 1024

# System roots that the directory-picking / search surfaces refuse to browse,
# search, OR create under — picking a workspace/project folder is for the user's
# own code, never system internals. is_sensitive_path() only covers ~/credential
# dirs, so this is the complementary system-root guard.
#
# The subtree list is read through personalclaw.security.system_subtrees() (the single source
# of truth shared with the Code workspace validation, including its carve-out for the running
# account's own home) so the surfaces can't drift. NOTE: this is a SUBTREE-only check (no
# mount/temp PARENT blocking, unlike security.is_system_path) — the directory BROWSER +
# @-search must be able to navigate INTO /Volumes, /var, /tmp to reach a real workspace beneath
# them. create-dir/workspace-bind use the stricter full is_system_path (parents blocked) since
# you never create/bind AT a bare parent.


def _is_system_root(path: str) -> bool:
    """True iff *path* is the filesystem root or sits under a protected system root
    (an already realpath'd absolute path is expected)."""
    return path == "/" or any(path == r or path.startswith(r + os.sep) for r in system_subtrees())


#: What each directory-picker refusal says: the reason ``sel()`` records → the stable ``reason`` a
#: client branches on, and what the location IS, in words. One table, so the refusal browse-dirs
#: shows and the ones create-dir gives cannot drift apart.
_PROTECTED_LOCATION: dict[str, tuple[str, str]] = {
    "sensitive path": ("sensitive_path", "a location that holds credentials"),
    "system root": ("system_root", "a protected system location"),
}


def _picker_refusal(path: str) -> str | None:
    """Why the directory picker will not open *path* (an already-realpath'd dir), or None.

    Exactly what ``browse-dirs`` refuses. ``create-dir`` asks the same question of the PARENT, so a
    folder can only be created where the picker could have navigated to.
    """
    # Late-bound like the two handlers' own import, so a patched security module reaches it too.
    from personalclaw.security import is_sensitive_path  # noqa: F811

    if is_sensitive_path(path):
        return "sensitive path"
    if _is_system_root(path):
        return "system root"
    return None


def _protected_location(refused: str, path: str, sel_reason: str) -> web.Response:
    """The 403 for a protected location, naming it and saying why.

    🔴 This answered a bare ``{"error": "Access denied"}`` — no path, no reason — and the workspace
    picker rendered exactly that under an empty listing it had never read, so a user whose first
    browse was refused had nothing to act on; the only record of what was refused was a
    ``security_events.jsonl`` row they cannot see. ``path`` and ``reason`` carry the same two facts
    for a client that branches on them. Echoing the path discloses nothing: it is the caller's own
    request, resolved — the form the 404 branch of browse-dirs already returns.
    """
    reason, what = _PROTECTED_LOCATION[sel_reason]
    return json_error(
        "path_protected",
        message=f"{refused} — it's {what}.",
        status=403,
        error_extra={"path": path, "reason": reason},
    )


def _sel():
    """Late-binding _sel() for test monkeypatch compatibility."""
    import personalclaw.dashboard.handlers as _pkg  # noqa: F811

    return _pkg.sel()


#: What the file explorer lets an app reach, as its refusal says it.
_EXPLORER_REACH = (
    "an app reaches only the folders the file explorer shows it, and no credential file in them"
)
#: What ``/api/reveal`` lets an app reach, as its refusal says it (:func:`_in_app_data_folder`).
_REVEAL_REACH = (
    "an app reveals and opens only its own files — those in its data folder, and no credential "
    "file"
)


def _app_path_refusal(raw: str, *, tool: str, reach: str = _EXPLORER_REACH) -> web.Response | None:
    """``403`` and a Security Event Log row when an APP asked for a path the explorer refuses it;
    ``None`` for the owner, whose refusal each endpoint answers exactly as before.

    The owner's answer is a ``400`` that confirms nothing about the path. An app's is an app
    refusal like every other one (the permission middleware, ``apps._foreign_app_config_refusal``):
    the explorer hides the PersonalClaw home from an app (:func:`_dashboard_roots`), so an app
    asking for ``config.json`` is asking for the owner's settings, and the owner's ``400`` with no
    row naming the app made that read as a typo. Decided on who asked and which path only, never
    on whether the file exists, so the ``403`` confirms no more than the ``400`` does.

    Called BEFORE an endpoint writes its own denial row, so an app's refusal is recorded once,
    under the app's name, rather than as a dashboard request. *reach* is the sentence saying what
    this surface lets an app reach, so the refusal names the rule it applied."""
    message = _app_path_refusal_message(raw, tool=tool, reach=reach)
    if not message:
        return None
    return json_error("forbidden", message=message, status=403)


def _app_path_refusal_message(raw: str, *, tool: str, reach: str = _EXPLORER_REACH) -> str:
    """Record an APP's refused path in the Security Event Log and return the sentence its ``403``
    carries; ``""`` for the owner, with nothing recorded. The half of
    :func:`_app_path_refusal` an endpoint with an error envelope of its own (the chunked upload's
    ``UploadError``) uses.

    WHO asked comes from ``permissions.request_app`` — the identity the gateway's permission
    middleware scopes an app's request to — which is the same source :func:`_dashboard_roots`
    filters the roots by. So the refusal cannot call a path an app refusal unless the roots it was
    refused against were an app's, and the reverse."""
    from personalclaw.apps.permissions import request_app

    app_name = request_app()
    if not app_name:
        return ""
    try:
        _sel().log_api_access(
            caller=f"app:{app_name}",
            operation=f"files.{tool}",
            outcome="denied",
            source="app_permissions",
            resources=raw,
            error="outside the folders an app may reach",
        )
    except Exception:
        logger.warning("SEL audit failed for a refused app file access", exc_info=True)
    return f"{reach} — not {raw}"


def _path_home_pclaw() -> Path:
    """Resolve PersonalClaw home dir, honoring PERSONALCLAW_HOME."""
    try:
        from personalclaw.config.loader import config_dir as _cd

        return _cd()
    except Exception:
        return Path.home() / ".personalclaw"


def _in_app_data_folder(app_name: str, path: str) -> bool:
    """Whether *path* lies inside *app_name*'s own data folder (``apps/<name>/data``).

    The folder is the app's only with its ``storage`` grant, which is what hands its backend the
    same directory (``apps/backend_runtime.py``), so an app without the grant has no folder here
    either. Both sides go through ``realpath``, so a symlink planted inside the folder cannot point
    a reveal back out of it. Read-only: the folder is resolved, never created.
    """
    from personalclaw.apps.manager import app_dir
    from personalclaw.apps.permissions import checker_for

    checker = checker_for(app_name)
    if checker is None or not checker.can_use_storage():
        return False
    try:
        root = os.path.realpath(str(app_dir(app_name) / "data"))
    except ValueError:
        return False
    real = os.path.realpath(os.path.expanduser(path))
    return real == root or real.startswith(root + os.sep)


async def api_reveal_path(request: web.Request) -> web.Response:
    """POST /api/reveal — reveal a file/folder in Finder or open with default app."""
    import subprocess  # noqa: F811
    import sys  # noqa: F811

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    path = body.get("path", "")
    action = body.get("action", "reveal")  # "reveal" or "open"
    if not path or ".." in Path(path).parts:
        return web.json_response({"error": "invalid path"}, status=400)
    if is_sensitive_path(path):
        refused = _app_path_refusal(path, tool="reveal_path", reach=_REVEAL_REACH)
        if refused is not None:
            return refused
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="reveal_path",
            outcome="denied",
            error="sensitive_path",
            resources=path,
            metadata={"action": action},
        )
        return web.json_response({"error": "access denied"}, status=403)
    # An APP reveals and opens only its own files. The dashboard roots are your workspace, uploads
    # and outbox, and "reveal" puts one of them on your screen while "open" hands it to your
    # default app for its type — a `.command` file runs in Terminal — so what an app may name is
    # the one folder that is the app's: its data folder, which it has with its `storage` grant.
    from personalclaw.apps.permissions import request_app

    app_name = request_app()
    if app_name and not _in_app_data_folder(app_name, path):
        # Returns on every path: the owner-only root check below is skipped for an app, so a
        # fall-through here would hand the path to `open`.
        message = _app_path_refusal_message(path, tool="reveal_path", reach=_REVEAL_REACH)
        return json_error("forbidden", message=message or _REVEAL_REACH, status=403)
    # 🔴 THE ROOT ALLOWLIST. This was the ONE files endpoint that skipped
    # `_validate_dashboard_path`, so `/etc/hosts` and another instance's home both answered 200
    # (#655) — and it gains nothing from the blocked-basename work, because that lives INSIDE the
    # function it was bypassing. The `..` and `is_sensitive_path` checks above are not a substitute:
    # a path needs neither to be outside every root the dashboard is meant to surface.
    #
    # This is the endpoint that hands a path to `open`/`xdg-open`, i.e. to the host's default
    # handler for whatever it is, which makes it the worst one to leave unrestricted.
    #
    # SECOND, not first: the sensitive-path check above is the more specific refusal (403 + a
    # `sensitive_path` audit reason) and it holds regardless of roots, so it keeps answering for the
    # case it already owned. This gate only decides paths that were previously ALLOWED.
    #
    # Non-breaking for the product: the only caller is the explorer's "Reveal in Finder" button,
    # which passes an `entry.path` the explorer itself enumerated — and the explorer cannot leave
    # the roots. (Yours: an app was held to its own data folder above.)
    if not app_name and _validate_dashboard_path(path) is None:
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="reveal_path",
            outcome="denied",
            error="outside_dashboard_roots",
            resources=path,
            metadata={"action": action},
        )
        # STRUCTURED (`json_error`), unlike this module's six flat siblings emitting the same
        # sentence. `test_wire_error_envelope_census` ratchets the flat population DOWN, so a
        # NEW refusal joins the shape the project converges on; converting the six is
        # its own change.
        return json_error("invalid_path", message="invalid or forbidden path", status=400)
    if action == "open":
        if not os.path.isfile(path):
            return web.json_response({"error": "not a regular file"}, status=400)
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", path])
        else:
            return web.json_response({"ok": True, "copy": path})
    else:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        elif shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(Path(path).parent)])
        else:
            return web.json_response({"ok": True, "copy": path})
    _sel().log_tool_invocation(
        session_key="api",
        source="api",
        tool_name="reveal_path",
        outcome="success",
        resources=path,
        metadata={"action": action},
    )
    return web.json_response({"ok": True})


async def api_outbox_notify(request: web.Request) -> web.Response:
    """POST /api/outbox/notify — agent sent a file, notify the user."""
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):

        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="notify",
            outcome="denied",
            error="invalid_json_body",
        )
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    from personalclaw.security import redact  # noqa: F811

    raw_path = body.get("path", "")
    raw_filename = body.get("filename", "")
    raw_desc = body.get("description", "")
    if (
        not isinstance(raw_path, str)
        or not isinstance(raw_filename, str)
        or not isinstance(raw_desc, str)
    ):
        return web.json_response({"error": "path/filename/description must be strings"}, status=400)
    # Reject files whose names/paths contain sensitive patterns
    if redact(raw_filename) != raw_filename or redact(raw_path) != raw_path:

        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="notify",
            outcome="denied",
            error="sensitive_filename_rejected",
        )
        return web.json_response(
            {"error": "filename or path contains sensitive content"}, status=400
        )
    import mimetypes  # noqa: PLC0415

    content_type = mimetypes.guess_type(raw_filename)[0] or "application/octet-stream"
    file_data = {
        "filename": raw_filename,
        "path": raw_path,
        "description": redact(raw_desc),
        "size": body.get("size", 0),
        "content_type": content_type,
    }
    # Validate file is readable (+ UTF-8 for text); media/binary types are
    # admitted without the UTF-8 check so audio/video/images can render inline.
    from pathlib import Path  # noqa: F811

    from personalclaw.config.loader import outbox_dir  # noqa: F811
    from personalclaw.hooks import FileTooLargeError, safe_read_file_bytes  # noqa: F811

    resolved = Path(file_data["path"]).resolve()
    if not resolved.is_relative_to(outbox_dir().resolve()):

        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="notify",
            outcome="denied",
            error="path_outside_outbox",
        )
        return web.json_response({"error": "path must be inside outbox"}, status=403)
    try:
        raw = safe_read_file_bytes(str(resolved))
    except FileTooLargeError as e:

        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="notify",
            outcome="denied",
            error=f"file_too_large: {e}",
        )
        return web.json_response({"error": str(e)}, status=413)
    if raw is None:

        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="notify",
            outcome="denied",
            error="file_not_found_or_access_denied",
        )
        return web.json_response({"error": "File not found or access denied"}, status=404)
    # Media/binary uploads (audio/video/image/pdf) skip the UTF-8 gate so they
    # render as inline players/images; everything else must be UTF-8 text. The
    # outbox-path containment (above) and size cap (FileTooLargeError) still apply.
    _is_media = (
        content_type.split("/", 1)[0] in ("audio", "video", "image")
        or content_type == "application/pdf"
    )
    if not _is_media:
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:

            _sel().log_tool_invocation(
                session_key="api",
                source="api",
                tool_name="notify_attachment",
                tool_kind="notify",
                outcome="denied",
                error="non_utf8_file",
            )
            return web.json_response(
                {"error": "Only UTF-8 text or media (audio/video/image/pdf) files are supported"},
                status=400,
            )
    # Inject into the most recently active chat session so the card persists
    if state._sessions:
        active = max(
            state._sessions.values(),
            key=lambda s: s.messages[-1]["ts"] if s.messages else "",
        )
        if active and active.messages:
            active.append("file", json.dumps(file_data))
    state.broadcast_ws("file_ready", file_data)

    _sel().log_tool_invocation(
        session_key="api",
        source="api",
        tool_name="notify_attachment",
        tool_kind="notify",
        outcome="completed",
        resources=f"filename={file_data['filename']}",
    )
    return web.json_response({"ok": True})


async def api_outbox_download(request: web.Request) -> web.StreamResponse:
    """GET /api/outbox/{filename} — download a file from the outbox."""
    import mimetypes  # noqa: PLC0415

    from personalclaw.config.loader import outbox_dir  # noqa: F811
    from personalclaw.hooks import FileTooLargeError, safe_read_file_bytes  # noqa: F811
    from personalclaw.security import redact  # noqa: F811

    filename = request.match_info["filename"]
    path = (outbox_dir() / filename).resolve()
    if not path.is_relative_to(outbox_dir().resolve()):
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="download",
            outcome="denied",
            error=f"path_traversal: {filename}",
        )
        return web.json_response({"error": "forbidden"}, status=403)
    # Media/binary (audio/video/image/pdf) → stream via FileResponse so the
    # browser gets the correct Content-Type + automatic Range support (needed
    # for <audio>/<video> seeking). Text files keep the redaction-gated path.
    content_type = mimetypes.guess_type(path.name)[0] or ""
    _is_media = (
        content_type.split("/", 1)[0] in ("audio", "video", "image")
        or content_type == "application/pdf"
    )
    if _is_media and path.is_file():
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="download",
            outcome="completed",
            resources=f"filename={filename} ct={content_type}",
        )
        return web.FileResponse(path, headers={"Content-Type": content_type})
    try:
        raw = safe_read_file_bytes(str(path))
    except FileTooLargeError as e:
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="download",
            outcome="denied",
            error=f"file_too_large: {e}",
        )
        return web.json_response({"error": str(e)}, status=413)
    if raw is None:
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="download",
            outcome="denied",
            error=f"safe_read_file_bytes rejected: {filename}",
        )
        return web.json_response({"error": "forbidden"}, status=403)
    # Scan content — reject binary, abort if redaction modifies content
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="download",
            outcome="denied",
            error="binary_file_rejected",
        )
        return web.json_response({"error": "Only UTF-8 text files are supported"}, status=400)
    redacted = redact(text)
    if redacted != text:
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="download",
            outcome="denied",
            error="content_redacted",
        )
        return web.json_response(
            {"error": "file content was redacted; download aborted"}, status=400
        )
    _sel().log_tool_invocation(
        session_key="api",
        source="api",
        tool_name="notify_attachment",
        tool_kind="download",
        outcome="completed",
        resources=f"filename={filename}",
    )
    return web.Response(
        body=redacted.encode("utf-8"),
        headers={"Content-Disposition": attachment_disposition(path.name)},
    )


async def api_outbox_list(request: web.Request) -> web.Response:
    """GET /api/outbox — list files in the outbox."""
    from personalclaw.config.loader import outbox_dir  # noqa: F811
    from personalclaw.security import redact  # noqa: F811

    entries = []
    odir = outbox_dir()
    if not odir.is_dir():
        return web.json_response({"files": []})
    for f in odir.iterdir():
        try:
            st = f.stat()
        except FileNotFoundError:
            continue
        if f.is_file() and redact(f.name) == f.name:
            entries.append({"filename": f.name, "size": st.st_size, "modified": st.st_mtime})
    entries.sort(key=lambda x: float(x["modified"]), reverse=True)  # type: ignore[arg-type,return-value]  # noqa: E501

    _sel().log_tool_invocation(
        session_key="api",
        source="api",
        tool_name="notify_attachment",
        tool_kind="list",
        outcome="completed",
        resources=f"count={len(entries)}",
    )
    return web.json_response({"files": entries[:50]})


async def api_channel_upload_file(request: web.Request) -> web.Response:
    """POST /api/channel/upload-file — upload a file to the active channel (internal, called by notify_attachment)."""  # noqa: E501
    from personalclaw.hooks import FileTooLargeError, safe_read_file_bytes  # noqa: F811
    from personalclaw.security import redact  # noqa: F811

    state: DashboardState = request.app["state"]
    delivery = state.channel_delivery
    if not delivery:
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="channel",
            outcome="skipped",
            error="no_channel_delivery",
        )
        return web.json_response({"ok": True, "skipped": "no_channel"})
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="channel",
            outcome="denied",
            error="invalid_json_body",
        )
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    file_path_raw = body.get("file_path", "")
    filename = body.get("filename", "")
    thread_ts = body.get("thread_ts")
    if not file_path_raw or not filename:
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="channel",
            outcome="denied",
            error="missing_required_fields",
        )
        return web.json_response({"error": "file_path, filename required"}, status=400)
    file_path = file_path_raw
    resolved = Path(file_path).resolve()
    from personalclaw.config.loader import outbox_dir, workspace_root  # noqa: F811

    allowed_outbox = outbox_dir().resolve()
    allowed_workspace = workspace_root().resolve()
    if not (resolved.is_relative_to(allowed_outbox) or resolved.is_relative_to(allowed_workspace)):
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="channel",
            outcome="denied",
            downstream_service="channel",
            error=f"path_not_allowed: {file_path}",
        )
        return web.json_response({"error": "file_path must be under ~/.personalclaw/"}, status=403)
    try:
        raw = safe_read_file_bytes(str(resolved))
    except FileTooLargeError as e:
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="channel",
            outcome="denied",
            downstream_service="channel",
            error=f"file_too_large: {e}",
        )
        return web.json_response({"error": str(e)}, status=413)
    if raw is None:
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="channel",
            outcome="denied",
            downstream_service="channel",
            error=f"safe_read_file_bytes rejected: {file_path}",
        )
        return web.json_response(
            {"error": f"File not found or access denied: {file_path}"}, status=404
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="channel",
            outcome="error",
            downstream_service="channel",
            error="binary_file_rejected",
        )
        return web.json_response({"error": "Only UTF-8 text files are supported"}, status=400)
    try:
        redacted = redact(text)
        if redacted != text:
            _sel().log_tool_invocation(
                session_key="api",
                source="api",
                tool_name="notify_attachment",
                tool_kind="channel",
                outcome="denied",
                downstream_service="channel",
                error="content_redacted",
            )
            return web.json_response(
                {"error": "file content was redacted; upload aborted"}, status=400
            )
        pass  # content validated, proceed to upload
    except Exception as redact_err:
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="channel",
            outcome="error",
            downstream_service="channel",
            error=f"redaction_failed: {redact_err}",
        )
        # Raw text stays in the SEL record above; the wire speaks guidance (failure_copy).
        return web.json_response({"error": relayed_failure_copy(redact_err)}, status=500)
    safe_filename = filename
    if redact(safe_filename) != safe_filename:
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="channel",
            outcome="denied",
            downstream_service="channel",
            error="sensitive_filename_rejected",
        )
        return web.json_response({"error": "filename contains sensitive content"}, status=400)
    # The owner's DM on the first channel that reaches them — the upload through the same handle
    # that opened it — and the Inbox, saying why, when none does.
    from personalclaw.channel_delivery import deliver_to_owner

    try:
        outcome = await deliver_to_owner(
            lambda delivery, dm: delivery.upload_attachment(
                dm,
                str(resolved),
                filename=safe_filename,
                thread_ts=thread_ts or "",
                title=safe_filename,
            ),
            title=f"File: {safe_filename}",
            text=str(resolved),
            state=state,
        )
        if not outcome.delivered:
            _sel().log_tool_invocation(
                session_key="api",
                source="api",
                tool_name="notify_attachment",
                tool_kind="channel",
                outcome="skipped",
                error="no_channel" if outcome.no_channel else "no_channel_reached_the_owner",
            )
            if outcome.no_channel:
                return web.json_response({"ok": True, "skipped": "no_channel"})
            # 200 with the sentence, not an error status: the MCP tool reads this body, and a 5xx
            # reaches it only as "HTTP Error 502". The file is in the outbox and the Inbox says so.
            return web.json_response(
                {"ok": False, "error": outcome.sentence(), "inbox": outcome.inboxed}
            )
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="channel",
            outcome="completed",
            downstream_service="channel",
            resources=f"channel={outcome.provider}:{outcome.channel} file={file_path}",
        )
        return web.json_response({"ok": True})
    except Exception as e:
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="channel",
            outcome="error",
            downstream_service="channel",
            error=str(e),
        )
        # Raw text stays in the SEL record above; the wire speaks guidance (failure_copy).
        return web.json_response({"error": relayed_failure_copy(e)}, status=500)


async def api_upload(request: web.Request) -> web.Response:
    """POST /api/upload — open native file picker and return selected paths."""
    if sys.platform != "darwin":
        return web.json_response({"error": "File picker is only available on macOS"}, status=400)

    proc = await asyncio.create_subprocess_exec(
        "osascript",
        "-e",
        "set f to choose file with multiple selections allowed\n"
        'set out to ""\n'
        "repeat with p in f\n"
        "  set out to out & POSIX path of p & linefeed\n"
        "end repeat\n"
        "return out",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.communicate()
        return web.json_response({"error": "Finder dialog timed out"}, status=504)
    paths = [ln for ln in stdout.decode("utf-8", errors="replace").strip().splitlines() if ln]

    if not paths:
        return web.json_response({"paths": []})
    return web.json_response({"paths": paths})


def _screenshot_dir() -> Path:
    """Where a captured screenshot lands, resolved AT CALL TIME (CRE-8).

    This was a module-level ``_SCREENSHOT_DIR`` bound at import, and that is the whole
    defect: a constant is already a value, so no test fixture can redirect it and a
    ``$PERSONALCLAW_HOME`` set after first import is ignored for the life of the process.
    MEASURED in CI, which is where the difference shows: with no ``~/.personalclaw`` to
    begin with, the capture handler created the developer's real home just by resolving
    this path — the suite's real-home rail caught it as `dir-entries-changed screenshots`.
    """
    return _path_home_pclaw() / "screenshots"


def _upload_dir() -> Path:
    """Where an upload lands, resolved AT CALL TIME (CRE-8).

    Same shape as :func:`_screenshot_dir` and fixed with it rather than after it: the two
    constants sat on adjacent lines, so leaving one frozen would have left the same bug
    waiting for whichever test writes an upload first.
    """
    return _path_home_pclaw() / "uploads"


_MAX_UPLOAD_FILES = 20  # max files per request
# Per-file size is gated by the shared filetype policy (personalclaw.uploads),
# not an extension allowlist — see _upload_check.


def _write_file_restricted(path: Path, data: bytes) -> None:
    """Write file with owner-only permissions (0o600)."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def _upload_check(filename: str, mime: str | None = None, *, size: int | None = None):
    """Category + per-filetype size gate via the shared upload policy.

    Returns an :class:`personalclaw.uploads.UploadCheck` — ``ok`` accepts, else
    ``reason``/``status`` drive the rejection. All recognized types (and unknown
    ones, capped as ``other``) are accepted up to their category limit; the size
    gate — not an extension allowlist — is the policy."""
    from personalclaw.uploads import check_upload

    return check_upload(filename, mime, size=size)


async def api_upload_file(request: web.Request) -> web.Response:
    """POST /api/upload/file — cross-platform multipart file upload.

    Accepts multipart form data with one or more 'file' fields.
    Saves files to ~/.personalclaw/uploads/ and returns server-side paths
    that ACP's _send_prompt() can detect for image inlining.
    """

    _upload_dir().mkdir(parents=True, exist_ok=True)
    ctype = request.headers.get("Content-Type", "")
    if not ctype.lower().startswith("multipart/"):
        return web.json_response(
            {"error": "multipart/form-data with one or more 'file' parts is required"},
            status=400,
        )
    try:
        reader = await request.multipart()
    except (ValueError, AssertionError, RuntimeError) as exc:
        return web.json_response(
            {"error": f"failed to parse multipart body: {exc}"},
            status=400,
        )
    paths: list[str] = []
    caller = request.get("user", "dashboard")

    def _cleanup() -> None:
        for p in paths:
            Path(p).unlink(missing_ok=True)

    try:
        while True:
            part = await reader.next()
            if part is None:
                break
            if not isinstance(part, BodyPartReader):
                continue
            if part.name != "file":
                continue
            if len(paths) >= _MAX_UPLOAD_FILES:
                _cleanup()
                _sel().log_api_access(
                    caller=caller,
                    operation="upload.file",
                    outcome="rejected",
                    source="dashboard",
                    resources=f"reason:too_many_files:{_MAX_UPLOAD_FILES}",
                )
                return web.json_response(
                    {"error": f"Too many files (max {_MAX_UPLOAD_FILES})"},
                    status=400,
                )
            fname = part.filename or "upload"
            # Sanitize: strip path components to prevent traversal
            safe_name = re.sub(r"[^\w.\-]", "_", Path(fname).name)
            # Per-filetype byte cap from the one shared upload policy. The browser's
            # declared content-type disambiguates .webm/.ogg (audio vs video) for the
            # right category cap. Unknown types are accepted (capped as "other"); the
            # size gate — not an extension allowlist — is the policy.
            part_mime = part.headers.get("Content-Type") if part.headers else None
            _limit = _upload_check(safe_name, part_mime).limit
            # Stream to a tempfile, enforcing the category cap as bytes arrive — never
            # buffer the whole file in memory (a 2 GB video would OOM otherwise).
            dest = _upload_dir() / f"{uuid.uuid4().hex}_{safe_name}"
            if not dest.resolve().is_relative_to(_upload_dir().resolve()):
                _cleanup()
                _sel().log_api_access(
                    caller=caller,
                    operation="upload.file",
                    outcome="rejected",
                    source="dashboard",
                    resources=f"file:{fname} reason:path_traversal",
                )
                return web.json_response({"error": "Invalid filename"}, status=400)
            size = 0
            over = False
            fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "wb") as fh:
                    while True:
                        chunk = await part.read_chunk(65536)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > _limit:
                            over = True
                            break
                        fh.write(chunk)
            except Exception:
                dest.unlink(missing_ok=True)
                raise
            if over:
                dest.unlink(missing_ok=True)
                _cleanup()
                _sel().log_api_access(
                    caller=caller,
                    operation="upload.file",
                    outcome="rejected",
                    source="dashboard",
                    resources=f"file:{fname} reason:too_large:{size}",
                )
                # Recompute with the over-limit size so the per-filetype message
                # ("video file too large (max 2 GB)") is populated.
                reason = _upload_check(safe_name, part_mime, size=size).reason
                return web.json_response({"error": reason}, status=413)
            paths.append(str(dest))
    except Exception:
        _cleanup()
        _sel().log_api_access(
            caller=caller,
            operation="upload.file",
            outcome="error",
            source="dashboard",
            resources=f"files_written:{len(paths)}",
        )
        raise
    if not paths:
        _sel().log_api_access(
            caller=caller,
            operation="upload.file",
            outcome="rejected",
            source="dashboard",
            resources="reason:no_files",
        )
        return web.json_response({"error": "No files uploaded"}, status=400)
    _sel().log_api_access(
        caller=caller,
        operation="upload.file",
        outcome="success",
        source="dashboard",
        resources=f"files:{len(paths)}",
    )
    # Kick off content extraction NOW (while the user is still typing the query),
    # so an attachment's text is ready — or nearly so — by the time the turn runs.
    # The chat runner awaits these per-file before answering (knowledge extraction
    # graph only: text read / ASR / OCR / ffmpeg — no enrichment). See
    # dashboard.attachment_extract + knowledge.extract.
    try:
        from personalclaw.dashboard.attachment_extract import get_extractor

        extractor = get_extractor()
        for p in paths:
            import mimetypes as _mt

            extractor.start(p, _mt.guess_type(p)[0])
    except Exception:
        logger.debug("attachment extraction kickoff failed", exc_info=True)
    return web.json_response({"paths": paths})


async def api_attachment_extract(request: web.Request) -> web.Response:
    """GET /api/attachment-extract?path=... — the extracted text content for an
    uploaded attachment, so the chat UI can preview what the agent saw. Awaits
    the extraction kicked off at upload (or runs it now). Restricted to the
    uploads dir to prevent reading arbitrary files through this surface."""
    import mimetypes as _mt

    caller = request.get("user", "dashboard")
    raw = request.query.get("path", "").strip()
    if not raw:
        return web.json_response({"error": "path is required"}, status=400)
    path = os.path.realpath(os.path.expanduser(raw))
    uploads = str(_upload_dir().resolve())
    if not path.startswith(uploads + os.sep):
        _sel().log_api_access(
            caller=caller,
            operation="attachment_extract",
            outcome="denied",
            resources=path,
            error="outside uploads",
        )
        return web.json_response({"error": "Access denied"}, status=403)
    if not os.path.isfile(path):
        return web.json_response({"error": "Not found"}, status=404)
    from personalclaw.dashboard.attachment_extract import display_name, get_extractor

    text = await get_extractor().get(path, _mt.guess_type(path)[0])
    _sel().log_api_access(
        caller=caller,
        operation="attachment_extract",
        outcome="allowed",
        resources=f"name={display_name(path)} chars={len(text)}",
    )
    return web.json_response({"name": display_name(path), "text": text})


async def api_screenshot(request: web.Request) -> web.Response:
    """POST /api/screenshot — capture screen region and return file path.

    macOS only — uses the built-in `screencapture` binary. Headless
    hosts (e.g. servers, containers) lack a display server, so this
    endpoint is unavailable on non-macOS platforms.
    """
    if sys.platform != "darwin":
        return web.json_response({"error": "Screenshot is only available on macOS"}, status=400)

    _screenshot_dir().mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    dest = _screenshot_dir() / f"screenshot_{ts}.png"

    proc = await asyncio.create_subprocess_exec(
        "screencapture",
        "-i",
        str(dest),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=120)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return web.json_response({"error": "screenshot timed out"}, status=504)
    if not dest.exists():
        return web.json_response({"path": ""})  # user cancelled
    # Same head start an upload gets: begin extracting (OCR for a screenshot) while the
    # user is still typing, so the turn does not wait on it. The runner awaits it per
    # file before answering either way.
    try:
        from personalclaw.dashboard.attachment_extract import get_extractor

        get_extractor().start(str(dest), "image/png")
    except Exception:
        logger.debug("screenshot extraction kickoff failed", exc_info=True)
    return web.json_response({"path": str(dest)})


def _dashboard_roots() -> list[tuple[str, str]]:
    """Return the labeled root directories the dashboard is allowed to surface.

    Each entry is ``(label, realpath)``. These are the boundaries the file
    explorer browses and the allowlist :func:`_validate_dashboard_path`
    enforces — workspace, outbox, uploads, and PERSONALCLAW_HOME. Roots that
    fail to resolve (e.g. not configured) are skipped. The order is
    user-facing-first (workspace) so the explorer can default to it.

    🔴 AN APP NEVER GETS THE HOME ITSELF. ``config.json``, ``mcp.json``, the automations, the
    agent files and every other app's install are plain files under PERSONALCLAW_HOME, so an
    app that declared ``/api/file-write`` could turn YOLO on or define an MCP command by
    editing one — past every refusal the config PATCH, the MCP routes and the automation routes
    make — and one that declared ``/api/file-read`` could read the MCP servers' credentials.
    So for a request the gateway scoped to an app (``permissions.request_app``), every root
    that IS the home or CONTAINS it is left out — the two home roots, and a loop or project
    workspace bound to ``~`` — while roots inside it (outbox, uploads) stay. The allowlist then
    admits a home path only through one of those, and the realpath checks in
    :func:`_validate_dashboard_path` refuse a symlink or ``..`` back out of them.
    """
    roots = _all_dashboard_roots()
    from personalclaw.apps.permissions import request_app

    if not request_app():
        return roots
    from personalclaw.config.loader import config_dir

    home = os.path.realpath(str(config_dir()))
    return [(label, r) for label, r in roots if not (home == r or home.startswith(r + os.sep))]


def _all_dashboard_roots() -> list[tuple[str, str]]:
    """Every root :func:`_dashboard_roots` may surface, before the app-scoped filter."""
    from personalclaw.config.loader import config_dir, outbox_dir

    candidates: list[tuple[str, str]] = []

    def _add(label: str, path_factory) -> None:
        try:
            candidates.append((label, os.path.realpath(str(path_factory()))))
        except Exception:
            pass

    # Default workspace root used by chat sessions and ACP agents — the
    # primary place users create/consume files, so list it first.
    try:
        from personalclaw.config.loader import workspace_root

        _add("Workspace", workspace_root)
    except Exception:
        pass
    _add("Home", config_dir)
    _add("Outbox", outbox_dir)
    # Uploads + PersonalClaw home roots must follow the ACTIVE home (config_dir()),
    # NOT a hardcoded ~/.personalclaw: a gateway on a custom PERSONALCLAW_HOME (every
    # dev instance) would otherwise browse AND edit the developer's REAL home via the
    # write allowlist (#294). config_dir() re-reads PERSONALCLAW_HOME live, and both
    # stay lambda/callable-deferred so the home is resolved per request, not at import.
    _add("Uploads", lambda: os.path.join(config_dir(), "uploads"))
    # PersonalClaw home root — quick access to the whole config/data tree.
    _add("PersonalClaw", config_dir)

    # Loop workspaces — a Loop (typically a code kind, but any kind may) can bind an
    # arbitrary (brownfield) directory anywhere on disk; its cockpit (file tree +
    # editor) must be allowed to browse + edit it. Surface each existing loop's
    # workspace_dir as a root so the allowlist admits it. Best-effort: never let a
    # loop-store hiccup break the file explorer for the normal roots.
    # A user-bound workspace (loop OR project) may point anywhere — but a bound
    # workspace that IS (or sits under) a protected system root must NEVER become a
    # browsable root, or /etc, /usr, / etc. would leak via a workspace binding. The
    # allowlist check in _validate_dashboard_path does not re-apply the system-root
    # guard, so we enforce it HERE at root derivation (the single admission point).
    def _add_workspace_root(label: str, wsd: str) -> None:
        real = os.path.realpath(os.path.expanduser(wsd))
        if _is_system_root(real):
            logger.warning("dashboard: refusing system-root workspace %r as a browsable root", real)
            return
        candidates.append((label, real))

    try:
        from personalclaw.loop import store as _loop_store

        for _lp in _loop_store.list_all():
            wsd = (_lp.workspace_dir or "").strip()
            if wsd:
                _add_workspace_root(f"Loop: {_lp.name[:24]}", wsd)
    except Exception:
        pass

    # Project workspaces — a Project (projects-native-entity) is a first-class work
    # unit that MAY bind an arbitrary codebase dir on disk. Its detail view surfaces
    # that workspace as a "view contents" peek + "Open in Files", so the allowlist
    # must admit each bound Project.workspace_dir (exactly like a Loop's, above) —
    # otherwise a project workspace not coincidentally shared by a Loop 400s. Best-
    # effort: a project-store hiccup must never break the explorer for normal roots.
    try:
        from personalclaw.projects import _store as _project_store

        for _pj in _project_store().list_projects():
            wsd = (_pj.workspace_dir or "").strip()
            if wsd:
                _add_workspace_root(f"Project: {_pj.name[:24]}", wsd)
    except Exception:
        pass

    # De-dupe by realpath while preserving order + first label.
    seen: set[str] = set()
    roots: list[tuple[str, str]] = []
    for label, rp in candidates:
        if rp and rp not in seen:
            seen.add(rp)
            roots.append((label, rp))
    return roots


def _is_dashboard_root(path: str) -> bool:
    """True if ``path`` IS one of the dashboard's browsable root directories.

    A root (workspace, home, outbox, uploads, PERSONALCLAW_HOME, a bound
    loop/project workspace) is a boundary the explorer surfaces — never a valid
    delete or move TARGET. ``_validate_dashboard_path`` treats a root as inside
    the allowlist (a root is inside itself: ``canonical == root``), so without
    this guard ``POST /api/file-delete`` on a root would ``shutil.rmtree`` the
    whole root and ``/api/file-move`` would relocate it (issue #780). Both
    ``path`` (via ``_validate_dashboard_path`` → ``validate_file_path``) and the
    roots are ``os.path.realpath``-canonicalized, so a plain equality holds.
    """

    return any(path == rp for _label, rp in _dashboard_roots())


#: The per-component byte limit essentially every filesystem enforces (ext4, APFS, NTFS).
#: BYTES, not characters: an emoji costs four, so a 90-character name can exceed it while
#: looking short. `len(name)` would have passed exactly the inputs the OS refuses.
MAX_NAME_BYTES = 255


def _path_rejection(exc: "ValidationError") -> str:
    """Why a file-I/O `path` was refused, in words that name a remedy (#296).

    Every one of these endpoints answered a flat ``invalid input``: no field, no rule, no
    fix. That was survivable while the rule was arbitrary (a charset allowlist the create
    side did not share); now that the two ends agree, the remaining rejections are all
    actionable, so say which one fired.
    """
    if exc.field != "path":
        return f"{exc.field}: {exc.message}"
    if exc.message == "invalid format":
        return (
            "path must start with '/' or '~' and contain no control characters "
            "(pass resolve=1 to resolve a relative path)"
        )
    return f"path: {exc.message}"


#: C0 controls + DEL — the ONLY characters this area refuses in a name or a path (#296).
#: Kept as an explicit frozenset rather than a regex because `_reject_name` reports WHICH
#: rule refused a name, and the identical rule is expressed on the read/write side as
#: `validation._FILE_PATH_RE`'s `[^\x00-\x1f\x7f]`. The two must stay the same set; the
#: whole point of #296 was that the two ends of this namespace disagreed about characters.
_CONTROL_CHARS = frozenset(chr(c) for c in list(range(0x20)) + [0x7F])


def _validate_dashboard_path(raw: str, allowed_roots: tuple[str, ...] | None = None) -> str | None:
    """Validate a file path for dashboard file I/O.

    Two-layer check:
      1. Reject sensitive credential paths via ``personalclaw.hooks.validate_file_path``
         (e.g. ``~/.ssh``, ``~/.aws``).
      2. Restrict to an allowlist of root directories the dashboard is meant
         to surface — workspaces, outbox, uploads, and PERSONALCLAW_HOME. This
         constrains the path-traversal surface so a request like
         ``GET /api/file-read?path=/etc/passwd`` is rejected.

    Returns the canonical path or ``None`` if rejected.
    """

    from personalclaw.hooks import validate_file_path  # noqa: F811

    canonical = validate_file_path(raw)
    if canonical is None:
        return None

    roots = (
        allowed_roots
        if allowed_roots is not None
        else tuple(rp for _label, rp in _dashboard_roots())
    )

    inside_allowlist = False
    for root in roots:
        if not root:
            continue
        if canonical == root or canonical.startswith(root + os.sep):
            inside_allowlist = True
            break
    if not inside_allowlist:
        return None

    # Even within allowed roots, block known-sensitive filenames (e.g. HMAC
    # keys, telemetry salt, app secrets) to prevent credential disclosure
    # via /api/file-read. Extensionless names must be listed here explicitly —
    # ``blocked_suffixes`` below cannot reach them.
    #
    # Two layers, because the host filesystem is often case-INSENSITIVE
    # (macOS/APFS, Windows/NTFS) while these comparisons used to be
    # case-SENSITIVE — so ``<home>/.LOCAL_SECRET`` sailed past the blocklist yet
    # resolved to the real ``.local_secret`` bytes (issue #690).
    #   Layer 1 — spelling: ``casefold()`` both sides so EVERY case variant of a
    #     blocked basename or suffix is refused, on any filesystem.
    #   Layer 2 — identity: on a case-insensitive (or hard-link-capable) volume
    #     the robust defence is file identity, not spelling. If the target
    #     resolves to the same inode as a blocked-name file in the same
    #     directory, refuse it whatever name reached it — this closes hard-link
    #     and short-name (8.3) aliases layer 1 cannot see. Bounded to the fixed
    #     blocked basenames (a handful of ``stat()``s), so it stays cheap even
    #     when file-list calls this per directory entry, and it is SKIPPED for a
    #     not-yet-existing target so create/write/move/upload of a fresh file is
    #     never rejected.
    # `OWN_SECRET_BASENAMES` is the SHARED definition (security.py), so this area and the
    # bash/terminal guards cannot disagree about what is secret. They did: every name here
    # was refused by `/api/file-read` and unknown to `is_sensitive_path`, which the terminal
    # cwd guard and the bash read hook both consult (#643).
    #
    # 🔴 DERIVED, NOT RE-LISTED (#354). Both halves come from `security.py`:
    #   `OWN_SECRET_BASENAMES`       — ours by NAME, wherever the file sits.
    #   `HOME_SECRET_FILE_BASENAMES` — ours by LOCATION (`.env`, `session_key`,
    #                                  `sessions.json`), applied here as a name rule because
    #                                  this tier is already scoped to the browsable roots.
    # Those three used to be spelled out here as literals, and that re-listing IS the mechanism
    # of the bug: `session_key` was documented as the session SIGNING KEY in `session_store.py`
    # and named a secret in `security.py`, and this list still did not have it — a hand-copied
    # list only ever knows what someone remembered to copy. The set is unchanged today; what
    # changes is that the NEXT name added to the one declaration is refused here without anyone
    # having to notice. `test_secret_file_blocklist_rail.py` asserts exactly that by adding a
    # synthetic name to the declaration and requiring this function to refuse it.
    #
    # The secret DIRECTORIES (`auth/`, `credentials/`, `governance/`) are deliberately NOT
    # folded in: a subtree is not a basename, and they are already refused one layer up by
    # `validate_file_path` → `is_sensitive_path`, which resolves them against the ACTIVE
    # `PERSONALCLAW_HOME`. Naming them here too would refuse a user's own `credentials/`
    # folder inside a workspace, which is an ordinary directory name.
    from personalclaw.security import HOME_SECRET_FILE_BASENAMES, OWN_SECRET_BASENAMES

    blocked_basenames = set(OWN_SECRET_BASENAMES) | set(HOME_SECRET_FILE_BASENAMES)
    # An over-long final component reaches the OS as `ENAMETOOLONG` and surfaced as a 500 from
    # `file-move` (over-long dest) and `create-dir`, which take a whole PATH rather than a name
    # and so never met the name rules (#652). Bounded here, at the one place every path-taking
    # endpoint already funnels through, rather than in each handler.
    if len(os.path.basename(canonical).encode("utf-8")) > MAX_NAME_BYTES:
        return None
    blocked_suffixes = (".key", ".pem", ".secret")
    base_cf = os.path.basename(canonical).casefold()
    if base_cf in {name.casefold() for name in blocked_basenames}:
        return None
    if any(base_cf.endswith(suffix.casefold()) for suffix in blocked_suffixes):
        return None

    # Layer 2 — identity. A missing target (create/write/move/upload validate
    # paths that need not exist yet) has no inode to compare, so fall through to
    # the layer-1 result rather than rejecting a legitimate new file.
    try:
        target_st = os.stat(canonical)
    except OSError:
        return canonical
    parent = os.path.dirname(canonical)
    for blocked in blocked_basenames:
        try:
            cand_st = os.stat(os.path.join(parent, blocked))
        except OSError:
            continue
        if cand_st.st_ino == target_st.st_ino and cand_st.st_dev == target_st.st_dev:
            return None
    return canonical


def _resolve_relative_path(raw_path: str) -> str:
    """Resolve a relative path against the candidate base dirs.

    A relative path from a chat file-mention or tool output can be relative to
    the WORKSPACE (where chat sessions + the native agent operate) or the
    PROJECT_DIR (the source tree). Try each base, picking the one that yields an
    existing file inside it; fall back to the first base-confined candidate.
    Traversal-safe (a path escaping a base is skipped). Absolute / ``~`` paths
    and the no-base case are returned unchanged so the caller's normal
    validation still applies. Shared by ``file-read`` and ``file-watch`` so the
    two endpoints resolve identically (one source of truth).
    """

    if not raw_path or raw_path.startswith(("/", "~")):
        return raw_path

    bases: list[str] = []
    try:
        from personalclaw.config.loader import workspace_root

        bases.append(str(workspace_root()))
    except Exception:
        pass
    proj = os.environ.get("PERSONALCLAW_PROJECT_DIR", "")
    if proj:
        bases.append(proj)
    if not bases:
        return raw_path

    first_inside: str | None = None
    for base in bases:
        candidate = os.path.realpath(os.path.join(base, raw_path))
        base_real = os.path.realpath(base)
        if not (candidate == base_real or candidate.startswith(base_real + os.sep)):
            continue  # path escapes this base — skip (traversal-safe)
        if first_inside is None:
            first_inside = candidate
        if os.path.isfile(candidate):
            return candidate
    return first_inside or raw_path


async def api_file_watch(request: web.Request) -> web.StreamResponse:
    """GET /api/file-watch?path=... — SSE stream of file content changes.

    Accepts ``resolve=1`` for relative paths (same contract as ``file-read``) so
    a chat file-mention that opens the side panel can also live-watch the file.
    Without identical resolution the panel would fetch content fine but the
    watch would 400 and clobber it.
    """

    raw_path = request.query.get("path", "")
    if request.query.get("resolve") == "1":
        raw_path = _resolve_relative_path(raw_path)
    try:
        validate_tool_args({"path": raw_path}, FILE_READ_SCHEMA)
    except ValidationError as exc:
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_watch", outcome="denied", resources=raw_path
        )
        return web.json_response({"error": _path_rejection(exc)}, status=400)

    path = _validate_dashboard_path(raw_path)
    if not path:
        refused = _app_path_refusal(raw_path, tool="file_watch")
        if refused is not None:
            return refused
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_watch", outcome="denied", resources=raw_path
        )
        return web.json_response({"error": "invalid or forbidden path"}, status=400)

    if os.path.isdir(path):
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_watch", outcome="denied", resources=path
        )
        return web.json_response({"error": "path is a directory"}, status=400)

    _sel().log_tool_invocation(
        session_key="dashboard", tool_name="file_watch", outcome="success", resources=path
    )

    resp = web.StreamResponse()
    resp.content_type = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    await resp.prepare(request)

    poll_interval = 1.0
    read_cap = 512_000
    last_mtime: float = 0.0
    last_content = ""
    resolved_at_start = await asyncio.to_thread(os.path.realpath, path)

    def _read_file(p: str, cap: int) -> str:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f.read(cap)

    try:
        while not (request.transport is None or request.transport.is_closing()):
            try:
                stat = await asyncio.to_thread(os.stat, path)
                mtime = stat.st_mtime
            except FileNotFoundError:
                await asyncio.sleep(poll_interval)
                continue

            if mtime != last_mtime:
                last_mtime = mtime
                current_resolved = await asyncio.to_thread(os.path.realpath, path)
                if current_resolved != resolved_at_start:
                    logger.warning(
                        "file-watch: symlink changed after validation: %s -> %s",
                        resolved_at_start,
                        current_resolved,
                    )
                    _sel().log_tool_invocation(
                        session_key="dashboard",
                        tool_name="file_watch",
                        outcome="denied",
                        resources=path,
                    )
                    break
                try:
                    content = await asyncio.to_thread(_read_file, current_resolved, read_cap)
                    content, _ = redact_exfiltration_urls(content)
                    content, _ = redact_credentials(content)
                except Exception:
                    logger.warning("file-watch read error for %s", path, exc_info=True)
                    await asyncio.sleep(poll_interval)
                    continue

                if content != last_content:
                    last_content = content
                    payload = json.dumps({"content": content, "mtime": mtime})
                    await resp.write(f"data: {payload}\n\n".encode())

            await asyncio.sleep(poll_interval)
    except (ConnectionResetError, asyncio.CancelledError, ClientConnectionResetError):
        pass

    return resp


async def api_config_fs_watch(request: web.Request) -> web.StreamResponse:
    """GET /api/config-fs/stream — SSE feed of out-of-band config-tree changes (#44).

    The config trees (config.json, agents/, skills/, workflows/) are filesystem-as-truth:
    edited on disk, by another tool, or by an agent. This per-resource feed (key
    ``fs:config``) emits a ``changed`` event per file so the UI live-refreshes instead
    of showing a stale view. Starting the stream lazily starts the poll watcher."""
    from personalclaw.dashboard.sse import stream_response
    from personalclaw.fs_watch import FS_WATCH_FEED

    state = request.app["state"]
    state.config_fs_watcher()  # lazy-start the poll watcher (idempotent)
    registry = state.config_fs_sse()
    _sel().log_tool_invocation(
        session_key="dashboard",
        tool_name="config_fs_watch",
        outcome="success",
        resources=FS_WATCH_FEED,
    )
    return await stream_response(request, registry.hub(FS_WATCH_FEED))


async def api_file_read(request: web.Request) -> web.Response:
    """GET /api/file-read?path=... — read file content for the markdown panel."""
    import logging  # noqa: F811

    from personalclaw.validation import (  # noqa: F811
        FILE_READ_SCHEMA,
        ValidationError,
        validate_tool_args,
    )

    raw_path = request.query.get("path", "")
    # Resolve relative paths (resolve=1) against the workspace / project dir —
    # shared with file-watch so both endpoints resolve a chat file-mention
    # identically (see _resolve_relative_path).
    if request.query.get("resolve") == "1":
        raw_path = _resolve_relative_path(raw_path)

    try:
        validate_tool_args({"path": raw_path}, FILE_READ_SCHEMA)
    except ValidationError as exc:
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="file_read",
            outcome="denied",
            resources=raw_path,
        )
        return web.json_response({"error": _path_rejection(exc)}, status=400)

    path = _validate_dashboard_path(raw_path)
    if not path:
        refused = _app_path_refusal(raw_path, tool="file_read")
        if refused is not None:
            return refused
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="file_read",
            outcome="denied",
            resources=raw_path,
        )
        return web.json_response({"error": "invalid or forbidden path"}, status=400)
    if not os.path.isfile(path):
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_read", outcome="not_found", resources=path
        )
        return web.json_response({"error": "not found"}, status=404)
    if request.method == "HEAD":
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_read", outcome="success", resources=path
        )
        return web.Response(status=200)
    try:
        read_cap = 512_000
        # Read RAW bytes first so a binary file (a .pyc/.so/.db/image-with-odd-ext) is
        # DETECTED, not decoded into mojibake (utf-8 errors='replace' turns NUL/binary
        # into a wall of  that renders as garbage in the editor). A NUL byte in the
        # head is git's own binary heuristic; signal it so the FE shows a clean
        # "binary file" placeholder instead of trying to display + edit it.
        with open(path, "rb") as f:
            raw = f.read(read_cap + 1)
        truncated = len(raw) > read_cap
        raw = raw[:read_cap]
        if b"\x00" in raw[:8192]:
            _sel().log_tool_invocation(
                session_key="dashboard", tool_name="file_read", outcome="success", resources=path
            )
            return web.Response(text="", content_type="text/plain", headers={"X-Binary": "true"})
        content = raw.decode("utf-8", errors="replace")
        content, _ = redact_exfiltration_urls(content)
        content, _ = redact_credentials(content)
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_read", outcome="success", resources=path
        )
        headers = {"X-Truncated": "true"} if truncated else {}
        return web.Response(text=content, content_type="text/plain", headers=headers)
    except Exception:
        logging.getLogger(__name__).exception("file_read failed for %s", path)
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_read", outcome="failure", resources=path
        )
        return web.json_response({"error": "failed to read file"}, status=500)


async def api_file_raw(request: web.Request) -> web.Response:
    """GET /api/file-raw?path=... — serve a file with its native content type (images, etc.)."""

    import personalclaw.dashboard.handlers as _h  # noqa: F811

    def _log(outcome: str, res: str) -> None:
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="file_raw",
            outcome=outcome,
            resources=res,
        )

    raw_path = request.query.get("path", "")
    # Resolve relative paths (resolve=1) against the workspace / project dir —
    # identical contract to file-read/file-watch so a chat file-mention that opens
    # the side panel serves its RAW bytes (image/pdf/video/binary/download) from the
    # same resolved path the text-read probe used. Without this, a relative-path
    # MEDIA file loaded fine as text but 400'd on the raw fetch (the "some files
    # fail" panel bug): text files worked, binaries didn't.
    if request.query.get("resolve") == "1":
        raw_path = _resolve_relative_path(raw_path)
    path = _h._validate_dashboard_path(raw_path)
    if not path:
        refused = _app_path_refusal(raw_path, tool="file_raw")
        if refused is not None:
            return refused
        _log("denied", raw_path)
        return web.json_response({"error": "invalid or forbidden path"}, status=400)
    from personalclaw.security import is_sensitive_path as _isp  # noqa: F811

    if _isp(path):
        _log("denied", path)
        return web.json_response({"error": "sensitive path blocked"}, status=403)
    if not os.path.isfile(path):
        _log("not_found", path)
        return web.json_response({"error": "not found"}, status=404)
    # Open with O_NOFOLLOW to atomically reject symlinks (no TOCTOU race).
    # Read header + full content through the same fd to avoid re-opening.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            if st.st_size > _MAX_INLINE_READ_BYTES:
                _log("denied", path)
                return web.json_response({"error": "file too large"}, status=413)
            header = f.read(12)
            f.seek(0)
            data = f.read()
    except OSError as exc:
        if exc.errno == errno.ELOOP:  # symlink with O_NOFOLLOW
            _log("denied", path)
            return web.json_response({"error": "symlinks not allowed"}, status=403)
        _log("failure", path)
        return web.json_response({"error": "cannot read file"}, status=500)
    _image_magic = (
        (b"\x89PNG", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"BM", "image/bmp"),
        (b"II\x2a\x00", "image/tiff"),
        (b"MM\x00\x2a", "image/tiff"),
        (b"\x00\x00\x01\x00", "image/x-icon"),
    )
    content_type = None
    # WebP: RIFF....WEBP compound signature
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        content_type = "image/webp"
    else:
        for magic, mime in _image_magic:
            if header.startswith(magic):
                content_type = mime
                break
    # SVG: XML-based, no magic bytes
    if not content_type:
        stripped = data.lstrip(b"\xef\xbb\xbf").lstrip()
        if stripped.startswith(b"<svg") or (
            stripped.startswith(b"<?xml") and b"<svg" in data[:4096]
        ):
            content_type = "image/svg+xml"
    # PDF: %PDF magic bytes
    if not content_type:
        if header.startswith(b"%PDF"):
            content_type = "application/pdf"
    if not content_type:
        _log("denied", path)
        return web.json_response({"error": "file content is not a recognized format"}, status=403)
    _log("success", path)
    headers = {"Content-Type": content_type, "X-Content-Type-Options": "nosniff"}
    if content_type == "image/svg+xml":
        headers["Content-Security-Policy"] = "script-src 'none'; style-src 'unsafe-inline'"
    return web.Response(body=data, headers=headers)


async def api_file_write(request: web.Request) -> web.Response:
    """POST /api/file-write — write file content from the markdown panel."""
    import logging  # noqa: F811

    from personalclaw.validation import (  # noqa: F811
        FILE_WRITE_SCHEMA,
        ValidationError,
        validate_tool_args,
    )

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    if not isinstance(body, dict):
        return web.json_response({"error": "invalid JSON body"}, status=400)

    try:
        validate_tool_args(
            {"path": body.get("path", ""), "content": body.get("content", "")}, FILE_WRITE_SCHEMA
        )
    except ValidationError as exc:
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="file_write",
            outcome="denied",
            resources=body.get("path", ""),
        )
        return web.json_response({"error": _path_rejection(exc)}, status=400)

    path = _validate_dashboard_path(body.get("path", ""))
    if not path:
        refused = _app_path_refusal(str(body.get("path", "")), tool="file_write")
        if refused is not None:
            return refused
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="file_write",
            outcome="denied",
            resources=body.get("path", ""),
        )
        return web.json_response({"error": "invalid or forbidden path"}, status=400)
    if not os.path.isfile(path):
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_write", outcome="not_found", resources=path
        )
        return web.json_response({"error": "not found"}, status=404)
    try:

        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path))
        try:
            try:
                shutil.copymode(path, tmp_path)
            except OSError:
                pass
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(body.get("content", ""))
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_write", outcome="success", resources=path
        )
        return web.json_response({"ok": True})
    except Exception:
        logging.getLogger(__name__).exception("file_write failed for %s", path)
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_write", outcome="failure", resources=path
        )
        return web.json_response({"error": "failed to write file"}, status=500)


# Files larger than this are listed but flagged non-previewable to keep the
# explorer responsive; the reader (api_file_read) caps content separately.
_EXPLORER_MAX_ENTRIES = 2_000


async def api_file_list(request: web.Request) -> web.Response:
    """GET /api/file-list?path=... — list a directory for the file explorer.

    With no ``path`` (or ``path`` empty), returns the set of allowed root
    directories the explorer may browse. Otherwise lists the immediate
    children of ``path`` (non-recursive), sorted dirs-first then by name.
    Every path is validated through :func:`_validate_dashboard_path`, so the
    explorer can never escape the dashboard allowlist.
    """

    raw_path = request.query.get("path", "").strip()

    # No path → enumerate the allowed roots as the explorer's entry points.
    if not raw_path:
        roots = []
        for label, rp in _dashboard_roots():
            if os.path.isdir(rp):
                roots.append({"label": label, "path": rp, "name": label, "is_dir": True})
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_list", outcome="success", resources="<roots>"
        )
        return web.json_response({"roots": roots, "entries": [], "path": ""})

    path = _validate_dashboard_path(raw_path)
    if not path:
        refused = _app_path_refusal(raw_path, tool="file_list")
        if refused is not None:
            return refused
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_list", outcome="denied", resources=raw_path
        )
        return web.json_response({"error": "invalid or forbidden path"}, status=400)
    if not os.path.isdir(path):
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_list", outcome="not_found", resources=path
        )
        return web.json_response({"error": "not a directory"}, status=404)

    entries: list[dict] = []
    try:
        with os.scandir(path) as it:
            for de in it:
                if len(entries) >= _EXPLORER_MAX_ENTRIES:
                    break
                name = de.name
                # Hide the dashboard's own dot-noise but keep user dotfiles visible.
                if name in (".git", "__pycache__", ".DS_Store"):
                    continue
                try:
                    is_dir = de.is_dir(follow_symlinks=False)
                    st = de.stat(follow_symlinks=False)
                    size = int(st.st_size)
                    mtime = float(st.st_mtime)
                except OSError:
                    continue
                # Skip entries the validator would reject (blocked secrets) so
                # the explorer never even names a forbidden file.
                if not is_dir and _validate_dashboard_path(de.path) is None:
                    continue
                entries.append(
                    {
                        "name": name,
                        "path": os.path.realpath(de.path),
                        "is_dir": is_dir,
                        "size": size,
                        "mtime": mtime,
                        # Is this child a git repo ROOT? The listing hides `.git`, so without
                        # this the explorer cannot tell a checked-out project from any other
                        # folder and its whole git surface stays dark on the default view
                        # (#428). One extra stat per DIRECTORY child only.
                        "repo": is_dir and _is_git_repo_root(de.path),
                    }
                )
    except OSError:
        logging.getLogger(__name__).exception("file_list failed for %s", path)
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_list", outcome="failure", resources=path
        )
        return web.json_response({"error": "failed to list directory"}, status=500)

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    _sel().log_tool_invocation(
        session_key="dashboard", tool_name="file_list", outcome="success", resources=path
    )
    return web.json_response({"roots": [], "entries": entries, "path": path})


def _within_roots_resolved(path: str) -> bool:
    """:func:`_path_within_roots`, comparing FULLY RESOLVED paths on both sides.

    Necessary wherever the candidate has been through ``realpath``: on macOS ``/var`` is a symlink
    to ``/private/var`` (and a user's home or workspace may be symlinked anywhere), so comparing a
    resolved candidate against an unresolved root reports "outside" for a path that is plainly
    inside. Measured while writing this: the first version of the gitdir check refused every
    ordinary in-root repository, not just the escaping one.

    Separate from :func:`_path_within_roots` rather than a change to it, because that function's
    other callers pass paths that have already been canonicalized their own way, and widening the
    comparison for all of them is a bigger change than this fix.
    """
    resolved = os.path.realpath(path)
    for _label, root in _dashboard_roots():
        if not root:
            continue
        root_resolved = os.path.realpath(root)
        if resolved == root_resolved or resolved.startswith(root_resolved + os.sep):
            return True
    return False


def _gitfile_target(marker: str) -> str | None:
    """Where a ``.git`` FILE points, absolute and resolved. ``None`` if it points nowhere usable.

    A ``.git`` file is git's standard *gitfile pointer* — one line, ``gitdir: <path>`` — which is
    how ``git worktree`` and submodules work, so it is an ordinary shape rather than a synthetic
    one. The pointed-at path may be relative, in which case it resolves against the directory
    holding the marker, exactly as git resolves it.

    Only the FILE case needs this. A ``.git`` directory is inside the repo the call sites already
    validate, so resolving and re-checking it would add nothing and would only invite the
    symlink-comparison mistake :func:`_within_roots_resolved` exists to avoid.
    """
    try:
        with open(marker, "r", encoding="utf-8", errors="replace") as fh:
            line = fh.readline(4096).strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    target = line[len("gitdir:") :].strip()
    if not target:
        return None
    if not os.path.isabs(target):
        target = os.path.join(os.path.dirname(marker), target)
    return os.path.realpath(target)


def _is_git_repo_root(path: str) -> bool:
    """Is *path* ITSELF a git repo root — the one-level test, no upward walk.

    The explorer hides ``.git`` from every listing, so a directory holding a checked-out
    project was rendered identically to one that was not, and the whole git surface (branch
    chip, porcelain badges) only lit up if the user already knew to type the repo's own path
    into "Go to path". The three root tabs are never repos, so the default landing view
    showed nothing — a complete feature, invisible in practice (#428). Marking the repo
    ROOTS in the listing makes it discoverable without changing what status is fetched.

    Same gitdir validation as :func:`_git_repo_root` (a ``.git`` FILE naming a gitdir outside
    the dashboard roots is refused), so this cannot advertise a repo the git endpoints would
    then decline to read — a marker on a row whose branch never loads is worse than no
    marker. Deliberately does NOT walk up: it is called once per directory child, and the
    walk would make a listing O(children x depth) in stat calls.
    """

    marker = os.path.join(path, ".git")
    if os.path.isdir(marker):
        return True
    if not os.path.exists(marker):
        return False
    gitdir = _gitfile_target(marker)
    return gitdir is not None and _within_roots_resolved(gitdir)


def _git_repo_root(path: str) -> str | None:
    """Walk up from *path* to find the enclosing git repo root (has ``.git``).

    🔴 THE GITDIR IS VALIDATED, NOT ONLY THE MARKER'S LOCATION. `os.path.exists` is true for a
    ``.git`` FILE, and git honours the ``gitdir: <path>`` pointer inside it — so a one-line ``.git``
    file written into an allowed root redirected every git read endpoint at an arbitrary repository
    anywhere on disk. The containment check at each of the four call sites ran on every request and
    measured the directory CONTAINING the pointer, which is legitimately inside a root; the gitdir
    it named was never looked at. Whole-commit diffs then returned `.env`/`.key` contents that
    `file-read` refuses by name (#430). The check was wired and pointed at the wrong path — and
    the dashboard writes the pointer for you, since ``.git`` is not a blocked basename.

    Refusing here rather than at the call sites is deliberate — all four ask the same question of
    this function, and a guard added to three of them is the shape this bug already has.

    **One narrow behaviour change, stated rather than hidden:** a git *worktree* whose main
    repository lives OUTSIDE the dashboard roots now reports no repo, so the explorer shows no git
    status for it. That is the exact shape the escape uses, and a worktree of an in-root repo is
    unaffected (its gitdir sits inside that repo). Logged at debug so it is diagnosable rather than
    mysterious.
    """

    cur = path if os.path.isdir(path) else os.path.dirname(path)
    while cur and cur != os.path.dirname(cur):
        marker = os.path.join(cur, ".git")
        if os.path.isdir(marker):
            # An ordinary repository. Its gitdir is inside `cur`, which the call sites validate.
            return cur
        if os.path.exists(marker):
            # A gitfile pointer. WHERE IT POINTS is the thing to check.
            gitdir = _gitfile_target(marker)
            if gitdir is None:
                logger.debug("git: %s has a .git file that names no usable gitdir", cur)
                return None
            if not _within_roots_resolved(gitdir):
                logger.debug(
                    "git: refusing %s — its gitdir %s is outside the dashboard roots", cur, gitdir
                )
                return None
            return cur
        cur = os.path.dirname(cur)
    return None


def _path_within_roots(path: str) -> bool:
    """True if *path* is inside one of the dashboard's allowed roots."""

    for _label, root in _dashboard_roots():
        if root and (path == root or path.startswith(root + os.sep)):
            return True
    return False


# The committed-side read is the one git call a user waits on interactively (opening a
# diff), so it gets a longer deadline than the status/log polls. Named rather than
# inline so a test can INJECT a deadline instead of sleeping out the real one.
_GIT_SHOW_TIMEOUT = 30.0
# Ceiling on the committed side of a diff, in bytes of git's stdout.
_GIT_ORIGINAL_CAP = 512 * 1024


class _GitResult(NamedTuple):
    """What one ``git`` invocation produced.

    ``ok`` is False for EVERY way a read can fail to answer — git absent or not
    executable, the host out of processes, the deadline blown, or a non-zero exit —
    because a caller that needs to tell "no answer" from "the answer is empty" needs
    one flag, not four exception types. ``out`` is still returned on a non-zero exit
    (git writes partial stdout before failing) so a caller may use whichever it wants.
    """

    ok: bool
    out: str
    truncated: bool = False


async def _git(
    args: list[str], cwd: str, timeout: float = 5.0, *, max_bytes: int | None = None
) -> _GitResult:
    """Run a read-only ``git`` command (arg vector, never shell) and report the result.

    🔴 **THE ONE GIT INVOCATION IN THIS MODULE.** A second, hand-rolled
    ``create_subprocess_exec("git", …)`` lived in :func:`api_file_git_original` and
    forgot, one by one, everything this function already knew (#432): a timed-out
    ``git show`` was neither killed nor reaped (**measured: 2 live processes leaked per
    timeout — the ``git`` child and its grandchild — accumulating one pair per poll**),
    an unexecutable git raised ``PermissionError`` out of the handler and answered the
    request with **HTTP 500** where this function returns "no answer", and the copy's
    ``returncode`` check was the only thing standing between the caller and a git *tree
    listing* served as a file's contents. The rail for that is
    ``test_files_git_status.py::test_files_has_exactly_one_git_invoker`` — it walks this
    module's AST, so a new git spawn cannot be added quietly.

    The timeout path goes through :func:`personalclaw.cancellation.kill_timed_out`
    rather than ``proc.kill()`` + ``await proc.wait()``, and the child leads its own
    session so that helper's GROUP branch can fire. Both halves were measured, not
    reasoned: with a forking ``git`` (an fsmonitor hook, an LFS filter — git plumbing
    does fork) the pid-only kill left the grandchild holding the inherited stdout pipe,
    and ``wait()`` resolves on pipe disconnect rather than on reaping, so **one
    git-status request with a 1.00s deadline took 135.48s to return**. A deadline that
    waits out the child it killed is not a deadline.

    *max_bytes*, when given, caps stdout BEFORE decode+redaction and sets
    :attr:`_GitResult.truncated` — so a caller that only wants the first N bytes of a
    committed blob doesn't pay a multi-megabyte regex pass to find that out.
    """

    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            # So kill_timed_out can signal the GROUP: git forks (remote helpers,
            # fsmonitor, filter drivers) and a grandchild holds this stdout pipe.
            start_new_session=True,
        )
    except (OSError, ValueError):
        # git absent (FileNotFoundError), present but not executable (PermissionError),
        # or the host out of fds/processes. A read that cannot be ASKED reads as no
        # answer — degrade, never raise into the request.
        return _GitResult(False, "")
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, OSError, ValueError):
        # Deadline blown or the pipe broke. Either way the child is still RUNNING and
        # must not outlive the request: group-signalled and reaped under a bound.
        await kill_timed_out(proc)
        return _GitResult(False, "")
    truncated = max_bytes is not None and len(out) > max_bytes
    if max_bytes is not None:
        out = out[:max_bytes]
    text = out.decode("utf-8", "replace")
    text, _ = redact_credentials(text)
    text, _ = redact_exfiltration_urls(text)
    return _GitResult(proc.returncode == 0, text, truncated)


#: Cap on the ``ls-files`` stdout that expands a collapsed untracked directory.
#: Collapsing is what keeps the status response small — an untracked (and NOT
#: ignored) ``node_modules`` is a single porcelain line but tens of thousands of
#: paths once expanded. On a trip the directory itself still badges from its
#: porcelain entry; only deep children go unmarked.
_UNTRACKED_EXPAND_MAX_BYTES = 1 << 20


async def _expand_untracked_dirs(repo: str, rel_dirs: list[str]) -> dict[str, str]:
    """Per-path ``??`` for everything inside a wholly-untracked directory.

    🔴 The other half of #431. ``git status --porcelain`` collapses a directory that is
    untracked in full to ONE entry (``?? report/``) and never names what is inside, so
    stripping the trailing slash badges the folder and still leaves its whole subtree
    unmarked. That subtree is the case that matters: when an agent produces a
    deliverable it writes a NEW DIRECTORY of files, which is precisely the shape
    porcelain collapses, so the most common way new content appears in a workspace was
    the one case the badges never marked.

    ``ls-files --others`` rather than a local walk because git owns the ignore rules:
    measured in a scratch repo, a gitignored ``report/ign.log`` inside an untracked
    ``report/`` is absent from this output, while `os.walk` would have badged it ``??``.
    One call for all collapsed roots, pathspec-scoped, so a repo without any pays
    nothing. ``:(literal)`` disables pathspec magic — a directory literally named
    ``:(glob)x`` is a path here, not a pattern.

    Intermediate directories are DERIVED from the returned paths instead of statted: the
    collapsed root is untracked in full, so by construction every directory under it is
    too. The walk stops AT that root and never above it — porcelain collapsing to
    ``?? a/b/`` rather than ``?? a/`` is proof that ``a`` holds tracked content, and
    badging it would claim a whole committed directory is new.
    """
    if not rel_dirs:
        return {}
    roots = [d.rstrip("/") for d in rel_dirs]
    res = await _git(
        ["ls-files", "--others", "--exclude-standard", "-z", "--"]
        + [f":(literal){r}/" for r in roots],
        repo,
        max_bytes=_UNTRACKED_EXPAND_MAX_BYTES,
    )
    if not res.ok:
        return {}
    rels = res.out.split("\0")
    if res.truncated and rels:
        rels.pop()  # a cut at max_bytes leaves a partial path with no terminating NUL
    out: dict[str, str] = {}
    for rel in rels:
        if not rel:
            continue
        out[os.path.join(repo, rel)] = "??"
        root = next((r for r in roots if rel.startswith(f"{r}/")), None)
        if root is None:
            continue
        parent = os.path.dirname(rel)
        while parent and parent != root:
            out[os.path.join(repo, parent)] = "??"
            parent = os.path.dirname(parent)
    return out


async def api_file_git_status(request: web.Request) -> web.Response:
    """GET /api/file-git-status?path=... — git branch + per-file status.

    Returns ``{repoRoot, branch, statuses}`` where ``statuses`` maps absolute
    file paths to a porcelain status code (``M``, ``A``, ``D``, ``R``, ``??``).
    Empty repoRoot when *path* is not inside a git repo. The repo root must lie
    within the dashboard's allowed roots, so this can never inspect an arbitrary
    repo via a crafted path.

    Keys are the shape the file tree indexes by — ``os.path.realpath``, never a
    trailing separator. See :func:`_expand_untracked_dirs` for the collapsed-directory
    case that made those two disagree (#431).
    """

    raw = request.query.get("path", "").strip()
    path = _validate_dashboard_path(raw)
    if not path:
        refused = _app_path_refusal(raw, tool="file_git_status")
        if refused is not None:
            return refused
        return web.json_response({"error": "invalid or forbidden path"}, status=400)
    repo = _git_repo_root(path)
    empty = {"repoRoot": "", "branch": "", "statuses": {}}
    if not repo or not _path_within_roots(repo):
        return web.json_response(empty)

    # ``symbolic-ref`` reports the real branch even on an UNBORN branch (a fresh
    # `git init` with no commits yet — the greenfield case), where
    # ``rev-parse --abbrev-ref HEAD`` just prints the literal "HEAD". Fall back to
    # rev-parse for a genuinely detached HEAD (no symbolic ref).
    branch = (await _git(["symbolic-ref", "--short", "HEAD"], repo)).out.strip()
    if not branch:
        branch = (await _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)).out.strip()
    porcelain = (await _git(["status", "--porcelain", "-z"], repo)).out
    statuses: dict[str, str] = {}
    # ``-z`` separates entries with NUL; rename entries carry a second NUL-
    # separated path (the origin) which we skip.
    parts = porcelain.split("\0")
    # Wholly-untracked directories, as porcelain reports them (with the slash), so their
    # subtrees can be expanded in one extra call below.
    collapsed: list[str] = []
    i = 0
    while i < len(parts):
        entry = parts[i]
        if not entry or len(entry) < 4:
            i += 1
            continue
        code = entry[:2].strip() or entry[:2]
        rel = entry[3:]
        # A directory that is untracked in full arrives as ``?? report/``. The tree keys
        # on `os.path.realpath`, which never carries a trailing separator, so joining
        # this verbatim produced `<repo>/report/` against a tree key of `<repo>/report`
        # and the lookup missed — zero badges on the folder AND on every file inside it
        # (#431). The slash survives `-z`, so this is the parse's job, not git's.
        if rel.endswith("/"):
            collapsed.append(rel)
            rel = rel.rstrip("/")
        statuses[os.path.join(repo, rel)] = code
        # A rename/copy ("R"/"C") consumes the next NUL-separated origin path.
        if entry[:1] in ("R", "C"):
            i += 2
        else:
            i += 1
    statuses.update(await _expand_untracked_dirs(repo, collapsed))

    _sel().log_tool_invocation(
        session_key="dashboard", tool_name="git_status", outcome="success", resources=repo
    )
    return web.json_response({"repoRoot": repo, "branch": branch, "statuses": statuses})


async def api_file_git_log(request: web.Request) -> web.Response:
    """GET /api/file-git-log?path=...&limit=N — recent commits for a repo.

    Returns ``{repoRoot, commits: [{hash, subject, relative, author}]}`` (newest
    first). Empty when *path* isn't in a git repo or the repo has no commits yet.
    Same root-containment guard as git-status, so it can't read an arbitrary repo.
    """
    raw = request.query.get("path", "").strip()
    path = _validate_dashboard_path(raw)
    if not path:
        refused = _app_path_refusal(raw, tool="file_git_log")
        if refused is not None:
            return refused
        return web.json_response({"error": "invalid or forbidden path"}, status=400)
    repo = _git_repo_root(path)
    if not repo or not _path_within_roots(repo):
        return web.json_response({"repoRoot": "", "commits": []})
    try:
        limit = max(1, min(50, int(request.query.get("limit", "20"))))
    except (TypeError, ValueError):
        limit = 20
    # NUL-delimited fields per commit, record-separated by newline, so subjects
    # with arbitrary punctuation parse cleanly.
    fmt = "%h%x00%s%x00%cr%x00%an"
    out = (await _git(["log", f"-{limit}", f"--pretty=format:{fmt}"], repo)).out
    commits: list[dict] = []
    for line in out.splitlines():
        parts = line.split("\x00")
        if len(parts) == 4:
            commits.append(
                {"hash": parts[0], "subject": parts[1], "relative": parts[2], "author": parts[3]}
            )
    _sel().log_tool_invocation(
        session_key="dashboard", tool_name="git_log", outcome="success", resources=repo
    )
    return web.json_response({"repoRoot": repo, "commits": commits})


async def api_file_git_commit(request: web.Request) -> web.Response:
    """GET /api/file-git-commit?path=...&hash=... — one commit's diff.

    Returns ``{repoRoot, hash, subject, diff}`` — the unified diff of that commit
    (``git show``), so the cockpit can review what a stage changed. The hash must
    be hex (no arg injection); same root-containment guard as git-log.
    """
    raw = request.query.get("path", "").strip()
    path = _validate_dashboard_path(raw)
    if not path:
        refused = _app_path_refusal(raw, tool="file_git_commit")
        if refused is not None:
            return refused
        return web.json_response({"error": "invalid or forbidden path"}, status=400)
    h = request.query.get("hash", "").strip()
    # Hex-only (4-40 chars): a git short/full hash. Rejects flags + injection.
    if not re.fullmatch(r"[0-9a-fA-F]{4,40}", h):
        return web.json_response({"error": "invalid commit hash"}, status=400)
    repo = _git_repo_root(path)
    if not repo or not _path_within_roots(repo):
        return web.json_response(
            {"repoRoot": "", "hash": h, "subject": "", "diff": "", "found": False}
        )
    # Confirm the hash actually resolves to a commit in THIS repo first. A valid-hex
    # but unknown hash (stale ref after a force-push/rebase, or a commit from a
    # different repo the workspace was re-pointed away from) otherwise yields empty
    # subject+diff — which the cockpit would misreport as a legit "empty checkpoint"
    # rather than "this commit no longer exists here". rev-parse --verify --quiet
    # echoes the full sha when h is a real commit in this repo, nothing when it isn't.
    resolved = (
        await _git(["rev-parse", "--verify", "--quiet", f"{h}^{{commit}}"], repo)
    ).out.strip()
    if not resolved:
        return web.json_response(
            {"repoRoot": repo, "hash": h, "subject": "", "diff": "", "found": False}
        )
    subject = (await _git(["show", "-s", "--format=%s", h], repo)).out.strip()
    # ``--`` separates the rev from paths so a hash can never be read as a flag.
    diff = (
        await _git(["show", "--no-color", "--stat", "--patch", h, "--"], repo, timeout=10.0)
    ).out
    _sel().log_tool_invocation(
        session_key="dashboard", tool_name="git_commit", outcome="success", resources=repo
    )
    # Cap the payload, but SIGNAL when it was cut so the cockpit can say so — a silently
    # truncated patch reads as the whole commit (no-silent-caps).
    _CAP = 512 * 1024
    truncated = len(diff) > _CAP
    return web.json_response(
        {
            "repoRoot": repo,
            "hash": h,
            "subject": subject,
            "diff": diff[:_CAP],
            "truncated": truncated,
            "found": True,
        }
    )


async def api_file_git_original(request: web.Request) -> web.Response:
    """GET /api/file-git-original?path=... — the committed (HEAD) contents of a
    file, for a working-vs-HEAD diff view. Returns ``{content, exists}``; exists is
    False for anything HEAD holds no FILE for — a newly added path (the diff is then
    against empty), a directory, or a repo git cannot currently be asked about.
    Allowlist-gated like the rest; redacted on the way out (inside :func:`_git`)."""
    raw = request.query.get("path", "").strip()
    path = _validate_dashboard_path(raw)
    if not path:
        refused = _app_path_refusal(raw, tool="file_git_original")
        if refused is not None:
            return refused
        return web.json_response({"error": "invalid or forbidden path"}, status=400)
    repo = _git_repo_root(path)
    if not repo or not _path_within_roots(repo):
        return web.json_response({"content": "", "exists": False})
    try:
        rel = os.path.relpath(path, repo)
    except ValueError:
        return web.json_response({"content": "", "exists": False})
    # ``cat-file blob`` asks the exact question this endpoint answers — "the committed
    # FILE at this path" — and fails when the named object is not a blob. The replaced
    # ``show HEAD:<rel>`` asked a looser one: for a DIRECTORY it exits 0 and prints a
    # tree LISTING, which this endpoint served as the file's committed content with
    # exists:true. MEASURED on the pre-fix code (#432):
    #   {"content": "tree HEAD:sub\n\na.txt\nb.txt\n", "exists": true}
    # — the diff view rendered a directory index as the original side of a file.
    # Type-checking the object separately would be a second round trip to re-derive
    # what the read itself can refuse.
    #
    # The 512KB cap is applied inside _git (before redaction, so a multi-megabyte blob
    # is not regex-scanned to be thrown away) and reported back, so a cut HEAD side is
    # still SIGNALLED rather than silently short — else a large committed file's diff
    # reads as if the worker deleted the tail (no-silent-caps; mirrors fileRead's
    # X-Truncated and the commit view's truncated flag).
    res = await _git(
        ["cat-file", "blob", f"HEAD:{rel}"],
        repo,
        timeout=_GIT_SHOW_TIMEOUT,
        max_bytes=_GIT_ORIGINAL_CAP,
    )
    if not res.ok:
        return web.json_response({"content": "", "exists": False})
    _sel().log_tool_invocation(
        session_key="dashboard", tool_name="git_show", outcome="success", resources=repo
    )
    return web.json_response({"content": res.out, "exists": True, "truncated": res.truncated})


_CONTENT_SEARCH_IGNORE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "target",
    ".next",
}
_CONTENT_SEARCH_MAX_RESULTS = 500
_CONTENT_SEARCH_TIMEOUT = 15.0
_CONTENT_SEARCH_STOP_GRACE = 0.1
_RG_AVAILABLE: bool | None = None


class _ContentSearchTimedOut(Exception):
    """The Python fallback crossed its deadline or received a stop request.

    Deliberately NOT a ``TimeoutError``: that is an ``OSError`` subclass, so the
    per-line deadline check inside the file-read ``try`` below would be caught by
    its ``except OSError: continue`` and read as an unreadable file — the walk
    would step to the next file and return a partial set reporting ``truncated=False``.
    """


def _check_content_search_deadline(
    deadline: float | None, stop_event: threading.Event | None
) -> None:
    if (stop_event is not None and stop_event.is_set()) or (
        deadline is not None and time.monotonic() >= deadline
    ):
        raise _ContentSearchTimedOut


def _has_rg() -> bool:
    """Whether ripgrep is on PATH (cached after first lookup)."""

    global _RG_AVAILABLE
    if _RG_AVAILABLE is None:
        _RG_AVAILABLE = shutil.which("rg") is not None
    return _RG_AVAILABLE


async def _content_search_rg(
    root: str,
    query: str,
    include: str,
    allowed_roots: tuple[str, ...] | None = None,
) -> tuple[list[dict], bool]:
    """Content search via ripgrep --json. Returns (results, truncated)."""
    import asyncio  # noqa: F811
    import json as _json  # noqa: F811

    args = ["rg", "--json", "--max-count", "50", "-i", "--", query, root]
    for glob in [g.strip() for g in include.split(",") if g.strip()]:
        args[1:1] = ["-g", glob]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return [], False
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=_CONTENT_SEARCH_TIMEOUT)
    except (asyncio.TimeoutError, OSError, ValueError):
        # The sibling of #432's git leak, in this same module: search over a huge tree
        # blows the 15s deadline, the handler returned, and ripgrep kept running —
        # unkilled and unreaped, one orphan per timed-out search. No start_new_session
        # above on purpose: rg never forks, so kill_timed_out's single-pid fallback is
        # the right signal and a group it doesn't lead would only widen the blast radius.
        await kill_timed_out(proc)
        return [], False
    results: list[dict] = []
    for line in out.decode("utf-8", "replace").splitlines():
        try:
            obj = _json.loads(line)
        except ValueError:
            continue
        if obj.get("type") != "match":
            continue
        d = obj["data"]
        path = d["path"].get("text", "")
        if not path or _validate_dashboard_path(path, allowed_roots) is None:
            continue
        text = (d.get("lines", {}) or {}).get("text", "")
        sub = d.get("submatches") or [{}]
        results.append(
            {
                "file": path,
                "line": d.get("line_number", 0),
                "col": (sub[0].get("start", 0) + 1) if sub else 1,
                "preview": _redact_search_preview(text.rstrip("\n")[:300]),
            }
        )
        if len(results) >= _CONTENT_SEARCH_MAX_RESULTS:
            return results, True
    return results, False


def _content_search_python(
    root: str,
    query: str,
    include: str,
    allowed_roots: tuple[str, ...] | None = None,
    *,
    deadline: float | None = None,
    stop_event: threading.Event | None = None,
) -> tuple[list[dict], bool]:
    """Pure-Python content search fallback (no ripgrep). Returns (results, truncated)."""
    import fnmatch

    if allowed_roots is None:
        allowed_roots = tuple(rp for _label, rp in _dashboard_roots())
    globs = [g.strip().removeprefix("**/") for g in include.split(",") if g.strip()]
    needle = query.lower()
    results: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        _check_content_search_deadline(deadline, stop_event)
        dirnames[:] = [
            d for d in dirnames if d not in _CONTENT_SEARCH_IGNORE_DIRS and not d.startswith(".")
        ]
        rel_dir = os.path.relpath(dirpath, root)
        for fn in filenames:
            _check_content_search_deadline(deadline, stop_event)
            relpath = fn if rel_dir == "." else f"{rel_dir.replace(os.sep, '/')}/{fn}"
            if globs and not any(
                fnmatch.fnmatch(relpath, glob) or fnmatch.fnmatch(fn, glob) for glob in globs
            ):
                continue
            fpath = os.path.join(dirpath, fn)
            if _validate_dashboard_path(fpath, allowed_roots) is None:
                continue
            try:
                with open(fpath, encoding="utf-8", errors="ignore") as fh:
                    for n, line in enumerate(fh, 1):
                        _check_content_search_deadline(deadline, stop_event)
                        col = line.lower().find(needle)
                        if col >= 0:
                            results.append(
                                {
                                    "file": fpath,
                                    "line": n,
                                    "col": col + 1,
                                    "preview": _redact_search_preview(line.rstrip("\n")[:300]),
                                }
                            )
                            if len(results) >= _CONTENT_SEARCH_MAX_RESULTS:
                                return results, True
            except _ContentSearchTimedOut:
                # Ahead of the read-error clause on purpose: the stop signal must leave
                # the walk, not be mistaken for an unreadable file. Load-bearing only if
                # the class is ever re-based onto an OSError lineage again.
                raise
            except OSError:
                continue
    return results, False


def _redact_search_preview(text: str) -> str:
    cleaned, _ = redact_credentials(text)
    cleaned, _ = redact_exfiltration_urls(cleaned)
    return cleaned


async def api_file_content_search(request: web.Request) -> web.Response:
    """GET /api/file-content-search?path=&q=&include= — recursive content search.

    Distinct from ``/api/file-search`` (fuzzy FILENAME match for the @-mention
    picker): this greps file *contents* under a directory, ripgrep-backed with a
    Python fallback. ``include`` is a comma-separated glob filter (e.g. ``*.py``).
    Results: ``[{file, line, col, preview}]`` plus ``engine`` and ``truncated``.
    """
    raw = request.query.get("path", "").strip()
    allowed_roots = tuple(rp for _label, rp in _dashboard_roots())
    path = _validate_dashboard_path(raw, allowed_roots)
    if not path:
        refused = _app_path_refusal(raw, tool="file_content_search")
        if refused is not None:
            return refused
    if not path or not os.path.isdir(path):
        return web.json_response({"error": "invalid or forbidden directory"}, status=400)
    q = request.query.get("q", "").strip()
    engine = "rg" if _has_rg() else "python"
    if not q:
        return web.json_response({"results": [], "engine": engine, "truncated": False})
    include = request.query.get("include", "")
    if _has_rg():
        results, truncated = await _content_search_rg(path, q, include, allowed_roots)
    else:
        stop_event = threading.Event()
        deadline = time.monotonic() + _CONTENT_SEARCH_TIMEOUT
        search_task = asyncio.create_task(
            asyncio.to_thread(
                _content_search_python,
                path,
                q,
                include,
                allowed_roots,
                deadline=deadline,
                stop_event=stop_event,
            )
        )
        try:
            results, truncated = await asyncio.wait_for(
                asyncio.shield(search_task), timeout=_CONTENT_SEARCH_TIMEOUT
            )
        except (TimeoutError, _ContentSearchTimedOut):
            # BOTH deadlines land here, because both mean "this search ran out of time"
            # and the user-visible answer is identical. ``TimeoutError`` is the OUTER
            # ``wait_for`` winning; ``_ContentSearchTimedOut`` is the in-thread rail
            # winning the race with it (the thread's own ``deadline`` is computed before
            # ``wait_for`` starts its clock, so it CAN fire first). Naming only the outer
            # one let an in-thread win escape the handler, skipping the 504, the SEL
            # ``outcome="error"`` row and the "Narrow the directory" guidance (#3399).
            # The sentinel keeps its plain ``Exception`` base — re-basing it onto
            # ``TimeoutError`` would put it back inside the walk's ``except OSError``
            # reach, which is the bug its own docstring exists to prevent.
            stop_event.set()
            with contextlib.suppress(TimeoutError, asyncio.CancelledError, _ContentSearchTimedOut):
                await asyncio.wait_for(
                    asyncio.shield(search_task), timeout=_CONTENT_SEARCH_STOP_GRACE
                )
            if not search_task.done():
                search_task.cancel()
            _sel().log_tool_invocation(
                session_key="dashboard",
                tool_name="file_content_search",
                outcome="error",
                resources=f"{path} q={q[:80]} timeout",
            )
            return json_error(
                "file_content_search_timeout",
                message=(
                    "File content search exceeded its time limit. "
                    "Narrow the directory or include glob and try again."
                ),
                status=504,
            )
    _sel().log_tool_invocation(
        session_key="dashboard",
        tool_name="file_content_search",
        outcome="success",
        resources=f"{path} q={q[:80]}",
    )
    return web.json_response({"results": results, "engine": engine, "truncated": truncated})


async def api_file_complete(request: web.Request) -> web.Response:
    """GET /api/file-complete?path=&kind=&limit= — path autocomplete for the PathBar.

    Given a (possibly partial) path, returns the ``limit`` alphabetically-first
    matching children of its parent directory (directories before files).
    ``kind=dir`` restricts to directories. Every returned candidate is validated
    through the allowlist, so completion can never enumerate or escape outside the
    dashboard's roots.

    🔴 **Sort the whole candidate set, THEN cut to ``limit``.** This used to `break`
    out of the ``scandir`` loop at ``limit`` and sort the survivors, which slices in
    filesystem (inode) order and orders an arbitrary subset — the window looks
    alphabetical while not being the real top-N, so a directory that exists and
    matches the prefix is silently never offered (#426). Measured on a 58-entry home
    at the default ``limit=30``: 16 of the true top-30 were missing (`apps`,
    `codegraph`, `agent-metadata`, `loop`, …) with `.db-shm`/`.db-wal` SQLite
    internals offered in their place, and the response carries no ``truncated``
    flag, so the omission is invisible. It only bites above ``limit`` children —
    exactly the directories where autocomplete is load-bearing and the user cannot
    eyeball the listing.
    """

    raw = request.query.get("path", "").strip()
    kind = request.query.get("kind", "")
    try:
        limit = max(1, min(int(request.query.get("limit", "30")), 100))
    except (TypeError, ValueError):
        limit = 30

    expanded = os.path.expanduser(raw)
    # The directory to list, and the prefix the final segment must match.
    if raw.endswith("/"):
        parent, prefix = expanded.rstrip("/") or "/", ""
    else:
        parent, prefix = os.path.dirname(expanded), os.path.basename(expanded)
    parent_ok = _validate_dashboard_path(parent)
    if not parent_ok or not os.path.isdir(parent_ok):
        return web.json_response({"suggestions": []})

    # Pass 1 — the CHEAP filters (prefix, kind) over every entry. `de.is_dir` reads the
    # dirent cache, so this costs one `scandir` regardless of directory size.
    candidates: list[tuple[str, bool]] = []
    try:
        with os.scandir(parent_ok) as it:
            for de in it:
                if prefix and not de.name.startswith(prefix):
                    continue
                try:
                    is_dir = de.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if kind == "dir" and not is_dir:
                    continue
                candidates.append((de.name, is_dir))
    except OSError:
        return web.json_response({"suggestions": []})

    # Pass 2 — the EXPENSIVE filter (`realpath` + the allowlist) in sort order, stopping
    # at `limit`. Deferring it is what keeps the cost of sorting the full set at ~`limit`
    # validations instead of one per directory entry: the roots hold directories with
    # 9684 / 8299 / 8297 children.
    candidates.sort(key=lambda c: (not c[1], c[0].lower()))
    out: list[dict] = []
    for name, is_dir in candidates:
        if len(out) >= limit:
            break
        full = os.path.realpath(os.path.join(parent_ok, name))
        if _validate_dashboard_path(full) is None:
            continue
        out.append({"name": name, "path": full, "is_dir": is_dir})
    return web.json_response({"suggestions": out})


def _reject_name(name: str) -> str:
    """Why this single path component is unusable, or ``""`` when it is fine.

    🔴 The length check is the one that was missing (#652). The separator and dot-segment
    rules were spelled inline at two call sites and neither bounded the length, so a
    300-character name from the explorer's inline "New file" field passed every check, reached
    the OS, and raised `ENAMETOOLONG` — which fell through to the generic handler as a **500**.
    A name typed into a text box is client input; answering 5xx says the server is at fault.

    One function because the rules were duplicated: `api_file_create` and `api_file_upload`
    each had their own copy of the same three conditions, which is how a fourth rule reaches
    one site and not the other.

    🔴 The control-character rule is the OTHER half of #296. This function and
    `validation._FILE_PATH_RE` (which `file-read`/`file-write`/`file-watch` enforce) govern
    the same namespace, and they used to disagree: create had NO charset rule at all while
    read/write pinned `^[~/][-\\w.@~/ ]+$`, so the explorer wrote `notes (draft).md` and then
    400'd on every attempt to reopen it. The fix drops the read side's allowlist — it was
    never the traversal defence — and both ends now refuse exactly one thing about the
    characters: C0 controls and DEL. `sanitize_string` preserves `\\n`/`\\r`/`\\t`, so without
    this a created name could carry a newline into SEL's `resources=` field and into every
    log line composed from the path.
    """
    if not name:
        return "a name is required"
    if "/" in name or "\\" in name:
        return "a name may not contain a path separator"
    if name in (".", ".."):
        return "a name may not be '.' or '..'"
    if any(ch in name for ch in _CONTROL_CHARS):
        return "a name may not contain control characters"
    encoded = len(name.encode("utf-8"))
    if encoded > MAX_NAME_BYTES:
        return f"a name may be at most {MAX_NAME_BYTES} bytes; this one is {encoded}"
    return ""


async def api_file_create(request: web.Request) -> web.Response:
    """POST /api/file-create — create a new file or directory in the explorer.

    Body: ``{"path": "<parent-or-full>", "name": "<new>", "kind": "file"|"dir",
    "content": "<optional initial text>"}``. The target's parent must be an
    existing directory inside the dashboard allowlist and the final path must
    itself validate (so secrets/blocked names can't be created). Refuses to
    overwrite an existing path.
    """

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    parent_raw = str(body.get("path", "")).strip()
    name = require_string(body, "name")
    kind = str(body.get("kind", "file")).strip().lower()
    content = body.get("content", "")
    if kind not in ("file", "dir"):
        return web.json_response({"error": "kind must be 'file' or 'dir'"}, status=400)
    # Reject path separators / traversal / over-long names — it's a single segment.
    refusal = _reject_name(name)
    if refusal:
        return json_error("invalid_name", message=refusal, status=400)

    parent = _validate_dashboard_path(parent_raw)
    if not parent:
        refused = _app_path_refusal(str(parent_raw), tool="file_create")
        if refused is not None:
            return refused
    if not parent or not os.path.isdir(parent):
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_create", outcome="denied", resources=parent_raw
        )
        return web.json_response({"error": "invalid or forbidden parent directory"}, status=400)

    target = _validate_dashboard_path(os.path.join(parent, name))
    if not target:
        refused = _app_path_refusal(os.path.join(parent, name), tool="file_create")
        if refused is not None:
            return refused
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="file_create",
            outcome="denied",
            resources=f"{parent}/{name}",
        )
        return web.json_response({"error": "invalid or forbidden path"}, status=400)
    if os.path.exists(target):
        return web.json_response({"error": "already exists"}, status=409)

    try:
        if kind == "dir":
            os.makedirs(target, exist_ok=False)
        else:
            text = content if isinstance(content, str) else ""
            with open(target, "x", encoding="utf-8") as f:
                f.write(text)
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_create", outcome="success", resources=target
        )
        return web.json_response({"ok": True, "path": target, "is_dir": kind == "dir"})
    except FileExistsError:
        return web.json_response({"error": "already exists"}, status=409)
    except Exception:
        logging.getLogger(__name__).exception("file_create failed for %s", target)
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_create", outcome="failure", resources=target
        )
        return web.json_response({"error": "failed to create"}, status=500)


async def api_file_move(request: web.Request) -> web.Response:
    """POST /api/file-move — rename or relocate a file/dir within the allowlist.

    Body: ``{"src": "<path>", "dest": "<path>"}``. Rename is the same-parent
    case; relocate moves across directories. BOTH endpoints validate through
    :func:`_validate_dashboard_path` (so neither side can touch a sensitive or
    out-of-allowlist path) and refuse to overwrite an existing destination.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    src = _validate_dashboard_path(str(body.get("src", "")))
    dest = _validate_dashboard_path(str(body.get("dest", "")))
    if not src or not dest:
        # The side that was refused — the source first, which is the one an app reads from.
        refused_raw = str(body.get("src", "")) if not src else str(body.get("dest", ""))
        refused = _app_path_refusal(refused_raw, tool="file_move")
        if refused is not None:
            return refused
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="file_move",
            outcome="denied",
            resources=f"{body.get('src', '')}→{body.get('dest', '')}",
        )
        return web.json_response({"error": "invalid or forbidden path"}, status=400)
    # A dashboard ROOT passes _validate_dashboard_path (a root is inside itself),
    # but a root is never a valid move endpoint: moving `src` relocates the whole
    # root, and a `dest` that IS a root would target the root itself (issue #780).
    if _is_dashboard_root(src) or _is_dashboard_root(dest):
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="file_move",
            outcome="denied",
            resources=f"{src}→{dest}",
        )
        return web.json_response({"error": "refusing to move a root directory"}, status=400)
    if not os.path.exists(src):
        return web.json_response({"error": "source not found"}, status=404)
    if os.path.exists(dest):
        return web.json_response({"error": "destination already exists"}, status=409)
    dest_parent = os.path.dirname(dest)
    if not os.path.isdir(dest_parent):
        return web.json_response({"error": "destination directory does not exist"}, status=400)
    try:
        shutil.move(src, dest)
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="file_move",
            outcome="success",
            resources=f"{src}→{dest}",
        )
        return web.json_response({"ok": True, "path": dest})
    except Exception:
        logging.getLogger(__name__).exception("file_move failed %s → %s", src, dest)
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="file_move",
            outcome="failure",
            resources=f"{src}→{dest}",
        )
        return web.json_response({"error": "failed to move"}, status=500)


async def api_file_delete(request: web.Request) -> web.Response:
    """POST /api/file-delete — delete a file or (recursively) a directory.

    Body: ``{"path": "<path>"}``. Allowlist + sensitive-path gated; the path
    must resolve inside a dashboard root. A directory is removed with its
    contents (the explorer's delete-folder affordance).
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    path = _validate_dashboard_path(str(body.get("path", "")))
    if not path:
        refused = _app_path_refusal(str(body.get("path", "")), tool="file_delete")
        if refused is not None:
            return refused
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="file_delete",
            outcome="denied",
            resources=str(body.get("path", "")),
        )
        return web.json_response({"error": "invalid or forbidden path"}, status=400)
    # A dashboard ROOT is inside the allowlist (a root is inside itself), so it
    # passes _validate_dashboard_path — but it is never a valid delete target.
    # Without this guard, deleting a root shutil.rmtree()s the whole root, e.g.
    # the workspace or the real ~/.personalclaw home (issue #780).
    if _is_dashboard_root(path):
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_delete", outcome="denied", resources=path
        )
        return web.json_response({"error": "refusing to delete a root directory"}, status=400)
    if not os.path.exists(path):
        return web.json_response({"error": "not found"}, status=404)
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_delete", outcome="success", resources=path
        )
        return web.json_response({"ok": True})
    except Exception:
        logging.getLogger(__name__).exception("file_delete failed for %s", path)
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_delete", outcome="failure", resources=path
        )
        return web.json_response({"error": "failed to delete"}, status=500)


async def api_file_upload(request: web.Request) -> web.Response:
    """POST /api/file-upload?path=<dir> — upload file(s) into an allowed directory.

    Multipart body with one or more file parts. The target directory and every
    resulting file path validate through :func:`_validate_dashboard_path`, so an
    upload can't land outside the allowlist or overwrite a blocked/sensitive
    name. Existing files are refused (no silent overwrite). Each part is capped
    per-filetype by the shared upload policy (:func:`_upload_check`).
    """
    target_dir = _validate_dashboard_path(request.query.get("path", ""))
    if not target_dir:
        refused = _app_path_refusal(request.query.get("path", ""), tool="file_upload")
        if refused is not None:
            return refused
    if not target_dir or not os.path.isdir(target_dir):
        return web.json_response({"error": "invalid or forbidden directory"}, status=400)

    ctype = request.headers.get("Content-Type", "")
    if not ctype.lower().startswith("multipart/"):
        return web.json_response({"error": "multipart/form-data required"}, status=400)

    try:
        reader = await request.multipart()
    except (ValueError, AssertionError, RuntimeError) as exc:
        return web.json_response({"error": f"failed to parse multipart body: {exc}"}, status=400)

    saved: list[str] = []
    try:
        async for part in _iter_multipart(reader):
            filename = os.path.basename(part.filename or "")
            refusal = _reject_name(filename)
            if refusal:
                return json_error(
                    "invalid_name",
                    message=f"invalid filename in upload: {refusal}",
                    status=400,
                )
            dest = _validate_dashboard_path(os.path.join(target_dir, filename))
            if not dest:
                refused = _app_path_refusal(os.path.join(target_dir, filename), tool="file_upload")
                if refused is not None:
                    return refused
                return web.json_response({"error": f"forbidden filename: {filename}"}, status=400)
            if os.path.exists(dest):
                return web.json_response({"error": f"already exists: {filename}"}, status=409)
            # Per-filetype cap from the shared policy (browser mime disambiguates media).
            part_mime = part.headers.get("Content-Type") if part.headers else None
            _limit = _upload_check(filename, part_mime).limit
            size = 0
            fd, tmp = tempfile.mkstemp(dir=target_dir)
            try:
                with os.fdopen(fd, "wb") as f:
                    while True:
                        chunk = await part.read_chunk()
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > _limit:
                            raise ValueError(_upload_check(filename, part_mime, size=size).reason)
                        f.write(chunk)
                os.replace(tmp, dest)
            except Exception:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
            saved.append(dest)
    except ValueError as exc:
        for p in saved:
            with contextlib.suppress(OSError):
                os.unlink(p)
        return web.json_response(
            {"error": str(exc)}, status=413 if "too large" in str(exc) else 400
        )
    except Exception:
        logging.getLogger(__name__).exception("file_upload failed into %s", target_dir)
        for p in saved:
            with contextlib.suppress(OSError):
                os.unlink(p)
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="file_upload",
            outcome="failure",
            resources=target_dir,
        )
        return web.json_response({"error": "failed to upload"}, status=500)

    if not saved:
        return web.json_response({"error": "no file parts in upload"}, status=400)
    _sel().log_tool_invocation(
        session_key="dashboard",
        tool_name="file_upload",
        outcome="success",
        resources=",".join(saved),
    )
    return web.json_response({"ok": True, "paths": saved})


async def _iter_multipart(reader):
    """Yield each file part (skips non-file form fields)."""
    while True:
        part = await reader.next()
        if part is None:
            break
        if isinstance(part, BodyPartReader) and part.filename:
            yield part


def _apply_centrality(results: list, root: str, max_results: int) -> list:
    """Re-rank file matches by codebase centrality (CONTEXT-ECONOMY §5.5).

    Among files that match the query about equally well, the one the rest of the
    codebase actually references is far more likely to be the one meant. The boost
    is a fraction of a name-match tier, so it only ever breaks near-ties — a better
    textual match still wins outright.

    Fail-soft: no index (or any error) returns the list untouched, so `@`-mention
    behaves exactly as it does today.
    """
    if not results:
        return results
    try:
        from personalclaw.codegraph import CodeGraphIndex

        index = CodeGraphIndex(root)
        if index.is_empty():
            return results[:max_results]
        ranks = index.centrality(limit=400)
        index.close()
    except Exception:  # noqa: BLE001
        logger.debug("file_search: centrality unavailable", exc_info=True)
        return results[:max_results]
    if not ranks:
        return results[:max_results]
    top = max(ranks.values()) or 1

    def boosted(row: dict) -> float:
        try:
            rel = os.path.relpath(row["path"], root).replace(os.sep, "/")
        except (ValueError, KeyError):
            return float(row.get("_score", 0.0))
        # Capped at 8.0 — below the 10-point relpath-substring tier, so centrality
        # informs the order without overriding what the user typed.
        return float(row.get("_score", 0.0)) + 8.0 * (ranks.get(rel, 0) / top)

    ordered = sorted(results, key=lambda r: (-boosted(r), len(r.get("name", ""))))
    return ordered[:max_results]


def _fuzzy_score(q: str, name: str, rel: str) -> float:
    """Score a file match. Higher = better. Returns 0 for no match."""
    nl = name.lower()
    rl = rel.lower()
    score = 0.0

    # Exact filename match (sans extension)
    stem = nl.rsplit(".", 1)[0] if "." in nl else nl
    if q == nl or q == stem:
        score += 100.0
    elif nl.startswith(q):
        score += 50.0
    elif q in nl:
        score += 30.0
    elif q in rl:
        score += 10.0
    else:
        # Fuzzy: check if query chars appear in order in filename
        matched_on_name = True
        qi = 0
        consecutive = 0
        max_run = 0
        for ch in nl:
            if qi < len(q) and ch == q[qi]:
                qi += 1
                consecutive += 1
                max_run = max(max_run, consecutive)
            else:
                consecutive = 0
        if qi < len(q):
            # Try path if filename didn't match all chars
            matched_on_name = False
            qi = 0
            consecutive = 0
            max_run = 0
            for ch in rl:
                if qi < len(q) and ch == q[qi]:
                    qi += 1
                    consecutive += 1
                    max_run = max(max_run, consecutive)
                else:
                    consecutive = 0
        if qi < len(q):
            return 0.0  # not all query chars found
        # Score based on coverage ratio and longest consecutive run
        matched_len = len(nl) if matched_on_name else len(rl)
        coverage = len(q) / max(matched_len, 1)
        score += 5.0 + 15.0 * (max_run / len(q)) + 5.0 * coverage

    # Bonus: shorter filenames are more relevant
    score += max(0.0, 5.0 - len(nl) * 0.1)
    return score


async def api_file_search(request: web.Request) -> web.Response:
    """GET /api/file-search?q=... — fuzzy filename search for the @-mention file picker."""
    import time  # noqa: F811

    from personalclaw.security import is_sensitive_path  # noqa: F811

    caller = request.get("user", "dashboard")
    query = request.query.get("q", "").strip().lower()
    if len(query) < 2:
        return web.json_response({"results": []})

    max_results = 15

    # Scope search to the session's working directory (an arbitrary path).
    project = request.query.get("project", "") or request.query.get("workspace_dir", "")
    search_roots: list[str] = []
    # System roots should not be searchable from the @-mention picker; this
    # blocks directory enumeration at the search surface.
    if project:
        project = os.path.realpath(os.path.expanduser(project))
        if is_sensitive_path(project):
            _sel().log_api_access(
                caller=caller,
                operation="file_search",
                outcome="denied",
                resources=project,
                error="sensitive path",
            )
            return web.json_response({"error": "Access denied"}, status=403)
        if _is_system_root(project):
            _sel().log_api_access(
                caller=caller,
                operation="file_search",
                outcome="denied",
                resources=project,
                error="system root",
            )
            return web.json_response({"error": "Access denied"}, status=403)
        if os.path.isdir(project):
            search_roots.append(project)
        else:
            return web.json_response(
                {"results": [], "error": "Working directory not found"}, status=404
            )

    scoped = bool(search_roots)

    if not search_roots:
        # Fallback (no session cwd yet, e.g. a brand-new chat): scope to where the
        # agent can actually read. Native tools confine reads to the session's
        # workspace root (builtin_tools._resolve rejects paths that escape it), so
        # surfacing files from the whole home dir would offer mentions the agent
        # can't open. Search the configured workspace root (honors the
        # PERSONALCLAW_WORKSPACE override) plus an explicit project dir.
        proj = os.environ.get("PERSONALCLAW_PROJECT_DIR", "")
        if proj and os.path.isdir(proj):
            search_roots.append(proj)
        from personalclaw.config.loader import workspace_root  # noqa: F811

        try:
            ws = str(workspace_root())
            if os.path.isdir(ws) and ws not in search_roots:
                search_roots.append(ws)
        except Exception:
            pc_workspace = os.path.expanduser("~/.personalclaw/workspace")
            if os.path.isdir(pc_workspace):
                search_roots.append(pc_workspace)

    # Filter out sensitive roots
    safe_roots: list[str] = []
    for r in search_roots:
        if is_sensitive_path(r):
            _sel().log_api_access(
                caller=caller,
                operation="file_search",
                outcome="denied",
                resources=r,
                error="sensitive path",
            )
        else:
            safe_roots.append(r)

    # Fast path: use in-memory index when available for a single scoped project
    state: DashboardState = request.app["state"]
    if scoped and len(safe_roots) == 1:
        idx = state.file_indexes.get(safe_roots[0])
        if idx and idx.is_ready and not idx.truncated:
            # Over-fetch so the centrality boost has candidates to reorder; without
            # this it could only shuffle a list already cut to max_results.
            results = await asyncio.to_thread(idx.search, query, _fuzzy_score, max_results * 3)
            results = await asyncio.to_thread(
                _apply_centrality, results, safe_roots[0], max_results
            )
            trimmed = [{k: v for k, v in r.items() if k != "_score"} for r in results]
            _sel().log_api_access(
                caller=caller,
                operation="file_search",
                outcome="allowed",
                resources=f"q={query} indexed=true entries={idx.entry_count} results={len(trimmed)}",  # noqa: E501
            )
            return web.json_response({"results": trimmed, "root": safe_roots[0]})

    # Fallback: walk filesystem per request
    # Dot-prefixed dirs (.personalclaw, .local, .config) excluded by startswith(".") guard below.
    # ``_ext`` is PClaw's per-cwd memory-partition store (one ephemeral clone per
    # session working dir, each with identical agent-internal memory files) — it
    # must never surface here or it floods the picker with duplicate matches.
    skip_dirs = {
        ".git",
        "node_modules",
        "__pycache__",
        ".cache",
        ".venv",
        "venv",
        "dist",
        "build",
        "env",
        "out",
        "target",
        "_ext",
    }
    # Non-dot package caches (Go module cache ~/go/pkg) whose generic basenames
    # can't be blanket-skipped — pruned by absolute-path suffix during descent.
    from personalclaw.dashboard.file_index import is_pkg_cache_dir  # noqa: F811

    max_scan = 50_000 if scoped else 5_000
    max_collect = max_results * 10  # collect enough candidates for good scoring, then stop

    def _walk_file_search() -> list[dict]:
        """Blocking file-system walk — offloaded via asyncio.to_thread."""
        results: list[dict] = []
        walked = 0
        for root_dir in safe_roots:
            if walked >= max_scan or len(results) >= max_collect:
                break
            for dirpath, dirnames, filenames in os.walk(root_dir):
                dirnames[:] = [
                    d
                    for d in dirnames
                    if not d.startswith(".")
                    and d not in skip_dirs
                    and not is_pkg_cache_dir(os.path.join(dirpath, d))
                ]
                for fname in filenames:
                    if walked >= max_scan or len(results) >= max_collect:
                        break
                    walked += 1
                    if fname.startswith("."):
                        continue
                    fpath = os.path.join(dirpath, fname)
                    rel = os.path.relpath(fpath, root_dir)
                    sc = _fuzzy_score(query, fname, rel)
                    if sc <= 0:
                        continue
                    if is_sensitive_path(fpath):
                        continue
                    try:
                        st = os.stat(fpath)
                    except OSError:
                        continue
                    results.append(
                        {
                            "path": fpath,
                            "name": fname,
                            "size": st.st_size,
                            "mtime": int(st.st_mtime),
                            "_score": sc,
                        }
                    )
                if walked >= max_scan or len(results) >= max_collect:
                    break
        return results

    results = await asyncio.to_thread(_walk_file_search)

    # Sort by score descending, then shorter name, then recency
    now = time.time()
    results.sort(key=lambda r: (-r["_score"], len(r["name"]), now - r["mtime"]))

    if scoped and len(safe_roots) == 1:
        results = await asyncio.to_thread(_apply_centrality, results, safe_roots[0], max_results)

    # Strip internal scoring field before response
    trimmed = [{k: v for k, v in r.items() if k != "_score"} for r in results[:max_results]]

    _sel().log_api_access(
        caller=caller,
        operation="file_search",
        outcome="allowed",
        resources=f"q={query} roots={len(safe_roots)} results={len(trimmed)}",
    )
    return web.json_response(
        {
            "results": trimmed,
            "root": safe_roots[0] if scoped and safe_roots else "",
        }
    )


def _default_browse_dir() -> str:
    """Where the folder picker opens when no folder is asked for: the WORKSPACE root.

    The same answer the Terminal gives (``terminal.py``'s cwd chain, #544), for the same reason:
    a folder the user creates from here is where their work lives. It used to be ``$HOME``, and in
    the container image that is ``/home/personalclaw`` — outside the ``/data`` volume — so a
    project folder made with "New folder here" at the picker's starting point was gone after
    ``docker rm`` + ``docker run``, while the project page kept showing its path.
    ``default_workspace_dir()`` is ``""`` when the root is missing or sensitive, and then the
    home is still the answer.
    """
    from personalclaw.config.loader import default_workspace_dir

    return default_workspace_dir() or os.path.realpath(os.path.expanduser("~"))


async def api_browse_dirs(request: web.Request) -> web.Response:
    """GET /api/browse-dirs?path=... — list subdirectories for directory browser.

    With no ``path`` it lists :func:`_default_browse_dir` — the workspace root.
    """

    from personalclaw.security import is_sensitive_path  # noqa: F811

    caller = request.get("user", "dashboard")
    raw = request.query.get("path", "").strip()
    base = os.path.realpath(os.path.expanduser(raw)) if raw else _default_browse_dir()
    if not os.path.isdir(base):
        # Distinguish the cases a path-bar user actually hits, instead of a blanket
        # "Not a directory": a path that doesn't exist (typo/stale), one that's a FILE,
        # or one the server can't read (permission). Vague errors made the picker's
        # jump-to-path feel broken when the path was simply mistyped.
        # Status matches api_file_list's contract: a path that isn't a browsable
        # directory (missing or a file) is 404 Not Found; only an unreadable
        # (permission) path is a 400 the caller can't act on.
        if not os.path.exists(base):
            return web.json_response({"error": "No such directory", "path": base}, status=404)
        if os.path.isfile(base):
            return web.json_response(
                {"error": "That path is a file, not a directory", "path": base}, status=404
            )
        return web.json_response(
            {"error": "Can't access that directory (permission denied)", "path": base}, status=400
        )
    # Credential dirs and system roots — the directory browser is for picking project/workspace
    # folders, not enumerating secrets or system internals.
    refusal = _picker_refusal(base)
    if refusal:
        _sel().log_api_access(
            caller=caller,
            operation="browse_dirs",
            outcome="denied",
            resources=base,
            error=refusal,
        )
        return _protected_location(f"Can't open {base}", base, refusal)
    skip = {
        ".git",
        "node_modules",
        "__pycache__",
        ".cache",
        ".venv",
        "venv",
        "env",
        ".personalclaw",
    }
    dirs: list[dict] = []
    try:
        for entry in sorted(os.scandir(base), key=lambda e: e.name.lower()):
            if (
                entry.is_dir(follow_symlinks=False)
                and entry.name not in skip
                and not entry.name.startswith(".")
            ):
                if is_sensitive_path(entry.path):
                    continue
                # Flag git repos so the brownfield workspace picker can mark which
                # folders are actual codebases (a cheap stat, not a walk).
                is_repo = os.path.isdir(os.path.join(entry.path, ".git"))
                dirs.append({"name": entry.name, "path": entry.path, "is_repo": is_repo})
    except PermissionError:
        pass
    _sel().log_api_access(caller=caller, operation="browse_dirs", outcome="allowed", resources=base)
    # Whether the CURRENT dir is inside a git repo (walks up, so a subdir of a repo
    # counts too) — lets the workspace picker confirm "Use this folder" lands on a
    # version-tracked codebase, the signal that most matters for a brownfield workspace
    # (a non-repo pick means no diff/history; see the cockpit Changes tab). The per-child
    # is_repo flags only mark repo ROOTS in the list; this covers the current location.
    in_repo = _git_repo_root(base) is not None
    return web.json_response(
        {"path": base, "parent": os.path.dirname(base), "dirs": dirs, "in_repo": in_repo}
    )


async def api_create_dir(request: web.Request) -> web.Response:
    """POST /api/create-dir — create a new directory."""

    from personalclaw.security import is_sensitive_path  # noqa: F811

    caller = request.get("user", "dashboard")
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    raw = str(body.get("path", "")).strip()
    if not raw:
        return web.json_response({"error": "path required"}, status=400)
    target = os.path.realpath(os.path.expanduser(raw))
    if is_sensitive_path(target):
        _sel().log_api_access(
            caller=caller,
            operation="create_dir",
            outcome="denied",
            resources=target,
            error="sensitive path",
        )
        return _protected_location(f"Can't create {target}", target, "sensitive path")
    # Don't create a workspace/project folder under a system root. Use the FULL
    # is_system_path (the same bind-semantics the Code workspace validation uses):
    # it also blocks the bare mount/temp PARENTS (/, /Volumes, /var, /tmp) — you'd
    # never create a project folder AT those (a child like /Volumes/disk/proj is fine,
    # allowed by the parent rule), so this can't materialize a stray folder anywhere a
    # workspace bind would then reject.
    if is_system_path(target):
        _sel().log_api_access(
            caller=caller,
            operation="create_dir",
            outcome="denied",
            resources=target,
            error="system root",
        )
        return _protected_location(f"Can't create {target}", target, "system root")
    # 🔴 Only where the picker could have navigated to. The folder is created inside `parent`, so
    # `parent` must be a folder browse-dirs would open. This used to be an ASSUMPTION ("the picker
    # always creates inside a dir it just navigated to") and it failed: with its first browse
    # refused, the picker built `'' + '/' + name`, and this route answered 200 for `/q4-launch` — a
    # new root-owned folder at the top of the disk, then bound as the project's workspace. Asking
    # the parent the browse question makes the server hold the assumption instead. Today that adds
    # exactly one refusal the two checks above cannot make — a child of `/` — which is never what a
    # user means: browse-dirs refuses `/` itself, so no picker was ever looking at it.
    parent = os.path.dirname(target)
    parent_refusal = _picker_refusal(parent)
    if parent_refusal:
        _sel().log_api_access(
            caller=caller,
            operation="create_dir",
            outcome="denied",
            resources=target,
            error=f"unbrowsable parent ({parent_refusal})",
        )
        return _protected_location(f"Can't create a folder in {parent}", parent, parent_refusal)
    # An over-long leaf name reaches `mkdir` as `ENAMETOOLONG` and surfaced as a 500 (#652).
    # Checked here rather than picked up from `_validate_dashboard_path`, because this endpoint
    # deliberately does NOT use it: it binds an arbitrary project/workspace folder outside the
    # dashboard allowlist, guarded by `is_sensitive_path` + `is_system_path` + the parent rule
    # instead. AFTER those three, so a denial never depends on how long the name happens to be.
    name_refusal = _reject_name(os.path.basename(target))
    if name_refusal:
        return json_error("invalid_name", message=name_refusal, status=400)
    if os.path.exists(target):
        return web.json_response({"error": "Already exists", "path": target}, status=409)
    # Create exactly ONE new leaf folder inside an EXISTING parent — not a chain.
    # makedirs() would silently materialize every missing ancestor, so a name like
    # "foo/bar/baz" (or a mistyped parent) built a surprise nested tree and buried the
    # workspace at the deepest level. The parent is one the picker can open (checked
    # above); it must also exist.
    if not os.path.isdir(parent):
        return web.json_response(
            {
                "error": "Parent folder doesn't exist — create the folder inside an existing directory.",  # noqa: E501
                "path": target,
            },
            status=400,
        )
    try:
        os.mkdir(target)
    except OSError as exc:
        # Raw OS text (errno + path) is diagnostics; the wire speaks guidance (failure_copy).
        logger.warning("create_dir failed for %s", target, exc_info=True)
        return web.json_response({"error": relayed_failure_copy(exc)}, status=500)
    _sel().log_api_access(caller=caller, operation="create_dir", outcome="ok", resources=target)
    return web.json_response({"ok": True, "path": target})


async def api_dashboard_config(request: web.Request) -> web.Response:
    """GET/PUT /api/dashboard/config — read or write dashboard settings."""
    cfg = AppConfig.load()
    if request.method == "PUT":
        try:
            body = await request.json()
        except Exception:
            _sel().log_tool_invocation(
                session_key="dashboard", tool_name="dashboard_config_write", outcome="failure"
            )
            return web.json_response({"error": "invalid JSON"}, status=400)
        if not isinstance(body, dict):
            _sel().log_tool_invocation(
                session_key="dashboard", tool_name="dashboard_config_write", outcome="failure"
            )
            return web.json_response({"error": "request body must be a JSON object"}, status=400)
        _allowed = {
            "restore_sessions",
            "restore_window_minutes",
            "merge_queued_messages",
            "auto_tag_sessions",
            "widget_density",
            "user_name",
            "username",  # attribution handle (TEAM-SHARED-ENTITIES §1)
            # server-stored message display prefs (consistent across browsers)
            "send_on_enter",
            "show_timestamps",
            "show_thinking_inline",
            "simplified_tool_names",
            "followup_chips",
            "offer_check_work",
            "stream_reveal",
            # MI-4 — the screen-context master switch. Writable here (the panel the
            # Settings toggle drives) as well as through the PATCH allowlist.
            "screen_share_enabled",
            # DFE-5 — the in-place document editor's master switch. Writable here (the
            # panel its Settings toggle drives) as well as through the PATCH allowlist.
            "document_editing",
        }
        unknown = set(body.keys()) - _allowed
        if unknown:
            _sel().log_tool_invocation(
                session_key="dashboard", tool_name="dashboard_config_write", outcome="failure"
            )
            return web.json_response({"error": f"Unknown fields: {unknown}"}, status=400)
        if "restore_sessions" in body:
            val = body["restore_sessions"]
            if not isinstance(val, bool):
                _sel().log_tool_invocation(
                    session_key="dashboard", tool_name="dashboard_config_write", outcome="failure"
                )
                return web.json_response(
                    {"error": "restore_sessions must be a boolean"}, status=400
                )
            cfg.dashboard.restore_sessions = val
        try:
            if "restore_window_minutes" in body:
                cfg.dashboard.restore_window_minutes = max(
                    0, min(1440, int(body["restore_window_minutes"]))
                )
        except (TypeError, ValueError):
            _sel().log_tool_invocation(
                session_key="dashboard", tool_name="dashboard_config_write", outcome="failure"
            )
            return web.json_response(
                {"error": "restore_window_minutes must be an integer"}, status=400
            )
        if "merge_queued_messages" in body:
            val = body["merge_queued_messages"]
            if not isinstance(val, bool):
                _sel().log_tool_invocation(
                    session_key="dashboard", tool_name="dashboard_config_write", outcome="failure"
                )
                return web.json_response(
                    {"error": "merge_queued_messages must be a boolean"}, status=400
                )
            cfg.dashboard.merge_queued_messages = val
        if "widget_density" in body:
            val = body["widget_density"]
            if val not in ("more", "less"):
                _sel().log_tool_invocation(
                    session_key="dashboard", tool_name="dashboard_config_write", outcome="failure"
                )
                return web.json_response(
                    {"error": "widget_density must be 'more' or 'less'"}, status=400
                )
            cfg.dashboard.widget_density = val
        if "stream_reveal" in body:
            val = body["stream_reveal"]
            if val not in ("smooth", "immediate"):
                _sel().log_tool_invocation(
                    session_key="dashboard", tool_name="dashboard_config_write", outcome="failure"
                )
                return web.json_response(
                    {"error": "stream_reveal must be 'smooth' or 'immediate'"}, status=400
                )
            cfg.dashboard.stream_reveal = val
        if "user_name" in body:
            val = body["user_name"]
            if not isinstance(val, str):
                _sel().log_tool_invocation(
                    session_key="dashboard", tool_name="dashboard_config_write", outcome="failure"
                )
                return web.json_response({"error": "user_name must be a string"}, status=400)
            # Operator name — trimmed + length-capped. Empty string is valid
            # (clears the name → re-triggers onboarding).
            cfg.dashboard.user_name = val.strip()[:80]
        if "username" in body:
            val = body["username"]
            if not isinstance(val, str):
                _sel().log_tool_invocation(
                    session_key="dashboard", tool_name="dashboard_config_write", outcome="failure"
                )
                return web.json_response({"error": "username must be a string"}, status=400)
            # Attribution handle — normalized at the boundary so what lands in
            # records is always canonical, whatever the client sent. Empty is
            # valid: it means "no attribution" (TEAM-SHARED-ENTITIES §1).
            from personalclaw.identity import slugify_username

            cfg.dashboard.username = slugify_username(val)
        # message display prefs + auto-tagging — all booleans
        for _bool_field in (
            "send_on_enter",
            "show_timestamps",
            "show_thinking_inline",
            "simplified_tool_names",
            "followup_chips",
            "offer_check_work",
            "auto_tag_sessions",
            "screen_share_enabled",
            "document_editing",
        ):
            if _bool_field in body:
                val = body[_bool_field]
                if not isinstance(val, bool):
                    _sel().log_tool_invocation(
                        session_key="dashboard",
                        tool_name="dashboard_config_write",
                        outcome="failure",
                    )
                    return web.json_response(
                        {"error": f"{_bool_field} must be a boolean"}, status=400
                    )
                setattr(cfg.dashboard, _bool_field, val)
        cfg.save()
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="dashboard_config_write", outcome="success"
        )
        return web.json_response({"ok": True})
    _sel().log_tool_invocation(
        session_key="dashboard", tool_name="dashboard_config_read", outcome="success"
    )
    return web.json_response(
        {
            "restore_sessions": cfg.dashboard.restore_sessions,
            "restore_window_minutes": cfg.dashboard.restore_window_minutes,
            "merge_queued_messages": cfg.dashboard.merge_queued_messages,
            "auto_tag_sessions": cfg.dashboard.auto_tag_sessions,
            "widget_density": cfg.dashboard.widget_density,
            "user_name": cfg.dashboard.user_name,
            "username": cfg.dashboard.username,
            "send_on_enter": cfg.dashboard.send_on_enter,
            "show_timestamps": cfg.dashboard.show_timestamps,
            "show_thinking_inline": cfg.dashboard.show_thinking_inline,
            "simplified_tool_names": cfg.dashboard.simplified_tool_names,
            "followup_chips": cfg.dashboard.followup_chips,
            "offer_check_work": cfg.dashboard.offer_check_work,
            "stream_reveal": cfg.dashboard.stream_reveal,
            "screen_share_enabled": cfg.dashboard.screen_share_enabled,
            "document_editing": cfg.dashboard.document_editing,
        }
    )
