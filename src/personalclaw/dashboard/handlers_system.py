"""System metrics and status handlers — CPU, memory, network, disk monitoring."""

import asyncio
import logging
import os
import platform
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

from aiohttp import web

import personalclaw
from personalclaw import home_gateway, record_files
from personalclaw.config import loader as config_loader
from personalclaw.dashboard.state import DashboardState
from personalclaw.stats import Stats

logger = logging.getLogger(__name__)

# Server-side network speed tracking (survives page refresh)
_prev_net: dict[str, float] = {"rx": 0.0, "tx": 0.0, "ts": 0.0}
_net_speed: dict[str, float] = {"rx_kbs": 0.0, "tx_kbs": 0.0}

# Server-side process CPU % tracking (delta of cpu_time / wall_time)
_prev_cpu: dict[str, float] = {"total": 0.0, "ts": 0.0}
_proc_cpu_pct: float = 0.0

# Cached static system info (computed once)
_STATIC_SYSTEM_INFO: dict[str, object] | None = None

# Eager fallback salt — trivial cost (32 bytes), eliminates race under run_in_executor
_IN_MEMORY_SALT: bytes = secrets.token_bytes(32)


def _get_telemetry_salt() -> bytes:
    """Return a per-install random salt, generating one on first run."""
    try:
        salt_file = config_loader.config_dir() / "telemetry_salt"
        if salt_file.exists():
            data = salt_file.read_bytes()
            if len(data) == 32:
                return data
            # corrupted/truncated — remove before regenerating
            salt_file.unlink(missing_ok=True)
        salt_file.parent.mkdir(parents=True, exist_ok=True)
        import contextlib
        import tempfile

        salt = secrets.token_bytes(32)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(salt_file.parent))
        try:
            os.write(tmp_fd, salt)
            os.close(tmp_fd)
            tmp_fd = -1
            os.chmod(tmp_path, 0o600)
            os.link(tmp_path, str(salt_file))
            return salt
        except FileExistsError:
            data = salt_file.read_bytes()
            if len(data) == 32:
                return data
            raise OSError("incomplete salt file")
        finally:
            if tmp_fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(tmp_fd)
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
    except (RuntimeError, KeyError, OSError):
        return _IN_MEMORY_SALT


def _serving_root() -> Path | None:
    """The directory the RUNNING code is imported from, or ``None`` if unknowable.

    For an editable install this is the checkout's ``src/personalclaw`` — which is exactly
    what disappears when a pinned worktree is removed from under a still-running gateway.
    """
    try:
        return Path(personalclaw.__file__).resolve().parent
    except (AttributeError, OSError, TypeError, ValueError):
        return None


async def api_healthz(request: web.Request) -> web.Response:
    """Liveness probe — auth-exempt, returns 200 once gateway is serving HTTP.

    Used by container/compose healthchecks. No secret values returned.

    It also answers WHOSE gateway this is, because "something answered on this port" is not
    the question a caller is actually asking. A gateway whose pinned worktree had been
    deleted kept answering this route 200; two probes read that as their own gateway being
    healthy and killed the same work twice. The identity fields close that:

    * ``pid`` — the answering process. A caller that spawned the gateway asserts this equals
      the child pid it holds. Not a disclosure: any local user reads it from ``ps``.
    * ``home_id`` — ``home_gateway.home_id()`` of the resolved ``PERSONALCLAW_HOME``. A caller
      that knows only its own home (the common case — ``make serve`` in one shell, a probe in
      another, every CLI command before it sends the home's secret) recomputes it and compares.
      The path itself is never sent; see ``home_gateway.home_id``.
    * ``root_ok`` — does the directory this code is being served FROM still exist on disk.
      ``false`` is the zombie: a live process serving code from a removed checkout. Computed
      live on every call, because becoming false is the whole event worth catching.

    ``status`` stays ``"ok"`` and the code stays 200 when ``root_ok`` is false, on purpose. A
    deleted serving root is a *provenance* fault, not an inability to serve, and this route is
    a container/compose healthcheck: flipping it would restart-loop a gateway that is working,
    including during the window when ``pip install --upgrade`` replaces the package directory
    under a running process. Callers that care assert on ``root_ok``.
    """
    root = _serving_root()
    try:
        # The one resolver, so the fingerprint is of the home the rest of the process actually
        # uses, and the one fingerprint, the one every command compares with before it sends this
        # home's secret: a second reimplementation here could drift from it.
        home_id = home_gateway.home_id(config_loader.config_dir())
    except (OSError, RuntimeError):  # pragma: no cover — an unresolvable home must not 500 here
        home_id = None
    return web.json_response(
        {
            "status": "ok",
            "version": personalclaw.__version__,
            "pid": os.getpid(),
            "home_id": home_id,
            "root_ok": bool(root is not None and root.exists()),
        }
    )


