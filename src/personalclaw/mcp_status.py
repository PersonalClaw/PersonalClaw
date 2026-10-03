"""Why an MCP server is not running, in the words every surface says it, and who is told.

Two things start a server: the probe whose result the Tools page card and the Settings test show
(`mcp_discovery.probe_server`), and the connection an agent's tools go through
(`mcp_client.McpServerConn`). Both start it the same way and record what happened in the same
place (`mcp_discovery.note_start`), so the card, Settings and what an agent is told about the
server say the same thing. This module holds the words, and the listeners told when a server's
card changes.

**A failed start** (:class:`StartFailure`) has a one-line headline that names what happened: the
server exited, with its exit code and the line of its error output that says why; it was still
running at the deadline and had not answered; its command is not there. The tail of what it wrote to
its error output is the failure's ``detail``, which the card shows behind Details, never in the
headline: a traceback is not a reason.

**A server that keeps failing is stopped** (:data:`STOP_AFTER`, :func:`stopped_trying`): it is not
started again, by an agent's next turn or by the Tools page's next look, until its owner presses
Retry or its definition changes. A failure that started nothing (its command is not there) or that
waits on its owner (a sign-in) does not count toward it.

**A server still starting is not a failure yet** (:func:`still_starting`): one that had not answered
by its deadline and was still running is left to finish (`mcp_stdio`), because a first start can be
installing what it runs. Its card reads as being checked until it is looked at again.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Failed starts in a row after which a server is not started again until its owner says so.
STOP_AFTER = 3

#: The status a server reads once it is stopped (`GET /api/mcp`), with Retry on its card.
STOPPED = "stopped"

#: How much of a server's error output is kept: its tail, which is where a cause is written.
STDERR_TAIL_BYTES = 8192

#: How long a cause line may be in a headline. The whole line is in the detail.
_CAUSE_CHARS = 200


@dataclass(frozen=True)
class StartFailure:
    """Why one start of a server failed: a one-line ``headline``, and the ``detail`` behind it."""

    headline: str
    #: The tail of the server's error output, as it wrote it. Shown behind Details.
    detail: str = ""
    #: Whether it counts toward :data:`STOP_AFTER`. A start that ran nothing (no command) or that
    #: waits on its owner (a sign-in) is not a failure to keep retrying.
    counts: bool = True
    #: It has not finished starting, so nothing is known yet (:func:`still_starting`): its card
    #: reads as being checked, not as an error.
    pending: bool = False


# ── the cause line in a server's error output ──────────────────────────────────────────────────

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
#: Box-drawing and bullet marks a tool prints in front of its own lines.
_DECORATION = re.compile(r"^[\s×✖✗•·│├└╰─▶>|*-]+")
#: Lines that are where an error happened, not what it was: a traceback's frames and the code under
#: them, a stack's ``at …`` lines, the traceback's own heading, and a tool's hints.
_NOT_A_CAUSE = re.compile(
    r"""^(File\ ".*",\ line\ \d+|at\ \S|Traceback\ \(most\ recent\ call\ last\)|
         During\ handling\ of\ the\ above|The\ above\ exception\ was|
         (hint|help|note):|\[(stdout|stderr)\]$|[\^~]+$)""",
    re.VERBOSE,
)
#: A line that states an error: ``SomethingError: …``, ``error: …``, ``fatal: …``, ``panic: …``.
_STATES_AN_ERROR = re.compile(
    r"^([A-Za-z_][\w.]*(Error|Exception|Exit)\b|(error|fatal|panic|failed)\b)", re.IGNORECASE
)


def _clean(line: str) -> str:
    return _DECORATION.sub("", _ANSI.sub("", line)).strip()


def cause_line(stderr: str) -> str:
    """The line of a server's error output that says why it stopped, or ``""``.

    The last line that states an error (``ModuleNotFoundError: …``, ``error: …``, ``Failed to
    build …``), else the last line that is not where an error happened (a traceback frame, a stack
    line, a hint). Never a frame: "File …, line 184, in run_commands" names a place, not a cause.
    """
    lines = [c for c in (_clean(raw) for raw in stderr.splitlines()) if c]
    reasons = [c for c in lines if not _NOT_A_CAUSE.match(c)]
    stated = [c for c in reasons if _STATES_AN_ERROR.match(c)]
    picked = (stated or reasons or [""])[-1]
    return picked if len(picked) <= _CAUSE_CHARS else picked[: _CAUSE_CHARS - 1] + "…"


def _seconds(n: float) -> str:
    return f"{n:g} second" if n == 1 else f"{n:g} seconds"


# ── the failures ───────────────────────────────────────────────────────────────────────────────


def exited(server: str, returncode: int, stderr: str) -> StartFailure:
    """*server*'s process ended before it answered. A negative *returncode* is the signal that
    ended it (``asyncio``'s convention)."""
    how = (
        f"was ended by signal {-returncode}" if returncode < 0 else f"exited with code {returncode}"
    )
    cause = cause_line(stderr)
    headline = (
        f"{server} {how} before it answered: {cause.rstrip('.')}."
        if cause
        else f"{server} {how} before it answered, and wrote nothing to say why."
    )
    return StartFailure(headline, detail=stderr.strip())


def closed(server: str, stderr: str) -> StartFailure:
    """*server* closed its output before it answered and did not end, so PersonalClaw stopped it."""
    cause = cause_line(stderr)
    tail = f": {cause.rstrip('.')}." if cause else "."
    return StartFailure(
        f"{server} closed its connection before it answered, and was stopped{tail}",
        detail=stderr.strip(),
    )


def did_not_answer(server: str, waited: float, stderr: str = "") -> StartFailure:
    """*server* was still running at the deadline and had not answered."""
    return StartFailure(
        f"{server} did not answer within {_seconds(waited)}, so PersonalClaw stopped it.",
        detail=stderr.strip(),
    )


def still_starting(
    server: str, waited: float, stderr: str = "", *, allowance: float, earlier: bool = False
) -> StartFailure:
    """*server* had not answered within *waited* and was still running, so it was left to finish
    starting for up to *allowance* (`mcp_stdio.FINISH_SECS`). With *earlier*, this start ran
    nothing: an earlier start of the same program was still finishing, and it waited for that one.
    Either way it is looked at again once that start ends, so it is not counted."""
    if earlier:
        headline = (
            f"{server} is still finishing an earlier start, so it was not started a second time. "
            "PersonalClaw checks it again when that start ends."
        )
    else:
        headline = (
            f"{server} has not answered after {_seconds(waited)} and is still starting. A first "
            "start can take longer while it installs what it runs, so PersonalClaw lets it finish, "
            f"for up to {_minutes(allowance)}, and then checks it again."
        )
    return StartFailure(headline, detail=stderr.strip(), counts=False, pending=True)


def _minutes(secs: float) -> str:
    minutes = secs / 60
    return "1 minute" if minutes == 1 else f"{minutes:g} minutes"


def command_not_found(command: str) -> StartFailure:
    """Nothing was started: *command* is not on the ``PATH`` the server is started with."""
    return StartFailure(f"command not found: {command}", counts=False)


def stopped_trying(server: str, last: str) -> str:
    """What a stopped server's card says (:data:`STOPPED`); *last* is what its last start found."""
    return (
        f"{server} failed to start {STOP_AFTER} times in a row, so PersonalClaw stopped trying. "
        f"The last time: {last.rstrip('.')}. Press Retry to start it again."
    )


# ── who is told ────────────────────────────────────────────────────────────────────────────────

#: ``(server name) -> None`` callbacks, called when what a server's card says may have changed.
_listeners: list[Callable[[str], None]] = []


def subscribe(listener: Callable[[str], None]) -> None:
    """Be told, with its name, each time what a server's card says may have changed (idempotent).
    The dashboard subscribes at start and sends the pages a ``refresh`` frame naming ``mcp``."""
    if listener not in _listeners:
        _listeners.append(listener)


def unsubscribe(listener: Callable[[str], None]) -> None:
    if listener in _listeners:
        _listeners.remove(listener)


def announce(server: str) -> None:
    """Tell every listener that *server*'s card may say something new. A listener that raises is
    logged and the rest are still told: what changed has already happened."""
    for listener in list(_listeners):
        try:
            listener(server)
        except Exception:  # noqa: BLE001 — a page that cannot be told must not fail the start
            logger.warning("MCP status listener failed for %s", server, exc_info=True)
