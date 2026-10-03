"""What a supervised child process prints: relayed into the gateway's log, and its last lines
kept for whoever shows why it stopped.

PersonalClaw runs programs it did not write for as long as an app is enabled: the app's backend,
its background worker and its engine. What such a program prints is the only account of what it
is doing, and the first thing anyone needs once it stops: a traceback, a stack trace, "address
already in use". So every line it prints reaches the log's sinks (``log_sinks``: the console, the
home's ``gateway.log`` and Settings › Diagnostics), tagged with the app, the process and its pid,
and its last :data:`TAIL_LINES` lines are kept with how it ended (:class:`ChildOutput`), which is
what the app's panel and the Doctor show for a process that is not running.

A child's output is not PersonalClaw's own text, so each line is:

* masked the way the log masks what a child prints (``security.mask_child_output``), and also
  by value, for every credential in the environment the child was started with: a backend's
  proxy secret has no shape a mask could recognise;
* one line, its control characters written as visible escapes, so a child cannot print a line
  that reads as one of the log's own records;
* bounded: a line longer than :data:`LINE_MAX_CHARS` is cut, saying so; and the log takes a burst
  of :data:`LOG_BURST` lines from one process and then :data:`LOG_PER_SEC` a second. What the log
  does not take is counted and said in one line, at most every :data:`NOTE_EVERY_SECS` and once
  at the end. Every line is kept, so the end of a child the log held back is still on its panel.

stderr is logged at WARNING, where a program writes what went wrong, so it shows at the default
level; stdout at INFO. A process's pipes are drained by a thread of its own as fast as it writes
(:func:`relay`), so a chatty child never waits on a full pipe and nothing in the gateway waits on
a child. Python buffers what a child prints to a pipe until it flushes (or exits); what it writes
to stderr, a traceback or the ``logging`` module's output, arrives line by line.
"""

from __future__ import annotations

import logging
import os
import re
import selectors
import subprocess
import threading
import time
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from personalclaw.security import mask_child_output, redact_known_values

logger = logging.getLogger(__name__)

STDOUT = "stdout"
STDERR = "stderr"

#: The level each stream is logged at: what went wrong is shown at the default level.
_LEVELS = {STDOUT: logging.INFO, STDERR: logging.WARNING}

#: Lines kept per process, newest last: room for a traceback and what led up to it.
TAIL_LINES = 40

#: The longest line logged or shown. A longer one is cut, and says so.
LINE_MAX_CHARS = 1000

#: How much of one line is read before the rest of it is skipped. Well above
#: :data:`LINE_MAX_CHARS`, so a line is masked whole before it is cut for showing.
_LINE_READ_BYTES = 8192

#: The log takes this many of one process's lines at once...
LOG_BURST = 200

#: ...and then this many a second.
LOG_PER_SEC = 2.0

#: How often, at most, a note says how many lines the log did not take.
NOTE_EVERY_SECS = 10.0

#: How much of a pipe is read at a time.
_READ_CHUNK = 64 * 1024

#: How often the drain wakes with nothing to read: to say a held-back count that is due, and to
#: notice a process that has ended while something it started still holds its output open.
_POLL_SECS = 1.0

#: How long the drain waits for the process to end once its output has closed.
_EXIT_WAIT_SECS = 5.0

#: The most leading indentation kept on a line (a traceback's frames are indented).
_INDENT_MAX = 32

#: The trailing run of a line cut short: it may be the first part of a credential, which no mask
#: recognises by half, so it is not kept.
_TRAILING_WORD = re.compile(r"\S+$")

#: The clock the log's share of a process is measured on.
_monotonic = time.monotonic


def _credential_values(env: Mapping[str, str] | None) -> tuple[str, ...]:
    """The values of *env* whose names say they are credentials (the one hint list,
    ``workflows.secrets.matches_secret_hint``): what a child was handed and may print."""
    if not env:
        return ()
    from personalclaw.workflows.secrets import matches_secret_hint

    return tuple(value for name, value in env.items() if value and matches_secret_hint(name))


def mask_line(text: str, secrets: Iterable[str] = ()) -> str:
    """One line a child printed, as it may be logged or shown: every one of *secrets* (the
    credential values it was handed) masked, then masked the way the log masks a child's output
    (``security.mask_child_output``) and kept on one line; its leading indentation kept (a
    traceback's frames are indented), and cut to :data:`LINE_MAX_CHARS`, saying so."""
    indent = text[: len(text) - len(text.lstrip(" \t"))][:_INDENT_MAX]
    shown = mask_child_output(redact_known_values(text, secrets), limit=None)
    if len(shown) > LINE_MAX_CHARS:
        shown = f"{shown[:LINE_MAX_CHARS].rstrip()} [… cut]"
    return indent + shown


def _cut(start: str, size: int) -> str:
    """A line of *size* bytes of which only *start* was read. Its last word may be the first
    part of a credential, which no mask recognises by half, so that is not kept either."""
    kept = _TRAILING_WORD.sub("", start).rstrip()
    if not kept:
        return f"[a line of {size} bytes, not kept]"
    return f"{kept} [… cut: the line was {size} bytes]"


