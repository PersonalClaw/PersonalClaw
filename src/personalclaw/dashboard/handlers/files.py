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
import time
import uuid
from pathlib import Path

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError
from aiohttp.multipart import BodyPartReader

from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.state import DashboardState
from personalclaw.security import (
    is_sensitive_path,
    is_system_path,
    redact_credentials,
    redact_exfiltration_urls,
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
# The subtree list is sourced from personalclaw.security (the single source of truth
# shared with the Code workspace validation) so the surfaces can't drift. NOTE: this
# is a SUBTREE-only check (no mount/temp PARENT blocking, unlike security.is_system_path)
# — the directory BROWSER + @-search must be able to navigate INTO /Volumes, /var, /tmp
# to reach a real workspace beneath them. create-dir/workspace-bind use the stricter
# full is_system_path (parents blocked) since you never create/bind AT a bare parent.
from personalclaw.security import _SYSTEM_SUBTREES as _SYSTEM_ROOTS  # noqa: E402


def _is_system_root(path: str) -> bool:
    """True iff *path* is the filesystem root or sits under a protected system root
    (an already realpath'd absolute path is expected)."""
    return path == "/" or any(path == r or path.startswith(r + os.sep) for r in _SYSTEM_ROOTS)


def _sel():
    """Late-binding _sel() for test monkeypatch compatibility."""
    import personalclaw.dashboard.handlers as _pkg  # noqa: F811
    return _pkg.sel()


def _path_home_pclaw() -> Path:
    """Resolve PersonalClaw home dir, honoring PERSONALCLAW_HOME."""
    try:
        from personalclaw.config.loader import config_dir as _cd
        return _cd()
    except Exception:
        return Path.home() / ".personalclaw"


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
        _sel().log_tool_invocation(
            session_key="api", source="api", tool_name="reveal_path",
            outcome="denied", error="sensitive_path",
            resources=path, metadata={"action": action})
        return web.json_response({"error": "access denied"}, status=403)
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
        session_key="api", source="api", tool_name="reveal_path",
        outcome="success", resources=path, metadata={"action": action})
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
    if not isinstance(raw_path, str) or not isinstance(raw_filename, str) or not isinstance(raw_desc, str):
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
            return web.json_response({"error": "Only UTF-8 text or media (audio/video/image/pdf) files are supported"}, status=400)
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
    import urllib.parse  # noqa: F811

    from personalclaw.config.loader import outbox_dir  # noqa: F811
    from personalclaw.hooks import FileTooLargeError, safe_read_file_bytes  # noqa: F811
    from personalclaw.security import redact  # noqa: F811

    import mimetypes  # noqa: PLC0415

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
            session_key="api", source="api", tool_name="notify_attachment",
            tool_kind="download", outcome="completed",
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
    safe_name = urllib.parse.quote(path.name, safe="")
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
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}"},
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
    entries.sort(key=lambda x: float(x["modified"]), reverse=True)  # type: ignore[arg-type,return-value]

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
    """POST /api/channel/upload-file — upload a file to the active channel (internal, called by notify_attachment)."""
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
        return web.json_response({"error": f"Redaction failed: {redact_err}"}, status=500)
    # Resolve channel: use owner DM if no channel specified
    channel = ""
    try:
        creds = AppConfig.load().load_credentials()
        owner_id = creds.get("PERSONALCLAW_OWNER_ID", "")
        if owner_id:
            channel = await delivery.open_dm(owner_id)
    except Exception:
        pass
    if not channel:
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="channel",
            outcome="skipped",
            error="no_channel",
        )
        return web.json_response({"ok": True, "skipped": "no_channel"})
    try:
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
        await delivery.upload_attachment(
            channel,
            str(resolved),
            filename=safe_filename,
            thread_ts=thread_ts or "",
            title=safe_filename,
        )
        _sel().log_tool_invocation(
            session_key="api",
            source="api",
            tool_name="notify_attachment",
            tool_kind="channel",
            outcome="completed",
            downstream_service="channel",
            resources=f"channel={channel} file={file_path}",
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
        return web.json_response({"error": str(e)}, status=500)


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


_SCREENSHOT_DIR = _path_home_pclaw() / "screenshots"

_UPLOAD_DIR = _path_home_pclaw() / "uploads"
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

    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
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
            {"error": f"failed to parse multipart body: {exc}"}, status=400,
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
            part_mime = (part.headers or {}).get("Content-Type") or None
            _limit = _upload_check(safe_name, part_mime).limit
            # Stream to a tempfile, enforcing the category cap as bytes arrive — never
            # buffer the whole file in memory (a 2 GB video would OOM otherwise).
            dest = _UPLOAD_DIR / f"{uuid.uuid4().hex}_{safe_name}"
            if not dest.resolve().is_relative_to(_UPLOAD_DIR.resolve()):
                _cleanup()
                _sel().log_api_access(
                    caller=caller, operation="upload.file", outcome="rejected",
                    source="dashboard", resources=f"file:{fname} reason:path_traversal",
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
                    caller=caller, operation="upload.file", outcome="rejected",
                    source="dashboard", resources=f"file:{fname} reason:too_large:{size}",
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
    uploads = str(_UPLOAD_DIR.resolve())
    if not path.startswith(uploads + os.sep):
        _sel().log_api_access(caller=caller, operation="attachment_extract", outcome="denied", resources=path, error="outside uploads")
        return web.json_response({"error": "Access denied"}, status=403)
    if not os.path.isfile(path):
        return web.json_response({"error": "Not found"}, status=404)
    from personalclaw.dashboard.attachment_extract import display_name, get_extractor

    text = await get_extractor().get(path, _mt.guess_type(path)[0])
    _sel().log_api_access(caller=caller, operation="attachment_extract", outcome="allowed", resources=f"name={display_name(path)} chars={len(text)}")
    return web.json_response({"name": display_name(path), "text": text})


async def api_screenshot(request: web.Request) -> web.Response:
    """POST /api/screenshot — capture screen region and return file path.

    macOS only — uses the built-in `screencapture` binary. Headless
    hosts (e.g. servers, containers) lack a display server, so this
    endpoint is unavailable on non-macOS platforms.
    """
    if sys.platform != "darwin":
        return web.json_response({"error": "Screenshot is only available on macOS"}, status=400)

    _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    dest = _SCREENSHOT_DIR / f"screenshot_{ts}.png"

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
    return web.json_response({"path": str(dest)})


def _dashboard_roots() -> list[tuple[str, str]]:
    """Return the labeled root directories the dashboard is allowed to surface.

    Each entry is ``(label, realpath)``. These are the boundaries the file
    explorer browses and the allowlist :func:`_validate_dashboard_path`
    enforces — workspace, outbox, uploads, and PERSONALCLAW_HOME. Roots that
    fail to resolve (e.g. not configured) are skipped. The order is
    user-facing-first (workspace) so the explorer can default to it.
    """

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
    _add("Uploads", lambda: os.path.expanduser("~/.personalclaw/uploads"))
    # PersonalClaw home root — quick access to the whole config/data tree.
    _add("PersonalClaw", lambda: os.path.expanduser("~/.personalclaw"))

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


def _validate_dashboard_path(raw: str) -> str | None:
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

    roots = [rp for _label, rp in _dashboard_roots()]

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
    # via /api/file-read.
    blocked_basenames = {
        "sel_hmac.key",
        ".local_secret",
        "telemetry_salt",
        ".env",
    }
    blocked_suffixes = (".key", ".pem", ".secret")
    base = os.path.basename(canonical)
    if base in blocked_basenames:
        return None
    if any(base.endswith(suffix) for suffix in blocked_suffixes):
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
    except ValidationError:
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_watch", outcome="denied", resources=raw_path
        )
        return web.json_response({"error": "invalid input"}, status=400)

    path = _validate_dashboard_path(raw_path)
    if not path:
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
        session_key="dashboard", tool_name="config_fs_watch", outcome="success",
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
    except ValidationError:
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="file_read",
            outcome="denied",
            resources=raw_path,
        )
        return web.json_response({"error": "invalid input"}, status=400)

    path = _validate_dashboard_path(raw_path)
    if not path:
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
            session_key="dashboard", tool_name="file_raw", outcome=outcome, resources=res,
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
    except ValidationError:
        _sel().log_tool_invocation(
            session_key="dashboard",
            tool_name="file_write",
            outcome="denied",
            resources=body.get("path", ""),
        )
        return web.json_response({"error": "invalid input"}, status=400)

    path = _validate_dashboard_path(body.get("path", ""))
    if not path:
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


