"""Runner lifecycle — idle-release, lease records, transparent reconnect (§3.1(5)).

The ACP connection pool already does claim-and-rewarm: a warmed connection is handed to a
session and a replacement is warmed behind it. What it did NOT do is say *who is holding
what*, or notice that a holder went quiet. Both gaps are the same gap — nothing outlived the
pool's in-memory slot, so a gateway restart erased every fact about who had which runner, and
a session that stopped using its runner held it as far as any observer could tell (forever,
silently).

This module adds the durable half, and deliberately does NOT invent a second locking scheme
for it. It is an APPLICATION of the WORK-R8 claim convention in
:mod:`personalclaw.workflows.leases`: a flock (mutual exclusion for the read-modify-write) over
a JSON file under ``~/.personalclaw/locks/leases/`` whose recorded ``expires_at`` is the lease.
Same record shape, same directory, same expiry-at-render rule. Two lease systems that drift is
a documented risk of this plan (§12); one convention with two target namespaces is not.

The target namespace is ``runner:<runtime_id>`` — disjoint from the run/task ids WORK-R8 uses,
so neither can shadow the other.

**What the lease is and is not.** It is not the pool's mutual exclusion: the pool's own slot
already guarantees one claimant, because ``claim`` detaches the provider and the next caller
finds nothing. The lease is the *observable* half — the record a co-tenant session and the
Settings surface read to answer "who has the Claude Code runner, and since when". So a lease
that cannot be acquired is logged and stepped over rather than failing the claim: refusing a
claim on a stale advisory record would turn a visibility feature into an outage.

**Idle-release.** ``expires_at`` IS the idle deadline. Activity renews it (a same-holder
re-claim renews by construction — see ``containers.claim``), so a lease still in the future
means the holder was recently active, and one in the past means the holder went quiet for
longer than ``agent.runner_idle_release_secs``. Two things then act on that:

* every READER drops it (``lease_for`` returns ``None`` past expiry), so no surface can ever
  present a dead holder as the current one — this is the same
  drop-at-render rule ``board_row`` uses, for the same reason;
* :func:`sweep_idle_leases` deletes the file, so the release is a real state change and not
  just a rendering convention. The pool's health loop calls it.

Both halves exist on purpose: the reader's drop makes the surface truthful even if the sweep
never runs, and the sweep makes the on-disk state match what the surface says.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.workflows.containers import Claim

logger = logging.getLogger(__name__)

#: Lease target prefix. Keeps runner leases from ever colliding with WORK-R8's run/task ids
#: even though they share one directory and one record shape.
RUNNER_LEASE_PREFIX = "runner:"

#: The clamp applied to ``agent.runner_idle_release_secs`` wherever it is read, matching the
#: window ``_EDITABLE_CONFIG`` enforces on the PATCH path so a hand-edited ``config.json``
#: cannot express a TTL the dashboard would refuse to save.
IDLE_RELEASE_MIN_SECS = 60
IDLE_RELEASE_MAX_SECS = 86_400


def lease_target(runtime_id: str) -> str:
    """The WORK-R8 lease target id for a runner runtime."""
    return f"{RUNNER_LEASE_PREFIX}{runtime_id}"


def idle_release_secs() -> int:
    """``agent.runner_idle_release_secs``, clamped. Falls back to the dataclass default.

    Read at use rather than cached: the field is runtime-editable from Settings, and a cached
    TTL would mean a user lowering the window has to restart the gateway to see it apply.
    """
    try:
        from personalclaw.config.loader import AppConfig

        raw = int(getattr(AppConfig.load().agent, "runner_idle_release_secs", 1800))
    except Exception:
        logger.debug("runner idle-release TTL unreadable; using the default", exc_info=True)
        return 1800
    return max(IDLE_RELEASE_MIN_SECS, min(IDLE_RELEASE_MAX_SECS, raw))


def durable_sessions_enabled() -> bool:
    """Whether durable tmux-backed sessions are on AND usable (§5.1).

    BOTH gates, deliberately: the config flag is the user's intent and the binary probe is
    reality. tmux missing means the feature is silently off and behaviour is identical to
    today — never a hard error, because a user who flipped a flag on a machine without tmux
    asked for durability, not for a broken gateway.
    """
    try:
        from personalclaw.config.loader import AppConfig

        if not bool(getattr(AppConfig.load().agent, "durable_sessions", False)):
            return False
    except Exception:
        logger.debug("durable_sessions flag unreadable; treating as off", exc_info=True)
        return False
    from personalclaw import tmux_substrate

    return tmux_substrate.tmux_available()


def claim_runner(
    runtime_id: str, holder: str, *, ttl: int | None = None
) -> tuple["Claim | None", str]:
    """Record *holder*'s lease on *runtime_id*, or return the refusal reason.

    ``ttl`` defaults to the configured idle-release window. A re-claim by the same holder
    RENEWS (``containers.claim``'s same-holder rule) — which is exactly what transparent
    reconnect needs: a session whose connection died and re-claimed a warm replacement must
    not be locked out of its own runner until the old lease expired.
    """
    if not runtime_id or not holder:
        return None, "no holder"
    from personalclaw.workflows import leases

    return leases.acquire_claim(
        lease_target(runtime_id), holder, ttl=int(ttl if ttl is not None else idle_release_secs())
    )


def release_runner(runtime_id: str, holder: str) -> tuple["Claim | None", str]:
    """Drop *holder*'s lease on *runtime_id*. Only the holder may (WORK-R8's rule)."""
    if not runtime_id or not holder:
        return None, "no holder"
    from personalclaw.workflows import leases

    return leases.release_claim(lease_target(runtime_id), holder)


def lease_for(runtime_id: str, *, now: float | None = None) -> dict | None:
    """The CURRENT lease on *runtime_id* as a dict, or ``None`` when free.

    Expiry is applied here rather than left to the caller. Every surface that renders a
    holder gets the same answer, and an idle-released runner cannot be painted as held by a
    session that stopped talking an hour ago — the drop-at-read rule from ``board_row``.

    ``age_secs`` is included because "held by X" alone does not tell a user whether to wait
    or to intervene; ``expires_in_secs`` says how long until idle-release takes it back.
    """
    if not runtime_id:
        return None
    from personalclaw.workflows import leases

    claim = leases.read_claim(lease_target(runtime_id))
    if claim is None:
        return None
    ts = time.time() if now is None else now
    if claim.expired(ts):
        return None
    return {
        "holder": claim.holder,
        "taken_at": claim.taken_at,
        "expires_at": claim.expires_at,
        "renewals": claim.renewals,
        "age_secs": max(0, int(ts - claim.taken_at)) if claim.taken_at else 0,
        "expires_in_secs": max(0, int(claim.expires_at - ts)),
    }


def sweep_idle_leases(*, now: float | None = None) -> list[str]:
    """Delete every runner lease whose idle window has elapsed. Returns the runtime ids.

    Enumerated from the runner catalog rather than by scanning the leases directory: the
    directory is shared with WORK-R8's run/task claims, and a sweep that walked it would be
    one prefix-matching bug away from releasing a workflow claim. The catalog is the set of
    runners that can be leased at all.

    The release is issued AS the recorded holder, so WORK-R8's holder-only release rule is
    honoured rather than bypassed — an expired lease is still somebody's record, and the one
    function allowed to drop it is the same one a live holder would call.
    """
    ts = time.time() if now is None else now
    released: list[str] = []
    try:
        from personalclaw.agents import runners as runner_catalog
        from personalclaw.workflows import leases
    except Exception:
        logger.debug("runner lease sweep: catalog unavailable", exc_info=True)
        return released
    try:
        runtime_ids = {d.runtime_id for d in runner_catalog.catalog().values() if d.runtime_id}
    except Exception:
        logger.debug("runner lease sweep: catalog read failed", exc_info=True)
        return released
    for runtime_id in sorted(runtime_ids):
        claim = leases.read_claim(lease_target(runtime_id))
        if claim is None or not claim.expired(ts):
            continue
        _, reason = leases.release_claim(lease_target(runtime_id), claim.holder)
        if reason:
            logger.debug("runner lease sweep: %s not released (%s)", runtime_id, reason)
            continue
        released.append(runtime_id)
        logger.info(
            "runner lifecycle: released %s — holder %s idle past its %ds lease",
            runtime_id,
            claim.holder,
            int(claim.expires_at - claim.taken_at) if claim.taken_at else idle_release_secs(),
        )
    return released
