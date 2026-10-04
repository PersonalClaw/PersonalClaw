"""What a restore, an import or a sync writes into the home lands in the home, never through a link.

Each of them puts what an archive or another machine brings at a path inside the home: a replace
restore and a merge restore (``snapshot``), the import of an export archive (``portability``), a
sync's pull and its conflict review (``durability.reconcile``, ``durability.writeback``,
``durability.db_merge``). A link the home holds at that path, or at a folder on the way to it,
would carry the write out of the home: a copy opens a symbolic link's target, a database merge
writes into the file a hard link shares with a name outside, and a folder that is a link puts
everything under it wherever the link leads. Writers keep files beside the one they write, and
open those the same way: SQLite its log and the log's index beside a database, and a store of
records the lock its writes take (``record_files.lock_path``). So those count as part of it.

One rule holds for every kind of item, a file, a folder, a database or a store of records: **a link
is never written through.** Where the home holds a symbolic link at the path an item would be
written to, at any folder between the home and it, or at a file kept beside it, or a file there
that has another name (a hard link), the item is left exactly as it is: nothing of it is written,
nothing the home has there is moved aside, and nothing is read through the link either. The result
names the path: a restore's and an import's last line and the parts they left unchanged, the
conflict review's refusal, and the sync report. Removing the link and running again brings the item
in.

:func:`home_path` is the one check, and every write those doors make into the home takes its path
from it (:func:`landing` is its form for a door that goes on to the next item, and
:func:`put_file` writes an archive's file at a path it gave). ``tests/test_home_path_census.py``
fails a write of theirs that takes its path from anywhere else.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from personalclaw.atomic_write import SQLITE_SIDECARS, private_file
from personalclaw.record_files import lock_path

#: Why an item is left as it is, by what the home holds on the way to it.
A_SYMBOLIC_LINK = (
    "a symbolic link in this home, which nothing restored, imported or synced is written through"
)
A_HARD_LINK = (
    "a hard link in this home (a file with another name), which nothing restored, imported or "
    "synced is written through"
)


class LinkInTheWay(Exception):
    """The home holds a link where a restore, an import or a sync would write. ``str()`` is the
    sentence: the link's path, inside the folder the check started from, and why the item it is in
    the way of was left as it is."""

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


def _link_at(path: Path) -> str:
    """Why *path* is in the way — :data:`A_SYMBOLIC_LINK` or :data:`A_HARD_LINK` — or ``""`` when
    nothing is there or it is no link."""
    try:
        st = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return ""
    if stat.S_ISLNK(st.st_mode):
        return A_SYMBOLIC_LINK
    if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
        return A_HARD_LINK
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
            raise LinkInTheWay("/".join(parts[:depth]), A_SYMBOLIC_LINK)
    target = here / parts[-1]
    why = _link_at(target)
    if why:
        raise LinkInTheWay("/".join(parts), why)
    if not target.is_dir():
        for kept in _beside(target):
            why = _link_at(kept)
            if why:
                raise LinkInTheWay("/".join([*parts[:-1], kept.name]), why)
    return target


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
