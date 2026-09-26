"""Provider availability — measured in a child process, served from memory, never on a request.

A provider app may export ``availability() -> (bool, str)``: "can this provider run on THIS
machine?". Settings → Providers shows the answer on every card. The list route used to call
every hook synchronously on the event loop, on every request, and a hook is app code with
unbounded cost. Measured cold in the container: sentence-transformers' hook (it imported
torch) 171.8 s, kiro-cli's 6.8 s, gemini-cli's 5.2 s. ``/api/providers`` and a concurrent
``/api/healthz`` both exceeded 120 s, and the container health check recorded 161 s — long
enough for an orchestrator to restart the gateway.

Moving the call onto a worker thread is not enough, and that was measured too: a thread that
imports sentence-transformers still stalled the loop for 3.8 s (faster-whisper: 4.4 s) —
a C-extension import holds the GIL for seconds at a time — and the library then stays
resident in the gateway (about a gigabyte for torch) to answer one boolean. A thread that
runs past its timeout also can never be stopped.

So the hooks run in a child process, ``personalclaw availability-probe <app>…``, which the
gateway reads line by line and kills at :data:`PROBE_DEADLINE_SECS`:

* the list route only READS this board; an app that was never measured reads ``checking``;
* an answer is served for :data:`AVAILABILITY_TTL_SECS`, after which a read re-measures in
  the background while the previous answer keeps being served;
* ``POST /api/providers/{name}/availability`` re-measures one app now — the facts a hook
  reads change when the user installs a package or signs a CLI in, which is exactly when
  they press the card's "Check again";
* the gateway warms the board once at boot, so a card is rarely seen in ``checking``.

The child is given the gateway's resolved home explicitly (``PERSONALCLAW_HOME``). It must
answer for the home this gateway serves, and a child left to resolve its own would read
whatever an unset variable defaults to — in a test process that is the developer's real home.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: The hidden CLI subcommand the child runs (``cli.HIDDEN_COMMANDS`` declares it).
PROBE_COMMAND = "availability-probe"

#: How long a measured answer is served before a read re-measures it. The facts a hook reads
#: (an installed package, a binary on PATH, a signed-in CLI) change when the user installs or
#: signs in, and the card's "Check again" is how they say so — this is the backstop.
AVAILABILITY_TTL_SECS = 15 * 60.0

#: Wall-clock budget for one probe process. The slowest hook measured (sentence-transformers,
#: importing torch cold in the container) took 171.8 s; the child runs every hook at once and
#: streams each answer as it lands, so this bounds the slowest hook, not their sum.
PROBE_DEADLINE_SECS = 180.0

#: The four states a card can be in. ``unknown`` is "the check itself failed or never
#: finished" — distinct from ``unavailable``, which is the app's own measured "no".
CHECKING = "checking"
AVAILABLE = "available"
UNAVAILABLE = "unavailable"
UNKNOWN = "unknown"
STATES = frozenset({CHECKING, AVAILABLE, UNAVAILABLE, UNKNOWN})

#: Shown when an app reports itself unavailable without saying why.
UNAVAILABLE_WITHOUT_REASON = "Its app reports that it can't run on this machine."

_Key = tuple[str, str]  # (app name, provider implementation path)


@dataclass(frozen=True)
class Availability:
    """One provider record's answer. ``checked_at`` is epoch seconds, ``None`` until measured."""

    state: str
    reason: str = ""
    checked_at: float | None = None

    def to_wire(self) -> dict[str, Any]:
        return {"state": self.state, "reason": self.reason, "checkedAt": self.checked_at}


_CHECKING = Availability(CHECKING)


def probe_argv(names: list[str]) -> list[str]:
    """The child's argv: this install's own CLI, running the hidden probe subcommand.

    ``sys.executable -m personalclaw`` rather than a console script on PATH — a gateway run
    from a venv whose ``bin`` is not on PATH would otherwise probe a DIFFERENT install. A
    frozen (PyInstaller) bundle has no ``-m``: its executable IS the CLI.
    """
    from personalclaw.self_update import is_frozen

    head = [sys.executable] if is_frozen() else [sys.executable, "-m", "personalclaw"]
    return [*head, PROBE_COMMAND, *names]


