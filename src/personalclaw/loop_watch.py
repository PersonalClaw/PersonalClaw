"""A watch on the gateway's event loop that names the frame a stalled loop is stuck in.

Only the browser harness arms it: ``web/playwright.config.ts`` sets :data:`STALL_ENV` for the
gateway it starts. A thread of the watch's own asks the loop to answer (``call_soon_threadsafe``)
once a second. When the loop has gone :data:`STALL_ENV` seconds without answering, the thread
appends every thread's stack to the gateway's log file, the loop's thread first, and does it again
each time the wait doubles, until the loop answers. So a hang in CI names its frame, where it used
to leave only requests that never got a response (#5638).

The dump is appended to the log file directly, formatted by the file handler's own formatter (the
masking one), and not handed to the logger: a loop stuck inside logging holds a handler's lock, and
a dump that waited for that lock would never be written. So it reaches no other sink either, and
Settings › Diagnostics never shows a stack.

A stall that holds the GIL (a C call that never releases it) stops this thread as well, so that
kind of stall goes unnamed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import traceback

from personalclaw.security import MaskingFormatter

#: How many seconds the loop may go without answering before every thread's stack is dumped. Unset,
#: or not a number above 0: nothing watches the loop.
STALL_ENV = "PERSONALCLAW_LOOP_STALL_SECONDS"

#: How often the watch asks the loop to answer.
ASK_EVERY = 1.0

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
    watch = threading.Thread(
        target=_watch,
        args=(asyncio.get_running_loop(), threading.get_ident(), seconds),
        name="loop-stall-watch",
        daemon=True,
    )
    watch.start()
    return watch


def _watch(loop: asyncio.AbstractEventLoop, loop_thread: int, seconds: float) -> None:
    while not loop.is_closed():
        answered = threading.Event()
        try:
            loop.call_soon_threadsafe(answered.set)
        except RuntimeError:  # the loop closed after the check
            return
        asked = time.monotonic()
        limit = seconds
        # In slices, so a loop that closed with the question unanswered ends the watch.
        while not answered.wait(min(ASK_EVERY, max(0.0, asked + limit - time.monotonic()))):
            if loop.is_closed():
                return
            if time.monotonic() - asked < limit:
                continue
            _append(
                f"The event loop has not answered for {time.monotonic() - asked:.0f} s. "
                f"Every thread's stack, most recent call last:\n{stacks(loop_thread)}"
            )
            limit *= 2
        if limit > seconds:
            _append(f"The event loop answered after {time.monotonic() - asked:.0f} s.")
        time.sleep(ASK_EVERY)


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


def _append(text: str) -> None:
    """Append *text*, as one ERROR record, to every log file the root logger writes, without taking
    a handler's lock; to stderr when it writes none. Masked either way, as every sink is."""
    record = logging.LogRecord(logger.name, logging.ERROR, __file__, 0, text, None, None)
    masked = MaskingFormatter(_FORMAT)
    files = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
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
