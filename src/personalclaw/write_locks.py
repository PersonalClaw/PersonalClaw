"""The lock a file is written under, for the writers that reach any file.

Two kinds of file are read by a task of the gateway that then waits and writes them again:
HEARTBEAT.md, whose finished tasks a heartbeat pass takes out once its turns end
(``heartbeat.run_tasks``), and the memory documents, each memory's preferences.md and projects.md,
which a consolidation rewrites once its model answers (``memory.MemoryStore.rewrite``), with the
days of its daily history, which the consolidation appends to and the Memory page saves one by one.
Each reads the file again when it is done waiting, and writes under the file's lock; every other
writer of the file holds that lock across its own read and write, so none lands between another's
read and its write.

The writers that know the file take its lock where they write it (``heartbeat.hold_queue``,
``memory.hold_documents``). The writers that reach any file, the Files editor's save and the
agent's ``write_file`` and ``edit_file``, take it here: :func:`write_lock` holds the lock its path
is written under, and nothing for any other path. A file-backed artifact's write-through and a
restore from the workspace's history write in one step on the event loop, where a heartbeat pass
and a consolidation write too, so neither lands inside theirs. A command the agent's shell runs,
or another program, takes no lock; it can meet one of these writers only in the instant that
writer writes the file, never across the minutes it waits.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

_T = TypeVar("_T")


@contextmanager
def write_lock(path: Path | str) -> Iterator[None]:
    """Hold the lock *path* is written under while it is read and written: the heartbeat queue's
    for HEARTBEAT.md, the memory documents' for a preferences.md, a projects.md or a day of the
    daily history (``memory.is_document``), none for any other path. Neither lock is re-entrant:
    nothing done while it is held may take it again."""
    from personalclaw import heartbeat, memory

    if heartbeat.is_queue_file(path):
        with heartbeat.hold_queue():
            yield
    elif memory.is_document(path):
        with memory.hold_documents():
            yield
    else:
        yield


def write_locked(path: Path | str, write: Callable[[], _T]) -> Callable[[], _T]:
    """*write*, run under :func:`write_lock` for *path*: the form for a writer that reads and writes
    the file on a worker thread (the agent's ``write_file`` and ``edit_file``), so the lock is held
    on that thread, around both."""

    def locked() -> _T:
        with write_lock(path):
            return write()

    return locked
