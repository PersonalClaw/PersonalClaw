"""``tools/ci/hung.py``: where each thread of a hung worker waits, read from outside it, and the
worker past its test's own deadline ended there.

The reader runs on a planted ``/proc`` everywhere, and on a real child on Linux: one thread in a
read on a pipe, one waiting on a mutex with the GIL held, so no Python thread of it can say a
thing. The watch runs on planted published lines and real children in groups of their own.
"""

from __future__ import annotations

import io
import os
import queue
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import hard_deadline
import pytest

from tools.ci import hung


def _stat(tid: int, name: str = "python3", state: str = "S", cpu: tuple[int, int] = (50, 2)) -> str:
    """A ``stat`` line: id, name, state, parent, then the fields up to user and system time."""
    return f"{tid} ({name}) {state} 1 {tid} {tid} 0 -1 4194560 100 0 0 0 {cpu[0]} {cpu[1]} 0 0 20\n"


def _proc(tmp_path: Path, pid: int, threads: dict[int, dict[str, str | None]]) -> Path:
    """A planted ``/proc`` holding process *pid* and its *threads*, each a file's content by its
    name, or None for a file that can't be read (a folder where the file would be)."""
    proc = tmp_path / "proc"
    base = proc / str(pid)
    (base / "task").mkdir(parents=True)
    (base / "stat").write_text(_stat(pid, "python3 (a worker)"), encoding="utf-8")
    for tid, files in threads.items():
        task = base / "task" / str(tid)
        task.mkdir()
        for name, content in files.items():
            if content is None:
                (task / name).mkdir()
            else:
                (task / name).write_text(content, encoding="utf-8")
    return proc


def _lines(capture: str) -> dict[int, str]:
    """Each thread's first line, by its id."""
    return {
        int(line.split()[1]): line for line in capture.splitlines() if line.startswith("  thread ")
    }


def test_each_thread_is_named_by_its_state_cpu_time_system_call_and_kernel_wait(tmp_path):
    proc = _proc(
        tmp_path,
        100,
        {
            100: {
                "stat": _stat(100),
                "syscall": "202 0x7f00 0x80 0x2 0x0 0x0 0x0 0x7ffd10 0x7f2a\n",
                "wchan": "futex_wait_queue",
                "stack": None,
            },
            101: {
                "stat": _stat(101, state="D", cpu=(0, 7)),
                "syscall": "0 0x3 0x7f10 0x1 0x0 0x0 0x0 0x7f68021fc6c0 0x7f2b\n",
                "wchan": "pipe_read",
                "stack": "[<0>] pipe_read+0x2f2/0x480\n[<0>] vfs_read+0x2e1/0x330\n",
            },
            102: {"stat": _stat(102, state="R"), "syscall": "running\n", "wchan": "0"},
            103: {"stat": _stat(103), "syscall": "-1 0x7ffd 0x7f2c\n", "wchan": "0"},
        },
    )
    capture = hung.threads(100, proc=proc, machine="x86_64")
    tick = os.sysconf("SC_CLK_TCK")
    assert capture.startswith("process 100 (python3 (a worker)), parent 1, 4 thread(s):\n")
    thread = _lines(capture)
    assert thread[100] == (
        f"  thread 100 (python3) asleep, {52 / tick:.2f} s of CPU, in futex,"
        " waiting in futex_wait_queue"
    )
    assert "asleep, uninterruptibly" in thread[101]
    assert "in read, waiting in pipe_read" in thread[101]
    assert thread[102].endswith(", running")
    assert thread[103].endswith(", in user code")
    # The kernel's line as it is, its stack pointer last but one; the kernel stack where readable.
    assert "    syscall: 0 0x3 0x7f10 0x1 0x0 0x0 0x0 0x7f68021fc6c0 0x7f2b\n" in capture
    assert "    kernel stack: (unreadable: IsADirectoryError)" in capture
    assert "      [<0>] pipe_read+0x2f2/0x480\n      [<0>] vfs_read+0x2e1/0x330\n" in capture


@pytest.mark.parametrize(
    "machine, number, named",
    [
        ("x86_64", "202", "in futex"),
        ("x86_64", "0", "in read"),
        ("x86_64", "74", "in fsync"),
        ("aarch64", "98", "in futex"),
        ("aarch64", "63", "in read"),
        ("aarch64", "82", "in fsync"),
        ("x86_64", "9999", "in system call 9999"),
        ("riscv64", "63", "in system call 63"),
    ],
)
def test_a_system_call_is_named_by_its_machines_own_number(tmp_path, machine, number, named):
    proc = _proc(tmp_path, 7, {7: {"stat": _stat(7), "syscall": f"{number} 0x1 0x2\n"}})
    assert f", {named}," in _lines(hung.threads(7, proc=proc, machine=machine))[7]


