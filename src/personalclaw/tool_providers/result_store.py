"""Per-session raw tool-result store (OP2) — the retrieval half of projection.

Projection (:mod:`projection`) shows the model a type-aware *preview* of a large
tool result; this store retains the **full raw output** so the agent can pull
back the part the preview dropped via the ``tool_result_get`` builtin tool.
Projection *defers*, this is where the deferred bytes live.

Backed by a bounded directory under the session workspace
(``~/.personalclaw/sessions/{session}/tool_results/``), one JSON file per result
keyed by a short ``result_id`` (``r_xxxxxxxx``). Bounded by a per-session file
cap (oldest evicted) so a long session can't grow it without limit; GC'd with the
session dir. Reuses the session-workspace substrate rather than a new persistence
layer (plan §2.3 / open-decision #1 — sibling, not the slug-based Artifacts store
which is for user-facing content).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.session_workspace import workspace_dir

logger = logging.getLogger(__name__)

_DIRNAME = "tool_results"
_MAX_PER_SESSION = 200      # oldest evicted beyond this (bounded growth)
_ID_PREFIX = "r_"


def _store_dir(session_id: str) -> Path:
    d = workspace_dir(session_id) / _DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _next_id(store: Path) -> str:
    """A short monotonic-ish id unique within the session store.

    ``Math.random``/``time`` are fine here (server runtime, not a workflow
    script). Sequence by existing-file count + a low-entropy suffix so ids stay
    short + human-quotable in a preview ("…full result: tool_result_get r_004a").
    """
    n = sum(1 for _ in store.glob(f"{_ID_PREFIX}*.json"))
    # nanosecond tail keeps it unique even if two land in the same count window
    suffix = f"{int(time.time_ns()) & 0xFFFF:04x}"
    return f"{_ID_PREFIX}{n:03d}{suffix}"


def store_result(session_id: str, raw: str, *, content_type: str = "generic",
                 tool: str = "") -> str:
    """Persist a raw tool output; return its ``result_id``.

    Evicts the oldest entries beyond :data:`_MAX_PER_SESSION` so the store stays
    bounded. Returns the id the projection preview cites + ``tool_result_get``
    reads. Never raises into the caller (a store failure must not break the tool
    call — returns ``""`` and the result simply isn't retrievable)."""
    try:
        store = _store_dir(session_id)
        rid = _next_id(store)
        payload = {
            "id": rid, "tool": tool, "content_type": content_type,
            "length": len(raw), "created": time.time_ns(), "raw": raw,
        }
        atomic_write(store / f"{rid}.json", json.dumps(payload, ensure_ascii=False))
        _evict_overflow(store)
        return rid
    except Exception:
        logger.debug("tool-result store write failed", exc_info=True)
        return ""


def _evict_overflow(store: Path) -> None:
    files = sorted(store.glob(f"{_ID_PREFIX}*.json"), key=lambda p: p.stat().st_mtime)
    for stale in files[:-_MAX_PER_SESSION] if len(files) > _MAX_PER_SESSION else []:
        stale.unlink(missing_ok=True)


def purge_session(session_id: str) -> bool:
    """Delete a session's ENTIRE on-disk workspace dir — the tool_results raw store
    and any other per-session state under ``sessions/{session_id}/``. Called on a
    hard-delete of a chat so a "deleted" conversation's retained tool outputs (which
    can hold file contents / command output) don't survive on disk. Best-effort;
    returns True if a dir was removed."""
    import shutil
    try:
        d = workspace_dir(session_id)
    except Exception:
        return False
    try:
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            return True
    except Exception:
        logger.debug("tool-result store purge failed for %s", session_id, exc_info=True)
    return False


def get_result(session_id: str, result_id: str) -> dict | None:
    """Load a stored result by id, or None if absent/unreadable."""
    if not result_id or "/" in result_id or ".." in result_id:
        return None
    p = _store_dir(session_id) / f"{result_id}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def fetch_slice(session_id: str, result_id: str, *, start: int = 0, end: int | None = None,
                grep: str | None = None, max_chars: int = 8000) -> dict:
    """Pull a slice or grep of a stored raw result — backs ``tool_result_get``.

    Returns ``{ok, error?, content, length, shown, mode}``. ``grep`` returns the
    matching lines (with a bound); else a ``[start:end]`` char range. The whole
    point is recovering the *specific* dropped slice, not re-dumping the blob.
    """
    rec = get_result(session_id, result_id)
    if rec is None:
        return {"ok": False, "error": f"no stored result {result_id!r} (it may have expired)",
                "content": "", "length": 0, "shown": 0, "mode": "none"}
    raw = rec.get("raw", "")
    ctype = rec.get("content_type", "generic")
    total = len(raw)
    if grep:
        import re
        try:
            pat = re.compile(grep, re.I)
        except re.error as exc:
            return {"ok": False, "error": f"bad grep pattern: {exc}", "content": "",
                    "length": total, "shown": 0, "mode": "grep", "content_type": ctype}
        hits = [ln for ln in raw.splitlines() if pat.search(ln)]
        body = "\n".join(hits)
        truncated = len(body) > max_chars
        return {"ok": True, "content": body[:max_chars], "length": total,
                "shown": min(len(body), max_chars), "matches": len(hits),
                "truncated": truncated, "mode": "grep", "content_type": ctype}
    # char range. A start past the end is a caller error, not an empty success —
    # surface it so the agent doesn't read "" as "the result is empty".
    if start and start >= total:
        return {"ok": False, "error": f"start={start} is past the result length ({total})",
                "content": "", "length": total, "shown": 0, "mode": "range", "content_type": ctype}
    s = max(0, start)
    e = total if end is None else min(end, total)
    window = raw[s:e]
    truncated = len(window) > max_chars
    return {"ok": True, "content": window[:max_chars], "length": total,
            "shown": min(len(window), max_chars), "start": s,
            "next_index": (s + max_chars) if truncated else None,
            "truncated": truncated, "mode": "range", "content_type": ctype}