def _safe_surfaces_flag() -> bool:
    """The `--safe-surfaces` latch, imported lazily so the status handler keeps no
    import-time dependency on the surface-layer module."""
    from personalclaw.surface_layers import safe_surfaces

    return safe_surfaces()


async def api_status(request: web.Request) -> web.Response:
    state: DashboardState = request.app["state"]
    uptime = time.time() - state.start_time
    # The update check's schedule: an automatic check starts in the background once one is due
    # (`updates.check_interval_hours` since the last), and never while automatic checks are off.
    from personalclaw.dashboard.handlers import updates as _updates_mod

    _updates_mod.start_scheduled_check_if_due()

    data = state.status_snapshot(update_available=bool(_updates_mod._update_info.get("available")))
    # Imported lazily (handler → handler): the triggers handler owns the union that defines
    # what a "trigger" is, and importing it here rather than at module scope keeps the status
    # handler's import graph flat.
    from personalclaw.dashboard.handlers.triggers import (
        unified_trigger_count as _unified_trigger_count,
    )

    static_info = _get_static_system_info()
    if state._owner_hash is not None:
        owner_hash = state._owner_hash
    else:
        loop = asyncio.get_running_loop()
        try:
            owner_hash = await loop.run_in_executor(None, _get_owner_hash, state)
        except Exception:
            owner_hash = "unknown"
    data.update(
        {
            "uptime_secs": int(uptime),
            # The unified store's counts, not `ScheduleService.status()`. That reported
            # `{"running": false, "jobs": 0, "enabled": 0}` on a healthy machine with automations:
            # the counts came from a service the cutover emptied, and `running` was False BY DESIGN
            # because `load_without_timer` never sets it — so the field claimed the scheduler was
            # down while the clock loop was firing normally. `running` is dropped rather than
            # rewired: the honest question is whether the CLOCK is running, and that belongs to the
            # doctor's engine check, which already answers it.
            "cron": state.trigger_counts(),
            # The dashboard SystemHealth rail's "triggers" metric (#773). The `cron` block above
            # counts the schedule STORE alone; this counts every trigger the Triggers page lists —
            # schedules + store-only kinds + lifecycle hooks + data-event triggers — so the rail's
            # number agrees with the page instead of dropping the lifecycle hooks the store never
            # held. Computed here, beside `cron`, because only the triggers handler owns that union.
            "triggers": _unified_trigger_count(state),
            "stats": Stats().snapshot(),
            "stats_summary": Stats().summary(),
            "update_progress": state._update_progress,
            "version": personalclaw.__version__,
            "platform": sys.platform,
            # Whether this process was started with --safe-surfaces. Reported so the
            # CLI/doctor (and a support conversation) can see the layer ceiling without
            # reading the served HTML.
            "safe_surfaces": _safe_surfaces_flag(),
            "yolo": state.is_yolo_active(),
            "yolo_expires_in": state.yolo_remaining_secs(),
            "owner_id_hash": owner_hash,
            "os_type": static_info.get("os", ""),
            "arch": static_info.get("arch", ""),
            "cpu_count": static_info.get("cpu_count", 0),
            "mem_total_gb": static_info.get("mem_total_gb", 0),
        }
    )
    return web.json_response(data)


def _memory_totals() -> tuple[float, float] | None:
    """``(total_gb, available_gb)`` from the ONE owner of this host fact, or ``None``.

    System memory is owned by :mod:`personalclaw.local_models.residency` — the same reader
    the model-fit budget is computed from — so the static system card, the live metrics
    tick and the fit chip cannot name three different totals for one machine.

    ``None`` rather than zeros on a host whose memory could not be read: the caller then
    omits the memory keys entirely, so "unknown" never renders as "0 GB".
    """
    from personalclaw.local_models.residency import memory_pressure

    # The threshold is passed explicitly because this payload publishes raw totals and
    # never the warn flag: letting it default would read (and JSON-Schema-validate) config
    # on every metrics tick.
    snapshot = memory_pressure(warn_pct=100)
    total_mb = int(snapshot.get("total_mb") or 0)
    if total_mb <= 0 or snapshot.get("source") == "unavailable":
        return None
    return round(total_mb / 1024, 1), round(int(snapshot.get("available_mb") or 0) / 1024, 1)