def _git_repo_root(path: str) -> str | None:
    """Walk up from *path* to find the enclosing git repo root (has ``.git``)."""

    cur = path if os.path.isdir(path) else os.path.dirname(path)
    while cur and cur != os.path.dirname(cur):
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        cur = os.path.dirname(cur)
    return None


def _path_within_roots(path: str) -> bool:
    """True if *path* is inside one of the dashboard's allowed roots."""

    for _label, root in _dashboard_roots():
        if root and (path == root or path.startswith(root + os.sep)):
            return True
    return False


async def _git(args: list[str], cwd: str, timeout: float = 5.0) -> str:
    """Run a read-only ``git`` command (arg vector, never shell). Returns stdout
    text, or '' on error/timeout. Output is redacted before returning."""
    import asyncio  # noqa: F811

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            # Reap the killed child so it doesn't linger as a zombie. The Changes
            # panel polls git-status every few seconds while a worker runs, so on a
            # slow/large repo unreaped timeouts would pile up defunct git processes.
            try:
                await proc.wait()
            except ProcessLookupError:
                pass
            return ""
    except (OSError, ValueError):
        return ""
    text = out.decode("utf-8", "replace")
    text, _ = redact_credentials(text)
    text, _ = redact_exfiltration_urls(text)
    return text


