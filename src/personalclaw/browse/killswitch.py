"""The browse kill switch (BROWSE-AUTOMATION §(b) — the mirror's one-click stop).

One flag — ``~/.personalclaw/browse_kill.json`` (``{active, reason, started_at}``) — stops all
UNATTENDED browsing: a running browse loop parks within one step, and a new browse run refuses to
start. It is checked at the two seams that spend a browser — the provider before it opens one, and
the loop before each model call — for the same reason the incident switch is checked at its seams
rather than mutating a store: a gate one level away from the work is bypassed by the next caller.

**Distinct from the incident kill switch, on purpose.** ``guardrails.incident`` suspends ALL
unattended work (cron, hooks, triggers, subagents, browse); this stops ONLY browse. The mirror
panel puts a stop button one click from "that page looks wrong" without the collateral of halting
every other automation the user has running — a scalpel beside the incident switch's hammer.
Killing browse never touches interactive chat, and re-enabling it is EXPLICIT (the release call),
so a stop is never silently undone.

The shape mirrors ``guardrails.incident`` deliberately (same file/flag/mtime-mirror discipline) so
there is one way to reason about a kill switch in this codebase, not two:

* **Opt-IN, fail toward NOT killed.** A missing or unreadable file means "not killed" — the normal,
  overwhelmingly common state. Flipping browse off on a transient read error would be the wrong
  failure mode for a control whose whole job is to be deliberate.
* **In-process mirror refreshed from the file's mtime**, so a flag flipped by another process (a
  CLI, another gateway worker) is picked up without a restart.
* **Engage/release are SEL-audited**, so the evidentiary record the amendment requires — that a
  human directed the stop — exists.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

_KILL_FILENAME = "browse_kill.json"


@dataclass(frozen=True)
class BrowseKillState:
    """Whether unattended browsing is stopped, and why."""

    active: bool = False
    reason: str = ""
    started_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"active": self.active, "reason": self.reason, "started_at": self.started_at}


def _kill_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _KILL_FILENAME


# In-process mirror + the mtime it was loaded at, so a flag flipped by another process is picked up
# without a restart (the same mtime-sync habit `guardrails.incident` uses).
_mirror: BrowseKillState | None = None
_mirror_mtime: float = -1.0


def _read_file() -> BrowseKillState:
    path = _kill_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return BrowseKillState()
        return BrowseKillState(
            active=bool(data.get("active", False)),
            reason=str(data.get("reason", "")),
            started_at=str(data.get("started_at", "")),
        )
    except (OSError, ValueError):
        return BrowseKillState()


def get_kill() -> BrowseKillState:
    """Current browse-kill state, refreshing the mirror from the file's mtime.

    Fail-safe is NOT applied (this is opt-IN, not a guard flag): an unreadable or missing file
    means NO kill — the normal state. Halting browse on a transient read error would be the wrong
    failure mode for a deliberate stop."""
    global _mirror, _mirror_mtime
    path = _kill_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _mirror = BrowseKillState()
        _mirror_mtime = -1.0
        return _mirror
    if _mirror is None or mtime != _mirror_mtime:
        _mirror = _read_file()
        _mirror_mtime = mtime
    return _mirror


def browse_killed() -> bool:
    """True when unattended browsing must be stopped. The one call each seam makes."""
    return get_kill().active


def engage(reason: str = "") -> BrowseKillState:
    """Stop unattended browsing (SEL-audited). Idempotent — re-engaging refreshes the reason but
    keeps the original ``started_at``."""
    current = get_kill()
    started = current.started_at if current.active else datetime.now(timezone.utc).isoformat()
    state = BrowseKillState(active=True, reason=reason or current.reason, started_at=started)
    _write(state)
    _audit("browse_kill_engaged", reason=reason)
    logger.warning("BROWSE KILL SWITCH ENGAGED: %s", reason or "(no reason given)")
    return state


def release() -> BrowseKillState:
    """Re-enable unattended browsing (SEL-audited)."""
    state = BrowseKillState(active=False, reason="", started_at="")
    _write(state)
    _audit("browse_kill_released")
    logger.warning("Browse kill switch released (unattended browsing re-enabled)")
    return state


def _write(state: BrowseKillState) -> None:
    global _mirror, _mirror_mtime
    path = _kill_path()
    atomic_write(path, json.dumps(state.to_dict()))
    _mirror = state
    try:
        _mirror_mtime = path.stat().st_mtime
    except OSError:
        _mirror_mtime = -1.0


def _audit(operation: str, *, reason: str = "") -> None:
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="browse",
            operation=f"browse.{operation}",
            outcome="ok",
            source="browse",
            resources=reason[:200] if reason else "",
        )
    except Exception:
        logger.debug("browse kill SEL audit failed", exc_info=True)


def reset_browse_kill_mirror() -> None:
    """Drop the in-process mirror — invoked by an autouse test fixture so kill state from one test
    never leaks into the next (the SEL/incident-mirror discipline)."""
    global _mirror, _mirror_mtime
    _mirror = None
    _mirror_mtime = -1.0