def test_what_cannot_be_read_is_named_never_raised(tmp_path):
    proc = _proc(tmp_path, 9, {9: {}})
    assert _lines(hung.threads(9, proc=proc, machine="x86_64"))[9] == (
        "  thread 9 its state (unreadable: FileNotFoundError), its system call (unreadable:"
        " FileNotFoundError), its kernel wait (unreadable: FileNotFoundError)"
    )
    gone = hung.threads(4242, proc=proc, machine="x86_64")
    assert gone == (
        "process 4242: (unreadable: FileNotFoundError): its threads could not be listed"
        " (FileNotFoundError)\n"
    )


#: A kernel stack longer than a read takes of a file.
_FRAME = "[<0>] a_kernel_function+0x1/0x2\n"
_DEEP = _FRAME * 200


def test_a_read_is_bounded_in_threads_bytes_and_characters(tmp_path):
    proc = _proc(tmp_path, 5, {t: {"stat": _stat(t), "stack": _DEEP} for t in range(5, 5 + 130)})
    capture = hung.threads(5, proc=proc, machine="x86_64")
    assert len(capture) <= hung.PROCESS_LIMIT and capture.endswith(hung.CUT)
    first = capture.split("  thread 6 ")[0]
    assert first.count("a_kernel_function") <= 4096 // len(_FRAME) + 1
    short = {"syscall": "running\n", "wchan": "0", "stack": ""}
    few = _proc(tmp_path / "few", 5, {t: {"stat": _stat(t), **short} for t in range(5, 5 + 130)})
    assert hung.threads(5, proc=few, machine="x86_64").endswith(
        "  ... and 2 more thread(s), not read\n"
    )


def test_a_group_is_bounded_in_processes_and_characters(tmp_path):
    proc = _proc(tmp_path, 1, {t: {"stat": _stat(t), "stack": _DEEP} for t in range(1, 1 + 128)})
    capture = hung.group(range(1000, 1020), proc=proc, machine="x86_64")
    assert capture.count("process ") == 16, "the first 16 are read, and the rest named"
    assert capture.endswith("... and 4 more process(es), not read\n")
    whole = hung.group([1] * 16, proc=proc, machine="x86_64")
    assert len(whole) <= hung.GROUP_LIMIT and whole.endswith(hung.CUT)


def test_without_proc_it_says_so(tmp_path):
    said = hung.threads(12, proc=tmp_path / "no-proc")
    no = tmp_path / "no-proc"
    assert said == f"process 12: no {no} on {sys.platform}, so its threads are not read\n"


#: A child with one thread in a read on a pipe, holding a mutex, and its main thread waiting on
#: that mutex through ``ctypes.PyDLL``. It first prints whether a call through ``PyDLL``, and one
#: through ``CDLL``, keeps the GIL: ctypes releases it around a call unless the function carries
#: ``_FUNCFLAG_PYTHONAPI``, as ``PyDLL``'s do. Then it prints the reading thread's id, and waits for
#: a line on its stdin before it says it is holding and waits on the mutex.
_TWO_WAITS = textwrap.dedent("""
    import ctypes, os, sys, threading

    libc = ctypes.CDLL(None)
    held = ctypes.PyDLL(None).pthread_mutex_lock
    keeps = [bool(f._flags_ & ctypes._FUNCFLAG_PYTHONAPI) for f in (held, libc.pthread_mutex_lock)]
    print(*keeps, flush=True)
    mutex = ctypes.create_string_buffer(128)
    assert libc.pthread_mutex_init(mutex, None) == 0
    r, w = os.pipe()


    def reads():
        libc.pthread_mutex_lock(mutex)
        print(threading.get_native_id(), flush=True)
        os.read(r, 1)


    threading.Thread(target=reads, daemon=True).start()
    sys.stdin.readline()
    print("holding", flush=True)
    held(mutex)
    """)


def _asleep_in_a_system_call(pid: int, tid: int) -> None:
    """Until the kernel says thread *tid* sleeps in a system call, twice in a row."""
    task = Path(f"/proc/{pid}/task/{tid}")
    steady = 0
    ends = time.monotonic() + 10
    while steady < 2:
        assert time.monotonic() < ends, f"premise: thread {tid} never slept in a system call"
        state = (task / "stat").read_text().rpartition(")")[2].split()[0]
        call = (task / "syscall").read_text().split()[0]
        steady = steady + 1 if state == "S" and call.isdigit() else 0
        time.sleep(0.05)


