"""Inbound request audit.

Every inbound request writes one JSONL line. This is deliberately its own log
rather than a subsection of the security event log: the SEL is the
security-relevant record (an auth failure, a cap breach), while this is the
complete request trace, including the boring successful reads — which is what you
actually need to answer "what did that client do yesterday?".

Refusals write to BOTH: here for the trace, and to the SEL because a rejected
credential on a network surface is a security event.

A refusal answered before any credential admitted its caller (a surface that's off, a peer the
surface refuses, no token or a wrong one, another machine the origin check turns away) comes from
a caller nobody has identified, so nothing else bounds how fast it can be sent. Its rows are held
to :data:`REFUSED_ROWS_PER_WINDOW` per caller in a window, and the window's further refusals are
counted into one summary row in each log (:class:`_RefusedRows`). The request is refused all the
same.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from personalclaw import bounded_log

logger = logging.getLogger(__name__)

_FILE = "inbound_audit.jsonl"
# Trim at 2x so the file is rewritten rarely (mirrors the notifications trim
# mechanic: cheap amortized cost, bounded size).
_MAX_LINES = 5_000

#: How long one caller's refusals before sign-in are counted as one window.
REFUSED_ROWS_WINDOW_SECS = 60.0
#: The rows such a caller writes in a window; its further refusals in it are only counted.
REFUSED_ROWS_PER_WINDOW = 10
#: Callers with a window open at once. Further callers share one window per surface.
_REFUSED_MAX_OPEN = 64
_FURTHER_CALLERS = "further callers"
#: How often, at most, a row looks for the windows that have closed.
_REFUSED_SWEEP_SECS = 1.0


def _audit_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _FILE


def audit(
    surface: str,
    *,
    route: str,
    status: int,
    bytes_in: int = 0,
    bytes_out: int = 0,
    duration_ms: int = 0,
    refused: str = "",
    tool: str = "",
    client_id: str = "",
    rate_limited: bool = False,
    unknown_caller: Any = None,
) -> None:
    """Append one audit line. Never raises — auditing must not break a response.

    A failure to record is itself logged at debug: losing an audit line is bad,
    but failing the user's request because we couldn't write a log line is worse.

    ``client_id`` is the identity that Settings → External Access renders per-client
    last-seen and request counts FROM — derived from this file rather than collected
    into a second counter, so the number beside a client and the trail behind it
    cannot disagree.

    ``unknown_caller`` is the request, for a refusal answered before any credential admitted
    its caller. Its rows are then held to a rate per caller, the transport's peer
    (:class:`_RefusedRows`); past it, this refusal is counted and not written.
    """
    try:
        path: Path | None = _audit_path()
    except Exception:  # noqa: BLE001
        logger.debug("inbound: audit path unknown", exc_info=True)
        path = None
    if path is not None and not _writes(path, surface, route, status, refused, unknown_caller):
        return
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "surface": surface,
        "client_id": client_id,
        "route": route,
        "status": status,
        "bytes_in": bytes_in,
        "bytes_out": bytes_out,
        "duration_ms": duration_ms,
        "rate_limited": bool(rate_limited),
    }
    if tool:
        row["tool"] = tool
    if refused:
        row["refused_reason"] = refused
    if path is not None:
        _append(path, row)

    if refused:
        _sel_refusal(surface, route, refused, client_id)


def _append(path: Path, row: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        _maybe_trim(path)
    except Exception:  # noqa: BLE001
        logger.debug("inbound: audit write failed", exc_info=True)


def _maybe_trim(path: Path) -> None:
    try:
        if path.stat().st_size < 512_000:  # cheap guard before counting lines
            return
        bounded_log.trim_jsonl(path, _MAX_LINES, at="ts")
    except Exception:  # noqa: BLE001
        logger.debug("inbound: audit trim failed", exc_info=True)


def _sel_refusal(
    surface: str, route: str, reason: str, client_id: str = "", metadata: dict | None = None
) -> None:
    """Mirror a refusal into the security event log.

    The caller names the CLIENT when one was resolved. A refusal attributed only to
    `inbound:mcp` cannot answer "which integration is failing auth?", which is the
    first question anyone asks of this log.
    """
    try:
        from personalclaw.sel import sel

        caller = f"inbound:{surface}:{client_id}" if client_id else f"inbound:{surface}"
        sel().log_api_access(
            caller=caller,
            operation=route,
            outcome="denied",
            source="inbound",
            resources=reason[:200],
            metadata=metadata,
        )
    except Exception:  # noqa: BLE001
        logger.debug("inbound: SEL refusal log failed", exc_info=True)


def _writes(path: Path, surface: str, route: str, status: int, refused: str, request: Any) -> bool:
    """Whether this row is written: always, unless it's a refusal of a caller still unknown past
    its caller's allowance. A throttle that fails writes the row."""
    try:
        if request is None or not refused:
            _REFUSED_ROWS.sweep(path)
            return True
        from personalclaw.inbound import auth

        return _REFUSED_ROWS.admit(
            path, surface, auth._peer_host(request), route=route, status=status, refused=refused
        )
    except Exception:  # noqa: BLE001
        logger.debug("inbound: refusal throttle failed", exc_info=True)
        return True