def _get_static_system_info() -> dict[str, object]:
    global _STATIC_SYSTEM_INFO
    if _STATIC_SYSTEM_INFO is not None:
        return _STATIC_SYSTEM_INFO

    arch = platform.machine()
    if sys.platform == "darwin":
        try:
            real_arch = (
                subprocess.check_output(["sysctl", "-n", "hw.optional.arm64"], timeout=2)
                .decode()
                .strip()
            )
            if real_arch == "1":
                arch = "arm64 (Apple Silicon)"
        except Exception:
            pass

    info: dict[str, object] = {
        "hostname": platform.node(),
        # Gateway version travels with the system payload so the shell's system
        # card can show it — the dashboard no longer carries a separate version pill.
        "version": personalclaw.__version__,
        "os": f"{platform.system()} {platform.release()}",
        # Raw platform token (sys.platform: darwin / linux / win32) so the frontend
        # can gate OS-specific affordances (e.g. Finder reveal, screencapture) on the
        # SERVER's OS — the gateway runs the subprocess, not the browser.
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "arch": arch,
        "pid": os.getpid(),
        "cpu_count": os.cpu_count() or 0,
        "cwd": os.getcwd(),
    }

    # Total memory (static) — one reader, shared with the live tick (see _memory_totals).
    totals = _memory_totals()
    if totals is not None:
        info["mem_total_gb"] = totals[0]

    _STATIC_SYSTEM_INFO = info
    return info


def _get_owner_hash(state: DashboardState) -> str:
    """Return a cached HMAC-SHA256 hash of the owner identity. Stored on state to avoid stale globals."""  # noqa: E501
    cached = getattr(state, "_owner_hash", None)
    if cached is not None:
        return cached
    import getpass
    import hashlib
    import hmac

    try:
        raw_owner = state.owner_id or f"{platform.node()}:{getpass.getuser()}"
    except (OSError, KeyError):
        raw_owner = f"{platform.node()}:unknown"
    h = hmac.new(_get_telemetry_salt(), raw_owner.encode(), hashlib.sha256).hexdigest()
    state._owner_hash = h
    return h


#: Timeout for the live GPU telemetry read. A wedged `nvidia-smi` must degrade the widget,
#: never stall a metrics tick.
_GPU_TELEMETRY_TIMEOUT = 2


def _collect_gpu_metrics() -> dict[str, object]:
    """Best-effort GPU stats. Returns empty dict if no GPU detected.

    The **capacity** half of this payload — vendor, model, and VRAM *total* — is a host
    fact the fit verdict is taken against, so it is read from the ONE host-fact probe
    (:func:`personalclaw.local_models.fit._probe_gpu`, cached there) rather than detected a
    second time here. Two detectors is how the system widget ends up naming a VRAM total
    the fit chip disagrees with.

    The **telemetry** half stays local: utilisation, temperature and *used* VRAM change on
    every tick, so they are read live and uncached, and only on NVIDIA hardware (Apple
    Silicon utilisation needs powermetrics, which needs root).

    Output schema (all optional):
        gpu_present:     bool — true if any GPU was detected
        gpu_vendor:      "nvidia" | "apple"
        gpu_model:       human-readable name
        gpu_pct:         0..100 utilisation
        gpu_temp_c:      degrees Celsius
        vram_used_gb:    float
        vram_total_gb:   float
    """
    from personalclaw.local_models.fit import _probe_gpu

    facts = _probe_gpu()
    vendor = str(facts.get("vendor") or "")
    model = str(facts.get("model") or "")
    if not vendor:
        return {}

    data: dict[str, object] = {"gpu_present": True, "gpu_vendor": vendor}
    if model:
        data["gpu_model"] = model
    # Unified-memory hosts report 0: their "VRAM" IS system memory, already published as
    # mem_total_gb, and naming it twice would double-count it in the reader's head.
    vram_total_bytes = int(facts.get("vram_bytes") or 0)
    if vram_total_bytes > 0:
        data["vram_total_gb"] = round(vram_total_bytes / (1024**3), 1)

    if vendor != "nvidia":
        # No utilisation source without root, so presence + model is the whole answer.
        return data if model else {}

    try:
        out = (
            subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                timeout=_GPU_TELEMETRY_TIMEOUT,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        return data
    # Take the first GPU only — most installs have one, and the inline pill
    # has no room for per-card breakdowns.
    line = out.splitlines()[0] if out else ""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) >= 2:
        try:
            data["gpu_pct"] = float(parts[0])
            data["vram_used_gb"] = round(float(parts[1]) / 1024, 1)
        except ValueError:
            pass
    if len(parts) >= 3 and parts[2]:
        try:
            data["gpu_temp_c"] = int(float(parts[2]))
        except ValueError:
            pass
    return data