def parse_answer(line: bytes | str) -> tuple[str, str | None, Availability] | None:
    """One protocol line → ``(name, implementation, answer)``; ``None`` for anything else.

    ``implementation`` is ``None`` for an answer about the whole app (the child could not
    find it). A malformed line is dropped rather than trusted: the channel is app-adjacent.
    """
    try:
        raw = json.loads(line)
    except (TypeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    state = raw.get("state")
    impl = raw.get("implementation")
    if not isinstance(name, str) or not name or state not in STATES or state == CHECKING:
        return None
    if impl is not None and not isinstance(impl, str):
        return None
    reason = raw.get("reason")
    return name, impl, Availability(str(state), str(reason or "")[:500], time.time())


class AvailabilityBoard:
    """The gateway's view of every provider's availability. Reads never block."""

    def __init__(
        self,
        *,
        ttl_secs: float = AVAILABILITY_TTL_SECS,
        deadline_secs: float = PROBE_DEADLINE_SECS,
        argv_for: Any = probe_argv,
    ) -> None:
        self._ttl = ttl_secs
        self._deadline = deadline_secs
        self._argv_for = argv_for
        self._answers: dict[_Key, Availability] = {}
        self._measured_at: dict[_Key, float] = {}  # monotonic
        self._probed_at: dict[str, float] = {}  # monotonic, per app name
        # An answer about a whole app (the child could not find it) — applies to every
        # record of the app, including ones no read has asked about yet.
        self._whole_app: dict[str, Availability] = {}
        self._keys_by_name: dict[str, set[_Key]] = {}
        self._pending: set[str] = set()
        self._inflight: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._proc: asyncio.subprocess.Process | None = None

    # ── reads ───────────────────────────────────────────────────────────

    def read(self, name: str, implementation: str) -> Availability:
        """The current answer for one provider record, scheduling a measurement if needed."""
        key = (name, implementation)
        self._keys_by_name.setdefault(name, set()).add(key)
        answer = self._answers.get(key)
        if answer is None:
            probed = self._probed_at.get(name)
            if probed is not None and time.monotonic() - probed < self._ttl:
                # Measured, and the child said nothing about this record. Asking again right
                # away would loop forever on an app whose child never answers for it.
                return self._whole_app.get(name) or Availability(
                    UNKNOWN, "The availability check returned no answer for this provider."
                )
            self._schedule([name])
            return _CHECKING
        if answer.state != CHECKING and self._is_stale(key):
            self._schedule([name])
        return answer

    def _is_stale(self, key: _Key) -> bool:
        measured = self._measured_at.get(key)
        return measured is None or time.monotonic() - measured >= self._ttl

    # ── writes ──────────────────────────────────────────────────────────

    def recheck(self, name: str) -> None:
        """Re-measure one app now: its cards read ``checking`` until the new answer lands."""
        for key in self._keys_by_name.get(name, set()):
            self._answers[key] = _CHECKING
            self._measured_at.pop(key, None)
        self._probed_at.pop(name, None)
        self._whole_app.pop(name, None)
        self._schedule([name], force=True)

    def warm(self, names: list[str]) -> None:
        """Measure these apps in the background (the gateway's boot call)."""
        self._schedule(names)

    async def shutdown(self) -> None:
        """Stop measuring: cancel the drain and kill a running child."""
        task, self._task = self._task, None
        self._pending.clear()
        proc = self._proc
        if proc is not None:
            await _terminate(proc)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def _schedule(self, names: list[str], *, force: bool = False) -> None:
        wanted = {n for n in names if n and (force or n not in self._inflight)}
        if not wanted:
            return
        self._pending |= wanted
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop to run on; the next read from an async caller schedules it
        # A drain stranded on a loop that has since closed never reports done — only a live
        # one on THIS loop is still going to pick the new names up.
        if self._task is None or self._task.done() or self._task.get_loop() is not loop:
            self._inflight = set()
            self._task = loop.create_task(self._drain())

    async def _drain(self) -> None:
        while self._pending:
            batch = sorted(self._pending)
            self._pending.clear()
            self._inflight = set(batch)
            try:
                await self._probe(batch)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a failed batch must not end the drain loop
                logger.warning("provider availability probe failed", exc_info=True)
                self._settle(batch, set(), "The availability check failed to run.")
            finally:
                self._inflight = set()

    async def _probe(self, names: list[str]) -> None:
        from personalclaw.config.loader import config_dir

        env = {**os.environ, "PERSONALCLAW_HOME": str(config_dir())}
        reported: set[_Key] = set()
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._argv_for(names),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                # Its own process group, so the kill at the deadline also reaches whatever a
                # hook spawned (kiro/gemini's resolver runs ``npm root -g``).
                start_new_session=True,
            )
        except OSError as exc:
            self._settle(names, reported, f"The availability check could not start ({exc}).")
            return
        self._proc = proc
        assert proc.stdout is not None and proc.stderr is not None
        stderr_tail = asyncio.ensure_future(_tail(proc.stderr))
        timed_out = False
        try:
            async with asyncio.timeout(self._deadline):
                while line := await proc.stdout.readline():
                    parsed = parse_answer(line)
                    if parsed is None:
                        continue
                    name, impl, answer = parsed
                    reported |= self._record(name, impl, answer)
        except TimeoutError:
            timed_out = True
        finally:
            await _terminate(proc)
            self._proc = None
        tail = await stderr_tail
        if timed_out:
            reason = f"The availability check did not finish within {self._deadline:.0f} s."
        else:
            reason = "The availability check ended without an answer for this provider."
            if proc.returncode not in (0, None) and tail:
                logger.warning(
                    "availability probe exited %s: %s", proc.returncode, tail.strip()[-600:]
                )
        self._settle(names, reported, reason)

    def _record(self, name: str, impl: str | None, answer: Availability) -> set[_Key]:
        if impl is None:
            self._whole_app[name] = answer
        keys = {(name, impl)} if impl is not None else set(self._keys_by_name.get(name, set()))
        now = time.monotonic()
        for key in keys:
            self._keys_by_name.setdefault(name, set()).add(key)
            self._answers[key] = answer
            self._measured_at[key] = now
        return keys

    def _settle(self, names: list[str], reported: set[_Key], reason: str) -> None:
        """Close a batch: every record it did not answer becomes ``unknown`` with ``reason``."""
        now = time.monotonic()
        for name in names:
            self._probed_at[name] = now
            for key in self._keys_by_name.get(name, set()):
                if key in reported:
                    continue
                self._answers[key] = Availability(UNKNOWN, reason, time.time())
                self._measured_at[key] = now


