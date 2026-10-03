"""What another home's library brought, settled where this library is opened.

A knowledge library comes into a home from another one whole (a snapshot restored on another
machine, a home folder moved, an archive imported into a home without a library) or row by row (a
merge restore, an archive import in merge mode, a folder sync). Two things it brings name the home
it came from, and are put right here: the documents its items keep, as paths into that home's
files folder (:func:`repoint_moved_documents`), and the source rows that home's system made, one
per provider (:func:`fold_system_sources`). The store does both when it opens; an import and a
merge restore point the documents as soon as the files are in.
"""

from __future__ import annotations

import logging
import pathlib
from typing import Any

logger = logging.getLogger(__name__)


def system_source_id(provider: str) -> str:
    """The id of the source the system makes for *provider* (the artifact mirror's row): one per
    provider in a library, and the same in every home, so a library merged with another's (a
    merge restore, an archive import, a folder sync) holds one row for it rather than one per
    home that made it."""
    return f"src-{provider}"


def place_in_files(path: str, files: pathlib.Path) -> tuple[str, ...] | None:
    """Where *path* sits in a library's files folder: its parts after the last pair of folders
    named as this library's *files* folder and its parent are (``knowledge/files``), or None when
    it names no such place (or one that climbs out of it)."""
    parts = pathlib.PurePosixPath(path).parts
    library, folder = files.parent.name, files.name
    for i in range(len(parts) - 1, 0, -1):
        if parts[i] == folder and parts[i - 1] == library:
            place = parts[i + 1 :]
            return place if place and not any(p in (".", "..") for p in place) else None
    return None


#: The columns of ``items`` that hold a path to a file in the library's own ``files`` folder.
_DOCUMENT_COLUMNS = ("file_path", "thumbnail_path")


def repoint_moved_documents(db: Any, files: pathlib.Path) -> int:
    """Point each document another home's library kept at this library's copy of it in *files*,
    its ``files`` folder, through the connection *db*, and return how many paths changed.

    An item keeps its document (and thumbnail) as a path into the library's ``files`` folder
    under the home that wrote it, and the file route serves only from this library's folder. A
    library that came here from another home — an archive imported into this one, a snapshot
    restored on another machine, a home folder moved — still named the other home's folder, so
    every document that arrived with it read "not found" though its file had arrived beside it.
    A path whose place in a library's ``files`` folder holds a file here is pointed at that file:
    by the store when it opens, and by an import or a merge restore once the files are in.

    The place is taken from a path another home wrote, so what it may name is narrow: a regular
    file inside this library's folder that no other item keeps. A path never reaches past the
    folder, and an item from elsewhere never takes over a document this home keeps — deleting the
    one would delete the other's file.
    """
    if not files.is_dir():
        return 0
    root = files.resolve()
    # The folder as this store names it and as the file system resolves it: a path under either
    # is already this library's, which a prefix tells without touching the disk, so a library
    # whose documents are all its own costs one query that returns nothing.
    here = tuple(dict.fromkeys((f"{files}/", f"{root}/")))

    def _outside(col: str) -> str:
        inside = " OR ".join(f"substr({col}, 1, ?) = ?" for _ in here)
        return f"(COALESCE({col}, '') != '' AND NOT ({inside}))"

    params = [v for _col in _DOCUMENT_COLUMNS for p in here for v in (len(p), p)]
    rows = db.execute(
        f"SELECT id, {', '.join(_DOCUMENT_COLUMNS)} FROM items "  # noqa: S608
        f"WHERE {' OR '.join(_outside(c) for c in _DOCUMENT_COLUMNS)}",
        params,
    ).fetchall()
    if not rows:
        return 0
    # Each file an item of this library keeps already, by its resolved path.
    kept: set[str] = set()
    for row in db.execute(f"SELECT {', '.join(_DOCUMENT_COLUMNS)} FROM items").fetchall():
        for path in tuple(row):
            if path and path.startswith(here[0]):
                kept.add(f"{root}/{path[len(here[0]):]}")
            elif path and path.startswith(here[-1]):
                kept.add(path)
    moved: list[tuple[str, str, str]] = []
    for item_id, *paths in (tuple(r) for r in rows):
        for col, path in zip(_DOCUMENT_COLUMNS, paths):
            if not path or path.startswith(here):
                continue
            place = place_in_files(path, files)
            if place is None:
                continue
            candidate = files.joinpath(*place)
            try:
                found = candidate.resolve()
            except OSError:
                continue
            if (
                candidate.is_symlink()
                or not found.is_relative_to(root)
                or not found.is_file()
                or str(found) in kept
            ):
                continue
            kept.add(str(found))
            moved.append((col, str(candidate), item_id))
    if not moved:
        return 0
    db.execute("BEGIN")
    try:
        for col, path, item_id in moved:
            # Not an edit: `updated_at` stays, so nothing that follows an edit (the vault's
            # projection, a re-index) takes the item for one.
            db.execute(f"UPDATE items SET {col} = ? WHERE id = ?", (path, item_id))  # noqa: S608
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    logger.info(
        "knowledge: documents another home's library kept, now pointed at this one's files: %d",
        len(moved),
    )
    return len(moved)


