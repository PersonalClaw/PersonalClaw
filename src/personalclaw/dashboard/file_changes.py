"""The file-change chips a chat turn shows: each file a write tool changed, before and after.

A backend that declares its edits hands the chip whole (``_capture_declared_file_change``); for the
native agent's write tools it is inferred from the call (``_capture_file_change``). Either way it
accumulates on the session and, once the turn ends, is deduplicated per path, redacted and attached
to the turn's answer (``_flush_file_changes``), so the chips render and survive a reload. Moved out
of ``chat_runner`` whole (#3905).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from personalclaw.dashboard.state import _ChatSession, tool_input_to_str
from personalclaw.security import is_sensitive_path, redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)

# File-change chips: the native default agent's write tools.
_WRITE_FILE_TOOLS = {"write_file", "edit_file"}
# Per-file snapshot cap (chars). Diffs of huge files are not useful inline and
# would bloat persisted meta; truncate with a marker.
_MAX_FILE_SNAPSHOT = 200_000


def _file_change_base(session: _ChatSession) -> Path:
    """The workspace base a write tool resolves paths against — mirror
    NativeBuiltinToolProvider._resolve (cwd = session.workspace_dir or root)."""
    from personalclaw.config.loader import workspace_root

    return Path(session.workspace_dir).resolve() if session.workspace_dir else workspace_root()


def _truncate_snapshot(text: str) -> str:
    if len(text) > _MAX_FILE_SNAPSHOT:
        return text[:_MAX_FILE_SNAPSHOT] + "\n… [truncated]"
    return text


def _capture_declared_file_change(session: _ChatSession, change: dict[str, str] | None) -> None:
    """File-change chip for a backend that DECLARED the edit.

    The native path below infers the chip: a write-tool NAME set, a workspace path
    resolution and a disk read, because it has to reconstruct ``after`` from the call's
    arguments. An ACP ``diff`` content block states path, old text and new text outright,
    so none of that inference applies — and none of it would transfer anyway, since an
    ACP CLI's edit tool is named and shaped however its vendor chose.

    Two guards are kept identical to the native path on purpose: a no-op edit files no
    chip, and both snapshots are capped. Sensitive-path skipping is NOT replicated —
    ``_flush_file_changes`` redacts every field it attaches, and refusing the chip here
    would drop the only record that the agent touched the file at all.

    LAST declaration wins, on BOTH sides — the one place this must NOT mirror the native
    path. ``_flush_file_changes`` keeps the earliest ``before`` and the latest ``after``,
    which is right for real disk snapshots and wrong for a streaming adapter that
    re-declares the same edit as its arguments fill in. Measured live on claude-code
    (2026-08-24): an early frame declared the replaced FRAGMENT as ``oldText`` and a later
    one the whole file, so the merge produced a chip whose "before" was one line and whose
    "after" was the entire file — a diff asserting the file used to contain only that
    line. A declared change carries whole-file text by contract, so the newest
    declaration is the most complete one.
    """
    if not change:
        return
    path = str(change.get("path") or "")
    if not path:
        return
    before, after = str(change.get("before") or ""), str(change.get("after") or "")
    if before == after:
        return  # no-op edit → no chip, same as the native path
    entry = {
        "path": path,
        "before": _truncate_snapshot(before),
        "after": _truncate_snapshot(after),
    }
    seen = getattr(session, "_declared_file_change_idx", None)
    if seen is None:
        seen = {}
        session._declared_file_change_idx = seen
    prior = seen.get(path)
    if prior is not None and prior < len(session._file_changes):
        session._file_changes[prior] = entry
        return
    seen[path] = len(session._file_changes)
    session._file_changes.append(entry)


def _capture_file_change(session: _ChatSession, tool_name: str, tool_input: object) -> None:
    """On a native write_file/edit_file CALL, snapshot before+after for the chip.

    Robust by construction: ``before`` is read off disk (empty for a new file);
    ``after`` is computed in-memory from the call args (write_file → ``content``;
    edit_file → before with the first ``old_str`` replaced), so no racy turn-end
    re-read is needed. Never raises — a snapshot failure must not break the turn.
    Sensitive paths are skipped (don't surface secrets in a diff chip).
    """
    if tool_name not in _WRITE_FILE_TOOLS:
        return
    try:
        args = (
            tool_input
            if isinstance(tool_input, dict)
            else json.loads(tool_input_to_str(tool_input))
        )
        if not isinstance(args, dict):
            return
        rel = str(args.get("path") or "")
        if not rel:
            return
        base = _file_change_base(session)
        target = (base / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
        # Confinement: only snapshot inside the workspace (matches the tool's gate).
        if base != target and base not in target.parents:
            return
        if is_sensitive_path(str(target)):
            return
        before = ""
        if target.is_file():
            try:
                before = target.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return  # binary/unreadable → skip silently
        if tool_name == "write_file":
            if args.get("content") is None:
                return  # a call with no text writes nothing (`_t_write_file` refuses it)
            after = str(args["content"])
        else:  # edit_file: mirror the tool impl (1 replacement, or all when replace_all)
            old, new = str(args.get("old_str", "")), str(args.get("new_str", ""))
            n = -1 if args.get("replace_all") else 1
            after = before.replace(old, new, n) if old and old in before else before
        if before == after:
            return  # no-op write → no chip
        session._file_changes.append(
            {
                "path": rel,
                "before": _truncate_snapshot(before),
                "after": _truncate_snapshot(after),
            }
        )
    except Exception:
        logger.debug("file-change snapshot skipped for %s", tool_name, exc_info=True)


def _flush_file_changes(session: _ChatSession) -> None:
    """Attach accumulated file changes to the last assistant message's meta so the
    chips render + survive reload. Dedups per path (first ``before`` / last
    ``after``), redacts every field, then clears the accumulator."""
    changes = session._file_changes
    session._file_changes = []
    # The declared-change index points into the list just emptied. Clearing both
    # together is what keeps a later turn from writing over slot 0 of a fresh list.
    session._declared_file_change_idx = {}
    if not changes:
        return
    # Dedup by path: keep the earliest before and the latest after.
    merged: dict[str, dict[str, str]] = {}
    for c in changes:
        p = c["path"]
        if p in merged:
            merged[p]["after"] = c["after"]
        else:
            merged[p] = dict(c)
    out: list[dict[str, str]] = []
    for c in merged.values():
        path, _ = redact_credentials(redact_exfiltration_urls(c["path"])[0])
        before, _ = redact_credentials(redact_exfiltration_urls(c["before"])[0])
        after, _ = redact_credentials(redact_exfiltration_urls(c["after"])[0])
        out.append({"path": path, "before": before, "after": after})
    # Attach to the most recent assistant message (the turn's final answer).
    for m in reversed(session.messages):
        if m.get("role") == "assistant":
            m.setdefault("meta", {})["file_changes"] = out
            break
