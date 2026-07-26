"""Structured crash capture (PLATFORM-RESILIENCE §6.5, grok-build learning).

An unhandled failure at a well-known boundary (gateway lifecycle, a chat turn, a
loop worker) writes ONE structured, recoverable artifact —
``~/.personalclaw/crashes/<ts>-<kind>.json`` — instead of scattering a stack trace
across a log nobody reads. The file is redacted before write (no credentials), the
directory is capped, and the Doctor surfaces recent crashes as a card.

Not telemetry: crash files never leave the machine — no upload, no aggregation.
They exist for the user and for the agent-run Doctor diagnosis only.
"""

from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path
from typing import Any, Literal, Optional

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir
from personalclaw.security import redact

logger = logging.getLogger(__name__)

CrashKind = Literal["gateway", "turn", "loop_worker"]

_DIR_NAME = "crashes"
_MAX_FILES = 20  # oldest pruned beyond this


def _crashes_dir() -> Path:
    return config_dir() / _DIR_NAME


def _clip(text: str, limit: int = 2000) -> str:
    """Redact then clip a free-text field for a crash artifact."""
    try:
        red = redact(str(text))
    except Exception:
        red = str(text)
    return red if len(red) <= limit else red[:limit] + "…"


def record_crash(
    kind: CrashKind,
    exc: BaseException,
    *,
    session_key: str = "",
    last_turns: Optional[list[str]] = None,
    in_flight_tool: Optional[dict[str, Any]] = None,
    active_model: str = "",
    uptime_secs: float = 0.0,
    now: float,
) -> Optional[Path]:
    """Write one crash artifact. Best-effort — a failure to record a crash must never
    itself raise (that would mask the original failure). Returns the path written, or
    ``None`` on any error.

    ``last_turns`` are content digests (not full text); everything is redacted.
    ``now`` is passed in (the scripting sandbox forbids wall-clock in some contexts;
    callers stamp it).
    """
    try:
        import personalclaw

        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        payload: dict[str, Any] = {
            "ts": now,
            "kind": kind,
            "exception": {
                "type": type(exc).__name__,
                "message": _clip(str(exc), 500),
                "traceback": _clip(tb, 6000),
            },
            "session_key": _clip(session_key, 200),
            "last_turns": [_clip(t, 300) for t in (last_turns or [])[-5:]],
            "in_flight_tool": _redact_tool(in_flight_tool),
            "active_model": _clip(active_model, 200),
            "version": getattr(personalclaw, "__version__", "unknown"),
            "uptime_secs": round(float(uptime_secs), 1),
        }
        d = _crashes_dir()
        d.mkdir(parents=True, exist_ok=True)
        # <ts>-<kind>.json — ts as an int for a sortable, filesystem-safe name.
        path = d / f"{int(now)}-{kind}.json"
        atomic_write(path, json.dumps(payload, indent=2), mode=0o600)
        _prune(d)
        return path
    except Exception:
        logger.debug("record_crash failed (kind=%s)", kind, exc_info=True)
        return None


def _redact_tool(tool: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not isinstance(tool, dict):
        return None
    return {
        "name": _clip(str(tool.get("name", "")), 100),
        "args_clipped": _clip(str(tool.get("args", "")), 300),
    }


def _prune(d: Path) -> None:
    """Keep only the most recent ``_MAX_FILES`` crash files (oldest dropped)."""
    try:
        files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in files[_MAX_FILES:]:
            try:
                stale.unlink()
            except OSError:
                pass
    except Exception:
        logger.debug("crash prune failed", exc_info=True)


def recent_crashes(limit: int = 10) -> list[dict[str, Any]]:
    """Read recent crash artifacts newest-first (a summary per file). Read-only and
    exception-safe — a corrupt file is skipped."""
    d = _crashes_dir()
    if not d.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return []
    for path in files[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        exc = data.get("exception", {}) if isinstance(data, dict) else {}
        out.append(
            {
                "file": path.name,
                "ts": data.get("ts"),
                "kind": data.get("kind", ""),
                "session_key": data.get("session_key", ""),
                "exception_type": exc.get("type", ""),
                "message": exc.get("message", ""),
            }
        )
    return out


def read_crash(filename: str) -> Optional[dict[str, Any]]:
    """Read one crash file's full JSON by name (path-traversal-guarded). ``None`` if
    absent/corrupt/escaping the crashes dir."""
    d = _crashes_dir()
    # Guard: only a bare filename inside the crashes dir.
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    path = d / filename
    try:
        if path.parent != d or not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def crash_count() -> int:
    """How many crash artifacts are on disk (the Doctor probe's signal)."""
    d = _crashes_dir()
    if not d.exists():
        return 0
    try:
        return sum(1 for _ in d.glob("*.json"))
    except Exception:
        return 0
