"""The claim store: which trigger is running right now (§3.1 overlap — S97).

**🔴 TWO MEASURED DEFECTS THIS CLEARS.** `scheduling.claim_fire` decides overlap from an `existing`
claim the caller supplies, and `firepath.evaluate` returns the claim it granted with the note "the
caller must release it". Measured: **`tick()` passes no `existing_claim` and nothing persists the
granted one.** So:

1. **`overlap: skip` is inert.** Every fire is evaluated against `existing=None`, so the claim gate
   always grants — a trigger whose previous run is still going fires again anyway, which is the
   precise failure `overlap` exists to prevent. The control is present, reviewed, and enforcing
   nothing.
2. **`is_running` is unanswerable.** `ScheduleService` answers it from `self._executing`, a
   PROCESS-LOCAL dict — so it is wrong after a restart (a run in flight reads as idle) and invisible
   to any other process. The API facade needs this to re-point off `ScheduleService`, and a
   process-local set cannot serve it.

A claim is therefore a **sidecar file per trigger** (`<store dir>/trigger-claims/<safe-id>.json`),
the same convention as `trigger-watch/` (S93) and `task_leases/` (S61d), for the same reasons: it
is high-churn runtime state that must not rewrite `triggers.json` on every fire, and it must be
visible ACROSS processes — the MCP tools, the gateway tick, and the API all answer "is it running"
from one place. The root comes from the STORE, so a claim never describes a different store's
trigger.

**Expiry is read-time, not swept.** A claim carries `max_duration_secs`; a reader treats an older
claim as absent rather than requiring a janitor to have run. A crashed run must not hold its trigger
hostage until some cleanup pass notices — the same fail-open direction `pool`'s leases take.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from personalclaw.triggers.scheduling import CLAIM_MAX_DURATION_SECS, Claim

logger = logging.getLogger(__name__)

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _claims_dir(base_dir: Path | str | None) -> Path:
    from personalclaw.config.loader import config_dir

    root = Path(base_dir) if base_dir else config_dir()
    return root / "trigger-claims"


def _claim_path(trigger_id: str, base_dir: Path | str | None) -> Path:
    safe = _SAFE_RE.sub("-", trigger_id) or "claim"
    return _claims_dir(base_dir) / f"{safe}.json"


def read_claim(
    trigger_id: str, *, now: float = 0.0, base_dir: Path | str | None = None
) -> Claim | None:
    """The live claim for a trigger, or None when it is idle.

    An EXPIRED claim reads as None (read-time expiry): a crashed run must not hold its
    trigger hostage until a janitor notices. A malformed record also reads as None — an
    unparseable claim that blocked every future fire would be worse than one that is ignored,
    and the row is visible on disk for a human either way.
    """
    now = now or time.time()
    path = _claim_path(trigger_id, base_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        claimed_at = float(raw.get("claimed_at") or 0.0)
        max_secs = float(raw.get("max_duration_secs") or CLAIM_MAX_DURATION_SECS)
    except (TypeError, ValueError):
        return None
    if claimed_at <= 0 or now - claimed_at >= max_secs:
        return None
    return Claim(
        trigger_id=str(raw.get("trigger_id") or trigger_id),
        holder=str(raw.get("holder") or ""),
        claimed_at=claimed_at,
        max_duration_secs=max_secs,
    )


def write_claim(claim: Any, *, base_dir: Path | str | None = None) -> None:
    """Persist a granted claim atomically (tmp→rename).

    Atomic because a half-written claim read back as malformed would read as IDLE, and the whole
    point of the record is that a second fire can see the first one.
    """
    if claim is None or not getattr(claim, "trigger_id", ""):
        return
    path = _claim_path(claim.trigger_id, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "trigger_id": claim.trigger_id,
        "holder": getattr(claim, "holder", ""),
        "claimed_at": float(getattr(claim, "claimed_at", 0.0)),
        "max_duration_secs": float(
            getattr(claim, "max_duration_secs", CLAIM_MAX_DURATION_SECS) or CLAIM_MAX_DURATION_SECS
        ),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def release_claim(trigger_id: str, *, base_dir: Path | str | None = None) -> bool:
    """Drop a trigger's claim. Idempotent — releasing an absent claim is success, not an error.

    Idempotent on purpose: the release path runs in a `finally`, and a run that failed BEFORE its
    claim was written would otherwise turn its own cleanup into a second error.
    """
    path = _claim_path(trigger_id, base_dir)
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        logger.debug("could not release claim for %s", trigger_id, exc_info=True)
        return False


def is_running(trigger_id: str, *, now: float = 0.0, base_dir: Path | str | None = None) -> bool:
    """Whether a run is in flight for this trigger, ACROSS processes.

    Replaces `ScheduleService.is_running`, which answered from a process-local dict — wrong after a
    restart (an in-flight run read as idle) and invisible to the MCP process writing the same store.
    """
    return read_claim(trigger_id, now=now, base_dir=base_dir) is not None


def running_since(
    trigger_id: str, *, now: float = 0.0, base_dir: Path | str | None = None
) -> float | None:
    """When the in-flight run started, or None. Replaces `ScheduleService.running_since`."""
    claim = read_claim(trigger_id, now=now, base_dir=base_dir)
    return claim.claimed_at if claim else None


def running_ids(*, now: float = 0.0, base_dir: Path | str | None = None) -> list[str]:
    """Every trigger with a live claim, sorted. One directory scan for a whole list view.

    Sorted so a list surface renders in a stable order rather than in directory order, which changes
    between reads and makes rows appear to move on their own.
    """
    root = _claims_dir(base_dir)
    if not root.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(root.iterdir()):
        if entry.suffix != ".json":
            continue
        try:
            raw = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        trigger_id = str(raw.get("trigger_id") or "") if isinstance(raw, dict) else ""
        if trigger_id and is_running(trigger_id, now=now, base_dir=base_dir):
            out.append(trigger_id)
    return out
