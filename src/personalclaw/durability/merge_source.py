"""The database a merge takes another home's rows from.

A merge restore, an archive import and a folder sync each hand the merge an archive's copy of a
store's database (``snapshot._merge_sqlite_attach``). It is merged only when it is whole: one that
fails SQLite's integrity check, or cannot be read, is left out, and the store is named among what
the merge left unchanged.

A database whose stores number its rows, and each make their own tables when first used (the
learning log), declares how a file of it is opened (``StateEntry.open_database``). Both databases
are opened that way before the merge, the archive's as a private copy, so the archive is never
written: a row an earlier version wrote then has the identity it has in every home that holds it
(``numbered_rows.give_identity``), and every table of the archive has one here to come into.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from personalclaw.sqlite_compat import connect, sqlite3


def _opener(rel: str) -> Callable[[Path], None] | None:
    """How the store at the home-relative *rel* (its own path, or a partition's) opens a database
    file of it (``StateEntry.open_database``), or None to merge the two files as they are."""
    try:
        from personalclaw.durability import inventory as inv

        entry = next((e for e in inv.INVENTORY if e.path == rel), None) or inv.partition_entry(rel)
    except Exception:  # noqa: BLE001 — a restore must work even if this import breaks
        return None
    return entry.open_database if entry is not None else None


def _whole(src_db: Path, label: str) -> bool:
    """Whether the archive's *src_db* passes SQLite's integrity check, saying why not when not."""
    try:
        check = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
        try:
            if check.execute("PRAGMA integrity_check;").fetchone()[0] != "ok":
                print(f"  ⚠️  {label}: source integrity check failed — skipping merge")
                return False
        finally:
            check.close()
    except Exception as exc:  # noqa: BLE001 — a corrupt source must not abort the restore
        print(f"  ⚠️  {label}: source unreadable ({exc}) — skipping merge")
        return False
    return True


@contextmanager
def ready_to_merge(
    src_db: Path, dst_db: Path, label: str, unchanged: list[str]
) -> Iterator[Path | None]:
    """The database to merge into *dst_db* in place of the archive's *src_db*, the store at the
    home-relative *label*: *src_db* itself, or the private copy of it the store's opener opened,
    with *dst_db* opened the same way. None, with *label* put on *unchanged*, when the archive's is
    not whole or either cannot be opened: one store that cannot be merged must not abort the
    restore."""
    if not _whole(src_db, label):
        unchanged.append(label)
        yield None
        return
    opens = _opener(label)
    if opens is None:
        yield src_db
        return
    with TemporaryDirectory() as work:
        copy = Path(work) / Path(dst_db).name
        ready: Path | None = copy
        try:
            with (
                closing(sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)) as there,
                closing(connect(str(copy))) as here,
            ):
                there.backup(here)
            opens(copy)
            opens(Path(dst_db))
        except Exception as exc:  # noqa: BLE001 — one store that cannot open must not abort
            print(f"  ⚠️  {label}: could not be opened to merge ({exc}) — left unchanged")
            unchanged.append(label)
            ready = None
        yield ready
