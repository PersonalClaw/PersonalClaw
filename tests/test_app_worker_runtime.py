"""The supervision half of APE-3: what `apps/worker_runtime.py` promises about processes.

`permissions.backgroundTasks` shipped in APE-1 with the manifest itself admitting the gap —
`apps/manifest.py:405`: "unlike ``backgroundTasks`` above, whose host still does not exist".
These tests are the host's contract, and they are deliberately split by what the claim is
ABOUT:

* **Claims about processes are made with real processes.** Crash survival kills a real
  child and asserts a real replacement. Graceful-vs-kill teardown is decided by the child's
  own `returncode` (0 from its SIGTERM handler, `-SIGKILL` when it ignored the signal), not
  by which branch we believe was taken. Reaping spawns a genuine orphan through
  `/bin/sh … &` and a genuine still-parented sibling running the SAME entry path, and
  asserts BOTH directions — a reaper that kills live processes is worse than one that
  misses orphans.
* **Claims about policy are made with fakes.** The crash-loop bound, the budget breach and
  the notification are decisions, so they are driven by patching the real seams
  (`guardrails.budgets.budget_from_config`/`get_meter`, `guardrails.incident.incident_active`,
  `action_providers.services.get_action_services`) rather than by spending money or
  declaring an incident.

`apps/background.py` (the sibling half — the declaration shape and the SDK's
`register_worker`) does not exist on this branch, so a stub module is installed into
`sys.modules` under its real name. That keeps this suite standalone AND keeps
`worker_runtime`'s real `from personalclaw.apps.background import …` statements on the
executed path, so the names/signatures this file stubs are exactly the ones integration has
to reconcile: `WorkerSpec(name, entry_point, restart=True)`,
`declared_workers(manifest) -> list[WorkerSpec]`, and `WORKER_NAME_ENV: str`.

No test here sleeps for a fixed duration in place of a condition: every wait polls to a
deadline, because a fixed sleep measures a skeleton and goes flaky under xdist. Every
process a test creates is registered with the `spawned` fixture, which kills leftovers at
teardown and then ASSERTS none survived.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from personalclaw.apps import manager, worker_runtime
from personalclaw.apps.manifest import AppManifest
from personalclaw.apps.worker_runtime import SupervisedWorker, WorkerState, WorkerSupervisor

#: A spawn goes through a fresh interpreter (plus the ceiling shim), and under full-suite
#: xdist load a 0.3s spawn can cost tens of seconds of wall time. Same headroom the sibling
#: app-backend suites use.
_WAIT_SECS = 90

#: Poll granularity for every deadline wait below.
_TICK = 0.02


# ── worker bodies (real programs; the path they write is baked into the SOURCE) ──


def _body_sleep() -> str:
    return "import time\nwhile True:\n    time.sleep(0.05)\n"


def _body_ready_then_sleep(ready: Path) -> str:
    return (
        "import time, pathlib\n"
        f"pathlib.Path({str(ready)!r}).write_text('up')\n"
        "while True:\n    time.sleep(0.05)\n"
    )


def _body_cooperative(ready: Path, marker: Path) -> str:
    """Honors SIGTERM: writes *marker* from the handler and exits 0 — the cooperative stop."""
    return (
        "import os, signal, time, pathlib\n"
        "def _bye(signum, frame):\n"
        f"    pathlib.Path({str(marker)!r}).write_text(str(signum))\n"
        "    os._exit(0)\n"
        "signal.signal(signal.SIGTERM, _bye)\n"
        f"pathlib.Path({str(ready)!r}).write_text('up')\n"
        "while True:\n    time.sleep(0.05)\n"
    )


def _body_stubborn(ready: Path) -> str:
    """Ignores SIGTERM entirely, so only SIGKILL ends it."""
    return (
        "import signal, time, pathlib\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pathlib.Path({str(ready)!r}).write_text('up')\n"
        "while True:\n    time.sleep(0.05)\n"
    )


def _body_dies() -> str:
    return "raise SystemExit(3)\n"


# ── process bookkeeping ──


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _ppid_of(pid: int) -> int | None:
    out = os.popen(f"ps -o ppid= -p {int(pid)} 2>/dev/null").read().strip()
    try:
        return int(out)
    except ValueError:
        return None


def _wait_for(predicate: Callable[[], bool], *, what: str, timeout: float = _WAIT_SECS) -> None:
    """Poll *predicate* to a deadline. Never a bare sleep — that measures a skeleton."""
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if predicate():
            return
        _time.sleep(_TICK)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


@dataclass
class _Spawned:
    """Everything a test started, so teardown can prove nothing was left running."""

    procs: list[subprocess.Popen] = field(default_factory=list)
    orphan_pids: list[int] = field(default_factory=list)


@pytest.fixture
def spawned() -> Iterator[_Spawned]:
    """Reap what the test spawned, and ASSERT the reaping worked.

    Supervised children are killed through their `Popen` handle (no pid-reuse hazard — we
    hold the handle and it is not reaped until we wait on it). Deliberately-orphaned pids
    have no handle, so they are killed by pid, the same way `test_backend_runtime_reap.py`
    handles its own orphans.
    """
    tracked = _Spawned()
    try:
        yield tracked
    finally:
        for proc in tracked.procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=30)
        for pid in tracked.orphan_pids:
            if _pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        _wait_for(
            lambda: all(p.poll() is not None for p in tracked.procs)
            and all(not _pid_alive(pid) for pid in tracked.orphan_pids),
            what="every process this test spawned to be gone",
            timeout=30,
        )
        assert all(p.poll() is not None for p in tracked.procs), "a supervised child survived"
        assert all(
            not _pid_alive(pid) for pid in tracked.orphan_pids
        ), "an orphaned child survived the test"


@pytest.fixture
def sup(spawned: _Spawned) -> Iterator[WorkerSupervisor]:
    """A fresh supervisor (never the process-wide singleton) that is emptied at teardown."""
    supervisor = WorkerSupervisor()
    try:
        yield supervisor
    finally:
        for rec in supervisor.list_workers():
            if rec.proc is not None:
                spawned.procs.append(rec.proc)
        supervisor.stop_all()


# ── isolation ──


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Apps, secrets, data dirs and guardrail state all live under a tmp home."""
    from personalclaw.config import loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


