"""Reconcile a peer's rows into the live store (DAS-6c-ii-d).

The bridge that turns "I pulled a peer's shards" into "the peer's rows are now in my live
store", composing the three pure pieces already built:

    local rows  ←  read the entry's on-disk form the same way the exporter extracts it
    merged      ←  merge.merge_rows(entry.merge, local, remote, tombstones=entry.tombstones)
    live store  →  writeback.apply_rows(entry, dest, merged, read=what was read)

It is deliberately the ROW path only — the kinds whose merge is a deterministic row
reconciliation (`json_entity_dir`, `json_file`, `jsonl_append`). A `sqlite` entry is merged
by the ATTACH-OR-IGNORE path in ``snapshot.py``, and a `tree` entry (a folder of files) is not
in a sync at all (``inventory.shard_entries``), so :func:`reconcile_entry` DECLINES those
(returns a ``handled=False`` outcome) rather than raising — the cycle engine (the change above
this) reads that verdict and routes a database to its DB path. A row-mergeable entry that raises
mid-reconcile is caught and reported as a `payload-bad` verdict so one poison entry can't
abort the whole pull; the caller advances its cursor past it rather than looping.

Reads the local rows exactly as ``shards.export_shards`` would, so the merge sees the same
row shapes on both sides — the invariant that makes convergence hold (criterion 4). And writes
back only what the merge changed, over files still as they were read (``writeback.apply_rows``):
a file the store wrote in between is left for the next pull, and not agreed on until then. A peer
row that names a file the store does not hold (``shards.store_file`` — a path outside it, a
database, runtime scratch) is never taken in. An append-only store that is a folder of files
(``sessions/``, ``cron-history/``) is left as it is: its rows name no file to go back to, and the
pull wrote them into a file per year beside the store's own, a job or a chat named for the year.

A ``json_file`` that holds user records (``StateEntry.records``: the automations in
``triggers.json``, the inbox's items, the tags, …) is exported as one row, the whole file, but
reconciled record by record (:func:`entity_rows`): each record is an entity with its own id,
conflict and common ancestor, and the merged records are written back into the one file, each
where this home keeps it (:func:`_merged_document`), through the file's lock
(``record_files.rewrite``) — the lock the store's own writes hold, so neither writes over the
other. Merged as one row, the file this home already had won whole, so a peer's records reached
only a home that had none. A restore's merge and an import bring an archive's records in by the
same rule (:func:`bring_in`).

A ``replace_only`` entry is restored whole or not at all — the configuration, and the stores
whose every record is a grant this home's owner gave — so a sync leaves it exactly as it is. It
leaves a ``machine_local`` entry as it is too, and the files of an entity directory that stay on
each machine (``StateEntry.machine_local_within``): another machine's copy is never merged in,
whether or not a peer still sends one.

**An edit made on one machine reaches the other** (:func:`_one_side_changed`). A merge keyed by
id kept this home's copy of every record it already had, so a record edited on one machine stayed
as it was on the other for good. Each record is now merged three ways, against the version this
home and that peer last agreed on (``ancestors``, :mod:`durability.ancestors`): as agreed here and
not there, the peer edited it and its edit is taken in (``merge.forward``, then the store's
``StateEntry.edit_arrives``); as agreed there and not here, this home edited it and it stays, even
where the peer's ``updated_at`` reads later; changed on both, it is a conflict for review. A
peer's copy that is a version this home published after the agreement is this home's own edit
handed back, so the peer is behind (:func:`_in_common`). A restore's merge and an import have no
agreement to measure from, so a record this home has stays as it is there.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from personalclaw import record_files
from personalclaw.durability import conflicts as conflicts_mod
from personalclaw.durability import inventory as inv
from personalclaw.durability import writeback
from personalclaw.durability.cursor import CONSUMED, PAYLOAD_BAD
from personalclaw.durability.merge import MergeResult, _is_tombstone, forward, merge_rows
from personalclaw.durability.shards import (
    Read,
    _jsonl_rows_by_year,
    read_entity_dir,
    read_json_file,
    row_file,
    store_file,
)

logger = logging.getLogger(__name__)

# The row-mergeable kinds this bridge handles; everything else is another path's job.
_ROW_KINDS = frozenset({inv.KIND_JSON_ENTITY_DIR, inv.KIND_JSON_FILE, inv.KIND_JSONL_APPEND})


@dataclass
class ReconcileResult:
    """The outcome of reconciling one entry — a consume verdict the cursor understands.

    ``handled`` is False when the entry is not a row-merge kind (sqlite/tree) — the caller
    routes it elsewhere and does NOT treat that as consumed. ``verdict`` is the
    cursor verdict for a handled entry: ``consumed`` on a clean merge, ``payload-bad`` when
    the entry's rows were structurally unusable (advance past it, don't loop).
    """

    entry_id: str
    handled: bool = True
    verdict: str = CONSUMED
    added: int = 0
    updated: int = 0
    removed: int = 0
    detail: str = ""
    #: Both-sides-edited divergences recorded for review this reconcile. Their
    #: remote rows were HELD — the local rows are byte-identical to before.
    conflicts: int = 0
    #: The files, by their path inside the store, that the peer's rows named outside it
    #: (``writeback.ApplyResult.refused``): never written.
    refused: list[str] = dataclass_field(default_factory=list)
    #: ``entity id → content sha`` for the ids where the merge landed on the row the PEER
    #: also holds (converged, the peer's edit taken in, or the remote won) — the only shas
    #: that are evidence of a common ancestor. Excludes every held (conflicted) id, and every
    #: id where the LOCAL row won: the peer has not seen that row yet, so claiming agreement on
    #: it would read the peer's older copy as an edit made there. The two agree on it once this
    #: home pulls a seq of the peer's that holds it.
    new_ancestors: dict[str, str] = dataclass_field(default_factory=dict)


def read_local(entry: inv.StateEntry, src: Path) -> Read:
    """The entry's current on-disk rows, read the same way the exporter extracts them, so both
    sides of the merge speak the same row shape, with what each file held when it was read (what
    a write compares against). A missing store is no rows."""
    if entry.kind == inv.KIND_JSON_ENTITY_DIR:
        return read_entity_dir(entry, src)
    if entry.kind == inv.KIND_JSON_FILE:
        return read_json_file(entry, src)
    if entry.kind == inv.KIND_JSONL_APPEND:
        files = [src] if src.is_file() else (sorted(src.rglob("*.jsonl")) if src.is_dir() else [])
        rows: list[dict] = []
        for path in files:
            for _year, bucket in _jsonl_rows_by_year(path).items():
                rows.extend(bucket)
        return Read(rows=rows)
    return Read()  # non-row kind — never reached (caller checks handles_kind first)


def read_local_rows(entry: inv.StateEntry, src: Path) -> list[dict]:
    """The rows of :func:`read_local`. Public because the conflict review reads the same rows for
    the same reason: a resolution substitutes one row into this exact set, so reading it any other
    way would let a review write reshape the store."""
    return read_local(entry, src).rows


def _peer_rows(entry: inv.StateEntry, rows: list[dict]) -> list[dict]:
    """*rows* of a peer, but what this home never takes in by a sync: the files that stay on each
    machine, and a row of an entity directory that names no file of the store."""
    rows = _without_what_stays_here(entry, rows)
    if entry.kind != inv.KIND_JSON_ENTITY_DIR:
        return rows
    kept = []
    for row in rows:
        rel = f"{row.get('id', '')}.json" if _is_tombstone(row) else row_file(row)
        if store_file(entry, rel):
            kept.append(row)
        else:
            logger.warning("reconcile: %s: a peer's row names %r, not a file of it", entry.id, rel)
    return kept


def handles_kind(kind: str) -> bool:
    """Whether :func:`reconcile_entry` owns this inventory kind (a row-merge kind)."""
    return kind in _ROW_KINDS


def outside_their_store(home: Path, entry: inv.StateEntry, rows: list[dict]) -> list[str]:
    """The files a peer's *rows* of *entry* would write or remove outside the store in *home*
    (``writeback.outside_the_store``), by their path inside it. A pull asks before it takes in
    anything of a change, and takes in nothing of one that names any; only an entity directory's
    rows name a file each."""
    if entry.kind != inv.KIND_JSON_ENTITY_DIR:
        return []
    return writeback.outside_the_store(Path(home) / entry.path, rows)


def entity_rows(entry: inv.StateEntry, rows: list[dict]) -> list[dict]:
    """The entities a sync reconciles among *rows* — the exporter's rows for *entry*, this home's
    or a peer's: the rows themselves, or for a store of records (``StateEntry.records``) the
    records its one document holds.

    Only records with an id: one without cannot be told from another, so it is never merged. This
    home's stays where it is in the file (:func:`_merged_document`); a peer's is not taken in.
    """
    if entry.records is None:
        return rows
    out: list[dict] = []
    for row in rows:
        out.extend(_identified(entry, row.get("data")))
    return out


def _identified(entry: inv.StateEntry, document: Any) -> list[dict]:
    """The records with an id in *document*, one of *entry*'s files (none for another shape)."""
    assert entry.records is not None
    return _with_ids(entry.records.records(document) or [])