async def api_file_git_status(request: web.Request) -> web.Response:
    """GET /api/file-git-status?path=... — git branch + per-file status.

    Returns ``{repoRoot, branch, statuses}`` where ``statuses`` maps absolute
    file paths to a porcelain status code (``M``, ``A``, ``D``, ``R``, ``??``).
    Empty repoRoot when *path* is not inside a git repo. The repo root must lie
    within the dashboard's allowed roots, so this can never inspect an arbitrary
    repo via a crafted path.
    """

    raw = request.query.get("path", "").strip()
    path = _validate_dashboard_path(raw)
    if not path:
        return web.json_response({"error": "invalid or forbidden path"}, status=400)
    repo = _git_repo_root(path)
    empty = {"repoRoot": "", "branch": "", "statuses": {}}
    if not repo or not _path_within_roots(repo):
        return web.json_response(empty)

    # ``symbolic-ref`` reports the real branch even on an UNBORN branch (a fresh
    # `git init` with no commits yet — the greenfield case), where
    # ``rev-parse --abbrev-ref HEAD`` just prints the literal "HEAD". Fall back to
    # rev-parse for a genuinely detached HEAD (no symbolic ref).
    branch = (await _git(["symbolic-ref", "--short", "HEAD"], repo)).strip()
    if not branch:
        branch = (await _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)).strip()
    porcelain = await _git(["status", "--porcelain", "-z"], repo)
    statuses: dict[str, str] = {}
    # ``-z`` separates entries with NUL; rename entries carry a second NUL-
    # separated path (the origin) which we skip.
    parts = porcelain.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        if not entry or len(entry) < 4:
            i += 1
            continue
        code = entry[:2].strip() or entry[:2]
        rel = entry[3:]
        statuses[os.path.join(repo, rel)] = code
        # A rename/copy ("R"/"C") consumes the next NUL-separated origin path.
        if entry[:1] in ("R", "C"):
            i += 2
        else:
            i += 1

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
    out = await _git(["log", f"-{limit}", f"--pretty=format:{fmt}"], repo)
    commits: list[dict] = []
    for line in out.splitlines():
        parts = line.split("\x00")
        if len(parts) == 4:
            commits.append({"hash": parts[0], "subject": parts[1], "relative": parts[2], "author": parts[3]})
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
        return web.json_response({"error": "invalid or forbidden path"}, status=400)
    h = request.query.get("hash", "").strip()
    # Hex-only (4-40 chars): a git short/full hash. Rejects flags + injection.
    if not re.fullmatch(r"[0-9a-fA-F]{4,40}", h):
        return web.json_response({"error": "invalid commit hash"}, status=400)
    repo = _git_repo_root(path)
    if not repo or not _path_within_roots(repo):
        return web.json_response({"repoRoot": "", "hash": h, "subject": "", "diff": "", "found": False})
    # Confirm the hash actually resolves to a commit in THIS repo first. A valid-hex
    # but unknown hash (stale ref after a force-push/rebase, or a commit from a
    # different repo the workspace was re-pointed away from) otherwise yields empty
    # subject+diff — which the cockpit would misreport as a legit "empty checkpoint"
    # rather than "this commit no longer exists here". rev-parse --verify --quiet
    # echoes the full sha when h is a real commit in this repo, nothing when it isn't.
    resolved = (await _git(["rev-parse", "--verify", "--quiet", f"{h}^{{commit}}"], repo)).strip()
    if not resolved:
        return web.json_response(
            {"repoRoot": repo, "hash": h, "subject": "", "diff": "", "found": False})
    subject = (await _git(["show", "-s", "--format=%s", h], repo)).strip()
    # ``--`` separates the rev from paths so a hash can never be read as a flag.
    diff = await _git(["show", "--no-color", "--stat", "--patch", h, "--"], repo, timeout=10.0)
    _sel().log_tool_invocation(
        session_key="dashboard", tool_name="git_commit", outcome="success", resources=repo
    )
    # Cap the payload, but SIGNAL when it was cut so the cockpit can say so — a silently
    # truncated patch reads as the whole commit (no-silent-caps).
    _CAP = 512 * 1024
    truncated = len(diff) > _CAP
    return web.json_response(
        {"repoRoot": repo, "hash": h, "subject": subject, "diff": diff[:_CAP], "truncated": truncated, "found": True})


