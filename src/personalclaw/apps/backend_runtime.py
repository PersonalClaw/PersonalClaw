"""App backend supervisor — launch/track an app's optional backend subprocess.

An app may declare a ``backend`` (``BackendConfig``: ``entryPoint`` / ``port`` /
``healthCheck`` / ``type``). When the app is enabled, the gateway launches that
entry point as an isolated **subprocess** bound to a localhost port, and the REST
layer reverse-proxies ``/apps/{name}/api/*`` to it (A4). This is the isolation
model chosen in the plan (§6.3) over an in-process ASGI mount.

This module owns the process table: start (pick a free port, spawn, record),
stop (terminate + reap), and lookup (the proxy asks "where is app X's backend?").
It does **not** proxy — that's the handler. It does **not** run setup hooks —
that's the lifecycle. A backend is just a localhost process here; its egress is
still subject to the egress layer like any other.

Process model: ``type`` selects the launcher — ``python`` runs
``python <entryPoint>``, ``node`` runs ``node <entryPoint>``; empty auto-detects
from the entry-point suffix (``.py``→python, ``.js``/``.mjs``→node). The chosen
port is passed via ``PORT`` env (the conventional contract) and recorded so the
proxy can reach it.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from personalclaw.apps.manager import app_dir
from personalclaw.apps.manifest import AppManifest

logger = logging.getLogger(__name__)

_TERM_TIMEOUT = 5  # seconds to wait for graceful termination before kill


@dataclass
class RunningBackend:
    name: str
    port: int
    pid: int
    health_check: str = "/health"
    proc: subprocess.Popen | None = field(default=None, repr=False)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


class BackendSupervisor:
    """Owns the table of running app-backend subprocesses (one per app)."""

    def __init__(self) -> None:
        self._procs: dict[str, RunningBackend] = {}
        self._lock = threading.Lock()

    # -- lookup -----------------------------------------------------------
    def get(self, name: str) -> RunningBackend | None:
        with self._lock:
            rb = self._procs.get(name)
            if rb and not rb.is_alive():
                # Process died — drop the stale entry so the proxy 502s honestly.
                self._procs.pop(name, None)
                return None
            return rb

    def list_running(self) -> list[RunningBackend]:
        with self._lock:
            return [rb for rb in self._procs.values() if rb.is_alive()]

    # -- lifecycle --------------------------------------------------------
    def start(self, manifest: AppManifest) -> RunningBackend | None:
        """Launch an app's backend subprocess if it declares one. Idempotent —
        returns the already-running backend if present. ``None`` if the app
        declares no backend entry point."""
        backend = manifest.backend
        if not backend.entryPoint:
            return None
        name = manifest.name
        with self._lock:
            existing = self._procs.get(name)
            if existing and existing.is_alive():
                return existing

            root = app_dir(name)
            entry = (root / backend.entryPoint).resolve()
            # Containment: the entry point must live inside the app dir.
            if not str(entry).startswith(str(root.resolve())) or not entry.is_file():
                logger.warning(
                    "app %s backend entryPoint missing/escapes app dir: %s",
                    name,
                    backend.entryPoint,
                )
                return None

            port = self._resolve_port(backend.port)
            cmd = self._launch_cmd(backend.type, entry)
            if cmd is None:
                logger.warning(
                    "app %s backend: cannot determine launcher for %s", name, backend.entryPoint
                )
                return None

            # The app's isolated, update-surviving storage dir is a stable
            # contract handed to the backend via env (so it never guesses a path
            # relative to __file__). Created up front so the first write works.
            # Gated by the app's declared ``storage`` permission — a backend without
            # it never receives the DATA_DIR, so it has no sanctioned place to
            # persist (untrusted-app sandbox P3: the capability grants the path).
            from personalclaw.apps.manager import app_data_dir
            from personalclaw.apps.permissions import checker_for

            checker = checker_for(name)
            storage_ok = checker is not None and checker.can_use_storage()
            data_dir = app_data_dir(name) if storage_ok else None

            env = dict(os.environ)
            env["PORT"] = str(port)
            env["PERSONALCLAW_APP_NAME"] = name
            if data_dir is not None:
                env["PERSONALCLAW_APP_DATA_DIR"] = str(data_dir)
            else:
                # Storage not declared → don't hand the backend a data dir.
                env.pop("PERSONALCLAW_APP_DATA_DIR", None)
            try:
                proc = subprocess.Popen(  # noqa: S603 — vetted app backend, scanned at install
                    cmd,
                    cwd=str(root),
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                logger.warning("app %s backend failed to launch: %s", name, exc)
                return None
            rb = RunningBackend(
                name=name, port=port, pid=proc.pid, health_check=backend.healthCheck, proc=proc
            )
            self._procs[name] = rb
            logger.info("app %s backend started: pid=%s port=%s", name, proc.pid, port)
            return rb

    def stop(self, name: str) -> bool:
        """Terminate an app's backend subprocess (graceful, then kill). Returns
        True if a process was stopped."""
        with self._lock:
            rb = self._procs.pop(name, None)
        if rb is None or rb.proc is None:
            return False
        proc = rb.proc
        if proc.poll() is not None:
            return False
        try:
            proc.terminate()
            try:
                proc.wait(timeout=_TERM_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=_TERM_TIMEOUT)
        except OSError:
            logger.debug("app %s backend stop: process already gone", name)
        logger.info("app %s backend stopped", name)
        return True

    def stop_all(self) -> None:
        for name in list(self._procs.keys()):
            self.stop(name)

    # -- boot-time orphan reaping -----------------------------------------
    # App backends are subprocesses on auto-ports, so a FRESH gateway (empty
    # in-memory table) can't reclaim a prior gateway's backends by port. If the
    # prior gateway died without a graceful shutdown (crash, `kill -9`, or the
    # double-signal force-exit path) it orphaned its backends (reparented to
    # init). Repeated hard-kills pile up MANY orphans per app. The reliable,
    # self-healing signal is the OS process table itself: on boot, scan for every
    # live process whose command line runs THIS app's exact entry path and reap
    # them all. Path-identity (not a recorded PID number) means no recycled-PID
    # risk and no dependence on a PID file surviving a hard kill.
    def reap_orphans(self, name: str, entry: Path) -> int:
        """Kill every TRULY ORPHANED process running ``entry`` for app ``name``.

        Only processes re-parented to init/launchd (PPID 1) are reaped — a
        process whose parent is still alive belongs to a live supervisor
        (this gateway, another gateway instance, or a test run) and must not
        be killed out from under it. Returns the count reaped. Best-effort."""
        owned = {rb.pid for rb in self._procs.values()}
        reaped = 0
        for pid, ppid in self._pids_running(entry):
            if pid in owned or pid <= 1 or ppid != 1:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                reaped += 1
                logger.info(
                    "app %s backend: reaped orphaned process pid=%s from prior run", name, pid
                )
            except (ProcessLookupError, PermissionError):
                pass
        return reaped

    @staticmethod
    def _pids_running(entry: Path) -> list[tuple[int, int]]:
        """(pid, ppid) pairs whose full command line contains ``entry`` (the
        app's resolved backend entry path). Uses the real ``ps`` directly so a
        test that monkeypatches subprocess.Popen for the spawn path can't
        entangle this read-only lookup. Any failure → empty (conservative —
        reap nothing).

        ``-ww`` disables the command-column truncation Linux ``ps`` applies when
        stdout is not a TTY (it clips ``command=`` to ~screen width, defaulting to
        80 cols). Without it, a backend under a long path (a CI temp dir, a deep
        home) has its entry path clipped off, ``needle`` never matches, and no
        orphan is ever found/reaped. ``-ww`` is a harmless no-op on macOS ``ps``."""
        needle = str(entry)
        pids: list[tuple[int, int]] = []
        try:
            out = os.popen(
                "ps -Awwo pid=,ppid=,command= 2>/dev/null"
            ).read()  # noqa: S605 — static command
        except Exception:  # noqa: BLE001 — never let the probe break the caller
            return pids
        for line in out.splitlines():
            line = line.strip()
            if needle not in line:
                continue
            parts = line.split(None, 2)
            try:
                pids.append((int(parts[0]), int(parts[1])))
            except (ValueError, IndexError):
                continue
        return pids

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _resolve_port(declared: str) -> int:
        if declared and declared != "auto":
            try:
                return int(declared)
            except ValueError:
                logger.debug("invalid declared port %r; falling back to auto", declared)
        # auto: ask the OS for a free ephemeral port.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    @staticmethod
    def _launch_cmd(backend_type: str, entry: Path) -> list[str] | None:
        kind = backend_type.strip().lower()
        if not kind:
            suffix = entry.suffix.lower()
            if suffix == ".py":
                kind = "python"
            elif suffix in (".js", ".mjs", ".cjs"):
                kind = "node"
        if kind in ("python", "asgi"):
            return [sys.executable, str(entry)]
        if kind == "node":
            return ["node", str(entry)]
        return None


_supervisor: BackendSupervisor | None = None


def get_backend_supervisor() -> BackendSupervisor:
    """Process-wide singleton backend supervisor."""
    global _supervisor
    if _supervisor is None:
        _supervisor = BackendSupervisor()
    return _supervisor


# ---------------------------------------------------------------------------
# Watchdog — periodic health check that relaunches crashed backends
# ---------------------------------------------------------------------------

_WATCHDOG_INTERVAL = 30  # seconds between sweeps


def start_backend_watchdog() -> threading.Thread:
    """Start a daemon thread that checks backend health every 30s and
    relaunches any that crashed. Returns the thread (for testing)."""
    import time

    def _loop() -> None:
        while True:
            time.sleep(_WATCHDOG_INTERVAL)
            try:
                _check_and_revive()
            except Exception:
                logger.debug("backend watchdog sweep failed", exc_info=True)

    t = threading.Thread(target=_loop, name="app-backend-watchdog", daemon=True)
    t.start()
    logger.info("app-backend watchdog started (interval=%ds)", _WATCHDOG_INTERVAL)
    return t


def _check_and_revive() -> None:
    """One watchdog sweep: for each enabled app with a backend, ensure the
    process is alive. If it died, relaunch it."""
    import os

    if os.environ.get("PERSONALCLAW_SKIP_APP_BACKENDS"):
        return

    from personalclaw.apps.manager import list_apps
    from personalclaw.apps.manifest import AppManifest

    sup = get_backend_supervisor()
    for app_info in list_apps():
        if not app_info.get("enabled", False):
            continue
        manifest_data = app_info.get("manifest", {})
        if not manifest_data.get("backend", {}).get("entryPoint"):
            continue
        name = app_info.get("name", "")
        rb = sup.get(name)
        if rb is not None:
            continue  # alive and tracked — nothing to do
        # Backend is dead or not tracked — relaunch
        try:
            manifest = AppManifest.from_dict(manifest_data)
            launched = sup.start(manifest)
            if launched is not None:
                logger.info(
                    "watchdog: revived app %s backend (pid=%s port=%s)",
                    name,
                    launched.pid,
                    launched.port,
                )
        except Exception:
            logger.debug("watchdog: failed to revive %s", name, exc_info=True)