def _with_ids(records: list) -> list[dict]:
    """The records that have an id: the only ones anything can tell apart."""
    return [r for r in records if isinstance(r, dict) and record_files.record_id(r)]


def is_record(entry: inv.StateEntry, row: object) -> bool:
    """Whether *row* can be written as one of the records of *entry* (a store of records): it has
    an id, and is not the store's whole file. A conflict recorded before the store was reconciled
    record by record holds the whole file as each version — the exporter's one row, named for the
    file — and writing that into the file as one record would break it."""
    rid = record_files.record_id(row)
    return bool(rid) and rid != Path(entry.path).name


def _here(entry: inv.StateEntry, document: Any) -> list:
    """This home's records, in its file's order — none when it has no file. Raises when the file
    holds another shape: a write built without it would replace records it could not see, so the
    file is left for its owner to inspect, as the store's own reader leaves it."""
    assert entry.records is not None
    if document is None:
        return []
    held = entry.records.records(document)
    if held is None:
        raise ValueError(f"{entry.path} holds no records here, so it is left as it is")
    return held


def _merged_document(
    entry: inv.StateEntry,
    document: Any,
    peer_document: Any,
    arrived_order: list[str],
    merged: list[dict],
) -> Any:
    """*document* — this home's file, or None — with *merged* (its records after a merge) in it,
    or None when that is the file exactly as it is.

    Each record where this home keeps it, and the ones that arrived after them in the order the
    peer keeps them, so a sync never reorders the list a person sees. A record the merge did not
    take — one with no id, or a second with an id already placed — stays as it is. A home with no
    file gets the peer's around the records, as its store writes one.
    """
    assert entry.records is not None
    by_id = {record_files.record_id(r): r for r in merged if record_files.record_id(r)}
    items: list[object] = []
    placed: set[str] = set()
    for item in _here(entry, document):
        rid = record_files.record_id(item)
        if rid in by_id and rid not in placed:
            items.append(by_id[rid])
            placed.add(rid)
        else:
            items.append(item)
    for rid in arrived_order:
        if rid in by_id and rid not in placed:
            items.append(by_id[rid])
            placed.add(rid)
    if document is None and not items:
        return None
    updated = entry.records.document(document if document is not None else peer_document, items)
    return None if updated == document else updated


