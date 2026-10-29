"""``tools/ci/hung.py``: where each thread of a hung worker waits, read from outside it.

The reader runs on a planted ``/proc`` everywhere, and on a real child on Linux: one thread in a
read on a pipe, one waiting on a mutex with the GIL held, so no Python thread of it can say a
thing.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

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
#: that mutex through ``ctypes.PyDLL``, whose calls keep the GIL. A third thread prints a tick
#: every 20 ms, which stops once the main thread waits. It prints the reading thread's id, and
#: waits for a line on its stdin before it waits on the mutex.
_TWO_WAITS = textwrap.dedent("""
    import ctypes, os, sys, threading, time

    libc = ctypes.CDLL(None)
    mutex = ctypes.create_string_buffer(128)
    assert libc.pthread_mutex_init(mutex, None) == 0
    r, w = os.pipe()


    def reads():
        libc.pthread_mutex_lock(mutex)
        print(threading.get_native_id(), flush=True)
        os.read(r, 1)


    def ticks():
        while True:
            print("tick", flush=True)
            time.sleep(0.02)


    threading.Thread(target=reads, daemon=True).start()
    sys.stdin.readline()
    threading.Thread(target=ticks, daemon=True).start()
    time.sleep(0.2)
    print("holding", flush=True)
    ctypes.PyDLL(None).pthread_mutex_lock(mutex)
    """)


def _asleep_in_a_system_call(pid: int, tid: int) -> None:
    """Until the kernel says thread *tid* sleeps in a system call, twice in a row."""
    task = Path(f"/proc/{pid}/task/{tid}")
    steady = 0
    ends = time.monotonic() + 10
    while steady < 2:
        assert time.monotonic() < ends, "premise: the reading thread never slept in its read"
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
        reader = int(said.get(timeout=10))
        _asleep_in_a_system_call(child.pid, reader)
        child.stdin.write("\n")
        child.stdin.flush()
        before = []
        while (line := said.get(timeout=10)) != "holding":
            before.append(line)
        assert "tick" in before, "premise: a Python thread ran before the main thread waited"
        time.sleep(0.5)
        while not said.empty():
            said.get()
        time.sleep(1.0)
        assert said.empty(), "premise: the main thread holds the GIL as it waits, so none ticks"
        capture = hung.threads(child.pid)
    finally:
        child.kill()
        child.wait(10)
    thread = _lines(capture)
    assert "in futex" in thread[child.pid], capture
    assert "in read" in thread[reader], capture
