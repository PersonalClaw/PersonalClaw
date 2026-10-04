"""What PersonalClaw puts into the home, sends out of it or locks in it: never through a link.

**Writes.** A restore, an import or a sync puts what an archive or another machine brings at a path
inside the home: a replace restore and a merge restore (``snapshot``), the import of an export
archive (``portability``) or of a project archive (``workflows.project_archive``), a pack's install
(``packs.import_``), a sync's pull and its conflict review (``durability.reconcile``,
``durability.writeback``, ``durability.db_merge``). A link the home holds at that path, or at a
folder on the way to it, would carry the write out of the home: a copy opens a symbolic link's
target, a database merge writes into the file a hard link shares with a name outside, and a folder
that is a link puts everything under it wherever the link leads. Writers keep files beside the one
they write, and open those the same way: SQLite its log and the log's index beside a database, and
a store of records the lock its writes take (``record_files.lock_path``). So those count as part of
it.

**Reads that leave.** A sync's export, which goes to the other machines, and the backup export read
the home's stores (``durability.shards``). A link there would send a file from outside the home in
the store's name: whatever a folder that is a link leads to, and what a file with a second name
shares with a name outside.

**Locks.** Every lock in the home is a file opened for ``flock``. Opened to write, it emptied
whatever a link at its name led to; made where a link dangled, it made a file wherever that pointed.

One rule holds for every kind of item, a file, a folder, a database, a store of records or a lock:
**a link is never written or read through.** Where the home holds a symbolic link at the path an
item would be written to or read from, at any folder between the home and it, or at a file kept
beside it, or a file there that has another name (a hard link), the item is left exactly as it is:
nothing of it is written, nothing the home has there is moved aside, and nothing is read through
the link. The result names the path: a restore's and an import's last line and the parts they left
unchanged, a pack's and a project's refused import, the conflict review's refusal, the sync report,
and the export's files it could not carry; a lock's refusal is logged and answered as
``link_in_the_way``. Removing the link and running again brings the item in.

:func:`home_path` is the one check. Every write those doors make into the home takes its path from
it (:func:`landing` is its form for a door that goes on to the next item, and :func:`put_file`
writes an archive's file at a path it gave), every read an export makes from :func:`export_path`,
its form for a read, and every lock in the home is opened by :func:`open_lock`.
``tests/test_home_path_census.py`` fails a write, a read or a lock of theirs that skips it.
"""

from __future__ import annotations

import errno
import io
import logging
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from personalclaw.atomic_write import PRIVATE_FILE_MODE, SQLITE_SIDECARS, private_file
from personalclaw.record_files import lock_path

logger = logging.getLogger(__name__)

#: Why an item is left as it is, by what the home holds on the way to it.
A_SYMBOLIC_LINK = (
    "a symbolic link in this home, which nothing restored, imported or synced is written through"
)
A_HARD_LINK = (
    "a hard link in this home (a file with another name), which nothing restored, imported or "
    "synced is written through"
)
#: Why an export leaves a store, or a file of one, out (:func:`export_path`, :func:`not_read`).
A_SYMBOLIC_LINK_READ = (
    "a symbolic link in this home, which nothing exported or synced is read through"
)
A_HARD_LINK_READ = (
    "a hard link in this home (a file with another name), which nothing exported or synced is "
    "read through"
)
#: Why a lock is not opened (:func:`open_lock`).
A_SYMBOLIC_LINK_LOCK = "a symbolic link in this home, which no lock is opened through"
A_HARD_LINK_LOCK = (
    "a hard link in this home (a file with another name), which no lock is opened through"
)


@dataclass(frozen=True)
class _Words:
    """What a door says of each kind of link in its way."""

    symbolic: str
    hard: str


_WRITTEN = _Words(A_SYMBOLIC_LINK, A_HARD_LINK)
_READ = _Words(A_SYMBOLIC_LINK_READ, A_HARD_LINK_READ)
_LOCKED = _Words(A_SYMBOLIC_LINK_LOCK, A_HARD_LINK_LOCK)