def take_in(
    entry: inv.StateEntry, dest: Path, entity_id: str, there: dict
) -> tuple[writeback.ApplyResult, bool]:
    """Write *there* — another machine's version of *entity_id*, or a merge drafted from it — into
    *entry*'s store at *dest* as a sync takes one in, and say whether it was an edit.

    Over the row this home has, it is the edit only the other machine made (:func:`_edited_there`):
    what a person makes of the row is the other machine's, and this home's own part stays — an
    automation's switch and what happened to it here, and its grant, which keeps only what the
    edit still runs as it ran here. A row this home does not have (or deleted) comes in by the
    store's arrival rule (``StateEntry.arrives``). The conflict review's write: the row this home
    has is read at the write, under the file's lock for a store of records and through the same
    guard as a sync's (:func:`_here`), never the copy the conflict recorded when it was found. In
    any other store the one file is written only while it is still as it was read: one that
    changed in between is left as it is, in ``moved``.
    """

    def taken(here: dict | None) -> tuple[dict, bool]:
        if here is None or _is_tombstone(here):
            return (there if entry.arrives is None else entry.arrives(there)), False
        return _edited_there(entry, here, there), True

    if entry.records is None:
        read = read_local(entry, dest)
        here = next((r for r in read.rows if conflicts_mod.row_id(r) == entity_id), None)
        row, edited = taken(here)
        # The store's rows with this one in place of this home's; the write is only what changed.
        out = [r for r in read.rows if conflicts_mod.row_id(r) != entity_id]
        out.append(row)
        return writeback.apply_rows(entry, dest, out, read=read), edited
    shape = entry.records
    edited = False

    def change(document: Any) -> Any:
        nonlocal edited
        items: list[object] = []
        placed = False
        for item in _here(entry, document):
            if record_files.record_id(item) == entity_id:
                if not placed:
                    row, edited = taken(item if isinstance(item, dict) else None)
                    items.append(row)
                    placed = True
                continue
            items.append(item)
        if not placed:
            row, edited = taken(None)
            items.append(row)
        return shape.document(document, items)

    wrote = record_files.rewrite(dest, change)
    return writeback.ApplyResult(written=1 if wrote else 0), edited


