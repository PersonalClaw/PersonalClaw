"""A JSON file of records that more than one writer writes: one lock, no write lost, and never
written over unread.

A store kept in one JSON file — a list of records, or a document that holds one — is written by
the store itself and, from outside it, by a sync that brings another machine's records in and by
a restore's or an import's merge. Three rules keep any of them from losing what the file holds:

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
* **Never written over unread.** A file that is there and cannot be read — not JSON, not the
  document its store keeps, or not openable — is left exactly as it is. Every write refuses
  (:class:`Unreadable`) until it can be read again, because a write built without what it holds
  would replace all of it: a store that read its broken file as empty used to write its next
  change over it, and everything else it held was gone. A copy of it as it was is kept beside it,
  ``<name>.broken-<UTC instant>`` (:func:`kept_copies`), so nothing it held is lost whatever
  happens to the file next — repaired by hand, restored, removed — and the first read to find it
  says so in the log, once, and names it for the Doctor (:func:`unreadable_files`). A reader that
  only lists may read it as holding nothing (:func:`read_or_none`, :func:`records_or_empty`); the
  read a write is built on never does (:func:`read`, :func:`records`). Absent is safe to write
  over, and so is a file holding nothing (zero bytes, or only whitespace): neither holds anything a
  write could destroy.

Every record is told apart by its ``id``; one without a string id is nobody's to merge.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from glob import escape
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_json_write
from personalclaw.home_paths import from_home

logger = logging.getLogger(__name__)

#: What the copy of a file that could not be read is kept as, beside it: ``<name>.broken-<UTC
#: instant>``. Never written over and never read as the store again, so no store keeps it as its
#: state, and none is deleted: the owner removes a copy once they no longer need it.
SET_ASIDE_MARK = ".broken-"


class Unreadable(ValueError):
    """A store file is there and cannot be read, so nothing is written to it until it can be.

    *why* is what is wrong, in one clause; *kept* is the copy of the file as it was, kept beside
    it (None when it could not be opened, so there was nothing to copy). The message says all of
    it: where the file is (from ``~``), why it cannot be read, that nothing is written to it, where
    the copy is, and what to do — the answer a refused write gives, the line the log says once, and
    what the Doctor names. It never quotes the file.
    """

    def __init__(self, path: Path | str, why: str, *, kept: Path | None = None) -> None:
        self.path = Path(path)
        self.why = why
        self.kept = kept
        super().__init__(self.sentence())

    def again(self) -> Unreadable:
        """This refusal, to raise anew. An exception raised again keeps every traceback it was
        raised through, so one refusal kept and raised by every read and write of a file that
        stays unreadable would grow without bound, and say all of it in each log line."""
        return Unreadable(self.path, self.why, kept=self.kept)

    def sentence(self) -> str:
        if self.kept is not None:
            after = (
                f"A copy of it as it was is kept beside it as {self.kept.name}. Repair the file, "
                "or remove it to start it over; the copy keeps what it held."
            )
        else:
            after = "Once it can be opened again, it is read and written as before."
        return (
            f"{from_home(self.path)} could not be read ({self.why}). Nothing is written to it "
            f"while it cannot be read, because a write would replace everything it holds. {after}"
        )


#: Each file this process found it could not read, by path → the refusal and the file's
#: :func:`stamp` when it was found. Process-local, like ``config.loader.config_discard``: what
#: this process saw. A read of the file once it has changed drops it.
_FOUND: dict[str, tuple[Unreadable, tuple[int, int, int] | None]] = {}
_FOUND_LOCK = threading.Lock()


def unreadable_files() -> list[Unreadable]:
    """Each file this process found it could not read that is still as it was then (its
    :func:`stamp` unchanged), oldest first: what the Doctor names. One that has changed since is
    left to its store's next read, which says again whether it can be read — only the store knows
    what its document must hold."""
    with _FOUND_LOCK:
        held = list(_FOUND.values())
    return [found.again() for found, at in held if stamp(found.path) == at]


def kept_copies(path: Path) -> list[Path]:
    """Each copy kept beside *path* of a time it could not be read (:data:`SET_ASIDE_MARK`),
    oldest first."""
    try:
        found = path.parent.glob(f"{escape(path.name)}{SET_ASIDE_MARK}*")
        return sorted(p for p in found if p.is_file())
    except OSError:
        return []


def _keep_copy(path: Path, raw: bytes) -> Path | None:
    """*raw* — what *path* held when it could not be read — kept beside it as
    ``<name>.broken-<UTC instant>``, 0600, and that copy's path: the copy already kept of the
    same bytes when there is one, so a file read again and again is kept once. A name already
    taken gets a counter rather than being written over. None, logged, when no copy could be made.
    """
    try:
        for copy in kept_copies(path):
            if copy.stat().st_size == len(raw) and copy.read_bytes() == raw:
                return copy
        instant = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target, counter = path.with_name(f"{path.name}{SET_ASIDE_MARK}{instant}"), 2
        while True:
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                named = f"{path.name}{SET_ASIDE_MARK}{instant}-{counter}"
                target, counter = path.with_name(named), counter + 1
                continue
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            return target
    except OSError:
        logger.warning("could not keep a copy of %s, which cannot be read", path, exc_info=True)
        return None


def _found(path: Path, why: str, raw: bytes | None, at: tuple[int, int, int] | None) -> Unreadable:
    """The refusal for *path*, found unreadable for *why*: a copy of *raw* kept, the finding
    named for :func:`unreadable_files`, and said in the log when it is new — the first time, or
    for a file that has changed since."""
    with _FOUND_LOCK:
        known = _FOUND.get(str(path))
    if known is not None and known[1] == at and known[0].why == why:
        return known[0].again()
    kept = _keep_copy(path, raw) if raw is not None else None
    found = Unreadable(path, why, kept=kept)
    with _FOUND_LOCK:
        _FOUND[str(path)] = (found, at)
    logger.warning("%s", found)
    return found.again()


def _read_again(path: Path, at: tuple[int, int, int] | None) -> None:
    """*path*, stamped *at*, parsed: what this process found wrong with it before is not true of
    it now if it has changed since. Unchanged, the finding stands, whatever this read makes of the
    document: a store that found it is not its own still finds it so, said once."""
    if _FOUND:
        with _FOUND_LOCK:
            known = _FOUND.get(str(path))
            if known is not None and known[1] != at:
                del _FOUND[str(path)]


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
    #: With ``key``: a document that is itself the list is read as the records too — the file as
    #: a hand edit left it after dropping the document around the list. Written back inside it.
    bare: bool = False

    def records(self, document: Any) -> list | None:
        """The records *document* holds, in its order; None when it is not this store's."""
        if self.split is not None:
            return self.split(document)
        if not self.key or (self.bare and isinstance(document, list)):
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

    @property
    def mismatch(self) -> str:
        """Why a JSON document that is not this store's cannot be read as it, in one clause."""
        if self.split is not None:
            return "it is JSON, but not in the form its store keeps"
        if not self.key:
            return "it is JSON, but not a list"
        return f'it is JSON, but holds no "{self.key}" list'


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


