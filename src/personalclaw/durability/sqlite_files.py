"""A copy of the home takes each SQLite database in it as one consistent file.

Every path that copies the home — a snapshot, an export, a merge restore, an import, a replace
restore, and an app's update or keep-data uninstall carrying its ``data/`` forward — copies folders,
and a folder can hold a database: one the state manifest declares
(``workspace/knowledge/knowledge.db``), one an installed app keeps in its ``data/`` folder, or one
the user put in the workspace. A database open in WAL mode is more than one file: its own, the
write-ahead log beside it (``-wal``) with that log's index (``-shm``), and in the older journal mode
a rollback journal (``-journal``) mid-transaction. Its newest committed rows can be in the log and
not yet in its own file, and SQLite applies a log to whatever database file it finds beside it:
nothing in the log names the database it was written for.

So one rule holds on every one of those paths, and it lives here:

* a database is copied through SQLite's online backup API (:func:`copy_file`), which reads one
  consistent state, its log folded in, into a single file;
* a sidecar is never copied on its own (:func:`is_sidecar`), so an archive holds each database as
  one file with nothing beside it;
* a merge restore, an import and a sync's first copy of a store never put a sidecar into the home,
  and put a database in on its own (:func:`bring_in`); a replace restore moves a database aside with
  its sidecars (:func:`move_aside`): an archive's copy never opens with a log it was not written
  with.

Whether a file is a database is read from the file (:func:`is_database`), and a database the
manifest declares is matched by its path relative to the home, never by its name: a ``runs.db`` of
the user's own in the workspace is not the workflow run ledger, and it is copied as the user's file
it is.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable, Collection
from contextlib import closing
from pathlib import Path

from personalclaw.atomic_write import SQLITE_SIDECARS
from personalclaw.sqlite_compat import connect

#: The first bytes of every SQLite database file.
_HEADER = b"SQLite format 3\x00"


def is_database(path: Path | str) -> bool:
    """Whether *path* is a regular file SQLite reads as a database: it begins with SQLite's header.
    A link, a folder, a file that cannot be read, or one merely named ``.db`` is not one."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return False
        with open(path, "rb") as fh:
            return fh.read(len(_HEADER)) == _HEADER
    except OSError:
        return False


def is_sidecar(folder: Path | str, name: str, *others: Path | str) -> bool:
    """Whether the file *name* in *folder* is one SQLite keeps beside a database: named for a
    ``.db`` file (``notes.db-wal``), or for a file beside it, in *folder* or in one of *others*,
    that is a database. A user's own ``daily-journal`` beside no database is not one."""
    for suffix in SQLITE_SIDECARS:
        if name.endswith(suffix) and len(name) > len(suffix):
            base = name[: -len(suffix)]
            return base.endswith(".db") or any(
                is_database(Path(f) / base) for f in (folder, *others)
            )
    return False


def copy_file(src: Path | str, dst: Path | str) -> str:
    """Copy one file of the home (also ``shutil.copytree``'s ``copy_function``): a database through
    SQLite's backup API, anything else as it is. A database SQLite cannot read is copied as it is
    too, and said: its bytes are all there is of it."""
    if is_database(src):
        try:
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            with (
                closing(connect(str(src))) as there,
                closing(connect(str(dst))) as here,
            ):
                there.backup(here)
            return str(dst)
        except Exception as exc:  # noqa: BLE001 — every failure falls back to the file's bytes
            name = Path(src).name
            print(f"⚠️  sqlite backup failed for {name} ({exc}); falling back to a file copy")
    shutil.copy2(str(src), str(dst))
    return str(dst)


def tree_ignore(home: Path, copied: Collection[str]) -> Callable[[str, list[str]], set[str]]:
    """A ``shutil.copytree`` ignore for a folder of *home*: it leaves out each database *copied*
    names (home-relative paths, already copied whole by the pass that copies the declared ones)
    and every sidecar. Any other database it meets is copied by :func:`copy_file`."""

    def _ignore(directory: str, contents: list[str]) -> set[str]:
        folder = Path(directory)
        try:
            base = folder.relative_to(home).as_posix()
        except ValueError:
            return sidecars_in(directory, contents)
        prefix = "" if base == "." else f"{base}/"
        return {n for n in contents if f"{prefix}{n}" in copied or is_sidecar(folder, n)}

    return _ignore


def sidecars_in(directory: str | Path, contents: list[str]) -> set[str]:
    """The sidecars among *contents* of *directory*: a ``shutil.copytree`` ignore for a copy that
    takes each database as one file, or for a restore, which never puts a sidecar into the home."""
    return {name for name in contents if is_sidecar(directory, name)}


def bring_in(item: Path, target: Path) -> bool:
    """Put the archive's file *item* at *target* in the home, for a merge restore, an import or a
    sync's first copy of a store, and say whether it did: only where the home has nothing at
    *target*, a link included, and never a sidecar (an older archive carried them). A database
    goes in on its own: a log, a log index or a journal the home keeps at *target* with no
    database there is removed first. SQLite discards such a leftover the first time it opens a
    database with no pages, and beside the archive's copy it would read the leftover's pages in
    place of the copy's own."""
    if os.path.lexists(target) or is_sidecar(item.parent, item.name, target.parent):
        return False
    if is_database(item):
        for suffix in SQLITE_SIDECARS:
            leftover = Path(f"{target}{suffix}")
            if leftover.is_symlink() or leftover.is_file():
                leftover.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(item), str(target))
    return True


def move_aside(live: Path, backup: Path) -> None:
    """Move what the home has at *live* to *backup*, for a replace restore that then puts the
    archive's copy in its place: the file or folder, and, when *live* is or is named as a
    database, each sidecar the home has of it, whether the database itself is there or not. The
    archive's copy then never opens with this home's log. A link is left where it is."""
    if live.is_symlink():
        return
    moves = [(live, backup)] if live.exists() else []
    if live.name.endswith(".db") or is_database(live):
        moves += [
            (Path(f"{live}{suffix}"), Path(f"{backup}{suffix}"))
            for suffix in SQLITE_SIDECARS
            if os.path.isfile(f"{live}{suffix}") and not os.path.islink(f"{live}{suffix}")
        ]
    for here, aside in moves:
        aside.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(here), str(aside))


def databases_in(unpacked: Path) -> list[Path]:
    """Every database an unpacked archive holds, for a restore drill to read: each file that is
    one, and each file at a path the manifest declares a database at (a store's own, or a
    partition of one), which a drill reports when it is not one any more. A file merely named like
    one (a user's own ``notes.db``) is neither. Each folder in *unpacked* is an archive's own, as a
    snapshot writes it."""
    from personalclaw.durability import inventory as inv

    stores = {e.path for e in inv.sqlite_entries()}
    found: list[Path] = []
    for top in sorted(unpacked.iterdir()):
        if top.is_symlink():
            continue
        if not top.is_dir():
            found += [top] if is_database(top) else []
            continue
        declared = stores | set(inv.partition_paths(top))
        for path in sorted(top.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if is_database(path) or path.relative_to(top).as_posix() in declared:
                found.append(path)
    return found