def _collect_system_metrics() -> dict[str, object]:
    """Collect system metrics synchronously (runs in thread pool).

    All subprocess calls and blocking I/O are isolated here so the
    asyncio event loop stays responsive.
    """
    data: dict[str, object] = dict(_get_static_system_info())

    # Process memory (RSS)
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
        data["proc_mem_mb"] = round(usage.ru_maxrss / divisor, 1)
    except Exception:
        data["proc_mem_mb"] = 0

    # System-wide memory — the SAME single reader as the static card (see _memory_totals);
    # the keys stay absent on an unmeasurable host, because 0 GB is a different claim.
    mem_totals = _memory_totals()
    if mem_totals is not None:
        mem_total, mem_free = mem_totals
        data["mem_total_gb"] = mem_total
        data["mem_free_gb"] = mem_free
        data["mem_used_gb"] = round(mem_total - mem_free, 1)

    # CPU usage
    cores = os.cpu_count() or 1
    try:
        load1, load5, load15 = os.getloadavg()
        data["load_1m"] = round(load1, 2)
        data["load_5m"] = round(load5, 2)
        data["load_15m"] = round(load15, 2)
    except Exception:
        pass
    # Left out when `ps` fails or times out (a loaded host): 0% is a different claim, the same
    # rule as memory above, and the page shows its placeholder for a missing reading.
    try:
        ps_cpu = subprocess.check_output(
            ["ps", "-A", "-o", "%cpu"], timeout=2, stderr=subprocess.DEVNULL
        ).decode()
        total_cpu = sum(float(x) for x in ps_cpu.strip().splitlines()[1:] if x.strip())
        data["cpu_pct"] = min(100.0, round(total_cpu / cores, 1))
    except Exception:
        pass

    # Local IP address
    try:
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        data["ip"] = s.getsockname()[0]
        s.close()
    except Exception:
        data["ip"] = "127.0.0.1"

    # Network bytes + speed — cross-platform
    try:
        rx_total = 0
        tx_total = 0
        if sys.platform == "darwin":
            out = subprocess.check_output(["netstat", "-ib"], timeout=2).decode()
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 10 and parts[2] != "<Link#0>":
                    try:
                        rx_total += int(parts[6])
                        tx_total += int(parts[9])
                    except (ValueError, IndexError):
                        pass
        else:
            with open("/proc/net/dev") as f:
                for line in f:
                    if ":" in line:
                        parts = line.split(":")[1].split()
                        rx_total += int(parts[0])
                        tx_total += int(parts[8])
        rx_mb = round(rx_total / (1024**2), 1)
        tx_mb = round(tx_total / (1024**2), 1)
        data["net_rx_mb"] = rx_mb
        data["net_tx_mb"] = tx_mb

        now = time.monotonic()
        if _prev_net["ts"] > 0:
            dt = now - _prev_net["ts"]
            if dt > 0.1:
                _net_speed["rx_kbs"] = round(((rx_mb - _prev_net["rx"]) * 1024) / dt, 1)
                _net_speed["tx_kbs"] = round(((tx_mb - _prev_net["tx"]) * 1024) / dt, 1)
        _prev_net["rx"] = rx_mb
        _prev_net["tx"] = tx_mb
        _prev_net["ts"] = now
        data["net_rx_kbs"] = max(0, _net_speed["rx_kbs"])
        data["net_tx_kbs"] = max(0, _net_speed["tx_kbs"])
    except Exception:
        pass

    # Disk — cross-platform (shutil.disk_usage works on all platforms)
    try:
        disk_total_v, _used, disk_free_v = shutil.disk_usage("/")
        data["disk_total_gb"] = round(disk_total_v / (1024**3), 1)
        data["disk_free_gb"] = round(disk_free_v / (1024**3), 1)
    except Exception:
        pass

    # GPU — best-effort. Try nvidia-smi on Linux/Windows. On macOS Apple
    # Silicon, gpu_pct via powermetrics requires sudo; skip and just report
    # presence so the UI can show "Apple GPU (system-managed)".
    try:
        gpu_info = _collect_gpu_metrics()
        if gpu_info:
            data.update(gpu_info)
    except Exception:
        pass

    # Process monitoring
    try:
        import threading

        data["thread_count"] = threading.active_count()
    except Exception:
        data["thread_count"] = 0
    try:
        import resource

        ru = resource.getrusage(resource.RUSAGE_SELF)
        cpu_total = ru.ru_utime + ru.ru_stime
        now_mono = time.monotonic()
        global _proc_cpu_pct
        if _prev_cpu["ts"] > 0:
            dt = now_mono - _prev_cpu["ts"]
            if dt > 0.1:
                cpu_delta = cpu_total - _prev_cpu["total"]
                _proc_cpu_pct = min(100.0, round(cpu_delta / dt * 100, 1))
        _prev_cpu["total"] = cpu_total
        _prev_cpu["ts"] = now_mono
        data["proc_cpu_pct"] = _proc_cpu_pct
    except Exception:
        data["proc_cpu_pct"] = 0
    try:
        my_pid = os.getpid()
        if sys.platform == "darwin":
            ps_out = subprocess.check_output(
                ["pgrep", "-P", str(my_pid)], timeout=2, stderr=subprocess.DEVNULL
            ).decode()
            child_pids = [p.strip() for p in ps_out.splitlines() if p.strip()]
        else:
            task_dir = Path(f"/proc/{my_pid}/task")
            child_pids = [d.name for d in task_dir.iterdir()] if task_dir.exists() else []
        data["child_processes"] = len(child_pids)
    except Exception:
        data["child_processes"] = 0

    # MCP ecosystem process count — scan for known command-line signatures.
    # A single process may match multiple signatures (e.g. a sandboxed ACP agent
    # matches both "personalclaw_sandbox" and "acp-agent"); per-category _counts can
    # overlap, while mcp_total uses _seen for unique PID dedup.
    #
    # Sandbox counting platform differences:
    #   Linux:  The namespace launcher (python3 /tmp/personalclaw_sandbox_*.py ...)
    #           forks — the parent stays alive with "personalclaw_sandbox" in its
    #           /proc/cmdline, so sandbox count is accurate.
    #   macOS:  sandbox-exec execs the target command, replacing the process
    #           image. The final cmdline becomes "claude ..." and the
    #           "personalclaw_sandbox" string (only in the -f path arg) is lost.
    #           Sandbox count will be 0 even when sandboxes are running.
    try:
        _my = os.getpid()
        _counts: dict[str, int] = {"sandbox": 0, "agent_cli": 0, "mcp_server": 0}
        _seen: set[str] = set()
        if sys.platform == "linux":
            for d in os.listdir("/proc"):
                if not d.isdigit() or int(d) == _my:
                    continue
                try:
                    cmd = Path(f"/proc/{d}/cmdline").read_bytes()
                    matched = False
                    if b"personalclaw_sandbox" in cmd:
                        _counts["sandbox"] += 1
                        matched = True
                    if b"claude" in cmd or b"acp-agent" in cmd:
                        _counts["agent_cli"] += 1
                        matched = True
                    if b"mcp-server" in cmd:
                        _counts["mcp_server"] += 1
                        matched = True
                    if matched:
                        _seen.add(d)
                except OSError:
                    pass
        else:
            _sigs = {
                "personalclaw_sandbox": "sandbox",
                "mcp-server": "mcp_server",
            }
            try:
                out = subprocess.check_output(
                    ["ps", "-eo", "pid,command"],
                    timeout=5,
                    text=True,
                )
                for line in out.splitlines():
                    parts = line.split(None, 1)
                    if len(parts) < 2:
                        continue
                    pid_s, cmd = parts
                    if pid_s.strip() == str(_my):
                        continue
                    matched = False
                    for sig, key in _sigs.items():
                        if sig in cmd:
                            _counts[key] += 1
                            matched = True
                    if matched:
                        _seen.add(pid_s.strip())
            except Exception:
                pass
        data["mcp_processes"] = _counts
        data["mcp_total"] = len(_seen)
    except Exception:
        data["mcp_processes"] = {"sandbox": 0, "agent_cli": 0, "mcp_server": 0}
        data["mcp_total"] = 0

    # Cumulative token counters (process-global Stats singleton). The topbar
    # metrics widget reads ``stats.input_tokens`` / ``stats.output_tokens`` to
    # show a running total — without this key it always rendered 0, so the
    # header token count never moved.
    try:
        data["stats"] = Stats().snapshot()
    except Exception:
        data["stats"] = {}

    return data