#: The declaration registry the stub `apps.background` answers from — `app name -> specs`.
_DECLARED: dict[str, list[Any]] = {}


@pytest.fixture(autouse=True)
def _stub_background(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.ModuleType]:
    """Inject per-test worker declarations into the REAL supervisor.

    Integration re-homed `WorkerSpec`/`declared_workers` from `apps/background.py` into
    `worker_runtime` itself: they are PARENT-side bookkeeping an app author never reads, and
    routing them through the app-facing SDK facade would have exported names with no
    app-side consumer. `apps/background.py` is no longer stubbed at all — the real module is
    imported, so `BACKGROUND_TASKS_PERMISSION`, `WORKER_ENTRY_POINT`, `WORKER_DEFAULT_NAME`
    and `WORKER_ID_ENV` are the shipped values rather than a fixture's guess at them.

    What still needs injecting is WHICH workers a manifest declares, because the real
    `declared_workers` derives exactly one from the permission and these tests need several
    (a second worker, a missing entry point, an escaping path).
    """
    from personalclaw.apps import worker_runtime as _wr

    monkeypatch.setattr(
        _wr, "declared_workers", lambda manifest: list(_DECLARED.get(manifest.name, []))
    )
    _DECLARED.clear()
    try:
        yield _wr
    finally:
        _DECLARED.clear()


def _spec(module: types.ModuleType, name: str, entry_point: str, *, restart: bool = True) -> Any:
    spec_cls = module.WorkerSpec
    return spec_cls(name=name, entry_point=entry_point, restart=restart)


def _install_worker_app(
    home: Path,
    app: str,
    *,
    body: str,
    workers: list[Any],
    background_tasks: bool = True,
    enabled: bool = True,
    entry_name: str = "worker.py",
) -> AppManifest:
    """Install an app with a real worker file, and register what it declares."""
    appdir = home / "apps" / app
    appdir.mkdir(parents=True, exist_ok=True)
    permissions: dict[str, Any] = {}
    if background_tasks:
        permissions["backgroundTasks"] = True
    (appdir / "app.json").write_text(
        json.dumps(
            {
                "name": app,
                "version": "1.0.0",
                "displayName": app,
                "description": "worker fixture",
                "permissions": permissions,
            }
        ),
        encoding="utf-8",
    )
    (appdir / "installed.json").write_text(
        json.dumps({"name": app, "version": "1.0.0", "enabled": enabled}), encoding="utf-8"
    )
    (appdir / entry_name).write_text(body, encoding="utf-8")
    _DECLARED[app] = workers
    return AppManifest.from_json_file(appdir / "app.json")