def bound_line(text: str) -> str:
    """*text*, or its start when it is longer than a kept line may be, cut as
    :class:`LineSplitter` cuts one: for a caller that already holds a whole line."""
    if len(text) <= _LINE_READ_BYTES:
        return text
    return _cut(text[:_LINE_READ_BYTES], len(text.encode("utf-8", "replace")))


def ended_words(code: int) -> str:
    """How a process with exit status *code* ended (negative: the signal that ended it), in the
    words the MCP card uses for a server."""
    if code < 0:
        return f"was ended by signal {-code}"
    if code:
        return f"exited with code {code}"
    return "exited"


@dataclass(frozen=True)
class ChildExit:
    """How one run of a supervised process ended."""

    #: Its exit status; negative, the signal that ended it.
    code: int
    #: When it was seen to end, in seconds since the epoch.
    at: float
    #: PersonalClaw ended it (a stop, a pause, a shutdown): it did not end on its own.
    stopped: bool


class LineSplitter:
    """The bytes one stream of a child carries, as the lines it wrote.

    A line is what a terminal would show of it: a progress bar that redraws itself with carriage
    returns is its last drawing. A line longer than ``_LINE_READ_BYTES`` keeps its start and says
    how long it was; the rest is skipped as it arrives, so a child that never ends a line holds no
    more than that much of it here.
    """

    def __init__(self) -> None:
        self._pending = bytearray()
        self._skipped = 0

    def feed(self, chunk: bytes) -> list[str]:
        """The lines *chunk* completes."""
        lines: list[str] = []
        start = 0
        while (end := chunk.find(b"\n", start)) >= 0:
            self._take(chunk[start:end])
            lines.append(self._finish())
            start = end + 1
        self._take(chunk[start:])
        return lines

    def close(self) -> list[str]:
        """The last line, when the stream ended without a line break after it."""
        if not self._pending and not self._skipped:
            return []
        return [self._finish()]

    def _take(self, piece: bytes) -> None:
        room = max(0, _LINE_READ_BYTES - len(self._pending))
        self._pending += piece[:room]
        self._skipped += max(0, len(piece) - room)

    def _finish(self) -> str:
        raw, skipped = bytes(self._pending), self._skipped
        self._pending.clear()
        self._skipped = 0
        text = raw.decode("utf-8", "replace").removesuffix("\r").rsplit("\r", 1)[-1]
        return _cut(text, len(raw) + skipped) if skipped else text


class ChildOutput:
    """One run of one supervised process: the lines it printed, its share of the log, and how
    it ended.

    *env* is the environment it was started with; every credential in it is masked out of what it
    prints, by value. Thread-safe: a process's streams may be read by different threads.
    """

    def __init__(
        self, *, app: str, process: str, pid: int, env: Mapping[str, str] | None = None
    ) -> None:
        self.app = app
        self.process = process
        self.pid = pid
        self._secrets = _credential_values(env)
        self._lock = threading.Lock()
        self._kept: deque[str] = deque(maxlen=TAIL_LINES)
        self._tokens = float(LOG_BURST)
        self._refilled = _monotonic()
        self._held_back = 0
        self._held_back_level = logging.INFO
        self._noted = float("-inf")
        self._stopping = False
        self._exit: ChildExit | None = None

    # -- what it printed ---------------------------------------------------

    def line(self, stream: str, text: str) -> None:
        """Keep one line the process printed on *stream* (:func:`bound_line`), and log it when
        the log takes it."""
        if not text.strip():
            return
        text = bound_line(text)
        level = _LEVELS[stream]
        with self._lock:
            self._kept.append(text)
            if not logger.isEnabledFor(level):
                return
            if not self._admit():
                self._held_back += 1
                self._held_back_level = max(self._held_back_level, level)
                return
            note = self._held_back_note(due_only=True)
        if note is not None:
            logger.log(*note)
        logger.log(
            level,
            "app %s %s (pid %d) %s: %s",
            self.app,
            self.process,
            self.pid,
            stream,
            self._shown(text),
        )

    def tick(self) -> None:
        """Say how many lines the log has not taken, when that is due."""
        with self._lock:
            note = self._held_back_note(due_only=True)
        if note is not None:
            logger.log(*note)

    def _admit(self) -> bool:
        """Whether the log takes one more line now (the burst, then the rate). Lock held."""
        now = _monotonic()
        self._tokens = min(float(LOG_BURST), self._tokens + (now - self._refilled) * LOG_PER_SEC)
        self._refilled = now
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True

    def _held_back_note(self, *, due_only: bool) -> tuple[Any, ...] | None:
        """The log call that says how many lines the log did not take, or ``None``. Lock held."""
        if not self._held_back:
            return None
        now = _monotonic()
        if due_only and now - self._noted < NOTE_EVERY_SECS:
            return None
        count, level = self._held_back, self._held_back_level
        self._held_back, self._held_back_level, self._noted = 0, logging.INFO, now
        return (
            level,
            "app %s %s (pid %d): %d more lines it printed are not in the log, which takes %d at "
            "once from one process and then %g a second",
            self.app,
            self.process,
            self.pid,
            count,
            LOG_BURST,
            LOG_PER_SEC,
        )

    def _shown(self, text: str) -> str:
        return mask_line(text, self._secrets)

    def lines(self) -> list[str]:
        """The last lines it printed, oldest first, masked."""
        with self._lock:
            kept = list(self._kept)
        return [self._shown(text) for text in kept]

    def cause(self) -> str:
        """The line of what it printed that says why it stopped, masked, or ``""``: the rule
        the MCP card uses (``mcp_status.cause_line``)."""
        from personalclaw.mcp_status import cause_line

        with self._lock:
            kept = list(self._kept)
        picked = cause_line("\n".join(kept))
        return self._shown(picked) if picked else ""

    # -- how it ended ------------------------------------------------------

    def stopping(self) -> None:
        """PersonalClaw is about to end it, so its end is not one it came to on its own."""
        with self._lock:
            self._stopping = True

    @property
    def exit(self) -> ChildExit | None:
        """How it ended, once it has."""
        with self._lock:
            return self._exit

    def ended(self, code: int) -> None:
        """Record that it ended with exit status *code*, and log it unless PersonalClaw ended it.
        Only the first call counts."""
        with self._lock:
            if self._exit is not None:
                return
            self._exit = ChildExit(code=code, at=time.time(), stopped=self._stopping)
            note = self._held_back_note(due_only=False)
        if note is not None:
            logger.log(*note)
        if self._exit.stopped:
            return
        cause = self.cause()
        logger.log(
            logging.INFO if code == 0 else logging.WARNING,
            "app %s %s (pid %d) %s%s",
            self.app,
            self.process,
            self.pid,
            ended_words(code),
            f": {cause}" if cause else "",
        )

    def report(self) -> dict[str, Any] | None:
        """How this run ended and the last lines it printed, for the surface that says why the
        process is not running; ``None`` while it runs, and for a run PersonalClaw ended."""
        ended = self.exit
        if ended is None or ended.stopped:
            return None
        from personalclaw.instants import utc_iso

        return {
            "pid": self.pid,
            "exitCode": ended.code,
            "ended": ended_words(ended.code),
            "endedAt": utc_iso(ended.at),
            "cause": self.cause(),
            "lines": self.lines(),
        }


