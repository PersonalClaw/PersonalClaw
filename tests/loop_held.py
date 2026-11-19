"""How long a step of the event loop was held by the work it did, not by a runner that stopped the
process (#5809).

A bound on how long the loop goes without a turn was read in wall time, and a queue runner that
stops the whole process, or pages it back in from a stalled disk, stretches wall time with no work
done. In one night three macOS queue shards read such a stall as a block, each in a test the change
never touched and each passing when run again alone: the apps list at 534 ms against its 150 ms
(run 44487362307), a search beside a re-index at 0.55 s against 0.4 s (run 44508176523), and an
upload's step of 0.2 s with 0.001 s of CPU that ran a socket's non-blocking read (run
44516860509).

So a step is judged by what it did:

- A step that made a blocking call on the loop thread is held for its whole wall time: the call is
  the block, however little CPU it used. The calls are those an audit hook sees (:data:`BLOCKING`:
  a file opened, which is also how a module is loaded from disk, a sleep, a folder listed or
  changed, a process started, a name looked up, a database opened), and the waits it cannot see,
  wrapped while a watch is open (:data:`WRAPPED`): ``select.select`` with a timeout, a blocking
  socket's reads, writes, accepts and connects, and, before Python 3.13, which raises no audit
  event for it, ``time.sleep`` (a module that took ``sleep`` with ``from time import sleep`` before
  the watch opened keeps the unwrapped one there).
- Any other step is held for the CPU it used, the loop thread's and the rest of the process's
  beside it, and never for more than its wall time: a worker that holds the interpreter's lock
  keeps the loop from running, and uses CPU while it does. A step the runner stopped uses neither.

Three things are held by neither, since they raise no audit event and wait with no CPU: a wait
on a lock or a condition made on the loop thread, a read, write, flush or fsync on a file already
open, and a read of a file's metadata (``os.stat``). A test whose regression is such a wait pins it
with its structure instead: the re-index search holds the rebuild until the search has answered
(``test_memory_recall_answers_within_its_budget``), and every write an upload's spool makes to its
open file is on a worker thread (``test_a_large_upload_leaves_the_gateway_answering``).
And a worker doing work that releases the interpreter's lock, beside a step the runner stopped, is
counted as that step's: it is CPU beside the step, and the helper cannot tell it from a worker that
held the lock.

:class:`LoopSteps` times each step of the loop on the thread that opens it. A probe that times its
own steps (the upload probe) reads the calls through :func:`watch` and judges with :func:`held`.
"""

from __future__ import annotations

import asyncio
import contextlib
import select
import socket
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")

#: The audit events that are a blocking call when the loop thread raises them.
BLOCKING = frozenset(
    {
        "open",
        "time.sleep",
        "os.listdir",
        "os.scandir",
        "os.remove",
        "os.rename",
        "os.rmdir",
        "os.mkdir",
        "os.chmod",
        "os.chown",
        "os.truncate",
        "os.utime",
        "os.link",
        "os.symlink",
        "os.system",
        "os.posix_spawn",
        "os.exec",
        "os.fork",
        "shutil.copyfile",
        "shutil.copymode",
        "shutil.copystat",
        "shutil.copytree",
        "shutil.move",
        "shutil.rmtree",
        "shutil.make_archive",
        "shutil.unpack_archive",
        "subprocess.Popen",
        "glob.glob",
        "glob.glob/2",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.gethostbyname_ex",
        "socket.gethostbyaddr",
        "socket.getnameinfo",
        "sqlite3.connect",
    }
)

#: The socket methods wrapped while a watch is open: each is a blocking call on a socket that
#: blocks (a timeout of None, or more than 0). The loop's own sockets never block, so its reads
#: and writes are not calls.
_SOCKET_WAITS = (
    "accept",
    "connect",
    "connect_ex",
    "recv",
    "recv_into",
    "recvfrom",
    "recvfrom_into",
    "recvmsg",
    "send",
    "sendall",
    "sendto",
    "sendmsg",
)

#: Whether this Python raises an audit event for ``time.sleep`` (3.13 on). Before it, the sleep is
#: wrapped while a watch is open, as the waits below are.
_SLEEP_AUDITED = sys.version_info >= (3, 13)

#: The waits no audit event names, wrapped while a watch is open.
WRAPPED = (
    "select.select",
    *(f"socket.socket.{name}" for name in _SOCKET_WAITS),
    *(() if _SLEEP_AUDITED else ("time.sleep",)),
)


class _Calls:
    """The blocking calls the watched thread has made, in order; one watch at a time."""

    def __init__(self) -> None:
        self.thread: int | None = None
        self.made: list[str] = []


_calls = _Calls()
_hooked = False


def _hook(event: str, _args: tuple[Any, ...]) -> None:
    # Called on every audit event in the process, watched or not: cheap until a watch opens.
    if _calls.thread is not None and event in BLOCKING and threading.get_ident() == _calls.thread:
        _calls.made.append(event)


def _wrap(name: str, real: Callable[..., Any], blocks: Callable[..., bool]) -> Callable[..., Any]:
    def call(*args: Any, **kwargs: Any) -> Any:
        if _calls.thread == threading.get_ident() and blocks(*args, **kwargs):
            _calls.made.append(name)
        return real(*args, **kwargs)

    call.__wrapped__ = real  # type: ignore[attr-defined]
    return call


def _select_blocks(*args: Any, **kwargs: Any) -> bool:
    timeout = args[3] if len(args) > 3 else kwargs.get("timeout")
    return timeout is None or timeout > 0


def _socket_blocks(sock: socket.socket, *_args: Any, **_kwargs: Any) -> bool:
    return sock.gettimeout() != 0.0


def _sleep_blocks(secs: float = 0.0, *_args: Any, **_kwargs: Any) -> bool:
    return secs > 0