@pytest.mark.skipif(sys.platform != "linux", reason="/proc is Linux's")
def test_a_thread_in_a_read_and_one_waiting_on_a_mutex_with_the_gil_are_both_named():
    """The coordinator's plant for the Linux shards' hang: a worker no Python thread of its own
    can end, here a child of the test's, read from outside it as run-job reads a step's group."""
    scope = Path("/proc/sys/kernel/yama/ptrace_scope")
    if scope.exists() and int(scope.read_text()) > 1:
        pytest.skip("this kernel lets no process trace even its own children")
    child = subprocess.Popen(
        [sys.executable, "-c", _TWO_WAITS],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    said: queue.Queue[str] = queue.Queue()

    def listen() -> None:
        for line in child.stdout:
            said.put(line.strip())

    threading.Thread(target=listen, daemon=True).start()
    try:
        keeps = said.get(timeout=10)
        assert keeps == "True False", f"premise: PyDLL's call keeps the GIL, CDLL's not: {keeps}"
        reader = int(said.get(timeout=10))
        _asleep_in_a_system_call(child.pid, reader)
        child.stdin.write("\n")
        child.stdin.flush()
        assert said.get(timeout=10) == "holding"
        _asleep_in_a_system_call(child.pid, child.pid)
        capture = hung.threads(child.pid)
    finally:
        child.kill()
        child.wait(10)
    thread = _lines(capture)
    assert "in futex" in thread[child.pid], capture
    assert "in read" in thread[reader], capture


# ── the watch ────────────────────────────────────────────────────────────────────────────────


def _sleeper() -> subprocess.Popen:
    """A child in a group of its own, sleeping."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )


def _publish(folder: Path, pid: int, *, began: float, deadline: float, worker: str = "gw0") -> Path:
    path = folder / f"hard-deadline-leg-{worker}{hung.ARMED}"
    path.write_text(f"{pid}\t{began:.3f}\t{deadline:.3f}\ttest_x.py::test_hangs\n", "utf-8")
    return path


def test_a_worker_past_its_tests_deadline_is_read_written_and_ended(tmp_path):
    child = _sleeper()
    try:
        log = io.StringIO()
        # Made with no start: its file is written a moment after, and a file system's coarser clock
        # can stamp it before the watch's start, which the earlier-step test is about.
        watch = hung.Overdue(
            tmp_path, "leg", read=lambda pid: f"the threads of {pid}\n", log=log, since=0
        )
        now = time.monotonic()
        armed = _publish(tmp_path, child.pid, began=now - 125, deadline=now - 35)
        watch(child.pid)
        assert child.poll() is None, "one look is never enough: the line may be half written"
        watch(child.pid)
        assert child.wait(10) == -signal.SIGKILL
    finally:
        child.kill()
        child.wait(10)
    dump = (tmp_path / "hard-deadline-leg-gw0.txt").read_text(encoding="utf-8")
    assert dump.startswith(
        "hard deadline, from outside: test_x.py::test_hangs was still running 125."
    ), dump
    assert "past its deadline" in dump and f"(pid {child.pid})" in dump
    assert dump.endswith(f"the threads of {child.pid}\n")
    assert log.getvalue() == dump + "\n"
    assert not armed.exists(), "a worker it ended is never looked at again"


def _ended(tmp_path: Path, pgid: int) -> list[tuple[int, int]]:
    """What the watch signals over two looks, with nothing really signalled. Every file here is
    taken for its step's own, whenever it was written."""
    ended: list[tuple[int, int]] = []

    def end(pid: int, sig: int) -> None:
        ended.append((pid, sig))

    watch = hung.Overdue(tmp_path, "leg", end=end, read=str, log=io.StringIO(), since=0)
    for _ in range(2):
        watch(pgid)
    return ended


def test_a_test_short_of_its_deadline_and_the_grace_after_it_is_left_alone(tmp_path):
    child = _sleeper()
    try:
        now = time.monotonic()
        _publish(tmp_path, child.pid, began=now - 100, deadline=now - hung.PAST + 5)
        assert _ended(tmp_path, child.pid) == []
        _publish(tmp_path, child.pid, began=now - 100, deadline=now - hung.PAST - 5)
        assert _ended(tmp_path, child.pid) == [(child.pid, signal.SIGKILL)]
    finally:
        child.kill()
        child.wait(10)


def test_a_line_is_acted_on_only_once_two_looks_read_it_the_same(tmp_path):
    child = _sleeper()
    try:
        now = time.monotonic()
        ended: list[int] = []
        watch = hung.Overdue(
            tmp_path,
            "leg",
            end=lambda pid, sig: ended.append(pid),
            read=str,
            log=io.StringIO(),
            since=0,  # as above: no start for a file written a moment after it
        )
        for n in range(3):  # a new test each look: never the same line twice
            _publish(tmp_path, child.pid, began=now - 200 + n, deadline=now - 100)
            watch(child.pid)
        assert ended == []
        watch(child.pid)
        assert ended == [child.pid]
    finally:
        child.kill()
        child.wait(10)


def test_a_line_an_earlier_step_left_is_never_acted_on_though_its_pid_is_in_this_group(tmp_path):
    """A worker of an earlier step that died mid-test left its line, and the pid it names is a
    live process of this step's group by now. The watch, made as this step began, leaves it alone.
    The same line, written by this step's worker, is acted on."""
    child = _sleeper()
    try:
        now = time.monotonic()
        armed = _publish(tmp_path, child.pid, began=now - 200, deadline=now - 100)
        ended: list[int] = []
        watch = hung.Overdue(
            tmp_path, "leg", end=lambda pid, sig: ended.append(pid), read=str, log=io.StringIO()
        )
        for _ in range(3):
            watch(child.pid)
        assert ended == []
        time.sleep(0.05)  # past the watch's start by the file system's coarser clock too
        armed.write_text(armed.read_text(encoding="utf-8"), encoding="utf-8")
        for _ in range(2):
            watch(child.pid)
        assert ended == [child.pid]
    finally:
        child.kill()
        child.wait(10)


def test_a_published_pid_outside_the_steps_group_is_never_signalled(tmp_path):
    step, other = _sleeper(), _sleeper()
    try:
        now = time.monotonic()
        for n, pid in enumerate((other.pid, os.getpid(), 1)):
            _publish(tmp_path, pid, began=now - 200, deadline=now - 100, worker=f"gw{n}")
        assert _ended(tmp_path, step.pid) == []
    finally:
        for child in (step, other):
            child.kill()
            child.wait(10)


@pytest.mark.parametrize(
    "line",
    ["", "123\t1\t2\ttest_x.py::t", "abc\t1\t2\ttest_x.py::t\n", "1\t2\t3\t\n", "1\t2\n", "\n"],
    ids=["empty", "no newline", "no pid", "no test", "too few fields", "a newline alone"],
)
def test_a_line_that_is_not_whole_is_never_acted_on(tmp_path, line):
    assert hung.parse(line) is None
    (tmp_path / f"hard-deadline-leg-gw0{hung.ARMED}").write_text(line, encoding="utf-8")
    assert _ended(tmp_path, os.getpgid(0)) == []


def test_what_the_hard_deadline_publishes_is_what_the_watch_reads(tmp_path):
    """The two sides of one file, which ``tools/ci`` may not import from ``tests``: where the
    watchdog publishes its test and writes its dump for a report, the watch for that report
    finds them, and it reads the line the watchdog wrote."""
    junit = tmp_path / "reports" / "junit-test-shard-2.xml"
    config = SimpleNamespace(option=SimpleNamespace(xmlpath=str(junit)), rootpath=tmp_path)
    dump = hard_deadline.dump_path(config, "gw1")
    watchdog = hard_deadline.Watchdog(dump, end=lambda status: None)
    watchdog.arm("tests/test_x.py::test_y[a\\tb]", 30.0, 30.0)
    armed = hard_deadline.armed_path(dump)
    try:
        watch = hung.Overdue.beside(junit)
        assert sorted(watch.folder.glob(watch.pattern)) == [armed]
        assert armed.with_suffix(".txt") == dump
        pid, began, deadline, nodeid = hung.parse(armed.read_text(encoding="utf-8"))
        assert (pid, nodeid) == (os.getpid(), "tests/test_x.py::test_y[a\\tb]")
        assert deadline - began == pytest.approx(90.0, abs=0.002)
        assert abs(began - time.monotonic()) < 60, "both read the system's monotonic clock"
    finally:
        watchdog.disarm()
    assert armed.read_text(encoding="utf-8") == "", "between tests, nothing is published"