# ── notification capture ──


class _RecordingState:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str, str, dict]] = []

    def notify(self, kind: str, title: str, body: str, *, meta: dict | None = None) -> None:
        self.notes.append((kind, title, body, dict(meta or {})))


@pytest.fixture
def notes(monkeypatch: pytest.MonkeyPatch) -> _RecordingState:
    """Capture notifications instead of delivering them, at the real seam `_notify` uses."""
    from personalclaw.action_providers import services

    state = _RecordingState()
    monkeypatch.setattr(services, "get_action_services", lambda: types.SimpleNamespace(state=state))
    return state


# ══ runs: the permission means something ══


def test_a_worker_needs_the_backgroundtasks_grant(
    tmp_path: Path, sup: WorkerSupervisor, _stub_background: types.ModuleType
) -> None:
    """No `backgroundTasks` → no worker. The SAME fixture WITH it starts, so this is not
    a test that would pass against a supervisor which never starts anything."""
    ungranted = _install_worker_app(
        tmp_path,
        "nogrant",
        body=_body_sleep(),
        workers=[_spec(_stub_background, "tick", "worker.py")],
        background_tasks=False,
    )
    assert sup.start(ungranted) == []
    assert sup.get("nogrant", "tick") is None, "a refused worker must leave no row"
    assert sup.list_running() == []

    granted = _install_worker_app(
        tmp_path,
        "granted",
        body=_body_sleep(),
        workers=[_spec(_stub_background, "tick", "worker.py")],
    )
    started = sup.start(granted)
    assert len(started) == 1, "the grant path is broken, so the refusal above proves nothing"
    assert started[0].is_alive()


def test_a_started_worker_is_tracked_by_get_and_list(
    tmp_path: Path, sup: WorkerSupervisor, _stub_background: types.ModuleType
) -> None:
    ready = tmp_path / "ready"
    manifest = _install_worker_app(
        tmp_path,
        "tracked",
        body=_body_ready_then_sleep(ready),
        workers=[_spec(_stub_background, "beat", "worker.py")],
    )
    (rec,) = sup.start(manifest)
    _wait_for(ready.exists, what="the worker to report itself up")

    assert rec.state is WorkerState.RUNNING
    assert rec.pid > 0 and _pid_alive(rec.pid)
    assert sup.get("tracked", "beat") is rec
    assert [r.key for r in sup.list_running()] == [("tracked", "beat")]
    assert [r.key for r in sup.list_workers("tracked")] == [("tracked", "beat")]
    assert sup.get("tracked", "nosuch") is None


# ══ survives a crash (watchdog) ══


def test_the_sweep_revives_a_worker_whose_process_died(
    tmp_path: Path, sup: WorkerSupervisor, _stub_background: types.ModuleType
) -> None:
    """A real child, really killed, really replaced."""
    manifest = _install_worker_app(
        tmp_path,
        "crashy",
        body=_body_sleep(),
        workers=[_spec(_stub_background, "loop", "worker.py")],
    )
    (rec,) = sup.start(manifest)
    first_pid = rec.pid
    assert rec.is_alive(), "the first child never ran, so a revival would prove nothing"

    assert rec.proc is not None
    rec.proc.kill()
    _wait_for(lambda: not rec.is_alive(), what="the killed child to be reaped")

    sup.sweep()

    revived = sup.get("crashy", "loop")
    assert revived is not None and revived.state is WorkerState.RUNNING
    assert revived.pid != first_pid, "the pid did not change — nothing was relaunched"
    assert revived.is_alive()
    assert revived.restarts == 1


