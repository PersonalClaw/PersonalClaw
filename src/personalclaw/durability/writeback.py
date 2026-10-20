"""Apply merged rows back to the live on-disk store.

The sync cycle's last pure primitive: the inverse of ``shards.py``'s row *extraction*.
``shards.export_shards`` turned each inventory entry's on-disk form into a flat row list;
after :mod:`durability.merge` reconciles a peer's rows with the local ones, this writes the
merged set back into the live store in that entry's native shape, dispatched by its
inventory ``kind``.

Row shapes, matching the readers (``shards.read_entity_dir``, ``shards.read_json_file``):

* ``json_entity_dir`` — one file per row (``shards.row_file``): a JSON file for a row with
  ``data``, and any other file, as it was, for one with ``text`` or ``base64``. A **tombstone**
  row (a ``deleted_at`` marker) removes the entity's file instead — the file the read found under
  its id, a JSON file or any other — so a delete synced from a peer propagates to the live store
  rather than resurrecting the entity.
* ``json_file`` — a single row → the file at ``dest``; a tombstone removes the file.
* ``jsonl_append`` — rows are the raw event dicts, of a store that is one file.

**Only what changed, and only over what was read.** A write is given the :class:`shards.Read`
the caller merged against. A row that is the one read is not written: a pull used to rewrite
every file of a synced folder, so a write the store made to any of them between the pull's read
and its write was lost. And a file is replaced only while it is still as it was read
(compare-and-swap on its sha256): one another writer changed or created in between is left as it
now is, and named in ``moved``, so the caller takes it in again next time. An append-only stream
is only ever appended to — the rows it did not hold, after what it holds — so nothing written to
it in between can be lost. A row that names a file the store does not hold
(``shards.store_file``: a path outside the store, a database, runtime scratch) is never written.

**Nothing outside the store, and nothing through a link.** A row's file is written and removed
only at a name of the store's shape (``record_ids.is_safe_relative_path``): one that climbs out or
is absolute would carry another machine's write, or its delete, out of the home. Such a row is
refused, and named in ``refused``; a pull asks first (:func:`outside_the_store`) and takes nothing
of a change that names one. And only at a path ``durability.home_paths.home_path`` gives from the
store's folder down, which the caller took from it too: where the store holds a link on the way —
a folder of it that is a symbolic link, or a file with another name — the row is not written or
removed, and the link is named in ``linked`` (``durability.home_paths``).

``sqlite`` and ``tree`` are NOT handled here — the cycle merges DBs via the ATTACH-OR-IGNORE
path (``snapshot.py``) and a sync leaves trees alone — so routing one through
:func:`apply_rows` is a caller bug, raised loudly, mirroring :func:`merge.merge_rows`. Each file
is written atomically (temp file + rename) through the shared writer.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from personalclaw.atomic_write import atomic_write_bytes, open_streamed
from personalclaw.durability import inventory as inv
from personalclaw.durability.home_paths import LinkInTheWay, home_path
from personalclaw.durability.shards import (
    Read,
    canonical_json,
    row_bytes,
    row_file,
    store_file,
)
from personalclaw.record_ids import is_safe_relative_path

logger = logging.getLogger(__name__)

_TOMBSTONE_FIELD = "deleted_at"


@dataclass
class ApplyResult:
    """A reviewable tally of what an apply did to the live store."""

    written: int = 0  # entities/files/stream rows written
    removed: int = 0  # entities removed by a tombstone row
    skipped: int = 0  # rows that couldn't be applied (malformed, or not a file of the store)
    unchanged: int = 0  # rows that are what was read, so were not written
    #: Row ids whose file is not as it was read — another writer changed or made it since, or a
    #: folder is where it would be: left as it now is.
    moved: list[str] = field(default_factory=list)
    #: The files, inside the store's folder by name, that rows named outside it: never touched.
    refused: list[str] = field(default_factory=list)
    #: The files, inside the store's folder, the store holds behind a link, and the link in the way
    #: of each, by its path inside the folder: never written or removed.
    linked: dict[str, LinkInTheWay] = field(default_factory=dict)


def _is_tombstone(row: dict) -> bool:
    # A JSONL row carries deleted_at at the top level; an entity-dir row nests it under the
    # exporter's {"id", "data"} wrapper. Check both so a synced delete removes the file for
    # either shape (mirrors merge._field).
    if row.get(_TOMBSTONE_FIELD):
        return True
    data = row.get("data")
    return bool(isinstance(data, dict) and data.get(_TOMBSTONE_FIELD))


#: What :func:`_sha_now` says of a path where no file can be: a folder, or under a file. It is no
#: sha a read recorded, so nothing is written there.
_NOT_A_FILE = "not a file"


def _sha_now(path: Path) -> str | None:
    """The sha256 of *path*'s bytes now, ``None`` when there is no such file, or
    :data:`_NOT_A_FILE`."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
    except (IsADirectoryError, NotADirectoryError):
        return _NOT_A_FILE


