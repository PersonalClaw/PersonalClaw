"""Claim leases — the on-disk half of the pure claim logic in ``containers``.

``containers.claim``/``release`` decide WHETHER a claim may be taken; this module makes
the claim SURVIVE the process that took it. The split is the same one the rest of the
slice keeps: the decision is pure and testable without a gateway, the storage lives here.

Two mechanisms, doing two different jobs:

* **The flock (``concurrency.single_flight``) is mutual exclusion.** It guards the
  read-modify-write of one claim file so two co-tenant workers cannot both read "free"
  and both write themselves as holder. It is held only for the duration of that critical
  section and is released — by the OS even on a crash — the instant it ends. It is NOT the
  lease.
* **The FILE is the lease.** Its recorded ``expires_at`` is what a crashed holder leaves
  behind: the work stays claimed for the TTL and then any reader (``board_row``) drops the
  expired claim at render, so the board is truthful across a gateway kill without any
  process having to clean up.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.concurrency import single_flight
from personalclaw.config.loader import config_dir
from personalclaw.workflows import containers
from personalclaw.workflows.containers import Claim

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^a-zA-Z0-9._-]")


def _leases_dir() -> Path:
    d = config_dir() / "locks" / "leases"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _lease_path(target_id: str) -> Path:
    """The claim file backing ``target_id`` (a run id or task id).

    Sanitized so a ``..``-shaped id cannot escape the leases dir — the id reaches here from
    a stored row, and a row is not a trust boundary.
    """
    safe = _UNSAFE.sub("_", target_id)[:64] or "_"
    return _leases_dir() / f"{safe}.json"


def read_claim(target_id: str) -> Claim | None:
    """The recorded claim for ``target_id``, or None when absent/unreadable.

    Never raises: a corrupt claim file reads as "no claim" rather than taking down the
    board that renders it. Expiry is NOT applied here — the reader (``board_row``) drops an
    expired claim at render with the clock it already holds, so this returns exactly what
    was written.
    """
    path = _lease_path(target_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    except Exception:
        logger.debug("claim read failed for %s", target_id, exc_info=True)
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return Claim(
            holder=str(raw.get("holder", "") or ""),
            expires_at=float(raw.get("expires_at", 0.0) or 0.0),
            taken_at=float(raw.get("taken_at", 0.0) or 0.0),
            renewals=int(raw.get("renewals", 0) or 0),
        )
    except (TypeError, ValueError):
        return None


def acquire_claim(
    target_id: str, holder: str, *, ttl: int = containers.DEFAULT_LEASE_SECS
) -> tuple[Claim | None, str]:
    """Take a claim on ``target_id`` for ``holder``, or refuse with a reason.

    The flock guards the whole read-existing → decide → write critical section, so two
    workers racing for the same target never both win. If the flock is already held by
    another live process the answer is ``"contended"`` — single-flight means don't
    double-run, never wait in line. On grant the new claim is written atomically so a
    crashed writer cannot leave a torn lease that reads as held-by-nobody.
    """
    with single_flight(f"claim:{target_id}") as got:
        if not got:
            return None, "contended"
        now = time.time()
        existing = read_claim(target_id)
        if existing is not None and existing.expired(now):
            existing = None
        granted, reason = containers.claim(holder, now=now, ttl=ttl, existing=existing)
        if granted is not None:
            atomic_write(_lease_path(target_id), json.dumps(granted.to_dict()))
        return granted, reason


def release_claim(target_id: str, holder: str) -> tuple[Claim | None, str]:
    """Release ``holder``'s claim on ``target_id``, or refuse with a reason.

    Only the holder may release — a release that let anyone drop anyone's claim would make
    the lease advisory in the one direction that matters (a second worker stealing work by
    releasing first). The file is unlinked on a real release and rewritten when a
    still-held foreign claim is returned unchanged, so the on-disk state always mirrors
    the decision.
    """
    with single_flight(f"claim:{target_id}") as got:
        if not got:
            return read_claim(target_id), "contended"
        existing = read_claim(target_id)
        remaining, reason = containers.release(existing, holder)
        path = _lease_path(target_id)
        if remaining is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, json.dumps(remaining.to_dict()))
        return remaining, reason