# Cached system metrics (avoid subprocess spawning on every poll)
_metrics_cache: dict[str, object] = {}
_metrics_cache_ts: float = 0.0
_METRICS_CACHE_TTL = 2.0  # seconds, counted from when a collection FINISHED
#: The collection in flight, which every request arriving while it runs waits on.
_metrics_inflight: asyncio.Task[dict[str, object]] | None = None


async def _refresh_metrics() -> dict[str, object]:
    """Run one collection, cache it, and stop being the collection in flight."""
    global _metrics_cache, _metrics_cache_ts, _metrics_inflight
    try:
        data = await asyncio.get_running_loop().run_in_executor(None, _collect_system_metrics)
        _metrics_cache = data
        # Stamped on arrival: stamped at the start, a collection slower than the TTL (a loaded
        # host) was stale the moment it was stored, so every request started another.
        _metrics_cache_ts = time.monotonic()
        return data
    finally:
        if _metrics_inflight is asyncio.current_task():
            _metrics_inflight = None


async def api_system(request: web.Request) -> web.Response:
    """System information endpoint with live CPU, memory, network metrics.

    Caches results for 2 seconds to avoid spawning subprocesses on every
    poll when multiple dashboard tabs are open, and runs ONE collection at a
    time: requests that arrive while it runs share its answer, so concurrent
    pollers never multiply the subprocess probes on a host that is already slow.
    """
    global _metrics_inflight
    if _metrics_cache and time.monotonic() - _metrics_cache_ts < _METRICS_CACHE_TTL:
        return web.json_response(_metrics_cache)
    task = _metrics_inflight
    # A task from a loop that has since closed (a test's, a restarted server's) is not shared.
    if task is None or task.done() or task.get_loop() is not asyncio.get_running_loop():
        task = asyncio.ensure_future(_refresh_metrics())
        _metrics_inflight = task
    # Shielded: one waiter's request being cancelled (its client went away) must not cancel
    # the collection everyone else is waiting on.
    data = await asyncio.shield(task)
    return web.json_response(data)


