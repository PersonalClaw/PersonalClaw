"""Read-only facts about this host's processes: their parents, their command lines, where they run.

Four features ask the OS the same questions, and one place asks them: the run web preview (which
listening processes work inside a run's workspace, ``workflows/web_preview.py``), the app
runtime (which processes still run an app's files after the app was unloaded,
``apps/app_runtime.py``), ``personalclaw stop`` (whether the pid a gateway recorded still runs
that gateway), and the end of a run (which processes carry its marker, ``run_processes``). Asking
has traps of its own — ``lsof -p ""`` selects every process on the host rather than none, and
``lsof`` exits non-zero when it merely found nothing — and a second copy of the asking would have
to relearn each of them.

``/proc`` is read wherever there is one (Linux), because it needs no program: the published
container image ships neither ``ps`` nor ``lsof``, and a table read from a missing ``ps`` is an
empty one, which a caller reads as "nothing is running". Everywhere else the probes run ``ps`` and
``lsof``. Every probe is a CONSTANT argv plus pids the caller has just read: never a model's, a
turn's or an app's words, no shell, ``check=False``, and a short timeout. A probe that fails reads
as "nothing found", never as an error. A process's own facts and its environment (:func:`process`,
:func:`environment_value`) come from the kernel itself on macOS too (``libproc`` and ``sysctl``),
so reading every process costs no program and no fork.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import shutil
import struct
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Seconds any probe may take. A wedged ``lsof`` must degrade whatever asked, never hang it.
PROBE_TIMEOUT = 4.0

#: Where Linux publishes its process facts. A module constant so a test can stand up a fake one.
PROC = Path("/proc")


def run_probe(argv: list[str]) -> str:
    """Best-effort capture of *argv*'s stdout. Never raises; returns "" on any failure."""
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell, no user-supplied words
            argv,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("process probe failed: %s", argv[0], exc_info=True)
        return ""
    # `lsof` exits non-zero when it merely found nothing, so stdout is read regardless.
    return proc.stdout or ""


def parse_lsof_cwds(out: str) -> dict[int, str]:
    """Parse ``lsof -a -p <pids> -d cwd -Fn`` into ``{pid: cwd}``."""
    cwds: dict[int, str] = {}
    pid = 0
    for line in out.splitlines():
        if not line:
            continue
        tag, rest = line[0], line[1:]
        if tag == "p":
            try:
                pid = int(rest)
            except ValueError:
                pid = 0
        elif tag == "n" and pid > 0:
            cwds[pid] = rest
    return cwds


def working_dirs(pids: list[int]) -> dict[int, str]:
    """The working directory of each pid in *pids* that can be read, as the OS keeps it (resolved).

    ``pids`` MUST be non-empty: ``lsof -p ""`` does not select nothing, it selects EVERY
    process — measured while building the web preview, and it would have turned a scoped probe
    into a host-wide one. The guard is here rather than at the call site so it cannot be
    forgotten. ``/proc`` where there is one (Linux); ``lsof`` elsewhere, one call for all of them.
    """
    if not pids:
        return {}
    proc_cwds: dict[int, str] = {}
    if PROC.is_dir():
        for pid in pids:
            try:
                proc_cwds[pid] = os.readlink(str(PROC / str(pid) / "cwd"))
            except OSError:
                continue
        if proc_cwds:
            return proc_cwds
    if not shutil.which("lsof"):
        return proc_cwds
    joined = ",".join(str(p) for p in pids)
    return parse_lsof_cwds(run_probe(["lsof", "-a", "-p", joined, "-d", "cwd", "-Fn"]))


def parse_ps_table(out: str) -> dict[int, tuple[int, str]]:
    """Parse ``ps -Awwo pid=,ppid=,command=`` into ``{pid: (ppid, command line)}``."""
    table: dict[int, tuple[int, str]] = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        try:
            table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
        except (ValueError, IndexError):
            continue
    return table


def _joined_argv(raw: bytes) -> str:
    """A ``/proc/<pid>/cmdline`` (NUL-separated words) as the one line ``ps`` would print."""
    return " ".join(word.decode("utf-8", "replace") for word in raw.split(b"\0") if word)


def command_line(pid: int) -> str:
    """The command line *pid* runs, its words joined by spaces; "" when it cannot be read.

    "" means no such process as much as an unreadable one, so a caller deciding whether to
    signal *pid* reads both as "not confirmed".
    """
    if pid <= 0:
        return ""
    if PROC.is_dir():
        try:
            return _joined_argv((PROC / str(pid) / "cmdline").read_bytes())
        except OSError:
            return ""
    return run_probe(["ps", "-ww", "-p", str(pid), "-o", "args="]).strip()


