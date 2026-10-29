"""A tree a test runs many plants on, laid out once and put back as it was before each.

The contract rows copied their scaffold whole for every control (34,090 files over 795 controls),
and the apps tables copied the installed home whole for every row. A copy's time grows with what
it writes, so on a loaded disk it grows with the runner's load, not the test's work: the tiers
row's 27 copies took 6.9 s of its 19.1 s at a load of 25, and a whole-tree-tiers control timed
out in its copy at 70 s. :class:`Laid` reads the tree once, and :meth:`Laid.reset` puts a tree
back to it: it reads every file, and writes, removes or makes only what differs, so a plant that
changed one file costs one write.

Put back is every file's bytes, mode and modification time, every symbolic link's target, every
folder and its mode, and nothing else. A file is put back as a file of its own, what is at its
path unlinked first: a plant's hard link to a file outside the tree is never written through, so
a reset writes nothing it did not lay. Whatever a plant added is removed, a ``__pycache__`` an
import wrote included. That one matters: Python runs a compiled file whose recorded source time
and size match its source, and a file put back has its own time back, so a same-size edit
compiled before the reset would otherwise run again in the next plant.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class _File:
    data: bytes
    mode: int
    mtime_ns: int


def _entries(root: Path, at: str = "") -> Iterator[tuple[str, os.DirEntry]]:
    """Every entry under *root*, by its path from it, a folder before what it holds. A link is
    never followed."""
    with os.scandir(root / at if at else root) as listing:
        entries = sorted(listing, key=lambda e: e.name)
    for entry in entries:
        rel = f"{at}/{entry.name}" if at else entry.name
        yield rel, entry
        if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
            yield from _entries(root, rel)


def _mode(path: str) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


@dataclass
class Laid:
    """A tree as :meth:`take` read it, by each path from its root: each file, each symbolic
    link's target, each folder's mode."""

    files: dict[str, _File] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)
    folders: dict[str, int] = field(default_factory=dict)

    @classmethod
    def take(cls, root: Path) -> Laid:
        """The tree under *root* as it is now."""
        laid = cls()
        for rel, entry in _entries(root):
            if entry.is_symlink():
                laid.links[rel] = os.readlink(entry.path)
            elif entry.is_dir(follow_symlinks=False):
                laid.folders[rel] = _mode(entry.path)
            elif entry.is_file(follow_symlinks=False):
                st = entry.stat(follow_symlinks=False)
                data = Path(entry.path).read_bytes()
                laid.files[rel] = _File(data, stat.S_IMODE(st.st_mode), st.st_mtime_ns)
            else:
                raise ValueError(f"{rel} is not a file, a folder or a link: it cannot be laid")
        return laid

    def reset(self, root: Path) -> list[str]:
        """Put the tree under *root* back to this one, and return the paths it put back: what
        was added is removed, what was changed is written back, what was removed is made again."""
        undone: list[str] = []
        self._prune(root, "", undone)
        for rel in self.folders:  # each folder before what it holds
            if not (root / rel).is_dir():
                (root / rel).mkdir(mode=stat.S_IRWXU)
                undone.append(rel)
        for rel, laid in self.files.items():
            if not os.path.lexists(root / rel):
                self._write(root / rel, laid)
                undone.append(rel)
        for rel, target in self.links.items():
            if not os.path.lexists(root / rel):
                os.symlink(target, root / rel)
                undone.append(rel)
        for rel, mode in self.folders.items():  # last, so a folder laid unwritable was written
            if _mode(str(root / rel)) != mode:
                os.chmod(root / rel, mode)
                undone.append(rel)
        return sorted(set(undone))

    def _prune(self, root: Path, at: str, undone: list[str]) -> None:
        """Under the folder *at*: remove what the tree does not hold as it is there, write back a
        file that differs, and go into each folder the tree holds."""
        folder = root / at if at else root
        if at and _mode(str(folder)) & stat.S_IRWXU != stat.S_IRWXU:
            os.chmod(folder, _mode(str(folder)) | stat.S_IRWXU)  # writable while it is put back
        with os.scandir(folder) as listing:
            entries = list(listing)
        for entry in entries:
            rel = f"{at}/{entry.name}" if at else entry.name
            if entry.is_symlink():
                if self.links.get(rel) != os.readlink(entry.path):
                    os.unlink(entry.path)
                    undone.append(rel)
            elif entry.is_dir(follow_symlinks=False):
                if rel in self.folders:
                    self._prune(root, rel, undone)
                else:
                    shutil.rmtree(entry.path)
                    undone.append(rel)
            elif entry.is_file(follow_symlinks=False) and rel in self.files:
                laid, st = self.files[rel], entry.stat(follow_symlinks=False)
                if (
                    st.st_nlink > 1  # another name for a file: a plant's link to one outside
                    or stat.S_IMODE(st.st_mode) != laid.mode
                    or st.st_mtime_ns != laid.mtime_ns
                    or Path(entry.path).read_bytes() != laid.data
                ):
                    self._write(Path(entry.path), laid)
                    undone.append(rel)
            else:
                os.unlink(entry.path)
                undone.append(rel)

    @staticmethod
    def _write(path: Path, laid: _File) -> None:
        """*laid* at *path* as a file of its own: what is there is unlinked first, never written
        through or changed in place, so a plant's hard link to a file outside the tree leaves that
        file as it was. A reset writes nothing it did not lay."""
        if os.path.lexists(path):
            os.unlink(path)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb") as out:
            out.write(laid.data)
        os.chmod(path, laid.mode)
        os.utime(path, ns=(laid.mtime_ns, laid.mtime_ns))