async def api_onboarding(request: web.Request) -> web.Response:
    """First-run onboarding signal — model readiness plus persisted flow progress.

    Reports whether a usable **chat model** is configured, so the dashboard can
    nudge the user to add one. The default agent is the in-process native
    runtime, which inferences through Settings → Models — so with no model
    provider configured, chat cannot work and we surface a setup prompt.

    Returns the readiness set ``{needs_model, has_model_provider, has_chat_binding,
    chat_model_refs, chat_is_bundled_floor, chat_download_offer, chat_provider_connection}``
    — computed live, never stored — plus the persisted first-run progress from
    ``entity_settings/onboarding.json``
    (``step``, ``essentials``, ``first_success``; see :mod:`personalclaw.onboarding`), which
    is what lets a mid-flow reload resume. The progress fields are purely additive: a client
    that only reads the readiness fields is unaffected. No secrets.

    ``chat_model_refs`` is the active chat chain (``["provider_name:model_id", …]``,
    position 0 = default) straight from ``active_models.json``. It is here because a
    resumed first run had nothing else to read: the flow persists ``essentials.model``,
    which is the **app** the user installed (``ollama-models``), and the recap rendered
    that under the words "Chat model" (#3528). The bound model is never a second stored
    copy of that fact — it is read live, from the one file that owns it, on the request
    the flow already makes. ``has_chat_binding`` is derived from this same list below, so
    the flag and the refs cannot disagree.

    ``chat_is_bundled_floor`` says WHAT is answering when that is the zero-config floor: the entry
    chat resolves to declares itself a floor, whether it is bound (onboarding binds the small
    model when the user downloads it there) or answering because nothing is. Read off the
    bridge's ``serving_entry`` — the same readiness authority as ``needs_model`` — so the two
    cannot disagree, and a recap names the small bundled model rather than calling it "a
    configured provider".

    ``chat_download_offer`` is a chat model this machine could download and has not. It is
    present whenever such a model is not on disk, whatever else is set up — it is an option,
    not a readiness claim, and onboarding's model step offers it in every state. The chat
    screen shows it only while ``needs_model`` is true. Read from fixed local catalogs only,
    so it costs no network call.

    Every readiness field reads ONE authority,
    :meth:`~personalclaw.llm.registry.ProviderRegistry.not_ready`, through the bridge. A
    provider that is configured but cannot serve (its model is not downloaded yet) is not "a
    model provider" here and does not make ``needs_model`` false — that pairing is what used to
    tell a user with no model on disk "a chat model is configured — you're ready".
    """
    has_provider = False
    has_binding = False
    try:
        from personalclaw.llm.capabilities import Capability
        from personalclaw.llm.registry import get_default_registry

        registry = get_default_registry()
        for entry in registry.list_entries():
            caps = entry.declared_capabilities
            if not caps:
                try:
                    caps = registry.capability_of(entry.type).capabilities
                except Exception:
                    caps = frozenset()
            # An agent-runtime entry (acp_agent) is not a model provider.
            if entry.type == "acp_agent":
                continue
            if Capability.CHAT in caps and registry.not_ready(entry, implicit=False) is None:
                has_provider = True
                break
    except Exception:
        logger.debug("onboarding: provider probe failed", exc_info=True)
    chat_refs: list[str] = []
    try:
        from personalclaw.providers.use_cases import active_model_refs

        chat_refs = list(active_model_refs("chat"))
        has_binding = bool(chat_refs)
    except Exception:
        logger.debug("onboarding: active-model probe failed", exc_info=True)

    # ``needs_model`` is the single source of truth: a dry-run of what the bridge
    # would actually resolve for chat. The has_provider/has_binding fields
    # remain for UI breakdown, but the nudge decision agrees with real resolution
    # rather than re-deriving it from a coarser heuristic that could diverge.
    try:
        from personalclaw.providers.provider_bridge import can_resolve_use_case

        needs_model = not can_resolve_use_case("chat")
    except Exception:
        logger.debug("onboarding: resolve probe failed; falling back", exc_info=True)
        needs_model = not (has_provider or has_binding)

    # ``chat_is_bundled_floor`` — is chat about to be answered by a zero-config FLOOR model?
    # It exists because "a model resolves" and "you have a model worth trusting" are
    # different facts, and collapsing them is how a user meets a 135M bundled model with no
    # warning and concludes the PRODUCT is bad at chat. True when the entry chat resolves to is
    # flagged ``floor`` — bound or not, because binding the small model (which onboarding does
    # when you download it there) does not make it any bigger. Binding anything else turns it
    # off. Derived, never stored, and no vendor name appears here: the flag is the entry's own
    # declaration.
    chat_is_floor = False
    try:
        from personalclaw.providers.provider_bridge import serving_entry

        serving = serving_entry("chat")
        chat_is_floor = bool(serving is not None and getattr(serving, "floor", False))
    except Exception:
        logger.debug("onboarding: floor probe failed", exc_info=True)

    # ``chat_download_offer`` — is there a chat model this machine could DOWNLOAD but has not?
    # It exists because the honest first-run answer on a fresh install is neither
    # "you have a model" nor "go configure a provider": it is "there is a one-time download and
    # here is how big it is". A surface cannot offer that without knowing the size up front, so
    # the payload carries the bytes — a download offer without a number is the shape this
    # explicitly must not be.
    #
    # Derived generically from the local-model registry: any registered provider whose app
    # declares the CHAT capability and whose catalog holds an undownloaded model. No vendor and
    # no app name appears here; the first such offer wins, and there is exactly one today.
    #
    # 🔴 NOT GATED ON ``needs_model``. It used to be computed only when nothing resolved chat, and
    # ``needs_model`` is the no-network readiness probe: a provider saved at an address nothing
    # listens on reads as set up to it. So on exactly the home that most needed the no-account
    # way out — its provider down — onboarding's "Pick a different provider" had no download to
    # offer. Downloading the small model is always a valid choice while it is not on disk; the
    # chat screen, which should not advertise it beside a model that answers, gates on
    # ``needs_model`` itself.
    #
    # 🔴 FIXED CATALOGS ONLY — this read makes no network call. A ``searchable`` provider's
    # ``list_models`` asks its server for what is already pulled (the manager-backed Ollama
    # adapter does exactly that), and by the ``LocalModelProvider`` contract it returns only
    # locally present models, so it can never hold an offer. Reading it here would put a network
    # round trip on every ``GET /api/onboarding`` for nothing.
    chat_offer: dict[str, object] | None = None
    try:
        from personalclaw.local_models.registry import capabilities_for, catalog_for, registered

        for key, provider in registered():
            if getattr(provider, "searchable", False) or "chat" not in capabilities_for(key):
                continue
            for model in await catalog_for(provider):
                if model.downloaded or "chat" not in (model.capabilities or []):
                    continue
                chat_offer = {
                    "provider": key,
                    "model": model.name,
                    # The name a person is shown (``SmolLM2-135M-Instruct``); ``model`` is
                    # the file/binding id it is downloaded and bound under.
                    "label": model.display_name or model.name,
                    "bytes": int(model.size_mb * 1024 * 1024),
                    "licence": model.license,
                    "description": model.description,
                }
                break
            if chat_offer is not None:
                break
    except Exception:
        logger.debug("onboarding: download-offer probe failed", exc_info=True)

    # ``chat_provider_connection`` — is the instance chat is bound to ANSWERING? Readiness above
    # makes no network calls by design, so a configured provider that is down read as set up.
    # This is the last MEASURED answer (``providers/connection.py``), read and never probed.
    chat_connection: dict[str, object] | None = None
    try:
        from personalclaw.providers.connection import chat_provider_status

        chat_connection = chat_provider_status()
    except Exception:
        logger.debug("onboarding: chat-provider connection read failed", exc_info=True)

    # ``install_kind`` / ``unattended_apply`` — how a new version reaches THIS install, which the
    # done screen's update pointer is about: only a source checkout installs one on its own. Read
    # here rather than from ``GET /api/update/check``, which can run a due automatic check and so
    # reach GitHub — a first-run screen must not reach the network to decide a sentence.
    from personalclaw import self_update
    from personalclaw.onboarding import load_onboarding_state

    install_kind = self_update.detect_install_kind()
    return web.json_response(
        {
            "needs_model": needs_model,
            "has_model_provider": has_provider,
            "has_chat_binding": has_binding,
            "chat_model_refs": chat_refs,
            "chat_is_bundled_floor": chat_is_floor,
            "chat_download_offer": chat_offer,
            "chat_provider_connection": chat_connection,
            "install_kind": install_kind,
            "unattended_apply": self_update.applies_updates_unattended(install_kind),
            **load_onboarding_state(),
        }
    )


