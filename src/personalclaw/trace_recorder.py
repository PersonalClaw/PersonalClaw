"""Event-trace recorder — dev-only NDJSON capture of live event streams.

Part of the Self-Verification event-trace replay substrate. This is the ONE piece that
lives in core (the harness that *consumes* traces is repo-inner dev infra and can't be
imported by core). It is a thin, opt-in tap at existing event chokepoints:

- **off by default, zero overhead:** recording is enabled only when ``PERSONALCLAW_TRACE_DIR``
  is set in the environment. When unset, :func:`record` returns immediately after a single
  cached-None check — no file I/O, no formatting, no import cost on the hot path.
- **redacted at write time:** every payload passes through ``security.redact`` (credentials
  + exfiltration URLs) because traces get checked into ``harness/traces/`` as fixtures.
- **one NDJSON line per event:** ``{ts, stream, key, seq?, type, payload}``. Files are
  ``<trace_dir>/<stream>-<key>.ndjson`` (one per stream+key), append-only.

This module deliberately has NO dependency on the harness and imports only stdlib +
``personalclaw.security`` — so wrapping a chokepoint with a ``record(...)`` call adds a
dev-only capability without coupling core to dev tooling.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from personalclaw.security import redact

# Cached resolution of PERSONALCLAW_TRACE_DIR: None (unset) after the first check means the
# recorder is a no-op. A sentinel distinguishes "not yet checked" from "checked, disabled".
_UNCHECKED = object()
_trace_dir: Any = _UNCHECKED
_lock = threading.Lock()


def _resolve_dir() -> Path | None:
    """Resolve the trace dir once from the env. ``None`` means recording is disabled."""
    global _trace_dir
    if _trace_dir is _UNCHECKED:
        raw = os.environ.get("PERSONALCLAW_TRACE_DIR", "").strip()
        _trace_dir = Path(raw) if raw else None
        if isinstance(_trace_dir, Path):
            try:
                _trace_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                _trace_dir = None
    return _trace_dir if isinstance(_trace_dir, Path) else None


def is_recording() -> bool:
    """True if trace recording is enabled (``PERSONALCLAW_TRACE_DIR`` set + writable)."""
    return _resolve_dir() is not None


def reset_for_test() -> None:
    """Re-read the env on the next call (tests toggle ``PERSONALCLAW_TRACE_DIR``)."""
    global _trace_dir
    with _lock:
        _trace_dir = _UNCHECKED


def _safe_payload(payload: Any) -> Any:
    """Redact string leaves of the payload for at-rest safety. Recurses dict/list; leaves
    non-str scalars untouched. Falls back to a redacted repr for anything non-JSONable."""
    if isinstance(payload, str):
        return redact(payload)
    if isinstance(payload, dict):
        return {k: _safe_payload(v) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_safe_payload(v) for v in payload]
    if isinstance(payload, (int, float, bool)) or payload is None:
        return payload
    return redact(repr(payload))


def _slug(text: str) -> str:
    """Filesystem-safe slug for the stream/key filename component."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in text) or "none"


def record(
    stream: str,
    key: str,
    event_type: str,
    payload: Any,
    *,
    seq: int | None = None,
) -> None:
    """Append one event to the trace for ``stream``/``key``. No-op unless recording.

    ``stream`` is the source class (``ws``/``sse``/``inbox``/``mcp``/``journal``); ``key``
    scopes within it (e.g. the SSE registry key ``loop:<id>``, or a session id). ``seq`` is
    the source sequence number when one exists (used by replay's dedup key). Never raises
    on the hot path — a recording failure must not affect production behavior.
    """
    d = _resolve_dir()
    if d is None:
        return
    try:
        entry: dict[str, Any] = {
            "ts": time.time(),
            "stream": stream,
            "key": key,
            "type": event_type,
            "payload": _safe_payload(payload),
        }
        if seq is not None:
            entry["seq"] = seq
        path = d / f"{_slug(stream)}-{_slug(key)}.ndjson"
        line = json.dumps(entry, ensure_ascii=False, default=repr)
        with _lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except (OSError, TypeError, ValueError):
        # Dev tooling must never break the thing it observes.
        return