class LinkInTheWay(Exception):
    """The home holds a link where a door would write, read or lock. ``str()`` is the sentence:
    the link's path, inside the folder the check started from, and why the item it is in the way
    of was left as it is."""

    def __init__(self, rel: str, why: str) -> None:
        super().__init__(f"{rel} ({why})")
        self.rel = rel
        self.why = why

    def under(self, folder: str) -> LinkInTheWay:
        """The same link, by its path inside what holds *folder*: a store's link, in the home."""
        return LinkInTheWay(f"{folder}/{self.rel}", self.why)

    def put_on(self, left: list[str]) -> None:
        """Put this link's sentence on *left*, once however many items it stops."""
        if str(self) not in left:
            left.append(str(self))


def _parts(rel: str | os.PathLike[str]) -> list[str]:
    """The names *rel* is made of, or ``ValueError`` when it names no path inside a folder: empty,
    absolute, or with a part that is empty, ``.``, ``..`` or holds a NUL."""
    text = os.fspath(rel)
    parts = text.split("/")
    if not text or text.startswith("/") or any(p in ("", ".", "..") or "\x00" in p for p in parts):
        raise ValueError(f"{text!r} names no path inside the home")
    return parts


def _link_at(path: Path, words: _Words = _WRITTEN) -> str:
    """Why *path* is in the way — *words*' sentence for a symbolic link or for a hard link — or
    ``""`` when nothing is there or it is no link."""
    try:
        st = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return ""
    if stat.S_ISLNK(st.st_mode):
        return words.symbolic
    if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
        return words.hard
    return ""


def home_path(base: Path | str, rel: str | os.PathLike[str]) -> Path:
    """``base / rel``, once nothing on the way to it is a link: the path a restore, an import or a
    sync writes an item at.

    *base* is the home, or a folder of it this check already gave (a store's own folder, which a
    writer then puts each of its files under), and is taken as it is: the home may itself live
    behind a link. Below it, each folder on the way to *rel* that is there must be a folder and
    not a symbolic link, and what is at *rel* must be no symbolic link and no file with a second
    name; unless it is a folder, neither may a file a writer keeps beside it (:func:`_beside`).
    What is not there yet is made by the write, so it is the home's own folder or file.

    Raises :class:`LinkInTheWay` naming the first link, by its path inside *base*, and
    ``ValueError`` for a *rel* that names no path inside it. A check, then a write: a folder that
    some other process makes a link in between is outside what this sees.
    """
    return _checked(base, rel, _WRITTEN)


def export_path(base: Path | str, rel: str | os.PathLike[str]) -> Path:
    """:func:`home_path` for a read that leaves this machine: the path an export reads a store at,
    a sync's for the other machines or a backup's. The same check, and the same refusal, in the
    words of a read (:data:`A_SYMBOLIC_LINK_READ`, :data:`A_HARD_LINK_READ`)."""
    return _checked(base, rel, _READ)


def _checked(base: Path | str, rel: str | os.PathLike[str], words: _Words) -> Path:
    parts = _parts(rel)
    here = Path(base)
    for depth, part in enumerate(parts[:-1], 1):
        here = here / part
        try:
            st = os.lstat(here)
        except (FileNotFoundError, NotADirectoryError):
            # Nothing further on is there: the write makes it, folders and all.
            return Path(base).joinpath(*parts)
        if stat.S_ISLNK(st.st_mode):
            raise LinkInTheWay("/".join(parts[:depth]), words.symbolic)
    target = here / parts[-1]
    why = _link_at(target, words)
    if why:
        raise LinkInTheWay("/".join(parts), why)
    if not target.is_dir():
        for kept in _beside(target):
            why = _link_at(kept, words)
            if why:
                raise LinkInTheWay("/".join([*parts[:-1], kept.name]), why)
    return target


