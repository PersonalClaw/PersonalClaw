"""App background-worker supervisor — the host ``permissions.backgroundTasks`` promised.

APE-1 shipped ``permissions.backgroundTasks`` as a *declaration* and said so out loud: the
note at ``apps/manifest.py:405`` reads "unlike ``backgroundTasks`` above, whose host still
does not exist". This module is that host, which is what turns the flag from disclosure
into a grant — every launch path funnels through one gate (:meth:`WorkerSupervisor._spawn`)
and an app that did not declare it gets no worker, ever.

**A worker is a portless backend.** Every question about process *lifetime* —
allowlisted child env, graceful-then-kill teardown, a watchdog that revives a crashed
child, boot-time PPID-1 orphan reaping — is already answered by
``apps/backend_runtime.py``, so this module reuses that machinery **by import** rather
than re-deriving it: ``_TERM_TIMEOUT`` (the graceful-then-kill budget),
``BackendSupervisor._launch_cmd`` (suffix→interpreter), and
``BackendSupervisor.reap_orphans`` (the PPID walk). A second PPID walk that disagreed
with the first is how a test run gets its own processes killed.

Two things a port bought a backend, and a worker does not have:

* **No health check, so "alive" means the process is running** — ``proc.poll() is None``,
  the same predicate ``RunningBackend.is_alive`` uses, and nothing stronger. A backend is
  *reachable*, so it can be asked. A worker has no inbound surface, so there is no one to
  ask. This module deliberately does not infer wedged-ness either: a worker blocked
  forever on a socket read is indistinguishable from one sleeping between iterations, and
  telling them apart needs a per-worker declared heartbeat interval — a manifest field and
  an SDK obligation, not a guess this layer can make. What that costs is stated plainly:
  a wedged-but-running worker is invisible here. What it buys is that "alive" never lies.
* **No ceiling of its own.** A backend serves requests someone asked for; a worker runs
  unattended forever, which is the denial-of-wallet surface the plan's Risk names. So this
  supervisor is policy-aware in a way the backend supervisor is not: it has a PAUSED state.

**PAUSED and stopped are different states, deliberately.** PAUSED is a *deferral* — the
day spend ceiling is breached or incident mode is on — so the record stays in the table
carrying the reason, and the next sweep resumes it by itself when the policy clears.
Stopped is an *answer* — the app was disabled, uninstalled, or lost the permission — so
the record is removed, exactly as ``BackendSupervisor.stop`` pops its row. Absence-vs-
PAUSED is the state difference, and it is what stops the two from collapsing: collapse
them one way and a budget breach permanently kills a worker the user never disabled;
collapse them the other and a disabled app's worker returns on the next sweep.

**The crash-loop bound is what makes revival safe.** A worker that dies during startup
would otherwise be relaunched every sweep forever, burning CPU and filling the log. After
``_MAX_RESTARTS`` revives that each failed to stay up for ``_HEALTHY_UPTIME_SECS``, the
supervisor gives up: state ``FAILED``, ``reason`` filled in, one ``logger.warning`` and one
notification. A silent give-up is indistinguishable from a working worker, so the give-up
is a fact you can read off the record, not an absence you have to infer.

Wiring (both out of this module's hands, on purpose): ``providers/loader.py`` starts the
backend watchdog at boot and should start :func:`start_worker_watchdog` the same way, and
``apps/app_manager.py`` — which already calls ``BackendSupervisor.reap_orphans`` on the
enable path — is where :meth:`WorkerSupervisor.start`/:meth:`stop` belong on
enable/disable. Neither is required for correctness: the sweep is self-healing (it starts
declared workers for enabled apps, stops them for disabled ones) and the first start of
any worker reaps that entry's orphans.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from personalclaw.apps.backend_runtime import _TERM_TIMEOUT, BackendSupervisor

# The app-facing contract, imported for the three facts the PARENT needs: which permission
# gates a worker, what the entry file is called, and what the single worker is named. The
# worker CLASS is not imported here — that runs in the child, and pulling it into the
# supervisor would put the app's own contract inside the host's import closure.
from personalclaw.apps.background import (
    BACKGROUND_TASKS_PERMISSION,
    WORKER_DEFAULT_NAME,
    WORKER_ENTRY_POINT,
)
from personalclaw.apps.manager import app_dir
from personalclaw.apps.manifest import AppManifest

logger = logging.getLogger(__name__)

#: Sweep cadence — the same 30s the backend watchdog uses. One number for "how stale may
#: my picture of a supervised child be" is easier to reason about than two.
_WATCHDOG_INTERVAL = 30

#: Revives allowed before the supervisor gives up on a worker. Five is enough to ride out
#: a transient (a port that was briefly busy, a dependency still installing) and small
#: enough that a worker broken at startup stops costing anything within ~2.5 minutes.
_MAX_RESTARTS = 5

#: Uptime that proves a start "took". A worker that ran this long before dying is having a
#: fresh crash, not looping, so its restart counter resets. Without this a worker that runs
#: happily for a month and then crashes six times over that month would be given up on.
_HEALTHY_UPTIME_SECS = 60.0

#: Set by a harness that must not have app workers spawned underneath it — the same escape
#: hatch ``_check_and_revive`` honors for backends.
_SKIP_ENV = "PERSONALCLAW_SKIP_APP_WORKERS"


class WorkerState(str, Enum):
    """The states a *tracked* worker can be in.

    There is no ``STOPPED`` member on purpose: a stopped worker has no record at all (see
    the module docstring). ``FAILED`` is sticky — it is the give-up, and a bound that
    forgets itself on the next sweep is not a bound.
    """

    RUNNING = "running"
    PAUSED = "paused"
    FAILED = "failed"


@dataclass
class SupervisedWorker:
    """One supervised worker process, plus the history the bound and the UI need."""

    app: str
    worker: str
    entry: Path
    restart: bool = True
    pid: int = 0
    proc: subprocess.Popen | None = field(default=None, repr=False)
    state: WorkerState = WorkerState.RUNNING
    restarts: int = 0
    started_at: float = 0.0
    #: Why it is PAUSED, or why the supervisor gave up. Empty while RUNNING.
    reason: str = ""
    #: PAUSED only: whether the sweep may resume it once the policy clears. False for an
    #: operator pause, so the supervisor never fights a human.
    auto_resume: bool = True

    @property
    def key(self) -> tuple[str, str]:
        return (self.app, self.worker)

    def is_alive(self) -> bool:
        """The only liveness fact a portless child offers: its process is running."""
        return self.proc is not None and self.proc.poll() is None


# ── What a manifest declares ────────────────────────────────────────────────────
#
# These are PARENT-side: an app author never reads them, so they are deliberately not
# in `apps/background.py` (mirrored verbatim by the app-facing `sdk/background.py`).
# Widening the SDK surface with the supervisor's own bookkeeping would add exported
# names with no app-side reader, which the inert-sdk-export gate exists to catch.


@dataclass(frozen=True)
class WorkerSpec:
    """One declared worker: what the supervisor needs before any process exists.

    Deliberately NOT the worker object — that is :class:`BackgroundWorker`, which lives in
    the child. This is the parent-side declaration, so it carries no callable and nothing
    that would have to be imported out of the app's code to be read.
    """

    name: str
    entry_point: str
    restart: bool = True


def declared_workers(manifest: "AppManifest") -> list[WorkerSpec]:
    """The workers *manifest* declares — at most one, and none without the permission.

    The permission IS the declaration (see :data:`WORKER_ENTRY_POINT`), so this returns an
    empty list rather than raising for an app that grants nothing: "no workers declared" and
    "this app may not run workers" are the same fact from the supervisor's side, and it
    re-checks the grant at every spawn anyway.
    """
    perms = getattr(manifest, "permissions", None)
    if perms is None or not getattr(perms, BACKGROUND_TASKS_PERMISSION, False):
        return []
    return [WorkerSpec(name=WORKER_DEFAULT_NAME, entry_point=WORKER_ENTRY_POINT)]


# The host writes the permission name it verified. Fail-closed: no grant, no context.
WORKER_GRANT_ENV = "PERSONALCLAW_APP_WORKER_GRANT"


def _declared_workers(manifest: AppManifest) -> list["WorkerSpec"]:
    """The workers *manifest* declares, per the APE-3 C2 contract.

    Imported at call time because ``apps/background.py`` owns the declaration shape and
    this module owns supervision; a module-scope import would also make the pair a hard
    import cycle the moment the SDK side wants to ask the supervisor anything.
    """
    return list(declared_workers(manifest))


def _background_tasks_granted(app: str) -> bool:
    """Whether *app* declared ``permissions.backgroundTasks``, read from DISK.

    Read through ``checker_for`` — the installed manifest — and never from a manifest
    object a caller handed in, because the caller is the party the gate exists to bind.
    """
    from personalclaw.apps.permissions import checker_for

    checker = checker_for(app)
    return checker is not None and checker.can_run_background_tasks()


def _manifest_for(app: str) -> AppManifest | None:
    """The installed manifest for *app*, or ``None`` if it cannot be read."""
    try:
        return AppManifest.from_json_file(app_dir(app) / "app.json")
    except Exception:  # noqa: BLE001 — an unreadable manifest is "no workers", not a crash
        logger.debug("app %s: manifest unreadable for worker supervision", app, exc_info=True)
        return None


def _incident_pause_reason() -> str:
    """The kill-switch half of the pause policy (AUTONOMY-GUARDRAILS §1.3).

    A background worker is unattended work by definition, so it is exactly what the
    incident switch suspends. Fail-OPEN like the switch itself: a stuck-closed kill switch
    stops work the user needs, which is the worse failure for a control nobody can see.
    """
    try:
        from personalclaw.guardrails.incident import incident_active

        if incident_active():
            return "incident mode is active — unattended work is suspended"
    except Exception:  # noqa: BLE001
        logger.debug("worker sweep: incident probe failed", exc_info=True)
    return ""


def _budget_pause_reason() -> str:
    """The wallet half: the DAY-scope spend ceiling, measured by the existing meter.

    This is ``guardrails/budgets.py``'s accounting, not a parallel one — the same
    ``SpendMeter`` ``ModelCallGuard`` charges on every model call, and the same
    ``budget_from_config()`` ceiling. ``BudgetVerdict.EXCEEDED`` is the breach ("the run
    must pause", per ``check_day``); ``WARN`` is surfaced elsewhere and does not stop
    anything here.

    Scope honesty: the meter has DAY and RUN scopes and no per-app scope, so a breach is a
    fact about *the day's whole spend*, not about this worker's. That is the strongest
    statement the existing accounting supports, and inventing a per-app ledger to say
    something narrower would be a second set of books. What makes it useful anyway is that
    ``ModelCallGuard`` already refuses the worker's individual calls when the ceiling is
    breached; this layer stops the process from spinning against a wall and, more to the
    point, tells the user why its worker went quiet.
    """
    try:
        from personalclaw.guardrails.budgets import BudgetVerdict, budget_from_config, get_meter

        budget = budget_from_config()
        if budget.is_unlimited:
            return ""
        verdict, reason = get_meter().check_day(budget)
        if verdict is BudgetVerdict.EXCEEDED:
            return reason or "the day spend budget is exceeded"
    except Exception:  # noqa: BLE001
        logger.debug("worker sweep: budget probe failed", exc_info=True)
    return ""


def _policy_pause_reason() -> str:
    """The one reason a worker must not be running right now, or ``""``."""
    return _incident_pause_reason() or _budget_pause_reason()


def app_label(rec: SupervisedWorker) -> str:
    """How a worker is named to a human — one place, so notifications agree."""
    return f"{rec.app}'s worker '{rec.worker}'"


def _notify(title: str, body: str, meta: dict) -> None:
    """Tell the user, through the existing delivery choke point.

    ``DashboardState.notify`` is THE path (rules, quiet hours, mute-all all live behind
    it), reached the way ``guardrails/rungs.py`` reaches it from a non-request context.
    ``notification_kinds.WARNING`` is an already-registered pair — a supervisor is not the
    place to mint a new kind. Never raises: a notification failure must not change what
    the supervisor does to a process.
    """
    try:
        from personalclaw import notification_kinds
        from personalclaw.action_providers.services import get_action_services

        services = get_action_services()
        state = getattr(services, "state", None) if services is not None else None
        if state is None:
            logger.debug("worker notify skipped (no wired state): %s", title)
            return
        state.notify(notification_kinds.WARNING, title, body, meta=meta)
    except Exception:  # noqa: BLE001
        logger.debug("worker notify failed", exc_info=True)


def _notify_paused(app: str, worker: str, reason: str) -> None:
    """The one wording for "your worker went quiet, and here is why".

    Two triggers reach it — the sweep pausing a RUNNING worker, and a launch held back
    before it spawned — and they must say the same thing, so the sentence lives here.
    """
    _notify(
        "A background worker was paused",
        f"{app}'s worker '{worker}' was paused: {reason}. It resumes on its own "
        "once that clears.",
        {"app": app, "worker": worker, "reason": reason},
    )


class WorkerSupervisor:
    """Owns the table of supervised app workers, keyed ``(app, worker)``."""

    def __init__(self) -> None:
        self._workers: dict[tuple[str, str], SupervisedWorker] = {}
        # Reentrant: the sweep takes the table lock and then calls stop/pause/resume,
        # each of which takes it again. A plain Lock would deadlock the watchdog thread.
        self._lock = threading.RLock()

    # -- lookup -----------------------------------------------------------
    def get(self, app: str, worker: str) -> SupervisedWorker | None:
        """The record for one worker, whatever state it is in.

        Unlike ``BackendSupervisor.get`` this does NOT drop a record whose process died:
        the record IS the state machine here (a dead process in ``RUNNING`` is precisely
        what the watchdog looks for), and a PAUSED record has no process by design.
        """
        with self._lock:
            return self._workers.get((app, worker))

    def list_workers(self, app: str | None = None) -> list[SupervisedWorker]:
        with self._lock:
            return [r for r in self._workers.values() if app is None or r.app == app]

    def list_running(self) -> list[SupervisedWorker]:
        with self._lock:
            return [
                r for r in self._workers.values() if r.state is WorkerState.RUNNING and r.is_alive()
            ]

    # -- lifecycle --------------------------------------------------------
    def start(self, manifest: AppManifest) -> list[SupervisedWorker]:
        """Start every worker *manifest* declares. Idempotent per worker.

        Returns the records that are running afterwards — empty when the app declared no
        worker, or declared workers without holding ``backgroundTasks``.
        """
        app = manifest.name
        started: list[SupervisedWorker] = []
        for spec in _declared_workers(manifest):
            rec = self._start_one(app, spec)
            if rec is not None:
                started.append(rec)
        return started

    def _start_one(self, app: str, spec: "WorkerSpec") -> SupervisedWorker | None:
        with self._lock:
            key = (app, spec.name)
            rec = self._workers.get(key)
            if rec is not None and rec.state is WorkerState.RUNNING and rec.is_alive():
                return rec
            entry = self._resolve_entry(app, spec)
            if entry is None:
                return None
            first_start = rec is None
            if rec is None:
                rec = SupervisedWorker(
                    app=app, worker=spec.name, entry=entry, restart=bool(spec.restart)
                )
                self._workers[key] = rec
            rec.entry = entry
            if first_start:
                # Boot-time reap: a prior gateway that died hard left its workers
                # reparented to init. Path identity, PPID 1 only — see reap_orphans.
                self.reap_orphans(app, entry)
            if not self._spawn(rec):
                # A refused first start leaves no row — except when the refusal was a
                # deliberate PAUSE, whose whole point is a row that says why.
                if first_start and rec.state is WorkerState.RUNNING:
                    self._workers.pop(key, None)
                return None
            return rec

    def _resolve_entry(self, app: str, spec: "WorkerSpec") -> Path | None:
        """The worker's entry file, or ``None`` if it is missing or escapes the app dir."""
        root = app_dir(app)
        entry = (root / spec.entry_point).resolve()
        if not str(entry).startswith(str(root.resolve())) or not entry.is_file():
            logger.warning(
                "app %s worker %s: entry point missing/escapes app dir: %s",
                app,
                spec.name,
                spec.entry_point,
            )
            return None
        return entry

    def _spawn(self, rec: SupervisedWorker) -> bool:
        """THE launch path — start, resume and revive all come through here.

        One gate, so a revoked ``backgroundTasks`` stops a revival as surely as it stops a
        first start, and the pause policy binds a resume as surely as a boot — no second
        copy of either check to drift out of step.
        """
        if not _background_tasks_granted(rec.app):
            logger.info(
                "app %s worker %s refused: permissions.backgroundTasks is not declared",
                rec.app,
                rec.worker,
            )
            return False
        pause_reason = _policy_pause_reason()
        if pause_reason:
            transitioned = rec.state is not WorkerState.PAUSED
            rec.state = WorkerState.PAUSED
            rec.reason = pause_reason
            rec.auto_resume = True
            if transitioned:
                logger.info("app %s worker %s held back: %s", rec.app, rec.worker, pause_reason)
                _notify_paused(rec.app, rec.worker, pause_reason)
            return False
        cmd = BackendSupervisor._launch_cmd("", rec.entry)
        if cmd is None:
            logger.warning(
                "app %s worker %s: cannot determine launcher for %s",
                rec.app,
                rec.worker,
                rec.entry.name,
            )
            return False
        try:
            env = self._child_env(rec)
        except Exception:  # noqa: BLE001 — a broken env is a refused start, not a crash
            logger.warning("app %s worker %s: child env failed", rec.app, rec.worker, exc_info=True)
            return False
        from personalclaw.sandbox import PROFILE_TOOL, spawn_shim_argv

        # Resource ceiling (PHF-1) by argv-prepend, not preexec_fn: this runs on the
        # watchdog daemon thread, and a preexec_fn would fork the whole gateway from a
        # non-loop thread while the loop holds locks (the backend_runtime hazard).
        launch_cmd = spawn_shim_argv(list(cmd), PROFILE_TOOL)
        try:
            proc = subprocess.Popen(  # noqa: S603 — vetted app code, scanned at install
                launch_cmd,
                cwd=str(app_dir(rec.app)),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            logger.warning("app %s worker %s failed to launch: %s", rec.app, rec.worker, exc)
            return False
        rec.proc = proc
        rec.pid = proc.pid
        rec.state = WorkerState.RUNNING
        rec.started_at = time.monotonic()
        rec.reason = ""
        rec.auto_resume = True
        logger.info("app %s worker %s started: pid=%s", rec.app, rec.worker, proc.pid)
        return True

    def _child_env(self, rec: SupervisedWorker) -> dict[str, str]:
        """The worker's environment: the child-env ALLOWLIST plus what this site computes.

        Same allowlist as an app backend (``build_child_env``) — a worker is third-party
        code too — and the same computed contract minus two things a portless child has no
        use for:

        * **``PORT``**, obviously: nothing binds one.
        * **the per-app proxy secret.** ``APP_SECRET_ENV`` exists so a backend can VERIFY
          the ``X-PersonalClaw-Proxy`` signature the gateway attaches on the way in. A
          worker has no inbound surface, so it has no signature to check and the secret has
          no role in its contract — handing a long-lived unattended process a shared HMAC
          key it cannot use only widens what a compromised worker leaks. (It is also what
          the ``core-must-not-import-its-own-published-facade`` ratchet asks for: the
          constant lives in ``sdk/security.py``, and ``backend_runtime``'s import of it is a
          grandfathered upward edge, not a pattern to copy. If a worker ever needs to
          identify itself to the gateway, that belongs on the SDK side of the seam, where
          the constant already is.)
        """
        from personalclaw.apps.backend_runtime import shared_storage_env
        from personalclaw.apps.background import WORKER_ID_ENV
        from personalclaw.apps.manager import app_data_dir
        from personalclaw.apps.permissions import checker_for
        from personalclaw.sandbox import build_child_env

        checker = checker_for(rec.app)
        storage_ok = checker is not None and checker.can_use_storage()
        extra: dict[str, str] = {
            "PERSONALCLAW_APP_NAME": rec.app,
            WORKER_ID_ENV: rec.worker,
        }
        if storage_ok:
            extra["PERSONALCLAW_APP_DATA_DIR"] = str(app_data_dir(rec.app))
        # APE-10 read-only shared mounts: the same grant, computed by the same function the
        # backend site uses, so the two children of one app never disagree about it.
        extra.update(shared_storage_env(rec.app))
        env = build_child_env(site="app-worker", extra=extra)
        if not storage_ok:
            # The gate is enforced where the name would become a variable — an operator's
            # ``sandbox.env_passthrough`` must not re-open the storage capability.
            env.pop("PERSONALCLAW_APP_DATA_DIR", None)
        return env

    def stop(self, app: str, worker: str | None = None) -> int:
        """Stop *app*'s worker(s): graceful first, kill after ``_TERM_TIMEOUT``.

        The record is REMOVED — stopped is the absence of a row, so a re-enabled app's
        worker starts clean and a disabled app's does not linger as a half-state. Returns
        how many live processes were terminated.
        """
        with self._lock:
            recs = [
                r
                for r in self._workers.values()
                if r.app == app and (worker is None or r.worker == worker)
            ]
            stopped = 0
            for rec in recs:
                if self._terminate(rec):
                    stopped += 1
                self._workers.pop(rec.key, None)
                logger.info("app %s worker %s stopped", rec.app, rec.worker)
            return stopped

    def stop_all(self) -> None:
        with self._lock:
            for app in {r.app for r in self._workers.values()}:
                self.stop(app)

    def _terminate(self, rec: SupervisedWorker) -> bool:
        """SIGTERM, then SIGKILL after ``_TERM_TIMEOUT`` — the backend's precedent, reused.

        SIGTERM *is* the cooperative stop: the SDK's worker host installs a handler and
        finishes its current iteration. A worker that ignores it gets killed, which is why
        the timeout is not optional. Returns True if a live process was ended.
        """
        proc = rec.proc
        if proc is None or proc.poll() is not None:
            return False
        try:
            proc.terminate()
            try:
                proc.wait(timeout=_TERM_TIMEOUT)
            except subprocess.TimeoutExpired:
                logger.info(
                    "app %s worker %s ignored SIGTERM after %ss; killing",
                    rec.app,
                    rec.worker,
                    _TERM_TIMEOUT,
                )
                proc.kill()
                proc.wait(timeout=_TERM_TIMEOUT)
        except OSError:
            logger.debug("app %s worker %s stop: process already gone", rec.app, rec.worker)
        return True

    def pause(self, app: str, worker: str, reason: str, *, auto_resume: bool = True) -> bool:
        """Suspend a worker but KEEP its row, so the sweep can bring it back.

        Returns True only on the RUNNING→PAUSED transition, so a caller that notifies does
        so once per breach instead of once per sweep for as long as the breach lasts.
        """
        with self._lock:
            rec = self._workers.get((app, worker))
            if rec is None or rec.state is WorkerState.FAILED:
                return False
            transitioned = rec.state is not WorkerState.PAUSED
            self._terminate(rec)
            rec.state = WorkerState.PAUSED
            rec.reason = reason
            rec.auto_resume = auto_resume
            if transitioned:
                logger.info("app %s worker %s paused: %s", app, worker, reason)
            return transitioned

    def resume(self, app: str, worker: str) -> SupervisedWorker | None:
        """Bring a PAUSED worker back. Its restart counter resets — a pause is not a crash."""
        with self._lock:
            rec = self._workers.get((app, worker))
            if rec is None or rec.state is not WorkerState.PAUSED:
                return None
            rec.restarts = 0
            if not self._spawn(rec):
                return None
            logger.info("app %s worker %s resumed", app, worker)
            return rec

    # -- boot-time orphan reaping -----------------------------------------
    def reap_orphans(self, app: str, entry: Path) -> int:
        """Kill every TRULY ORPHANED (PPID 1) process running *entry*.

        Delegated to ``BackendSupervisor.reap_orphans`` — one PPID walk for the whole tree.
        The delegate's ``owned`` set (its own live pids) is empty on a fresh instance, and
        that is correct rather than a gap: the guard that protects a live process is
        ``ppid != 1``, and every worker this supervisor owns has *this* process as its
        parent, so none can be PPID 1 while we are alive. A fresh instance rather than the
        singleton keeps a recycled pid recorded by some other test from making a real
        orphan unreapable.
        """
        return BackendSupervisor().reap_orphans(app, entry)

    # -- the sweep --------------------------------------------------------
    def sweep(self) -> None:
        """One watchdog pass: enforce policy, revive crashes, stop what should not run."""
        if os.environ.get(_SKIP_ENV):
            return
        from personalclaw.apps.manager import list_apps

        pause_reason = _policy_pause_reason()
        declared: set[tuple[str, str]] = set()
        for info in list_apps():
            app = str(info.get("name", ""))
            if not app:
                continue
            if not info.get("enabled", False):
                self.stop(app)  # "stops on disable", enforced without a lifecycle hook
                continue
            manifest = _manifest_for(app)
            if manifest is None:
                continue
            try:
                specs = _declared_workers(manifest)
            except Exception:  # noqa: BLE001
                logger.debug("app %s: worker declaration unreadable", app, exc_info=True)
                continue
            if not specs:
                continue
            if not _background_tasks_granted(app):
                # The grant can be withdrawn while a worker RUNS (an app update rewrites
                # app.json). ``_spawn``'s gate binds every launch, but a gate that only
                # bound at launch would let a de-permissioned worker keep running until it
                # happened to crash — so revocation is enforced here too, on the state.
                self.stop(app)
                continue
            for spec in specs:
                declared.add((app, spec.name))
                self._sweep_one(app, spec, pause_reason)
        # A row whose declaration is gone (uninstalled, renamed, or the app.json changed)
        # must not keep a process alive — that is the other half of "no orphan on
        # uninstall", the half reaping cannot see because the process is still parented.
        for rec in self.list_workers():
            if rec.key not in declared:
                self.stop(rec.app, rec.worker)

    def _sweep_one(self, app: str, spec: "WorkerSpec", pause_reason: str) -> None:
        rec = self.get(app, spec.name)
        if pause_reason:
            if rec is None or rec.state is not WorkerState.RUNNING:
                return  # nothing to pause, and nothing starts under a live breach
            if self.pause(app, spec.name, pause_reason):
                _notify_paused(app, spec.name, pause_reason)
            return
        if rec is None:
            self._start_one(app, spec)
            return
        if rec.state is WorkerState.FAILED:
            return  # the give-up is sticky
        if rec.state is WorkerState.PAUSED:
            if rec.auto_resume:
                self.resume(app, spec.name)
            return
        if rec.is_alive():
            return
        self._revive(rec)

    def _revive(self, rec: SupervisedWorker) -> None:
        """Relaunch a crashed worker, or give up — bounded, and observably so."""
        with self._lock:
            if not rec.restart:
                rec.state = WorkerState.FAILED
                rec.reason = "the worker exited and declared restart=False"
                logger.info("app %s worker %s: %s", rec.app, rec.worker, rec.reason)
                return
            now = time.monotonic()
            if rec.started_at and (now - rec.started_at) >= _HEALTHY_UPTIME_SECS:
                # It stayed up long enough to count as healthy — this is a fresh crash.
                rec.restarts = 0
            rec.restarts += 1
            if rec.restarts > _MAX_RESTARTS:
                rec.state = WorkerState.FAILED
                rec.reason = (
                    f"crash-loop: gave up after {_MAX_RESTARTS} restarts, none of which "
                    f"stayed up {_HEALTHY_UPTIME_SECS:.0f}s"
                )
                logger.warning("app %s worker %s: %s", rec.app, rec.worker, rec.reason)
                _notify(
                    "A background worker keeps crashing",
                    f"{app_label(rec)} was restarted {_MAX_RESTARTS} times and kept "
                    "failing, so it has been stopped. Check the app's worker.",
                    {"app": rec.app, "worker": rec.worker, "reason": rec.reason},
                )
                return
            if self._spawn(rec):
                logger.info(
                    "watchdog: revived app %s worker %s (pid=%s, restart %d/%d)",
                    rec.app,
                    rec.worker,
                    rec.pid,
                    rec.restarts,
                    _MAX_RESTARTS,
                )


_supervisor: WorkerSupervisor | None = None


def get_worker_supervisor() -> WorkerSupervisor:
    """Process-wide singleton worker supervisor."""
    global _supervisor
    if _supervisor is None:
        _supervisor = WorkerSupervisor()
    return _supervisor


def start_worker_watchdog() -> threading.Thread:
    """Start the daemon thread that sweeps every ``_WATCHDOG_INTERVAL`` seconds.

    The equivalent of ``start_backend_watchdog``, and it wants the same call site:
    ``providers/loader.py`` starts that one at boot. Returned for testing.
    """

    def _loop() -> None:
        while True:
            time.sleep(_WATCHDOG_INTERVAL)
            try:
                get_worker_supervisor().sweep()
            except Exception:  # noqa: BLE001 — one bad sweep must not end the watchdog
                logger.debug("app-worker watchdog sweep failed", exc_info=True)

    t = threading.Thread(target=_loop, name="app-worker-watchdog", daemon=True)
    t.start()
    logger.info("app-worker watchdog started (interval=%ds)", _WATCHDOG_INTERVAL)
    return t