def _proc_table() -> dict[int, tuple[int, str]]:
    """:func:`process_table` read from ``/proc``."""
    table: dict[int, tuple[int, str]] = {}
    try:
        entries = [e for e in os.scandir(PROC) if e.name.isdigit()]
    except OSError:
        return table
    for entry in entries:
        try:
            stat = Path(entry.path, "stat").read_text(encoding="utf-8", errors="replace")
            raw = Path(entry.path, "cmdline").read_bytes()
        except OSError:
            continue  # it exited between the listing and the read
        # `pid (comm) state ppid …`. The program name in the parentheses may itself hold spaces
        # and parentheses, so the fields are counted from the LAST `)`.
        try:
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (IndexError, ValueError):
            continue
        table[int(entry.name)] = (ppid, _joined_argv(raw))
    return table


def process_table() -> dict[int, tuple[int, str]]:
    """``{pid: (ppid, command line)}`` for every process on this host; empty when none can be read.

    ``-ww`` keeps ``ps``'s command line whole: without it Linux ``ps`` clips it to the screen
    width when stdout is not a terminal, and a path at the end of a long command would never
    match.
    """
    if PROC.is_dir():
        return _proc_table()
    return parse_ps_table(run_probe(["ps", "-Awwo", "pid=,ppid=,command="]))


def descendants(of: int, table: dict[int, tuple[int, str]]) -> dict[int, frozenset[int]]:
    """Every process below pid *of* in *table*, each with the pids between it and *of*."""
    children: dict[int, list[int]] = {}
    for pid, (ppid, _command) in table.items():
        children.setdefault(ppid, []).append(pid)
    above: dict[int, frozenset[int]] = {}
    pending: list[tuple[int, frozenset[int]]] = [(of, frozenset())]
    while pending:
        parent, chain = pending.pop()
        for child in children.get(parent, ()):
            if child not in above:
                above[child] = chain
                pending.append((child, chain | {child}))
    return above


# ── One process's own facts, and the environment it started with ─────────────────────────────────


@dataclass(frozen=True)
class Process:
    """What the kernel says of one running process, read in one call."""

    pid: int
    ppid: int
    pgid: int
    uid: int
    #: When it started, in the kernel's own count. A pid the system later gives another program
    #: starts at another moment, so ``(pid, started)`` names one process for as long as it runs.
    started: int


def processes() -> dict[int, Process]:
    """Every running process whose facts this user can read, by pid; ``{}`` where none can be.

    ``/proc`` on Linux and ``libproc`` on macOS, so no program runs to ask. A zombie is not among
    them: it has exited, and what is left of it is its parent's to collect. On macOS another user's
    processes are not either, since the kernel tells this user nothing about them."""
    if PROC.is_dir():
        try:
            pids = [int(e.name) for e in os.scandir(PROC) if e.name.isdigit()]
        except OSError:
            return {}
        found = (_proc_process(pid) for pid in pids)
    elif sys.platform == "darwin":
        found = (_libproc_process(pid) for pid in _libproc_pids())
    else:
        return {}
    return {p.pid: p for p in found if p is not None}


def process(pid: int) -> Process | None:
    """:func:`processes`' facts for the one process *pid*, or ``None`` when it is not running."""
    if pid <= 0:
        return None
    if PROC.is_dir():
        return _proc_process(pid)
    if sys.platform == "darwin":
        return _libproc_process(pid)
    return None


def environment_value(pid: int, name: str) -> str | None:
    """*name*'s value in the environment process *pid* started with, or ``None``: it has no such
    variable, or its environment cannot be read. That is another user's process, one that has
    exited, and on macOS one of the system's own programs (a shell, ``sleep``, ``tail``), whose
    environment the kernel does not show even to the user running it.

    The environment as the process STARTED with it: what it changed in its own since is not seen,
    and a program that writes over the memory holding it (a server setting its process title over
    its arguments) shows what it wrote there."""
    if pid <= 0:
        return None
    if PROC.is_dir():
        try:
            raw = (PROC / str(pid) / "environ").read_bytes()
        except OSError:
            return None
        return _value_in(raw.split(b"\0"), name)
    if sys.platform == "darwin":
        raw = _darwin_procargs(pid)
        return environment_in_procargs(raw, name) if raw else None
    return None


def _value_in(items: list[bytes], name: str) -> str | None:
    prefix = name.encode("utf-8") + b"="
    for item in items:
        if item.startswith(prefix):
            return item[len(prefix) :].decode("utf-8", "replace")
    return None


