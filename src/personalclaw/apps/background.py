"""The contract an app's supervised background worker implements (APE-3, contract half).

An app may declare the ``backgroundTasks`` permission (``apps/manifest.py``) to register a
long-lived worker — richer than ``cron``, which is N discrete agent runs on a clock. Until
this module existed the permission granted nothing because, as ``manifest.py`` says of it,
"whose host still does not exist". This is the half of that host an app **writes against**:
the worker shape, the cooperative control protocol, and the env handshake the child reads.
Supervision (spawn, watchdog restart, PPID reaping, budget accounting) is the parent-side
half and lives in the runtime, not here.

Process model — deliberately the same shape as an app **backend**
================================================================
``backend_runtime._launch_cmd`` starts a child as ``[sys.executable, <entry script>]`` — a
*script path*, not an imported symbol — and hands the child its whole context through
**environment variables** ("so it never guesses a path relative to ``__file__``"). A worker
follows that convention exactly rather than inventing a second one: the app ships an entry
script, and the script's ``__main__`` block hands its worker object to :func:`run_worker`::

    # apps/my-app/worker.py
    from personalclaw.sdk.background import BackgroundWorker, run_worker

    class Poller(BackgroundWorker):
        poll_interval = 60.0

        def run_once(self, ctx):
            (ctx.data_dir / "last-run").write_text(str(time.time()))

    if __name__ == "__main__":
        run_worker(Poller())

So the worker is **a unit-of-work object driven by a loop the SDK owns**, not a bare
``async def run()`` the host awaits and not a free-form script that owns its own ``while
True``. The choice is forced by the two done-when clauses: a host that must be able to STOP
and to PAUSE a worker needs a point in time where the worker is between units of work and
the control state can be consulted. ``run_once`` *is* that point. A worker that owned its
own loop would have to be trusted to poll — see "an uncooperative worker" below.

Stop and pause are DIFFERENT states, not one flag
=================================================
* **stop** is terminal. The app was disabled or uninstalled, or the gateway is shutting
  down. The worker finishes the unit of work it is in, the driver returns, the process
  exits. :meth:`WorkerControl.resume` deliberately cannot undo it.
* **pause** is resumable. The app breached a budget (or an operator paused it, or the host
  is degraded). No new unit of work starts, the process stays alive, and a later
  :meth:`WorkerControl.resume` continues where it left off.

Collapsing the two into one boolean is the failure this module exists to prevent: a paused
worker that reports itself stopped is a worker the host will not resume, and a stopping
worker that reports itself paused is an orphan.

An uncooperative worker
=======================
Cooperation is how a worker keeps its in-flight unit of work; it is **not** what makes the
host correct. A worker that never calls :meth:`WorkerControl.should_stop` still yields
control at the end of each ``run_once`` because the *driver*, not the worker, owns the loop
condition. A worker whose ``run_once`` never returns at all is escalated by the parent-side
supervisor on the existing precedent for app backends — SIGTERM, then SIGKILL after
``backend_runtime._TERM_TIMEOUT`` (5 seconds). It loses whatever it was mid-way through,
which is exactly the cost the cooperative path buys off.

The permission handshake
========================
This module invents **no** permission surface. ``backgroundTasks`` (declared at install,
consented in the Store) stays the only gate, and it is checked **parent-side**, by the
host, before a child is ever spawned. What the child does is *fail closed on the absence of
evidence that the check happened*: the host sets :data:`WORKER_GRANT_ENV` to the exact
permission name it verified, and :meth:`WorkerContext.from_env` refuses to build a context
without it. So ``python worker.py`` by hand, or an app backend trying to self-start a second
worker, raises :class:`WorkerContractError` instead of quietly running unsupervised work.

That handshake is a *contract* check, not a sandbox: an env var is forgeable by the app
itself, and an app that spawns its own process was always able to. Confinement remains the
sandbox's job (``sandbox.build_child_env`` / ``spawn_shim_argv``, as for a backend). What
the handshake actually guarantees is that no worker runs *believing it is supervised* when
it is not.

Import closure: stdlib only, on purpose. A contract an app imports must not drag the host
in — nothing here imports the gateway, the dashboard, or ``backend_runtime``.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

# The manifest permission that gates a worker at install consent. Named here so the
# contract and the host cannot drift to two spellings of the same gate.
BACKGROUND_TASKS_PERMISSION = "backgroundTasks"

# --- the env handshake (mirrors backend_runtime's four computed variables) ------------
# Reused verbatim from the backend contract: one app, one name, one data dir.
WORKER_APP_ENV = "PERSONALCLAW_APP_NAME"
WORKER_DATA_DIR_ENV = "PERSONALCLAW_APP_DATA_DIR"
# Which of the app's declared workers this child is (an app may register more than one).
WORKER_ID_ENV = "PERSONALCLAW_APP_WORKER"
# The host writes the permission name it verified. Fail-closed: no grant, no context.
WORKER_GRANT_ENV = "PERSONALCLAW_APP_WORKER_GRANT"


#: One worker per app, by convention, resolved relative to the app directory.
#:
#: ``manifest.py``'s own comment on the permission says an app "may register **a**
#: long-lived supervised worker" — singular, gated by a boolean. So there is deliberately no
#: manifest list of worker specs to parse: the permission is the declaration and this is the
#: filename it implies. An app that grants ``backgroundTasks`` and ships no ``worker.py``
#: simply has no worker (the supervisor logs the missing entry and starts nothing), which is
#: a better failure than a manifest field that can disagree with the files on disk.
WORKER_ENTRY_POINT = "worker.py"

#: The single worker's declared name. One name, so the env handshake, the SEL rows and the
#: supervisor's table all say the same thing about the same process.
WORKER_DEFAULT_NAME = "worker"


# Fallback cadence when a worker declares none. Long on purpose: a background worker that
# wants to run hot should say so, rather than inheriting a hot default by omission.
DEFAULT_POLL_INTERVAL = 30.0


class WorkerContractError(RuntimeError):
    """The worker or its environment does not satisfy this contract.

    Raised eagerly and with the specific missing piece named, so an app author sees the
    contract violation instead of an ``AttributeError`` from deep inside a running loop.
    """


class WorkerState(str, Enum):
    """The four states a supervised worker can be in.

    ``STOPPING`` and ``STOPPED`` are distinct because "asked to stop" and "no longer
    running" are different facts to the supervisor: the first is the graceful window in
    which the current unit of work is still finishing, the second is when reaping is safe.
    """

    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"


class PauseReason(str, Enum):
    """Why a worker was paused. Every value is resumable — that is what pause means."""

    BUDGET = "budget"  # APE-3 done-when: a budget breach pauses the worker + notifies
    OPERATOR = "operator"  # a human paused it from the Apps surface
    DEGRADED = "degraded"  # the host is degraded and is shedding background load


class StopReason(str, Enum):
    """Why a worker was stopped. Every value is terminal — that is what stop means.

    There is deliberately no ``UNINSTALLED``: from the CHILD's vantage an uninstall is a
    disable that never comes back, and the part that differs — reaping the process so no
    orphan survives (APE-3 V1) — happens parent-side, where the PID is. A vocabulary the
    child cannot act on differently would be a distinction it only reports, not one it uses.
    """

    DISABLED = "disabled"  # APE-3 done-when: the app was disabled (or uninstalled)
    SHUTDOWN = "shutdown"  # the gateway is going away
    ERROR = "error"  # the supervisor gave up on this worker


class WorkerControl:
    """The cooperative stop/pause channel between the host and a running worker.

    Thread-safe and signal-safe: the flags are :class:`threading.Event`s, so a SIGTERM
    handler can set them while ``run_once`` is mid-flight on the main thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._resumed = threading.Event()
        self._resumed.set()  # not paused
        self._exited = False
        self._stop_reason: StopReason | None = None
        self._pause_reason: PauseReason | None = None

    # -- state ------------------------------------------------------------
    @property
    def state(self) -> WorkerState:
        with self._lock:
            if self._exited:
                return WorkerState.STOPPED
            if self._stop.is_set():
                return WorkerState.STOPPING
            if not self._resumed.is_set():
                return WorkerState.PAUSED
            return WorkerState.RUNNING

    @property
    def stop_reason(self) -> StopReason | None:
        with self._lock:
            return self._stop_reason

    @property
    def pause_reason(self) -> PauseReason | None:
        with self._lock:
            return self._pause_reason

    # -- what the worker asks ---------------------------------------------
    def should_stop(self) -> bool:
        """True once a stop has been requested. A long ``run_once`` polls this and
        returns early to keep its work in a consistent state."""
        return self._stop.is_set()

    def is_paused(self) -> bool:
        return not self._resumed.is_set() and not self._stop.is_set()

    def wait(self, timeout: float) -> bool:
        """Sleep up to ``timeout`` seconds, waking IMMEDIATELY on a stop request.

        A worker uses this instead of ``time.sleep`` so that "stops on disable" does not
        mean "stops up to one poll interval after disable". Returns True if it should stop.
        """
        if timeout > 0:
            self._stop.wait(timeout)
        return self._stop.is_set()

    # -- what the host asks -----------------------------------------------
    def request_stop(self, reason: StopReason = StopReason.SHUTDOWN) -> None:
        """Ask the worker to finish its current unit of work and exit. Terminal.

        Also releases a paused worker, so "pause on budget breach, then disable the app"
        cannot wedge a process that is blocked waiting to be resumed.
        """
        with self._lock:
            if self._stop_reason is None:
                self._stop_reason = reason
        self._stop.set()
        self._resumed.set()

    def pause(self, reason: PauseReason = PauseReason.OPERATOR) -> None:
        """Ask the worker to start no further unit of work, but stay alive. Resumable.

        A no-op once a stop has been requested: stop wins, because reviving a stopping
        worker into a paused-but-alive one is how an uninstall leaves an orphan.
        """
        with self._lock:
            if self._stop.is_set():
                return
            self._pause_reason = reason
        self._resumed.clear()

    def resume(self) -> None:
        """Let a paused worker continue. Deliberately CANNOT undo a stop."""
        with self._lock:
            if self._stop.is_set():
                return
            self._pause_reason = None
        self._resumed.set()

    def wait_while_paused(self, poll: float = 0.5) -> bool:
        """Block while paused; return True if the worker should now stop.

        Bounded waits rather than one unbounded one so a stop that arrives during a pause
        is observed promptly even if the resume event is never set.
        """
        while not self._resumed.wait(poll):
            if self._stop.is_set():
                return True
        return self._stop.is_set()

    # -- driver-owned ------------------------------------------------------
    def mark_exited(self) -> None:
        """Record that the driver loop has returned. STOPPING becomes STOPPED here."""
        with self._lock:
            self._exited = True


