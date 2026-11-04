"""A watch on the gateway's event loop that names the frame a stalled loop is stuck in.

Only the browser harness arms it: ``web/playwright.config.ts`` sets :data:`STALL_ENV` for the
gateway it starts. A thread of the watch's own asks the loop to answer (``call_soon_threadsafe``)
once a second. When the loop has gone :data:`STALL_ENV` seconds without answering, the thread
appends every thread's stack to the gateway's log file, the loop's thread first, and does it again
each time the wait doubles, until the loop answers. So a hang in CI names its frame, where it used
to leave only requests that never got a response (#5638).

The harness gateway went on losing runs that way with a gateway.log of 0 bytes, so the watch also
names the stalls the loop's answer cannot show:

- The worker pool. A handler that waits on ``asyncio.to_thread`` (the loop's default executor)
  waits for a free worker, and with every worker stuck every such request goes unanswered while the
  loop itself answers. So once the loop answers, the thread hands the pool a job that does nothing,
  and while the pool has not run it for :data:`STALL_ENV` seconds, the same dump is written, saying
  it is the pool, again each time the wait doubles.
- The watch itself. A stall that holds the GIL (a C call that never releases it) stops this thread
  too. So at every slice the thread re-arms :func:`faulthandler.dump_traceback_later`, whose
  watchdog is a C thread that needs no GIL: it writes every thread's stack into the log file only
  when this thread has been held for :data:`STALL_ENV` seconds.

And whoever holds the gateway's pid can ask for every thread's stack, whatever holds the gateway:
the watch registers :data:`DUMP_SIGNAL` with :mod:`faulthandler`, which writes them from the signal
handler itself (``askTheGatewayWhereItWaits`` in ``web/e2e/runPlaces.ts`` sends it when the
harness's shell guard finds the gateway unanswering). Both of faulthandler's dumps name files,
lines and functions, never a value. The watch lets go of the signal and the watchdog when the loop
closes.

The dump is appended to the log file directly, formatted by the file handler's own formatter (the
masking one), and not handed to the logger: a loop stuck inside logging holds a handler's lock, and
a dump that waited for that lock would never be written. So it reaches no other sink either, and
Settings › Diagnostics never shows a stack.
"""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import os
import signal
import sys
import threading
import time
import traceback
from typing import IO

from personalclaw.security import MaskingFormatter

#: How many seconds the loop may go without answering before every thread's stack is dumped. Unset,
#: or not a number above 0: nothing watches the loop.
STALL_ENV = "PERSONALCLAW_LOOP_STALL_SECONDS"

#: How often the watch asks the loop to answer.
ASK_EVERY = 1.0

#: The signal that asks a watched gateway for every thread's stack; ``None`` where there is none.
DUMP_SIGNAL: signal.Signals | None = getattr(signal, "SIGUSR1", None)

#: The line a dump is written as where the log file's handler names no formatter of its own.
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

logger = logging.getLogger(__name__)


def arm_from_env() -> threading.Thread | None:
    """Watch the running loop when :data:`STALL_ENV` asks for it, and return the watching thread.
    Called on the loop's own thread."""
    raw = os.environ.get(STALL_ENV, "")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        seconds = 0.0
    if not seconds > 0:
        logger.warning(
            "%s=%r is not a number of seconds above 0, so nothing watches the event loop",
            STALL_ENV,
            raw,
        )
        return None
    sink = _faulthandler_sink()
    if DUMP_SIGNAL is not None:
        faulthandler.register(DUMP_SIGNAL, file=sink, all_threads=True)
    # Armed here, on the loop's thread, so the watchdog runs from the start even when the loop
    # holds the GIL before the watch's thread has run at all; the thread pushes it on from then.
    faulthandler.dump_traceback_later(_watchdog_seconds(seconds), file=sink)
    watch = threading.Thread(
        target=_watch,
        args=(asyncio.get_running_loop(), threading.get_ident(), seconds, sink),
        name="loop-stall-watch",
        daemon=True,
    )
    watch.start()
    return watch


def _watch(
    loop: asyncio.AbstractEventLoop, loop_thread: int, seconds: float, sink: IO[str]
) -> None:
    try:
        while not loop.is_closed():
            answered, ran = threading.Event(), threading.Event()
            try:
                loop.call_soon_threadsafe(_ask_the_pool, loop, answered, ran)
            except RuntimeError:  # the loop closed after the check
                return
            asked = time.monotonic()
            if not _wait(answered, "the event loop", loop, loop_thread, seconds, sink, asked):
                return
            if not _wait(ran, "the worker pool", loop, loop_thread, seconds, sink, asked):
                return
            time.sleep(ASK_EVERY)
    finally:
        faulthandler.cancel_dump_traceback_later()
        if DUMP_SIGNAL is not None:
            faulthandler.unregister(DUMP_SIGNAL)
        if sink is not sys.stderr:
            sink.close()