def apply_rows(entry: inv.StateEntry, dest: Path, rows: list[dict], *, read: Read) -> ApplyResult:
    """Write the rows of ``rows`` that changed since ``read`` back to ``dest``, *entry*'s store,
    over files still as ``read`` found them (see the module docstring).

    ``dest`` is the store's path, which ``durability.home_paths.home_path`` gave. ``read`` is
    what the caller read of the store and merged against: ``shards.Read()`` for one it read
    nothing of, which writes a file only where there is none. Raises ``ValueError`` for
    ``sqlite``/``tree`` (handled by other paths), for an append-only store that is a folder of
    files, and for any unknown kind.
    """
    dest = Path(dest)
    kind = entry.kind
    if kind == inv.KIND_JSON_ENTITY_DIR:
        return _apply_entity_dir(entry, dest, rows, read)
    if kind == inv.KIND_JSON_FILE:
        return _apply_json_file(dest, rows, read)
    if kind == inv.KIND_JSONL_APPEND:
        return _apply_jsonl(entry, dest, rows, read)
    if kind in (inv.KIND_SQLITE, inv.KIND_TREE):
        raise ValueError(
            f"{kind!r} is not row-applied — the cycle merges sqlite via ATTACH-OR-IGNORE and "
            "a sync leaves trees alone; do not route it through apply_rows"
        )
    raise ValueError(f"unknown inventory kind {kind!r}")


def _swap(result: ApplyResult, rid: str, target: Path, expected: str | None) -> bool:
    """Whether *target* is still as it was read (*expected*: its sha then, ``None`` for none);
    when not, *rid* is recorded as moved."""
    if _sha_now(target) == expected:
        return True
    result.moved.append(rid)
    return False


def row_rel(row: dict) -> str | None:
    """The path, inside its store's folder, of the file *row* writes — or removes, for a
    tombstone — or ``None`` for a row with no string id, which names no file."""
    rid = row.get("id")
    if not isinstance(rid, str) or not rid:
        return None
    return f"{rid}.json" if _is_tombstone(row) else row_file(row)


def outside_the_store(rows: list[dict]) -> list[str]:
    """The files *rows* — an entity directory's — would write or remove outside it: a name that
    climbs out or is absolute. What :func:`apply_rows` refuses, asked before anything is written. A
    link on the way is the store's, not the name's, and is the link rule's (``linked``)."""
    rels = (row_rel(row) for row in rows)
    return [rel for rel in rels if rel is not None and not is_safe_relative_path(rel)]


def _apply_entity_dir(
    entry: inv.StateEntry, root: Path, rows: list[dict], read: Read
) -> ApplyResult:
    """One file per row at ``root/<row_file>``; a tombstone removes the entity's file."""
    result = ApplyResult()
    as_read = {str(r.get("id", "")): r for r in read.rows}
    for row in rows:
        rel = row_rel(row)
        if rel is None:
            result.skipped += 1
            logger.debug("apply_rows(entity_dir): row without a string id — skipped")
            continue
        rid = str(row["id"])
        if not is_safe_relative_path(rel):
            result.refused.append(rel)
            logger.warning("apply_rows: %s names %r, outside the store — refused", entry.id, rel)
            continue
        if not store_file(entry, rel):
            result.skipped += 1
            logger.warning(
                "apply_rows: %s names %r, not a file of the store — skipped", entry.id, rel
            )
            continue
        if _is_tombstone(row):
            here = as_read.get(rid)
            if here is None:
                continue  # nothing of it was read here, so nothing is removed
            rel = row_file(here)
            if not is_safe_relative_path(rel):
                result.refused.append(rel)
                continue
        try:
            target = home_path(root, rel)
        except LinkInTheWay as link:
            result.linked[rel] = link
            logger.warning("apply_rows: %s: %s — left as it is", entry.id, link)
            continue
        if _is_tombstone(row):
            if target.exists() and _swap(result, rid, target, read.shas.get(rid)):
                target.unlink()
                result.removed += 1
            continue
        if as_read.get(rid) == row:
            result.unchanged += 1
            continue
        if not _swap(result, rid, target, read.shas.get(rid)):
            continue
        atomic_write_bytes(target, row_bytes(row))
        result.written += 1
    return result


def _apply_json_file(dest: Path, rows: list[dict], read: Read) -> ApplyResult:
    """A single-document store: write the one row to ``dest`` (or remove it on a tombstone).
    More than one row is a merge bug (a json_file has exactly one id) — the last row wins,
    logged, rather than a silent half-write."""
    result = ApplyResult()
    if not rows:
        return result
    if len(rows) > 1:
        logger.warning("apply_rows(json_file): %d rows for a single-document store", len(rows))
    row = rows[-1]
    rid = str(row.get("id", "") or dest.name)
    expected = next(iter(read.shas.values()), None)
    if _is_tombstone(row):
        if dest.exists() and _swap(result, rid, dest, expected):
            dest.unlink()
            result.removed += 1
        return result
    if read.rows and read.rows[-1] == row:
        result.unchanged += 1
        return result
    if not _swap(result, rid, dest, expected):
        return result
    atomic_write_bytes(dest, row_bytes(row))
    result.written += 1
    return result


def _apply_jsonl(entry: inv.StateEntry, dest: Path, rows: list[dict], read: Read) -> ApplyResult:
    """Append to a one-file append-only stream the rows it did not hold when it was read, after
    what it holds now, in one write. Never a rewrite: a line appended by the store since the read
    stays where it is."""
    if inv.append_only_folder(entry):
        raise ValueError(
            f"{entry.id} is a folder of append-only files, whose rows name no file to go back to"
        )
    result = ApplyResult()
    held = {canonical_json(r) for r in read.rows}
    new = [r for r in rows if canonical_json(r) not in held]
    result.unchanged = len(rows) - len(new)
    if not new:
        return result
    with open_streamed(dest, "ab") as fh:
        fh.write("".join(canonical_json(r) + "\n" for r in new).encode("utf-8"))
    result.written = len(new)
    return result
