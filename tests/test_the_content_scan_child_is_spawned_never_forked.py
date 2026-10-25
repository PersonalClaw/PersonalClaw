"""The content scan's child is spawned, never forked from the gateway on its event loop.

``asyncio`` starts a child with ``subprocess.Popen`` on the event loop, and Popen forks the whole
gateway unless it can use ``posix_spawn``: on macOS only with ``close_fds=False`` and no new
session. The fork holds the interpreter lock while the kernel copies the gateway's memory map, so
it grows with the gateway. The large-upload scan's probe failed on it: a loop step of 0.127 s on
a loaded CI runner (run 40231076989, #5570). Beside a 1 ms tick, in a 1.2 GB process on a loaded
Mac, the fork held the loop 32-90 ms, from the loop or from a worker thread alike; the spawn 3 ms.

So the gateway asks Popen for neither. It names the descriptors the child inherits, and the
child's first step takes a session of its own and closes exactly those (``uploads.content_scan``,
"How it starts"; ``uploads.scan_child.first_step``). These tests hold the start to a spawn, the
child to its session and its standard streams before it reads any input, a timeout's kill to the
child's whole group (and to the child alone before its first step), and its argv, environment and
limits to what they were. Each plant starts the child the old way, or leaves a step out.
"""

from __future__ import annotations

import asyncio
import json
import os
import select
import subprocess
import sys
import textwrap
import time

import pytest

from personalclaw.sandbox import build_child_env
from personalclaw.uploads import content_scan, scan_child

_CLEAN = b"Shopping list for Saturday: eggs, flour, apples.\n"

pytestmark = pytest.mark.skipif(
    not getattr(subprocess, "_USE_POSIX_SPAWN", False),
    reason="this platform's subprocess never uses posix_spawn, so every child is forked",
)

#: A stand-in for the scan's child that takes the real child's first step, then answers as it
#: does, with what it holds: its argv, environment, limits, folder, session and descriptors, all
#: read from inside it. Planted with ``FIRST_STEP = False``, it takes no first step.
_REPORTER = textwrap.dedent("""
    import json, os, resource, sys
    from personalclaw.uploads.scan_child import first_step
    argv = list(sys.orig_argv)
    if FIRST_STEP:
        first_step(sys.argv[1:])
    folder = "/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"
    fds = []
    for name in os.listdir(folder):
        try:
            os.fstat(int(name))
        except OSError:
            continue  # the listing's own descriptor, closed by now
        fds.append(int(name))
    umask = os.umask(0)
    print(json.dumps({
        "dangerous": False,
        "argv": argv,
        "env": dict(os.environ),
        "limits": {
            name: resource.getrlimit(getattr(resource, name))
            for name in dir(resource) if name.startswith("RLIMIT_")
        },
        "cwd": os.getcwd(),
        "umask": umask,
        "leads_its_session": os.getsid(0) == os.getpid(),
        "leads_its_group": os.getpgrp() == os.getpid(),
        "fds": sorted(fds),
    }))
    """)


def _reporting(monkeypatch, *, first_step: bool = True) -> None:
    """The scan's child, from now on, is :data:`_REPORTER`."""
    code = f"FIRST_STEP = {first_step}\n{_REPORTER}"
    monkeypatch.setattr(content_scan, "scan_argv", lambda: [sys.executable, "-P", "-c", code])


def _the_old_way(monkeypatch) -> None:
    """The start as it was: Popen asked for a session, and closing every descriptor itself."""
    real = asyncio.create_subprocess_exec

    async def start(*_argv, **kwargs):
        kwargs.pop("close_fds", None)
        return await real(*content_scan.scan_argv(), start_new_session=True, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)


def _record_starts(monkeypatch) -> list[str]:
    """How each scan child is started from now on: ``posix_spawn``, or ``fork``."""
    starts: list[str] = []
    spawn, fork = subprocess.Popen._posix_spawn, subprocess._fork_exec

    def posix_spawn(self, args, *rest):
        if content_scan.CHILD_MODULE in args:
            starts.append("posix_spawn")
        return spawn(self, args, *rest)

    def fork_exec(args, *rest):
        if any(content_scan.CHILD_MODULE in os.fsdecode(word) for word in args):
            starts.append("fork")
        return fork(args, *rest)

    monkeypatch.setattr(subprocess.Popen, "_posix_spawn", posix_spawn)
    monkeypatch.setattr(subprocess, "_fork_exec", fork_exec)
    return starts


