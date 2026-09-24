"""Which process macOS actually attributes an Accessibility request to (`DCU-3`, #2569).

**The principal is not the binary that asked.** macOS resolves a TCC request against the
*responsible* process — the application that launched the interpreter — and not against the
executable that called ``AXIsProcessTrusted()``. So the identity System Settings is asking
about is the terminal emulator, IDE or app bundle hosting the gateway, and the grant is a
property of **that** process rather than of the machine or of the python binary.

Measured on one workstation, same python, three hours apart::

    09:51  binary_path=.../uv/python/cpython-3.13.14-macos-aarch64-none/bin/python3.13
           responsible=com.amazon.kiro.crew  (.../KiroCrew.app/Contents/MacOS/KiroCrew)  -> granted
    18:38  binary_path=.../uv/python/cpython-3.13.14-macos-aarch64-none/bin/python3.13
           responsible=dev.warp.Warp-Stable  (/Applications/Warp.app/Contents/MacOS/stable)
           AUTHREQ_RESULT: authValue=0                                                    -> denied

That is why this module exists and why it is not part of
:mod:`~personalclaw.computer_use.macos_ffi`. There is no accessibility API that reports the
responsible process, so this is not an FFI call at all: ``tccd`` writes the attribution it used
into the unified log, and reading it back is the only way to *name* the principal an operator
must tick. The query is the one PR #2572 used to measure the two sessions above, kept as one
:data:`RESPONSIBLE_PROBE_COMMAND` string so the refusal, the two live validators and an
operator at a prompt are all reading the same thing.

**It reports, and it never guesses.** The probe is scoped to this process's own pid: an
``AUTHREQ_ATTRIBUTION`` row belonging to some other application is worse than no answer,
because it would send the operator to grant something unrelated with full confidence. Every
failure — no ``log`` binary, a slow log store, a window with no row for this pid — returns a
:class:`Responsible` that says it is unknown *and why*, so the caller can say so rather than
fall back on a plausible name.

**It is bounded, because its caller is, and the two callers have different deadlines.**
``service._run_driver`` gives the driver child
:data:`~personalclaw.computer_use.service.DRIVER_TIMEOUT_SECS` (20s) to answer, and a refusal's
probe runs inside that budget. ``log show``'s cost is the *host's* — measured on one workstation
at 3.5s, 6.7s and 23s within the same hour, and driven by the machine's log archive rather than
by the window asked for. So a refusal gets :data:`PROBE_TIMEOUT_SECS` and no more (a slow log
store must not convert an operator-fixable permission refusal into a driver timeout, which would
lose the very FIX line this module exists to write), while a caller with nothing waiting on it —
the live validators, whose whole job is to record which principal they measured — passes
:data:`PATIENT_PROBE_TIMEOUT_SECS`. One number cannot serve both.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess  # nosec B404 - one fixed-argv read of the OS's own log; see _probe
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: ``/usr/bin/log``, absolutely: ``log`` is also a zsh builtin, and a ``PATH`` lookup for a
#: three-letter name is how a probe ends up reading something else entirely.
_LOG_BINARY = "/usr/bin/log"

#: How far back to look — the same window PR #2572 used by hand. Wide enough that a probe run
#: after the grant check still sees its own row, and narrowing it buys nothing: measured on one
#: host, a one-second window and a two-minute window cost the same, because ``log show``'s time
#: goes on opening the archive rather than on the span asked for.
_WINDOW = "2m"

#: ``tccd``'s own subsystem, and the row that carries the attribution it decided on.
_SUBSYSTEM_PREDICATE = 'subsystem == "com.apple.TCC"'
_ATTRIBUTION = "AUTHREQ_ATTRIBUTION"

#: Longest this probe may take when a *refusal* is waiting on it. Deliberately well under
#: ``service.DRIVER_TIMEOUT_SECS`` (20s): see the module docstring — a slow log store must cost
#: the FIX its detail, never the refusal its code. Not imported from ``service`` on purpose; the
#: driver child must not import the dispatch it is subordinate to.
PROBE_TIMEOUT_SECS = 8.0

#: For a caller with no such deadline — the live validators, whose whole job is to *record* which
#: principal they measured. ``log show`` has a fixed startup cost that scales with the machine's
#: log archive, not with the window asked for: measured at 6.7s for a one-second window with no
#: predicate on one busy host, and over 20s with one. So the refusal's 8s and a validator's
#: provenance line cannot share a number. A validator that reported ``unknown`` because it was in
#: a hurry would be recording exactly the gap it exists to close.
PATIENT_PROBE_TIMEOUT_SECS = 90.0

#: The argv the probe runs. Fixed, with no shell and no interpolation of anything a caller or a
#: model supplied — the predicate and the window are literals in this module.
_PROBE_ARGV = (
    _LOG_BINARY,
    "show",
    "--last",
    _WINDOW,
    "--style",
    "compact",
    "--predicate",
    f'{_SUBSYSTEM_PREDICATE} AND eventMessage CONTAINS "{_ATTRIBUTION}"',
)

#: The same query as a copy-pasteable one-liner, for a refusal's FIX and for an operator who
#: wants to check by hand. Kept beside :data:`_PROBE_ARGV` so the instruction the operator is
#: given cannot drift from the query this module actually runs.
RESPONSIBLE_PROBE_COMMAND = (
    f"log show --last {_WINDOW} --style compact --predicate '{_SUBSYSTEM_PREDICATE}'"
    f" | grep {_ATTRIBUTION} | tail -3"
)

#: ``responsible={TCCDProcess: identifier=…, pid=…, …, responsible_path=…, binary_path=…}``.
#: There is no ``}`` inside the block, so a non-greedy class is enough and no nesting is needed.
_RESPONSIBLE_BLOCK = re.compile(r"responsible=\{([^}]*)\}")


def _field(body: str, key: str) -> str:
    """One ``key=value`` out of a ``TCCDProcess`` block, tolerating spaces in a value.

    The terminator is the *next* ``key=`` rather than the next comma, because an application
    path legitimately contains spaces and can contain a comma (``/Applications/Some, App.app``),
    and a comma-split would silently hand back a truncated path.
    """
    match = re.search(rf"\b{re.escape(key)}=(.*?)(?=, [a-z_]+=|$)", body)
    return match.group(1).strip() if match else ""


@dataclass(frozen=True)
class Responsible:
    """The process macOS attributed this session's Accessibility request to, or why not known.

    Either it is known (``identifier`` and/or ``path`` set) or :attr:`unknown_reason` says what
    stopped the probe. Both empty is not a state this module produces — an answer with no
    content and no reason is exactly the shape that lets a refusal claim it checked.
    """

    identifier: str = ""
    path: str = ""
    unknown_reason: str = ""

    @property
    def known(self) -> bool:
        return bool(self.identifier or self.path)

    def describe(self) -> str:
        """One phrase for a refusal, a validator's JSON, or an operator's eye."""
        if not self.known:
            return f"unknown ({self.unknown_reason or 'no reason recorded'})"
        if self.identifier and self.path:
            return f"{self.identifier} ({self.path})"
        return self.identifier or self.path


#: Cached for the life of the process. Responsibility is assigned when the process is created
#: and does not change under it, so re-probing would spend seconds re-deriving a constant. An
#: *unknown* answer is cached too: a log store that timed out once will time out again, and a
#: refusal path must not pay :data:`PROBE_TIMEOUT_SECS` twice to learn the same nothing.
_CACHED: Responsible | None = None


def reset_cache() -> None:
    """Drop the cached answer. For tests, and for a probe re-run after a grant changes."""
    global _CACHED
    _CACHED = None


def _probe(timeout: float) -> tuple[list[str], str]:
    """The tccd attribution rows from the last :data:`_WINDOW`, or ``([], reason)``.

    Never raises. Every way this can fail is a reason string, because the caller's job is to
    write a FIX line and a probe that raised would take the whole refusal with it.
    """
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv, no shell, no caller input
            list(_PROBE_ARGV),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return [], f"{_LOG_BINARY} is not present on this system"
    except subprocess.TimeoutExpired:
        return [], f"the unified log did not answer within {timeout:.0f}s"
    except OSError as exc:
        logger.debug("tccd responsible-process probe failed", exc_info=True)
        return [], f"the unified log could not be read ({type(exc).__name__})"
    if proc.returncode != 0:
        return [], f"`log show` exited {proc.returncode}"
    return proc.stdout.splitlines(), ""


def _from_lines(lines: list[str], pid: int) -> Responsible:
    """Parse the newest attribution row that names *pid*. Borrows nobody else's row.

    Scoped to *pid* deliberately: on a busy desktop the last ``AUTHREQ_ATTRIBUTION`` row in the
    window usually belongs to some other application, and naming it would send the operator to
    grant something that was never asking.
    """
    marker = f"pid={pid},"
    mine = [line for line in lines if _ATTRIBUTION in line and marker in line]
    if not mine:
        return Responsible(
            unknown_reason=(
                f"no {_ATTRIBUTION} row in the last {_WINDOW} of the unified log names pid {pid}"
            )
        )
    block = _RESPONSIBLE_BLOCK.search(mine[-1])
    if block is None:
        return Responsible(
            unknown_reason=f"tccd's {_ATTRIBUTION} row for pid {pid} named no responsible process"
        )
    body = block.group(1)
    found = Responsible(
        identifier=_field(body, "identifier"), path=_field(body, "responsible_path")
    )
    if not found.known:
        return Responsible(
            unknown_reason=(
                f"tccd's responsible= block for pid {pid} carried neither an identifier nor a path"
            )
        )
    return found


def responsible_process(
    *, refresh: bool = False, timeout: float = PROBE_TIMEOUT_SECS
) -> Responsible:
    """The process macOS resolves THIS session's Accessibility request against.

    Only meaningful after something in this process has actually asked the OS — the row this
    reads is written by ``tccd`` when it answers, so a probe run before any
    ``AXIsProcessTrusted()`` call reports that no row names this pid. That ordering is the
    natural one for every caller: the grant is checked, the answer is no, and the refusal is
    then written with the principal the OS just used.

    ``timeout`` defaults to the refusal's budget. A caller with no deadline — a validator
    recording which principal it measured — passes
    :data:`PATIENT_PROBE_TIMEOUT_SECS`; see that constant for why one number cannot serve both.
    """
    global _CACHED
    if _CACHED is not None and not refresh:
        return _CACHED
    if platform.system() != "Darwin":
        answer = Responsible(unknown_reason=f"TCC does not exist on {platform.system()}")
    else:
        lines, reason = _probe(timeout)
        answer = Responsible(unknown_reason=reason) if reason else _from_lines(lines, os.getpid())
    _CACHED = answer
    return answer
