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

Environment: a backend does **not** inherit the gateway's environment. It receives
``sandbox.build_child_env(site="app-backend")`` — the ``CHILD_ENV_BASE_NAMES``
allowlist plus whatever the operator declared in ``sandbox.env_passthrough`` — layered
with the four variables this module computes (``PORT``, ``PERSONALCLAW_APP_NAME``,
``PERSONALCLAW_APP_SECRET``, and ``PERSONALCLAW_APP_DATA_DIR`` when the app holds the
``storage`` capability), plus (APE-10) a read-only ``PERSONALCLAW_APP_SHARED_DIR_<SHARER>``
for every app it holds a ``storageRead`` grant on. See ``docs/architecture/app-platform.md``.
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
from typing import TYPE_CHECKING

from personalclaw.apps.manager import app_dir
from personalclaw.apps.manifest import AppManifest

if TYPE_CHECKING:
    from personalclaw.sandbox_providers import SandboxSpec

logger = logging.getLogger(__name__)

_TERM_TIMEOUT = 5  # seconds to wait for graceful termination before kill


def _sel_capability_grant(*, consumer: str, sharer: str) -> None:
    """Audit one active APE-10 shared-storage read grant on the HMAC-chained SEL.

    Emitted at the moment the grant becomes REAL — when the sharer's data dir is
    mounted into a running consumer backend — mirroring how ``messaging._sel_message``
    audits at the point of enforcement. Never raises: an audit failure must not stop a
    backend from launching."""
    try:
        from datetime import datetime, timezone
        from uuid import uuid4

        from personalclaw.sel import SecurityEvent, sel

        sel().log(
            SecurityEvent(
                event_id=uuid4().hex[:16],
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type="capability_grant",
                caller_identity=f"app:{consumer}",
                agent="personalclaw",
                source="apps",
                operation="storage_shared_read",
                outcome="granted",
                resources=f"sharer={sharer}",
                error="",
            )
        )
    except Exception:  # audit must never break the backend launch
        logger.debug("capability_grant SEL emit failed for %s->%s", consumer, sharer, exc_info=True)


def shared_storage_env(consumer: str) -> dict[str, str]:
    """The read-only shared-storage env mounts a CONSUMER backend is granted (APE-10).

    For every INSTALLED app the consumer holds a double-declared, deny-by-default
    ``storageRead`` grant on (``permissions.can_read_shared_storage`` — consumer names
    it AND the sharer declared ``storageShared``), map
    ``PERSONALCLAW_APP_SHARED_DIR_<SHARER>`` → that app's ``app_data_dir``. An app with
    no grant gets ``{}`` — NO mount. Read-only is the contract: the path points at the
    sharer's live data dir but the SDK hands the consumer a read-only handle
    (``sdk.util.shared_app_data_dir``) and writes stay broker-only (APE-9). Emits one
    ``capability_grant`` SEL per active grant."""
    from personalclaw.apps.manager import app_data_dir, list_apps, shared_dir_env_name
    from personalclaw.apps.permissions import checker_for

    checker = checker_for(consumer)
    if checker is None or not checker.permissions.storageRead:
        return {}
    mounts: dict[str, str] = {}
    for entry in list_apps():
        sharer = entry.get("name", "")
        if not sharer or sharer == consumer:
            continue
        if not checker.can_read_shared_storage(sharer):
            continue
        mounts[shared_dir_env_name(sharer)] = str(app_data_dir(sharer))
        _sel_capability_grant(consumer=consumer, sharer=sharer)
    return mounts