@pytest.mark.asyncio
async def test_the_scan_child_is_spawned(monkeypatch):
    starts = _record_starts(monkeypatch)

    answer = await content_scan._ask_child(_CLEAN)

    assert answer == {"dangerous": False}, "premise: the child scanned the window"
    assert starts == ["posix_spawn"], (
        "the scan's child was forked from the gateway: a fork holds the event loop for as long as "
        "the kernel takes to copy the gateway's memory map. Ask Popen for no session and no close "
        f"of descriptors (content_scan, 'How it starts'). Started by: {starts}"
    )


_PLANTS = [
    pytest.param({"start_new_session": True}, id="a-session-asked-for"),
    pytest.param(
        {"close_fds": True},
        id="descriptors-closed-by-popen",
        marks=pytest.mark.skipif(
            getattr(subprocess, "_HAVE_POSIX_SPAWN_CLOSEFROM", False),
            reason="this platform's posix_spawn closes descriptors itself",
        ),
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("planted", _PLANTS)
async def test_a_scan_child_started_the_old_way_is_seen_forked(monkeypatch, planted):
    """The planted start asks Popen for what the gateway used to: a session, or its own close of
    every descriptor. Each one makes Popen fork, and the check sees it."""
    starts = _record_starts(monkeypatch)
    real = asyncio.create_subprocess_exec

    async def the_old_way(*argv, **kwargs):
        return await real(*argv, **{**kwargs, **planted})

    monkeypatch.setattr(asyncio, "create_subprocess_exec", the_old_way)

    answer = await content_scan._ask_child(_CLEAN)

    assert answer == {"dangerous": False}, "premise: the planted child scanned the window"
    assert starts == ["fork"], starts


@pytest.fixture()
def an_inherited_descriptor():
    """The write end of a pipe, made inheritable as a C library's descriptor can be (Python's own
    never are, PEP 446), and its read end."""
    read, write = os.pipe()
    os.set_inheritable(write, True)
    yield read, write
    for fd in (read, write):
        try:
            os.close(fd)
        except OSError:
            pass


def _closed_by(fd: int, until: float) -> bool:
    """Whether every writer of the pipe or FIFO read at *fd* has closed it by *until*."""
    while time.monotonic() < until:
        ready, _, _ = select.select([fd], [], [], max(0.0, until - time.monotonic()))
        if ready and not os.read(fd, 64):
            return True
    return False


def _the_real_child(argv: list[str], home) -> subprocess.Popen:
    """The scan's child, started as ``_ask_child`` starts it, with its stdin left open."""
    return subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=build_child_env(site="upload content scan", extra={"PERSONALCLAW_HOME": str(home)}),
        close_fds=False,
    )


@pytest.mark.parametrize("named", [True, False], ids=["named", "planted-unnamed"])
def test_the_scan_child_closes_what_it_inherited_and_takes_a_session_before_any_input(
    tmp_path, an_inherited_descriptor, named
):
    """The real child, given nothing on stdin yet: the pipe's only other writer is its inherited
    copy, so the pipe closes when the child's first step closes that. Planted, the gateway names
    no descriptor, and the child keeps it."""
    read, write = an_inherited_descriptor
    assert write in content_scan.inherited_fds(), "premise: the gateway sees what it hands on"
    argv = content_scan.start_argv() if named else content_scan.scan_argv()
    child = _the_real_child(argv, tmp_path)
    os.close(write)
    try:
        closed = _closed_by(read, time.monotonic() + (20.0 if named else 2.0))
        if not named:
            assert not closed, "premise: an unnamed inherited descriptor stays open in the child"
            return
        assert closed, "the scan's child kept a descriptor it inherited past its first step"
        assert os.getsid(child.pid) == child.pid, "the scan's child took no session of its own"
        assert os.getpgid(child.pid) == child.pid, "the scan's child leads no group of its own"
        out, err = child.communicate(_CLEAN, timeout=20)
        assert out.strip() == b'{"dangerous": false}', err[-2000:]
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate()


@pytest.mark.asyncio
@pytest.mark.parametrize("first_step", [True, False], ids=["after-its-first-step", "planted"])
async def test_inside_the_scan_child_only_its_standard_streams_are_open(
    monkeypatch, an_inherited_descriptor, first_step
):
    _read, write = an_inherited_descriptor
    _reporting(monkeypatch, first_step=first_step)

    seen = await content_scan._ask_child(_CLEAN)

    assert seen is not None, "premise: the stand-in answered"
    if not first_step:
        assert write in seen["fds"], f"premise: the listing sees the inherited one: {seen['fds']}"
        assert not seen["leads_its_session"], "premise: without the step, the gateway's session"
        return
    assert seen["fds"] == [0, 1, 2], (
        f"after its first step the scan's child holds descriptors of the gateway's: {seen['fds']} "
        f"(the gateway's inheritable one is {write})"
    )
    assert seen["leads_its_session"] and seen["leads_its_group"], seen


@pytest.mark.asyncio
@pytest.mark.parametrize("planted", [False, True], ids=["as-it-is", "one-inherited-planted"])
async def test_the_scan_child_gets_the_argv_environment_and_limits_it_had(
    monkeypatch, request, planted
):
    """Started the gateway's way and the old way, the stand-in reports the same bytes of
    environment, the same limits, folder, umask and descriptors, and in both it leads its own
    session. Its argv is the old one and the descriptors it was named to close: byte for byte the
    old one when the gateway holds none to hand on, as under PEP 446 it holds none of Python's."""
    if planted:
        _read, write = request.getfixturevalue("an_inherited_descriptor")
    _reporting(monkeypatch)
    inherited = content_scan.inherited_fds()
    assert not planted or write in inherited, "premise: the planted descriptor is named"
    now = await content_scan._ask_child(_CLEAN)
    _the_old_way(monkeypatch)
    before = await content_scan._ask_child(_CLEAN)

    assert now is not None and before is not None, "premise: both answered"
    assert now["argv"] == before["argv"] + scan_child.close_args(inherited), (
        f"argv: the gateway's way {now['argv']!r}, the old way {before['argv']!r}, "
        f"the descriptors to close {inherited}"
    )
    for what in ("env", "limits", "cwd", "umask", "leads_its_session", "fds"):
        assert (
            now[what] == before[what]
        ), f"{what}: the gateway's way {now[what]!r}, the old way {before[what]!r}"


#: A stand-in for the scan's child that waits before its first step, and writes its pid first.
_SLOW_TO_STEP = textwrap.dedent("""
    import os, sys, time
    with open(sys.argv[1], "w") as pidfile:
        pidfile.write(str(os.getpid()))
    time.sleep(60)
    from personalclaw.uploads.scan_child import first_step
    first_step(sys.argv[2:])
    """)


@pytest.mark.asyncio
async def test_a_scan_past_its_deadline_before_the_childs_first_step_kills_the_child(
    monkeypatch, tmp_path
):
    """The child leads no group yet, so the kill falls back to its pid, and it ends."""
    pidfile = tmp_path / "pid"
    argv = [sys.executable, "-P", "-c", _SLOW_TO_STEP, str(pidfile)]
    monkeypatch.setattr(content_scan, "scan_argv", lambda: list(argv))
    monkeypatch.setattr(content_scan, "SCAN_DEADLINE_SECS", 3.0)

    answer = await content_scan._ask_child(_CLEAN)

    assert answer is None, "premise: the scan ran past its deadline"
    assert pidfile.exists(), "premise: the child started before the deadline"
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.05)
    os.kill(pid, 9)
    pytest.fail("a scan killed before its child's first step left the child running")


