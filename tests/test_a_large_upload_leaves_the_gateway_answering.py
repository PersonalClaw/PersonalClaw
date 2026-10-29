"""A large upload leaves the gateway answering while it lands.

Measured: completing a resumable upload copied every part into one file and hashed it on the event
loop, and a knowledge upload sent in one request was moved and hashed there too. On the loop,
assembling a 512 MB upload stopped every other request for up to 0.34 s and hashing it for 0.22 s,
and a 2 GB one for as long as copying 2 GB takes: no request, socket message or approval was
answered meanwhile. The content scan a text-like upload gets held the loop for its whole run
(0.45 s for a 512 KB window of code).

The work on the file now runs off the loop: assembling, moving, hashing and removing it in worker
threads (file reads and writes and the hash leave the interpreter lock, so the loop keeps its
turns), and the scan in a child process (its parse of the window holds the lock: in a worker
thread it still stopped the loop for 0.12 s).

The threshold, for each case below: while the upload lands, no single step of the event loop
(one callback it runs, during which it runs nothing else) takes :data:`_BOUND` (0.1 s), and every
health check asked meanwhile answers within it. A step is timed as asyncio's debug mode times a
slow callback: a blocking call on the loop is inside one, a wait on the disk included, while time
the loop spends idle in its selector, given CPU or not, is between steps (:class:`_Probe`). Time
the kernel counts as ready to run with no CPU to run on is left out of both: that is the runner's
load, not anything the loop waited on (:class:`_NoCpu`).

A macOS runner that reads no thread's instruction count can leave none of it out, so there the
probe measures wall time, and a virtual machine that stops the whole process for a tenth of a
second fails a reading that no block of the loop did. A reading over the bound only by time the
process's own count says was ready is measured once more, on a fresh gateway, and the second is
judged (:func:`_judged`): a block every upload makes is in both and fails, while a stall of the
runner's passes. The limit: a block that comes only sometimes, or once a process (a lazy import on
the loop), can pass that second measurement on macOS. Linux counts each thread's own time ready,
leaves out exactly the loop's, and catches it.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import gc
import json
import os
import select
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import warnings
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from table_rows import row

from personalclaw.dashboard.handlers import knowledge as kn_handlers
from personalclaw.dashboard.handlers_system import api_healthz
from personalclaw.dashboard.routes import _register_upload_routes
from personalclaw.knowledge.pipeline import ensure_nodes_registered
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.sel import SecurityEventLog, sel
from personalclaw.uploads.store import UploadStore

_MB = 1024 * 1024
#: The longest one step of the event loop may take, and a health check, while it lands.
_BOUND = 0.1
#: A step at least this long has the time it waited for a CPU left out; a shorter one is counted
#: in wall time, which is already far under the bound (on macOS, it skips its second snapshot).
_COUNTED = 0.01
#: The resumable upload: 256 MB, assembled from 32 parts and hashed (on the loop: about 0.19 s).
_LARGE = 256 * _MB
#: The single-request upload: 384 MB, moved and hashed (on the loop: about 0.18 s).
_LARGER = 384 * _MB


def _bytes(size: int) -> bytes:
    """*size* bytes of a file: what is in it does not change how long it takes to copy or hash."""
    return bytes(range(256)) * (size // 256)


@pytest.fixture()
def gateway(tmp_path, monkeypatch):
    """The real upload routes, the knowledge ingest route and the health check, over a home in tmp.

    Free disk reads as plenty, so a large upload is kept as separate parts and assembled at its
    complete, whatever this machine has free."""
    return _a_gateway(tmp_path, monkeypatch)


@pytest.fixture()
def again(tmp_path, monkeypatch):
    """A fresh gateway for a second measurement (:func:`_judged`), as a restarted one is: a home,
    a library and a security log of its own, so a write its first upload makes once is made again.
    """

    def fresh():
        SecurityEventLog._instance = None
        SecurityEventLog._initialized = False
        return _a_gateway(_mkdir(tmp_path / "again"), monkeypatch)

    return fresh


def _a_gateway(tmp_path, monkeypatch):
    """:func:`gateway`'s, under *tmp_path*."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    # The security log made before anything is served, its key with it, as the gateway's start
    # makes it (dashboard.server): never by the first event a request writes.
    sel()
    monkeypatch.setattr(tempfile, "tempdir", str(_mkdir(tmp_path / "tmp")))
    monkeypatch.setattr("personalclaw.uploads.store._free_bytes", lambda _path: 1 << 40)
    # A chat attachment's extraction is the chat's business, not this upload's.
    monkeypatch.setattr(
        "personalclaw.dashboard.attachment_extract.get_extractor",
        lambda: SimpleNamespace(start=lambda *a, **k: None),
    )
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.files._upload_dir", lambda: tmp_path / "home" / "uploads"
    )
    ensure_nodes_registered()
    queued: list[str] = []
    app = web.Application(client_max_size=4 * 1024 * _MB)
    app["upload_store"] = UploadStore(tmp_path / "home" / "uploads" / ".parts")
    app["state"] = SimpleNamespace(
        knowledge_store=KnowledgeStore(str(tmp_path / "knowledge.db")),
        knowledge_ingest_queue=lambda: SimpleNamespace(enqueue=queued.append),
    )
    _register_upload_routes(app)
    app.router.add_post("/api/knowledge/ingest", kn_handlers.ingest_file)
    app.router.add_get("/api/healthz", api_healthz)
    return SimpleNamespace(
        app=app,
        parts=tmp_path / "home" / "uploads" / ".parts",
        uploads=tmp_path / "home" / "uploads",
        store=app["state"].knowledge_store,
        queued=queued,
    )


def _mkdir(path):
    path.mkdir(parents=True, exist_ok=True)
    return path


class _MachTimebase(ctypes.Structure):
    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


class _RusageInfoV4(ctypes.Structure):
    """``struct rusage_info_v4`` from macOS's ``<sys/resource.h>``, whose last field is
    ``ri_runnable_time``. Times are in Mach absolute time units."""

    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [
        (f"ri_{name}", ctypes.c_uint64)
        for name in (
            "user_time system_time pkg_idle_wkups interrupt_wkups pageins wired_size "
            "resident_size phys_footprint proc_start_abstime proc_exit_abstime child_user_time "
            "child_system_time child_pkg_idle_wkups child_interrupt_wkups child_pageins "
            "child_elapsed_abstime diskio_bytesread diskio_byteswritten cpu_time_qos_default "
            "cpu_time_qos_maintenance cpu_time_qos_background cpu_time_qos_utility "
            "cpu_time_qos_legacy cpu_time_qos_user_initiated cpu_time_qos_user_interactive "
            "billed_system_time serviced_system_time logical_writes lifetime_max_phys_footprint "
            "instructions cycles billed_energy serviced_energy interval_max_phys_footprint "
            "runnable_time"
        ).split()
    ]


class _ProcThreadInfo(ctypes.Structure):
    """``struct proc_threadinfo`` from macOS's ``<sys/proc_info.h>``. Times are in ns."""

    _fields_ = (
        [("pth_user_time", ctypes.c_uint64), ("pth_system_time", ctypes.c_uint64)]
        + [
            (f"pth_{name}", ctypes.c_int32)
            for name in (
                "cpu_usage policy run_state flags sleep_time curpri priority maxpriority"
            ).split()
        ]
        + [("pth_name", ctypes.c_char * 64)]
    )


class _ThreadCountsAtALevel(ctypes.Structure):
    """``struct proc_threadcounts_data`` from macOS's ``<sys/proc_info.h>``: a thread's counts on
    one kind of core."""

    _fields_ = [
        (name, ctypes.c_uint64)
        for name in "instructions cycles user_time_mach system_time_mach energy_nj".split()
    ]


class _ProcThreadCounts(ctypes.Structure):
    """``struct proc_threadcounts`` (``PROC_PIDTHREADCOUNTS``): a thread's counts on each kind of
    core, as many as the call says it wrote."""

    _fields_ = [
        ("ptc_len", ctypes.c_uint16),
        ("ptc_reserved0", ctypes.c_uint16),
        ("ptc_reserved1", ctypes.c_uint32),
        ("ptc_counts", _ThreadCountsAtALevel * 8),
    ]


class _ProcTaskInfo(ctypes.Structure):
    """``struct proc_taskinfo`` from macOS's ``<sys/proc_info.h>``. Its totals count every thread
    the process has had, its ``threads`` fields only the ones it has: the difference is the CPU
    time of the threads that have ended. Times are in Mach absolute time units."""

    _fields_ = [
        (f"pti_{name}", ctypes.c_uint64)
        for name in (
            "virtual_size resident_size total_user total_system threads_user threads_system"
        ).split()
    ] + [
        (f"pti_{name}", ctypes.c_int32)
        for name in (
            "policy faults pageins cow_faults messages_sent messages_received syscalls_mach "
            "syscalls_unix csw threadnum numrunning priority"
        ).split()
    ]


#: macOS run states (``<mach/thread_info.h>``) in which a thread is neither running nor ready to
#: run: stopped, waiting, waiting uninterruptibly, halted.
_NOT_READY = frozenset({2, 3, 4, 5})
#: Those run states by name, and running or ready (1), for the probe's report.
_STATES = {
    1: "running or ready",
    2: "stopped",
    3: "waiting",
    4: "waiting uninterruptibly",
    5: "halted",
}


