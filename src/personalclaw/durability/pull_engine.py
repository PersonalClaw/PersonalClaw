"""The transport-driven pull half of the sync cycle (DURABILITY-AND-SYNC §4.1, DAS-6c-ii-e).

This is where the pure pieces meet a real remote. Given a transport (DAS-6a), the local
:class:`registry.Registry` just pulled, and the durable :class:`cursor.Cursor`, it walks the
peers' unseen shard sets and merges each into the live store:

    for each peer prefix the cursor hasn't consumed (registry.new_prefixes_since, ascending):
        refs   = transport.list_remote(prefix)          # cheap
        objs   = transport.pull(refs)                   # bytes
        dir    = materialize objs (strip the prefix)    # a validatable shard dir
        rows   = shards.import_shards(dir)              # 6b — validates, reassembles
        for each entry: reconcile.reconcile_entry(...)  # 6c-i + 6c-ii-c + 6c-ii-d
        cursor.record(peer, seq, aggregate_verdict)     # 6c-ii-b — consumed-only

The **DB path is an injected seam**, not skipped. A `sqlite`/`tree` entry can't be losslessly
rebuilt from row shards (the exporter stores embedding/blob columns as size placeholders), so
those go to an optional ``db_merger`` callback. Until it's provided (DAS-6c-ii-f), a seq that
contains a DB entry is **held** — the cursor is not advanced, so the seq is re-pulled once the
seam lands, rather than silently skipping unmerged database data (§4.1: advance only on
consumed rows). That is the honest partial-slice behavior, and it keeps the row-entry
convergence path (criterion 4) fully working today.

Aggregate verdict for a seq: any held entry (prerequisite-absent, or a DB entry with no
merger) holds the whole seq; otherwise ``payload-bad`` if any entry was poison (advance past
it), else ``consumed``. A prefix the remote can't actually serve yet (a partial push) holds.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from personalclaw.atomic_write import atomic_write_bytes
from personalclaw.durability import inventory as inv
from personalclaw.durability import reconcile
from personalclaw.durability.cursor import CONSUMED, PAYLOAD_BAD, PREREQ_ABSENT, Cursor
from personalclaw.durability.registry import Registry, shard_prefix
from personalclaw.durability.shards import import_shards
from personalclaw.sync_transports.base import SyncTransportProvider

logger = logging.getLogger(__name__)

#: A DB/tree merger: given the entry and the materialized shard dir, return a cursor verdict.
DbMerger = Callable[[inv.StateEntry, Path], str]


@dataclass
class SeqOutcome:
    """What pulling one peer's one seq did."""

    peer_id: str
    seq: int
    verdict: str = CONSUMED
    advanced: bool = False
    entries: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    deferred_db: list[str] = field(default_factory=list)  # entry ids held for the DB seam
    detail: str = ""


@dataclass
class PullReport:
    """Every seq outcome from one pull sweep, plus roll-ups for the caller/doctor."""

    outcomes: list[SeqOutcome] = field(default_factory=list)

    @property
    def advanced(self) -> int:
        return sum(1 for o in self.outcomes if o.advanced)

    @property
    def held(self) -> int:
        return sum(1 for o in self.outcomes if not o.advanced)

    @property
    def added(self) -> int:
        return sum(o.added for o in self.outcomes)

    @property
    def removed(self) -> int:
        return sum(o.removed for o in self.outcomes)


def _materialize(objs, prefix: str, dest: Path) -> int:
    """Write pulled objects into ``dest`` as a validatable shard dir, stripping ``prefix``
    from each key so paths are shard-dir-relative (``manifest.json``, ``tasks/entities.jsonl``).
    Returns how many objects landed. Objects outside ``prefix`` are ignored defensively."""
    written = 0
    for obj in objs:
        key = obj.key
        if not key.startswith(prefix):
            continue
        rel = key[len(prefix) :].lstrip("/")
        if not rel:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, obj.data)
        written += 1
    return written


def _pull_one_seq(
    transport: SyncTransportProvider,
    home: Path,
    peer_id: str,
    seq: int,
    db_merger: Optional[DbMerger],
) -> SeqOutcome:
    prefix = shard_prefix(peer_id, seq)
    out = SeqOutcome(peer_id=peer_id, seq=seq)
    refs = transport.list_remote(prefix)
    if not refs:
        # The registry says this seq exists but its objects aren't listable yet — a partial
        # push. Hold: prerequisite-absent, retried next cycle when the push completes.
        out.verdict = PREREQ_ABSENT
        out.detail = "no objects under prefix (partial push?)"
        return out
    objs = transport.pull(refs)
    with tempfile.TemporaryDirectory() as tmp:
        shard_dir = Path(tmp)
        if _materialize(objs, prefix, shard_dir) == 0:
            out.verdict = PREREQ_ABSENT
            out.detail = "prefix listed but no bytes pulled"
            return out
        try:
            imported = import_shards(shard_dir)
        except (ValueError, OSError) as exc:
            # A structurally invalid export won't merge on retry — advance past it, per §4.1.
            out.verdict = PAYLOAD_BAD
            out.detail = f"import failed: {exc}"
            return out
        held = False
        poison = False
        for entry_id, rows in imported.rows.items():
            entry = inv.by_id(entry_id)
            if entry is None:
                # A shard for an entry this build doesn't know — hold rather than lose it,
                # so a newer peer's entry isn't silently dropped by an older reader.
                held = True
                out.deferred_db.append(entry_id)
                continue
            out.entries += 1
            if reconcile.handles_kind(entry.kind):
                res = reconcile.reconcile_entry(home, entry, rows)
                out.added += res.added
                out.updated += res.updated
                out.removed += res.removed
                if res.verdict == PAYLOAD_BAD:
                    poison = True
            elif db_merger is not None:
                verdict = db_merger(entry, shard_dir)
                if verdict == PREREQ_ABSENT:
                    held = True
                elif verdict == PAYLOAD_BAD:
                    poison = True
            else:
                # DB/tree entry with no merger seam yet — hold the whole seq.
                held = True
                out.deferred_db.append(entry_id)
    if held:
        out.verdict = PREREQ_ABSENT
        out.detail = out.detail or ("held for DB seam: " + ", ".join(out.deferred_db))
    else:
        out.verdict = PAYLOAD_BAD if poison else CONSUMED
    return out


def pull_from_peers(
    transport: SyncTransportProvider,
    home: Path,
    registry: Registry,
    cursor: Cursor,
    *,
    self_id: str,
    db_merger: Optional[DbMerger] = None,
) -> PullReport:
    """Pull and merge every peer shard set the cursor hasn't consumed, oldest seq first.

    Advances the cursor only on a consumed (or payload-bad) seq; a held seq (a not-yet-servable
    prefix, an unknown entry, or a DB entry with no ``db_merger``) leaves the cursor where it
    is, so it is re-pulled next cycle. Returns a :class:`PullReport` of per-seq outcomes.
    """
    report = PullReport()
    seen = cursor.seen()
    for peer in registry.peers(self_id):
        already = int(seen.get(peer.machine_id, 0) or 0)
        for seq in range(already + 1, peer.seq + 1):
            outcome = _pull_one_seq(transport, home, peer.machine_id, seq, db_merger)
            outcome.advanced = cursor.record(peer.machine_id, seq, outcome.verdict)
            report.outcomes.append(outcome)
            if not outcome.advanced:
                # Contiguity: don't pull seq+1 past a held seq — its prerequisite may be
                # exactly the held one. Resume from here next cycle.
                break
    return report