def _first_line(fd: int, until: float) -> bytes:
    """The first line written on the FIFO by *until*, or what of it came before."""
    got = b""
    while time.monotonic() < until and not got.endswith(b"\n"):
        ready, _, _ = select.select([fd], [], [], max(0.0, until - time.monotonic()))
        if not ready:
            break
        chunk = os.read(fd, 64)
        if not chunk:
            break
        got += chunk
    return got


#: A stand-in for the scan's child that takes its first step (planted: none), starts a grandchild
#: and never answers. The grandchild holds the FIFO open for writing, and writes its pid on it,
#: until it ends.
_A_CHILD_WITH_A_GRANDCHILD = textwrap.dedent("""
    import subprocess, sys, time
    if FIRST_STEP:
        from personalclaw.uploads.scan_child import first_step
        first_step(sys.argv[2:])
    grandchild = (
        "import os, sys, time\\n"
        "fd = os.open(sys.argv[1], os.O_WRONLY)\\n"
        "os.write(fd, str(os.getpid()).encode() + b'\\\\n')\\n"
        "time.sleep(120)\\n"
    )
    subprocess.Popen(
        [sys.executable, "-c", grandchild, sys.argv[1]],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sys.stdin.buffer.read()
    time.sleep(120)
    """)


@pytest.mark.asyncio
@pytest.mark.parametrize("first_step", [True, False], ids=["its-own-session", "planted-without"])
async def test_a_scan_past_its_deadline_ends_the_childs_grandchildren(
    monkeypatch, tmp_path, first_step
):
    fifo = tmp_path / "grandchild"
    os.mkfifo(fifo)
    code = f"FIRST_STEP = {first_step}\n{_A_CHILD_WITH_A_GRANDCHILD}"
    argv = [sys.executable, "-P", "-c", code, str(fifo)]
    monkeypatch.setattr(content_scan, "scan_argv", lambda: list(argv))
    monkeypatch.setattr(content_scan, "SCAN_DEADLINE_SECS", 5.0)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    line = b""
    try:
        scan = asyncio.create_task(content_scan._ask_child(_CLEAN))
        line = await asyncio.to_thread(_first_line, reader, time.monotonic() + 5.0)
        answer = await scan
        assert line.endswith(b"\n"), "premise: the grandchild started before the deadline"
        assert answer is None, "premise: the scan ran past its deadline and was killed"
        # Its own session: the kill's end within 10 s. Planted without one: still open at 2 s.
        waited = 10.0 if first_step else 2.0
        ended = await asyncio.to_thread(_closed_by, reader, time.monotonic() + waited)
        if first_step:
            assert ended, "the scan's kill left its child's grandchild running"
        else:
            assert not ended, "premise: a kill of the child alone leaves its grandchild running"
    finally:
        if line.endswith(b"\n"):
            try:
                os.kill(int(line), 9)
            except ProcessLookupError:
                pass
        os.close(reader)