def bring_in_folder(home: Path, entry: inv.StateEntry, archived: Path) -> int:
    """Bring the files of *archived* — *entry*'s folder in an archive: a snapshot, an export — into
    this home's store, by the rule a sync brings another machine's in. Returns how many came.

    Each file of the store (``shards.store_file``) this home does not have arrives, a JSON row by
    the store's arrival rule (``StateEntry.arrives``), written only where this home still has none;
    the ones it has stay exactly as they are, as a merge only fills in what the home lacks. The
    files that stay on each machine (``StateEntry.machine_local_within``) never come in. Copied
    whole, an archive's workflow brought the steps its machine's owner allowed there to run here
    unasked, and its agent runtime config the tools that machine's agent runs without asking.
    """
    read = read_local(entry, Path(home) / entry.path)
    held = {conflicts_mod.row_id(r) for r in read.rows}
    arriving = _as_they_arrive(
        entry,
        [
            row
            for row in _peer_rows(entry, read_entity_dir(entry, archived).rows)
            if conflicts_mod.row_id(row) not in held
        ],
    )
    if not arriving:
        return 0
    applied = writeback.apply_rows(entry, Path(home) / entry.path, arriving, read=read)
    return applied.written


def holds(entry: inv.StateEntry, dest: Path, entity_id: str) -> bool:
    """Whether this home's store at *dest* has *entity_id* (and has not deleted it): whether taking
    another machine's version of it would be an edit (:func:`take_in`)."""
    rows = entity_rows(entry, read_local_rows(entry, dest))
    return any(conflicts_mod.row_id(r) == entity_id and not _is_tombstone(r) for r in rows)


def bring_in(home: Path, entry: inv.StateEntry, archived: Path) -> int:
    """Bring the records of *archived* — *entry*'s file in an archive: a snapshot, an export —
    into this home's store, by the rule a sync brings another machine's in. Returns how many came.

    One record at a time, by its ``id``: each one this home does not have arrives, by the store's
    arrival rule (``StateEntry.arrives``), after the ones it has, which stay exactly as they are —
    a merge only fills in what the home lacks. A home with no store gets the archive's, every
    record in it brought in by the same rule. Written through the file's lock
    (``record_files.rewrite``), which the store's own writes hold. Raises ``ValueError`` when
    either file cannot be read or holds another shape, leaving this home's as it is.
    """
    assert entry.records is not None
    shape = entry.records
    archived_document = record_files.read(archived)
    if shape.records(archived_document) is None:
        raise ValueError(f"the archive's {entry.path} holds no records, so nothing came from it")
    arriving = _identified(entry, archived_document)
    came = 0

    def change(document: Any) -> Any:
        nonlocal came
        here = _here(entry, document)
        known = {record_files.record_id(r) for r in here if record_files.record_id(r)}
        added: list[object] = []
        for record in arriving:
            rid = record_files.record_id(record)
            if rid in known:
                continue
            known.add(rid)
            added.append(record if entry.arrives is None else entry.arrives(record))
        came = len(added)
        if document is not None and not added:
            return None
        return shape.document(document if document is not None else archived_document, here + added)

    record_files.rewrite(Path(home) / entry.path, change)
    return came


