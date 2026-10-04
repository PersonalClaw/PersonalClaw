"""A JSON file of records that more than one writer writes: one lock, and no write lost.

A store kept in one JSON file — a list of records, or a document that holds one — is written by
the store itself and, from outside it, by a sync that brings another machine's records in and by
a restore's or an import's merge. Two rules keep either from losing the other's write:

* **One lock.** Every write is a read-modify-write under the file's lock (:func:`locked`): a lock
  file beside it, so the rename that replaces the file cannot invalidate a lock held on it. No
  writer replaces the file with a document built from a copy another writer has since changed.
  :func:`rewrite` is that shape for a writer outside the store.
* **What another writer wrote stays.** A store that holds its records in memory between writes
  knows the file as it last read or wrote it (:class:`Kept`). When it writes (:func:`written`),
  a record another writer added, changed or removed since then is written as that writer left it,
  unless the store changed the same record itself; and it takes those records in whenever it sees
  that the file has moved (:func:`taken_in`). Writing the list it read before they came would
  put back what was there before, and nothing would say so.

Every record is told apart by its ``id``; one without a string id is nobody's to merge.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_json_write

logger = logging.getLogger(__name__)


class Unreadable(ValueError):
    """The file is there and holds no document this can read. A write built without it would
    replace records it could not see, so the file is left as it is for its owner to inspect."""


@dataclass(frozen=True)
class Shape:
    """Where a JSON document keeps its records.

    ``key`` is the list's key in the document (``triggers.json``'s ``triggers``), or ``""`` when
    the document is itself the list (``tags.json``). A document that holds its records another
    way names ``split`` (the document's records in order, or None for a document that is not the
    store's) and ``join`` (the document *base* holding the records given, in that order; *base*
    is None for a store that has no file yet).
    """

    key: str = ""
    split: Callable[[Any], list | None] | None = None
    join: Callable[[Any, list], Any] | None = None

    def records(self, document: Any) -> list | None:
        """The records *document* holds, in its order; None when it is not this store's."""
        if self.split is not None:
            return self.split(document)
        if not self.key:
            return document if isinstance(document, list) else None
        held = document.get(self.key) if isinstance(document, dict) else None
        return held if isinstance(held, list) else None

    def document(self, base: Any, records: list) -> Any:
        """*base* holding *records* in their place, the rest of it as it is."""
        if self.join is not None:
            return self.join(base, records)
        if not self.key:
            return list(records)
        return {**(base if isinstance(base, dict) else {}), self.key: list(records)}


def lock_path(path: Path) -> Path:
    """The lock beside *path*: ``.<stem>.lock`` (``triggers.json`` → ``.triggers.lock``)."""
    return path.with_name(f".{path.stem}.lock")


@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    """Hold *path*'s lock, blocking until it is free. Advisory, across processes and threads;
    not re-entrant, so nothing called while it is held may take it again. Opened by the one
    opener of a lock (``durability.home_paths.open_lock``), which refuses a link at the lock's
    name, so nothing is written while one is there."""
    from personalclaw.durability.home_paths import open_lock  # lazy: it imports this module

    path.parent.mkdir(parents=True, exist_ok=True)
    with open_lock(lock_path(path)) as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read(path: Path) -> Any:
    """The document in *path*, or None when there is no file. Raises :class:`Unreadable` for a
    file that is there and is not JSON."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Unreadable(f"{path.name} could not be read here, so it is left as it is") from exc
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Unreadable(f"{path.name} is not JSON here, so it is left as it is") from exc


def rewrite(path: Path, change: Callable[[Any], Any]) -> bool:
    """Apply *change* to *path*'s document under its lock, the document re-read first — the one
    way a writer outside the store writes it. *change* gets the document (None when there is no
    file) and returns the one to write, or None to leave the file exactly as it is. Returns
    whether it wrote. Raises :class:`Unreadable` (before *change* runs) for a file it cannot
    read. Written by the one JSON writer (``atomic_write.atomic_json_write``)."""
    with locked(path):
        updated = change(read(path))
        if updated is None:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(path, updated)
        return True


def record_id(record: object) -> str:
    """A record's ``id``, or ``""`` for one without a string id."""
    rid = record.get("id") if isinstance(record, dict) else None
    return rid if isinstance(rid, str) else ""