def fold_system_sources(store: Any) -> int:
    """Fold each source row the system made into its provider's one row
    (:func:`system_source_id`), and return how many rows were folded.

    The system makes one source per provider — the artifact mirror's — and each home made it
    under an id it minted, so a merge of another home's library brought the other home's row
    in beside this one's: the Sources list named it twice, the older answered for the mirror,
    and this home's mirrors were no longer found under it. A row under any other id (a
    merge's, or one made before the id was the provider's) is folded in here.
    """
    rows = store.db.execute(
        "SELECT id, provider FROM sources WHERE created_by = 'system' ORDER BY created_at"
    ).fetchall()
    folded = 0
    for row in rows:
        into = system_source_id(row["provider"])
        if row["id"] != into:
            _fold_source(store, row["id"], into)
            folded += 1
    if folded:
        logger.info("knowledge: system source rows folded into their provider's: %d", folded)
    return folded


def _fold_source(store: Any, old: str, into: str) -> None:
    """Move source *old*'s items, sightings and cursor to source *into*, made from *old* when
    the library has no such row, and drop *old*.

    When both rows hold an item for one guid, the one *into* holds stays and the other goes,
    as any removed item goes (its index rows and dependents with it): a row folded in never
    replaces what the provider's row already mirrors, and a mirror is brought up to date by
    the next save of what it mirrors.
    """
    twins = store.db.execute(
        "SELECT o.id FROM items AS o JOIN items AS k ON k.source_id = ? AND k.guid = o.guid "
        "WHERE o.source_id = ? AND o.guid IS NOT NULL",
        (into, old),
    ).fetchall()
    for (twin,) in twins:
        store.delete_item(twin)
    store.db.execute("PRAGMA foreign_keys=OFF")
    store.db.execute("BEGIN")
    try:
        if store.db.execute("SELECT 1 FROM sources WHERE id = ?", (into,)).fetchone() is None:
            cols = ", ".join(
                r[1] for r in store.db.execute("PRAGMA table_info(sources)") if r[1] != "id"
            )
            store.db.execute(
                f"INSERT INTO sources (id, {cols}) SELECT ?, {cols} FROM sources "  # noqa: S608
                "WHERE id = ?",
                (into, old),
            )
        store.db.execute("UPDATE items SET source_id = ? WHERE source_id = ?", (into, old))
        for table, cols in (
            ("source_seen", "guid, first_seen_at"),
            ("source_cursors", "cursor, updated_at"),
        ):
            store.db.execute(
                f"INSERT OR IGNORE INTO {table} (source_id, {cols}) "  # noqa: S608
                f"SELECT ?, {cols} FROM {table} WHERE source_id = ?",
                (into, old),
            )
            store.db.execute(f"DELETE FROM {table} WHERE source_id = ?", (old,))  # noqa: S608
        store.db.execute("DELETE FROM sources WHERE id = ?", (old,))
        store.db.execute("COMMIT")
    except Exception:
        store.db.execute("ROLLBACK")
        raise
    finally:
        store.db.execute("PRAGMA foreign_keys=ON")
