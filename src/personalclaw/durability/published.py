"""What this machine last published to its sync store, and which of its copies the store keeps.

Every sync cycle exports this machine's records whole (``shards.export_shards(for_sync=True)``),
and a publish writes that export as a new seq: one complete copy, about as large as the records.
Two rules keep the store from holding a copy per cycle forever.

**A copy is sent only when the records changed.** :func:`export_digest` is one sha over what an
export holds, read from the export itself: a store a sync merges record by record counts by what
two homes compare of each record (``conflicts.compared``), and every other store by its shards'
and database copies' sha256 in the manifest. Two exports of an unchanged home give the same
digest. What changes in this home by itself while nothing happens does not count: the manifest's
own time, an automation's run times (its heartbeat runs every minute), and the security log — this
machine's account of what happened on it, which those runs append to every minute. They ride the
next copy that is sent; counted, every export was new, and every sync sent a whole copy.
:class:`Published` keeps the digest of the copy last sent, here on this machine and never in the
shared registry — that is the one object an encrypted sync leaves readable, and a hash of what a
copy holds is not the store's to read.

**A copy a newer one replaced is removed** (:func:`superseded`). A peer reads one copy of each
machine — the newest the registry names when its cycle starts — so a copy stops being read once a
newer one lands, and a peer that started reading it just before has :data:`KEEP_PREVIOUS_SECS`
to finish. So the store keeps this machine's newest copy, and the one before it until the newest
has been there that long; every older one is removed. A read cut short anyway is never merged: an
export that lacks files is held whole (``shards.IncompleteExport``) and the next cycle reads the
newest. The newest is never removed, so a machine that has been away catches up from it.

Machine-local, under the sync root beside the pull cursor, which the home audit ignores, so it is
never exported. Clock-free: ``now`` is passed in, an ISO-8601 time, and a time that doesn't parse
removes nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from personalclaw.atomic_write import atomic_json_write
from personalclaw.durability import conflicts as conflicts_mod
from personalclaw.durability import inventory as inv
from personalclaw.durability import reconcile
from personalclaw.durability.shards import canonical_json

logger = logging.getLogger(__name__)

_FILE = "published.json"
_MANIFEST = "manifest.json"

#: The inventory entry whose shards are the security log (``inventory.INVENTORY``).
_SECURITY_LOG = "security_events"

#: How long the copy before the newest stays after the newest lands, so a machine that started
#: reading it just before can finish. A cycle reads one copy in well under this.
KEEP_PREVIOUS_SECS = 15 * 60


def _merged_by_record(entry: inv.StateEntry) -> bool:
    """Whether a sync merges *entry* record by record, comparing what two homes compare of each
    (the stores whose versions ``ancestors`` keeps)."""
    return (
        reconcile.handles_kind(entry.kind)
        and entry.merge in conflicts_mod.ID_KEYED_MERGES
        and not entry.machine_local
    )


def _compared_shas(entry: inv.StateEntry, export_dir: Path, rels: list[str]) -> list[list[str]]:
    """``[id, sha]`` of what two homes compare of each of *entry*'s records in the export, from
    its shards *rels*, sorted. Raises when one can't be read or parsed."""
    rows: list[dict] = []
    for rel in rels:
        for line in (export_dir / rel).read_bytes().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return sorted(
        [conflicts_mod.row_id(r), conflicts_mod.row_sha(conflicts_mod.compared(entry, r))]
        for r in reconcile.entity_rows(entry, rows)
    )