def test_the_crash_loop_bound_gives_up_observably(
    tmp_path: Path,
    sup: WorkerSupervisor,
    notes: _RecordingState,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    _stub_background: types.ModuleType,
) -> None:
    """A worker that always dies is given up on — and the give-up is a readable fact.

    A silent give-up is indistinguishable from a working worker, so all three observable
    forms are asserted: the record's state/reason, the warning log, and the notification.
    """
    monkeypatch.setattr(worker_runtime, "_MAX_RESTARTS", 2)
    # A large healthy-uptime means no revive can ever "count as healthy" and reset the
    # counter, which is what makes this deterministic rather than a race with the clock.
    monkeypatch.setattr(worker_runtime, "_HEALTHY_UPTIME_SECS", 10_000.0)

    manifest = _install_worker_app(
        tmp_path,
        "doomed",
        body=_body_dies(),
        workers=[_spec(_stub_background, "boom", "worker.py")],
    )
    (rec,) = sup.start(manifest)

    with caplog.at_level(logging.WARNING, logger="personalclaw.apps.worker_runtime"):
        for _ in range(4):
            _wait_for(lambda: not rec.is_alive(), what="the doomed child to exit")
            sup.sweep()
            if rec.state is WorkerState.FAILED:
                break

    assert rec.state is WorkerState.FAILED, f"never gave up (restarts={rec.restarts})"
    assert rec.restarts == 3, "gave up at the wrong count for _MAX_RESTARTS=2"
    assert "crash-loop" in rec.reason and "gave up" in rec.reason
    assert not rec.is_alive()
    assert any("crash-loop" in r.getMessage() for r in caplog.records), "the give-up was silent"
    assert [n[1] for n in notes.notes] == ["A background worker keeps crashing"]
    assert notes.notes[0][3]["worker"] == "boom"

    # Sticky: a FAILED worker is not quietly restarted by the next sweep.
    pid_at_give_up = rec.pid
    sup.sweep()
    assert rec.state is WorkerState.FAILED
    assert rec.pid == pid_at_give_up and rec.restarts == 3


# ══ stops on disable: graceful first, kill second ══


def test_stop_takes_the_graceful_path_when_the_worker_cooperates(
    tmp_path: Path, sup: WorkerSupervisor, _stub_background: types.ModuleType
) -> None:
    """Proven by the child's own exit status and its handler's marker, not by our belief."""
    ready, marker = tmp_path / "ready", tmp_path / "sigterm-seen"
    manifest = _install_worker_app(
        tmp_path,
        "polite",
        body=_body_cooperative(ready, marker),
        workers=[_spec(_stub_background, "w", "worker.py")],
    )
    (rec,) = sup.start(manifest)
    _wait_for(ready.exists, what="the worker to install its SIGTERM handler")

    assert sup.stop("polite") == 1
    assert marker.exists(), "the child never saw SIGTERM — the stop was not graceful"
    assert marker.read_text() == str(int(signal.SIGTERM))
    assert rec.proc is not None and rec.proc.returncode == 0, (
        "the child did not exit through its own handler",
        rec.proc.returncode if rec.proc else None,
    )
    assert sup.get("polite", "w") is None, "a stopped worker keeps no row"


def test_stop_kills_a_worker_that_ignores_sigterm(
    tmp_path: Path,
    sup: WorkerSupervisor,
    monkeypatch: pytest.MonkeyPatch,
    _stub_background: types.ModuleType,
) -> None:
    """The kill path is what makes 'graceful' a preference rather than a hope."""
    monkeypatch.setattr(worker_runtime, "_TERM_TIMEOUT", 0.5)
    ready = tmp_path / "ready"
    manifest = _install_worker_app(
        tmp_path,
        "stubborn",
        body=_body_stubborn(ready),
        workers=[_spec(_stub_background, "w", "worker.py")],
    )
    (rec,) = sup.start(manifest)
    _wait_for(ready.exists, what="the worker to start ignoring SIGTERM")

    assert sup.stop("stubborn") == 1
    assert rec.proc is not None
    assert rec.proc.returncode == -signal.SIGKILL, (
        "the stubborn child did not end on SIGKILL",
        rec.proc.returncode,
    )
    assert sup.get("stubborn", "w") is None


def test_a_disabled_app_loses_its_worker_on_the_next_sweep(
    tmp_path: Path, sup: WorkerSupervisor, _stub_background: types.ModuleType
) -> None:
    """'Stops on disable' holds without a lifecycle hook: the sweep is the enforcement."""
    manifest = _install_worker_app(
        tmp_path,
        "toggle",
        body=_body_sleep(),
        workers=[_spec(_stub_background, "w", "worker.py")],
    )
    (rec,) = sup.start(manifest)
    assert rec.is_alive()

    (tmp_path / "apps" / "toggle" / "installed.json").write_text(
        json.dumps({"name": "toggle", "version": "1.0.0", "enabled": False}), encoding="utf-8"
    )
    sup.sweep()

    assert sup.get("toggle", "w") is None
    _wait_for(lambda: not rec.is_alive(), what="the disabled app's worker to die")


