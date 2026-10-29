"""A pytest worker no thread of its own can end: where each of its threads waits, read from outside
it, and its process ended there.

A worker blocked in native code that holds the GIL is beyond every Python thread in it:
pytest-timeout's alarm, the hard deadline's watchdog (``tests/hard_deadline.py``) and anything else
that would say where it stopped. Twice a Linux shard sat that way until its job ran out of time
about 45 minutes in (runs 40793606150 and 40864682473). All either left was faulthandler's Python
stacks at 120 s, which say nothing of what the native code waited on.

The kernel says, to a process allowed to trace the worker: each thread's state and CPU time, the
system call it is in (``/proc/<pid>/task/<tid>/syscall``), the kernel function it sleeps in
(``wchan``) and, to root alone, its kernel stack (``stack``). Under Yama's ``ptrace_scope`` 1, the
Ubuntu runners' setting, a process may trace its descendants, and run-job starts every step, so it
may read every process of a step's group. :func:`threads` reads one process's threads, and
:func:`group` several. Each is bounded, catches what it cannot read and names it, and caps what it
gives. Off Linux there is no ``/proc``, and each says so. A pytest step that runs out of time has
every process of its group still alive read this way before run-job stops the group.

Each process that runs tests publishes the deadline of the test it runs, beside the hard
deadline's dump (``tests/hard_deadline.py``'s ``armed_path``). :class:`Overdue`, which run-job calls
while a pytest step runs, ends a worker still running its test :data:`PAST` seconds after that
deadline: its own watchdog ends it at the deadline, so by then no Python thread of it can run. It
reads the worker's threads first, and writes them where the watchdog's own dump goes. xdist's
controller attaches that file to the test's report as it names the worker crashed. So a hang of
this kind is a red test with its waits two minutes in, not a shard lost at the job's limit.

Standard library only, like every ``tools/ci`` module.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import IO

#: Where the kernel lists each process.
PROC = Path("/proc")

#: The most of any one file under ``/proc`` a read takes: a kernel stack is a few hundred bytes.
_FILE = 4096
#: The most threads of one process read: a worker runs about 20.
_THREADS = 128
#: The most processes :func:`group` reads: a step's group is uv, pytest and its workers.
_PROCESSES = 16
#: The most characters :func:`threads` gives for one process, and :func:`group` for all.
PROCESS_LIMIT = 16_000
GROUP_LIMIT = 64_000
#: What a capped read ends with.
CUT = "... cut here\n"

#: A thread's state, as the third field of its ``stat`` gives it.
_STATES = {
    "R": "running",
    "S": "asleep",
    "D": "asleep, uninterruptibly",
    "T": "stopped",
    "t": "stopped by a tracer",
    "Z": "a zombie",
    "X": "dead",
    "I": "idle",
}

#: The system calls a blocked thread is most often in, by number, for each machine the runners
#: are: x86-64 and arm64 Linux. Any other is named by its number.
SYSCALLS: dict[str, dict[int, str]] = {
    "x86_64": {
        0: "read",
        1: "write",
        3: "close",
        7: "poll",
        9: "mmap",
        11: "munmap",
        17: "pread64",
        18: "pwrite64",
        19: "readv",
        20: "writev",
        23: "select",
        26: "msync",
        34: "pause",
        35: "nanosleep",
        42: "connect",
        43: "accept",
        44: "sendto",
        45: "recvfrom",
        46: "sendmsg",
        47: "recvmsg",
        61: "wait4",
        72: "fcntl",
        73: "flock",
        74: "fsync",
        75: "fdatasync",
        128: "rt_sigtimedwait",
        130: "rt_sigsuspend",
        202: "futex",
        230: "clock_nanosleep",
        232: "epoll_wait",
        247: "waitid",
        257: "openat",
        270: "pselect6",
        271: "ppoll",
        277: "sync_file_range",
        281: "epoll_pwait",
        288: "accept4",
        426: "io_uring_enter",
        441: "epoll_pwait2",
        449: "futex_waitv",
    },
    "aarch64": {
        22: "epoll_pwait",
        25: "fcntl",
        32: "flock",
        56: "openat",
        57: "close",
        63: "read",
        64: "write",
        65: "readv",
        66: "writev",
        67: "pread64",
        68: "pwrite64",
        72: "pselect6",
        73: "ppoll",
        82: "fsync",
        83: "fdatasync",
        84: "sync_file_range",
        95: "waitid",
        98: "futex",
        101: "nanosleep",
        115: "clock_nanosleep",
        133: "rt_sigsuspend",
        137: "rt_sigtimedwait",
        202: "accept",
        203: "connect",
        206: "sendto",
        207: "recvfrom",
        211: "sendmsg",
        212: "recvmsg",
        215: "munmap",
        222: "mmap",
        227: "msync",
        242: "accept4",
        260: "wait4",
        426: "io_uring_enter",
        441: "epoll_pwait2",
        449: "futex_waitv",
    },
}


def _read(path: Path) -> tuple[str | None, str]:
    """The first :data:`_FILE` bytes of *path*, or None and why it could not be read."""
    try:
        with open(path, "rb") as f:
            return f.read(_FILE).decode("utf-8", "replace"), ""
    except OSError as exc:
        return None, f"(unreadable: {type(exc).__name__})"


def _capped(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - len(CUT)] + CUT


def _stat(text: str) -> tuple[str, list[str]]:
    """A ``stat`` line's name and the fields after it: the name is in parentheses and may hold
    spaces and parentheses itself, so it ends at the last ``)``."""
    head, _, rest = text.rpartition(")")
    return head.partition("(")[2], rest.split()


def _call(raw: str, machine: str) -> str:
    """What a ``syscall`` file's line says its thread is in: a system call, by name where
    :data:`SYSCALLS` holds it; user code (``-1``); or nothing, as it runs."""
    first = raw.split(maxsplit=1)[0] if raw.strip() else ""
    if first == "running":
        return "running"
    if first == "-1":
        return "in user code"
    if first.isdigit():
        name = SYSCALLS.get(machine, {}).get(int(first))
        return f"in {name}" if name else f"in system call {first}"
    return f"in {raw.strip()[:40]!r}"


def _thread(task: Path, tid: int, machine: str, tick: float) -> list[str]:
    """A thread's lines: its name, state, CPU time, the call it is in and the kernel function it
    sleeps in; then its ``syscall`` line as the kernel gives it, whose last two fields are its
    stack pointer and program counter (for a thread glibc started, the address faulthandler names
    it by lies just above that stack pointer); then its kernel stack, where it can be read."""
    said = []
    stat, why = _read(task / "stat")
    if stat is None:
        said.append(f"its state {why}")
    else:
        name, fields = _stat(stat)
        state = _STATES.get(fields[0], fields[0]) if fields else "?"
        try:
            cpu = f"{(int(fields[11]) + int(fields[12])) / tick:.2f} s of CPU"
        except (IndexError, ValueError):
            cpu = "its CPU time unread"
        said.append(f"({name}) {state}, {cpu}")
    syscall, why = _read(task / "syscall")
    said.append(f"its system call {why}" if syscall is None else _call(syscall, machine))
    wchan, why = _read(task / "wchan")
    if wchan is None:
        said.append(f"its kernel wait {why}")
    elif wchan.strip() not in ("", "0"):
        said.append(f"waiting in {wchan.strip()}")
    lines = [f"  thread {tid} " + ", ".join(said)]
    if syscall is not None:
        lines.append(f"    syscall: {syscall.strip()}")
    stack, why = _read(task / "stack")
    if stack is None:
        lines.append(f"    kernel stack: {why}")
    elif stack.strip():
        lines.append("    kernel stack:")
        lines.extend(f"      {line}" for line in stack.strip().splitlines())
    return lines


def threads(pid: int, *, proc: Path = PROC, machine: str | None = None) -> str:
    """Process *pid*'s threads, each as :func:`_thread` gives it: at most :data:`_THREADS` of
    them, and :data:`PROCESS_LIMIT` characters in all. What can't be read is named, never raised.
    Its command line is never read: that takes the process's memory lock, which a process stuck
    in a page fault could hold, where everything read here answers without it."""
    if not proc.is_dir():
        return f"process {pid}: no {proc} on {sys.platform}, so its threads are not read\n"
    machine = machine or os.uname().machine
    tick = float(os.sysconf("SC_CLK_TCK")) if hasattr(os, "sysconf") else 100.0
    base = proc / str(pid)
    stat, why = _read(base / "stat")
    if stat is None:
        head = f"process {pid}: {why}"
    else:
        name, fields = _stat(stat)
        parent = fields[1] if len(fields) > 1 else "?"
        head = f"process {pid} ({name}), parent {parent}"
    try:
        tids = sorted(int(t) for t in os.listdir(base / "task") if t.isdigit())
    except OSError as exc:
        return f"{head}: its threads could not be listed ({type(exc).__name__})\n"
    lines = [f"{head}, {len(tids)} thread(s):"]
    for tid in tids[:_THREADS]:
        lines.extend(_thread(base / "task" / str(tid), tid, machine, tick))
    if len(tids) > _THREADS:
        lines.append(f"  ... and {len(tids) - _THREADS} more thread(s), not read")
    return _capped("\n".join(lines) + "\n", PROCESS_LIMIT)


def group(pids: Iterable[int], *, proc: Path = PROC, machine: str | None = None) -> str:
    """:func:`threads` for each of *pids*, at most :data:`_PROCESSES` of them and
    :data:`GROUP_LIMIT` characters in all."""
    pids = list(pids)
    out = [threads(pid, proc=proc, machine=machine) for pid in pids[:_PROCESSES]]
    if len(pids) > _PROCESSES:
        out.append(f"... and {len(pids) - _PROCESSES} more process(es), not read\n")
    return _capped("".join(out), GROUP_LIMIT)


#: The suffix of the file a process that runs tests publishes the test it runs in, beside the hard
#: deadline's dump (``tests/hard_deadline.py``): one line, ``<pid>\t<began>\t<deadline>\t<nodeid>``,
#: both times by the system's monotonic clock, or nothing between tests.
ARMED = ".armed"

#: Seconds past a test's own deadline before its worker is ended from outside. Its own watchdog
#: ends it at the deadline, once it has written every thread's stack, which a loaded runner may
#: take seconds to do. For a test with the suite's 30 s timeout, whose deadline is 90 s in, that is
#: 120 s in, as faulthandler dumps its Python stacks.
PAST = 30.0


def parse(line: str) -> tuple[int, float, float, str] | None:
    """A published line's pid, start, deadline and test, or None for one that is not whole."""
    if not line.endswith("\n"):
        return None
    parts = line[:-1].split("\t", 3)
    if len(parts) != 4 or not parts[3]:
        return None
    try:
        return int(parts[0]), float(parts[1]), float(parts[2]), parts[3]
    except ValueError:
        return None