def _proc_process(pid: int) -> Process | None:
    """:func:`process` from ``/proc/<pid>/stat``: ``pid (comm) state ppid pgrp …``, where the
    program name in the parentheses may itself hold spaces, so the fields count from the LAST
    ``)``, and the start is field 22."""
    try:
        stat = (PROC / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
        uid = os.stat(PROC / str(pid)).st_uid
        fields = stat.rsplit(")", 1)[1].split()
        state, ppid, pgid, started = fields[0], int(fields[1]), int(fields[2]), int(fields[19])
    except (OSError, IndexError, ValueError):
        return None
    if state in ("Z", "X", "x"):
        return None
    return Process(pid=pid, ppid=ppid, pgid=pgid, uid=uid, started=started)


# macOS: `struct proc_bsdinfo` (<sys/proc_info.h>), as `proc_pidinfo(PROC_PIDTBSDINFO)` fills it.
_PROC_PIDTBSDINFO = 3
_BSDINFO_SIZE = 136
_BSDINFO_STATUS, _BSDINFO_PPID, _BSDINFO_UID, _BSDINFO_PGID, _BSDINFO_START = 4, 16, 20, 100, 120
_SZOMB = 5
# `sysctl({CTL_KERN, KERN_PROCARGS2, pid})`: the arguments and the environment a process started
# with, the same area `ps` reads them from.
_CTL_KERN, _KERN_ARGMAX, _KERN_PROCARGS2 = 1, 8, 49

_DARWIN: dict[str, Any] = {}
_DARWIN_LOCK = threading.Lock()


def _darwin_lib(name: str) -> Any:
    """``libproc`` or ``libc``, loaded the first time a process is read, never at import."""
    lib = _DARWIN.get(name)
    if lib is None:
        lib = ctypes.CDLL(ctypes.util.find_library(name) or f"/usr/lib/lib{name}.dylib")
        if name == "proc":
            lib.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
            lib.proc_listallpids.restype = ctypes.c_int
            lib.proc_pidinfo.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint64,
                ctypes.c_void_p,
                ctypes.c_int,
            ]
            lib.proc_pidinfo.restype = ctypes.c_int
        _DARWIN[name] = lib
    return lib


def _libproc_pids() -> list[int]:
    try:
        lib = _darwin_lib("proc")
        count = lib.proc_listallpids(None, 0)
        if count <= 0:
            return []
        buf = (ctypes.c_int * (count + 256))()
        got = lib.proc_listallpids(buf, ctypes.sizeof(buf))
    except OSError:
        return []
    return [buf[i] for i in range(max(got, 0)) if buf[i] > 0]


def _libproc_process(pid: int) -> Process | None:
    try:
        lib = _darwin_lib("proc")
        info = ctypes.create_string_buffer(_BSDINFO_SIZE)
        if lib.proc_pidinfo(pid, _PROC_PIDTBSDINFO, 0, info, _BSDINFO_SIZE) != _BSDINFO_SIZE:
            return None
    except OSError:
        return None
    raw = info.raw
    if struct.unpack_from("=I", raw, _BSDINFO_STATUS)[0] == _SZOMB:
        return None
    ppid, uid = struct.unpack_from("=II", raw, _BSDINFO_PPID)
    (pgid,) = struct.unpack_from("=I", raw, _BSDINFO_PGID)
    seconds, micros = struct.unpack_from("=QQ", raw, _BSDINFO_START)
    return Process(pid=pid, ppid=ppid, pgid=pgid, uid=uid, started=seconds * 1_000_000 + micros)


def _darwin_procargs(pid: int) -> bytes:
    """``KERN_PROCARGS2`` for *pid*, or ``b""``. One buffer of the system's argument limit, kept
    for every read: a sweep reads every process this user runs."""
    try:
        libc = _darwin_lib("c")
        with _DARWIN_LOCK:
            area = _DARWIN.get("area")
            if area is None:
                limit = ctypes.c_int(0)
                size = ctypes.c_size_t(ctypes.sizeof(limit))
                mib2 = (ctypes.c_int * 2)(_CTL_KERN, _KERN_ARGMAX)
                if libc.sysctl(mib2, 2, ctypes.byref(limit), ctypes.byref(size), None, 0) != 0:
                    return b""
                area = _DARWIN["area"] = ctypes.create_string_buffer(max(limit.value, 4096))
            size = ctypes.c_size_t(ctypes.sizeof(area))
            mib3 = (ctypes.c_int * 3)(_CTL_KERN, _KERN_PROCARGS2, pid)
            if libc.sysctl(mib3, 3, area, ctypes.byref(size), None, 0) != 0:
                return b""
            return ctypes.string_at(area, size.value)
    except OSError:
        return b""


def environment_in_procargs(raw: bytes, name: str) -> str | None:
    """*name*'s value in a ``KERN_PROCARGS2`` area: the argument count, the program's path and its
    padding, the arguments, then the environment up to an empty string. ``None`` when it is not
    there, or the area ends before it (the kernel stops after the arguments for a program whose
    environment it does not show)."""
    if len(raw) < 4:
        return None
    (argc,) = struct.unpack_from("=i", raw, 0)
    pos = raw.find(b"\0", 4)
    if pos < 0:
        return None
    while pos < len(raw) and raw[pos] == 0:
        pos += 1
    for _ in range(max(argc, 0)):
        end = raw.find(b"\0", pos)
        if end < 0:
            return None
        pos = end + 1
    items: list[bytes] = []
    while pos < len(raw):
        end = raw.find(b"\0", pos)
        item = raw[pos : end if end >= 0 else len(raw)]
        if not item:
            break
        items.append(item)
        if end < 0:
            break
        pos = end + 1
    return _value_in(items, name)