def not_read(path: Path) -> str:
    """Why an export does not read *path*, a file or a folder it met inside a store that
    :func:`export_path` gave — a symbolic link, or a file with a second name — or ``""`` when it
    may."""
    return _link_at(path, _READ)


def _beside(target: Path) -> list[Path]:
    """The files writers keep beside *target* and open with it: SQLite's journal, log and log index
    beside a database (``atomic_write.SQLITE_SIDECARS``), and the lock a store of records takes
    beside its file (``record_files.lock_path``)."""
    return [*(Path(f"{target}{suffix}") for suffix in SQLITE_SIDECARS), lock_path(target)]


def landing(home: Path, rel: str, left: list[str]) -> Path | None:
    """:func:`home_path` for a door that goes on to its next item: the path in *home*, or ``None``
    when a link is in the way, its sentence put on *left* (once, however many items it stops)."""
    try:
        return home_path(home, rel)
    except LinkInTheWay as link:
        link.put_on(left)
        return None


def put_file(src: Path, dst: Path) -> None:
    """Write the archive's file *src* at *dst*, a path :func:`home_path` gave, through the private
    writer (``atomic_write.private_file``): a new file beside it, readable by its owner alone and
    renamed into place, in folders it makes where they are missing. So whatever is at *dst* by then
    is replaced and never written through. It keeps *src*'s times."""
    st = os.stat(src)
    with open(src, "rb") as data, private_file(dst, fsync=False) as out:
        shutil.copyfileobj(data, out, 1 << 20)
        out.flush()
        os.utime(out.fileno(), ns=(st.st_atime_ns, st.st_mtime_ns))


def open_lock(path: Path | str) -> io.FileIO:
    """The lock file *path*, opened for ``fcntl.flock``: the one way a lock in the home is opened.

    Made when it is not there, readable by its owner alone, and never emptied: what a lock file
    holds is nothing a lock reads, and a lock opened to write emptied whatever a link at its name
    led to. Never through a link: a symbolic link at *path*, dangling or not, is refused by the
    open itself (``O_NOFOLLOW``), so no file is made where it points, and a file there with a
    second name is refused before anything of it changes. Either raises :class:`LinkInTheWay`
    naming the lock by its path in the home, and is logged. The folder it is in is the caller's to
    make, as before.

    The caller closes it (``with open_lock(...) as handle``), which releases the lock.
    """
    path = Path(path)
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, PRIVATE_FILE_MODE)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK) and os.path.islink(path):
            raise _refused_lock(path, A_SYMBOLIC_LINK_LOCK) from None
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.EINVAL, f"{path} is not a file, so it cannot be a lock")
        if st.st_nlink > 1:
            raise _refused_lock(path, A_HARD_LINK_LOCK)
        if st.st_mode & 0o077:
            try:
                os.fchmod(fd, PRIVATE_FILE_MODE)
            except OSError:
                # One this process may open but not own: the lock holds nothing to keep private.
                logger.debug("could not make the lock %s private", path, exc_info=True)
    except BaseException:
        os.close(fd)
        raise
    return io.FileIO(fd, "r+", closefd=True)


def _refused_lock(path: Path, why: str) -> LinkInTheWay:
    link = LinkInTheWay(_in_home(path), why)
    logger.warning("a lock was not opened: %s", link)
    return link


def _in_home(path: Path) -> str:
    """*path* by its path inside the home when it is in it (``config.loader.resolve_config_dir``,
    which makes nothing), else as it is."""
    try:
        from personalclaw.config import loader  # lazy: the loader's imports reach this module

        home = os.path.abspath(loader.resolve_config_dir())
    except Exception:  # noqa: BLE001 — a home that cannot be resolved names the lock in full
        return str(path)
    full = os.path.abspath(path)
    if full.startswith(home + os.sep):
        return Path(os.path.relpath(full, home)).as_posix()
    return str(path)