#: The most CPU time of the loop thread one start of the scan's child may take, in a gateway of
#: :data:`_RESIDENT_MB`. Spawned it took 0.5-1.3 ms on a loaded Mac; forked, 20-36 ms: a 3x margin
#: and more, both ways.
_START_CPU_BOUND = 0.005
#: The resident size of the planted gateway: one a macOS runner holds beside its test workers.
_RESIDENT_MB = 512

#: The planted gateway: a process holding :data:`_RESIDENT_MB` of written memory that starts the
#: scan's child five times, the gateway's way or the old way, and reports the loop thread's CPU
#: time over each start.
_LARGE_GATEWAY = textwrap.dedent("""
    import asyncio, json, sys, time
    from personalclaw.uploads import content_scan

    held = [bytearray(1 << 20) for _ in range(int(sys.argv[1]))]

    def touch():
        # Every page written, before each start: one the kernel compressed or paged out since
        # is not in the map a fork copies.
        for block in held:
            block[::4096] = b"x" * len(block[::4096])

    old_way = sys.argv[2] == "old"
    real, starts = asyncio.create_subprocess_exec, []

    async def timed(*argv, **kwargs):
        if old_way:
            argv, kwargs = content_scan.scan_argv(), {**kwargs, "start_new_session": True}
            kwargs.pop("close_fds", None)
        touch()
        cpu = time.thread_time()
        proc = await real(*argv, **kwargs)
        starts.append(time.thread_time() - cpu)
        return proc

    asyncio.create_subprocess_exec = timed

    async def main():
        for _ in range(5):
            assert await content_scan._ask_child(b"a note\\n") == {"dangerous": False}

    asyncio.run(main())
    print(json.dumps(starts))
    """)


def _starts_in_a_large_gateway(way: str, home) -> list[float]:
    done = subprocess.run(
        [sys.executable, "-c", _LARGE_GATEWAY, str(_RESIDENT_MB), way],
        capture_output=True,
        timeout=120,
        env={**os.environ, "PERSONALCLAW_HOME": str(home)},
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")[-2000:]
    return json.loads(done.stdout)


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Linux's fork of a child is a vfork, which lets go of the interpreter lock",
)
@pytest.mark.parametrize("way", ["the-gateways", "planted-the-old-way"])
def test_a_large_gateway_starts_the_scan_child_without_holding_its_loop(tmp_path, way):
    old = way == "planted-the-old-way"
    starts = _starts_in_a_large_gateway("old" if old else "new", tmp_path / "home")

    shown = [round(cpu * 1000, 1) for cpu in starts]
    if not old:
        assert max(starts) < _START_CPU_BOUND, (
            f"a start of the scan's child took the loop thread {shown} ms of CPU in a "
            f"{_RESIDENT_MB} MB gateway; spawned, it takes about 1 ms "
            "(content_scan, 'How it starts')"
        )
    else:
        assert min(starts) >= _START_CPU_BOUND, (
            f"premise: a fork of a {_RESIDENT_MB} MB gateway takes the loop thread more than "
            f"{_START_CPU_BOUND * 1000:.0f} ms of CPU; it took {shown} ms"
        )