@dataclass(frozen=True)
class WorkerContext:
    """The identity and environment handed to a worker, built from the host's env handoff.

    Carries the app NAME rather than any capability object: the worker runs under its app's
    declared permissions, checked host-side, and must not be able to widen them from inside.
    """

    app_name: str
    worker_id: str
    control: WorkerControl
    data_dir: Path | None = None
    env: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def granted_permission(self) -> str:
        """The permission the host verified before spawning this worker."""
        return BACKGROUND_TASKS_PERMISSION

    def should_stop(self) -> bool:
        return self.control.should_stop()

    def sleep(self, seconds: float) -> bool:
        """Stop-interruptible sleep. Returns True if the worker should stop."""
        return self.control.wait(seconds)

    @classmethod
    def from_env(
        cls,
        env: dict[str, str] | None = None,
        control: WorkerControl | None = None,
    ) -> "WorkerContext":
        """Build a context from the host's env handoff, or fail closed.

        Every failure names the missing variable, because the app author debugging it is
        looking at a child process whose stdout the supervisor discards.
        """
        src = dict(os.environ if env is None else env)
        grant = src.get(WORKER_GRANT_ENV, "").strip()
        if grant != BACKGROUND_TASKS_PERMISSION:
            raise WorkerContractError(
                f"{WORKER_GRANT_ENV} is {grant!r}, expected {BACKGROUND_TASKS_PERMISSION!r}: a "
                "background worker runs only when the host has verified the app's "
                f"{BACKGROUND_TASKS_PERMISSION!r} permission and spawned it. Enable the app and "
                "let the supervisor start the worker; do not run this script directly."
            )
        app_name = src.get(WORKER_APP_ENV, "").strip()
        if not app_name:
            raise WorkerContractError(
                f"{WORKER_APP_ENV} is empty: a worker must know which app it belongs to."
            )
        worker_id = src.get(WORKER_ID_ENV, "").strip() or "default"
        raw_dir = src.get(WORKER_DATA_DIR_ENV, "").strip()
        # Absent is legitimate and means exactly one thing: the app did not declare the
        # ``storage`` permission, so it has no sanctioned place to persist (the same gate
        # backend_runtime applies). The worker gets ``None``, not a guessed path.
        data_dir = Path(raw_dir) if raw_dir else None
        return cls(
            app_name=app_name,
            worker_id=worker_id,
            control=control or WorkerControl(),
            data_dir=data_dir,
            env=src,
        )