# ══ pause is not stop ══


def test_pause_keeps_the_row_that_stop_removes(
    tmp_path: Path, sup: WorkerSupervisor, _stub_background: types.ModuleType
) -> None:
    """Paused and stopped are different states, and a paused worker can come back.

    Collapsing them costs one of two things: a budget breach permanently killing a worker
    the user never disabled, or a disabled app's worker returning on the next sweep.
    """
    manifest = _install_worker_app(
        tmp_path,
        "pausable",
        body=_body_sleep(),
        workers=[_spec(_stub_background, "w", "worker.py")],
    )
    (rec,) = sup.start(manifest)
    first_pid = rec.pid

    assert sup.pause("pausable", "w", "a test reason") is True
    paused = sup.get("pausable", "w")
    assert paused is not None, "pause removed the row — that is stop, not pause"
    assert paused.state is WorkerState.PAUSED
    assert paused.reason == "a test reason"
    _wait_for(lambda: not paused.is_alive(), what="the paused worker's process to end")
    # Idempotent: a second pause is not a new transition (so a caller notifies once).
    assert sup.pause("pausable", "w", "a test reason") is False

    resumed = sup.resume("pausable", "w")
    assert resumed is not None, "a paused worker must be resumable"
    assert resumed.state is WorkerState.RUNNING and resumed.is_alive()
    assert resumed.pid != first_pid
    assert resumed.reason == ""

    # And now the contrast, on the very same worker.
    assert sup.stop("pausable", "w") == 1
    assert sup.get("pausable", "w") is None, "stop must remove the row pause keeps"


# ══ budget breach pauses it + notifies ══


def _breach(monkeypatch: pytest.MonkeyPatch, *, exceeded: bool) -> None:
    """Drive the REAL budget seam `_budget_pause_reason` reads: `budgets.budget_from_config`
    plus the day-scope verdict from `budgets.get_meter().check_day`."""
    from personalclaw.guardrails import budgets

    monkeypatch.setattr(budgets, "budget_from_config", lambda: budgets.Budget(max_dollars=1.0))
    verdict = budgets.BudgetVerdict.EXCEEDED if exceeded else budgets.BudgetVerdict.OK
    reason = "day dollar budget exceeded ($2.5/$1)" if exceeded else ""
    meter = types.SimpleNamespace(check_day=lambda budget: (verdict, reason))
    monkeypatch.setattr(budgets, "get_meter", lambda: meter)


def test_a_budget_breach_pauses_the_worker_and_notifies(
    tmp_path: Path,
    sup: WorkerSupervisor,
    notes: _RecordingState,
    monkeypatch: pytest.MonkeyPatch,
    _stub_background: types.ModuleType,
) -> None:
    """Both effects, or the clause is half-shipped: it pauses, AND the user is told."""
    from personalclaw import notification_kinds

    manifest = _install_worker_app(
        tmp_path,
        "spender",
        body=_body_sleep(),
        workers=[_spec(_stub_background, "w", "worker.py")],
    )
    (rec,) = sup.start(manifest)
    assert rec.is_alive() and notes.notes == []

    _breach(monkeypatch, exceeded=True)
    sup.sweep()

    assert rec.state is WorkerState.PAUSED, "a breach did not pause the worker"
    assert "exceeded" in rec.reason
    _wait_for(lambda: not rec.is_alive(), what="the paused worker's process to end")
    assert len(notes.notes) == 1, "the breach was silent"
    kind, title, body, meta = notes.notes[0]
    assert kind == notification_kinds.WARNING
    assert title == "A background worker was paused"
    assert "exceeded" in body and meta["app"] == "spender" and meta["worker"] == "w"

    # A breach that persists does not re-notify every 30 seconds.
    sup.sweep()
    assert len(notes.notes) == 1

    # And the pause is a deferral: clearing the breach resumes it, unattended.
    _breach(monkeypatch, exceeded=False)
    sup.sweep()
    assert rec.state is WorkerState.RUNNING and rec.is_alive()


