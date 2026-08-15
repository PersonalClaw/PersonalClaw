"""The transport-driven push half of the sync cycle (DURABILITY-AND-SYNC §4.1, DAS-6c-ii-f).

The mirror of the pull engine. Given a fresh local shard export (from ``shards.export_shards``),
it publishes that export as this machine's next seq and announces it in the shared registry:

    seq       = registry.bump(self_id, manifest_sha=…, now=…)   # 6c-ii-a — monotonic
    objects   = every file in the export dir, keyed machines/<self_id>/seq-NNNN/<rel>
    outbox.enqueue(target, seq, …)                              # 6c-ii-b — durable obligation
    drain: transport.push(objects) → outbox.record_outcome(...) # never-drop outcomes
    CAS:   transport.cas_registry(expected_sha, registry.to_bytes())
             on a lost race → re-pull the registry, re-bump on top, retry (idempotent)

Every step composes a piece already shipped and tested in isolation; this module owns only the
orchestration and the CAS-retry loop. Insert-only object keys (seq-numbered, never rewritten)
make a retried push a no-op, so a CAS race costs a re-pull, never a double-write or a lost push.

Clock-free: the timestamp is passed in (``now``), like the registry and outbox models.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from personalclaw.durability.outbox import (
    OUTCOME_PERMANENT,
    OUTCOME_TRANSIENT,
    Outbox,
)
from personalclaw.durability.registry import REGISTRY_KEY, Registry, shard_prefix
from personalclaw.sync_transports.base import SyncObject, SyncTransportProvider

logger = logging.getLogger(__name__)

# How many times to re-pull-and-retry the registry CAS before giving up this cycle.
_MAX_CAS_ATTEMPTS = 5


@dataclass
class PushReport:
    """What one publish did."""

    seq: int = 0
    objects: int = 0
    pushed: int = 0
    push_outcome: str = ""
    registry_committed: bool = False
    cas_attempts: int = 0
    detail: str = ""


def _objects_for(export_dir: Path, prefix: str) -> list[SyncObject]:
    """Every file under ``export_dir`` as a :class:`SyncObject` keyed by ``prefix`` + its
    export-relative path — the exact inverse of the pull engine's ``_materialize``."""
    objs: list[SyncObject] = []
    for path in sorted(export_dir.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(export_dir).as_posix()
            objs.append(SyncObject(key=prefix + rel, data=path.read_bytes()))
    return objs


def publish_export(
    transport: SyncTransportProvider,
    export_dir: Path,
    registry: Registry,
    outbox: Outbox,
    *,
    self_id: str,
    manifest_sha: str,
    now: str = "",
    reload_registry=None,
) -> PushReport:
    """Publish ``export_dir`` as this machine's next seq and announce it via a CAS registry bump.

    ``reload_registry`` is an optional ``() -> Registry`` the CAS loop calls to re-pull the
    shared registry after a lost race (the cycle passes one that reads + parses the remote
    ``registry.json``); without it a CAS failure ends the attempt (single-writer/test path).
    Returns a :class:`PushReport`. The push obligation is recorded in the durable outbox first,
    so a crash between push and registry-commit leaves a pending entry the next cycle re-drains
    (the object keys are insert-only, so that re-drain is a no-op).
    """
    report = PushReport()
    seq = registry.bump(self_id, manifest_sha=manifest_sha, now=now)
    report.seq = seq
    prefix = shard_prefix(self_id, seq)
    objects = _objects_for(export_dir, prefix)
    report.objects = len(objects)

    # Durable obligation FIRST — if we crash mid-push, the outbox still owes this push.
    entry = outbox.enqueue(transport.name, seq, prefix=prefix, local_dir=str(export_dir), now=now)

    push = transport.push(objects)
    report.pushed = push.pushed
    report.push_outcome = push.outcome
    outbox.record_outcome(entry.id, push.outcome, now=now, detail=push.detail)
    if push.outcome in (OUTCOME_TRANSIENT, OUTCOME_PERMANENT):
        # The bytes did not all land — do NOT announce the seq in the registry, or a peer
        # would pull a prefix whose objects are missing. The outbox retries next cycle.
        report.detail = f"push {push.outcome}: {push.detail}"
        return report

    report.registry_committed = _commit_registry(
        transport, registry, self_id, manifest_sha, now, reload_registry, report
    )
    return report


def _commit_registry(
    transport, registry, self_id, manifest_sha, now, reload_registry, report
) -> bool:
    """CAS-update the shared registry with our new seq, re-pulling + re-bumping on a lost race.

    Insert-only object writes are idempotent, so a retry is free: on a CAS miss we re-pull the
    remote registry, re-apply our bump on top of the peers' latest, and try again. Bounded so a
    pathological race can't spin forever — a give-up leaves the objects pushed (a peer can still
    discover them once someone's registry write wins) and the outbox entry delivered.
    """
    expected: str | None = None
    for attempt in range(1, _MAX_CAS_ATTEMPTS + 1):
        report.cas_attempts = attempt
        if transport.cas_registry(expected, registry.to_bytes()):
            return True
        if reload_registry is None:
            report.detail = "registry CAS lost and no reloader provided"
            return False
        # Lost the race: re-pull, re-apply our seq on top of the peers' latest, retry.
        remote = reload_registry()
        merged = Registry.loads(remote.to_bytes())
        merged.bump(self_id, manifest_sha=manifest_sha, now=now)
        # Carry peers' higher seqs forward (our own bump already applied above).
        for mid, e in registry.machines.items():
            if mid != self_id and e.seq > merged.seq_of(mid):
                merged.machines[mid] = e
        # …and our ancestor shas (§4.2): the pull we just did is the freshest agreement, so it
        # wins over the reloaded copy per family/id. Dropping them would silently erase the
        # ancestry conflict detection compares against, turning the next divergence into an
        # undetectable LWW coin-flip.
        for entry_id, rows in registry.ancestors.items():
            merged.record_ancestors(entry_id, rows)
        registry.machines = merged.machines
        registry.ancestors = merged.ancestors
        expected = remote.sha()
    report.detail = f"registry CAS lost after {_MAX_CAS_ATTEMPTS} attempts"
    return False


# Re-exported so the cycle engine names one registry-key constant, not a string literal.
__all__ = ["PushReport", "publish_export", "REGISTRY_KEY"]