def build_backend_sandbox_spec(
    *,
    workspace_dir: str,
    data_dir: object | None,
    env: dict[str, str],
    port: int,
    can_network: bool,
) -> "SandboxSpec":
    """Map an app's declared permissions to a :class:`SandboxSpec` (EXECUTION-ISOLATION EI-4
    §1.3(4)). Pure — no I/O, no spawn — so the mapping is unit-testable without a running tier.

    * ``permissions.network`` → ``egress_tier``: a backend WITHOUT the network permission runs
      ``off`` (the tier isolates outbound where it can); WITH it, ``all``.
    * ``permissions.storage`` → ``allowed_write_paths``: the app's own data dir is the one host
      path it may write — and it is granted that dir ONLY when storage is permitted (the caller
      passes ``data_dir=None`` when it is not, mirroring the P3 storage gate). Without storage the
      backend gets no writable host path beyond the workspace boundary.

    ``expose_ports`` carries the backend's port so the tier maps it to the host (the gateway
    reaches the backend over loopback). ``env`` becomes the container/guest environment verbatim —
    it is already the allowlisted, secret-filtered child env the launcher built.
    """
    from personalclaw.sandbox import PROFILE_TOOL
    from personalclaw.sandbox_providers import SandboxSpec

    allowed_write_paths = (str(data_dir),) if data_dir is not None else ()
    return SandboxSpec(
        profile=PROFILE_TOOL,
        workspace_dir=str(workspace_dir),
        allowed_write_paths=allowed_write_paths,
        egress_tier="all" if can_network else "off",
        env=dict(env),
        expose_ports=(int(port),),
    )


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

            # Mint (or read) the per-app proxy secret and hand it to the backend via env.
            # Fail-closed: a backend that cannot obtain a verifiable secret does NOT start
            # — an unprotected backend (no inbound signature check) is worse than a missing
            # one. The value is 0600 on disk and never logged (see apps/app_secret.py).
            from personalclaw.apps.app_secret import ensure_app_secret
            from personalclaw.sdk.security import APP_SECRET_ENV

            proxy_secret = ensure_app_secret(name)
            if not proxy_secret:
                logger.warning(
                    "app %s backend: proxy secret unavailable; refusing to start unprotected",
                    name,
                )
                return None

            # The child env is an ALLOWLIST, not a copy of the gateway's (EI-12 D1).
            # App backends are the LEAST trusted children in the tree — third-party code,
            # scanned at install, running for as long as the app is enabled — and
            # `dict(os.environ)` handed them every variable the gateway had grown to carry,
            # including the `.env` credentials `config/loader.py` seeds into `os.environ` so
            # "trusted children" inherit them. The variables below (PORT, APP_NAME, the
            # proxy secret, the app's own DATA_DIR when held, plus any APE-10
            # read-only PERSONALCLAW_APP_SHARED_DIR_<SHARER> mounts) are the ones this
            # site COMPUTES; everything else arrives only if it is in
            # `CHILD_ENV_BASE_NAMES` or the operator declared it by name in
            # `sandbox.env_passthrough`. Withheld names are logged under the `app-backend`
            # site so an app author can diagnose a variable that stopped arriving.
            from personalclaw.sandbox import PROFILE_TOOL, build_child_env, spawn_shim_argv

            extra = {
                "PORT": str(port),
                "PERSONALCLAW_APP_NAME": name,
                APP_SECRET_ENV: proxy_secret,
            }
            if data_dir is not None:
                extra["PERSONALCLAW_APP_DATA_DIR"] = str(data_dir)
            # APE-10: mount each app this consumer holds a read-only storageRead grant
            # on as PERSONALCLAW_APP_SHARED_DIR_<SHARER>. Double-declared + deny-by-default
            # (apps/permissions.can_read_shared_storage); an undeclared pair yields no key.
            # Read-only is the contract — the SDK hands the consumer a read-only handle
            # (sdk.util.shared_app_data_dir) and writes stay broker-only (APE-9).
            extra.update(shared_storage_env(name))
            env = build_child_env(site="app-backend", extra=extra)
            if data_dir is None:
                # Storage not declared → don't hand the backend a data dir. The allowlist
                # cannot inherit this name (it is not in the base), but an operator CAN
                # declare it in `sandbox.env_passthrough`, which would otherwise re-open the
                # P3 storage gate for every backend at once. The gate is enforced here, at
                # the point where the name would become a variable.
                env.pop("PERSONALCLAW_APP_DATA_DIR", None)
            # Resource ceiling (PHF-1): an app backend is agent-influenced (third-party
            # code, scanned at install). Wrap its argv with the post-exec ceiling shim for
            # the ``tool`` profile. This runs synchronously off the watchdog daemon thread,
            # so it uses argv-prepend (spawn_shim_argv) rather than preexec_fn — a
            # preexec_fn here would fork the whole gateway from a non-loop thread while the
            # loop holds locks (see backend_runtime hazard audit / §1.1). The shim sets the
            # limit AFTER exec in the single-threaded child, so no fork-time lock hazard.
            # EI-4 §1.3(4): a backend that declares a sandbox tier launches THROUGH that
            # provider — the app's declared permissions mapped to the tier's confinement policy
            # (build_backend_sandbox_spec: network → egress_tier, storage → allowed_write_paths).
            # A NAMED tier that is unregistered or unavailable refuses to launch rather than
            # falling back to an unconfined host process (failure-honesty §1.1): a container/VM
            # backend that silently ran on the host would defeat the isolation it asked for. The
            # ceiling shim still wraps the resulting (client) argv exactly as the host path does.
            inner_cmd = list(cmd)
            sandbox_name = (backend.sandbox or "").strip()
            if sandbox_name:
                from personalclaw.sandbox_providers import (
                    SandboxUnavailableError,
                    get_provider,
                )

                provider = get_provider(sandbox_name)
                if provider is None:
                    logger.warning(
                        "app %s backend: sandbox %r is not registered (install/enable the tier "
                        "app); refusing to launch unconfined on the host",
                        name,
                        sandbox_name,
                    )
                    return None
                spec = build_backend_sandbox_spec(
                    workspace_dir=str(root),
                    data_dir=data_dir,
                    env=env,
                    port=port,
                    can_network=checker is not None and checker.can_use_network(),
                )
                try:
                    handle = provider.wrap(spec, inner_cmd)
                except SandboxUnavailableError as exc:
                    logger.warning(
                        "app %s backend: sandbox %r unavailable (%s); refusing host downgrade",
                        name,
                        sandbox_name,
                        exc,
                    )
                    return None
                inner_cmd = handle.argv
            launch_cmd = spawn_shim_argv(inner_cmd, PROFILE_TOOL)
            try:
                proc = subprocess.Popen(  # noqa: S603 — vetted app backend, scanned at install
                    launch_cmd,
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