async def _tail(stream: asyncio.StreamReader, limit: int = 4000) -> str:
    """Drain ``stream`` to EOF (a full pipe would wedge the child) and keep its last bytes."""
    kept = b""
    with contextlib.suppress(Exception):
        while chunk := await stream.read(65536):
            kept = (kept + chunk)[-limit:]
    return kept.decode("utf-8", errors="replace")


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Kill the child's whole process group if it is still running, then reap it."""
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=5)


_board: AvailabilityBoard | None = None


def get_availability_board() -> AvailabilityBoard:
    """The process-wide board the list route reads and the boot warm fills."""
    global _board
    if _board is None:
        _board = AvailabilityBoard()
    return _board


def reset_availability_board() -> None:
    """Forget every answer (tests; a board is otherwise process-lifetime)."""
    global _board
    _board = None


__all__ = [
    "AVAILABLE",
    "AVAILABILITY_TTL_SECS",
    "Availability",
    "AvailabilityBoard",
    "CHECKING",
    "PROBE_COMMAND",
    "PROBE_DEADLINE_SECS",
    "UNAVAILABLE",
    "UNAVAILABLE_WITHOUT_REASON",
    "UNKNOWN",
    "get_availability_board",
    "parse_answer",
    "probe_argv",
    "reset_availability_board",
]