def _in_group(pid: int, pgid: int) -> bool:
    """Whether *pid* is a process of group *pgid*, and not this one: never a process outside the
    step's group, whatever a published line names."""
    if pid <= 1 or pid == os.getpid():
        return False
    try:
        return os.getpgid(pid) == pgid
    except OSError:
        return False


class Overdue:
    """Called with a pytest step's group id while it runs (``run.run_job``'s *watch*): ends each
    process of the group whose test is :data:`PAST` seconds past its own deadline, once it has
    read its threads and written them where the hard deadline's dump goes, beside the published
    line, and into the job's log.

    A line is acted on only once two looks have read it the same, so never one half written, and
    only in a file written since the watch was made, as its step began: a worker of an earlier step
    that died mid-test (killed, or crashed) leaves its line behind, and the pid it names may be a
    process of this step's group by now. Each look reads at most every published file of the
    step's report folder."""

    def __init__(
        self,
        folder: Path,
        leg: str,
        *,
        past: float = PAST,
        clock: Callable[[], float] = time.monotonic,
        end: Callable[[int, int], None] = os.kill,
        read: Callable[[int], str] = threads,
        log: IO[str] | None = None,
        since: int | None = None,
    ) -> None:
        self.folder = folder
        self.pattern = f"hard-deadline-{leg}-*{ARMED}"
        self.past = past
        self._clock = clock
        self._end = end
        self._read = read
        self._log = log
        self._seen: dict[Path, str] = {}
        #: When the watch was made, by the clock a file's time is stamped by, in nanoseconds: a
        #: worker of its step starts a second or more after that, so all it writes is later.
        self._since = time.time_ns() if since is None else since

    @classmethod
    def beside(cls, junit: Path, **kw) -> Overdue:
        """For the run whose JUnit report is *junit*: its leg as the hard deadline names it, from
        ``junit-<leg>.xml``."""
        return cls(junit.parent, junit.stem.removeprefix("junit-") or "run", **kw)

    def __call__(self, pgid: int) -> None:
        try:
            published = sorted(self.folder.glob(self.pattern))
        except OSError:
            return
        for path in published:
            self._look(path, pgid)

    def _look(self, path: Path, pgid: int) -> None:
        try:
            if path.stat().st_mtime_ns < self._since:
                return
            line = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        twice = self._seen.get(path) == line
        self._seen[path] = line
        armed = parse(line) if twice else None
        if armed is None:
            return
        pid, began, deadline, nodeid = armed
        now = self._clock()
        if now < deadline + self.past or not _in_group(pid, pgid):
            return
        text = (
            f"hard deadline, from outside: {nodeid} was still running {now - began:.1f}s after it"
            f" started, {now - deadline:.1f}s past its deadline, and nothing in its process"
            f" (pid {pid}) had ended it, so run-job read its threads and ended it.\n\n"
            f"{self._read(pid)}"
        )
        try:
            path.with_suffix(".txt").write_text(text, encoding="utf-8")
        except OSError:
            pass
        print(text, file=self._log or sys.stderr, flush=True)
        try:
            self._end(pid, signal.SIGKILL)
        except OSError:
            pass
        self._seen.pop(path, None)
        try:
            path.unlink()
        except OSError:
            pass