class _NoCpu:
    """The seconds some threads spent ready to run with no CPU to run on, as the kernel counts
    them: what a loaded runner takes from the event loop without the loop waiting on anything. A
    thread blocked on a read, a write, a sleep or a lock is not ready to run, so nothing it waits
    on is ever in the count.

    What the probe leaves out, of a step and of a health check's answer alike, is the loop
    thread's own count. Its ready time and its blocked time never overlap, so a step or an answer
    less it still holds every block of the loop. Two threads' counts can overlap: a worker that
    served a health check can wait for a CPU while the loop is blocked on a read, and an answer
    less both would lose the block.

    Linux counts them for each thread, as ``run_delay`` in its ``schedstat``, so the loop
    thread's count is exactly its own (:meth:`loop_waited`, read from a file the loop thread
    holds open).

    macOS counts them only for the whole process: ``ri_runnable_time`` is the time its threads
    were running or ready to run, so less the CPU time they used, the time they were ready. So
    :meth:`waited` counts for the loop thread only the ready time no other thread can account
    for. Any other thread that ran, or was ready, between two :meth:`snapshot` calls could have
    been ready for all of that time less its CPU, and that much is taken off the process's count,
    which stops at 0. A thread neither running nor ready at either end that ran no instruction
    between accounts for nothing, since to be ready it would have had to run before it could wait
    again. Whether it ran is read from its instruction count (``PROC_PIDTHREADCOUNTS``), which any
    run moves, and not from its CPU time, which macOS gives in whole microseconds: a thread woken
    in a crowd, which waits for a CPU and then runs under a microsecond before it waits again,
    reads as one that never ran. On a CI runner two such threads were taken for idle beside a
    step of 1.474 s, the process's 1.793 s ready was all left out as the loop's, and the step's
    0.2 s block with it (run 40496838581). A thread whose count cannot be read is taken to have
    run. So a thread beside the loop that waits for a CPU is never counted as the loop's wait,
    and a step or answer beside such a thread is counted in wall time.

    A thread that started and ended between two snapshots is in neither list, but its ready time
    is in the process's count. A blocking read in the crowded plant is one: its writer thread
    starts with the read and ends before it, and it waited for a CPU inside a health check's
    answer, which left that wait out as the loop's (0.068 s counted of 0.363 s, beside a block of
    0.2 s, on a CI runner). So a snapshot also takes the CPU time of the threads that have ended,
    which the kernel keeps, and which any thread that ran and ended adds to. When it grows between
    two snapshots, some thread came and went, and nothing is left out: the window is counted in
    wall time. A thread that is ending is in neither either: listed, it can no longer be read,
    and its CPU reaches that count only once the kernel is done with it (up to 2.5 ms after its
    join, on this machine, under load). So a snapshot that lists a thread it cannot read, or
    whose kernel thread count is above the threads it read, saw a thread ending (or one starting
    after the list, which leaves out less too), and a window either end of which did is counted
    in wall time too.

    Where the kernel offers neither count, everything reads 0 and the probe measures wall time."""

    def __init__(self) -> None:
        self.source = "none"
        self._fd: int | None = None
        self._status: int | None = None
        if sys.platform == "darwin":
            self._libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            self._libsystem.proc_pidinfo.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint64,
                ctypes.c_void_p,
                ctypes.c_int,
            ]
            timebase = _MachTimebase()
            self._libsystem.mach_timebase_info(ctypes.byref(timebase))
            self._seconds = timebase.numer / timebase.denom / 1e9
            self._ids = (ctypes.c_uint64 * 1024)()
            self.source = "macOS: the process's, less what its other threads could account for"
        elif os.path.exists("/proc/thread-self/schedstat"):
            self.source = "Linux: each thread's own"

    def open(self) -> None:
        """On the loop thread: ``/proc/thread-self`` names the thread that opens it."""
        if self.source.startswith("Linux"):
            self._fd = os.open("/proc/thread-self/schedstat", os.O_RDONLY)
            try:
                self._status = os.open("/proc/thread-self/status", os.O_RDONLY)
            except BaseException:
                os.close(self._fd)
                self._fd = None
                raise

    def close(self) -> None:
        for fd in (self._fd, self._status):
            if fd is not None:
                os.close(fd)
        self._fd = self._status = None

    def loop_slept(self) -> int | None:
        """Linux: how many times the loop thread has given up its CPU of its own accord
        (``voluntary_ctxt_switches``), to sleep or to wait on a read, a write, a lock or the GIL,
        from the file it holds open. Elsewhere None."""
        if self._status is None:
            return None
        for line in os.pread(self._status, 4096, 0).splitlines():
            if line.startswith(b"voluntary_ctxt_switches:"):
                return int(line.split()[1])
        return None

    def loop_waited(self) -> float | None:
        """Linux: the loop thread's own count, from the file it holds open. Elsewhere None."""
        if self._fd is None:
            return None
        return int(os.pread(self._fd, 128, 0).split()[1]) / 1e9

    def snapshot(self) -> SimpleNamespace:
        """Every thread's count (Linux), or every thread's CPU time and run state with the
        process's count (macOS), and the time it was taken: before the reads, for the first of
        two snapshots, so that what lies between two of them is never shorter than it was. And
        ``ready_at``, read just before the call that reads the process's count, with no other call
        to the kernel between: a window timed from one ``ready_at`` to another holds each wait for
        a CPU the count does (:class:`_Window`)."""
        at = time.monotonic()
        if self.source.startswith("Linux"):
            counts = {}
            for thread in os.listdir("/proc/self/task"):
                try:
                    with open(f"/proc/self/task/{thread}/schedstat", "rb") as schedstat:
                        counts[int(thread)] = int(schedstat.read().split()[1]) / 1e9
                except (FileNotFoundError, ProcessLookupError):
                    pass  # a thread that ended meanwhile
            done = time.monotonic()
            return SimpleNamespace(
                at=at, ready_at=done, ready=0.0, threads=counts, ended=0, ending=False, done=done
            )
        if self.source == "none":
            return SimpleNamespace(
                at=at, ready_at=at, ready=0.0, threads={}, ended=0, ending=False, done=at
            )
        pid, info, counts, threads = os.getpid(), _ProcThreadInfo(), _ProcThreadCounts(), {}
        listed = self._libsystem.proc_pidinfo(pid, 28, 0, self._ids, ctypes.sizeof(self._ids))
        ids = self._ids[: max(0, listed) // 8]
        for thread in ids:  # PROC_PIDLISTTHREADIDS
            size = ctypes.sizeof(info)  # PROC_PIDTHREADID64INFO
            if self._libsystem.proc_pidinfo(pid, 15, thread, ctypes.byref(info), size) == size:
                used = (info.pth_user_time + info.pth_system_time) / 1e9
                wrote = self._libsystem.proc_pidinfo(  # PROC_PIDTHREADCOUNTS
                    pid, 34, thread, ctypes.byref(counts), ctypes.sizeof(counts)
                )
                levels = (wrote - 8) // ctypes.sizeof(_ThreadCountsAtALevel) if wrote > 8 else 0
                ran = (
                    sum(counts.ptc_counts[level].instructions for level in range(levels))
                    if levels
                    else None
                )
                threads[thread] = (used, info.pth_run_state, ran)
        usage, task = _RusageInfoV4(), _ProcTaskInfo()
        ready_at = time.monotonic()  # the commpage's clock: no call to the kernel
        if self._libsystem.proc_pid_rusage(pid, 4, ctypes.byref(usage)) != 0:
            raise OSError(ctypes.get_errno(), "proc_pid_rusage")
        size = ctypes.sizeof(task)  # PROC_PIDTASKINFO
        if self._libsystem.proc_pidinfo(pid, 4, 0, ctypes.byref(task), size) != size:
            raise OSError(ctypes.get_errno(), "proc_pidinfo PROC_PIDTASKINFO")
        ready = usage.ri_runnable_time - usage.ri_user_time - usage.ri_system_time
        ended = task.pti_total_user + task.pti_total_system
        ended -= task.pti_threads_user + task.pti_threads_system
        # A thread listed but not read, or one the kernel counts that was not listed, was ending
        # or starting.
        ending = len(threads) < len(ids) or task.pti_threadnum > len(threads)
        return SimpleNamespace(
            at=at,
            ready_at=ready_at,
            ready=ready * self._seconds,
            threads=threads,
            ended=ended,
            ending=ending,
            done=time.monotonic(),
        )

    def waited(self, first: SimpleNamespace, then: SimpleNamespace, threads: set[int]) -> float:
        """What ``threads`` spent ready with no CPU between two snapshots, never more."""
        if self.source.startswith("Linux"):
            return sum(
                max(0.0, then.threads[t] - first.threads.get(t, 0.0))
                for t in threads
                if t in then.threads
            )
        if then.ended != first.ended or first.ending or then.ending:
            return 0.0  # a thread came and went, or was ending or starting, unread at either end
        others = sum(row[-1] for row in self.accounts(first, then, threads))
        return max(0.0, then.ready - first.ready - others)

    def accounts(
        self, first: SimpleNamespace, then: SimpleNamespace, threads: set[int]
    ) -> list[tuple[int, float, int | None, int | None, float]]:
        """macOS: each thread not in ``threads`` between two snapshots, as ``(id, CPU it used,
        its run state at the first and at the second, the instructions it ran or None where they
        could not be read, the ready time it could account for)``. A thread listed at one end only
        used no CPU the probe can read. Elsewhere, nothing."""
        if not self.source.startswith("macOS"):
            return []
        between = then.done - first.at
        rows = []
        for thread in sorted(first.threads.keys() | then.threads.keys()):
            if thread in threads:
                continue
            was, now = first.threads.get(thread), then.threads.get(thread)
            used = now[0] - was[0] if was and now else 0.0
            ran = now[2] - was[2] if was and now and None not in (was[2], now[2]) else None
            if ran == 0 and {was[1], now[1]} <= _NOT_READY:
                credited = 0.0  # neither running nor ready at either end, and it never ran between
            else:
                credited = max(0.0, between - used)
            rows.append((thread, used, was and was[1], now and now[1], ran, credited))
        return rows


class _ThreadCounts:
    """Linux: the loop thread's major page faults and its voluntary and involuntary context
    switches, read from the files ``/proc/thread-self`` names for the thread that opens them. A
    step that waited off its CPU and not ready to run shows how: a major fault waited on the disk
    for a page, a voluntary switch slept (on a lock, the GIL, a read), an involuntary one was
    taken off its CPU. Elsewhere it reads nothing."""

    def __init__(self) -> None:
        self._stat: int | None = None
        self._status: int | None = None

    def open(self) -> None:
        """On the loop thread."""
        if os.path.exists("/proc/thread-self/stat"):
            self._stat = os.open("/proc/thread-self/stat", os.O_RDONLY)
            self._status = os.open("/proc/thread-self/status", os.O_RDONLY)

    def close(self) -> None:
        for fd in (self._stat, self._status):
            if fd is not None:
                os.close(fd)
        self._stat = self._status = None

    def read(self) -> tuple[bytes, bytes] | None:
        """The two files as they read now, unparsed, so a step pays only the reads; None off
        Linux. :meth:`counts` reads them."""
        if self._stat is None or self._status is None:
            return None
        return os.pread(self._stat, 1024, 0), os.pread(self._status, 4096, 0)

    @staticmethod
    def counts(read: tuple[bytes, bytes]) -> tuple[int, int, int]:
        """``(major faults, voluntary switches, involuntary switches)`` from one :meth:`read`."""
        stat, status = read
        # The fields after the name, which may hold spaces, in its parentheses: majflt is the
        # tenth of them (field 12 of proc(5)).
        majflt = int(stat[stat.rindex(b")") + 2 :].split()[9])
        switches = {
            key: int(value)
            for key, _, value in (line.partition(":\t") for line in status.decode().splitlines())
            if key in ("voluntary_ctxt_switches", "nonvoluntary_ctxt_switches")
        }
        return majflt, switches["voluntary_ctxt_switches"], switches["nonvoluntary_ctxt_switches"]


def _what_runs(handle) -> tuple:
    """What a step of *handle* runs, read before it runs: the task it wakes and the frames the
    task waits in, outermost first, as ``(task, [(code, line), ...])``; for a handle that wakes
    no task, ``(handle, [])``. Kept raw, so the loop pays only the walk; :func:`_said` writes it."""
    task = getattr(getattr(handle, "_callback", None), "__self__", None)
    if not isinstance(task, asyncio.Task):
        return handle, []
    frames, awaited = [], task.get_coro()
    while awaited is not None and len(frames) < 16:
        frame = (
            getattr(awaited, "cr_frame", None)
            or getattr(awaited, "ag_frame", None)
            or getattr(awaited, "gi_frame", None)
        )
        if frame is None:
            break
        frames.append((frame.f_code, frame.f_lineno))
        awaited = (
            getattr(awaited, "cr_await", None)
            or getattr(awaited, "ag_await", None)
            or getattr(awaited, "gi_yieldfrom", None)
        )
    return task, frames


def _said(runs: tuple) -> str:
    """:func:`_what_runs`'s reading as a line: the task, then where it waited, its three
    innermost frames, each a function with its file and line."""
    what, frames = runs
    if not isinstance(what, asyncio.Task):
        return repr(what)[:200]
    where = " > ".join(
        f"{code.co_name} ({os.path.basename(code.co_filename)}:{line})"
        for code, line in frames[-3:]
    )
    return f"task {what.get_name()}, resumed in {where or 'its first step'}"


#: Whether a Linux step in which the loop thread gave up its CPU of its own accord (a sleep, a
#: wait on a read, a write, a lock or the GIL) is counted in wall time, no part of its count's time
#: ready left out, and an answer leaves out none of what such steps inside it held. The probe reads
#: both ways and reports the other beside what it judges (:meth:`_Probe.says`).
#:
#: It is. A step that slept waited on something for a time the probe cannot read: the kernel's
#: count of a thread's sleep is off on the runners (``kernel.sched_schedstats``), and what the step
#: waited on is the code under test. So the most of the step's count that is the runner's is the
#: step less that wait, which may be all of it, and none of the count is left out. On a CI runner
#: the count said 0.192 s ready in a 0.284 s step holding a 0.2 s wait to read, more than the step
#: less the wait could hold, and the step was counted 0.091 s, the block missed (run 40760127591).
#: A step the loop thread never gave up its CPU in is left its count as before: there the count
#: can only be the runner's.
_SLEPT_STEPS_EXCUSE_NOTHING = True


class SleptSteps(UserWarning):
    """Linux: a reading that held steps the loop thread slept in, whose count gave them time ready,
    and what it reads both ways (:data:`_SLEPT_STEPS_EXCUSE_NOTHING`), in the run's warnings
    summary, so a green run says what the rule costs it."""


#: How far a window's count of the loop thread's time ready may run past the window less its CPU
#: time before the count is taken to be wrong: the CPU clock's own calls, and the probe's reads
#: around the count, lie inside the CPU time and outside the count.
_COUNT_SLACK = 0.001

#: How much further macOS's count of the process's time ready may run past the window when the
#: loop thread is the process's only thread, so the count is its own. The kernel brings the count
#: up to date on the scheduler's turns, not when it is read, so each read can lag by up to one
#: 10 ms timeshare quantum. Measured on a Mac, one thread spinning in 0.15 s steps under a crowd:
#: each step's count ran from 8.7 ms short of the window less its CPU to 6.3 ms past it outside
#: the background band, and within 0.4 ms inside it. On a CI runner the starved-step plant's step
#: ran 3 ms past, and was counted 0.208 s in wall time for it (run 40855070354). With threads
#: beside, the loop's share is the count less what they could account for, so a share past the
#: window says those credits were wrong, and only :data:`_COUNT_SLACK` is allowed: an answer whose
#: share ran 10.1 ms past hid a block of 0.2 s (run 40518880630).
_A_LONE_THREADS_COUNT_LAG = 0.010


def _believed(wall: float, cpu: float, waited: float, slack: float = _COUNT_SLACK) -> float:
    """What a window of *wall* seconds, in which the loop thread used *cpu*, may leave out of the
    *waited* its count says the loop spent ready with no CPU: all of it, or nothing. The loop thread
    cannot have been ready for longer than the window less the time it ran, so a count that says it
    was is wrong, and the window is counted in wall time. On a CI runner, a macOS virtual machine,
    the process's count over a health check's answer held more than its three threads could have
    been ready for, one of them blocked 0.2 s in it: given the surplus, the loop's share came to the
    whole answer, which was counted 0.008 s of its 1.967 s, and the block was lost (run
    40518880630). A count that says less than that, however wrong, is not caught here. *slack* is
    how far past the window a count may run and still be the window's
    (:data:`_A_LONE_THREADS_COUNT_LAG`)."""
    return waited if waited <= wall - cpu + slack else 0.0


def _held_to_its_steps(waited: float, steps_left_out: float, outside: float) -> float:
    """What an answer whose count says it was *waited* ready may leave out: no more than the
    steps inside it whose time ready was read left out (*steps_left_out*), and its time outside
    them (*outside*), so it is counted no less than those steps were. Its count and theirs read
    the same stretches of the loop thread's time, so where the answer's leaves out more, one of
    them is wrong, and the probe takes the one that leaves out less. The same on every platform.
    On a CI runner, a macOS virtual machine that reads no instruction counts, the answer around a
    planted 0.2 s wait to read was the step that held it and 0.105 s more, yet its count left the
    loop 0.372 s more ready than the step's did: the step was counted 0.334 s, the answer 0.068 s,
    and the answer missed the block (run 40633805032)."""
    return min(waited, steps_left_out + outside)


class _Window:
    """One stretch of the loop thread's time, a step or a health check's answer: its wall time, its
    CPU time, and the time the loop thread spent in it ready to run with no CPU (:class:`_NoCpu`).

    Each end of the wall time is read beside the read of the time ready, with no call to the kernel
    between them. A wait for a CPU that falls due lands where the thread comes back from the kernel,
    and the probe's own reads are such calls (the CPU clock, Linux's count, each of macOS's
    snapshot's): placed so, a wait at one of them is inside both the wall time and the time ready,
    or outside both. Breadth's Linux runner (run 40259756916, and 40181486016 before) had the CPU
    clock's call between the wall time's end and the count, and the count's own call between the
    count and the wall time's start: it left 0.225 s out of a step's 0.313 s that held a 0.2 s
    wait to read, which can hold none, and counted the step 0.088 s. Read the other way round, with
    the counts inside the wall time and those calls too, every wait at them was counted in the step,
    and a step the runner took was counted over the bound (0.324 s of 0.510 s, on a loaded Mac)."""

    def __init__(self, no_cpu: "_NoCpu", threads: set[int]) -> None:
        self.no_cpu, self.threads = no_cpu, threads
        # The CPU clock first: its call is outside the window.
        self.cpu = time.thread_time()
        # Linux: the loop thread's voluntary switches, read before the window starts and after it
        # ends, so a switch at either read is the window's, which only leaves out less
        # (:attr:`slept`).
        self._slept_from = no_cpu.loop_slept()
        # Then the wall time and, beside it, the time ready: Linux's count (a wait at its own call
        # is after both, so inside the window and in the count), or macOS's first snapshot, timed
        # from just before its call that reads the count.
        self.began = time.monotonic()
        self.own = no_cpu.loop_waited()
        self.first = self.then = None
        self.said = 0.0
        self.could = 0.0
        #: Linux: whether the loop thread gave up its CPU of its own accord in the window, and the
        #: time ready its count gave the window, before :func:`_believed` judged it.
        self.slept = False
        self.ready = 0.0
        #: Whether the window's time ready was read: macOS reads none in a window shorter than
        #: :meth:`end`'s *counted_from*, which is wall time.
        self.read = False
        if self.own is None:
            self.first = no_cpu.snapshot()
            self.began = self.first.ready_at

    def end(self, *, counted_from: float = 0.0) -> tuple[float, float, float]:
        """The window's wall time, its CPU time and the time ready left out of it. On macOS that
        is read only for a window of *counted_from* or longer (a shorter one is wall time)."""
        waited, slack = 0.0, _COUNT_SLACK
        if self.own is not None:
            # The wall time ends, then Linux's count is read beside it: a wait at the count's own
            # call is after both, so outside the window and outside the count.
            ended = time.monotonic()
            now = self.no_cpu.loop_waited()
            # The step that ends :meth:`_Probe.during` closes the file before it ends: it is
            # counted in wall time.
            waited = max(0.0, now - self.own) if now is not None else 0.0
            self.read = now is not None
            slept_to = self.no_cpu.loop_slept()
            self.slept = self._slept_from is not None and slept_to is not None
            self.slept = self.slept and slept_to > self._slept_from
            self.ready = waited
        elif time.monotonic() - self.began >= counted_from:
            # macOS's second snapshot: the window ends just before its call that reads the count.
            self.then = then = self.no_cpu.snapshot()
            ended = then.ready_at
            waited = self.no_cpu.waited(self.first, then, self.threads)
            self.read = True
            # macOS: the process's count is the loop thread's own when it is the only thread. A
            # Linux count is each thread's own, brought up to date as it is read: no lag.
            listed = getattr(self.first, "threads", {}).keys() | getattr(then, "threads", {}).keys()
            lone = listed and listed <= self.threads
            if lone and self.no_cpu.source.startswith("macOS"):
                slack += _A_LONE_THREADS_COUNT_LAG
        else:
            ended = time.monotonic()
        # The CPU clock last: its call is outside the window.
        wall, cpu = ended - self.began, time.thread_time() - self.cpu
        #: What the count said, before :func:`_believed` judged it.
        self.said = waited
        waited = _believed(wall, cpu, waited, slack)
        #: What the window would count were all of the process's time ready the loop's, as much
        #: as the window less the loop's CPU can hold: the least it can be, and what a runner that
        #: reads no instruction counts cannot rule out (:meth:`_Probe.undecided`).
        ready = 0.0
        if self.first is not None and self.then is not None:
            ready = min(max(0.0, self.then.ready - self.first.ready), max(0.0, wall - cpu))
        self.could = max(wall - max(waited, ready), cpu)
        return wall, cpu, waited


class _Probe:
    """Beside some work: a health check asked again 10 ms after the last one answered, and the
    time each step of the event loop takes.

    A step is one callback the loop runs (``Handle._run``: a task resumed up to its next
    ``await``, a timer, an I/O callback), timed as asyncio's debug mode times it for its
    ``slow_callback_duration`` warning. While a step runs the loop runs nothing else, so the
    longest step (:attr:`worst_step`) is the longest the loop was blocked: a blocking call on it,
    a wait on the disk included, is inside a step however little CPU it uses. Time the loop spends
    waiting in its selector for something to do is between steps, so a process given no CPU while
    it waits there is not counted as blocked.

    Inside a step of :data:`_COUNTED` or longer, the time the loop thread spent ready to run with
    no CPU (:class:`_NoCpu`) is left out: a loaded runner stretches a step without the loop
    waiting on anything, and a step's own waits are never in that count. A step is never counted
    as less than the CPU time the loop thread used in it. A health check's answer leaves out the
    same: the time the loop thread spent ready with no CPU, never another thread's, and it too is
    never counted as less than the loop thread's CPU time over it, nor than the steps inside it
    were (:func:`_held_to_its_steps`). The wall
    times are reported beside (:attr:`worst_step_wall`, :attr:`slowest_answer_wall`).

    A 10 ms tick is kept beside it, and its longest gap (:attr:`worst_gap`) is reported: that gap
    is wall time between two turns, the selector's wait included, which is why it is not the
    measure."""

    def __init__(
        self, client: TestClient, *, asking: bool = True, no_cpu: _NoCpu | None = None
    ) -> None:
        self.client = client
        self.asking = asking
        self.no_cpu = no_cpu or _NoCpu()
        self.counts = _ThreadCounts()
        self.worst_step = 0.0
        self.worst_step_was = ""
        #: The longest step's own wall time, CPU time and time ready left out, and what the loop
        #: thread's counts did over it (:class:`_ThreadCounts`; None off Linux).
        self.worst_step_own: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.worst_step_counts: tuple[int, int, int] | None = None
        #: macOS: the longest step's two snapshots' process count of time ready, and each other
        #: thread's CPU, run states and credit over it (:meth:`_NoCpu.accounts`); None elsewhere.
        self.worst_step_threads: tuple[float, list, dict[int, str]] | None = None
        #: What the longest step's count said, before :func:`_believed` judged it.
        self.worst_step_said = 0.0
        self.worst_step_wall = 0.0
        self.worst_gap = 0.0
        self.slowest_answer = 0.0
        self.slowest_answer_wall = 0.0
        #: The answer longest in wall time: its wall time, CPU time, time ready left out and what
        #: its count said, and the threads over it as for the longest step.
        self.longest_answer_own: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self.longest_answer_threads: tuple[float, list, dict[int, str]] | None = None
        self.waited_for_a_cpu = 0.0
        self.answered_during = 0
        #: The longest step and the slowest answer, were all of the process's time ready over
        #: each the loop's (:attr:`_Window.could`).
        self.worst_step_could = 0.0
        self.slowest_answer_could = 0.0
        #: The reading this one was measured again after (:func:`_judged`), if it was.
        self.before: _Probe | None = None
        #: Linux: the steps the loop thread slept in whose count gave them time ready, and that
        #: time in all (:attr:`_Window.slept`); and the longest step and the slowest answer read
        #: leaving none of it out, beside as they read leaving all of it out
        #: (:data:`_SLEPT_STEPS_EXCUSE_NOTHING` says which is judged).
        self.slept_steps = 0
        self.slept_ready = 0.0
        self.worst_step_ruled = self.worst_step_unruled = 0.0
        self.slowest_answer_ruled = self.slowest_answer_unruled = 0.0
        #: The wall time of the steps whose time ready was read (:attr:`_Window.read`), and the
        #: time ready they left out read both ways, in all: what holds an answer to its steps
        #: (:func:`_held_to_its_steps`).
        self.stepped = 0.0
        self.left_out_ruled = self.left_out_unruled = 0.0
        #: macOS: the snapshots taken, and whether any read a thread's instruction count
        #: (:attr:`reads_no_instructions`).
        self.snapshots = 0
        self.instructions_read = False

    @property
    def reads_no_instructions(self) -> bool:
        """macOS, with no thread's instruction count read in any snapshot taken: every other thread
        is then credited its whole window (:meth:`_NoCpu.accounts`), so nothing is left out, and
        the probe measures wall time. A macOS virtual machine is such a runner (runs 40518880630
        and 40578869717)."""
        return (
            self.no_cpu.source.startswith("macOS")
            and self.snapshots > 0
            and not self.instructions_read
        )

    def _note(self, window: _Window) -> None:
        """Count *window*'s snapshots toward :attr:`reads_no_instructions`."""
        for snapshot in (window.first, window.then):
            if snapshot is not None:
                self.snapshots += 1
                self.instructions_read |= any(
                    t[2] is not None for t in getattr(snapshot, "threads", {}).values()
                )

    def _timed(self, loop_thread: int, real):
        probe = self

        def run(handle):
            if threading.get_ident() != loop_thread:
                return real(handle)
            try:
                # What the step runs, and the thread's counts, read outside its window.
                runs, counts = _what_runs(handle), probe.counts.read()
                # The step's window: its reads of the time ready inside its wall time (_Window).
                window = _Window(probe.no_cpu, {threading.get_native_id()})
            except BaseException:
                # An exception raised in the probe's own work, as a timeout's alarm is wherever
                # it lands, must not drop the loop's callback: the loop has already taken it off
                # its queue. A task whose wakeup is dropped is never woken again, not even to be
                # cancelled, and the loop's close then waits for it forever (a test teardown hung
                # for two hours, on a task waiting on a future already done).
                real(handle)
                raise
            try:
                return real(handle)
            finally:
                wall, cpu, unruled = window.end(counted_from=_COUNTED)
                ruled = 0.0 if window.slept else unruled
                if window.slept and window.ready > 0:
                    probe.slept_steps += 1
                    probe.slept_ready += window.ready
                probe.worst_step_ruled = max(probe.worst_step_ruled, wall - ruled, cpu)
                probe.worst_step_unruled = max(probe.worst_step_unruled, wall - unruled, cpu)
                if window.read:
                    probe.stepped += wall
                    probe.left_out_ruled += wall - max(wall - ruled, cpu)
                    probe.left_out_unruled += wall - max(wall - unruled, cpu)
                waited = ruled if _SLEPT_STEPS_EXCUSE_NOTHING else unruled
                took = max(wall - waited, cpu)
                probe.worst_step_wall = max(probe.worst_step_wall, wall)
                probe.worst_step_could = max(probe.worst_step_could, window.could)
                probe._note(window)
                probe.waited_for_a_cpu += waited
                if took > probe.worst_step:
                    after = probe.counts.read()
                    probe.worst_step = took
                    probe.worst_step_was = _said(runs)
                    probe.worst_step_own = (wall, cpu, waited)
                    probe.worst_step_counts = (
                        tuple(  # type: ignore[assignment]
                            b - a
                            for a, b in zip(
                                _ThreadCounts.counts(counts), _ThreadCounts.counts(after)
                            )
                        )
                        if counts is not None and after is not None
                        else None
                    )
                    probe.worst_step_said = window.said
                    probe.worst_step_threads = probe._threads_over(window)

        return run

    async def during(self, work):
        if self.asking:
            # The first health check reads the home and the serving root, and waits on the disk
            # for them, by design (handlers_system._Identity): asked before the work, so the
            # answers timed are the gateway's every-time answers.
            first = await self.client.get("/api/healthz")
            await first.read()
        self.no_cpu.open()
        self.counts.open()
        try:
            with patch.object(
                asyncio.events.Handle,
                "_run",
                self._timed(threading.get_ident(), asyncio.events.Handle._run),
            ):
                return await self._during(work)
        finally:
            self.counts.close()
            self.no_cpu.close()
            if self.slept_steps:
                warnings.warn(
                    SleptSteps(
                        f"{self.slept_steps} step(s) the loop thread slept in held"
                        f" {self.slept_ready:.3f}s ready; leaving none of it out, the longest step"
                        f" reads {self.worst_step_ruled:.3f}s and the slowest answer"
                        f" {self.slowest_answer_ruled:.3f}s (bound {_BOUND}s), against"
                        f" {self.worst_step_unruled:.3f}s and {self.slowest_answer_unruled:.3f}s"
                    ),
                    stacklevel=2,
                )

    def _threads_over(self, window: _Window) -> tuple[float, list, dict[int, str]] | None:
        """macOS: the process's count of time ready over *window*, each other thread's CPU, run
        states and credit over it (:meth:`_NoCpu.accounts`), and the threads' names; None
        elsewhere."""
        if window.first is None or window.then is None:
            return None
        return (
            window.then.ready - window.first.ready,
            self.no_cpu.accounts(window.first, window.then, window.threads),
            {t.native_id: t.name for t in threading.enumerate() if t.native_id},
        )

    async def _timed_answer(self, answer) -> None:
        """One health check's answer, *answer* awaited, counted as :meth:`during` counts each: in
        a window whose reads of the time ready lie inside its wall time (:class:`_Window`), less
        the loop thread's own time ready, never less than its CPU time, nor than the steps inside
        it were counted (:func:`_held_to_its_steps`)."""
        window = _Window(self.no_cpu, {threading.get_native_id()})
        slept_before = self.slept_ready
        stepped, ruled_before, unruled_before = (
            self.stepped,
            self.left_out_ruled,
            self.left_out_unruled,
        )
        await answer()
        wall, cpu, unruled = window.end()
        # The steps inside it that slept had their time ready counted in this window's too.
        ruled = max(0.0, unruled - (self.slept_ready - slept_before))
        outside = max(0.0, wall - (self.stepped - stepped))
        ruled = _held_to_its_steps(ruled, self.left_out_ruled - ruled_before, outside)
        unruled = _held_to_its_steps(unruled, self.left_out_unruled - unruled_before, outside)
        self.slowest_answer_ruled = max(self.slowest_answer_ruled, wall - ruled, cpu)
        self.slowest_answer_unruled = max(self.slowest_answer_unruled, wall - unruled, cpu)
        waited = ruled if _SLEPT_STEPS_EXCUSE_NOTHING else unruled
        self.slowest_answer = max(self.slowest_answer, wall - waited, cpu)
        self.slowest_answer_could = max(self.slowest_answer_could, window.could)
        self._note(window)
        if wall > self.slowest_answer_wall:
            self.slowest_answer_wall = wall
            self.longest_answer_own = (wall, cpu, waited, window.said)
            self.longest_answer_threads = self._threads_over(window)

    async def _during(self, work):
        stop = asyncio.Event()
        started = finished = 0.0

        async def tick() -> None:
            last = time.monotonic()
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = time.monotonic()
                self.worst_gap = max(self.worst_gap, now - last)
                last = now

        async def healthz() -> None:
            r = await self.client.get("/api/healthz")
            await r.read()
            assert r.status == 200

        async def ask() -> None:
            while not stop.is_set():
                await self._timed_answer(healthz)
                if started and not finished:
                    self.answered_during += 1
                await asyncio.sleep(0.01)

        # The tick measures the upload's work, not the collector's: a full collection of this
        # test process's heap is not something the upload does.
        gc.collect()
        gc.disable()
        tasks = [asyncio.create_task(tick())]
        if self.asking:
            tasks.append(asyncio.create_task(ask()))
        try:
            await asyncio.sleep(0.05)  # both run before the work starts
            started = time.monotonic()
            result = await work
            finished = time.monotonic()
        finally:
            stop.set()
            await asyncio.gather(*tasks)
            gc.enable()
        self.took = finished - started
        return result

    def undecided(self, *, answers: bool = True) -> bool:
        """Whether this reading is over the bound only by time the probe cannot place: on a runner
        that reads no instruction counts (:attr:`reads_no_instructions`), every step, and
        with *answers* every answer, is under the bound once the process's own time ready over it
        is taken for the loop's. A reading over the bound even then is decided: it fails. One under
        the bound is decided too."""
        over = self.worst_step >= _BOUND or (answers and self.slowest_answer >= _BOUND)
        placed = self.worst_step_could >= _BOUND or (
            answers and self.slowest_answer_could >= _BOUND
        )
        return over and not placed and self.reads_no_instructions

    def says(self) -> str:
        """The probe's reading, one line a field: what the longest step ran and how its time
        went, beside the health checks, the time left out and the tick."""
        wall, cpu, waited = self.worst_step_own
        lines = [
            f"the work took {self.took:.2f}s",
            f"the longest step: {self.worst_step:.3f}s counted, of {wall:.3f}s in wall time,"
            f" {cpu:.3f}s of CPU and {waited:.3f}s ready with no CPU",
            f"  it ran {self.worst_step_was}",
        ]
        if self.worst_step_counts is not None:
            major, voluntary, involuntary = self.worst_step_counts
            lines.append(
                f"  the loop thread over it: {major} major faults, {voluntary} voluntary and"
                f" {involuntary} involuntary switches"
            )
        lines += _set_aside(waited, self.worst_step_said)
        lines += _over(self.worst_step_threads)
        answer_wall, answer_cpu, answer_waited, answer_said = self.longest_answer_own
        lines += [
            f"the slowest health check: {self.slowest_answer:.3f}s counted, of"
            f" {self.slowest_answer_wall:.3f}s in wall time ({self.answered_during} answered"
            " during the work)",
            f"  the longest in wall time: {answer_wall:.3f}s, {answer_cpu:.3f}s of CPU and"
            f" {answer_waited:.3f}s ready with no CPU left out",
            *_set_aside(answer_waited, answer_said),
            *_over(self.longest_answer_threads),
            f"ready with no CPU, left out of the steps: {self.waited_for_a_cpu:.3f}s"
            f" ({self.no_cpu.source})",
            f"the longest step in wall time: {self.worst_step_wall:.3f}s",
            f"the longest gap between turns: {self.worst_gap:.3f}s",
        ]
        if self.slept_steps:
            rule = "judged" if _SLEPT_STEPS_EXCUSE_NOTHING else "not judged"
            lines.append(
                f"steps the loop thread slept in, whose count gave them time ready:"
                f" {self.slept_steps}, {self.slept_ready:.3f}s in all; read leaving none of it out"
                f" ({rule}), the longest step {self.worst_step_ruled:.3f}s and the slowest answer"
                f" {self.slowest_answer_ruled:.3f}s, against {self.worst_step_unruled:.3f}s and"
                f" {self.slowest_answer_unruled:.3f}s leaving it out"
            )
        if self.reads_no_instructions:
            lines.append(
                "this runner reads no instruction counts: nothing another thread could account for"
                " is left out, so the probe measures wall time here. A reading over the bound only"
                " by the process's time ready is measured again on a fresh gateway, and the second"
                " judged; a block that comes only sometimes can pass that second measurement on"
                " macOS, and Linux, which counts each thread's own time ready, catches it"
            )
        if self.before is not None:
            lines.append(
                "measured again: the first reading was over the bound only by time ready this"
                " runner cannot place, and read:"
            )
            lines += [f"  {line}" for line in self.before.says().splitlines()]
        return "\n".join(lines)


def _set_aside(waited: float, said: float) -> list[str]:
    """The line that says a window's count was set aside (:func:`_believed`), if it was."""
    if said == waited:
        return []
    return [f"  its count said {said:.3f}s ready, more than it could hold: nothing left out"]


def _over(threads: tuple[float, list, dict[int, str]] | None) -> list[str]:
    """The lines for the threads over a window (:meth:`_Probe._threads_over`); none off macOS."""
    if threads is None:
        return []
    ready, rows, names = threads
    return [f"  the process over it: {ready:.3f}s ready with no CPU"] + [
        f"  {names.get(thread, f'thread {thread}')}: {used:.6f}s of CPU,"
        f" {'unread' if ran is None else ran} instructions,"
        f" {_STATES.get(was, 'unlisted')} then {_STATES.get(now, 'unlisted')},"
        f" credited {credited:.3f}s"
        for thread, used, was, now, ran, credited in rows
    ]


async def _init(client, size: int, *, target: str, filename: str, mime: str) -> dict:
    r = await client.post(
        "/api/uploads/init",
        json={"filename": filename, "size": size, "mime": mime, "target": target},
    )
    assert r.status == 200, await r.text()
    return await r.json()


async def _send_parts(client, info: dict, payload: bytes) -> None:
    part = info["partSize"]
    for i in range(info["totalParts"]):
        r = await client.put(
            f"/api/uploads/{info['uploadId']}/part?index={i}",
            data=payload[i * part : (i + 1) * part],
            headers={"Content-Type": "application/octet-stream"},
        )
        assert r.status == 200, await r.text()


async def _complete(client, uid: str) -> tuple[int, dict]:
    r = await client.post(f"/api/uploads/{uid}/complete", json={})
    return r.status, await r.json()


def _items(store: KnowledgeStore) -> int:
    return store.db.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"]


async def _judged(measure, gateway, again, *, answers: bool = True) -> _Probe:
    """The reading to judge: *measure* run over *gateway*, and, when that reading is over the bound
    only by time the probe cannot place (:meth:`_Probe.undecided`), run once more over a fresh one
    from *again*, whose reading is judged as it is, carrying the first in its report.

    A runner that reads no instruction counts, a macOS virtual machine, leaves nothing out of a
    window, so a stall of the whole process there reads as a block: run 40578869717 counted a step
    of the probe's own health check 0.097 s with 0.001 s of CPU, the process 0.096 s ready over it
    and every other thread idle, and its answer 0.144 s. A block the upload makes, it makes every
    time, so it fails the second measurement too; the runner's stall does not come back. A block
    that comes only sometimes, or once a process (a lazy import on the loop), can pass the second
    on macOS: Linux, which counts each thread's own time ready and leaves out exactly the loop's,
    catches it. *answers* says whether the health
    checks' answers are judged, as well as the steps."""
    probe = await measure(gateway)
    if probe.undecided(answers=answers):
        first, probe = probe, await measure(again())
        probe.before = first
    return probe


@pytest.mark.asyncio
async def test_completing_a_large_upload_leaves_the_gateway_answering(gateway, again):
    """🔴 Red before: the complete of a 256 MB video copied its 32 parts into one file and hashed
    it on the event loop, which went 0.19 s without a turn."""
    payload = _bytes(_LARGE)

    async def measure(gw) -> _Probe:
        async with TestClient(TestServer(gw.app)) as client:
            info = await _init(
                client,
                _LARGE,
                target="knowledge",
                filename="walkthrough.mov",
                mime="video/quicktime",
            )
            await _send_parts(client, info, payload)
            probe = _Probe(client)

            status, body = await probe.during(_complete(client, info["uploadId"]))

        assert status == 200, body
        assert gw.queued == [body["item_id"]], "premise: the upload became an item"
        item = gw.store.get_item(body["item_id"])
        assert item["file_size"] == _LARGE
        assert not (gw.parts / info["uploadId"]).exists(), "its parts were removed"
        assert (
            probe.answered_during >= 1
        ), f"premise: the gateway was asked meanwhile; {probe.says()}"
        return probe

    probe = await _judged(measure, gateway, again)
    assert probe.worst_step < _BOUND, probe.says()
    assert probe.slowest_answer < _BOUND, probe.says()


async def _multipart(filename: str, mime: str, payload: bytes, boundary: str):
    """A one-file form body, sent a megabyte at a time as a browser sends it."""
    yield (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode()
    view = memoryview(payload)
    for start in range(0, len(payload), _MB):
        yield bytes(view[start : start + _MB])
    yield f"\r\n--{boundary}--\r\n".encode()


@pytest.mark.asyncio
async def test_filing_a_large_single_request_upload_leaves_the_gateway_answering(gateway, again):
    """🔴 Red before: a knowledge upload sent in one request was moved and hashed on the event
    loop once its bytes had arrived: 0.18 s without a turn for this one."""
    payload = _bytes(_LARGER)
    boundary = "pcuploadboundary"

    async def measure(gw) -> _Probe:
        async with TestClient(TestServer(gw.app)) as client:
            probe = _Probe(client)

            async def send() -> tuple[int, dict]:
                r = await client.post(
                    "/api/knowledge/ingest",
                    data=_multipart("walkthrough.mov", "video/quicktime", payload, boundary),
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                )
                return r.status, await r.json()

            status, body = await probe.during(send())

        assert status == 200, body
        assert gw.queued == [body["item_id"]], "premise: the upload became an item"
        assert gw.store.get_item(body["item_id"])["file_size"] == _LARGER
        assert (
            probe.answered_during >= 1
        ), f"premise: the gateway was asked meanwhile; {probe.says()}"
        return probe

    probe = await _judged(measure, gateway, again)
    assert probe.worst_step < _BOUND, probe.says()
    assert probe.slowest_answer < _BOUND, probe.says()


#: How long the planted security-log write takes: as long as the log's key once took to make,
#: with its fsync, beside a large upload on a Linux runner (#5605).
_SLOW_SECURITY_LOG = 0.2


@pytest.mark.asyncio
async def test_a_slow_security_log_write_does_not_hold_the_gateway(gateway, again, monkeypatch):
    """🔴 Red before: the ingest wrote its security event on the event loop, so a write the disk
    took 0.2 s for held every other request as long. On a Linux runner beside a 384 MB upload the
    first write made the log's key with an fsync and took up to 0.343 s, failing the test above
    in 5 runs of 30 (#5605). The write runs in a worker thread now, awaited, so the gateway
    answers meanwhile and the event is on disk before the upload's answer."""
    real, wrote = SecurityEventLog.log, []

    def slow(self, event):
        time.sleep(_SLOW_SECURITY_LOG)
        wrote.append(event.operation)
        return real(self, event)

    monkeypatch.setattr(SecurityEventLog, "log", slow)
    boundary = "pcuploadboundary"

    async def measure(gw) -> _Probe:
        wrote.clear()
        async with TestClient(TestServer(gw.app)) as client:
            probe = _Probe(client)

            async def send() -> tuple[int, dict]:
                r = await client.post(
                    "/api/knowledge/ingest",
                    data=_multipart("walkthrough.mov", "video/quicktime", _bytes(_MB), boundary),
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                )
                return r.status, await r.json()

            status, body = await probe.during(send())

        assert status == 200, body
        assert "knowledge.ingest" in wrote, f"premise: the ingest wrote its event; {wrote}"
        assert (
            probe.answered_during >= 1
        ), f"premise: the gateway was asked meanwhile; {probe.says()}"
        return probe

    probe = await _judged(measure, gateway, again, answers=False)
    assert probe.worst_step < _BOUND, probe.says()


def _json_lines(size: int) -> bytes:
    """Ordinary JSON lines, the kind of export that is uploaded as a large text file. Each line
    also reads as Python, and is 64 bytes, so the scan's head and tail windows end on a line's
    end and its parse has the whole window to parse."""
    line = b'{"id": 12, "title": "release walkthrough", "tags": ["notes"]}'
    line = line.ljust(63) + b"\n"
    assert len(line) == 64
    return line * (size // len(line))


@pytest.mark.asyncio
async def test_scanning_a_large_text_upload_leaves_the_gateway_answering(gateway, again):
    """🔴 Red before: the content scan ran on the event loop, which went its whole run without
    a turn. In a worker thread its parse still held the loop for about 0.1 s; in a child
    process, not at all."""
    payload = _json_lines(2 * _MB)

    async def measure(gw) -> _Probe:
        async with TestClient(TestServer(gw.app)) as client:
            info = await _init(
                client,
                len(payload),
                target="attachment",
                filename="feed-export.jsonl",
                mime="application/x-ndjson",
            )
            await _send_parts(client, info, payload)
            probe = _Probe(client)

            status, body = await probe.during(_complete(client, info["uploadId"]))

        assert status == 200, body
        (path,) = body["paths"]
        assert os.path.getsize(path) == len(payload), "premise: the scanned upload was kept"
        assert (
            probe.answered_during >= 1
        ), f"premise: the gateway was asked meanwhile; {probe.says()}"
        return probe

    probe = await _judged(measure, gateway, again)
    assert probe.worst_step < _BOUND, probe.says()
    assert probe.slowest_answer < _BOUND, probe.says()


# ── what the upload becomes is decided off the loop ─────────────────────────────────────────

#: How long :class:`_HeldInsert` holds the insert when nothing lets it go.
_HOLD = 2.0


#: The store's calls that decide what an upload becomes, in the order they run.
_DECIDING_CALLS = ("find_active_by_file_hash", "create_typed_item", "get_item")


class _HeldInsert:
    """Records the thread each of :data:`_DECIDING_CALLS` runs on, and holds the insert of a new
    item, wherever it runs, until :meth:`release` or for :data:`_HOLD` seconds. Held on the event
    loop it stops every request for the whole hold; held in a worker thread, the loop answers
    meanwhile."""

    def __init__(self, store: KnowledgeStore) -> None:
        self.entered = threading.Event()
        self.released = threading.Event()
        self.threads: dict[str, list[int]] = {name: [] for name in _DECIDING_CALLS}
        self.timed_out = False
        self._patches = [
            patch.object(store, name, self._recorded(name, getattr(store, name)))
            for name in _DECIDING_CALLS
        ]

    def _recorded(self, name, real):
        def call(*args, **kwargs):
            self.threads[name].append(threading.get_ident())
            if name == "create_typed_item":
                self.entered.set()
                self.timed_out = not self.released.wait(_HOLD)
            return real(*args, **kwargs)

        return call

    def __enter__(self) -> "_HeldInsert":
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc) -> None:
        for p in reversed(self._patches):
            p.stop()

    def release(self) -> None:
        self.released.set()


@pytest.mark.asyncio
async def test_the_item_is_made_while_the_gateway_answers(gateway):
    """🔴 Red before: the duplicate check, the insert and the read of the new item ran on the
    event loop, so a commit or a read waiting on a busy disk held every request up (0.2 s and
    0.32 s measured). All three run in one worker thread, the same one; held open there, the
    insert lets a health check be answered before it is let go, and the complete still makes its
    one item."""
    loop_thread = threading.get_ident()
    payload = _bytes(4 * _MB)
    with _HeldInsert(gateway.store) as held:
        async with TestClient(TestServer(gateway.app)) as client:
            info = await _init(
                client, len(payload), target="knowledge", filename="clip.mp4", mime="video/mp4"
            )
            await _send_parts(client, info, payload)
            first = asyncio.ensure_future(_complete(client, info["uploadId"]))
            assert await asyncio.to_thread(held.entered.wait, 30), "the insert never began"
            health = await client.get("/api/healthz")
            answered_while_held = health.status == 200 and not held.released.is_set()
            held.release()
            status, body = await first

    ran_on = {name: threads for name, threads in held.threads.items()}
    assert all(
        len(threads) == 1 for threads in ran_on.values()
    ), f"premise: each ran once; {ran_on}"
    on_loop = [name for name, (thread,) in ran_on.items() if thread == loop_thread]
    assert not on_loop, f"what decides the item ran on the event loop's thread: {on_loop}"
    assert (
        len({thread for (thread,) in ran_on.values()}) == 1
    ), f"what decides the item did not run in one worker call: {ran_on}"
    assert not held.timed_out, "nothing was answered until the held insert gave up waiting"
    assert answered_while_held, "the gateway did not answer while the insert was held"
    assert status == 200, body
    assert _items(gateway.store) == 1 and gateway.queued == [body["item_id"]]


@pytest.mark.asyncio
async def test_two_uploads_of_the_same_bytes_finishing_together_make_one_item(gateway, tmp_path):
    """Off the loop, the duplicate check and the insert are still one step under the store's
    lock: two uploads of the same bytes stored at once make one item, and one is told so."""
    from personalclaw.knowledge.file_items import store_file_item

    payload = _bytes(_MB)
    first, second = tmp_path / "a.mov", tmp_path / "b.mov"
    first.write_bytes(payload)
    second.write_bytes(payload)

    made = await asyncio.gather(
        store_file_item(gateway.store, str(first), "a.mov", mime="video/quicktime"),
        store_file_item(gateway.store, str(second), "b.mov", mime="video/quicktime"),
    )

    assert sorted(is_new for _item, is_new in made) == [False, True]
    assert made[0][0]["id"] == made[1][0]["id"]
    assert _items(gateway.store) == 1


def test_an_item_decided_while_another_change_is_open_waits_for_it(gateway):
    """🔴 Red before: the store's changes held its connection's lock one statement at a time, so
    an upload decided in a worker thread while this thread's edit was open ran inside it: its
    insert failed with "cannot start a transaction within a transaction", the upload answered 500
    and its stored file was left without an item. A change now holds the lock from its start to
    its end, and the upload is decided after it."""
    from personalclaw.knowledge import file_items

    store = gateway.store
    edited = store.create_typed_item(item_type="note", title="draft", content="first")
    item = {"item_type": "note", "title": "clip", "content": "", "extra": {}}
    decided: dict = {}

    def decide() -> None:
        try:
            decided["made"] = file_items._decide(store, "", item)
        except Exception as exc:  # what the upload's request would have answered 500 with
            decided["error"] = exc

    worker = threading.Thread(target=decide)
    real_write_tags = store._write_item_tags
    opened = threading.Event()

    def write_tags_then_decide(*args, **kwargs):
        # Inside the edit's transaction, on this thread: the upload is decided meanwhile.
        if not opened.is_set():
            opened.set()
            worker.start()
            worker.join(1.0)
        return real_write_tags(*args, **kwargs)

    with patch.object(store, "_write_item_tags", write_tags_then_decide):
        store.update_item(edited, content="second", tags=["kept"])
    worker.join(30)

    assert opened.is_set(), "premise: the edit's transaction was open while the upload was decided"
    assert not worker.is_alive(), "the upload was never decided"
    assert "error" not in decided, f"the upload was decided inside the edit: {decided['error']!r}"
    made, is_new = decided["made"]
    assert is_new and made and made["title"] == "clip"
    assert store.get_item(edited)["content"] == "second", "the edit was not stored"
    assert _items(store) == 2


@pytest.mark.asyncio
async def test_two_stores_over_one_library_make_one_item_of_the_same_bytes(gateway, tmp_path):
    """🔴 Red before: the duplicate check and the insert were one step under ONE store's lock, and
    the process can hold two stores over one library (the vault's sweep opens its own). Two
    uploads of the same bytes decided on the two at once made two items. Deciding is one upload
    at a time in the process, so the second finds the first's item."""
    from personalclaw.knowledge.file_items import store_file_item

    other = KnowledgeStore(gateway.store.db_path)
    payload = _bytes(_MB)
    first, second = tmp_path / "a.mov", tmp_path / "b.mov"
    first.write_bytes(payload)
    second.write_bytes(payload)
    try:
        made = await asyncio.gather(
            store_file_item(gateway.store, str(first), "a.mov", mime="video/quicktime"),
            store_file_item(other, str(second), "b.mov", mime="video/quicktime"),
        )
    finally:
        other.close()

    assert sorted(is_new for _item, is_new in made) == [False, True]
    assert made[0][0]["id"] == made[1][0]["id"]
    assert _items(gateway.store) == 1


def test_the_module_that_makes_the_item_loads_with_the_upload_handlers():
    """🔴 Red before: a complete imported it on the first Knowledge upload, and a module's first
    import runs on the event loop (0.13-0.44 s measured on a busy machine). It is imported with
    the handlers, which the gateway loads when it registers the routes."""
    from personalclaw.dashboard.handlers import uploads
    from personalclaw.knowledge import file_items

    assert getattr(uploads, "store_file_item", None) is file_items.store_file_item


# ── what the probe counts as the loop being blocked ─────────────────────────────────────────

#: How long each planted block below holds the event loop, and how long the process is stopped.
_PLANTED = 0.2
_STOPPED = 0.25


def _block_by_sleeping(_tmp_path) -> None:
    time.sleep(_PLANTED)


def _block_by_writing(tmp_path) -> None:
    """A write that waits, as a write to a busy disk does, using no CPU meanwhile: 1 MB into a
    pipe whose reader starts draining it only after :data:`_PLANTED`."""
    fifo = tmp_path / "slow-disk"
    os.mkfifo(fifo)

    def drain() -> None:
        with open(fifo, "rb") as reader:
            time.sleep(_PLANTED)
            while reader.read(1 << 16):
                pass

    drainer = threading.Thread(target=drain)
    drainer.start()
    with open(fifo, "wb") as writer:
        writer.write(b"\0" * _MB)
    drainer.join()


def _block_by_reading(tmp_path, late: float = 0.0) -> float:
    """A read that waits, as a read from a busy disk does, using no CPU meanwhile: from a pipe
    whose writer writes only after :data:`_PLANTED`. *late* holds the reader back between opening
    its end and reading, as a thread kept that long from a CPU is. It returns how long the read
    itself waited."""
    fifo = tmp_path / "slow-read"
    os.mkfifo(fifo)

    def feed() -> None:
        with open(fifo, "wb") as writer:
            time.sleep(_PLANTED)
            writer.write(b"\0" * 4096)

    feeder = threading.Thread(target=feed)
    feeder.start()
    with open(fifo, "rb") as reader:
        if late:
            time.sleep(late)
        began = time.monotonic()
        assert len(reader.read(4096)) == 4096
        waited = time.monotonic() - began
    feeder.join()
    return waited


def _block_by_waiting_to_read(_tmp_path, late: float = 0.0) -> float:
    """A read that waits, as one from a peer that sends nothing does, using no CPU meanwhile: a
    wait on a socket for something to read, for :data:`_PLANTED`. The kernel times it from the
    call's own start (select's timeout is relative), so all of it is spent waiting, however long
    the thread waited for a CPU before the call, or *late* held it back. :func:`_block_by_reading`'s
    read is not: its writer's delay runs from when both ends of the pipe are open, so a thread
    kept from a CPU that long after opening its end finds the data already there, and never
    blocks. It returns how long the wait took."""
    ours, theirs = socket.socketpair()
    with ours, theirs:
        if late:
            time.sleep(late)
        began = time.monotonic()
        readable, _, _ = select.select([ours], [], [], _PLANTED)
        waited = time.monotonic() - began
    assert not readable, "the peer that sends nothing sent something"
    return waited


#: The block the crowded plant makes (:func:`_blocked_while_answering_app`).
_CROWDED_BLOCK = _block_by_waiting_to_read


_PLANTED_BLOCKS = {
    "a blocking sleep": _block_by_sleeping,
    "a blocking file write": _block_by_writing,
    "a blocking file read": _block_by_reading,
}


def _planted_app(block, tmp_path) -> web.Application:
    async def planted(_request: web.Request) -> web.Response:
        block(tmp_path)  # on the event loop, in the request
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/api/healthz", api_healthz)
    app.router.add_get("/planted", planted)
    return app


@pytest.mark.asyncio
async def test_a_blocking_call_on_the_loop_fails_the_probe(subtests, tmp_path):
    """The vacuity arm: a request that blocks the loop for 0.2 s, with a sleep, or with a write
    or a read that waits as a busy disk makes it wait, is a step over the bound. The thread is
    blocked, not ready to run, so none of it is the time with no CPU the step leaves out. The
    tick's gap, which the probe measured before, catches it too, so the step catches everything
    that measure did."""
    assert len(_PLANTED_BLOCKS) == 3
    for name, block in _PLANTED_BLOCKS.items():
        with row(subtests, block=name):
            where = tmp_path / name.replace(" ", "-")
            where.mkdir()
            async with TestClient(TestServer(_planted_app(block, where))) as client:
                probe = _Probe(client)

                async def ask_planted():
                    r = await client.get("/planted")
                    return r.status

                status = await probe.during(ask_planted())

            assert status == 200, f"{name}: premise: the planted request answered"
            assert probe.worst_step >= _BOUND, f"{name}: the step missed it; {probe.says()}"
            assert probe.worst_gap >= _BOUND, f"{name}: premise: the tick saw it; {probe.says()}"


#: How long the step the report test plants keeps the loop.
_REPORTED = 0.15


@pytest.mark.parametrize("how", ["burns its CPU", "sleeps"])
@pytest.mark.asyncio
async def test_the_report_names_the_longest_steps_task_its_line_and_where_its_time_went(how):
    """A failure says what the longest step ran and how its time went, one line a field: the task
    and the frames it resumed in, the step's CPU, and on Linux the loop thread's major faults and
    switches over it. A step that burns its CPU reads its CPU as its time; one that sleeps reads
    almost none, and on Linux at least one voluntary switch, so a block the next failure shows
    says whether the loop thread ran or waited off its CPU, and in whose code."""
    async with TestClient(TestServer(web.Application())) as client:
        probe = _Probe(client, asking=False)

        async def planted() -> None:
            await asyncio.sleep(0)  # the step below resumes here
            if how == "sleeps":
                time.sleep(_REPORTED)
            else:
                began = time.thread_time()
                while time.thread_time() - began < _REPORTED:
                    pass

        await probe.during(planted())

    says = probe.says()
    lines = says.splitlines()
    _, cpu, _ = probe.worst_step_own
    assert probe.worst_step >= _REPORTED, says
    assert lines[0].startswith("the work took "), says
    assert lines[1].startswith(f"the longest step: {probe.worst_step:.3f}s counted"), says
    resumed = f"planted ({os.path.basename(__file__)}:{planted.__code__.co_firstlineno + 1})"
    assert lines[2].startswith("  it ran task ") and resumed in lines[2], says
    if how == "sleeps":
        assert cpu < _REPORTED / 3, f"a sleeping step read {cpu:.3f}s of CPU; {says}"
    else:
        assert cpu >= _REPORTED, f"a burning step read {cpu:.3f}s of CPU; {says}"
    if os.path.exists("/proc/thread-self/stat"):
        assert probe.worst_step_counts is not None, says
        assert lines[3].startswith("  the loop thread over it: "), says
        if how == "sleeps":
            assert probe.worst_step_counts[1] >= 1, f"no voluntary switch over a sleep; {says}"
    else:
        assert probe.worst_step_counts is None, says
        assert not any("the loop thread over it" in line for line in lines), says
    assert all(line.strip() for line in lines), f"an empty line in the report; {says}"


class _StoppedWhileIdle:
    """Stops this process from outside (``SIGSTOP``, then ``SIGCONT`` after :data:`_STOPPED`)
    once, while the event loop waits in its selector: a process the machine gives no CPU while
    it has nothing to do. The loop's next selector wait hands a helper process the word, and
    stays there until the helper has stopped and continued it, so the stop lands in that wait
    and in no step."""

    _HELPER = textwrap.dedent("""
        import os, signal, sys, time
        pid, stopped = int(sys.argv[1]), float(sys.argv[2])
        if sys.stdin.buffer.read(1) == b"s":
            os.kill(pid, signal.SIGSTOP)
            time.sleep(stopped)
            os.kill(pid, signal.SIGCONT)
        sys.stdout.buffer.write(b"c")
        sys.stdout.buffer.flush()
        """)

    def __init__(self) -> None:
        self.stopped = False
        self._helper = subprocess.Popen(
            [sys.executable, "-c", self._HELPER, str(os.getpid()), str(_STOPPED)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def selector(self, real):
        def select(timeout=None):
            if not self.stopped:
                self.stopped = True
                self._helper.stdin.write(b"s")
                self._helper.stdin.flush()
                assert self._helper.stdout.read(1) == b"c", "the helper never continued us"
            return real(timeout)

        return select

    def close(self) -> None:
        if self._helper.poll() is None:
            self._helper.stdin.close()
        self._helper.wait(10)


@pytest.mark.asyncio
async def test_a_process_stopped_while_the_loop_is_idle_does_not_fail_the_probe(tmp_path):
    """🔴 Red before (the tick's gap was the measure): stopped for 0.25 s while it had nothing to
    do, the process went 0.25 s without a turn, and the probe called that a blocked loop. No step
    ran meanwhile, so the step measure passes it, as it does a runner that gives the process no
    core while it waits."""
    async with TestClient(TestServer(_planted_app(_block_by_sleeping, tmp_path))) as client:
        probe = _Probe(client)
        stop = _StoppedWhileIdle()
        selector = asyncio.get_running_loop()._selector  # type: ignore[attr-defined]
        try:

            async def idle():
                await asyncio.sleep(0.05)
                with patch.object(selector, "select", stop.selector(selector.select)):
                    await asyncio.sleep(_STOPPED + 0.1)

            await probe.during(idle())
        finally:
            stop.close()

    assert stop.stopped, "premise: the process was stopped"
    assert probe.worst_gap >= _STOPPED, f"premise: the tick counted the stop; {probe.says()}"
    assert probe.worst_step < _BOUND, f"the stop was counted as a step; {probe.says()}"


#: The step breadth's Linux runner counted short (run 40259756916): a 0.2 s wait to read, the
#: time it then waited for a CPU once woken, and one wait for a CPU more, at an edge of the step.
_WOKEN_AND_READY, _AT_AN_EDGE = 0.113, 0.112


class _AStepsReads:
    """The clocks and the loop thread's time ready with no CPU, as one step's reads see them.

    Its callback holds a wait of *block*, asleep and so never ready, then *ready* waiting for a
    CPU, and uses 1 ms of CPU. A wait for a CPU of :data:`_AT_AN_EDGE` falls due where the thread
    comes back from the kernel: at each of the probe's calls before the callback (``edge="start"``),
    at each after it (``"end"``), or at every one (``"every"``). The wall clock is the
    vDSO's or the commpage's, and calls nothing. ``source`` is the kernel's: ``"linux"`` reads the
    thread's own count in one call, ``"macos"`` the process's in a snapshot of several, the count
    read by the one in the middle."""

    def __init__(
        self, edge: str, source: str, *, block: float = _PLANTED, ready: float = _WOKEN_AND_READY
    ) -> None:
        self.edge, self.source, self.block, self.callback_ready = edge, source, block, ready
        self.t = self.cpu = self.ready = 0.0
        self.ran = False

    def _back_from_the_kernel(self) -> None:
        if self.edge == "every" or (self.edge == "end") == self.ran:
            self.t += _AT_AN_EDGE
            self.ready += _AT_AN_EDGE

    def monotonic(self) -> float:
        return self.t

    def thread_time(self) -> float:
        cpu = self.cpu
        self._back_from_the_kernel()
        return cpu

    def loop_waited(self) -> float | None:
        if self.source != "linux":
            return None  # macOS has no count of the thread's own: nothing is called
        ready = self.ready
        self._back_from_the_kernel()
        return ready

    def loop_slept(self) -> None:
        return None  # read outside the window, so a wait at it is in neither

    def snapshot(self) -> SimpleNamespace:
        at = self.t
        self._back_from_the_kernel()  # the thread list and each thread's
        ready_at, ready = self.t, self.ready
        self._back_from_the_kernel()  # the process's count
        self._back_from_the_kernel()  # the task's
        return SimpleNamespace(at=at, ready_at=ready_at, ready=ready, done=self.t)

    def waited(self, first, then, _threads) -> float:
        return then.ready - first.ready

    def accounts(self, _first, _then, _threads) -> list:
        return []  # the loop thread is the only one

    def callback(self) -> None:
        self.t += self.block
        self.t += self.callback_ready
        self.ready += self.callback_ready
        self.cpu += 0.001
        self.ran = True


def _counted(reads: _AStepsReads) -> float:
    """The step as :class:`_Window` counts it, its clocks being *reads*."""
    with (
        patch.object(time, "monotonic", reads.monotonic),
        patch.object(time, "thread_time", reads.thread_time),
    ):
        window = _Window(reads, set())
        reads.callback()
        wall, cpu, waited = window.end(counted_from=_COUNTED)
    return max(wall - waited, cpu)


def _counted_the_old_way(reads: _AStepsReads) -> float:
    """Planted: the order the probe had, the time ready read before the clocks start and after
    they end, with the CPU clock's call between the wall time's end and the count."""
    own = reads.loop_waited()
    first = reads.snapshot() if own is None else None
    began, cpu = reads.monotonic(), reads.thread_time()
    reads.callback()
    wall, cpu = reads.monotonic() - began, reads.thread_time() - cpu
    now = reads.loop_waited()
    waited = now - own if own is not None else reads.waited(first, reads.snapshot(), set())
    return max(wall - waited, cpu)


def _counted_with_the_reads_inside(reads: _AStepsReads) -> float:
    """Planted: the counts read inside the wall time, every probe call of the kernel with them."""
    began, cpu = reads.monotonic(), reads.thread_time()
    own = reads.loop_waited()
    first = reads.snapshot() if own is None else None
    reads.callback()
    now = reads.loop_waited()
    waited = now - own if own is not None else reads.waited(first, reads.snapshot(), set())
    wall, cpu = reads.monotonic() - began, reads.thread_time() - cpu
    return max(wall - waited, cpu)


@pytest.mark.parametrize("source", ["linux", "macos"])
@pytest.mark.parametrize("edge", ["start", "end"])
def test_a_wait_for_a_cpu_at_a_steps_edge_is_never_left_out_of_it(edge, source):
    """A wait for a CPU at one of the probe's own calls of the kernel, at an edge of a step that
    held a 0.2 s block, was taken off the step though its wall time never held it: the planted
    order counts the step 0.088 s, as breadth did."""
    old = _counted_the_old_way(_AStepsReads(edge, source))
    if source == "linux":
        assert (
            abs(old - (_PLANTED - _AT_AN_EDGE)) < 1e-9
        ), f"premise: breadth's 0.088s, not {old:.3f}s"
    assert old < _BOUND, f"premise: the planted order counts the block under the bound: {old:.3f}s"
    took = _counted(_AStepsReads(edge, source))
    assert took >= _PLANTED - 1e-9, (
        f"the step that held a {_PLANTED}s block was counted {took:.3f}s: a wait for a CPU outside "
        "its wall time was left out of it"
    )


@pytest.mark.parametrize("source", ["linux", "macos"])
def test_a_wait_for_a_cpu_at_the_probes_own_calls_is_never_counted_in_a_step(source):
    """A step the runner held, with no block in it, and a wait for a CPU at every one of the
    probe's own calls of the kernel: each wait is inside both the wall time and the time ready, or
    outside both, and the step is counted for its CPU time. Planted with the counts read inside the
    wall time, those waits were counted in the step (0.324 s of 0.510 s, on a loaded Mac)."""

    def starved() -> _AStepsReads:
        return _AStepsReads("every", source, block=0.0, ready=0.3)

    inside = _counted_with_the_reads_inside(starved())
    assert inside >= _BOUND, f"premise: the planted order counts the waits: {inside:.3f}s"
    took = _counted(starved())
    assert took < _BOUND, (
        f"a step that held no block was counted {took:.3f}s: a wait for a CPU at the probe's own "
        "calls was counted in it"
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS's snapshot reads the process's count")
def test_macos_snapshot_reads_its_clock_just_before_the_call_that_reads_the_count():
    """``ready_at`` is read with no call to the kernel between it and ``proc_pid_rusage``: a wait
    for a CPU at any other of the snapshot's calls is then on one side of both."""
    no_cpu, calls = _NoCpu(), []
    kernel, clock = no_cpu._libsystem, time.monotonic

    class Recorded:
        def __getattr__(self, name):
            real = getattr(kernel, name)

            def call(*args):
                calls.append(f"{name}:{args[1]}" if name == "proc_pidinfo" else name)
                return real(*args)

            return call

    def monotonic() -> float:
        calls.append("clock")
        return clock()

    no_cpu._libsystem = Recorded()
    with patch.object(time, "monotonic", monotonic):
        seen = no_cpu.snapshot()
    at = calls.index("proc_pid_rusage")
    assert calls[at - 1] == "clock", f"a call between ready_at and the count: {calls}"
    assert calls.count("proc_pid_rusage") == 1 and "proc_pidinfo:28" in calls[:at], calls
    assert seen.at <= seen.ready_at <= seen.done, seen


def test_a_window_without_a_threads_own_count_runs_from_one_snapshots_ready_at_to_the_next():
    """Where the kernel keeps no count of the loop thread's own (macOS), the window's wall time is
    from the first snapshot's ``ready_at`` to the second's, not from the clock around them."""

    class Snapshots:
        def __init__(self) -> None:
            self.taken = 0

        def loop_waited(self) -> None:
            return None

        def loop_slept(self) -> None:
            return None

        def snapshot(self) -> SimpleNamespace:
            self.taken += 1
            return SimpleNamespace(ready_at=10.0 * self.taken, ready=0.0)

        def waited(self, first, then, _threads) -> float:
            return 0.0

    with (
        patch.object(time, "monotonic", lambda: 15.0),
        patch.object(time, "thread_time", lambda: 0.0),
    ):
        window = _Window(Snapshots(), set())
        wall, _cpu, _waited = window.end(counted_from=_COUNTED)
    assert (
        window.began == 10.0
    ), f"the window does not start at the first snapshot's ready_at: {window.began}"
    assert wall == 10.0, f"the window does not end at the second snapshot's ready_at: {wall}"


def _answered(reads: _AStepsReads) -> float:
    """A health check's answer as :meth:`_Probe._timed_answer` counts it, its clocks being *reads*,
    and the answer holding what the step above holds. The coroutine is driven by hand: it awaits
    nothing, and no event loop reads the clocks meanwhile."""
    probe = _Probe(None, asking=False)
    probe.no_cpu = reads

    async def answer() -> None:
        reads.callback()

    with (
        patch.object(time, "monotonic", reads.monotonic),
        patch.object(time, "thread_time", reads.thread_time),
    ):
        timed = probe._timed_answer(answer)
        with pytest.raises(StopIteration):
            timed.send(None)
    return probe.slowest_answer


@pytest.mark.parametrize("source", ["linux", "macos"])
@pytest.mark.parametrize("edge", ["start", "end"])
def test_a_wait_for_a_cpu_at_an_answers_edge_is_never_left_out_of_it(edge, source):
    """The health check's answer is timed in the same window as a step, and so is never counted
    short by a wait for a CPU outside it (the step's test above has the planted order)."""
    took = _answered(_AStepsReads(edge, source))
    assert took >= _PLANTED - 1e-9, (
        f"an answer that waited on a {_PLANTED}s block was counted {took:.3f}s: a wait for a CPU "
        "outside its wall time was left out of it"
    )


class _LinuxReads:
    """Linux's counts as a fake kernel gives them, for steps run through :meth:`_Probe._timed` and
    answers through :meth:`_Probe._timed_answer`: each :meth:`spend` takes *wall* seconds, uses
    *cpu* of them, has the count say *ready* of them were ready with no CPU, and has the loop thread
    give up its CPU of its own accord *slept* times. No read costs any time."""

    source = "Linux: each thread's own"

    def __init__(self) -> None:
        self.t = self.cpu = self.ready = 0.0
        self.switches = 0

    def monotonic(self) -> float:
        return self.t

    def thread_time(self) -> float:
        return self.cpu

    def loop_waited(self) -> float:
        return self.ready

    def loop_slept(self) -> int:
        return self.switches

    def spend(self, wall: float, cpu: float, ready: float, slept: int) -> None:
        self.t += wall
        self.cpu += cpu
        self.ready += ready
        self.switches += slept


#: Run 40760127591's longest step, python-suite (3/3) on Linux: 0.284 s in wall time and no CPU,
#: holding the plant's 0.2 s wait to read, with the loop thread's own count at 0.192 s ready, more
#: than the 0.084 s the step less that wait could hold, and one voluntary switch, the wait's own.
_CI_SLEPT_STEP = (0.284, 0.0, 0.192, 1)
#: Its slowest answer, around that step: 0.474 s in wall time, 0.381 s ready by the count.
_CI_SLEPT_ANSWER = (0.474, 0.003, 0.381)


class _VmReads(_NoCpu):
    """macOS as a virtual machine that reads no instruction counts gives it, driven as
    :class:`_LinuxReads` is: the process's count of time ready, and two threads beside the loop's,
    a hasher running or ready and a worker waiting, each credited its whole window less the CPU it
    used (:meth:`_NoCpu.accounts`), or none when *alone*. Each :meth:`spend` takes *wall*
    seconds, the loop thread using *cpu* of them, the process's count rising *ready*, and the
    threads beside using *beside* of CPU. No read costs any time."""

    def __init__(self, *, alone: bool = False) -> None:
        super().__init__()
        self.source = "macOS: the process's, less what its other threads could account for"
        self.t = self.cpu = self.ready = 0.0
        self.beside = (0.0, 0.0)
        self.alone = alone

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def loop_waited(self) -> None:
        return None

    def monotonic(self) -> float:
        return self.t

    def thread_time(self) -> float:
        return self.cpu

    def snapshot(self) -> SimpleNamespace:
        hasher, worker = self.beside
        threads = {threading.get_native_id(): (self.cpu, 1, None)}
        if not self.alone:
            threads.update({1: (hasher, 1, None), 2: (worker, 3, None)})
        return SimpleNamespace(
            at=self.t,
            ready_at=self.t,
            ready=self.ready,
            threads=threads,
            ended=0,
            ending=False,
            done=self.t,
        )

    def spend(
        self, wall: float, cpu: float, ready: float, _slept: int = 0, beside=(0.0, 0.0)
    ) -> None:
        self.t += wall
        self.cpu += cpu
        self.ready += ready
        self.beside = (self.beside[0] + beside[0], self.beside[1] + beside[1])


#: Run 40633805032's longest step, python-suite-macos (1/5) on a virtual machine that reads no
#: instruction counts: 4.421 s in wall time and 0.001 s of CPU, holding the plant's 0.2 s wait to
#: read, the process's count 12.922 s ready over it, and the hasher and asyncio's worker using
#: 0.006178 s and 0.001020 s of CPU. The loop's share came to 4.087 s, and the step was counted
#: 0.334 s.
_VM_STEP = (4.421, 0.001, 12.922, 0, (0.006178, 0.001020))
#: Its slowest answer, around that step: 4.526 s in wall time and 0.003 s of CPU, the process's
#: count 13.503 s ready, and the threads beside using 0.006971 s and 0.001062 s. The loop's share
#: came to 4.458 s, and the answer was counted 0.068 s.
_VM_ANSWER = (4.526, 0.003, 13.503, (0.006971, 0.001062))


def _probed(reads, step: tuple, answer: tuple | None = None) -> _Probe:
    """A probe over *reads* (:class:`_LinuxReads`, :class:`_VmReads`) that ran one *step*
    (wall, CPU, ready, voluntary switches, and the CPU of the threads beside), inside an answer
    of *answer* (wall, CPU, ready, and the CPU beside, the rest beside the step's) when one is
    given, its rest a wait in the loop's selector (one voluntary switch). Driven by hand, as
    :func:`_answered` drives its answer."""
    probe = _Probe(None, asking=False, no_cpu=reads)
    probe.took = step[0] if answer is None else answer[0]
    handle = SimpleNamespace()
    run = probe._timed(threading.get_ident(), lambda _handle: reads.spend(*step))

    async def answered() -> None:
        run(handle)
        if answer is not None:
            wall, cpu, ready, *beside = answer
            rest = [tuple(a - b for a, b in zip(beside[0], step[4]))] if beside else []
            reads.spend(wall - step[0], cpu - step[1], ready - step[2], 1, *rest)

    with (
        patch.object(time, "monotonic", reads.monotonic),
        patch.object(time, "thread_time", reads.thread_time),
    ):
        if answer is None:
            run(handle)
        else:
            timed = probe._timed_answer(answered)
            with pytest.raises(StopIteration):
                timed.send(None)
    return probe


def test_a_linux_step_the_loop_slept_in_is_read_both_ways_and_reported():
    """Each Linux step is read as the count gives it and, beside that, leaving out none of the
    time ready of a step the loop thread slept in, and an answer leaving out none of what such
    steps inside it held; the report and the run's warnings say both."""
    probe = _probed(_LinuxReads(), _CI_SLEPT_STEP, _CI_SLEPT_ANSWER)
    assert probe.slept_steps == 1 and probe.slept_ready == pytest.approx(0.192)
    assert probe.worst_step_unruled == pytest.approx(0.284 - 0.192)
    assert probe.worst_step_ruled == pytest.approx(0.284)
    assert probe.slowest_answer_unruled == pytest.approx(0.474 - 0.381)
    assert probe.slowest_answer_ruled == pytest.approx(0.474 - (0.381 - 0.192))
    assert "steps the loop thread slept in" in probe.says(), probe.says()


def test_a_linux_step_with_a_wait_for_a_cpu_and_no_sleep_is_read_alike_both_ways():
    """A step the loop thread never gave up its CPU in, its wait for one the runner's, reads the
    same both ways, and is reported as no slept step."""
    probe = _probed(_LinuxReads(), (0.284, 0.04, 0.192, 0))
    assert probe.slept_steps == 0
    assert probe.worst_step_ruled == probe.worst_step_unruled == pytest.approx(0.284 - 0.192)
    assert "steps the loop thread slept in" not in probe.says()


def test_a_linux_count_whose_second_file_will_not_open_leaves_the_first_closed(monkeypatch):
    """🔴 Red before: the loop thread's counts are read from two files, and when the second would
    not open the first stayed open. It is closed before the error goes on."""
    real_open, real_close = os.open, os.close
    opened: list[int] = []
    closed: list[int] = []

    def open_(path, flags):
        if path.endswith("/status"):
            raise PermissionError(path)
        opened.append(real_open(os.devnull, flags))
        return opened[-1]

    def close(fd):
        closed.append(fd)
        real_close(fd)

    no_cpu = _NoCpu()
    no_cpu.source = "Linux: each thread's own"
    monkeypatch.setattr(os, "open", open_)
    monkeypatch.setattr(os, "close", close)
    with pytest.raises(PermissionError):
        no_cpu.open()
    assert opened and closed == opened, f"opened {opened}, closed {closed}"
    assert no_cpu._fd is None and no_cpu._status is None


def test_a_linux_step_whose_count_is_ready_through_its_own_sleep_fails_the_probe():
    """🔴 Red before (a Linux step left out all its count gave it): the CI runner's step, 0.284 s
    with no CPU around a 0.2 s wait to read, its count at 0.192 s ready, was counted 0.091 s, and
    its answer 0.093 s, both under the bound, so the block was missed (run 40760127591). The loop
    thread slept in it, so none of the count is left out: the step reads 0.284 s and the answer,
    leaving out none of what that step held, 0.285 s."""
    probe = _probed(_LinuxReads(), _CI_SLEPT_STEP, _CI_SLEPT_ANSWER)
    assert probe.worst_step >= _BOUND, f"the step missed the block; {probe.says()}"
    assert probe.slowest_answer >= _BOUND, f"the answer missed the block; {probe.says()}"


def test_a_linux_step_with_a_wait_for_a_cpu_and_no_sleep_is_left_out_exactly_as_before():
    """The rule's other side: a step the loop thread never gave up its CPU in has the runner's
    wait for a CPU left out as before, all its count gives it."""
    probe = _probed(_LinuxReads(), (0.284, 0.04, 0.192, 0))
    assert probe.worst_step == pytest.approx(0.284 - 0.192), probe.says()


def test_without_the_rule_the_ci_runners_step_is_counted_under_the_bound(monkeypatch):
    """The mutant: with the rule dropped, the CI runner's step and its answer read under the bound
    again, so the plant above fails on it."""
    monkeypatch.setattr(sys.modules[__name__], "_SLEPT_STEPS_EXCUSE_NOTHING", False)
    probe = _probed(_LinuxReads(), _CI_SLEPT_STEP, _CI_SLEPT_ANSWER)
    assert probe.worst_step < _BOUND and probe.slowest_answer < _BOUND, probe.says()


def test_an_answer_is_counted_no_less_than_the_steps_inside_it():
    """🔴 Red before (an answer left out all its count gave it): on a macOS virtual machine that
    reads no instruction counts, the answer around the plant's 0.2 s wait to read was counted
    0.068 s of 4.526 s, and the step that held the wait 0.334 s, so the answer missed the block
    (run 40633805032). The answer was that step and 0.105 s more, yet its count left the loop
    0.372 s more ready than the step's. It now leaves out no more than the step did and its time
    outside it, and is counted as the step was."""
    probe = _probed(_VmReads(), _VM_STEP, _VM_ANSWER)
    assert probe.worst_step == pytest.approx(0.334, abs=0.001), probe.says()
    assert probe.slowest_answer >= _BOUND, f"the answer missed the block; {probe.says()}"
    assert probe.slowest_answer == pytest.approx(probe.worst_step), probe.says()


@pytest.mark.parametrize(
    ("step", "answer"),
    [
        pytest.param((0.05, 0.01, 0.0, 0), (0.5, 0.011, 0.44), id="outside its step"),
        pytest.param((0.3, 0.01, 0.25, 0), (0.5, 0.012, 0.44), id="its step's and outside it"),
    ],
)
def test_an_answer_leaving_out_what_its_steps_did_and_its_time_outside_them_passes(step, answer):
    """The rule's other side: what the runner held the loop thread from a CPU for between steps,
    once woken in its selector, and what a step's own count left out of it, an answer leaves out
    as before, all its count gives it."""
    probe = _probed(_LinuxReads(), step, answer)
    assert probe.slowest_answer == pytest.approx(answer[0] - answer[2]), probe.says()
    assert probe.slowest_answer < _BOUND, probe.says()


def test_without_the_rule_the_virtual_machines_answer_is_counted_under_the_bound(monkeypatch):
    """The mutant: with an answer no longer held to its steps, the virtual machine's answer reads
    under the bound again, so the plant above fails on it."""
    monkeypatch.setattr(sys.modules[__name__], "_held_to_its_steps", lambda waited, *_: waited)
    probe = _probed(_VmReads(), _VM_STEP, _VM_ANSWER)
    assert probe.slowest_answer < _BOUND, probe.says()


def test_an_exception_in_the_probes_own_work_still_runs_the_loops_callback():
    """🔴 Red before: the probe runs each of the loop's callbacks through its own code, and a
    timeout's alarm raises wherever it lands. Landing in the probe's work before the callback, it
    dropped the callback the loop had already taken off its queue, so the task that callback was to
    wake was never woken. Cancelled at the teardown, a task like that never ends (its waiter is
    already done), and the loop's close waited for it until the run was killed. The exception still
    goes on, after the callback has run."""

    class Alarm(BaseException):
        pass

    def landing() -> float | None:
        raise Alarm

    probe = _Probe(client=None, asking=False)
    probe.no_cpu.loop_waited = landing
    ran: list[str] = []
    run = probe._timed(threading.get_ident(), ran.append)
    with pytest.raises(Alarm):
        run("the loop's callback")
    assert ran == ["the loop's callback"]


#: The starved request below runs this many steps, each until it has run this long in wall time or
#: used this much CPU, whichever comes first: no step is over the bound by its own work, and a
#: runner that gives the process no CPU stretches one past it in wall time.
_STARVED_STEPS = 5
_STARVED_WALL = 0.15
_STARVED_CPU = 0.05


@contextlib.contextmanager
def _in_the_background_band():
    """macOS: the whole process in the background band, the lowest it can take, while the block
    runs, and back after, so a child pays the band's crawl only for the requests it measures (one
    starved child took 22.6 s on a CI runner with the band from start to end). Elsewhere it does
    nothing: on Linux the child took nice 19 when it started, which a process cannot undo."""
    if sys.platform != "darwin":
        yield
        return
    os.setpriority(os.PRIO_DARWIN_PROCESS, 0, os.PRIO_DARWIN_BG)
    try:
        yield
    finally:
        os.setpriority(os.PRIO_DARWIN_PROCESS, 0, 0)


def _starved_app() -> web.Application:
    async def starved(_request: web.Request) -> web.Response:
        with _in_the_background_band():
            for _ in range(_STARVED_STEPS):
                began, cpu = time.monotonic(), time.thread_time()
                while time.monotonic() - began < _STARVED_WALL:
                    if time.thread_time() - cpu >= _STARVED_CPU:
                        break
                await asyncio.sleep(0)  # the next one is a step of its own
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/api/healthz", api_healthz)
    app.router.add_get("/starved", starved)
    return app


#: A process that takes a CPU and holds it, for at most the seconds it is given, once it has said
#: so.
_BUSY = textwrap.dedent("""
    import sys, time
    sys.stdout.write("b")
    sys.stdout.flush()
    end = time.monotonic() + float(sys.argv[1])
    while time.monotonic() < end:
        pass
    """)

#: The start of each child below: once it has its gateway up and says so, it waits for the
#: parent to say every CPU is taken, then takes the lowest priority. On Linux that is nice 19.
#: macOS barely weighs a nice value against busy processes (a nice 19 spin measured 0.12-0.20 s of
#: CPU in 0.3 s), so there the request it measures runs in the background band, which macOS
#: schedules beside them in bursts (0.001-0.013 s in 0.3 s, at times half a CPU for 40 ms):
#: :func:`_in_the_background_band`.
_LOWEST = """
    def lowest():
        sys.stdout.write("ready\\n")
        sys.stdout.flush()
        assert sys.stdin.readline() == "go\\n"
        if sys.platform != "darwin":
            os.nice(19)
"""

#: The probe, asking no health check, over the starved request.
_STARVED_CHILD = (
    textwrap.dedent("""
    import asyncio, json, os, sys
    sys.path.insert(0, sys.argv[1])
    import test_a_large_upload_leaves_the_gateway_answering as upload
    from aiohttp.test_utils import TestClient, TestServer
    """)
    + textwrap.dedent(_LOWEST)
    + textwrap.dedent("""
    async def main():
        async with TestClient(TestServer(upload._starved_app())) as client:
            probe = upload._Probe(client, asking=False)
            lowest()

            async def ask():
                return (await client.get("/starved")).status

            status = await probe.during(ask())
            print(json.dumps({
                "status": status, "says": probe.says(), **vars(upload._seen(probe)),
            }), flush=True)

    asyncio.run(main())
    """)
)


def _seen(probe: _Probe) -> SimpleNamespace:
    return SimpleNamespace(
        worst_step=probe.worst_step,
        worst_step_wall=probe.worst_step_wall,
        slowest_answer=probe.slowest_answer,
        slowest_answer_wall=probe.slowest_answer_wall,
    )


def _crowded(child: str, home, *args: str, hold: float) -> dict:
    """Runs ``child`` on the scratch ``home`` once it is up, with every CPU taken by a process of
    its own for at most ``hold`` seconds, and returns the one line of JSON it prints. What the
    child says on stderr goes to a file beside ``home``, and a child that fails, or prints
    nothing, fails the test with the end of it."""
    said = home.parent / "child-stderr.txt"
    with open(said, "w", encoding="utf-8") as stderr:
        child_process = subprocess.Popen(
            [sys.executable, "-c", child, os.path.dirname(__file__), *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            env={**os.environ, "PERSONALCLAW_HOME": str(home)},
        )
    busy: list[subprocess.Popen] = []
    out = ""
    try:
        assert child_process.stdout.readline() == "ready\n", "the child never set up its gateway"
        for _ in range(os.cpu_count() or 2):
            busy.append(
                subprocess.Popen([sys.executable, "-c", _BUSY, str(hold)], stdout=subprocess.PIPE)
            )
        for one in busy:
            assert one.stdout.read(1) == b"b", "a busy process never started"
        child_process.stdin.write("go\n")
        child_process.stdin.flush()
        out = child_process.stdout.readline()
    finally:
        for one in busy:
            one.kill()
            one.wait()
        child_process.stdin.close()
        code = child_process.wait(10)
        if code != 0 or not out:
            tail = said.read_text(encoding="utf-8", errors="replace")[-3000:].strip()
            pytest.fail(
                f"the child exited {code} and printed {out!r}; on stderr: {tail or 'nothing'}"
            )
    return json.loads(out)


#: How many times a test below sets up its crowd before it takes its premise as unmet.
_TRIES = 3

#: How long the busy processes hold every CPU for a try. On macOS a process in the background band
#: beside them is at times given no turn at all until they let go: on a CI runner the loop once had
#: no turn for 17.2 s, and the block it was to make came after, once the CPUs were free (#5499); the
#: starved-step test below once took 23.31 s while they held for 20 s. There they hold a few times
#: what a crowded try takes (under 2 s on a CI runner), so a try that stalls ends soon and the next
#: one starts. Linux, at nice 19, is given turns, only slowly (a try took up to 12 s on a CI
#: runner).
_HOLD = 5 if sys.platform == "darwin" else 20


def _until_crowded(
    tmp_path,
    child: str,
    *,
    args=lambda where: (),
    premise,
    holds,
    measure,
    measured,
    required=None,
    required_premise="",
) -> dict:
    """Runs ``child`` crowded (:func:`_crowded`) up to :data:`_TRIES` times, each try in a folder
    of its own, until ``holds`` says its ``premise`` held, and returns that try's line. A premise
    unmet every time fails on Linux, where nice 19 makes it hold each time, and skips on macOS,
    whose background band does not always (:data:`_HOLD`), naming the ``measure`` each try
    ``measured``. A ``required`` part of the premise, which the band has no say in, fails on
    every platform when no try met it: a test whose tries never reach what it checks has
    checked nothing."""
    measures, met = [], False
    for n in range(_TRIES):
        where = tmp_path / f"try-{n}"
        where.mkdir()
        seen = _crowded(child, where / "home", *args(where), hold=_HOLD)
        assert seen["status"] == 200, f"premise: the request answered; {seen['says']}"
        measures.append(measured(seen))
        met = met or required is None or required(seen)
        if holds(seen):
            return seen
    unmet = (
        f"premise: {premise}, in none of {_TRIES} tries ({measure}: {', '.join(measures)});"
        f" the last: {seen['says']}"
    )
    if not met:
        pytest.fail(f"premise: {required_premise}, in none of {_TRIES} tries; {unmet}")
    if sys.platform == "darwin":
        pytest.skip(unmet)
    pytest.fail(unmet)


#: The start of a child that is ready at once and waits to be told to go (:func:`_crowded`).
_READY = """
    import json, sys
    sys.stdout.write("ready\\n")
    sys.stdout.flush()
    assert sys.stdin.readline() == "go\\n"
"""


@pytest.mark.parametrize(
    ("child", "said"),
    [
        ('raise RuntimeError("planted before it was ready")', "planted before it was ready"),
        (
            textwrap.dedent(_READY) + 'raise RuntimeError("planted once told to go")',
            "RuntimeError: planted once told to go",
        ),
        (textwrap.dedent(_READY), "exited 0 and printed ''; on stderr: nothing"),
    ],
    ids=["before it was ready", "once told to go", "printing nothing"],
)
def test_a_crowded_child_that_fails_says_why(tmp_path, child, said):
    """🔴 Red before: the beside-thread child imported another clone's ``personalclaw``
    (tests/tree_under_test.py), raised and printed nothing, and its test failed with "the child
    failed: ''", the child's traceback only in pytest's captured stderr. A child that exits 0
    printing nothing failed on reading its empty line as JSON. Each failure now says the child's
    exit code, what it printed and the end of its stderr."""
    with pytest.raises(pytest.fail.Exception) as failed:
        _crowded(child, tmp_path / "home", hold=1.0)
    assert said in str(failed.value), failed.value


def test_a_step_the_runner_gives_no_cpu_does_not_fail_the_probe(tmp_path):
    """🔴 Red before (a step's wall time was the measure): a request whose steps wait on nothing
    and each use at most 0.05 s of CPU, run at the lowest priority while every CPU is taken, had a
    step the runner stretched past 0.1 s in wall time, and the probe counted a loop blocked that
    long. The loaded runner took that time, not anything the step waited on, so the probe leaves
    it out and counts the step for the CPU time it used. No health check is asked, so no other
    thread runs beside it: on macOS a thread that does could have spent that time ready, and the
    step would be counted in wall time (the test below).

    The crowd is set up again (:func:`_until_crowded`) until the runner held a step: on macOS a
    try that stalls until the busy processes let go runs its steps on free CPUs."""
    assert _NoCpu().source != "none", "premise: this kernel counts the time ready with no CPU"
    seen = _until_crowded(
        tmp_path,
        _STARVED_CHILD,
        premise="the runner held a step",
        holds=lambda seen: seen["worst_step_wall"] >= _BOUND,
        measure="the longest step in wall time",
        measured=lambda seen: f"{seen['worst_step_wall']:.3f}s",
    )
    assert seen["worst_step"] < _BOUND, f"a step the runner took was counted; {seen['says']}"


#: How long the health check below stays under way in its worker once the loop is told of it.
_HELD = 0.05


class _HeldHealthCheck:
    """``_identity`` for the first health check asked once :meth:`arm` is called: it tells the
    loop it is under way and stays so for :data:`_HELD`, so a block the loop starts on hearing it
    lands inside that health check's answer."""

    def __init__(self) -> None:
        self.under_way = asyncio.Event()
        self.blocked = (0.0, 0.0)
        self._armed = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def arm(self) -> None:
        from personalclaw.dashboard import handlers_system

        self._loop = asyncio.get_running_loop()
        self._armed.set()
        handlers_system._IDENTITY.forget()  # so the next health check reads, as the first does

    def identity(self, real):
        def call(*args, **kwargs):
            if self._armed.is_set():
                self._armed.clear()
                self._loop.call_soon_threadsafe(self.under_way.set)
                time.sleep(_HELD)
            return real(*args, **kwargs)

        return call


def _blocked_while_answering_app(held: _HeldHealthCheck, where) -> web.Application:
    async def planted(_request: web.Request) -> web.Response:
        with _in_the_background_band():
            held.arm()
            await held.under_way.wait()
            began = time.monotonic()
            _CROWDED_BLOCK(where)  # on the loop, while that health check waits on it
            held.blocked = (began, time.monotonic())
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/api/healthz", api_healthz)
    app.router.add_get("/planted", planted)
    return app


#: The probe over a wait to read on the loop, made while a health check is under way, beside a
#: thread that hashes as an upload's worker does and so is ready to run whenever it is not.
_BESIDE_A_READY_THREAD_CHILD = (
    textwrap.dedent("""
    import asyncio, hashlib, json, os, sys, threading, time
    from pathlib import Path
    from unittest.mock import patch
    sys.path.insert(0, sys.argv[1])
    import test_a_large_upload_leaves_the_gateway_answering as upload
    from aiohttp.test_utils import TestClient, TestServer
    from personalclaw.dashboard import handlers_system
    """)
    + textwrap.dedent(_LOWEST)
    + textwrap.dedent("""
    async def main():
        held = upload._HeldHealthCheck()
        app = upload._blocked_while_answering_app(held, Path(sys.argv[2]))
        identity = held.identity(handlers_system._identity)
        stop, hashed = threading.Event(), [(time.monotonic(), 0.0)]

        def hash_beside():
            chunk = os.urandom(1 << 20)
            while not stop.is_set():
                hashlib.sha256(chunk).digest()
                hashed.append((time.monotonic(), time.thread_time()))

        def used_by(moment):
            return max(cpu for at, cpu in hashed if at <= moment)

        with patch.object(handlers_system, "_identity", identity):
            async with TestClient(TestServer(app)) as client:
                probe = upload._Probe(client)
                lowest()
                beside = threading.Thread(target=hash_beside)
                beside.start()

                async def ask():
                    return (await client.get("/planted")).status

                try:
                    status = await probe.during(ask())
                finally:
                    stop.set()
                    beside.join()
                began, ended = held.blocked
                share = (used_by(ended) - used_by(began)) / (ended - began)
                print(json.dumps({
                    "status": status, "says": probe.says(), "share": share,
                    **vars(upload._seen(probe)),
                }), flush=True)

    asyncio.run(main())
    """)
)


def test_a_block_beside_a_thread_that_waits_for_a_cpu_still_fails_the_probe(tmp_path):
    """The vacuity arm for the time left out: the time a thread beside the loop spends waiting
    for a CPU is not the loop's. A wait to read on the loop for 0.2 s, made while a health check
    is under way, at the lowest priority while every CPU is taken, beside a thread that hashes
    and so waits for a CPU most of that time, is a step over the bound and an answer over it.

    The crowd is set up again (:func:`_until_crowded`) until the thread beside waited for a CPU
    during the block: on a CI runner a try that stalled made its block after the busy processes
    let go, when the thread beside had a CPU to itself.

    The wait is timed from its own call (:func:`_block_by_waiting_to_read`), not a read from
    :func:`_block_by_reading`'s pipe. On Linux CI (run 40181486016) that read's writer waited
    out its 0.2 s while the loop, at the lowest priority, waited for a CPU, so the read found its
    data there and never blocked: no step was charged more than 0.054 s, from a longest step of
    0.507 s in wall time, the rest the loop's own wait for a CPU."""
    seen = _until_crowded(
        tmp_path,
        _BESIDE_A_READY_THREAD_CHILD,
        args=lambda where: (str(where),),
        premise="the thread beside waited for a CPU during the block",
        holds=lambda seen: seen["share"] < 0.5,
        measure="the share of a CPU the thread beside used",
        measured=lambda seen: f"{seen['share']:.0%}",
    )
    says = f"the thread beside used {seen['share']:.0%} of a CPU during the block; {seen['says']}"
    assert seen["worst_step"] >= _BOUND, f"the step missed the block; {says}"
    assert seen["slowest_answer"] >= _BOUND, f"the answer missed the block; {says}"


@pytest.mark.parametrize("late", [0.0, 1.5 * _PLANTED], ids=["on time", "late"])
def test_the_crowded_plants_read_waits_all_its_time_however_late_the_loop_comes_to_it(
    tmp_path, late
):
    """On Linux CI (run 40181486016) the crowded plant read from :func:`_block_by_reading`'s pipe,
    whose writer's delay runs from when both ends are open. The loop, at the lowest priority
    under the crowd, waited for a CPU past that delay, found the data there and never blocked, so
    the probe rightly charged it none of the block. Its wait now runs from its own call: a
    reader held back past the delay, as that loop was, still waits all of it."""
    waited = _CROWDED_BLOCK(tmp_path, late=late)
    says = f"the read waited {waited:.3f}s of {_PLANTED}s, {late:g}s late"
    assert waited >= _PLANTED * 0.95, says


#: How long the thread that stands for the loop waits after the join before its second snapshot:
#: the kernel adds an ended thread's CPU, and its time ready, to the process's counts up to 2.5 ms
#: after its join, and a snapshot taken before then holds none of that thread's time.
_AFTER_THE_JOIN = 0.01

#: Between two snapshots, while every CPU is taken: a thread in the background band that spins for
#: 0.3 s, so it is ready to run whenever it is not running, and ends, while the thread that stands
#: for the loop waits for it to end. The stand-in stays out of the band, so its own waits for a CPU,
#: woken from the start, the join and the pause after it, are a few milliseconds.
_ENDED_THREAD_CHILD = (
    textwrap.dedent("""
    import json, os, sys, threading, time
    sys.path.insert(0, sys.argv[1])
    import test_a_large_upload_leaves_the_gateway_answering as upload
    """)
    + textwrap.dedent(_LOWEST)
    + textwrap.dedent("""
    no_cpu, spun = upload._NoCpu(), {}
    lowest()

    def spin():
        os.setpriority(os.PRIO_DARWIN_THREAD, 0, os.PRIO_DARWIN_BG)  # this thread alone
        began, cpu = time.monotonic(), time.thread_time()
        while time.monotonic() - began < 0.3:
            pass
        spun["share"] = (time.thread_time() - cpu) / (time.monotonic() - began)

    first = no_cpu.snapshot()
    thread = threading.Thread(target=spin)
    thread.start()
    thread.join()
    time.sleep(upload._AFTER_THE_JOIN)
    then = no_cpu.snapshot()
    print(json.dumps({
        "status": 200, "says": no_cpu.source, "share": spun["share"],
        "counted": then.ended != first.ended,
        "waited": no_cpu.waited(first, then, {threading.get_native_id()}),
    }), flush=True)
    """)
)


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS's count is the process's: elsewhere each thread's count is its own (Linux's "
    "schedstat), so a thread that came and went is never in the loop's, and the stand-in's own "
    "wait for a CPU, after the start and after the join, is rightly the loop's",
)
def test_a_thread_that_came_and_went_is_never_counted_as_the_loops_wait(tmp_path):
    """🔴 Red before, on macOS: a thread that started and ended between two snapshots was in
    neither list, so its time ready to run was left in the process's count and charged to the
    loop thread. The crowded plant's read has one, its writer, and on a CI runner a health check's
    answer beside the block left out 0.295 s of that thread's wait as the loop's. Here the thread
    that stands for the loop waits for the spinning thread to end, so the only wait between the
    two snapshots that is its own is its wait for a CPU each time it wakes.

    That wait is kept small: only the spinning thread is in the background band. With the
    stand-in in it too, a try on a CI runner (run 40391397926) charged it 0.079 s, the thread
    beside at 15% of a CPU: that try's second snapshot came before the kernel had added the ended
    thread's time to the process's counts, so none of it was there to charge, and the 0.079 s
    was the stand-in's own wait at the band's priority. And a try counts only once the ended
    thread's time is in the count at the second snapshot (:data:`_AFTER_THE_JOIN`): before then
    there is nothing of it to charge, so such a try cannot tell this code from the code before.

    macOS alone: on Linux each thread's count is its own, and the stand-in's wait for a CPU after
    the start and after the join is, rightly, the loop's: main's push run 40218103397 charged it
    0.115 s, the thread beside at 1% of a CPU."""
    seen = _until_crowded(
        tmp_path,
        _ENDED_THREAD_CHILD,
        premise="the thread that ended waited for a CPU, and its time was in the count",
        holds=lambda seen: seen["share"] < 0.5 and seen["counted"],
        measure="the share of a CPU it used, and whether its time was counted",
        measured=lambda seen: f"{seen['share']:.0%} {'counted' if seen['counted'] else 'not'}",
        required=lambda seen: seen["counted"],
        required_premise="the ended thread's time was in the count at the second snapshot",
    )
    says = f"the thread that ended used {seen['share']:.0%} of a CPU ({seen['says']})"
    assert seen["waited"] < 0.05, f"the loop was charged {seen['waited']:.3f}s; {says}"


@pytest.mark.parametrize(
    ("counted", "tries", "outcome"),
    [
        pytest.param([False] * _TRIES, _TRIES, "fails", id="the time never arrives"),
        pytest.param([False, True], 2, "passes", id="the time arrives on the second try"),
    ],
)
def test_a_came_and_went_run_whose_ended_threads_time_never_arrives_fails(
    tmp_path, monkeypatch, counted, tries, outcome
):
    """The test above retries a try whose ended thread's time is not yet in the count, at most
    :data:`_TRIES` times, and fails, on every platform, when no try had it: it has then checked
    nothing. The crowded runs are planted, each a line the child could print."""
    lines = iter(counted)
    ran = []

    def planted(child, home, *args, hold):
        ran.append(home)
        return {
            "status": 200,
            "says": "planted",
            "share": 0.1,
            "counted": next(lines),
            "waited": 0.0,
        }

    monkeypatch.setattr(sys.modules[__name__], "_crowded", planted)
    if outcome == "fails":
        try:
            test_a_thread_that_came_and_went_is_never_counted_as_the_loops_wait(tmp_path)
        except pytest.skip.Exception as skipped:
            pytest.fail(f"it skipped, having checked nothing: {skipped}")
        except pytest.fail.Exception as failed:
            assert "the ended thread's time was in the count" in str(failed), failed
        else:
            pytest.fail("it passed, though no try had the ended thread's time in the count")
    else:
        test_a_thread_that_came_and_went_is_never_counted_as_the_loops_wait(tmp_path)
    assert len(ran) == tries


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS's count is the process's alone")
@pytest.mark.parametrize("ending", ["a thread listed but not read", "one more counted than read"])
def test_a_thread_ending_at_a_snapshot_has_its_window_counted_in_wall_time(ending):
    """A thread that is ending is in neither snapshot's list, nor yet in the ended threads' CPU:
    the measure plant above once had 0.163 s charged to the loop that way, under load. A snapshot
    that lists a thread it cannot read, or whose kernel thread count is above the threads it
    read, saw one ending, and a window with such an end leaves nothing out."""
    no_cpu = _NoCpu()
    first = no_cpu.snapshot()
    assert not first.ending, "premise: no thread was ending at the first snapshot"
    real = no_cpu._libsystem.proc_pidinfo
    unread = []

    def told(pid, flavor, arg, buf, size):
        got = real(pid, flavor, arg, buf, size)
        if ending == "one more counted than read" and flavor == 4:  # PROC_PIDTASKINFO
            buf._obj.pti_threadnum += 1
        if ending == "a thread listed but not read" and flavor == 15 and not unread:
            unread.append(arg)
            return 0  # the first thread listed can no longer be read
        return got

    class _Told:
        def __getattr__(self, name):
            return told if name == "proc_pidinfo" else getattr(no_cpu._libsystem_real, name)

    no_cpu._libsystem_real, no_cpu._libsystem = no_cpu._libsystem, _Told()
    then = no_cpu.snapshot()
    assert then.ending, f"{ending}, and the snapshot did not see a thread ending"
    # With the process's count grown by more than every thread read can account for, the
    # window is still counted in wall time.
    grown = SimpleNamespace(**{**vars(then), "ready": then.ready + 1.0})
    assert no_cpu.waited(first, grown, {threading.get_native_id()}) == 0.0


def _snapshot(at: float, ready: float, threads: dict) -> SimpleNamespace:
    """A macOS snapshot as :meth:`_NoCpu.snapshot` gives one, from its numbers."""
    return SimpleNamespace(
        at=at, ready_at=at, ready=ready, threads=threads, ended=0, ending=False, done=at
    )


def _macos_no_cpu() -> _NoCpu:
    """A :class:`_NoCpu` that reads snapshots as macOS's, on any host: :meth:`_NoCpu.waited` and
    :meth:`_NoCpu.accounts` read nothing but the snapshots they are given."""
    no_cpu = _NoCpu()
    no_cpu.source = "macOS: the process's, less what its other threads could account for"
    return no_cpu


#: Run 40496838581's longest step on a CI runner: 1.474 s in wall time and no CPU, with the
#: process 1.793 s ready with no CPU over it, and a block of 0.2 s inside it.
_CI_STEP, _CI_READY = 1.474, 1.793


@pytest.mark.parametrize(
    "ran", [(400, 300, 200), (None, None, None)], ids=["under a microsecond", "counts unread"]
)
def test_a_thread_that_ran_beside_a_step_is_not_taken_for_idle_for_its_cpu_time(ran):
    """🔴 Red before (a thread's run was read from its CPU time): the CI step's two other threads
    were waiting at both ends with their CPU times, which macOS gives in whole microseconds,
    unchanged, and were taken for idle though each had run. The process's whole 1.793 s ready was
    left out as the loop's, and the step was counted 0.000 s, its 0.2 s block lost. Each ran a few
    hundred instructions between, so each could account for the window; a thread whose count could
    not be read is taken to have run."""
    loop, beside, worker = 1, 2, 3
    start = None if ran[0] is None else 0
    first = _snapshot(
        0.0, 0.0, {loop: (0.0, 1, start), beside: (0.5, 3, start), worker: (0.1, 3, start)}
    )
    then = _snapshot(
        _CI_STEP,
        _CI_READY,
        {loop: (0.0, 1, ran[0]), beside: (0.5, 3, ran[1]), worker: (0.1, 3, ran[2])},
    )
    counted = max(_CI_STEP - _macos_no_cpu().waited(first, then, {loop}), 0.0)
    assert counted >= _BOUND, f"the step was counted {counted:.3f}s of its {_CI_STEP}s"


def test_threads_that_ran_nothing_and_waited_at_both_ends_account_for_nothing():
    """The rule's other side, which keeps what the probe leaves out on macOS: other threads that
    ran no instruction between and were waiting at both ends could not have been ready, so the
    process's time ready over the step is the loop's own wait for a CPU, and all of it is left
    out."""
    loop, beside, worker = 1, 2, 3
    idle = {beside: (0.5, 3, 1_000), worker: (0.1, 3, 2_000)}
    first = _snapshot(0.0, 0.0, {loop: (0.0, 1, 0), **idle})
    then = _snapshot(_CI_STEP, 1.2, {loop: (0.0, 1, 900), **idle})
    no_cpu = _macos_no_cpu()
    assert no_cpu.waited(first, then, {loop}) == pytest.approx(1.2)
    assert [row[-1] for row in no_cpu.accounts(first, then, {loop})] == [0.0, 0.0]


#: Run 40518880630's health check on a CI runner, a macOS virtual machine: its answer took 1.967 s
#: in wall time and 0.0083 s of the loop thread's CPU, beside two threads whose instructions could
#: not be read, with a block of 0.2 s inside it. To count it 0.008 s, the process's count of time
#: ready over it was 5.89 s or more; three threads, one of them blocked 0.2 s, could have been
#: ready for at most 5.69 s of it.
_CI_ANSWER, _CI_ANSWER_CPU = 1.967, 0.0083


def test_a_count_of_more_time_ready_than_the_window_holds_leaves_nothing_out():
    """🔴 Red before (a window left out whatever its count gave the loop thread): over the CI
    answer, with the process's count at 5.90 s, the two threads beside were credited all they
    could account for and the loop thread's share came to more than the answer less its CPU time,
    which no thread can be ready for. The answer was counted 0.008 s and its block lost. A count
    that says more than the window holds is wrong, so nothing is left out of it."""
    loop, beside, worker = 1, 2, 3
    first = _snapshot(
        0.0, 0.0, {loop: (0.0, 1, None), beside: (0.0, 1, None), worker: (0.0, 3, None)}
    )
    then = _snapshot(
        _CI_ANSWER,
        5.90,
        {loop: (_CI_ANSWER_CPU, 1, None), beside: (0.002, 1, None), worker: (0.0008, 3, None)},
    )
    said = _macos_no_cpu().waited(first, then, {loop})
    assert said > _CI_ANSWER - _CI_ANSWER_CPU, f"premise: the count held too much ({said:.3f}s)"
    left_out = _believed(_CI_ANSWER, _CI_ANSWER_CPU, said)
    counted = max(_CI_ANSWER - left_out, _CI_ANSWER_CPU)
    assert counted >= _BOUND, f"the answer was counted {counted:.3f}s of its {_CI_ANSWER}s"


@pytest.mark.parametrize("over", [0.0, _COUNT_SLACK / 2], ids=["within the window", "in the slack"])
def test_a_count_the_window_can_hold_is_left_out_whole(over):
    """The guard's other side: a window whose count gives the loop thread no more than the window
    less its CPU time, or up to :data:`_COUNT_SLACK` past it, leaves all of it out, as a step the
    runner gave no CPU needs."""
    wall, cpu = 0.5, 0.04
    assert _believed(wall, cpu, wall - cpu + over) == wall - cpu + over


#: Run 40855070354's longest step, python-suite-macos (3/5) on a macOS virtual machine: the
#: starved-step plant's, 0.208 s in wall time with 0.005 s of CPU, the loop thread the process's
#: only thread, and its count 0.206 s ready, 3 ms past the window less its CPU.
_CI_LONE_STEP = (0.208, 0.005, 0.206, 0, (0.0, 0.0))


def test_a_lone_threads_count_a_quantum_past_its_window_is_left_out():
    """🔴 Red before (a count past the window less its CPU by more than 1 ms left nothing out):
    the CI runner's starved step, its count 3 ms past, was counted 0.208 s in wall time, over the
    bound, for a step the runner took (run 40855070354). The loop thread was the process's only
    thread, so the count was its own, which lags by up to a quantum: it is left out, and the step
    is counted for its CPU."""
    probe = _probed(_VmReads(alone=True), _CI_LONE_STEP)
    assert probe.worst_step == pytest.approx(0.005), probe.says()
    assert probe.worst_step < _BOUND, probe.says()


def test_without_the_lag_the_ci_runners_lone_step_is_counted_in_wall_time(monkeypatch):
    """The mutant: with no lag allowed, the CI runner's starved step is counted over the bound
    again, so the plant above fails on it."""
    monkeypatch.setattr(sys.modules[__name__], "_A_LONE_THREADS_COUNT_LAG", 0.0)
    probe = _probed(_VmReads(alone=True), _CI_LONE_STEP)
    assert probe.worst_step >= _BOUND, probe.says()


class _LinuxSnapshots(_VmReads):
    """Linux's counts read through snapshots, as a window reads them when the loop thread's own
    file is not open: each thread's own count, the loop thread alone, driven as :class:`_VmReads`
    is."""

    def __init__(self) -> None:
        super().__init__(alone=True)
        self.source = "Linux: each thread's own"

    def snapshot(self) -> SimpleNamespace:
        threads = {threading.get_native_id(): self.ready}
        return SimpleNamespace(
            at=self.t,
            ready_at=self.t,
            ready=0.0,
            threads=threads,
            ended=0,
            ending=False,
            done=self.t,
        )


def test_a_linux_count_a_few_ms_past_its_window_still_leaves_nothing_out():
    """The lag is macOS's alone: a Linux thread's count is brought up to date as it is read, so
    the CI runner's lone step, its count 3 ms past, read through Linux's snapshots, leaves nothing
    out and is counted in wall time."""
    reads = _LinuxSnapshots()
    with (
        patch.object(time, "monotonic", reads.monotonic),
        patch.object(time, "thread_time", reads.thread_time),
    ):
        window = _Window(reads, {threading.get_native_id()})
        reads.spend(*_CI_LONE_STEP)
        wall, cpu, waited = window.end(counted_from=_COUNTED)
    assert window.said == pytest.approx(0.206), "premise: the count ran 3 ms past"
    assert waited == 0.0 and wall == pytest.approx(0.208)


@pytest.mark.parametrize(
    ("alone", "step"),
    [
        pytest.param(False, (1.967, 0.0083, 1.9688 + 2 * 1.967, 0, (0.0, 0.0)), id="a share"),
        pytest.param(True, (0.318, 0.04, 0.485, 0, (0.0, 0.0)), id="a lone count far past"),
    ],
)
def test_a_count_past_its_window_by_more_than_its_own_lag_still_leaves_nothing_out(alone, step):
    """The lag's other side. With threads beside, the loop's share 10.1 ms past the window is
    still taken as wrong, as the CI answer's was over a block of 0.2 s (run 40518880630). A lone
    thread's count of 0.485 s in a 0.318 s step, past it by more than a quantum, is too (as a
    local run of the starved plant's once read)."""
    probe = _probed(_VmReads(alone=alone), step)
    assert probe.worst_step == pytest.approx(step[0]), probe.says()


# ── a reading the runner cannot place is measured again ─────────────────────────────────────


class _StarvedRunner(_NoCpu):
    """A macOS virtual machine that reads no instruction counts, its process starved: each
    snapshot lists the loop thread and one other, waiting, neither's instructions read, and the
    process's count says it was ready for every moment its loop thread did not run. So a step of
    the loop's that waits on a write and one the runner stalls read alike to the probe, as on run
    40578869717. *instructions* reads both threads' counts instead, the other thread's moving, so
    the probe can tell it ran and could have been the one ready; *starved* False has the count say
    the process was never ready."""

    def __init__(self, *, instructions: bool = False, starved: bool = True) -> None:
        super().__init__()
        self.source = "macOS: the process's, less what its other threads could account for"
        self.instructions, self.starved = instructions, starved
        self._began, self._cpu = time.monotonic(), time.thread_time()

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def loop_waited(self) -> float | None:
        return None

    def snapshot(self) -> SimpleNamespace:
        at, cpu = time.monotonic(), time.thread_time() - self._cpu
        ran = int(at * 1e9) if self.instructions else None
        threads = {threading.get_native_id(): (cpu, 1, ran), 0: (0.0, 3, ran)}
        ready = max(0.0, at - self._began - cpu) if self.starved else 0.0
        return SimpleNamespace(
            at=at, ready_at=at, ready=ready, threads=threads, ended=0, ending=False, done=at
        )


#: How long the planted stall holds the loop thread off its CPU: a whole process stopped by the
#: virtual machine, as run 40578869717's was for 0.096 s of a 0.097 s step, but over the bound.
_STALLED = 0.15


def _stalled_once_app(stalls: list[float]) -> web.Application:
    """A request whose first answer the runner stalls for the first of *stalls*, and so on: what
    the starved runner's count reads as ready, as a stall of the machine's is."""

    async def stalled(_request: web.Request) -> web.Response:
        if stalls:
            time.sleep(stalls.pop(0))  # stands for the machine's stall, never the loop's own
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/api/healthz", api_healthz)
    app.router.add_get("/planted", stalled)
    return app


def _measure_on(runner, app_for):
    """A measure for :func:`_judged`: one request to ``/planted`` of the app *app_for* makes for
    each gateway (a folder), probed on a fresh *runner*."""

    async def measure(where) -> _Probe:
        async with TestClient(TestServer(app_for(where))) as client:
            probe = _Probe(client, no_cpu=runner())

            async def ask() -> int:
                r = await client.get("/planted")
                return r.status

            assert await probe.during(ask()) == 200, "premise: the planted request answered"
        return probe

    return measure


def _folders(tmp_path):
    """The first gateway's folder, and a maker of fresh ones for :func:`_judged`."""
    made = iter(range(1, 10))
    return _mkdir(tmp_path / "first"), lambda: _mkdir(tmp_path / f"again-{next(made)}")


@pytest.mark.asyncio
async def test_on_a_starved_runner_a_blocking_write_on_the_loop_fails_both_measurements(tmp_path):
    """The differential's failing side: on a runner that reads no instruction counts, with the
    process starved, a write on the loop that waits on the disk reads as time ready, so the first
    reading cannot be placed and is measured again on a fresh gateway. The write is the upload's
    own, so it is in the second too, which is judged and fails. The report says why."""
    first, again = _folders(tmp_path)
    measure = _measure_on(_StarvedRunner, lambda where: _planted_app(_block_by_writing, where))
    probe = await _judged(measure, first, again)
    assert probe.before is not None, f"premise: the first reading was undecided; {probe.says()}"
    assert probe.before.worst_step >= _BOUND, f"the first missed the write; {probe.says()}"
    assert probe.worst_step >= _BOUND, f"the second missed the write; {probe.says()}"
    says = probe.says()
    assert "this runner reads no instruction counts" in says, says
    assert "a block that comes only sometimes can pass" in says and "Linux" in says, says
    assert "measured again" in says, says


@pytest.mark.asyncio
async def test_on_a_starved_runner_a_stalled_idle_loop_passes_the_second_measurement(tmp_path):
    """🔴 Red before (the first reading was the one judged): the runner stalls the process once,
    while the loop runs a step that waits on nothing, and the first reading is over the bound by
    nothing the probe can place. Measured again on a fresh gateway, the stall is not there, and
    the second reading is under the bound."""
    first, again = _folders(tmp_path)
    stalls = [_STALLED]
    measure = _measure_on(_StarvedRunner, lambda _where: _stalled_once_app(stalls))
    probe = await _judged(measure, first, again)
    assert probe.before is not None, f"premise: the first reading was undecided; {probe.says()}"
    assert probe.before.worst_step >= _BOUND, f"premise: the stall failed the first; {probe.says()}"
    assert probe.worst_step < _BOUND, probe.says()
    assert probe.slowest_answer < _BOUND, probe.says()


@pytest.mark.parametrize(
    ("runner", "unread"),
    [
        pytest.param(
            lambda: _StarvedRunner(instructions=True), False, id="instruction counts read"
        ),
        pytest.param(lambda: _StarvedRunner(starved=False), True, id="no time ready to place"),
    ],
)
@pytest.mark.asyncio
async def test_a_reading_over_the_bound_by_what_the_probe_can_place_is_never_measured_again(
    tmp_path, runner, unread
):
    """Only a reading that cannot be placed is measured again: where the runner reads instruction
    counts the probe leaves out what it can prove and judges the rest, and where the process's
    count says it was never ready, the window is over the bound whatever the count. A runner's
    stall that comes once fails such a reading: it is measured once."""
    first, again = _folders(tmp_path)
    stalls = [_STALLED]
    probe = await _judged(
        _measure_on(runner, lambda _where: _stalled_once_app(stalls)), first, again
    )
    assert probe.before is None, f"it was measured again; {probe.says()}"
    assert probe.worst_step >= _BOUND, f"premise: the stall is over the bound; {probe.says()}"
    assert ("this runner reads no instruction counts" in probe.says()) == unread, probe.says()


def test_a_reading_judged_by_its_steps_alone_is_not_measured_again_for_an_answer():
    """The security log's test judges its steps alone: an answer over the bound, there, does not
    make the reading undecided, and a step over it still does."""
    probe = _Probe(None, asking=False, no_cpu=_StarvedRunner())
    probe.snapshots = 1
    probe.slowest_answer, probe.slowest_answer_could = 0.3, 0.01
    assert probe.undecided() and not probe.undecided(answers=False)
    probe.worst_step, probe.worst_step_could = 0.3, 0.01
    assert probe.undecided(answers=False)
    probe.worst_step_could = _BOUND
    assert not probe.undecided(answers=False), "a step over the bound whatever the count fails"


# ── what the upload's requests read and write on disk, they do off the loop ─────────────────


def _recorder(ran: list[int], real):
    """*real*, recording the thread of each call into *ran*."""

    def call(*args, **kwargs):
        ran.append(threading.get_ident())
        return real(*args, **kwargs)

    return call


@pytest.mark.asyncio
async def test_the_health_check_reads_the_disk_in_a_worker_thread(gateway, monkeypatch):
    """🔴 Red before: the health check resolved the home and the folder the code is served from
    on the event loop. Each walks the file system and waits on the disk (0.13 s caught on a busy
    machine, in the very check this file asks while an upload lands). Both are read in a worker
    thread."""
    from personalclaw.config import loader as config_loader
    from personalclaw.dashboard import handlers_system

    loop_thread = threading.get_ident()
    ran: dict[str, list[int]] = {"resolve_config_dir": [], "_serving_root": []}
    async with TestClient(TestServer(gateway.app)) as client:
        monkeypatch.setattr(
            config_loader,
            "resolve_config_dir",
            _recorder(ran["resolve_config_dir"], config_loader.resolve_config_dir),
        )
        monkeypatch.setattr(
            handlers_system,
            "_serving_root",
            _recorder(ran["_serving_root"], handlers_system._serving_root),
        )
        r = await client.get("/api/healthz")
        body = await r.json()

    assert r.status == 200 and body["status"] == "ok", body
    assert body["root_ok"] is True and body["home_id"], f"premise: it still answers whose; {body}"
    assert all(ran.values()), f"premise: each ran; {ran}"
    on_loop = [name for name, threads in ran.items() if loop_thread in threads]
    assert not on_loop, f"the health check read the disk on the event loop's thread: {on_loop}"


@pytest.mark.asyncio
async def test_a_parts_record_is_saved_in_a_worker_thread(gateway, monkeypatch):
    """🔴 Red before: each part's PUT saved the upload's record (``meta.json``) on the event loop:
    a temporary file written, its mode set and renamed over the record, each waiting on the disk
    (0.31 s caught on a busy machine). It is saved in a worker thread."""
    from personalclaw.uploads import store as upload_store

    loop_thread = threading.get_ident()
    saved: list[int] = []
    payload = _bytes(10 * _MB)  # two parts
    async with TestClient(TestServer(gateway.app)) as client:
        info = await _init(
            client, len(payload), target="knowledge", filename="clip.mp4", mime="video/mp4"
        )
        monkeypatch.setattr(
            upload_store, "atomic_json_write", _recorder(saved, upload_store.atomic_json_write)
        )
        await _send_parts(client, info, payload)
        r = await client.get(f"/api/uploads/{info['uploadId']}")
        status = await r.json()

    assert status["received"] == [0, 1], f"premise: both parts were recorded; {status}"
    assert len(saved) == 2, f"premise: one save of the record per part; {saved}"
    assert loop_thread not in saved, "a part's record was saved on the event loop's thread"


class _Body:
    """A part's body, handed out a chunk at a time, as aiohttp's reader hands it."""

    def __init__(self, data: bytes) -> None:
        self._data = memoryview(data)

    async def read_chunk(self, n: int) -> bytes:
        chunk, self._data = bytes(self._data[:n]), self._data[n:]
        return chunk


@pytest.mark.asyncio
async def test_a_drop_while_a_parts_record_is_saved_does_not_bring_the_upload_back(
    tmp_path, monkeypatch
):
    """Saved in a worker thread, the record could be written after a drop had taken the upload's
    folder away, and writing it makes the folder again: the dropped upload came back. The save
    and the drop's rename take one lock, so a drop asked meanwhile waits for the save, then drops
    the upload whole."""
    from personalclaw.uploads import store as upload_store

    store = UploadStore(tmp_path)
    sess = store.init(filename="clip.mp4", size=20 * _MB, mime="video/mp4", target="knowledge")
    real = upload_store.atomic_json_write
    entered, go = threading.Event(), threading.Event()

    def held(path, data):
        entered.set()
        assert go.wait(5), "the test never let the save go on"
        return real(path, data)

    monkeypatch.setattr(upload_store, "atomic_json_write", held)
    writing = asyncio.ensure_future(store.write_part(sess.id, 0, _Body(_bytes(sess.part_size))))
    assert await asyncio.to_thread(entered.wait, 5), "the record's save never began"
    dropping = asyncio.ensure_future(store.drop(sess.id))
    await asyncio.sleep(0.2)  # the drop is asked while the record is being saved
    go.set()
    written, dropped = await asyncio.gather(writing, dropping, return_exceptions=True)

    assert not isinstance(written, BaseException), f"the part was not written: {written!r}"
    assert dropped is True, f"the drop did not drop the upload: {dropped!r}"
    assert not (tmp_path / sess.id).exists(), "the dropped upload was made again"


@pytest.mark.asyncio
async def test_the_librarys_files_folder_is_made_in_a_worker_thread(gateway, monkeypatch):
    """🔴 Red before: an upload asked for the library's files folder on the event loop, which
    resolves the home and makes the folder when it is missing, each waiting on the disk. It is
    asked in a worker thread."""
    import personalclaw.knowledge as knowledge

    loop_thread = threading.get_ident()
    ran: list[int] = []
    monkeypatch.setattr(
        knowledge, "knowledge_files_dir", _recorder(ran, knowledge.knowledge_files_dir)
    )
    payload = _bytes(_MB)
    boundary = "pcuploadboundary"
    async with TestClient(TestServer(gateway.app)) as client:
        r = await client.post(
            "/api/knowledge/ingest",
            data=_multipart("walkthrough.mov", "video/quicktime", payload, boundary),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        body = await r.json()

    assert r.status == 200, body
    assert gateway.queued == [body["item_id"]], "premise: the upload became an item"
    assert ran, "premise: the upload asked for the files folder"
    assert loop_thread not in ran, "the files folder was made on the event loop's thread"


# ── while its complete runs, nothing else touches the upload ────────────────────────────────


class _HeldAssembly:
    """Holds *store*'s assembly of an upload in its worker thread until :meth:`release`."""

    def __init__(self, store: UploadStore) -> None:
        self.entered = threading.Event()
        self._go = threading.Event()
        self._real = store._concatenate

    def __call__(self, sess, final):
        self.entered.set()
        assert self._go.wait(30), "the test never let the assembly go on"
        return self._real(sess, final)

    def release(self) -> None:
        self._go.set()


@pytest.mark.asyncio
async def test_a_running_complete_is_left_alone_and_still_makes_its_item(gateway):
    """The complete's work runs in threads, so other requests are answered meanwhile. A retried
    complete, a part sent again and a drop (the page sends one when the complete's request
    fails) are each refused with 409 while it runs, and the complete makes its one item."""
    payload = _bytes(20 * _MB)
    store = gateway.app["upload_store"]
    held = _HeldAssembly(store)
    with patch.object(store, "_concatenate", held):
        async with TestClient(TestServer(gateway.app)) as client:
            info = await _init(
                client, len(payload), target="knowledge", filename="clip.mp4", mime="video/mp4"
            )
            uid = info["uploadId"]
            await _send_parts(client, info, payload)
            first = asyncio.ensure_future(_complete(client, uid))
            assert await asyncio.to_thread(held.entered.wait, 30), "the complete never began"

            again = await client.post(f"/api/uploads/{uid}/complete", json={})
            part = await client.put(
                f"/api/uploads/{uid}/part?index=0",
                data=payload[: info["partSize"]],
                headers={"Content-Type": "application/octet-stream"},
            )
            drop = await client.delete(f"/api/uploads/{uid}")
            refusals = [
                (again.status, await again.json()),
                (part.status, await part.json()),
                (drop.status, await drop.json()),
            ]
            swept = store.sweep(ttl_secs=-1)

            held.release()
            status, body = await first
            after = await client.delete(f"/api/uploads/{uid}")

    assert refusals == [
        (409, {"error": "upload is already being completed"}),
        (409, {"error": "upload is being completed"}),
        (
            409,
            {"error": {"code": "upload_completing", "message": refusals[2][1]["error"]["message"]}},
        ),
    ]
    assert "can no longer be cancelled" in refusals[2][1]["error"]["message"]
    assert swept == 0, "the sweep passed over the upload being completed"
    assert status == 200, body
    assert _items(gateway.store) == 1 and gateway.queued == [body["item_id"]]
    assert gateway.store.get_item(body["item_id"])["file_size"] == len(payload)
    assert after.status == 404, "once completed, there is nothing left to drop"


@pytest.mark.asyncio
async def test_an_upload_whose_content_cannot_be_checked_is_not_used(gateway):
    """A content scan that never ran is not a pass: when its child cannot start, the upload is
    refused (503), nothing is made from it, and its parts go. The same upload is kept when the
    child runs: the vacuity arm."""
    payload = _json_lines(_MB)
    async with TestClient(TestServer(gateway.app)) as client:

        async def upload() -> tuple[int, dict]:
            info = await _init(
                client,
                len(payload),
                target="attachment",
                filename="feed-export.jsonl",
                mime="application/x-ndjson",
            )
            await _send_parts(client, info, payload)
            return await _complete(client, info["uploadId"])

        with patch(
            "personalclaw.uploads.content_scan.scan_argv",
            return_value=["/nonexistent/personalclaw-content-scan"],
        ):
            refused, said = await upload()
        kept, body = await upload()

    assert refused == 503, said
    assert said["error"]["code"] == "upload_content_unchecked"
    assert "could not be checked" in said["error"]["message"]
    assert list(gateway.parts.iterdir()) == [], "its parts went"
    assert kept == 200, body
    assert [p.name for p in gateway.uploads.iterdir() if p.is_file()] == [
        os.path.basename(body["paths"][0])
    ], "only the checked upload was kept"