class BackgroundWorker(ABC):
    """What an app implements to run supervised background work.

    One method is required: :meth:`run_once`, ONE unit of work. The loop, the cadence, the
    pause window and the stop check belong to :func:`run_worker` — a worker that owned its
    own ``while True`` could only be stopped by being killed.
    """

    #: Seconds between units of work. Override; the default is deliberately unhurried.
    poll_interval: float = DEFAULT_POLL_INTERVAL

    @abstractmethod
    def run_once(self, ctx: WorkerContext) -> None:
        """Do ONE unit of work, then return.

        Keep it short enough that returning is a real stop signal. If it cannot be short,
        poll ``ctx.should_stop()`` inside and return early when it goes True.
        """

    def setup(self, ctx: WorkerContext) -> None:
        """Called once before the first unit of work. Default: nothing."""

    def teardown(self, ctx: WorkerContext) -> None:
        """Called once after the loop exits, including after a stop. Default: nothing.

        Runs on the graceful path only — a worker SIGKILLed for ignoring the stop request
        never reaches it, which is the other half of the cost of not cooperating.
        """

    def on_pause(self, ctx: WorkerContext, reason: PauseReason | None) -> None:
        """Called when the worker enters the paused state. Default: nothing."""

    def on_resume(self, ctx: WorkerContext) -> None:
        """Called when a paused worker is resumed. Default: nothing."""