def test_a_breach_holds_back_a_worker_that_was_not_yet_running(
    tmp_path: Path,
    sup: WorkerSupervisor,
    notes: _RecordingState,
    monkeypatch: pytest.MonkeyPatch,
    _stub_background: types.ModuleType,
) -> None:
    """`start` under a live breach must not spawn — and must still say why in a row."""
    _breach(monkeypatch, exceeded=True)
    manifest = _install_worker_app(
        tmp_path,
        "held",
        body=_body_sleep(),
        workers=[_spec(_stub_background, "w", "worker.py")],
    )
    assert sup.start(manifest) == []
    rec = sup.get("held", "w")
    assert rec is not None and rec.state is WorkerState.PAUSED
    assert rec.proc is None, "a held worker must never have been spawned"
    assert "exceeded" in rec.reason
    assert len(notes.notes) == 1


def test_incident_mode_pauses_a_worker_too(
    tmp_path: Path,
    sup: WorkerSupervisor,
    monkeypatch: pytest.MonkeyPatch,
    _stub_background: types.ModuleType,
) -> None:
    """The kill-switch half of the same policy: unattended work is suspended."""
    from personalclaw.guardrails import incident

    manifest = _install_worker_app(
        tmp_path,
        "incidental",
        body=_body_sleep(),
        workers=[_spec(_stub_background, "w", "worker.py")],
    )
    (rec,) = sup.start(manifest)
    assert rec.is_alive()

    monkeypatch.setattr(incident, "incident_active", lambda: True)
    sup.sweep()
    assert rec.state is WorkerState.PAUSED and "incident" in rec.reason


# ══ uninstall leaves no orphan (PPID-reaping, both directions) ══


def _worker_entry(tmp_path: Path, app: str = "reapme") -> Path:
    appdir = tmp_path / "apps" / app
    appdir.mkdir(parents=True, exist_ok=True)
    entry = appdir / "worker.py"
    entry.write_text(_body_sleep(), encoding="utf-8")
    return entry.resolve()


def test_reaping_takes_the_orphan_and_leaves_a_live_parented_process_alone(
    tmp_path: Path, sup: WorkerSupervisor, spawned: _Spawned
) -> None:
    """The direction that matters is the second one.

    A reaper that misses an orphan leaks a process. A reaper that kills a process whose
    parent is alive kills a working worker out from under a live supervisor — another
    gateway instance, or this very pytest run. Both are asserted, and the orphan's PPID is
    confirmed to be 1 first so the "reaped" half cannot pass by the process having simply
    exited on its own.
    """
    entry = _worker_entry(tmp_path)

    # (1) A genuine orphan: an intermediate shell backgrounds the worker and exits, so the
    # worker is re-parented to init/launchd. fds are detached or `subprocess.run` would
    # block on the pipe forever.
    out = subprocess.run(  # noqa: S603 — test fixture, static argv
        ["/bin/sh", "-c", f'"{sys.executable}" "{entry}" >/dev/null 2>&1 </dev/null & echo $!'],
        capture_output=True,
        text=True,
        check=True,
    )
    orphan_pid = int(out.stdout.strip())
    spawned.orphan_pids.append(orphan_pid)

    # (2) A sibling running the SAME entry path whose parent (this test) is alive.
    parented = subprocess.Popen([sys.executable, str(entry)])  # noqa: S603 — test fixture
    spawned.procs.append(parented)

    _wait_for(lambda: _ppid_of(orphan_pid) == 1, what="the orphan to be re-parented to init")
    assert _pid_alive(orphan_pid) and parented.poll() is None, "a fixture process died early"

    reaped = sup.reap_orphans("reapme", entry)

    assert reaped == 1, f"expected exactly the orphan to be reaped, got {reaped}"
    _wait_for(lambda: not _pid_alive(orphan_pid), what="the orphan to be gone")
    assert parented.poll() is None, (
        "the reaper killed a process whose parent is ALIVE — this is the failure that "
        "destroys a concurrent gateway's or a test run's work"
    )


def test_a_worker_whose_declaration_vanished_is_stopped(
    tmp_path: Path, sup: WorkerSupervisor, _stub_background: types.ModuleType
) -> None:
    """The other half of 'uninstall leaves no orphan': the still-parented half.

    Reaping only sees PPID-1 processes, so a worker this gateway still owns must be stopped
    by the sweep when its declaration disappears — which is what uninstall looks like.
    """
    manifest = _install_worker_app(
        tmp_path,
        "goner",
        body=_body_sleep(),
        workers=[_spec(_stub_background, "w", "worker.py")],
    )
    (rec,) = sup.start(manifest)
    assert rec.is_alive()

    _DECLARED["goner"] = []
    sup.sweep()

    assert sup.get("goner", "w") is None
    _wait_for(lambda: not rec.is_alive(), what="the undeclared worker to be stopped")