async def api_file_git_original(request: web.Request) -> web.Response:
    """GET /api/file-git-original?path=... — the committed (HEAD) contents of a
    file, for a working-vs-HEAD diff view. Returns ``{content, exists}``; exists is
    False for a file not in HEAD (newly added — the diff is then against empty).
    Allowlist-gated like the rest; redacted on the way out."""
    raw = request.query.get("path", "").strip()
    path = _validate_dashboard_path(raw)
    if not path:
        return web.json_response({"error": "invalid or forbidden path"}, status=400)
    repo = _git_repo_root(path)
    if not repo or not _path_within_roots(repo):
        return web.json_response({"content": "", "exists": False})
    try:
        rel = os.path.relpath(path, repo)
    except ValueError:
        return web.json_response({"content": "", "exists": False})
    # git show HEAD:<rel> — non-zero exit (path not in HEAD) → newly added file.
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "show", f"HEAD:{rel}", cwd=repo,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except (FileNotFoundError, asyncio.TimeoutError):
        return web.json_response({"content": "", "exists": False})
    if proc.returncode != 0:
        return web.json_response({"content": "", "exists": False})
    raw = out or b""
    _CAP = 512 * 1024
    # Signal when the HEAD side was cut — else a large committed file's diff would
    # silently show the original truncated, reading as if the worker deleted the tail
    # (no-silent-caps; mirrors fileRead's X-Truncated + the commit-view truncated flag).
    truncated = len(raw) > _CAP
    text = raw[:_CAP].decode("utf-8", "replace")
    text, _ = redact_credentials(text)
    text, _ = redact_exfiltration_urls(text)
    _sel().log_tool_invocation(
        session_key="dashboard", tool_name="git_show", outcome="success", resources=repo
    )
    return web.json_response({"content": text, "exists": True, "truncated": truncated})


_CONTENT_SEARCH_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", ".next",
}
_CONTENT_SEARCH_MAX_RESULTS = 500
_CONTENT_SEARCH_TIMEOUT = 15.0
_RG_AVAILABLE: bool | None = None


def _has_rg() -> bool:
    """Whether ripgrep is on PATH (cached after first lookup)."""

    global _RG_AVAILABLE
    if _RG_AVAILABLE is None:
        _RG_AVAILABLE = shutil.which("rg") is not None
    return _RG_AVAILABLE


