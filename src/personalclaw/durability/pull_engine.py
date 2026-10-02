"""The transport-driven pull half of the sync cycle (DAS-6c-ii-e).

This is where the pure pieces meet a real remote. Given a transport, the local
:class:`registry.Registry` just pulled, and the durable :class:`cursor.Cursor`, it takes each
peer's newest shard set the cursor hasn't consumed and merges it into the live store:

    for each peer whose registry seq is past the cursor's:
        prefix = shard_prefix(peer, its newest seq)     # one whole copy of its records
        refs   = transport.list_remote(prefix)          # cheap
        objs   = transport.pull(refs)                   # bytes
        dir    = materialize objs (strip the prefix)    # a validatable shard dir
        rows   = shards.import_shards(dir)              # 6b — validates, reassembles
        for each entry: reconcile.reconcile_entry(...)  # 6c-i + 6c-ii-c + 6c-ii-d
        cursor.record(peer, seq, aggregate_verdict)     # 6c-ii-b — consumed-only

**Only the newest.** Every seq is a whole export of that machine's records, not a change to
the one before, so the newest holds everything an older one does: a row merge measures it from
what the two homes last agreed on (``ancestors``), a database merge takes the newest copy whole,
and a deletion rides it as a tombstone (``tombstones``). Pulling each seq in turn read every
copy a machine had sent since — 96 a day at the default fifteen minutes — and made every old
copy one some peer might still need, so none could ever be removed. A peer now reads one copy of
each machine per cycle, and the copies a newer one replaced are removed (``durability.published``).

The **DB path is an injected seam**, not skipped. A `sqlite` entry can't be losslessly
rebuilt from row shards (the exporter stores embedding/blob columns as size placeholders), so
it goes to an optional ``db_merger`` callback (a `tree` is not in the shards at all). Without
one, a seq that contains a DB entry is **held** — the cursor is not advanced, so the peer's
newest is pulled again once there is one, rather than silently skipping unmerged database data
(§4.1: advance only on consumed rows).

Aggregate verdict for a seq: any held entry (prerequisite-absent, or a DB entry with no
merger) holds the whole seq; otherwise ``payload-bad`` if any entry was poison (advance past
it), else ``consumed``. A prefix the remote can't serve whole yet holds too: one with nothing
under it, or an export that lacks files it declares (``shards.IncompleteExport``) — a folder
that syncs itself brings another machine's files over one by one, and a copy read while its
machine removed it is short. Taken as poison, the cursor moved past it, and the copy was not read
again until that machine sent another, which may be days when its records don't change.

**Nothing another machine names lands outside where a sync may write.** Every path a peer names
is resolved before anything is written, and refused unless it is inside the export it came in
or inside the store it names, every symlink on the way followed (``record_ids.is_path_in_store``):
an object's key, a path its manifest declares (``shards.OutsideTheExport``), the file each of its
rows stands for (``reconcile.outside_their_store``), and its machine id, which names its folder of
the remote. A seq that names one is ``payload-bad`` whole — nothing of it is written — and the
paths are in ``refused``, with why, for the sync report. A pulled key used to be joined onto the
scratch folder as it came, so ``../`` in one wrote anywhere this machine's user may. A seq whose
key the transport itself won't list or read (``KeysRefused``: a link in its folder that leads out,
say) is refused the same way, with the transport's why.
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
from personalclaw.durability.ancestors import Ancestors
from personalclaw.durability.conflicts import ConflictQueue
from personalclaw.durability.cursor import CONSUMED, PAYLOAD_BAD, PREREQ_ABSENT, Cursor
from personalclaw.durability.registry import Registry, shard_prefix
from personalclaw.durability.shards import (
    ImportResult,
    IncompleteExport,
    OutsideTheExport,
    import_shards,
)
from personalclaw.record_ids import is_path_in_store, is_safe_record_id
from personalclaw.sync_transports.base import KeysRefused, SyncTransportProvider

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
    conflicts: int = 0  # both-sides-edited divergences queued for review
    detail: str = ""
    #: The paths this seq named outside what a sync may write, with why: never written.
    refused: dict[str, str] = field(default_factory=dict)


#: Why a path was refused, as the sync report says it.
OUTSIDE_THE_EXPORT = "names a path outside the export it came in"
OUTSIDE_THE_STORE = "names a file outside its store"
NOT_ONE_NAME = "a machine id that is not one plain name, so it names another folder of the remote"


@dataclass
class PullReport:
    """Every seq outcome from one pull sweep, plus roll-ups for the caller/doctor."""

    outcomes: list[SeqOutcome] = field(default_factory=list)
    #: The peers not pulled from, by their machine id, with why.
    peers_refused: dict[str, str] = field(default_factory=dict)

    @property
    def refused(self) -> dict[str, str]:
        """Every path the sweep refused, with why — each seq's, and each peer's it did not pull
        from — for the sync report."""
        out = dict(self.peers_refused)
        for outcome in self.outcomes:
            out.update(outcome.refused)
        return out

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
    def updated(self) -> int:
        return sum(o.updated for o in self.outcomes)

    @property
    def removed(self) -> int:
        return sum(o.removed for o in self.outcomes)

    @property
    def conflicts(self) -> int:
        return sum(o.conflicts for o in self.outcomes)


def _materialize(objs, prefix: str, dest: Path) -> tuple[int, dict[str, str]]:
    """Write pulled objects into ``dest`` as a validatable shard dir, stripping ``prefix``
    from each key so paths are shard-dir-relative (``manifest.json``, ``tasks/entities.jsonl``).
    Returns how many objects landed, and the keys refused with why.

    Every key is resolved first, and one that is not inside ``dest`` once the prefix is taken off
    — outside the prefix it was pulled with, absolute, climbing out, or leaving through a symlink
    — is refused. When any is, nothing is written: the seq it came in is not merged at all.
    """
    placed: list[tuple[Path, bytes]] = []
    refused: dict[str, str] = {}
    for obj in objs:
        key = obj.key
        rel = key[len(prefix) :] if key.startswith(prefix) else ""
        if key == prefix:
            continue  # the prefix's own folder, as a listing may name it: no object
        if not rel or not is_path_in_store(dest, rel):
            refused[key] = OUTSIDE_THE_EXPORT
            continue
        placed.append((dest.joinpath(*rel.split("/")), obj.data))
    if refused:
        return 0, refused
    for target, data in placed:
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, data)
    return len(placed), refused


def _rows_outside_their_store(home: Path, imported: ImportResult) -> dict[str, str]:
    """The files a peer's rows would write or remove outside their store
    (``reconcile.outside_their_store``), by their path inside the home, with why."""
    refused: dict[str, str] = {}
    for entry_id, rows in imported.rows.items():
        entry = inv.by_id(entry_id)
        if entry is None:
            continue  # an entry this build does not know is held, below
        for rel in reconcile.outside_their_store(home, entry, rows):
            refused[f"{entry.path}/{rel}"] = OUTSIDE_THE_STORE
    return refused


def _refuse(out: SeqOutcome, refused: dict[str, str]) -> SeqOutcome:
    """*out* as a seq that named a path outside what a sync may write: ``payload-bad``, which the
    cursor advances past — it can never become safe to take in — with the paths it named."""
    out.verdict = PAYLOAD_BAD
    out.refused = dict(refused)
    shown = ", ".join(sorted(refused)[:5])
    out.detail = f"refused: named a path outside what a sync may write ({shown})"
    return out


def _pull_one_seq(
    transport: SyncTransportProvider,
    home: Path,
    peer_id: str,
    seq: int,
    db_merger: Optional[DbMerger],
    ancestors: Optional[Ancestors] = None,
    queue: Optional[ConflictQueue] = None,
    now: str = "",
    codec=None,
    self_id: str = "",
) -> SeqOutcome:
    prefix = shard_prefix(peer_id, seq)
    out = SeqOutcome(peer_id=peer_id, seq=seq)
    try:
        refs = transport.list_remote(prefix)
        objs = transport.pull(refs) if refs else []
    except KeysRefused as refusal:
        # A key of this seq the transport won't list or read: nothing of it came in, and it is
        # refused whole, as one outside the export it came in is.
        return _refuse(out, refusal.refused)
    if not refs:
        # The registry names this seq but nothing of it is listable here yet: a folder that
        # syncs itself hasn't brought it over, or a newer copy replaced it since the registry was
        # read. Hold: prerequisite-absent, and the next cycle reads the newest.
        out.verdict = PREREQ_ABSENT
        out.detail = "nothing of it is in the store yet"
        return out
    if codec is not None:
        objs, refused = codec.decrypt_after_pull(objs)
        if refused.keys:
            # §4.4 receive-side rejection of PLAINTEXT in an encrypted store: a permanent skip.
            # Verdict payload-bad — which the cursor ADVANCES past — rather than the
            # prerequisite-absent hold an empty pull would otherwise produce, because a hold
            # here is precisely the error loop §4.4 forbids: the object will never become
            # decryptable, so re-pulling it forever is the bug.
            out.verdict = PAYLOAD_BAD
            out.detail = "encrypted-store violation: " + "; ".join(refused.reasons[:5])
            return out
        if refused.unreadable:
            # A well-formed ciphertext whose tag failed: wrong passphrase or tampering, and
            # GCM cannot say which. HOLD — advancing here permanently skipped every peer seq
            # after a single mistyped-passphrase cycle, and fixing the passphrase did not
            # bring them back. A hold costs a re-pull; the advance cost the user their data.
            out.verdict = PREREQ_ABSENT
            out.detail = (
                f"{len(refused.unreadable)} object(s) did not decrypt (wrong passphrase, or "
                "the store was modified) — held for retry"
            )
            return out
    with tempfile.TemporaryDirectory() as tmp:
        shard_dir = Path(tmp)
        written, refused = _materialize(objs, prefix, shard_dir)
        if refused:
            return _refuse(out, refused)
        if written == 0:
            out.verdict = PREREQ_ABSENT
            out.detail = "prefix listed but no bytes pulled"
            return out
        try:
            imported = import_shards(shard_dir)
        except OutsideTheExport as exc:
            return _refuse(out, {f"{prefix}{rel}": OUTSIDE_THE_EXPORT for rel in exc.paths})
        except IncompleteExport as exc:
            # Not all of it is here: still arriving, or removed while it was read. Nothing of it
            # is merged, and the next cycle reads it — or the newer copy that replaced it — again.
            out.verdict = PREREQ_ABSENT
            out.detail = f"{len(exc.paths)} of its files are not in the store yet"
            return out
        except (ValueError, OSError) as exc:
            # A structurally invalid export won't merge on retry — advance past it.
            out.verdict = PAYLOAD_BAD
            out.detail = f"import failed: {exc}"
            return out
        # Every file the change would write, resolved before any is: one outside its store and
        # nothing of the change is taken in.
        refused = _rows_outside_their_store(home, imported)
        if refused:
            return _refuse(out, refused)
        held = False
        poison = False
        # What the peer last agreed on with this home, as its copy says: the version of a record
        # it took from here before changing it, which an older copy of it used to show.
        agreed_there = imported.agreements.get(self_id, {}) if self_id else {}
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
                res = reconcile.reconcile_entry(
                    home,
                    entry,
                    rows,
                    ancestors=ancestors.of(peer_id, entry.id) if ancestors else {},
                    published=ancestors.published(entry.id) if ancestors else {},
                    agreed_there=agreed_there.get(entry.id, {}),
                    queue=queue,
                    now=now,
                )
                out.added += res.added
                out.updated += res.updated
                out.removed += res.removed
                out.conflicts += res.conflicts
                # A file that left its store between the check above and the write — a folder
                # made a symlink meanwhile — is refused by the writer, and named the same way.
                out.refused.update(
                    {f"{entry.path}/{rel}": OUTSIDE_THE_STORE for rel in res.refused}
                )
                if ancestors is not None:
                    # The records this home now holds as the peer does are what the two agree
                    # on, and the next divergence from this peer is measured from them.
                    ancestors.record(peer_id, entry.id, res.new_ancestors)
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
    queue: Optional[ConflictQueue] = None,
    ancestors: Optional[Ancestors] = None,
    now: str = "",
    codec=None,
) -> PullReport:
    """Pull and merge each peer's newest shard set the cursor hasn't consumed (see the module
    docstring on why only the newest).

    Advances the cursor only on a consumed (or payload-bad) seq; a held seq (a not-yet-servable
    prefix, an unknown entry, or a DB entry with no ``db_merger``) leaves the cursor where it
    is, so the peer's newest is pulled again next cycle. ``codec`` is the optional DAS-8 sync
    codec: when present every pulled object is decrypted before it is materialized, and a
    plaintext one is a permanent skip. ``ancestors`` is what this home last agreed on with each
    peer: each seq is merged against its peer's, and what the merge agrees on is written back
    before the cursor moves past the seq. Returns a :class:`PullReport` of per-seq outcomes.
    """
    report = PullReport()
    seen = cursor.seen()
    for peer in registry.peers(self_id):
        if not is_safe_record_id(peer.machine_id):
            # Its id names its folder of the remote (`registry.shard_prefix`): one that is not a
            # single plain name names some other folder, so nothing is pulled from it.
            report.peers_refused[peer.machine_id] = NOT_ONE_NAME
            continue
        if peer.seq <= int(seen.get(peer.machine_id, 0) or 0):
            continue
        outcome = _pull_one_seq(
            transport,
            home,
            peer.machine_id,
            peer.seq,
            db_merger,
            ancestors=ancestors,
            queue=queue,
            now=now,
            codec=codec,
            self_id=self_id,
        )
        if ancestors is not None:
            ancestors.save()
        outcome.advanced = cursor.record(peer.machine_id, peer.seq, outcome.verdict)
        report.outcomes.append(outcome)
    return report