async def api_onboarding_state(request: web.Request) -> web.Response:
    """Record first-run progress — a partial merge into the onboarding entity state.

    ``POST /api/onboarding/state`` with any subset of
    ``{step, essentials: {model, search, speech, channel}, first_success: {knowledge,
    trigger, loop}, name_draft: {name, handle, handle_touched} | null}``. The merge is partial
    at both levels, so each onboarding step records only what it learned and never clears
    another step's progress; ``name_draft`` is replaced whole.

    This is deliberately NOT the config PATCH allowlist: onboarding progress is entity
    state, so it is written here and stored in ``entity_settings/onboarding.json``.
    Unknown or mistyped fields are rejected with a 400 rather than dropped silently.
    Returns ``{ok: true, state: {...}}`` — read it back to confirm the merge.
    """
    from personalclaw.onboarding import merge_onboarding_state

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    try:
        state = merge_onboarding_state(body)
    except record_files.Unreadable:
        # The stored progress cannot be read, so nothing was written: the request boundary says
        # so (409), where this would call it a malformed request.
        raise
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response({"ok": True, "state": state})


async def api_auth_status(request: web.Request) -> web.Response:
    """Auth configuration status — mode, bind_host, and session validity.

    Returns a JSON object with no secret values:
    ``{mode, bind_host, valid, minutes_remaining?}``

    ``valid`` is always ``true`` for an authenticated request (unauthenticated
    requests are rejected before reaching this handler).  ``minutes_remaining``
    is how long the session this request signed in with has left, in
    ``local_token`` mode: the token middleware records its end on the request
    (``session_expires_at``). It is omitted when no session authorized the
    request — ``none`` mode, or the local-network bypass — because then no
    sign-in is ending.

    It used to read a ``token_state`` key nothing ever set, so it was omitted
    always, and the status card never said when a sign-in would end.
    """
    import time

    from personalclaw.auth.modes import AuthConfig

    auth_cfg: AuthConfig = request.app.get("auth_cfg", AuthConfig())
    body: dict[str, object] = {
        "mode": auth_cfg.mode.value,
        "bind_host": auth_cfg.bind_host,
        "valid": True,
    }
    ends = float(request.get("session_expires_at") or 0.0)
    if auth_cfg.mode.value == "local_token" and ends:
        body["minutes_remaining"] = int(max(0.0, ends - time.time()) / 60)
    return web.json_response(body)
