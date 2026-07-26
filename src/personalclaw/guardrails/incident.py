"""Incident kill switch (AUTONOMY-GUARDRAILS §1.3).

One flag — ``~/.personalclaw/incident.json`` (``{active, reason, started_at}``) —
suspends ALL unattended work within one poll interval. There is no unified trigger
store to flip (six independent stores), so incident mode does NOT mutate stores; it
is checked at the execution seams (cron due-collection, hook fire, event-trigger
fire, autonudge, heartbeat tick, inbox AI, non-interactive subagent spawn). Each
seam gains one ``if incident_active(): skip``.

**Interactive chat is untouched** — the user talking to their assistant during an
incident is the point. Resume is EXPLICIT (``POST /api/incident/resume {confirm}``
or ``personalclaw incident off``); activation/resume are SEL-audited.

The in-process mirror is refreshed from the file's mtime (the existing mtime-sync
habit), so a flag flipped by the CLI in another process is picked up by the running
gateway without a restart.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

_INCIDENT_FILENAME = "incident.json"


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
