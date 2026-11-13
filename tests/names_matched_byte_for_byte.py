"""A filesystem that matches names byte for byte, as Linux's do, on one that compares them in one
Unicode form, as macOS's APFS does.

While :func:`matched_byte_for_byte` is entered, ``os.stat`` and ``os.lstat`` (and so every
``os.path`` test built on them: ``exists``, ``lexists``, ``isdir``, ``islink``, ``realpath``),
``os.listdir`` and ``os.scandir`` answer for a path inside the folder it is given only when each
part below that folder is in its parent's listing exactly as written; any other is not there. A
listing hands back each name as it was made, on either filesystem. A check that asks the filesystem
for a name by one spelling, where the name is kept in another, is then caught on a Mac as it is on
Linux. Only the names inside the folder are compared, so a lookup costs a listing of the few
folders a test made, not of every folder above them.
"""

from __future__ import annotations

import contextlib
import errno
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

_REAL = {name: getattr(os, name) for name in ("stat", "lstat", "listdir", "scandir")}


def _as_written(path, roots: tuple[str, ...]) -> bool:
    """Whether each part of *path* below one of *roots* is in its parent's listing exactly as
    written; a path outside them is taken as written."""
    if isinstance(path, int):
        return True
    path = os.path.abspath(os.fsdecode(path))
    root = next((r for r in roots if path == r or path.startswith(r + os.sep)), None)
    if root is None:
        return True
    here = root
    for part in path[len(root) :].split(os.sep):
        if not part:
            continue
        try:
            if part not in _REAL["listdir"](here):
                return False
        except OSError:
            return False
        here = os.path.join(here, part)
    return True


def _by_bytes(real, roots: tuple[str, ...]):
    def answer(path=".", *args, **kwargs):
        if not _as_written(path, roots):
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), os.fsdecode(path))
        return real(path, *args, **kwargs)

    return answer


@contextlib.contextmanager
def matched_byte_for_byte(monkeypatch: pytest.MonkeyPatch, inside: Path) -> Iterator[None]:
    """Lookups and listings of the names inside *inside* answer by bytes until the block ends."""
    roots = tuple(dict.fromkeys([str(inside), os.path.realpath(inside)]))
    with monkeypatch.context() as patched:
        for name, real in _REAL.items():
            patched.setattr(os, name, _by_bytes(real, roots))
        yield
