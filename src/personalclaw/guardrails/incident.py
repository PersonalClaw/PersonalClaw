"""Incident kill switch.

One flag — ``~/.personalclaw/incident.json`` (``{active, reason, started_at}``) —
suspends ALL unattended work within one poll interval. There is no unified trigger
store to flip (six independent stores), so incident mode does NOT mutate stores; it
is checked at the execution seams (cron due-collection, hook fire, event-trigger
fire, the idle runtime that fires loop cycles and idle triggers, the loop watchdog,
memory consolidation and background compression, inbox AI, non-interactive subagent
spawn). Each seam gains one ``if incident_active(): skip``, and nothing is lost by
it: the skipped work is still due when the switch is turned off.

**Interactive chat is untouched** — the user talking to their assistant during an
incident is the point. Resume is EXPLICIT (``POST /api/incident/resume {confirm}``
or ``personalclaw incident off``); activation/resume are SEL-audited.

The in-process mirror is refreshed from the file's mtime (the existing mtime-sync
habit), so a flag flipped by the CLI in another process is picked up by the running
gateway without a restart. :func:`watch` is how the gateway tells its open pages that
the switch moved, so no page has to poll it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

_INCIDENT_FILENAME = "incident.json"

#: How often :func:`watch` looks at the flag. One ``stat`` of one small file, so it is cheap to
#: look often, and this is what bounds how late an open page follows ``personalclaw incident``.
WATCH_INTERVAL_SECS = 2.0


@dataclass(frozen=True)
class IncidentState:
    active: bool = False
    reason: str = ""
    started_at: str = ""


def _incident_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _INCIDENT_FILENAME


# In-process mirror + the mtime it was loaded at, so a flag flipped by another
# process (the CLI) is picked up without a restart.
_mirror: IncidentState | None = None
_mirror_mtime: float = -1.0


def _read_file() -> IncidentState:
    path = _incident_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return IncidentState()
        return IncidentState(
            active=bool(data.get("active", False)),
            reason=str(data.get("reason", "")),
            started_at=str(data.get("started_at", "")),
        )
    except (OSError, ValueError):
        return IncidentState()


def get_incident() -> IncidentState:
    """Current incident state, refreshing the mirror from the file's mtime.

    Fail-safe on error is NOT applied here (incident is opt-IN, not a guard flag):
    an unreadable/missing file means NO incident — the normal, overwhelmingly common
    state. Flipping every seam off on a transient read error would be the wrong
    failure mode for a kill switch (it would halt all automation on a hiccup)."""
    global _mirror, _mirror_mtime
    path = _incident_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        # No file → no incident. Reset the mirror so a resumed state is seen.
        _mirror = IncidentState()
        _mirror_mtime = -1.0
        return _mirror
    if _mirror is None or mtime != _mirror_mtime:
        _mirror = _read_file()
        _mirror_mtime = mtime
    return _mirror


def incident_active() -> bool:
    """True when unattended work must be suspended. The one call each seam makes."""
    return get_incident().active


def activate(reason: str = "") -> IncidentState:
    """Turn incident mode ON (SEL-audited). Idempotent — re-activating refreshes
    the reason but keeps the original ``started_at``."""
    current = get_incident()
    started = current.started_at if current.active else datetime.now(timezone.utc).isoformat()
    state = IncidentState(active=True, reason=reason or current.reason, started_at=started)
    _write(state)
    _audit("incident_activated", reason=reason)
    logger.warning("INCIDENT MODE ACTIVATED: %s", reason or "(no reason given)")
    return state


def resume() -> IncidentState:
    """Turn incident mode OFF (SEL-audited). The window (started_at→now) is left in
    the log via the SEL transition so the Runs surface can show 'suppressed during
    incident'."""
    state = IncidentState(active=False, reason="", started_at="")
    _write(state)
    _audit("incident_resumed")
    logger.warning("Incident mode resumed (unattended work re-enabled)")
    return state


async def watch(
    on_change: Callable[[IncidentState], None], *, interval: float | None = None
) -> None:
    """Call *on_change* each time the switch is found to have moved, until the gateway stops.

    A flip is a change of ``active`` or of the reason, made here or by the CLI in another
    process (the mtime refresh in :func:`get_incident` sees both). A watcher whose callback
    raises keeps watching: a page that missed one change must still hear the next.
    """
    from personalclaw import shutdown_event

    last = get_incident()
    while not shutdown_event.is_set():
        await asyncio.sleep(WATCH_INTERVAL_SECS if interval is None else interval)
        current = get_incident()
        if (current.active, current.reason) == (last.active, last.reason):
            continue
        last = current
        try:
            on_change(current)
        except Exception:
            logger.debug("incident watch callback failed", exc_info=True)


def _write(state: IncidentState) -> None:
    global _mirror, _mirror_mtime
    path = _incident_path()
    atomic_write(
        path,
        json.dumps(
            {"active": state.active, "reason": state.reason, "started_at": state.started_at}
        ),
    )
    _mirror = state
    try:
        _mirror_mtime = path.stat().st_mtime
    except OSError:
        _mirror_mtime = -1.0


def _audit(operation: str, *, reason: str = "") -> None:
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="incident",
            operation=f"guardrails.{operation}",
            outcome="ok",
            source="guardrails",
            resources=reason[:200] if reason else "",
        )
    except Exception:
        logger.debug("incident SEL audit failed", exc_info=True)


def reset_incident_mirror() -> None:
    """Drop the in-process mirror — invoked by an autouse test fixture so incident
    state from one test never leaks into the next (the SEL/breaker discipline)."""
    global _mirror, _mirror_mtime
    _mirror = None
    _mirror_mtime = -1.0