def _install_signal_handlers(control: WorkerControl) -> None:
    """Translate the parent's signals into control-state changes, child-side.

    SIGTERM is the supervisor's graceful ask (it escalates to SIGKILL after
    ``_TERM_TIMEOUT``); SIGUSR1/SIGUSR2 are pause/resume, so a budget breach does not have
    to kill a process it intends to bring back. Best-effort: a worker driven from a
    non-main thread (or on a platform without SIGUSR*) simply keeps the API-only path.
    """
    try:
        signal.signal(signal.SIGTERM, lambda *_: control.request_stop(StopReason.SHUTDOWN))
        signal.signal(signal.SIGINT, lambda *_: control.request_stop(StopReason.SHUTDOWN))
        if hasattr(signal, "SIGUSR1"):
            signal.signal(signal.SIGUSR1, lambda *_: control.pause(PauseReason.BUDGET))
        if hasattr(signal, "SIGUSR2"):
            signal.signal(signal.SIGUSR2, lambda *_: control.resume())
    except (ValueError, OSError) as exc:  # not the main thread, or unsupported platform
        logger.debug("worker signal handlers not installed: %s", exc)


def run_worker(
    worker: BackgroundWorker,
    ctx: WorkerContext | None = None,
    *,
    install_signals: bool = True,
) -> WorkerState:
    """Drive ``worker`` until it is stopped. The app's entry script calls this.

    The loop, not the worker, owns the exit condition — which is what makes stop work for a
    worker that never polls. Returns the terminal :class:`WorkerState`.
    """
    if not isinstance(worker, BackgroundWorker):
        raise WorkerContractError(
            f"{type(worker).__name__} is not a BackgroundWorker: a background worker must "
            f"subclass personalclaw.sdk.background.BackgroundWorker and implement "
            f"run_once(ctx). Got {type(worker).__name__} with "
            f"run_once={'present' if hasattr(worker, 'run_once') else 'MISSING'}."
        )
    if ctx is None:
        ctx = WorkerContext.from_env()
    control = ctx.control
    if install_signals:
        _install_signal_handlers(control)

    try:
        worker.setup(ctx)
        while not control.should_stop():
            if control.is_paused():
                worker.on_pause(ctx, control.pause_reason)
                if control.wait_while_paused():
                    break
                worker.on_resume(ctx)
                continue
            try:
                worker.run_once(ctx)
            except Exception:
                # A worker that raises is a CRASH, and crash recovery is the supervisor's
                # watchdog, not a swallowed exception here: log it and let the process die
                # so the restart is visible and counted.
                logger.exception(
                    "app %s worker %s raised; exiting for the supervisor to restart",
                    ctx.app_name,
                    ctx.worker_id,
                )
                raise
            interval = max(0.0, float(getattr(worker, "poll_interval", DEFAULT_POLL_INTERVAL)))
            if control.wait(interval):
                break
        worker.teardown(ctx)
    finally:
        control.mark_exited()
    return control.state


__all__ = [
    "BACKGROUND_TASKS_PERMISSION",
    "DEFAULT_POLL_INTERVAL",
    "WORKER_APP_ENV",
    "WORKER_DATA_DIR_ENV",
    "WORKER_GRANT_ENV",
    "WORKER_ID_ENV",
    "BackgroundWorker",
    "PauseReason",
    "StopReason",
    "WorkerContext",
    "WorkerContractError",
    "WorkerControl",
    "WorkerState",
    "WORKER_DEFAULT_NAME",
    "WORKER_ENTRY_POINT",
    "run_worker",
]