async def _content_search_rg(root: str, query: str, include: str) -> tuple[list[dict], bool]:
    """Content search via ripgrep --json. Returns (results, truncated)."""
    import asyncio  # noqa: F811
    import json as _json  # noqa: F811

    args = ["rg", "--json", "--max-count", "50", "-i", "--", query, root]
    for glob in [g.strip() for g in include.split(",") if g.strip()]:
        args[1:1] = ["-g", glob]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=_CONTENT_SEARCH_TIMEOUT)
    except (asyncio.TimeoutError, OSError, ValueError):
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
        if not path or _validate_dashboard_path(path) is None:
            continue
        text = (d.get("lines", {}) or {}).get("text", "")
        sub = d.get("submatches") or [{}]
        results.append({
            "file": path,
            "line": d.get("line_number", 0),
            "col": (sub[0].get("start", 0) + 1) if sub else 1,
            "preview": _redact_search_preview(text.rstrip("\n")[:300]),
        })
        if len(results) >= _CONTENT_SEARCH_MAX_RESULTS:
            return results, True
    return results, False


def _content_search_python(root: str, query: str, include: str) -> tuple[list[dict], bool]:
    """Pure-Python content search fallback (no ripgrep). Returns (results, truncated)."""
    import fnmatch

    globs = [g.strip() for g in include.split(",") if g.strip()]
    needle = query.lower()
    results: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _CONTENT_SEARCH_IGNORE_DIRS and not d.startswith(".")]
        for fn in filenames:
            if globs and not any(fnmatch.fnmatch(fn, g) for g in globs):
                continue
            fpath = os.path.join(dirpath, fn)
            if _validate_dashboard_path(fpath) is None:
                continue
            try:
                with open(fpath, encoding="utf-8", errors="ignore") as fh:
                    for n, line in enumerate(fh, 1):
                        col = line.lower().find(needle)
                        if col >= 0:
                            results.append({
                                "file": fpath, "line": n, "col": col + 1,
                                "preview": _redact_search_preview(line.rstrip("\n")[:300]),
                            })
                            if len(results) >= _CONTENT_SEARCH_MAX_RESULTS:
                                return results, True
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
    path = _validate_dashboard_path(raw)
    if not path or not os.path.isdir(path):
        return web.json_response({"error": "invalid or forbidden directory"}, status=400)
    q = request.query.get("q", "").strip()
    engine = "rg" if _has_rg() else "python"
    if not q:
        return web.json_response({"results": [], "engine": engine, "truncated": False})
    include = request.query.get("include", "")
    if _has_rg():
        results, truncated = await _content_search_rg(path, q, include)
    else:
        results, truncated = await asyncio.to_thread(_content_search_python, path, q, include)
    _sel().log_tool_invocation(
        session_key="dashboard", tool_name="file_content_search", outcome="success",
        resources=f"{path} q={q[:80]}",
    )
    return web.json_response({"results": results, "engine": engine, "truncated": truncated})