def test_a_revoked_permission_stops_the_worker(
    tmp_path: Path, sup: WorkerSupervisor, _stub_background: types.ModuleType
) -> None:
    """The grant is checked on every launch, so revoking it is not merely cosmetic."""
    manifest = _install_worker_app(
        tmp_path,
        "revoked",
        body=_body_sleep(),
        workers=[_spec(_stub_background, "w", "worker.py")],
    )
    (rec,) = sup.start(manifest)
    assert rec.is_alive()

    appdir = tmp_path / "apps" / "revoked"
    data = json.loads((appdir / "app.json").read_text(encoding="utf-8"))
    data["permissions"] = {}
    (appdir / "app.json").write_text(json.dumps(data), encoding="utf-8")

    sup.sweep()
    assert sup.get("revoked", "w") is None
    _wait_for(lambda: not rec.is_alive(), what="the worker of a de-permissioned app to die")


# ══ the child's contract ══


def test_the_worker_child_gets_its_name_and_not_the_gateway_environment(
    tmp_path: Path, sup: WorkerSupervisor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker is handed its identity by env (never a guessed path), from an ALLOWLIST.

    Same posture as an app backend, minus `PORT` — a portless child has no use for one.
    """
    monkeypatch.setenv("ACME_CLOUD_API_KEY", "planted-value-9e12")
    dump = tmp_path / "env.json"
    body = (
        "import json, os, pathlib\n"
        f"pathlib.Path({str(dump)!r}).write_text(json.dumps(dict(os.environ)))\n"
    )
    from personalclaw.apps import worker_runtime as module

    manifest = _install_worker_app(
        tmp_path,
        "envprobe",
        body=body,
        workers=[_spec(module, "probe", "worker.py")],
    )
    (rec,) = sup.start(manifest)
    _wait_for(lambda: dump.exists() and dump.stat().st_size > 0, what="the worker's env dump")
    env = json.loads(dump.read_text(encoding="utf-8"))

    assert env.get("PERSONALCLAW_APP_NAME") == "envprobe"
    assert env.get("PERSONALCLAW_APP_WORKER") == "probe"
    assert "PORT" not in env, "a portless worker was handed a port"
    assert "PERSONALCLAW_APP_SECRET" not in env, (
        "a worker was handed the proxy-signature secret it has no inbound surface to "
        "verify — that is blast radius for nothing"
    )
    assert "ACME_CLOUD_API_KEY" not in env, "the child inherited a gateway credential"
    assert "PATH" in env, "the child got no PATH — the spawn, not the filter, is broken"
    # Storage was not declared, so no sanctioned place to persist was handed over.
    assert "PERSONALCLAW_APP_DATA_DIR" not in env
    assert isinstance(rec, SupervisedWorker)


def test_the_worker_spawn_actually_goes_through_the_ceiling_shim():
    """The spawn-ceiling AUDIT pins the classification; this pins the behaviour.

    Measured, not assumed: deleting `spawn_shim_argv` from `_spawn` and spawning the bare
    command leaves `tests/test_spawn_ceiling_audit.py` fully GREEN (3 passed). That audit
    asserts every spawn site is *classified*, not that any site is *ceilinged* — so the entry
    reading "app background worker -> tool ceiling via spawn_shim_argv" is a claim nothing
    checked. An unceilinged worker matters more than an unceilinged one-shot: it is
    long-lived and unattended, which is the fork bomb nobody is watching.

    Asserted by AST on the call, not by grepping the file, because a mention in a docstring
    or a comment is not a call.
    """
    import ast
    import inspect
    import textwrap

    from personalclaw.apps import worker_runtime as wr

    # dedent first: `ast.parse` on an indented METHOD source raises IndentationError, which
    # would make this rail a permanent false RED rather than a check (found by running it).
    tree = ast.parse(textwrap.dedent(inspect.getsource(wr.WorkerSupervisor._spawn)))
    called = {
        (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", ""))
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    assert "spawn_shim_argv" in called, (
        "WorkerSupervisor._spawn no longer routes the command through spawn_shim_argv, so the "
        "worker spawns with no resource ceiling. The spawn-ceiling audit will not notice: it "
        "checks the classification table, not the call."
    )
    # Vacuity floor: the AST walk really does see this function's calls.
    assert "Popen" in called or "subprocess" in str(called), called