class _RefusedRows:
    """The rows of refusals answered before any credential admitted the caller, held to a rate.

    Each caller, keyed on the transport's peer and never a header, writes its first
    :data:`REFUSED_ROWS_PER_WINDOW` refusals in a window as rows, as they happen. Its further
    refusals in that window are counted, and when the window closes (the next row after it,
    of any request) or the gateway stops (:func:`flush_refused_rows`), one summary row in the
    audit file and one in the Security log say how many there were, with the last one's route,
    status and reason. Every refusal is accounted for.

    Past :data:`_REFUSED_MAX_OPEN` callers at once, further callers share one window per
    surface, so a caller with many addresses can't grow this. Windows are kept for one audit
    file: when the home changes, the old one's counts go to its own file, and its Security log
    is no longer the one written.
    """

    def __init__(
        self,
        window_secs: float = REFUSED_ROWS_WINDOW_SECS,
        per_window: int = REFUSED_ROWS_PER_WINDOW,
        max_open: int = _REFUSED_MAX_OPEN,
    ) -> None:
        self._window = window_secs
        self._per_window = per_window
        self._max_open = max_open
        self._open: dict[tuple[str, str], dict[str, Any]] = {}
        self._path: Path | None = None
        self._last_sweep = 0.0
        self._lock = threading.Lock()

    def admit(
        self, path: Path, surface: str, peer: str, *, route: str, status: int, refused: str
    ) -> bool:
        """True when this refusal is written as its rows; else it is counted into its window."""
        now = time.monotonic()
        with self._lock:
            due = self._take_due(path, now)
            key = (surface, peer or "unknown")
            if key not in self._open and len(self._open) >= self._max_open:
                key = (surface, _FURTHER_CALLERS)
            entry = self._open.get(key)
            if entry is not None and now - entry["since"] >= self._window:
                due.append((path, key, entry, True))
                entry = None
            if entry is None:
                entry = self._open[key] = {"since": now, "written": 0, "more": 0}
            written = entry["written"] < self._per_window
            if written:
                entry["written"] += 1
            else:
                entry.update(more=entry["more"] + 1, route=route, status=status, reason=refused)
        self._summarize(due)
        return written

    def sweep(self, path: Path) -> None:
        """Summarize the windows that have closed (a row of any request calls this)."""
        with self._lock:
            due = self._take_due(path, time.monotonic())
        self._summarize(due)

    def flush(self) -> None:
        """Summarize every open window now (gateway stop, and tests)."""
        with self._lock:
            path, pending = self._path, list(self._open.items())
            self._open.clear()
        if path is not None:
            self._summarize([(path, key, entry, True) for key, entry in pending])

    def _take_due(self, path: Path, now: float) -> list[tuple[Path, tuple[str, str], dict, bool]]:
        """Pop the windows to summarize: all of them when the audit file has changed (their
        Security log is no longer this one), else those that have closed, at most once per
        sweep interval or whenever every window is taken. Caller holds the lock."""
        if path != self._path:
            old, pending = self._path, list(self._open.items())
            self._open.clear()
            self._path, self._last_sweep = path, now
            return [(old, k, e, False) for k, e in pending] if old is not None else []
        if now - self._last_sweep < _REFUSED_SWEEP_SECS and len(self._open) < self._max_open:
            return []
        self._last_sweep = now
        closed = [(k, e) for k, e in self._open.items() if now - e["since"] >= self._window]
        for key, _entry in closed:
            del self._open[key]
        return [(path, k, e, True) for k, e in closed]

    def _summarize(self, due: list[tuple[Path, tuple[str, str], dict, bool]]) -> None:
        for path, (surface, peer), entry, to_sel in due:
            more = int(entry["more"])
            if more <= 0:
                continue  # every refusal of the window is already its own row
            who = "further callers" if peer == _FURTHER_CALLERS else "the same caller"
            reason = (
                f"{more} more refusal(s) of {who} within {int(self._window)} s, counted and not "
                f"written one by one; the last: {entry['reason']}"
            )[:200]
            _append(
                path,
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "surface": surface,
                    "client_id": "",
                    "route": entry["route"],
                    "status": entry["status"],
                    "bytes_in": 0,
                    "bytes_out": 0,
                    "duration_ms": 0,
                    "rate_limited": False,
                    "refused_reason": reason,
                    "suppressed": more,
                },
            )
            if to_sel:
                _sel_refusal(
                    surface,
                    entry["route"],
                    reason,
                    metadata={
                        "summary": True,
                        "suppressed": more,
                        "window_secs": int(self._window),
                    },
                )


_REFUSED_ROWS = _RefusedRows()


def flush_refused_rows() -> None:
    """Write every open window's count of refusals now; the gateway calls this on stop."""
    _REFUSED_ROWS.flush()


def recent(limit: int = 100) -> list[dict]:
    """The most recent audit rows, newest first (operator surfacing)."""
    try:
        lines = _audit_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except (FileNotFoundError, OSError):
        return []
    out: list[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out