class Watch:
    """The blocking calls one thread makes while the watch is open (:func:`watch`)."""

    def mark(self) -> int:
        """Where the calls made from now on start: :meth:`since` reads them."""
        return len(_calls.made)

    def since(self, mark: int) -> tuple[str, ...]:
        """The blocking calls made since *mark*, in order."""
        return tuple(_calls.made[mark:])


@contextlib.contextmanager
def watch(thread: int | None = None) -> Iterator[Watch]:
    """Watch *thread* (this one when None) for blocking calls while open. One watch at a time."""
    global _hooked
    if _calls.thread is not None:
        raise RuntimeError("a loop watch is already open")
    if not _hooked:
        sys.addaudithook(_hook)
        _hooked = True
    real_select, real_sleep = select.select, time.sleep
    real_socket = {name: getattr(socket.socket, name) for name in _SOCKET_WAITS}
    select.select = _wrap("select.select", real_select, _select_blocks)  # type: ignore[assignment]
    if not _SLEEP_AUDITED:
        time.sleep = _wrap("time.sleep", real_sleep, _sleep_blocks)  # type: ignore[assignment]
    for name, real in real_socket.items():
        setattr(socket.socket, name, _wrap(f"socket.socket.{name}", real, _socket_blocks))
    _calls.made = []
    _calls.thread = threading.get_ident() if thread is None else thread
    try:
        yield Watch()
    finally:
        _calls.thread = None
        select.select = real_select  # type: ignore[assignment]
        time.sleep = real_sleep  # type: ignore[assignment]
        for name, real in real_socket.items():
            setattr(socket.socket, name, real)


def held(wall: float, cpu: float, beside: float, calls: tuple[str, ...]) -> float:
    """How long a step of *wall* seconds held the loop: all of it when it made a blocking call
    (*calls*), else the CPU the loop thread used in it (*cpu*) and the rest of the process beside it
    (*beside*), never more than its wall time."""
    if calls:
        return wall
    return min(wall, max(0.0, cpu) + max(0.0, beside))


@dataclass(frozen=True)
class Step:
    """One step of the loop: a callback it ran, start to end, with nothing else run meanwhile."""

    wall: float
    cpu: float
    beside: float
    calls: tuple[str, ...]
    ran: str

    @property
    def held(self) -> float:
        return held(self.wall, self.cpu, self.beside, self.calls)

    def __str__(self) -> str:
        made = ", ".join(dict.fromkeys(self.calls)) if self.calls else "no blocking call"
        return (
            f"{self.held:.3f}s held, of {self.wall:.3f}s in wall time, {self.cpu:.3f}s of the loop"
            f" thread's CPU and {self.beside:.3f}s beside it, {made}; it ran {self.ran}"
        )


def _ran(handle: asyncio.Handle) -> str:
    callback = getattr(handle, "_callback", None)
    owner = getattr(callback, "__self__", None)
    if isinstance(owner, asyncio.Task):
        coro = owner.get_coro()
        return f"the task {getattr(coro, '__qualname__', coro)!s}"
    return getattr(callback, "__qualname__", repr(callback))


class LoopSteps:
    """Each step the event loop runs on this thread while open, judged by what it did (module doc).

    Open it on the loop's thread, around the work, and await the work through :meth:`during`:
    ``with LoopSteps() as steps: await steps.during(work())``. The step running when the watch
    opens is not one it times, so work awaited straight in it, with no step of its own, is not
    measured. :attr:`held` is the longest any step held the loop, and :meth:`says` names that step
    and the one longest in wall time. Steps shorter than *recorded_from* are not kept."""

    def __init__(self, *, recorded_from: float = 0.0) -> None:
        self.recorded_from = recorded_from
        self.steps: list[Step] = []
        self._stack: contextlib.ExitStack | None = None

    def __enter__(self) -> LoopSteps:
        loop_thread = threading.get_ident()
        stack = contextlib.ExitStack()
        calls = stack.enter_context(watch(loop_thread))
        real = asyncio.events.Handle._run
        steps = self

        def run(handle: asyncio.Handle) -> None:
            if threading.get_ident() != loop_thread:
                return real(handle)
            mark = calls.mark()
            began, cpu, process = time.monotonic(), time.thread_time(), time.process_time()
            try:
                return real(handle)
            finally:
                wall = time.monotonic() - began
                cpu = time.thread_time() - cpu
                beside = time.process_time() - process - cpu
                if wall >= steps.recorded_from:
                    steps.steps.append(
                        Step(wall, cpu, max(0.0, beside), calls.since(mark), _ran(handle))
                    )

        asyncio.events.Handle._run = run  # type: ignore[method-assign]
        stack.callback(setattr, asyncio.events.Handle, "_run", real)
        self._stack = stack
        return self

    def __exit__(self, *exc: object) -> None:
        assert self._stack is not None
        self._stack.close()
        self._stack = None

    async def during(self, work: Awaitable[T]) -> T:
        """Await *work* as a task of its own, so each of its steps is one this watch times."""
        return await asyncio.ensure_future(work)

    @property
    def worst(self) -> Step | None:
        """The step that held the loop longest."""
        return max(self.steps, key=lambda s: s.held, default=None)

    @property
    def held(self) -> float:
        """The longest any step held the loop."""
        worst = self.worst
        return worst.held if worst else 0.0

    @property
    def longest(self) -> Step | None:
        """The step longest in wall time."""
        return max(self.steps, key=lambda s: s.wall, default=None)

    def says(self) -> str:
        worst, longest = self.worst, self.longest
        if worst is None or longest is None:
            return "no step of the loop was recorded"
        line = f"the step that held the loop longest: {worst}"
        if longest is not worst:
            line += f"; the longest in wall time: {longest}"
        return line