def _watchdog_seconds(seconds: float) -> float:
    """How long the watch's own thread may go unrun before faulthandler's watchdog dumps: the stall
    limit, and never less than two of the thread's slices, so a slice is never taken for a stall."""
    return max(seconds, 2 * ASK_EVERY)


def _ask_the_pool(
    loop: asyncio.AbstractEventLoop, answered: threading.Event, ran: threading.Event
) -> None:
    """On the loop's thread: say it answered, and hand the default executor a job that says it ran.
    A pool that is shutting down takes no job, and is not a stall."""
    answered.set()
    try:
        loop.run_in_executor(None, ran.set)
    except RuntimeError:
        ran.set()


#: What a dump says of each thing the watch waits for: that it has not answered, and that it did.
_SAYS = {
    "the event loop": ("The event loop has not answered", "The event loop answered"),
    "the worker pool": ("The worker pool has not run a job", "The worker pool ran a job"),
}


def _wait(
    done: threading.Event,
    what: str,
    loop: asyncio.AbstractEventLoop,
    loop_thread: int,
    seconds: float,
    sink: IO[str],
    asked: float,
) -> bool:
    """Wait for *done* (*what* answering), dumping every thread's stack each time the wait passes
    its limit, then doubling the limit. ``False`` when the loop closed with *done* unset."""
    stalled, answered = _SAYS[what]
    limit = seconds
    # In slices, so a loop that closed with the question unanswered ends the watch, and so the
    # watchdog is pushed on for as long as this thread runs.
    while True:
        faulthandler.dump_traceback_later(_watchdog_seconds(seconds), file=sink)
        if done.wait(min(ASK_EVERY, max(0.0, asked + limit - time.monotonic()))):
            break
        if loop.is_closed():
            return False
        if time.monotonic() - asked < limit:
            continue
        _append(
            f"{stalled} for {time.monotonic() - asked:.0f} s. "
            f"Every thread's stack, most recent call last:\n{stacks(loop_thread)}"
        )
        limit *= 2
    if limit > seconds:
        _append(f"{answered} after {time.monotonic() - asked:.0f} s.")
    return True


def stacks(loop_thread: int) -> str:
    """Every thread's stack but the caller's, each under its name, *loop_thread*'s first."""
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    frames = sys._current_frames()
    frames.pop(threading.get_ident(), None)
    parts = []
    for ident in sorted(frames, key=lambda ident: ident != loop_thread):
        whose = ", the event loop's" if ident == loop_thread else ""
        name = names.get(ident, "a thread threading does not list")
        parts.append(
            f"Thread {name!r} (0x{ident:x}){whose}:\n"
            + "".join(traceback.format_stack(frames[ident]))
        )
    return "\n".join(parts)


def _log_files() -> list[logging.FileHandler]:
    return [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]


def _faulthandler_sink() -> IO[str]:
    """Where faulthandler writes: the first log file the root logger writes that is a file (not
    the null device), opened for appending and held open while the watch runs (faulthandler writes
    to its descriptor from C, with no lock and no formatter, as a stall needs); stderr when it
    writes none."""
    files = [h.baseFilename for h in _log_files() if h.baseFilename != os.devnull]
    if not files:
        return sys.stderr
    return open(files[0], "a", encoding="utf-8")  # noqa: SIM115 - held for the watch


def _append(text: str) -> None:
    """Append *text*, as one ERROR record, to every log file the root logger writes, without taking
    a handler's lock; to stderr when it writes none. Masked either way, as every sink is."""
    record = logging.LogRecord(logger.name, logging.ERROR, __file__, 0, text, None, None)
    masked = MaskingFormatter(_FORMAT, dated=True)
    files = _log_files()
    if not files:
        _write(2, masked.format(record))
        return
    for handler in files:
        line = (handler.formatter or masked).format(record)
        fd = os.open(handler.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            _write(fd, line)
        finally:
            os.close(fd)


def _write(fd: int, line: str) -> None:
    data = (line + "\n").encode("utf-8", "replace")
    while data:
        data = data[os.write(fd, data) :]
