"""The fence the Linux sandbox puts on PersonalClaw's home, stood in for in one process on any host.

On Linux an agent CLI runs in the host's sandbox, and so does the tool server it starts
(``personalclaw mcp-core``). The sandbox binds the home onto itself read-only and binds every entry
it holds at launch back writable, except the owner-only ones (``sandbox._build_launcher_script``,
``owner_only``). Inside it nothing can be added, replaced, renamed or removed at the top of the
home; a file that was there at launch is still written in place, and so is anything in a folder
that was there; an owner-only entry is not written at all. A top-level entry the gateway replaces
while the process runs (an atomic save, a log's rotation) is a new file the process can only read.

This module stands in for that fence where the host offers no such sandbox, for the one process it
is the ``sitecustomize`` of (:func:`fenced`): each operation the fence would refuse that the
process makes from Python is refused with the error the kernel gives (EROFS), and recorded in a
log a test reads (:func:`refused`). What a process writes below Python (SQLite's journal) is not
seen; the real sandbox is driven on a Linux host that runs it.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

#: The home to fence, and where each refusal is recorded, as the fenced process reads them.
HOME_VARIABLE = "PERSONALCLAW_TEST_FENCED_HOME"
LOG_VARIABLE = "PERSONALCLAW_TEST_FENCE_LOG"

_WRITES = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
#: The operations that change an entry's name or presence, by the audit event each raises, with the
#: positions of the paths in its arguments.
_NAMING = {
    "os.mkdir": (0,),
    "os.rename": (0, 1),
    "os.remove": (0,),
    "os.rmdir": (0,),
    "os.symlink": (1,),
    "os.link": (1,),
}


def fenced(folder: Path, home: Path, log: Path) -> dict[str, str]:
    """The environment that starts a Python process with *home* fenced: this module copied into
    *folder* as its ``sitecustomize``, and where its refusals go (*log*)."""
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(__file__, folder / "sitecustomize.py")
    return {"PYTHONPATH": str(folder), HOME_VARIABLE: str(home), LOG_VARIABLE: str(log)}


def refused(log: Path) -> list[dict[str, Any]]:
    """Each operation the fence refused, as recorded: the audit event and the path it named."""
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def _install(home: str, log: str) -> None:
    from personalclaw.owner_only import OWNER_ONLY_DIRS, OWNER_ONLY_FILES

    root = os.path.realpath(home)
    owner_only = {*OWNER_ONLY_FILES, *OWNER_ONLY_DIRS}
    # The entries the sandbox binds back writable: each one there now, by the file it is.
    bound: dict[str, tuple[int, int]] = {}
    for name in os.listdir(root):
        if name in owner_only:
            continue
        info = os.lstat(os.path.join(root, name))
        bound[name] = (info.st_dev, info.st_ino)
    record = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)

    def entry(path: Any) -> tuple[str, bool] | None:
        """The top-level entry *path* is or is under, and whether it is that entry itself."""
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        if not isinstance(path, (str, os.PathLike)):
            return None
        full = os.path.realpath(os.fspath(path))
        if full == root or not full.startswith(root + os.sep):
            return None
        rest = full[len(root) + 1 :]
        name = rest.split(os.sep, 1)[0]
        return name, rest == name

    def still_bound(name: str) -> bool:
        try:
            info = os.lstat(os.path.join(root, name))
        except OSError:
            return False
        return bound.get(name) == (info.st_dev, info.st_ino)

    def refuse(event: str, path: Any) -> None:
        line = json.dumps({"event": event, "path": os.fsdecode(path)}) + "\n"
        os.write(record, line.encode())
        raise OSError(errno.EROFS, os.strerror(errno.EROFS), os.fsdecode(path))

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if event == "open":
            path, _mode, flags = (*args, None, None)[:3]
            if not isinstance(flags, int) or not flags & _WRITES:
                return
            where = entry(path)
            if where is not None and not still_bound(where[0]):
                refuse(event, path)
            return
        positions = _NAMING.get(event)
        if positions is None:
            return
        for position in positions:
            path = args[position] if position < len(args) else None
            where = entry(path)
            if where is None:
                continue
            name, itself = where
            if itself and event == "os.mkdir" and os.path.lexists(os.path.join(root, name)):
                continue  # the kernel answers a folder that is there as there (EEXIST)
            if itself or not still_bound(name):
                refuse(event, path)

    sys.addaudithook(hook)


if __name__ == "sitecustomize" and os.environ.get(HOME_VARIABLE):
    _install(os.environ[HOME_VARIABLE], os.environ[LOG_VARIABLE])