def _not_json(exc: BaseException) -> str:
    if isinstance(exc, json.JSONDecodeError):
        return f"it is not valid JSON: line {exc.lineno}, column {exc.colno}"
    if isinstance(exc, UnicodeDecodeError):
        return "it is not UTF-8 text"
    return "it is not valid JSON"


@dataclass(frozen=True)
class _Read:
    """One read of a file: its document (None for no file, or one holding nothing), the bytes it
    was read from, and its :func:`stamp`, taken before the read."""

    document: Any
    raw: bytes | None
    at: tuple[int, int, int] | None

    def refused(self, path: Path, why: str) -> Unreadable:
        """The refusal for a document this read found that is not the store's."""
        return _found(path, why, self.raw, self.at)


def _read(path: Path) -> _Read:
    at = stamp(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _Read(None, None, at)
    except OSError as exc:
        opened = f"it could not be opened: {exc.strerror}" if exc.strerror else ""
        raise _found(path, opened or "it could not be opened", None, at) from exc
    if not raw.strip():
        _read_again(path, at)
        return _Read(None, raw, at)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        raise _found(path, _not_json(exc), raw, at) from exc
    _read_again(path, at)
    return _Read(document, raw, at)


def _of_kind(path: Path, done: _Read, kind: type | None) -> Any:
    if done.document is not None and kind is not None and not isinstance(done.document, kind):
        nouns: dict[type, str] = {dict: "an object", list: "a list"}
        raise done.refused(path, f"it is JSON, but not {nouns.get(kind, kind.__name__)}")
    return done.document


def _of_shape(path: Path, done: _Read, shape: Shape) -> list | None:
    """The records *done* found, None when it found no document; raises for one not *shape*'s."""
    if done.document is None:
        return None
    held = shape.records(done.document)
    if held is None:
        raise done.refused(path, shape.mismatch)
    return held


def not_the_store(path: Path, why: str) -> Unreadable:
    """The refusal for *path*, whose JSON is not what its store keeps, for *why* (one clause):
    kept, logged once and named for the Doctor like any file that cannot be read. For a store
    that checks its document beyond what :func:`read`'s *kind* and a :class:`Shape` say."""
    at = stamp(path)
    try:
        raw: bytes | None = path.read_bytes()
    except OSError:
        raw = None
    return _found(path, why, raw, at)


def read(path: Path, kind: type | None = None) -> Any:
    """The document in *path*, for a read a write is built on: None when there is no file, or
    when it holds nothing (zero bytes, or only whitespace), which a write may replace — there is
    nothing in it to lose. Raises :class:`Unreadable` for a file that is there and holds no JSON,
    or with *kind*, JSON that is not one (a store whose document is an object passes ``dict``),
    after keeping a copy of it."""
    return _of_kind(path, _read(path), kind)


def read_or_none(path: Path, kind: type | None = None) -> Any:
    """:func:`read` for a reader that only shows or decides from what the file holds: None for a
    file that cannot be read too, which the failed read has kept, logged and named for the
    Doctor. Never the read a write is built on."""
    try:
        return read(path, kind)
    except Unreadable:
        return None


def records(path: Path, shape: Shape) -> list:
    """The records *path* holds, in order, for a read a write is built on: ``[]`` when
    :func:`read` finds no document. Raises :class:`Unreadable` for a file that cannot be read, or
    whose document is not *shape*'s, after keeping a copy of it."""
    return _of_shape(path, _read(path), shape) or []


def records_or_empty(path: Path, shape: Shape) -> list:
    """:func:`records` for a reader that only lists: ``[]`` for a file that cannot be read too,
    which the failed read has kept, logged and named for the Doctor. Never the read a write is
    built on."""
    try:
        return records(path, shape)
    except Unreadable:
        return []


def rewrite(path: Path, change: Callable[[Any], Any], shape: Shape | None = None) -> bool:
    """Apply *change* to *path*'s document under its lock, the document re-read first — the one
    way a writer outside the store writes it. *change* gets the document (None when there is no
    file) and returns the one to write, or None to leave the file exactly as it is. Returns
    whether it wrote. Raises :class:`Unreadable` before *change* runs for a file it cannot read,
    or, given its store's *shape*, whose document is not that store's. Written by the one JSON
    writer (``atomic_write.atomic_json_write``)."""
    with locked(path):
        done = _read(path)
        if shape is not None:
            _of_shape(path, done, shape)
        updated = change(done.document)
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

    It also knows when the file could not be read (:attr:`unreadable`): until the file changes,
    every write refuses with that, without reading it again.
    """

    def __init__(self, normalize: Callable[[dict], dict] | None = None) -> None:
        self._normalize = normalize
        self._base: dict[str, str] = {}
        self._stamp: tuple[int, int, int] | None = None
        #: The store's last write put there records it does not hold yet (:func:`written`).
        self._behind = False
        #: Why the file could not be read when the store last looked, at ``_stamp``.
        self._unreadable: Unreadable | None = None

    @property
    def unreadable(self) -> Unreadable | None:
        """Why the file could not be read when the store last looked, or None. The store holds
        nothing it read from it then, and says so by this."""
        return self._unreadable

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
        self._unreadable = None

    def could_not_read(self, at: tuple[int, int, int] | None, found: Unreadable) -> None:
        """The store found the file (stamped *at*) unreadable for *found*: it took nothing from
        it, and every write refuses until the file changes. What it last knew of the file stays,
        so a repair is taken in as the changes it brings."""
        self._stamp = at
        self._behind = False
        self._unreadable = found

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
        self._unreadable = None

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


def loaded(path: Path, shape: Shape, kept: Kept) -> list:
    """The records *path* holds, for an in-memory store's first read: ``[]`` when there is no
    file, and when it cannot be read — which *kept* then knows (:attr:`Kept.unreadable`), so the
    store's every write refuses until the file can be read."""
    at = stamp(path)
    try:
        on_disk = records(path, shape)
    except Unreadable as found:
        kept.could_not_read(at, found)
        return []
    kept.took(at, on_disk)
    return on_disk


def taken_in(
    path: Path, shape: Shape, kept: Kept, held: Mapping[str, dict]
) -> dict[str, dict | None]:
    """What an in-memory store holding *held* (by id, in its form) is to take in from *path*: what
    another writer changed there since it last read or wrote the file (:meth:`Kept.moves`). Empty
    when the file has not moved. Once the store applies it, *kept* knows the file as it is now.

    A file that cannot be read gives nothing to take in, and is not read again until it changes:
    *kept* knows it cannot be, so every write refuses meanwhile (:func:`written`)."""
    if not kept.behind(path):
        return {}
    with locked(path):
        at = stamp(path)
        try:
            on_disk = records(path, shape)
        except Unreadable as found:
            kept.could_not_read(at, found)
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
    behind until then. Raises :class:`Unreadable`, writing nothing, for a file that cannot be read:
    what *held* lacks of it is not known, so writing *held* would replace it."""
    with locked(path):
        moves: dict[str, dict | None] = {}
        if kept.behind(path):
            at = stamp(path)
            try:
                on_disk = records(path, shape)
            except Unreadable as found:
                kept.could_not_read(at, found)
                raise
            moves = kept.moves(on_disk, {record_id(r): r for r in held if record_id(r)})
        elif kept.unreadable is not None:
            raise kept.unreadable.again()
        write(with_moves(held, moves) if moves else held)
        kept.wrote(path, held, behind=bool(moves))