def reconcile_entry(
    home: Path,
    entry: inv.StateEntry,
    remote_rows: list[dict],
    *,
    ancestors: Optional[Mapping[str, str]] = None,
    published: Optional[Mapping[str, Sequence[str]]] = None,
    agreed_there: Optional[Mapping[str, str]] = None,
    queue: Optional[conflicts_mod.ConflictQueue] = None,
    now: str = "",
) -> ReconcileResult:
    """Merge ``remote_rows`` into ``entry``'s live store under ``home`` and write the result
    back. Returns a :class:`ReconcileResult` carrying the cursor verdict.

    Declines (``handled=False``) a non-row kind — the cycle routes sqlite via ATTACH-IGNORE,
    and a sync carries no tree. Consumes a ``replace_only`` entry and writes nothing: it is
    restored whole or not at all, and never merged (``merge.merge_rows`` refuses one). A row
    kind that throws mid-merge is caught and reported ``payload-bad`` so a single bad entry
    advances the cursor past itself rather than wedging every later seq (§4.1).

    **Conflict handling (DAS-7, §4.2).** With ``ancestors`` (what this home and the peer last
    agreed on in this family, :meth:`ancestors.Ancestors.of`) and a ``queue``, every id whose
    local AND remote row both moved since the ancestor is recorded for review and then
    **HELD**: its remote row is dropped before the merge, so the local bytes are untouched and
    the local version stays authoritative until a human resolves. Held ids also keep their old
    ancestor, so the conflict re-detects next cycle instead of quietly self-resolving. A
    conflicted entry is still ``consumed`` — the divergence is durably recorded, so re-pulling
    the same seq forever would add nothing and would wedge the cursor.

    Every other id one side alone moved is that side's edit (:func:`_one_side_changed`): the
    peer's is taken in, this home's stays. ``published`` is what this home published of each
    record, oldest first (:meth:`ancestors.Ancestors.published`): a peer's copy that is one of
    those, newer than the agreement, is this home's own edit handed back (:func:`_in_common`).
    ``agreed_there`` is what the peer's copy says it last agreed on with this home, record by
    record (``shards.ImportResult.agreements``): one of those versions, newer than this home's
    own agreement, is one the peer took from here, and its edit since is measured from it.
    """
    if not handles_kind(entry.kind):
        return ReconcileResult(entry.id, handled=False, detail=f"non-row kind {entry.kind}")
    if entry.merge == inv.MERGE_REPLACE_ONLY:
        return ReconcileResult(entry.id, detail="restored whole or not at all; left as it is")
    if entry.machine_local:
        return ReconcileResult(entry.id, detail="this machine's own; left as it is")
    if inv.append_only_folder(entry):
        return ReconcileResult(
            entry.id, detail="a folder of append-only files; left as it is (rows name no file)"
        )
    dest = Path(home) / entry.path
    remote_rows = _peer_rows(entry, remote_rows)
    remote = entity_rows(entry, remote_rows)
    bases, handed_back = _in_common(
        entry, remote, ancestors or {}, published or {}, agreed_there or {}
    )
    outcome: dict[str, Any] = {}

    def merge_into(local: list[dict]) -> MergeResult:
        held, recorded = _record_conflicts(entry, local, remote, bases, queue, now)
        effective_remote = (
            [r for r in remote if conflicts_mod.row_id(r) not in held] if held else remote
        )
        outcome.update(held=held, recorded=recorded, effective_remote=effective_remote)
        ahead, behind = _one_side_changed(entry, local, effective_remote, bases)
        decided = ahead.keys() | behind
        merged = merge_rows(
            entry.merge,
            [ahead.get(conflicts_mod.row_id(r), r) for r in local] if ahead else local,
            _as_they_arrive(
                entry, [r for r in effective_remote if conflicts_mod.row_id(r) not in decided]
            ),
            tombstones=entry.tombstones,
            dedup_key="id",
        )
        merged.updated += len(ahead)
        merged.kept -= len(ahead)
        return merged

    try:
        if entry.records is not None:
            peer_document = next((r.get("data") for r in remote_rows if "data" in r), None)

            def change(document: Any) -> Any:
                merged = merge_into(_with_ids(_here(entry, document)))
                outcome["merged"] = merged
                arrived_order = [conflicts_mod.row_id(r) for r in outcome["effective_remote"]]
                return _merged_document(entry, document, peer_document, arrived_order, merged.rows)

            record_files.rewrite(dest, change)
            removed, moved, refused = 0, [], []
        else:
            # This machine's own files of the folder are not the merge's: left out of what it
            # writes back, so the pull never touches them. The write is only what the merge
            # changed, over files still as they were read.
            read = read_local(entry, dest)
            merged = merge_into(_without_what_stays_here(entry, read.rows))
            outcome["merged"] = merged
            applied = writeback.apply_rows(entry, dest, merged.rows, read=read)
            removed, moved, refused = applied.removed, applied.moved, applied.refused
    except Exception as exc:  # noqa: BLE001 — one bad entry must not abort the whole pull
        logger.warning("reconcile: %s failed (%s) — advancing past it", entry.id, exc)
        return ReconcileResult(entry.id, verdict=PAYLOAD_BAD, detail=str(exc))
    merged = outcome["merged"]
    held, recorded = outcome["held"], outcome["recorded"]
    detail = f"+{merged.added} ~{merged.updated} -{removed}"
    if recorded or held:
        detail += f" !{recorded} conflict(s), {len(held)} id(s) held local"
    if moved:
        # Changed here while the pull merged it: left as it is now, and not agreed on, so the
        # next pull takes the peer's in again against it.
        detail += f" {len(moved)} left for the next pull (changed here meanwhile)"
        held = held | set(moved)
    if refused:
        detail += f" {len(refused)} refused (outside the store)"
    return ReconcileResult(
        entry.id,
        verdict=CONSUMED,
        added=merged.added,
        updated=merged.updated,
        removed=removed,
        detail=detail,
        conflicts=recorded,
        refused=list(refused),
        new_ancestors={
            **{rid: sha for rid, sha in handed_back.items() if rid not in held},
            **_agreed_shas(entry, outcome["effective_remote"], merged.rows, held),
        },
    )


