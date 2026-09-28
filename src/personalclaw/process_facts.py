"""Read-only facts about this host's processes: their parents, their command lines, where they run.

Three features ask the OS the same questions, and one place asks them: the run web preview (which
listening processes work inside a run's workspace, ``workflows/web_preview.py``), the app
runtime (which processes still run an app's files after the app was unloaded,
``apps/app_runtime.py``), and ``personalclaw stop`` (whether the pid a gateway recorded still runs
that gateway). Asking has traps of its own — ``lsof -p ""`` selects every process on the host
rather than none, and ``lsof`` exits non-zero when it merely found nothing — and a second copy of
the asking would have to relearn each of them.

``/proc`` is read wherever there is one (Linux), because it needs no program: the published
container image ships neither ``ps`` nor ``lsof``, and a table read from a missing ``ps`` is an
empty one, which a caller reads as "nothing is running". Everywhere else the probes run ``ps`` and
``lsof``. Every probe is a CONSTANT argv plus pids the caller has just read: never a model's, a
turn's or an app's words, no shell, ``check=False``, and a short timeout. A probe that fails reads
as "nothing found", never as an error.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

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
