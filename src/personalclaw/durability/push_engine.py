"""The transport-driven push half of the sync cycle (DAS-6c-ii-f).

The mirror of the pull engine. Given a fresh local shard export (from ``shards.export_shards``),
it publishes that export as this machine's next seq and announces it in the shared registry:

    seq       = registry.bump(self_id, now=…)                   # 6c-ii-a — monotonic
    objects   = every file in the export dir, keyed machines/<self_id>/seq-NNNN/<rel>
    outbox.enqueue(target, seq, …)                              # 6c-ii-b — durable obligation
    drain: transport.push(objects) → outbox.record_outcome(...) # never-drop outcomes
    CAS:   transport.cas_registry(expected_sha, registry.to_bytes())
             on a lost race → re-pull the registry, re-bump on top, retry (idempotent)

Every step composes a piece already shipped and tested in isolation; this module owns only the
orchestration and the CAS-retry loop. Insert-only object keys (seq-numbered, never rewritten)
make a retried push a no-op, so a CAS race costs a re-pull, never a double-write or a lost push.

A seq is announced only once its push landed, so a publish that failed leaves its objects under
a seq no peer reads, and the next publish takes the same number. On a transport that removes
objects those are cleared before it pushes: insert-only, its push would keep them beside its own
export, and a peer would read one copy made of two. Once a newer copy has landed,
:func:`retire_superseded` removes this machine's copies it replaced (``durability.published``).

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
from personalclaw.durability.published import superseded
from personalclaw.durability.registry import REGISTRY_KEY, Registry, seq_of_key, shard_prefix
from personalclaw.sync_transports.base import (
    KeysRefused,
    RemoteRef,
    SyncObject,
    SyncTransportProvider,
)

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


def _clear(transport: SyncTransportProvider, prefix: str) -> None:
    """Remove what is under *prefix*, a seq the registry does not name yet — what an earlier
    publish of it left when its push or its registry write failed. No peer reads such a seq."""
    stale = [ref.key for ref in transport.list_remote(prefix) if ref.key.startswith(prefix)]
    if stale:
        logger.info("sync push: clearing %d object(s) a failed publish left", len(stale))
        transport.remove(stale)


def publish_export(
    transport: SyncTransportProvider,
    export_dir: Path,
    registry: Registry,
    outbox: Outbox,
    *,
    self_id: str,
    now: str = "",
    reload_registry=None,
    codec=None,
) -> PushReport:
    """Publish ``export_dir`` as this machine's next seq and announce it via a CAS registry bump.

    ``reload_registry`` is an optional ``() -> Registry`` the CAS loop calls to re-pull the
    shared registry after a lost race (the cycle passes one that reads + parses the remote
    ``registry.json``); without it a CAS failure ends the attempt (single-writer/test path).
    ``codec`` is an optional :class:`~personalclaw.durability.crypto.SyncCodec` (DAS-8): when
    present, every non-routing object is AES-256-GCM encrypted here — the LAST step before the
    transport, so no unencrypted shard byte can reach an untrusted store.
    Returns a :class:`PushReport`. The push obligation is recorded in the durable outbox first,
    so a crash between push and registry-commit leaves a pending entry the next cycle re-drains
    (the object keys are insert-only, so that re-drain is a no-op).
    """
    report = PushReport()
    seq = registry.bump(self_id, now=now)
    report.seq = seq
    prefix = shard_prefix(self_id, seq)
    objects = _objects_for(export_dir, prefix)
    report.objects = len(objects)

    if codec is not None:
        objects, refused = codec.encrypt_for_push(objects)
        if refused:
            # §4.4 send-side rejection. Do NOT announce a seq whose objects are incomplete,
            # and do not retry: a plaintext object in an encrypted store is a contract
            # violation, so this is a permanent outcome the outbox records and stops chasing.
            entry = outbox.enqueue(
                transport.name, seq, prefix=prefix, local_dir=str(export_dir), now=now
            )
            outbox.record_outcome(
                entry.id, OUTCOME_PERMANENT, now=now, detail="; ".join(refused.reasons[:5])
            )
            report.push_outcome = OUTCOME_PERMANENT
            report.detail = f"encryption refused {len(refused)} plaintext object(s)"
            return report

    # Durable obligation FIRST — if we crash mid-push, the outbox still owes this push.
    entry = outbox.enqueue(transport.name, seq, prefix=prefix, local_dir=str(export_dir), now=now)

    try:
        if transport.removes_old_copies:
            _clear(transport, prefix)
        push = transport.push(objects)
    except KeysRefused as refusal:
        # Nothing of the push was written, and a retry would be refused again: the outbox stops
        # chasing it, and the cycle says what was refused.
        outbox.record_outcome(entry.id, OUTCOME_PERMANENT, now=now, detail=str(refusal))
        raise
    report.pushed = push.pushed
    report.push_outcome = push.outcome
    outbox.record_outcome(entry.id, push.outcome, now=now, detail=push.detail)
    if push.outcome in (OUTCOME_TRANSIENT, OUTCOME_PERMANENT):
        # The bytes did not all land — do NOT announce the seq in the registry, or a peer
        # would pull a prefix whose objects are missing. The outbox retries next cycle.
        # The transport's own sentence: the sync report says it as the push's failure.
        report.detail = push.detail or f"the store answered {push.outcome}"
        return report

    report.registry_committed = _commit_registry(
        transport, registry, self_id, now, reload_registry, report
    )
    return report


def _commit_registry(transport, registry, self_id, now, reload_registry, report) -> bool:
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
        merged.bump(self_id, now=now)
        # Carry peers' higher seqs forward (our own bump already applied above).
        for mid, e in registry.machines.items():
            if mid != self_id and e.seq > merged.seq_of(mid):
                merged.machines[mid] = e
        registry.machines = merged.machines
        # A reload that found no registry has no bytes to swap against, so the next try creates
        # it, as the first did. A swap against the bytes of an empty one loses every time, and
        # that is what a create lost to a write that then never landed (a conditional write S3
        # answers 409 while another is still in progress) met on every retry.
        expected = remote.sha() if remote.present else None
    report.detail = (
        f"the shared registry changed under each of {_MAX_CAS_ATTEMPTS} tries to name this copy"
    )
    return False


def holds_copy(listed: list[RemoteRef], self_id: str, seq: int) -> bool:
    """Whether *listed*, a listing of this machine's folder of the store, holds its copy *seq*:
    the manifest an export writes is there."""
    manifest = f"{shard_prefix(self_id, seq)}manifest.json"
    return any(ref.key == manifest for ref in listed)


def retire_superseded(
    transport: SyncTransportProvider,
    listed: list[RemoteRef],
    *,
    self_id: str,
    newest: int,
    landed: dict[int, str],
    now: str,
) -> tuple[list[int], int]:
    """Remove this machine's copies in the store that a newer one replaced
    (``published.superseded``), and return their seqs and how many objects went.

    *listed* is a listing of this machine's folder of the store (``registry.machine_prefix``);
    only an object of one of its own seqs is ever removed, never another machine's, the registry
    or the salt. Each copy goes manifest first, so a peer that reads it meanwhile finds it lacking
    and holds it (``shards.IncompleteExport``) rather than reading half of it."""
    by_seq: dict[int, list[str]] = {}
    for ref in listed:
        seq = seq_of_key(self_id, ref.key)
        if seq is not None:
            by_seq.setdefault(seq, []).append(ref.key)
    doomed = superseded(by_seq, newest=newest, landed=landed, now=now)
    removed = 0
    for seq in doomed:
        keys = sorted(by_seq[seq], key=lambda key: (not key.endswith("/manifest.json"), key))
        removed += transport.remove(keys)
    return doomed, removed


# Re-exported so the cycle engine names one registry-key constant, not a string literal.
__all__ = ["PushReport", "publish_export", "retire_superseded", "holds_copy", "REGISTRY_KEY"]
