"""Sidecar isolation for local-model providers.

A local-model provider that wraps a crash-prone native stack (the loky segfault that
left the embedding store unsearchable is the motivating case) can declare
``provider.execution: "sidecar"`` in its manifest. Core then runs its heavy work in a
**child process with its own venv**, so a native-lib crash kills the child and raises a
typed :class:`SidecarCrashed` in the caller instead of taking the gateway down with it.

Four things live here:

:class:`SidecarRunner`
    The supervisor: a dedicated venv at ``~/.personalclaw/apps/{app}/venv/``, one child
    speaking the newline-JSON protocol of :mod:`._sidecar_child` (five verbs), a
    **process-generation counter**, a restart budget, and an inspectable watchdog.

:func:`run_once`
    One call in a child of its own, which exits once it has answered: for native inference
    that holds the interpreter lock, which in any thread of the gateway stops its event loop.
    The same child harness and protocol, the same interpreter and environment rule; no
    supervision, because nothing outlives the call, and a cancelled call kills the child.

:class:`SidecarInstall`
    The resumable install job: venv → pip deps → weights, each step
    existence-checked before it does work, so an install killed halfway re-runs from
    where it died. Driven by the existing download-job registry, not a second one.

The registry of live runners (:func:`register_runner` / :func:`sweep_sidecars`)
    So the memory-pressure surface can enumerate children and one watchdog sweep can
    revive them all.

**Why generations exist.** Request ids restart at 1 in every child, so a zombie's late
reply for ``3`` could otherwise satisfy a *new* child's request ``3`` — the caller would
believe a dead process. Every id is therefore ``"<generation>:<seq>"`` and
:meth:`SidecarRunner.deliver` FENCES any frame whose generation is not the current one,
counting it in :attr:`SidecarRunner.stale_replies`. That fence is the whole reason the
counter exists.

**Why a partial line is refused.** A child killed mid-write leaves a truncated frame in
the pipe. :meth:`SidecarRunner.deliver` never sees it: the reader drops any final line
that lacks its newline, so a half-written result can never be read as a complete one.
The caller gets ``SidecarCrashed`` instead — an honest failure rather than a plausible
half-answer.

Isolation claim, stated precisely: a sidecar is a **crash and dependency** boundary (its
own process, its own venv, its own OOM-first bias via the ``tool`` ceiling profile). It
is NOT a security sandbox — the child runs with the same user and the same network
access as the gateway.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personalclaw import app_code
from personalclaw.atomic_write import atomic_write
from personalclaw.child_output import (
    STDERR,
    STDOUT,
    ChildOutput,
    LineSplitter,
    bound_line,
    mask_line,
    relay,
)
from personalclaw.periodic_sweep import PeriodicSweep
from personalclaw.security import mask_child_output

logger = logging.getLogger(__name__)

#: Seconds to wait for a child to exit after SIGTERM before SIGKILL.
_TERM_TIMEOUT = 5.0

#: Default per-call ceiling. Generous because a first ``load`` pages a model in from
#: disk; the caller can pass its own for a hot path.
_DEFAULT_CALL_TIMEOUT = 120.0

#: How many lines of an install's output to retain for its status. Bounded — pip's output for a
#: large engine must not grow the heap.
_LOG_TAIL_MAX = 40

#: The venv marker file. Its presence means CORE created this venv, so core may delete
#: it; a user-supplied venv has no marker and is never removed.
_MARKER = ".personalclaw-sidecar.json"

#: Sentinel the reader thread enqueues when the child's stdout reaches EOF.
_EOF = {"__eof__": True}

#: The child harness. Executed by path in the child, so it never needs the core package.
_CHILD_HARNESS = Path(__file__).with_name("_sidecar_child.py")


class SidecarCrashed(RuntimeError):
    """A sidecar child died (or hung) instead of answering.

    ``reason`` is the typed, machine-readable string the FE translates:
    ``signal_11`` (the segfault class), ``exit_1``, ``timeout``, ``eof``,
    ``spawn_failed``, ``restart_budget_exhausted``. :attr:`typed_reason` prefixes it with
    the vocabulary's namespace (``sidecar_crashed:signal_11``) for the wire.
    """

    def __init__(self, reason: str, *, generation: int = 0, detail: str = "") -> None:
        self.reason = reason
        self.generation = generation
        self.detail = detail
        message = f"sidecar crashed (generation {generation}): {reason}"
        super().__init__(f"{message} — {detail}" if detail else message)

    @property
    def typed_reason(self) -> str:
        return f"sidecar_crashed:{self.reason}"


def sidecar_venv_dir(app: str) -> Path:
    """The dedicated venv for app *app* — ``~/.personalclaw/apps/{app}/venv``.

    Deliberately NOT ``<home>/app-python``, where every other app's
    ``dependencies.pythonDependencies`` land: that directory is loaded into the gateway's
    own process, so all apps share one version of each package. A sidecar app's deps are
    only ever imported by its own child process, so it gets an interpreter of its own.

    Inside the app's folder, so an update carries it to the new version with the app's
    ``data/`` (``apps/app_manager.update``) and a removal takes it with the app's files.
    """
    from personalclaw.apps.manager import APP_VENV_DIRNAME, app_dir

    return app_dir(app) / APP_VENV_DIRNAME


def venv_python(venv: Path) -> Path:
    """The interpreter inside *venv* (``Scripts\\python.exe`` on Windows)."""
    if os.name == "nt":  # pragma: no cover — POSIX is the tested path
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


#: What the desktop app says it cannot do when an engine would start or install
#: (``python_children.refusal``): its bundle has no interpreter for one, and does not carry the
#: child harness, which an engine runs by path.
ENGINE_REFUSAL = "install or start this app's engine in a Python environment of its own"


def _refuse_in_the_desktop_app(generation: int) -> None:
    """Refuse an engine child before it starts when this install has no interpreter for it."""
    from personalclaw import python_children

    if not python_children.available():
        raise SidecarCrashed(
            "spawn_failed", generation=generation, detail=python_children.refusal(ENGINE_REFUSAL)
        )


def _child_python(venv: Path) -> Path:
    """The interpreter a child runs under: *venv*'s when it has one, else the gateway's own.

    A provider whose packages are the ones apps declare (``<home>/app-python``) has no venv of its
    own, and runs under the gateway's interpreter with those packages loaded as the gateway loads
    them (:func:`_sidecar_child_env`)."""
    candidate = venv_python(venv)
    return candidate if candidate.is_file() else Path(sys.executable)


def _sidecar_child_env(python: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment of a child started under *python*: the child allowlist, never the
    gateway's own environment and the secrets in it, with *extra* over it.

    Under the gateway's interpreter the child also gets the packages apps declare, which the child
    harness appends to its import path, after the interpreter's own, exactly as the gateway does
    (``_sidecar_child.APP_PYTHON_PATH_ENV``). They are built for that interpreter, so a child in
    an app's own venv gets none of them."""
    from personalclaw.sandbox import build_child_env

    layered = dict(extra or {})
    if python == Path(sys.executable):
        from personalclaw.apps import app_python

        layered = {**app_python.child_env(), **layered}
    return build_child_env(site="model-sidecar", extra=layered)