def _without_what_stays_here(entry: inv.StateEntry, rows: list[dict]) -> list[dict]:
    """*rows* of *entry* but its files that stay on each machine
    (``StateEntry.machine_local_within``)."""
    if not entry.machine_local_within:
        return rows
    return [r for r in rows if not inv.stays_here(entry, conflicts_mod.row_id(r))]


def _in_common(
    entry: inv.StateEntry,
    remote: list[dict],
    ancestors: Mapping[str, str],
    published: Mapping[str, Sequence[str]],
    agreed_there: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """The version this home and the peer have in common of each record: ``(bases, handed
    back)``.

    Their last agreement (``ancestors``), unless the peer has since held a version this home
    published after it (``published``, oldest first): the peer took this home's edit, and this home
    may have edited the record again since. The two have that version in common, which is newer
    than the agreement: measured from the agreement, the peer's copy read as an edit made there,
    and every second edit in a row was a conflict. The peer's copy shows it two ways. It is that
    version — handed back before this home pulled it; ``handed back`` is those records,
    ``id → sha``, which the two now agree on. Or the peer says it agreed on that version
    (``agreed_there``) and has changed the record since: a peer is read at its newest copy only, so
    the copy that held the version it took is not read. A version this home never published is no
    such evidence, whatever a copy says.
    """
    bases = dict(ancestors)
    handed_back: dict[str, str] = {}
    for row in remote:
        rid = conflicts_mod.row_id(row)
        agreed = bases.get(rid)
        versions = list(published.get(rid) or [])
        if not agreed or agreed not in versions:
            continue
        since = versions[len(versions) - versions[::-1].index(agreed) :]
        sha = conflicts_mod.row_sha(conflicts_mod.compared(entry, row))
        if sha in since:
            bases[rid] = handed_back[rid] = sha
        elif agreed_there.get(rid) in since:
            bases[rid] = agreed_there[rid]
    return bases, handed_back


def held_shas(home: Path, entry: inv.StateEntry) -> dict[str, str]:
    """``entity id → sha`` of what two homes compare of every record this home holds of *entry*
    (:func:`conflicts.compared`): what it publishes, which the sync records after each export
    (:meth:`ancestors.Ancestors.publish`)."""
    rows = entity_rows(entry, read_local_rows(entry, Path(home) / entry.path))
    return {
        conflicts_mod.row_id(row): conflicts_mod.row_sha(conflicts_mod.compared(entry, row))
        for row in _without_what_stays_here(entry, rows)
        if conflicts_mod.row_id(row)
    }


def _one_side_changed(
    entry: inv.StateEntry,
    local: list[dict],
    remote: list[dict],
    ancestors: Mapping[str, str],
) -> tuple[dict[str, dict], set[str]]:
    """The records one side alone changed since this home and the peer last agreed on them
    (``ancestors``): ``(ahead, behind)``.

    ``ahead`` — ``id → row`` for each one the peer edited, as this home writes it with the edit
    taken in (:func:`_edited_there`). ``behind`` — the ids this home edited, which the peer still
    holds as agreed: the peer's row is not merged for them, so this home's edit stays, even where
    the peer's ``updated_at`` reads later — as it does whenever the machine that made the edit
    has a clock behind the other's.

    A tombstone on either side is a deletion, which the merge's tombstone rule decides, and an id
    held under a conflict is not among ``remote``. (A file that is one machine's own account of
    itself — what it spent, what it last ran — never reaches this: it is ``machine_local``.)
    """
    if not ancestors or entry.merge not in conflicts_mod.ID_KEYED_MERGES:
        return {}, set()
    here = {conflicts_mod.row_id(r): r for r in local if conflicts_mod.row_id(r)}
    ahead: dict[str, dict] = {}
    behind: set[str] = set()
    for there in remote:
        rid = conflicts_mod.row_id(there)
        mine = here.get(rid)
        agreed = ancestors.get(rid)
        if mine is None or not agreed or _is_tombstone(mine) or _is_tombstone(there):
            continue
        mine_sha = conflicts_mod.row_sha(conflicts_mod.compared(entry, mine))
        there_sha = conflicts_mod.row_sha(conflicts_mod.compared(entry, there))
        if mine_sha == there_sha:
            continue
        if mine_sha == agreed:
            ahead[rid] = _edited_there(entry, mine, there)
        elif there_sha == agreed:
            behind.add(rid)
    return ahead, behind


def _edited_there(entry: inv.StateEntry, here: dict, there: dict) -> dict:
    """*here* — a row this home has — with the edit the peer made to it (*there*) taken in: the
    part two homes compare is the peer's, the rest stays this home's (``merge.forward``), and then
    whatever follows here from the edit, by the store's rule (``StateEntry.edit_arrives``)."""
    edited = forward(here, there, compared=entry.compared)
    return edited if entry.edit_arrives is None else entry.edit_arrives(here, edited)


def _as_they_arrive(entry: inv.StateEntry, remote_rows: list[dict]) -> list[dict]:
    """The peer's rows as this home's store takes them in (``StateEntry.arrives``). A tombstone is
    a deletion rather than a row, and passes as it is.

    Only the merge sees them: the conflict check and :func:`_agreed_shas` read the rows the peer
    published, through what two homes compare of them (``conflicts.compared``)."""
    if entry.arrives is None:
        return remote_rows
    return [row if _is_tombstone(row) else entry.arrives(row) for row in remote_rows]


def _agreed_shas(
    entry: inv.StateEntry, remote_rows: list[dict], merged_rows: list[dict], held: set[str]
) -> dict[str, str]:
    """The ids whose merged row is the row the peer published, as two homes compare it
    (``conflicts.compared``) — the only ones we can honestly call a common ancestor (see
    ``ReconcileResult.new_ancestors``).

    For a store that holds what is one home's, a row it brought in is not the peer's byte for byte
    (``StateEntry.arrives``), and what both homes do with it afterwards changes it on each; what a
    person makes of it is the same on both, which is what the next divergence is measured from."""
    remote_shas = {
        conflicts_mod.row_id(r): conflicts_mod.row_sha(conflicts_mod.compared(entry, r))
        for r in remote_rows
        if conflicts_mod.row_id(r)
    }
    out: dict[str, str] = {}
    for row in merged_rows:
        rid = conflicts_mod.row_id(row)
        if not rid or rid in held:
            continue
        sha = conflicts_mod.row_sha(conflicts_mod.compared(entry, row))
        if remote_shas.get(rid) == sha:
            out[rid] = sha
    return out


def _record_conflicts(
    entry: inv.StateEntry,
    local: list[dict],
    remote_rows: list[dict],
    ancestors: Optional[Mapping[str, str]],
    queue: Optional[conflicts_mod.ConflictQueue],
    now: str,
) -> tuple[set[str], int]:
    """Detect + queue this entry's both-sides-edited divergences.

    Returns ``(held ids, newly recorded count)``. Held is the union of what was detected now
    and what is still unresolved in the queue from an earlier cycle — "local stays
    authoritative until resolved" has to survive across cycles, not just the cycle that
    detected the conflict. Without a queue nothing is held: a caller that cannot record a
    conflict must not silently suppress a remote row either (the merge stays as it was).
    """
    if queue is None:
        return set(), 0
    detected = conflicts_mod.detect_conflicts(entry, local, remote_rows, ancestors or {}, now=now)
    recorded = 0
    for rec in detected:
        if queue.record(rec):
            recorded += 1
            logger.warning(
                "reconcile: %s/%s diverged on both sides — queued for review (local held)",
                entry.id,
                rec.entity_id,
            )
    held = {rec.entity_id for rec in detected} | queue.held_ids(entry.id)
    return held, recorded
