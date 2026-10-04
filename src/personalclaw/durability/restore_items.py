"""How a restore and an import bring each part of an archive into the home, and say what became
of it.

A merge restore and a replace restore (``snapshot``) and the import of an export archive
(``portability``) put an archive's parts into the home one at a time: a file merged into the one the
home has, or brought in where it has none (:func:`merged_or_brought_in`), and a folder copied into
what the home's lacks (:func:`copy_tree_no_overwrite`). Each takes its path from
``durability.home_paths``, which leaves a part the home holds behind a link as it is and names it,
and each restore says what became of every part (:func:`said`, :func:`left_unchanged_line`), so a
part it left unchanged is never reported as done.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from personalclaw.durability import home_paths, sqlite_files


def left_out_of_restore(entry_path: str, rel: str) -> bool:
    """Whether a restore leaves ``rel``, a path inside the entry at ``entry_path``, out of the home.

    What capture leaves behind (the entry's ``derived_within``) is never planted either. An archive
    written before a path was left out still carries it, and planting it undoes the exclusion: an
    app's ``venv/`` came back without its interpreter but with its package receipt, so Install
    engine re-made the interpreter and skipped pip. A path another entry claims (``loop/loops.db``
    inside ``loop``) is that entry's to restore, so it is kept.
    """
    from personalclaw.durability import inventory as inv
    from personalclaw.portability import _is_derived_within

    if not _is_derived_within(entry_path, rel):
        return False
    owner = inv.claim_for(f"{entry_path}/{rel}")
    return owner is None or owner.path == entry_path


def copy_tree_no_overwrite(
    src: Path, home: Path, rel: str, left: list[str], *, entry_path: str = ""
) -> int:
    """Copy what the home's folder *rel* lacks of the archive's folder *src*, and return how many
    files that was: none through a link the home holds, which is named on *left*
    (``home_paths.landing``). Given the inventory ``entry_path`` it copies, leaves out what a
    restore never plants (:func:`left_out_of_restore`). A database is taken whole, as any other
    file is, and on its own; a sidecar never comes in, so no log lands beside a database it was not
    written with (``sqlite_files.bring_in``)."""
    copied = 0
    for item in src.rglob("*"):
        if item.is_symlink():
            continue
        inner = item.relative_to(src).as_posix()
        if entry_path and left_out_of_restore(entry_path, inner):
            continue
        target = home_paths.landing(home, f"{rel}/{inner}", left)
        if target is not None and item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif target is not None and item.is_file() and sqlite_files.bring_in(item, target):
            copied += 1
    return copied


def merged_or_brought_in(
    snap: Path, home: Path, rel: str, left: list[str], merge: Callable[[Path, Path], object]
) -> str:
    """The archive's file *rel* (in its unpacked copy *snap*) into the home: merged into the one
    the home has (*merge*), ``"merged"``, or brought in where it has none, ``"copied"``. ``""``
    when the archive does not hold it, the home holds a folder there, or a link is in the way, which
    is named on *left* (``home_paths.landing``)."""
    dst = home_paths.landing(home, rel, left) if (snap / rel).is_file() else None
    if dst is not None and dst.is_file():
        merge(snap / rel, dst)
        return "merged"
    return "copied" if dst is not None and sqlite_files.bring_in(snap / rel, dst) else ""


def said(what: str, left: list[str], since: int) -> None:
    """Say how the part *what* of a restore went: done, or what it put on *left* after its first
    *since*."""
    done = len(left) == since
    print(f"  ✅ {what}" if done else f"  ⚠️  {what}: left unchanged: {', '.join(left[since:])}")


def left_unchanged_line(left: list[str], what: str = "Merge") -> str:
    """The last line of a merge, or of a replace (*what*), that could not bring everything in:
    how many parts, and which."""
    parts = "1 part was" if len(left) == 1 else f"{len(left)} parts were"
    return f"⚠️  {what} finished, but {parts} left unchanged: {', '.join(left)}."