def relay(
    proc: subprocess.Popen[Any],
    output: ChildOutput,
    *,
    streams: Iterable[str] = (STDOUT, STDERR),
) -> None:
    """Drain *proc*'s piped *streams* into *output* on a daemon thread of its own, and record
    how *proc* ended once they close. A stream that is not a pipe is passed over, and a process
    with none has nothing to relay."""
    pipes = {name: pipe for name in streams if (pipe := getattr(proc, name, None)) is not None}
    if not pipes:
        return
    threading.Thread(
        target=_drain,
        args=(proc, output, pipes),
        name=f"child-output-{output.app}-{output.process}",
        daemon=True,
    ).start()


def _drain(proc: subprocess.Popen[Any], output: ChildOutput, pipes: dict[str, Any]) -> None:
    """Read every pipe as it fills, without blocking on any, until each has closed.

    A line that cannot be relayed is passed over and the pipes are still drained: a closed or
    full pipe would stop the child at its next write, for a fault that is not its own."""
    splitters = {name: LineSplitter() for name in pipes}
    selector = selectors.DefaultSelector()
    try:
        for name, pipe in pipes.items():
            fd = pipe.fileno()
            os.set_blocking(fd, False)
            selector.register(fd, selectors.EVENT_READ, name)
        while selector.get_map():
            ready = selector.select(timeout=_POLL_SECS)
            chunks: list[tuple[str, bytes]] = []
            for key, _ in ready:
                try:
                    chunk = os.read(key.fd, _READ_CHUNK)
                except BlockingIOError:
                    continue
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(key.fd)
                chunks.append((key.data, chunk))
            try:
                for name, chunk in chunks:
                    splitter = splitters[name]
                    for text in splitter.feed(chunk) if chunk else splitter.close():
                        output.line(name, text)
                output.tick()
                if not ready and output.exit is None and (code := proc.poll()) is not None:
                    # It has ended, and something it started still holds its output open: that
                    # goes on being read, under this process's name.
                    output.ended(code)
            except Exception:  # noqa: BLE001 — the pipes are still drained (see above)
                logger.debug(
                    "app %s %s: a line it printed was not relayed",
                    output.app,
                    output.process,
                    exc_info=True,
                )
    except (OSError, ValueError):
        logger.debug("app %s %s: its output could not be read", output.app, output.process)
    finally:
        selector.close()
        for pipe in pipes.values():
            try:
                pipe.close()
            except OSError:
                pass
    if output.exit is not None:
        return
    try:
        code = proc.wait(timeout=_EXIT_WAIT_SECS)
    except subprocess.TimeoutExpired:
        return  # it closed its output and runs on; its supervisor still sees it running
    output.ended(code)