def export_digest(export_dir: Path) -> str:
    """One sha over what the export at *export_dir* holds (see the module docstring). ``""`` when
    its manifest can't be read: nothing matches that, so such an export is always sent."""
    export_dir = Path(export_dir)
    try:
        manifest = json.loads((export_dir / _MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(manifest, dict):
        return ""
    # The manifest lists shards by path, and databases in the order the export staged them, so
    # the same export always reads in the same order.
    held: list[list] = []
    by_record: dict[str, tuple[inv.StateEntry, list[str]]] = {}
    for record in manifest.get("shards") or []:
        rel, sha = str(record.get("path", "")), str(record.get("sha256", ""))
        entry_id = rel.split("/", 1)[0]
        if entry_id == _SECURITY_LOG:
            continue
        entry = inv.by_id(entry_id)
        if entry is not None and _merged_by_record(entry):
            by_record.setdefault(entry_id, (entry, []))[1].append(rel)
            continue
        held.append(["shard", rel, sha])
    for entry_id, (entry, rels) in sorted(by_record.items()):
        try:
            held.append(["records", entry_id, _compared_shas(entry, export_dir, rels)])
        except (OSError, ValueError, TypeError, AttributeError):
            return ""  # a shard that doesn't read: the export is sent, as an unreadable one is
    for record in manifest.get("databases") or []:
        held.append(["database", str(record.get("path", "")), str(record.get("sha256", ""))])
    return hashlib.sha256(canonical_json(held).encode("utf-8")).hexdigest()


def _when(stamp: str) -> datetime | None:
    """*stamp*, an ISO-8601 time, as an aware datetime; ``None`` when it doesn't parse."""
    try:
        moment = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def superseded(
    seqs: Iterable[int], *, newest: int, landed: Mapping[int, str], now: str
) -> list[int]:
    """Which of this machine's copies in the store (*seqs*) to remove, oldest first: every one
    older than the *newest* whose next copy landed at least :data:`KEEP_PREVIOUS_SECS` before
    *now* (*landed*: seq → when it landed). A next copy with no known landing time landed before
    this machine kept the times, which is long ago. Nothing when *now* doesn't parse."""
    moment = _when(now)
    if moment is None or newest <= 0:
        return []
    out: list[int] = []
    for seq in sorted(set(seqs)):
        if seq >= newest:
            continue
        stamp = landed.get(seq + 1, "")
        replaced = _when(stamp) if stamp else None
        if replaced is None or (moment - replaced).total_seconds() >= KEEP_PREVIOUS_SECS:
            out.append(seq)
    return out


@dataclass
class Published:
    """This machine's record of its own copies in the sync store: the seq and :func:`export_digest`
    of the newest it sent, and when each of its copies still there landed."""

    seq: int = 0
    digest: str = ""
    landed: dict[int, str] = field(default_factory=dict)

    @classmethod
    def load(cls, sync_root: Path) -> Published:
        """The record under *sync_root*; an empty one when there is none or it can't be read —
        the next export is then sent, which is the safe direction."""
        try:
            raw = json.loads((Path(sync_root) / _FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        landed: dict[int, str] = {}
        times = raw.get("landed")
        for seq, stamp in (times if isinstance(times, dict) else {}).items():
            try:
                landed[int(seq)] = str(stamp)
            except (TypeError, ValueError):
                continue
        try:
            seq = int(raw.get("seq", 0) or 0)
        except (TypeError, ValueError):
            seq = 0
        return cls(seq=seq, digest=str(raw.get("digest", "") or ""), landed=landed)

    def save(self, sync_root: Path) -> None:
        atomic_json_write(
            Path(sync_root) / _FILE,
            {
                "seq": self.seq,
                "digest": self.digest,
                "landed": {str(s): t for s, t in sorted(self.landed.items())},
            },
        )

    def stands(self, digest: str, *, remote_seq: int, remote_has_it: bool) -> bool:
        """Whether an export with *digest* is the copy the store already holds as this machine's
        newest: the same records as the copy last sent, the store's registry still naming that
        seq as this machine's (*remote_seq*), and its objects still there (*remote_has_it*). A
        store this machine has not sent that copy to — a new folder, another transport — reads
        another seq, so it is sent the copy."""
        return (
            bool(digest)
            and self.seq > 0
            and digest == self.digest
            and remote_seq == self.seq
            and remote_has_it
        )

    def record(self, seq: int, digest: str, *, now: str) -> None:
        """*seq*, an export with *digest*, landed at *now*."""
        self.seq, self.digest = seq, digest
        self.landed[seq] = now

    def keep_only(self, seqs: Iterable[int]) -> None:
        """Keep the landing times of *seqs* only — the copies still in the store."""
        wanted = set(seqs)
        self.landed = {s: t for s, t in self.landed.items() if s in wanted}