def _restart_max_default() -> int:
    """``local_models.sidecar_restart_max`` from config, fail-open to 3.

    Fail-open matters: a broken config must not turn into "no sidecar may ever restart",
    which would make one crash permanent.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return max(0, int(AppConfig.load().local_models.sidecar_restart_max))
    except Exception:
        logger.debug("sidecar_restart_max fell back to the default", exc_info=True)
        return 3


@dataclass
class _Child:
    """One spawned generation: its process, its reader thread, its reply queue, and what it
    prints that is not a frame (relayed into the gateway's log, its last lines kept)."""

    proc: subprocess.Popen
    generation: int
    output: ChildOutput
    replies: queue.Queue = field(default_factory=queue.Queue)
    reader: threading.Thread | None = None

    def is_alive(self) -> bool:
        return self.proc.poll() is None


class SidecarRunner:
    """Supervises one app's sidecar child: spawn, protocol, generations, watchdog.

    Synchronous by design (blocking pipes + a reader thread), with :meth:`acall` for the
    gateway's event loop. A sidecar's calls are already serialized — the embedding
    re-index that motivated this runs one encode at a time — so one child, one in-flight
    call, and a lock is the honest model rather than a pool that pretends otherwise.
    """

    def __init__(
        self,
        *,
        app: str,
        worker: Path | str,
        venv: Path | None = None,
        python: Path | str | None = None,
        restart_max: int | None = None,
        call_timeout: float = _DEFAULT_CALL_TIMEOUT,
        env_extra: dict[str, str] | None = None,
    ) -> None:
        self.app = app
        self.worker = Path(worker)
        self.venv = venv if venv is not None else sidecar_venv_dir(app)
        self._python_override = Path(python) if python else None
        self.restart_max = _restart_max_default() if restart_max is None else int(restart_max)
        self.call_timeout = float(call_timeout)
        self._env_extra = dict(env_extra or {})
        self._child: _Child | None = None
        self._generation = 0
        self._seq = 0
        self._restarts = 0
        self._consecutive_failures = 0
        self._stale_replies = 0
        self._last_stat: dict[str, Any] = {}
        self._last_reason = ""
        #: The latest child's output, kept after it dies: what the crash it raised was about.
        self._output: ChildOutput | None = None
        self._lock = threading.Lock()

    # ── inspection ────────────────────────────────────────────────────────────

    @property
    def generation(self) -> int:
        """How many children have been spawned. Every request id carries it."""
        return self._generation

    @property
    def restarts(self) -> int:
        """Total respawns after a death (observability, not the budget)."""
        return self._restarts

    @property
    def stale_replies(self) -> int:
        """Frames fenced for arriving from a superseded generation."""
        return self._stale_replies

    @property
    def last_stat(self) -> dict[str, Any]:
        """The most recent child-reported stat frame (``rss_mb``/``pid``), or ``{}``."""
        return dict(self._last_stat)

    @property
    def log_tail(self) -> list[str]:
        """The last lines the latest child wrote that are not frames, masked
        (``child_output.ChildOutput.lines``)."""
        return self._output.lines() if self._output is not None else []

    def is_alive(self) -> bool:
        return self._child is not None and self._child.is_alive()

    def python_executable(self) -> Path:
        """The interpreter the child runs under.

        The dedicated venv when it exists, else the gateway's own interpreter — a
        provider whose deps happen to be importable in core still works, it just isn't
        dependency-isolated. Honest degradation beats refusing to run.
        """
        if self._python_override is not None:
            return self._python_override
        return _child_python(self.venv)

    def health(self) -> dict[str, Any]:
        """The runner's state as data — what the watchdog decided and why.

        A watchdog whose decision is only visible as a side effect is untestable, so
        every field the sweep keys on is readable here.
        """
        return {
            "app": self.app,
            "alive": self.is_alive(),
            "generation": self._generation,
            "pid": self._child.proc.pid if self._child is not None else 0,
            "restarts": self._restarts,
            "consecutive_failures": self._consecutive_failures,
            "restart_max": self.restart_max,
            "budget_exhausted": self._consecutive_failures > self.restart_max,
            "stale_replies": self._stale_replies,
            "last_reason": self._last_reason,
            "rss_mb": float(self._last_stat.get("rss_mb", 0.0) or 0.0),
            "venv": str(self.venv),
            "isolated": venv_python(self.venv).is_file(),
        }

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def ensure_started(self) -> int:
        """Spawn the child if it is not running. Returns the current generation.

        Raises :class:`SidecarCrashed` with ``restart_budget_exhausted`` once a child has
        died more times in a row than the budget allows — an unbounded respawn loop over
        a genuinely broken install is a busy-loop, not resilience.
        """
        with self._lock:
            return self._ensure_started_locked()

    def _ensure_started_locked(self) -> int:
        if self._child is not None and self._child.is_alive():
            return self._generation
        # Refused before anything is counted: no restart would change it, so it is never a
        # crash, never spends the restart budget, and the watchdog has nothing to revive.
        _refuse_in_the_desktop_app(self._generation)
        if self._consecutive_failures > self.restart_max:
            raise SidecarCrashed(
                "restart_budget_exhausted",
                generation=self._generation,
                detail=f"{self._consecutive_failures} consecutive failures "
                f"(sidecar_restart_max={self.restart_max})",
            )
        # Any spawn after the first is a RESPAWN. Counted on generation, not on a live
        # child handle: a crash detaches the handle (``_died``), so keying off it undercounts
        # exactly the case the counter exists to report.
        if self._generation > 0:
            self._restarts += 1
        self._generation += 1
        self._seq = 0
        self._child = self._spawn(self._generation)
        return self._generation

    def _spawn(self, generation: int) -> _Child:
        from personalclaw.sandbox import PROFILE_TOOL, spawn_shim_argv

        python = self.python_executable()
        argv = [str(python), str(_CHILD_HARNESS), "--worker", str(self.worker)]
        # Resource ceiling: a sidecar runs third-party native code, so it is
        # agent-influenced and carries the ``tool`` profile — which also gives it the
        # OOM-first bias, exactly the disposition wanted for a process holding a model.
        # argv-prepend (never preexec_fn): this can run off a watchdog thread while the
        # loop holds locks, and a fork there is the documented gateway hazard.
        launch = spawn_shim_argv(argv, PROFILE_TOOL)
        env = _sidecar_child_env(python, self._env_extra)
        try:
            proc = subprocess.Popen(  # noqa: S603 — argv is core-built; worker is app code
                launch,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self._consecutive_failures += 1
            self._last_reason = "spawn_failed"
            raise SidecarCrashed("spawn_failed", generation=generation, detail=str(exc)) from exc
        # What it prints that is not a frame reaches the gateway's log, masked, as an app
        # backend's does: its stderr through the relay, a stray stdout line through the frame
        # reader.
        output = ChildOutput(app=self.app, process="engine", pid=proc.pid, env=env)
        self._output = output
        child = _Child(proc=proc, generation=generation, output=output)
        child.reader = threading.Thread(
            target=self._read_frames,
            args=(child,),
            name=f"sidecar-{self.app}-g{generation}",
            daemon=True,
        )
        child.reader.start()
        relay(proc, output, streams=(STDERR,))
        logger.info(
            "sidecar %s started: pid=%s generation=%s python=%s",
            self.app,
            proc.pid,
            generation,
            self.python_executable(),
        )
        return child

    def _read_frames(self, child: _Child) -> None:
        """Drain the child's stdout, delivering every COMPLETE frame line.

        The newline check is load-bearing: at EOF a pipe hands back whatever partial
        bytes were written before the process died, and ``readline`` returns them as a
        line. Delivering that would be believing a half-written result.
        """
        stream = child.proc.stdout
        if stream is None:  # pragma: no cover — always a pipe here
            return
        while True:
            try:
                raw = stream.readline()
            except (ValueError, OSError):
                break
            if raw == "":
                break
            if not raw.endswith("\n"):
                child.output.line(STDOUT, f"[truncated frame discarded] {raw}")
                break
            line = raw.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except ValueError:
                child.output.line(STDOUT, line)  # a native lib's stray print, not a frame
                continue
            if not isinstance(frame, dict):
                continue
            self.deliver(child.generation, frame)
        child.replies.put((child.generation, dict(_EOF)))

    def deliver(self, generation: int, frame: dict[str, Any]) -> bool:
        """The generation fence: accept *frame* only if *generation* is current.

        Returns False (and counts a stale reply) for a frame from a superseded child —
        the zombie-reply bug the generation counter exists to stop. A stat frame is
        recorded rather than queued: it answers no request.
        """
        if generation != self._generation:
            self._stale_replies += 1
            logger.debug(
                "sidecar %s fenced a stale frame from generation %s (current %s)",
                self.app,
                generation,
                self._generation,
            )
            return False
        if "stat" in frame and frame.get("id") is None:
            stat = frame.get("stat")
            if isinstance(stat, dict):
                self._last_stat = dict(stat)
            return True
        child = self._child
        if child is not None:
            child.replies.put((generation, frame))
        return True

    def stop(self) -> None:
        """Terminate the child (graceful, then kill). Idempotent."""
        with self._lock:
            child, self._child = self._child, None
        if child is None:
            return
        proc = child.proc
        if proc.poll() is None:
            child.output.stopping()
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=_TERM_TIMEOUT)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=_TERM_TIMEOUT)
            except OSError:
                logger.debug("sidecar %s already gone at stop", self.app)
        # Not its stderr: the relay reading it closes it once the child's end of it has.
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        logger.info("sidecar %s stopped (generation %s)", self.app, child.generation)

    # ── the protocol ──────────────────────────────────────────────────────────

    def call(
        self, verb: str, payload: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> Any:
        """Send one verb and return its result. Raises :class:`SidecarCrashed` on death.

        The child is spawned on demand, so the call AFTER a crash is what brings the next
        generation up — that is how "search recovers without a gateway restart" works
        without anyone having to notice the crash first.
        """
        deadline = self.call_timeout if timeout is None else float(timeout)
        with self._lock:
            generation = self._ensure_started_locked()
            child = self._child
            if child is None:  # pragma: no cover — _ensure_started_locked sets it
                raise SidecarCrashed("not_started", generation=generation)
            self._seq += 1
            request_id = f"{generation}:{self._seq}"
            request = {"id": request_id, "verb": verb, "payload": dict(payload or {})}
            try:
                stdin = child.proc.stdin
                if stdin is None:  # pragma: no cover
                    raise BrokenPipeError("no stdin")
                stdin.write(json.dumps(request) + "\n")
                stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                raise self._died(child, "eof") from None
            frame = self._await_reply(child, request_id, deadline)
        if frame.get("ok"):
            self._consecutive_failures = 0
            return frame.get("result")
        # A typed worker failure is NOT a crash — the child is alive and honest.
        raise SidecarWorkerError(
            str(frame.get("error") or "sidecar call failed"),
            reason=str(frame.get("reason") or "worker_error"),
        )

    def _await_reply(self, child: _Child, request_id: str, deadline: float) -> dict[str, Any]:
        """Wait for the reply to *request_id*, or raise. Called with the lock held."""
        end = time.monotonic() + deadline
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise self._died(child, "timeout")
            try:
                _, frame = child.replies.get(timeout=remaining)
            except queue.Empty:
                raise self._died(child, "timeout") from None
            if frame.get("__eof__"):
                raise self._died(child, self._exit_reason(child))
            if frame.get("id") != request_id:
                # Out of order or duplicated: never satisfy a request with another's reply.
                # The GENERATION fence is not repeated here — it lives in exactly one
                # place (:meth:`deliver`), so it is one testable rule rather than two
                # half-rules that mask each other.
                self._stale_replies += 1
                continue
            return frame

    def _exit_reason(self, child: _Child) -> str:
        """``signal_11`` / ``exit_1`` / ``eof`` from the dead child's return code."""
        try:
            code = child.proc.poll()
            if code is None:
                code = child.proc.wait(timeout=_TERM_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            return "eof"
        if code is None:
            return "eof"
        return f"signal_{-code}" if code < 0 else f"exit_{code}"

    def _died(self, child: _Child, reason: str) -> SidecarCrashed:
        """Mark the child dead, tear it down, and build the typed error to raise."""
        self._consecutive_failures += 1
        self._last_reason = reason
        proc = child.proc
        if proc.poll() is None:
            # Ended here, not on its own: the warning below says why.
            child.output.stopping()
            try:
                proc.kill()
            except OSError:
                pass
        if self._child is child:
            self._child = None
        detail = "; ".join(child.output.lines()[-3:])
        logger.warning("sidecar %s died: %s (generation %s)", self.app, reason, child.generation)
        return SidecarCrashed(reason, generation=child.generation, detail=detail)

    async def acall(
        self, verb: str, payload: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> Any:
        """:meth:`call` off the event loop — what an async provider proxy awaits."""
        return await asyncio.to_thread(self.call, verb, payload, timeout=timeout)

    def stat(self) -> dict[str, Any]:
        """Ask the child for a fresh ``rss_mb`` frame (also updates :attr:`last_stat`)."""
        result = self.call("stat", timeout=15.0)
        if isinstance(result, dict):
            self._last_stat = dict(result)
            return dict(result)
        return {}

    # ── watchdog ──────────────────────────────────────────────────────────────

    def watchdog_sweep(self) -> dict[str, Any]:
        """One supervisor decision, returned as data.

        ``noop`` (never started, or alive), ``respawned``, ``budget_exhausted`` — the
        outcome a test asserts instead of sleeping and hoping.
        """
        if self._generation == 0:
            return {"app": self.app, "action": "noop", "reason": "never_started"}
        if self.is_alive():
            return {"app": self.app, "action": "noop", "reason": "alive"}
        try:
            generation = self.ensure_started()
        except SidecarCrashed as exc:
            return {"app": self.app, "action": "budget_exhausted", "reason": exc.reason}
        return {
            "app": self.app,
            "action": "respawned",
            "generation": generation,
            "restarts": self._restarts,
        }


class SidecarWorkerError(RuntimeError):
    """The child answered with a typed failure — it is alive, the *call* failed.

    Distinct from :class:`SidecarCrashed` on purpose: a bad request or a model that
    refuses to load must not be mistaken for a process death and burn a restart.
    """

    def __init__(self, message: str, *, reason: str = "worker_error") -> None:
        self.reason = reason
        super().__init__(message)


# ---------------------------------------------------------------------------
# One call in a child of its own
# ---------------------------------------------------------------------------


async def run_once(
    app: str,
    worker: Path | str,
    method: str,
    payload: dict[str, Any] | None = None,
    *,
    env_extra: dict[str, str] | None = None,
) -> Any:
    """Run one ``call(method, payload)`` of *worker* in a child process of its own, and return
    what the call returned. The child exits once it has answered.

    For native inference that holds the interpreter lock while it works. A worker thread does not
    help with that: a library call that never lets the lock go stops every thread of its process,
    the event loop included, so the gateway answers nothing until the call returns (a speaker
    diarization held every request for two minutes). In a child, the lock it holds is the
    child's.

    The child is the sidecar child (``_sidecar_child.py``) running *worker*: under the app's own
    venv when it has one (:func:`sidecar_venv_dir`), else under the gateway's interpreter with the
    packages apps declare loaded after its own, as in the gateway. It gets the child allowlist
    environment, the ``tool`` resource ceiling, and a process group of its own.

    No deadline of its own: the caller's bound decides how long it may take. A cancelled call (a
    knowledge step out of its time, the gateway stopping) kills the child and every program it
    started, rather than leaving them to run for nobody.

    The two speak the sidecar protocol: one JSON line each way, so nothing the child writes back
    is ever more than data. Raises :class:`SidecarWorkerError` when the worker raised (the child
    answered), and :class:`SidecarCrashed` when the child could not start, or died before it
    answered.
    """
    from personalclaw.cancellation import kill_timed_out
    from personalclaw.sandbox import PROFILE_TOOL, spawn_shim_argv

    _refuse_in_the_desktop_app(1)
    python = _child_python(sidecar_venv_dir(app))
    argv = [str(python), str(_CHILD_HARNESS), "--worker", str(Path(worker))]
    launch = spawn_shim_argv(argv, PROFILE_TOOL)
    env = _sidecar_child_env(python, env_extra)
    request_id = "1:1"  # one child, one request: the first of the first generation
    request = {
        "id": request_id,
        "verb": "call",
        "payload": {"method": method, "payload": dict(payload or {})},
    }
    try:
        # Its own group, so a kill reaches what the worker starts (ffmpeg decoding a recording).
        proc = await asyncio.create_subprocess_exec(
            *launch,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        logger.warning("%s: its child process could not start: %s", app, exc)
        raise SidecarCrashed("spawn_failed", generation=1, detail=str(exc)) from exc
    # What it writes to stderr reaches the gateway's log, masked, as a supervised engine's does.
    output = ChildOutput(app=app, process="engine", pid=proc.pid, env=env)
    try:
        out = await _exchange(proc, request, output)
    except BaseException:
        # Cancelled (or anything else that leaves the call unanswered): nothing waits for the
        # child any more, so it goes, with what it started.
        output.stopping()
        await kill_timed_out(proc)
        logger.debug("%s: the call ended unanswered, and its child process stopped", app)
        raise
    reply = _reply(out, request_id)
    if reply is None:
        code = proc.returncode
        reason = "eof" if code is None else f"signal_{-code}" if code < 0 else f"exit_{code}"
        logger.warning("%s: its child process ended before it answered (%s)", app, reason)
        raise SidecarCrashed(reason, generation=1, detail="; ".join(output.lines()[-3:]))
    if reply.get("ok"):
        return reply.get("result")
    raise SidecarWorkerError(
        str(reply.get("error") or "the call failed"),
        reason=str(reply.get("reason") or "worker_error"),
    )


async def _exchange(proc: Any, request: dict[str, Any], output: ChildOutput) -> bytes:
    """Send *request*, close the child's stdin, and read until the child exits: everything it
    wrote to stdout. Each line of its log (stderr) goes to *output*, which then records how it
    ended."""
    from personalclaw.cancellation import cancel_and_wait

    async def _read_log() -> None:
        splitter = LineSplitter()
        while chunk := await proc.stderr.read(65536):
            for text in splitter.feed(chunk):
                output.line(STDERR, text)
        for text in splitter.close():
            output.line(STDERR, text)

    logs = asyncio.ensure_future(_read_log())
    try:
        try:
            proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the child is already gone: its exit status says why
        proc.stdin.close()
        out = await proc.stdout.read()
        await proc.wait()
        await logs
    finally:
        # Ended early (cancelled, or a failure above): the log reader is stopped too, and a
        # reader that does not leave is given up after the stop's bound.
        await cancel_and_wait([logs], what="a one-call child's log")
    output.ended(proc.returncode)
    return out


def _reply(out: bytes, request_id: str) -> dict[str, Any] | None:
    """The reply frame to *request_id* in what the child wrote, or None.

    Only COMPLETE lines count: a child killed mid-write leaves a final line with no newline,
    and a half-written frame is never read as an answer. A line that is not a frame (a native
    library's stray print) is passed over."""
    for line in out.decode("utf-8", "replace").split("\n")[:-1]:
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        if isinstance(frame, dict) and frame.get("id") == request_id:
            return frame
    return None


# ---------------------------------------------------------------------------
# The live runner table
# ---------------------------------------------------------------------------

_runners: dict[str, SidecarRunner] = {}


def register_runner(runner: SidecarRunner) -> None:
    """Track *runner* so the pressure surface and the watchdog can see it.

    A runner an app's code registered is stopped and dropped when the app is unloaded — its
    child runs the version of the app that started it.
    """
    _runners[runner.app] = runner

    def _forget() -> None:
        if _runners.get(runner.app) is runner:
            unregister_runner(runner.app)

    app_code.keep(_forget)


def unregister_runner(app: str) -> None:
    """Drop and stop the named runner (app disabled / uninstalled)."""
    runner = _runners.pop(app, None)
    if runner is not None:
        runner.stop()


def get_runner(app: str) -> SidecarRunner | None:
    return _runners.get(app)


def runners() -> list[SidecarRunner]:
    """Every registered runner (registration order)."""
    return list(_runners.values())


def sweep_sidecars() -> list[dict[str, Any]]:
    """One watchdog pass over every registered runner. Returns each decision."""
    return [runner.watchdog_sweep() for runner in runners()]


def stop_all_sidecars() -> None:
    """Terminate every child. Sidecars do not survive a gateway restart."""
    for runner in runners():
        runner.stop()


_WATCHDOG_INTERVAL = 30  # seconds between sweeps, matching the app-backend watchdog


def _sweep_and_log() -> None:
    for decision in sweep_sidecars():
        if decision.get("action") != "noop":
            logger.info("sidecar watchdog: %s", decision)


_WATCHDOG = PeriodicSweep("model-sidecar-watchdog", _WATCHDOG_INTERVAL, _sweep_and_log)


def start_sidecar_watchdog() -> threading.Thread:
    """Daemon sweep that revives crashed sidecar children every 30s — or the one already running.

    Same semantics as the app-backend watchdog: relaunch on crash, never survive the
    gateway (:func:`stop_sidecar_watchdog` runs from its cleanup). A sweep over an empty
    table is free, so this is harmless when no app declares ``execution: sidecar``.
    """
    return _WATCHDOG.start()


def stop_sidecar_watchdog() -> None:
    """Stop the sweep :func:`start_sidecar_watchdog` started. Idempotent."""
    _WATCHDOG.stop()


# ---------------------------------------------------------------------------
# Resumable install jobs
# ---------------------------------------------------------------------------

#: The install steps, in order. Every one existence-checks before doing work, so a
#: killed install re-runs from where it died rather than from the top.
INSTALL_STEPS = ("venv", "deps", "weights")

#: How long ``python -m venv`` may take.
_VENV_TIMEOUT_SECS = 300
#: How long the engine's ``pip install`` may run before it is stopped. An engine is torch-sized:
#: gigabytes of wheels over whatever connection the owner has, then an install that unpacks tens
#: of thousands of files. The live log is what shows it is working in the meantime.
DEPS_TIMEOUT_SECS = 2 * 60 * 60


class InstallCancelled(RuntimeError):
    """The owner cancelled the install while a step was running."""


@dataclass
class _Step:
    name: str
    status: str = "pending"  # pending | running | done | skipped | error | cancelled
    detail: str = ""
    #: When the step last started running (epoch seconds), so a surface can say for how long.
    started_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "started_at": self.started_at,
        }


class SidecarInstall:
    """One app's sidecar install: dedicated venv + pip deps + weights check.

    Resumable and idempotent by construction. ``run()`` may be called again after
    a kill, a crash, or a success: the venv step skips when the interpreter is already
    there, the deps step skips when the receipt matches the manifest's requirement list,
    and the weights step is a disk probe. Nothing is torn down to be rebuilt.

    The requirements are the manifest's ``dependencies.sidecarDependencies`` — the engine —
    never its ``pythonDependencies``, which the app installer puts in the gateway's own
    ``app-python``. A running command's output reaches :attr:`log_tail` line by line, and
    :meth:`cancel` stops it.
    """

    def __init__(
        self,
        app: str,
        *,
        requirements: list[str] | None = None,
        venv: Path | None = None,
        cache_root: Path | None = None,
        model: str = "",
    ) -> None:
        self.app = app
        self.requirements = sorted(requirements or [])
        self.venv = venv if venv is not None else sidecar_venv_dir(app)
        self.cache_root = cache_root
        self.model = model
        self.steps = [_Step(name) for name in INSTALL_STEPS]
        self.error = ""
        self.reason = ""
        self.remediation = ""
        self._log: list[str] = []
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._cancelled = False

    # -- discovery ---------------------------------------------------------

    @classmethod
    def for_app(cls, app: str) -> "SidecarInstall | None":
        """Build the install for an INSTALLED app that declares a sidecar provider.

        Returns None when the app is not installed, has no manifest, or runs every provider
        ``execution: in-process`` — an install has nothing to mean for an app that never
        asked for a child process.
        """
        from personalclaw.apps.manager import APP_MANIFEST_FILENAME, app_dir
        from personalclaw.apps.manifest import EXECUTION_SIDECAR, AppManifest

        manifest_path = app_dir(app) / APP_MANIFEST_FILENAME
        if not manifest_path.is_file():
            return None
        try:
            manifest = AppManifest.from_json_file(manifest_path)
        except Exception:
            logger.debug("sidecar install: unreadable manifest for %s", app, exc_info=True)
            return None
        if not any(p.execution == EXECUTION_SIDECAR for p in manifest.all_providers()):
            return None
        return cls(app, requirements=list(manifest.dependencies.sidecarDependencies))

    # -- state -------------------------------------------------------------

    @property
    def installed(self) -> bool:
        """Whether the venv exists AND its deps receipt matches the manifest."""
        return venv_python(self.venv).is_file() and self._receipt_matches()

    @property
    def managed(self) -> bool:
        """Whether CORE created this venv (marker present) — the delete gate."""
        return (self.venv / _MARKER).is_file()

    @property
    def log_tail(self) -> list[str]:
        """The last lines the running command printed, masked as a relayed line is
        (``child_output.mask_line``): a status poll shows them."""
        with self._lock:
            kept = list(self._log)
        return [mask_line(line) for line in kept]

    def status(self) -> dict[str, Any]:
        """The rich poll shape: what happened, and what to do about it."""
        return {
            "provider": self.app,
            "installed": self.installed,
            "managed": self.managed,
            "install_dir": str(self.venv),
            "requirements": list(self.requirements),
            "steps": [s.to_dict() for s in self.steps],
            "log_tail": self.log_tail,
            "error": self.error,
            "reason": self.reason,
            "remediation": self.remediation,
        }

    # -- the steps ---------------------------------------------------------

    def begin(self) -> None:
        """Start a fresh run: the last run's steps, error, log and cancellation are forgotten,
        so a run that succeeds after a failed one does not go on showing its error."""
        with self._lock:
            self._cancelled = False
            self._log.clear()
        for step in self.steps:
            step.status, step.detail, step.started_at = "pending", "", 0.0
        self.error = self.reason = self.remediation = ""

    def cancel(self) -> None:
        """Stop the install: the running command is killed and no later step starts. What the
        steps already finished stays, so the next run resumes from the one that stopped."""
        with self._lock:
            self._cancelled = True
            proc = self._proc
        if proc is not None:
            _stop(proc)

    def run(self) -> bool:
        """Run every step, skipping the already-satisfied ones. True if all succeeded."""
        for step in self.steps:
            if not self.run_one(step.name):
                return False
        return True

    def run_one(self, name: str) -> bool:
        """Run ONE step by name. False on failure (with ``error``/``remediation`` set).

        Exposed per-step so the job runner can publish a progress frame between steps: an
        install whose only signal was "still running" for twenty minutes of pip output is
        indistinguishable from a hang.
        """
        step = next((s for s in self.steps if s.name == name), None)
        if step is None:
            return False
        handler = getattr(self, f"_step_{step.name}")
        step.status, step.started_at = "running", time.time()
        from personalclaw import python_children

        try:
            if self._cancelled:
                raise InstallCancelled("the install was cancelled")
            python_children.require(ENGINE_REFUSAL)
            step.status, step.detail = handler()
        except Exception as exc:  # noqa: BLE001 — a step failure is reported, not raised
            from personalclaw._installer import NoInstallerError

            step.status = "cancelled" if isinstance(exc, InstallCancelled) else "error"
            # A missing pip's fix is the remediation, so the error is only what broke: the
            # card shows the two together. That sentence is whole however long the interpreter's
            # path is, and so is the desktop app's refusal, which says what to do itself; only
            # an exception's own text, which can be any length, is cut.
            if isinstance(exc, NoInstallerError) and exc.problem:
                broke = exc.problem
            elif isinstance(exc, python_children.NeedsInterpreter):
                broke = str(exc)
            else:
                broke = str(exc)[:200]
            step.detail = broke
            self.error = broke
            self.reason, self.remediation = _classify_install_failure(
                exc, step.name, "\n".join(self.log_tail)
            )
            logger.warning("sidecar install %s: step %s %s", self.app, step.name, step.status)
            return False
        return True

    def _step_venv(self) -> tuple[str, str]:
        python = venv_python(self.venv)
        if python.is_file():
            return "skipped", "venv already present"
        self.venv.parent.mkdir(parents=True, exist_ok=True)
        # No pip of its own: the engine installs with the gateway's (`_step_deps`), and a venv
        # made WITH pip asks ensurepip for one, which a Debian or Ubuntu system Python lacks
        # unless `python3-venv` is installed, so `python -m venv` fails there.
        self._run(
            [sys.executable, "-m", "venv", "--without-pip", str(self.venv)],
            label="python -m venv",
            timeout=_VENV_TIMEOUT_SECS,
        )
        if not python.is_file():
            raise RuntimeError(f"venv creation produced no interpreter at {python}")
        atomic_write(
            self.venv / _MARKER, json.dumps({"app": self.app, "created_by": "personalclaw"}) + "\n"
        )
        return "done", str(self.venv)

    def _step_deps(self) -> tuple[str, str]:
        if not self.requirements:
            return "skipped", "no sidecarDependencies declared"
        if self._receipt_matches():
            return "skipped", "requirements already installed"
        unreadable = [r for r in self.requirements if not _is_requirement(r)]
        if unreadable:
            raise ValueError(
                f"{unreadable[0][:80]!r} is not a requirement pip can read, so nothing was "
                "installed"
            )
        from personalclaw._installer import env_install_argv

        # The gateway's pip, run under the engine's interpreter (`pip --python`), so the venv
        # needs no pip of its own. `--` ends pip's options: a requirement can never be read as
        # one, whatever it says. `--no-input`: an index that wants a login fails instead of
        # waiting on a prompt nobody sees until the step's timeout.
        self._run(
            env_install_argv(
                venv_python(self.venv),
                ["--disable-pip-version-check", "--no-input", "--", *self.requirements],
            ),
            label="pip",
            timeout=DEPS_TIMEOUT_SECS,
        )
        # The receipt is written only after pip EXITS ZERO, which is what makes the step
        # resumable: a killed pip leaves no receipt, so the next run redoes it.
        atomic_write(self._receipt_path(), json.dumps(self.requirements) + "\n")
        return "done", f"{len(self.requirements)} requirement(s)"

    def _step_weights(self) -> tuple[str, str]:
        if self.cache_root is None or not self.model:
            return "skipped", "weights are fetched by the download job"
        from personalclaw.local_models import layouts

        if layouts.is_downloaded(self.cache_root, self.model):
            return "done", "weights present on disk"
        return "pending", "weights not downloaded yet"

    # -- helpers -----------------------------------------------------------

    def _receipt_path(self) -> Path:
        return self.venv / ".personalclaw-deps.json"

    def _receipt_matches(self) -> bool:
        try:
            recorded = json.loads(self._receipt_path().read_text("utf-8"))
        except (OSError, ValueError):
            return not self.requirements
        return sorted(str(r) for r in recorded) == self.requirements

    def _run(self, argv: list[str], *, label: str, timeout: float) -> None:
        """Run one install command, its output read into the log tail as it is written.

        Raises, naming it by *label*, when it exits non-zero, when it is still running after
        *timeout* seconds, and when :meth:`cancel` stopped it; a stopped command's whole
        process group goes with it."""
        from personalclaw._installer import installer_cache_env
        from personalclaw.sandbox import PROFILE_BUILD, build_child_env, spawn_shim_argv

        # Ceiling: pip and venv creation are operator-initiated but run third-party
        # setup code, so they carry the ``build`` profile (NOFILE raised, OOM bias kept)
        # via argv-prepend — never preexec_fn, this can run off a worker thread. The same
        # third-party code is why the environment is the child allowlist, with pip's own
        # settings (index, certificates, cache) so an engine installs wherever an app's
        # packages do.
        launch = spawn_shim_argv(list(argv), PROFILE_BUILD)
        proc = subprocess.Popen(  # noqa: S603 — core-built argv, no shell
            launch,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            # The engine installs into the app's folder, so pip keeps no cache in the user's.
            env=build_child_env(
                site="model-sidecar-install", installer="pip", extra=installer_cache_env()
            ),
            start_new_session=True,
        )
        with self._lock:
            self._proc = proc
            cancelled = self._cancelled
        if cancelled:  # cancelled before there was a process to stop
            _stop(proc)
        reader = threading.Thread(
            target=self._read_output, args=(proc,), name=f"sidecar-install-{self.app}", daemon=True
        )
        reader.start()
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _stop(proc)
            raise RuntimeError(
                f"{label} was still running after {_duration(timeout)} and was stopped "
                "(timed out)"
            ) from None
        finally:
            with self._lock:
                self._proc = None
            reader.join(timeout=5)
        if self._cancelled:
            raise InstallCancelled("the install was cancelled")
        if code != 0:
            tail = self.log_tail
            last = mask_child_output(tail[-1], limit=160) if tail else "no output"
            raise RuntimeError(f"{label} exited {code}: {last}")

    def _read_output(self, proc: subprocess.Popen[str]) -> None:
        stream = proc.stdout
        if stream is None:  # pragma: no cover — always a pipe here
            return
        for line in stream:
            self._note(line)

    def _note(self, line: str) -> None:
        line = bound_line(line.rstrip())
        if not line:
            return
        with self._lock:
            self._log.append(line)
            if len(self._log) > _LOG_TAIL_MAX:
                del self._log[: len(self._log) - _LOG_TAIL_MAX]

    def delete(self) -> bool:
        """Remove the venv — only ever a CORE-created one (``managed``).

        A user-supplied venv is never deleted: core did not create it and cannot know
        what else depends on it.
        """
        import shutil

        if not self.managed:
            return False
        shutil.rmtree(self.venv, ignore_errors=True)
        for step in self.steps:
            step.status, step.detail, step.started_at = "pending", "", 0.0
        return not self.venv.exists()


def _is_requirement(spec: str) -> bool:
    """Whether pip reads *spec* as one requirement (PEP 508), never as an option."""
    from packaging.requirements import InvalidRequirement, Requirement

    try:
        Requirement(spec)
    except InvalidRequirement:
        return False
    return True


def _stop(proc: subprocess.Popen[str]) -> None:
    """Terminate *proc* and everything it started (pip's build children), then reap it."""
    import signal

    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, AttributeError):
        proc.terminate()
    try:
        proc.wait(timeout=_TERM_TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, AttributeError):
            proc.kill()
        proc.wait(timeout=_TERM_TIMEOUT)


def _duration(secs: float) -> str:
    """``2 hours``, ``5 minutes``, ``30 seconds``."""
    for unit, size in (("hour", 3600), ("minute", 60)):
        if secs >= size and secs % size == 0:
            n = int(secs // size)
            return f"{n} {unit}{'s' if n != 1 else ''}"
    n = int(secs)
    return f"{n} second{'s' if n != 1 else ''}"


def _classify_install_failure(exc: Exception, step: str, log: str = "") -> tuple[str, str]:
    """``(reason, remediation)`` for a failed install step. *log* is what the step wrote.

    ``remediation`` is deliberately distinct from the error: the error says what broke,
    the remediation says what the user should DO about it — the one field that turns a
    dead end into a next action.
    """
    if isinstance(exc, InstallCancelled):
        return "cancelled", "Install engine starts it again, from the step it stopped at."
    from personalclaw import python_children

    if isinstance(exc, python_children.NeedsInterpreter):
        return "needs_python", ""  # the refusal already says what to do instead
    from personalclaw._installer import NoInstallerError

    if isinstance(exc, NoInstallerError) and exc.fix:
        return "pip_failed", f"{exc.fix[0].upper()}{exc.fix[1:]}, then re-run the install."
    from personalclaw.sandbox import login_left_out_note

    login = login_left_out_note(log, installer="pip") if step == "deps" else ""
    if login:
        return "network", f"{login} Then re-run the install."
    text = str(exc).lower()
    if isinstance(exc, subprocess.TimeoutExpired) or "timed out" in text:
        return "timeout", "Re-run the install — it resumes from the step that timed out."
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        return "disk_full", "Free disk space, then re-run the install."
    if "no space" in text or "disk full" in text:
        return "disk_full", "Free disk space, then re-run the install."
    if any(
        w in text for w in ("connection", "network", "resolve", "unreachable", "temporary fail")
    ):
        return "network", "Check the network connection, then re-run the install."
    if step == "deps":
        return "pip_failed", "Read the log tail for the failing requirement, then re-run."
    if step == "venv":
        return "venv_failed", "Check that python -m venv works, then re-run the install."
    return "install_failed", "Re-run the install; it resumes from the failed step."