async def api_file_complete(request: web.Request) -> web.Response:
    """GET /api/file-complete?path=&kind=&limit= — path autocomplete for the PathBar.

    Given a (possibly partial) path, returns up to ``limit`` matching children of
    its parent directory. ``kind=dir`` restricts to directories. Every candidate
    is validated through the allowlist, so completion can never enumerate or
    escape outside the dashboard's roots.
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

    out: list[dict] = []
    try:
        with os.scandir(parent_ok) as it:
            for de in it:
                if len(out) >= limit:
                    break
                if prefix and not de.name.startswith(prefix):
                    continue
                try:
                    is_dir = de.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if kind == "dir" and not is_dir:
                    continue
                full = os.path.realpath(de.path)
                if _validate_dashboard_path(full) is None:
                    continue
                out.append({"name": de.name, "path": full, "is_dir": is_dir})
    except OSError:
        return web.json_response({"suggestions": []})

    out.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return web.json_response({"suggestions": out})


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
    name = str(body.get("name", "")).strip()
    kind = str(body.get("kind", "file")).strip().lower()
    content = body.get("content", "")
    if kind not in ("file", "dir"):
        return web.json_response({"error": "kind must be 'file' or 'dir'"}, status=400)
    # Reject path separators / traversal in the new name — it's a single segment.
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return web.json_response({"error": "invalid name"}, status=400)

    parent = _validate_dashboard_path(parent_raw)
    if not parent or not os.path.isdir(parent):
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_create", outcome="denied", resources=parent_raw
        )
        return web.json_response({"error": "invalid or forbidden parent directory"}, status=400)

    target = _validate_dashboard_path(os.path.join(parent, name))
    if not target:
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_create", outcome="denied",
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
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_move", outcome="denied",
            resources=f"{body.get('src', '')}→{body.get('dest', '')}",
        )
        return web.json_response({"error": "invalid or forbidden path"}, status=400)
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
            session_key="dashboard", tool_name="file_move", outcome="success",
            resources=f"{src}→{dest}",
        )
        return web.json_response({"ok": True, "path": dest})
    except Exception:
        logging.getLogger(__name__).exception("file_move failed %s → %s", src, dest)
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_move", outcome="failure",
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
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_delete", outcome="denied",
            resources=str(body.get("path", "")),
        )
        return web.json_response({"error": "invalid or forbidden path"}, status=400)
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
            if not filename or "/" in filename or "\\" in filename or filename in (".", ".."):
                return web.json_response({"error": "invalid filename in upload"}, status=400)
            dest = _validate_dashboard_path(os.path.join(target_dir, filename))
            if not dest:
                return web.json_response({"error": f"forbidden filename: {filename}"}, status=400)
            if os.path.exists(dest):
                return web.json_response({"error": f"already exists: {filename}"}, status=409)
            # Per-filetype cap from the shared policy (browser mime disambiguates media).
            part_mime = (part.headers or {}).get("Content-Type") or None
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
        return web.json_response({"error": str(exc)}, status=413 if "too large" in str(exc) else 400)
    except Exception:
        logging.getLogger(__name__).exception("file_upload failed into %s", target_dir)
        for p in saved:
            with contextlib.suppress(OSError):
                os.unlink(p)
        _sel().log_tool_invocation(
            session_key="dashboard", tool_name="file_upload", outcome="failure", resources=target_dir
        )
        return web.json_response({"error": "failed to upload"}, status=500)

    if not saved:
        return web.json_response({"error": "no file parts in upload"}, status=400)
    _sel().log_tool_invocation(
        session_key="dashboard", tool_name="file_upload", outcome="success",
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
            _sel().log_api_access(caller=caller, operation="file_search", outcome="denied", resources=project, error="sensitive path")
            return web.json_response({"error": "Access denied"}, status=403)
        if _is_system_root(project):
            _sel().log_api_access(caller=caller, operation="file_search", outcome="denied", resources=project, error="system root")
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
            _sel().log_api_access(caller=caller, operation="file_search", outcome="denied", resources=r, error="sensitive path")
        else:
            safe_roots.append(r)

    # Fast path: use in-memory index when available for a single scoped project
    state: DashboardState = request.app["state"]
    if scoped and len(safe_roots) == 1:
        idx = state.file_indexes.get(safe_roots[0])
        if idx and idx.is_ready and not idx.truncated:
            results = await asyncio.to_thread(idx.search, query, _fuzzy_score, max_results)
            trimmed = [{k: v for k, v in r.items() if k != "_score"} for r in results]
            _sel().log_api_access(caller=caller, operation="file_search", outcome="allowed", resources=f"q={query} indexed=true entries={idx.entry_count} results={len(trimmed)}")
            return web.json_response({"results": trimmed, "root": safe_roots[0]})

    # Fallback: walk filesystem per request
    # Dot-prefixed dirs (.personalclaw, .local, .config) excluded by startswith(".") guard below.
    # ``_ext`` is PClaw's per-cwd memory-partition store (one ephemeral clone per
    # session working dir, each with identical agent-internal memory files) — it
    # must never surface here or it floods the picker with duplicate matches.
    skip_dirs = {
        ".git", "node_modules", "__pycache__", ".cache", ".venv", "venv",
        "dist", "build", "env", "out", "target", "_ext",
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
                    d for d in dirnames
                    if not d.startswith(".") and d not in skip_dirs
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
                    results.append({"path": fpath, "name": fname, "size": st.st_size, "mtime": int(st.st_mtime), "_score": sc})
                if walked >= max_scan or len(results) >= max_collect:
                    break
        return results

    results = await asyncio.to_thread(_walk_file_search)

    # Sort by score descending, then shorter name, then recency
    now = time.time()
    results.sort(key=lambda r: (-r["_score"], len(r["name"]), now - r["mtime"]))

    # Strip internal scoring field before response
    trimmed = [{k: v for k, v in r.items() if k != "_score"} for r in results[:max_results]]

    _sel().log_api_access(caller=caller, operation="file_search", outcome="allowed", resources=f"q={query} roots={len(safe_roots)} results={len(trimmed)}")
    return web.json_response({
        "results": trimmed,
        "root": safe_roots[0] if scoped and safe_roots else "",
    })


async def api_browse_dirs(request: web.Request) -> web.Response:
    """GET /api/browse-dirs?path=... — list subdirectories for directory browser."""

    from personalclaw.security import is_sensitive_path  # noqa: F811

    caller = request.get("user", "dashboard")
    raw = request.query.get("path", "").strip()
    base = os.path.realpath(os.path.expanduser(raw)) if raw else os.path.realpath(os.path.expanduser("~"))
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
                {"error": "That path is a file, not a directory", "path": base}, status=404)
        return web.json_response(
            {"error": "Can't access that directory (permission denied)", "path": base}, status=400)
    if is_sensitive_path(base):
        _sel().log_api_access(caller=caller, operation="browse_dirs", outcome="denied", resources=base, error="sensitive path")
        return web.json_response({"error": "Access denied"}, status=403)
    # Block system roots — directory browser is for picking project/workspace
    # folders, not enumerating system internals.
    if _is_system_root(base):
        _sel().log_api_access(caller=caller, operation="browse_dirs", outcome="denied", resources=base, error="system root")
        return web.json_response({"error": "Access denied"}, status=403)
    skip = {".git", "node_modules", "__pycache__", ".cache", ".venv", "venv", "env", ".personalclaw"}
    dirs: list[dict] = []
    try:
        for entry in sorted(os.scandir(base), key=lambda e: e.name.lower()):
            if entry.is_dir(follow_symlinks=False) and entry.name not in skip and not entry.name.startswith("."):
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
    return web.json_response({"path": base, "parent": os.path.dirname(base), "dirs": dirs, "in_repo": in_repo})


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
        _sel().log_api_access(caller=caller, operation="create_dir", outcome="denied", resources=target, error="sensitive path")
        return web.json_response({"error": "Access denied"}, status=403)
    # Don't create a workspace/project folder under a system root. Use the FULL
    # is_system_path (the same bind-semantics the Code workspace validation uses):
    # it also blocks the bare mount/temp PARENTS (/, /Volumes, /var, /tmp) — you'd
    # never create a project folder AT those (a child like /Volumes/disk/proj is fine,
    # allowed by the parent rule), so this can't materialize a stray folder anywhere a
    # workspace bind would then reject.
    if is_system_path(target):
        _sel().log_api_access(caller=caller, operation="create_dir", outcome="denied", resources=target, error="system root")
        return web.json_response({"error": "Access denied"}, status=403)
    if os.path.exists(target):
        return web.json_response({"error": "Already exists", "path": target}, status=409)
    # Create exactly ONE new leaf folder inside an EXISTING parent — not a chain.
    # makedirs() would silently materialize every missing ancestor, so a name like
    # "foo/bar/baz" (or a mistyped parent) built a surprise nested tree and buried the
    # workspace at the deepest level. The picker always creates inside a dir it just
    # navigated to, so the parent legitimately exists; require it.
    parent = os.path.dirname(target)
    if not os.path.isdir(parent):
        return web.json_response(
            {"error": "Parent folder doesn't exist — create the folder inside an existing directory.",
             "path": target}, status=400)
    try:
        os.mkdir(target)
    except OSError as exc:
        return web.json_response({"error": str(exc)}, status=500)
    _sel().log_api_access(caller=caller, operation="create_dir", outcome="ok", resources=target)
    return web.json_response({"ok": True, "path": target})


# Widget ids the home dashboard knows about (mirrors web/pages/dashboard/layout.ts
# WidgetId). A persisted layout is filtered to these so a stale/forged id can't
# smuggle arbitrary data into config. Kept as a set for O(1) membership.
_DASHBOARD_WIDGET_IDS = {
    "hero", "action-center", "active-work", "ask", "suggestions",
    "tasks", "schedule", "knowledge", "memory", "system",
}


def _sanitize_dashboard_layout(raw: object) -> dict | None:
    """Validate + normalize a persisted dashboard layout. Returns the cleaned dict,
    an empty dict (reset-to-default), or None if the shape is invalid (→ 400).

    Shape: ``{"widgets": [{"id","x","y","w","h","hidden"?}], "v": 1}``. Numeric
    fields are coerced + clamped to the 12-col grid; unknown widget ids are dropped;
    an empty/absent widgets list is treated as reset."""
    if raw is None or raw == {}:
        return {}
    if not isinstance(raw, dict):
        return None
    widgets_in = raw.get("widgets")
    if not isinstance(widgets_in, list):
        return None
    seen: set[str] = set()
    widgets_out: list[dict] = []
    for w in widgets_in:
        if not isinstance(w, dict):
            return None
        wid = w.get("id")
        if wid not in _DASHBOARD_WIDGET_IDS or wid in seen:
            continue  # drop unknown / duplicate ids rather than failing the whole save
        seen.add(wid)
        try:
            x = max(0, min(11, int(w.get("x", 0))))
            y = max(0, min(200, int(w.get("y", 0))))
            width = max(1, min(12, int(w.get("w", 4))))
            height = max(1, min(12, int(w.get("h", 2))))
        except (TypeError, ValueError):
            return None
        widgets_out.append({
            "id": wid, "x": x, "y": y, "w": width, "h": height,
            "hidden": bool(w.get("hidden", False)),
        })
    if not widgets_out:
        return {}
    return {"widgets": widgets_out, "v": 1}


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
            "restore_sessions", "restore_window_minutes", "merge_queued_messages",
            "auto_tag_sessions",
            "widget_density", "user_name",
            # server-stored message display prefs (consistent across browsers)
            "send_on_enter", "show_timestamps", "show_thinking_inline",
            "simplified_tool_names", "confirm_close_session",
            # home dashboard widget layout (customization; per-user)
            "dashboard_layout",
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
        # message display prefs + auto-tagging — all booleans
        for _bool_field in (
            "send_on_enter", "show_timestamps", "show_thinking_inline",
            "simplified_tool_names", "confirm_close_session", "auto_tag_sessions",
        ):
            if _bool_field in body:
                val = body[_bool_field]
                if not isinstance(val, bool):
                    _sel().log_tool_invocation(
                        session_key="dashboard", tool_name="dashboard_config_write", outcome="failure"
                    )
                    return web.json_response({"error": f"{_bool_field} must be a boolean"}, status=400)
                setattr(cfg.dashboard, _bool_field, val)
        if "dashboard_layout" in body:
            layout = _sanitize_dashboard_layout(body["dashboard_layout"])
            if layout is None:
                _sel().log_tool_invocation(
                    session_key="dashboard", tool_name="dashboard_config_write", outcome="failure"
                )
                return web.json_response(
                    {"error": "dashboard_layout must be {widgets:[{id,x,y,w,h,hidden?}], v} or {} to reset"},
                    status=400,
                )
            cfg.dashboard.dashboard_layout = layout
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
            "send_on_enter": cfg.dashboard.send_on_enter,
            "show_timestamps": cfg.dashboard.show_timestamps,
            "show_thinking_inline": cfg.dashboard.show_thinking_inline,
            "simplified_tool_names": cfg.dashboard.simplified_tool_names,
            "confirm_close_session": cfg.dashboard.confirm_close_session,
            "dashboard_layout": cfg.dashboard.dashboard_layout or {},
        }
    )