def stamp(path: Path) -> tuple[int, int, int] | None:
    """What says the file changed: its inode, size and modification time (an atomic write is a
    new inode), or None when there is none. Taken before a store reads the file, so a write that
    lands while it reads shows as a change the next time it looks."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


class Kept:
    """The file as a store that holds its records in memory last read or wrote it: each record as
    it was then, in the store's own form, and the file's :func:`stamp`.

    *normalize* turns a record as it is in the file into the store's own form (what it writes
    back after reading it), and raises for one the store cannot hold. Given, a record another
    writer wrote in another spelling is not taken for a change, and a record the store cannot
    hold is left out of what it takes in, as its own reader leaves it out.
    """

    def __init__(self, normalize: Callable[[dict], dict] | None = None) -> None:
        self._normalize = normalize
        self._base: dict[str, str] = {}
        self._stamp: tuple[int, int, int] | None = None
        #: The store's last write put there records it does not hold yet (:func:`written`).
        self._behind = False

    def took(self, at: tuple[int, int, int] | None, records: Iterable[object]) -> None:
        """The store has read the file (stamped *at*, before the read), which held *records*."""
        base: dict[str, str] = {}
        for record in records:
            rid = record_id(record)
            if rid and rid not in base and isinstance(record, dict):
                form = self._normalized(record)
                if form is not None:
                    base[rid] = form
        self._base = base
        self._stamp = at
        self._behind = False

    def wrote(self, path: Path, held: Iterable[object], *, behind: bool) -> None:
        """The store wrote *path* holding *held*, in its form; *behind*, with another writer's
        records on top of them, for it to take in."""
        base: dict[str, str] = {}
        for record in held:
            rid = record_id(record)
            if rid and rid not in base:
                base[rid] = _canonical(record)
        self._base = base
        self._stamp = stamp(path)
        self._behind = behind

    def behind(self, path: Path) -> bool:
        """Whether *path* holds what the store has not taken in: another writer wrote it since
        the store last read or wrote it."""
        return self._behind or stamp(path) != self._stamp

    def _normalized(self, record: dict) -> str | None:
        """*record* in the store's form, or None for one it cannot hold."""
        if self._normalize is None:
            return _canonical(record)
        try:
            return _canonical(self._normalize(record))
        except Exception:  # noqa: BLE001 — a record the store cannot hold is left out, as it is
            return None

    def _form(self, record: dict) -> str | None:
        """*record*, as it is in the file, in the store's form: as it is when it is spelled as
        the store last saw it, which is the store's own spelling."""
        raw = _canonical(record)
        if self._normalize is None or raw == self._base.get(record_id(record)):
            return raw
        return self._normalized(record)

    def moves(self, on_disk: Iterable[object], held: Mapping[str, dict]) -> dict[str, dict | None]:
        """What another writer changed in the file since the store read or wrote it: each id it
        added or changed, with the record as it is in the file now, and each it removed, with
        None — leaving out every id the store changed itself since then (added, edited or
        removed), whose change stands. *held* is what the store holds now, by id, in its form;
        the result is in the file's order."""
        out: dict[str, dict | None] = {}
        there: set[str] = set()
        for record in on_disk:
            rid = record_id(record)
            if not rid or rid in there or not isinstance(record, dict):
                continue
            there.add(rid)
            form = self._form(record)
            before = self._base.get(rid)
            if form is None or form == before:
                continue
            mine = held.get(rid)
            if before is None:
                if mine is None:
                    out[rid] = record  # came in
            elif mine is not None and _canonical(mine) == before:
                out[rid] = record  # changed there, and not here
        for rid, before in self._base.items():
            mine = held.get(rid)
            if rid not in there and mine is not None and _canonical(mine) == before:
                out[rid] = None  # removed there, and not changed here
        return out


def with_moves(records: list[dict], moves: Mapping[str, dict | None]) -> list[dict]:
    """*records* with *moves* (:meth:`Kept.moves`) applied: a changed record in its place, a
    removed one gone, and the ones that came in after the rest, in the order they are in."""
    out: list[dict] = []
    placed: set[str] = set()
    for record in records:
        rid = record_id(record)
        if rid in moves:
            moved = moves[rid]
            if moved is not None and rid not in placed:
                out.append(moved)
                placed.add(rid)
            continue
        out.append(record)
        placed.add(rid)
    for rid, moved in moves.items():
        if moved is not None and rid not in placed:
            out.append(moved)
            placed.add(rid)
    return out


def taken_in(
    path: Path, shape: Shape, kept: Kept, held: Mapping[str, dict]
) -> dict[str, dict | None]:
    """What an in-memory store holding *held* (by id, in its form) is to take in from *path*: what
    another writer changed there since it last read or wrote the file (:meth:`Kept.moves`). Empty
    when the file has not moved. Once the store applies it, *kept* knows the file as it is now.

    A file that cannot be read, or holds no records of this shape, gives nothing to take in and is
    not read again until it changes."""
    if not kept.behind(path):
        return {}
    with locked(path):
        at = stamp(path)
        try:
            on_disk = shape.records(read(path))
        except Unreadable as exc:
            logger.warning("%s", exc)
            on_disk = None
        if on_disk is None:
            kept.took(at, held.values())
            return {}
        moves = kept.moves(on_disk, held)
        kept.took(at, on_disk)
    return moves


def written(
    path: Path,
    shape: Shape,
    kept: Kept,
    held: list[dict],
    write: Callable[[list[dict]], None],
) -> None:
    """Write *held* — the records an in-memory store holds, in its order and form — to *path*
    under its lock, with what another writer changed there since the store last read or wrote it
    on top (:meth:`Kept.moves`). *write* is the store's own writer, given the records to write.

    The store takes the other writer's records in at its next :func:`taken_in`: *kept* says it is
    behind until then. A file that cannot be read is written over, as the store's own write always
    did; there is nothing in it to keep."""
    with locked(path):
        moves: dict[str, dict | None] = {}
        if kept.behind(path):
            try:
                on_disk = shape.records(read(path))
            except Unreadable:
                on_disk = None
            if on_disk is not None:
                moves = kept.moves(on_disk, {record_id(r): r for r in held if record_id(r)})
        write(with_moves(held, moves) if moves else held)
        kept.wrote(path, held, behind=bool(moves))
