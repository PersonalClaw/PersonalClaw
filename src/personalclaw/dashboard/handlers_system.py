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
        salt_file = _path_home_pclaw() / "telemetry_salt"
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


async def api_healthz(request: web.Request) -> web.Response:
    """Liveness probe — auth-exempt, returns 200 once gateway is serving HTTP.

    Used by container/compose healthchecks. No secret values returned.
    """
    return web.json_response({"status": "ok", "version": personalclaw.__version__})


async def api_status(request: web.Request) -> web.Response:
    state: DashboardState = request.app["state"]
    uptime = time.time() - state.start_time
    from personalclaw.dashboard.handlers import (
        _UPDATE_CHECK_INTERVAL,
        _do_update_check,
        _update_info,
    )
    from personalclaw.dashboard.handlers import updates as _updates_mod

    # Auto-recheck every 12h in background
    if time.time() - _updates_mod._last_update_check > _UPDATE_CHECK_INTERVAL:
        asyncio.create_task(_do_update_check())

    data = state.status_snapshot(update_available=bool(_update_info.get("available")))
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
            "messages_received": state.messages_received,
            "cron": state.crons.status(),
            "stats": Stats().snapshot(),
            "stats_summary": Stats().summary(),
            "update_progress": state._update_progress,
            "version": personalclaw.__version__,
            "platform": sys.platform,
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

    # Total memory (static) — cross-platform
    if sys.platform == "darwin":
        try:
            out = (
                subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=2).decode().strip()
            )
            info["mem_total_gb"] = round(int(out) / (1024**3), 1)
        except Exception:
            pass
    elif sys.platform == "linux":
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        info["mem_total_gb"] = round(kb / (1024**2), 1)
                        break
        except Exception:
            pass

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


def _path_home_pclaw():
    """Resolve PersonalClaw home dir, honoring PERSONALCLAW_HOME."""
    try:
        from personalclaw.config.loader import config_dir as _cd

        return _cd()
    except Exception:
        from pathlib import Path as _P

        return _P.home() / ".personalclaw"


_GPU_PROBE_AT: float = 0.0
_GPU_HAS_NVIDIA_SMI: bool | None = None


def _collect_gpu_metrics() -> dict[str, object]:
    """Best-effort GPU + VRAM stats. Returns empty dict if no GPU detected.

    Probes once for `nvidia-smi` availability and caches the result, so the
    cost on machines without NVIDIA hardware is a single `shutil.which` call.

    Output schema (all optional):
        gpu_present:     bool — true if any GPU was detected
        gpu_vendor:      "nvidia" | "apple"
        gpu_model:       human-readable name
        gpu_pct:         0..100 utilisation
        gpu_temp_c:      degrees Celsius
        vram_used_gb:    float
        vram_total_gb:   float
    """
    global _GPU_HAS_NVIDIA_SMI
    if _GPU_HAS_NVIDIA_SMI is None:
        _GPU_HAS_NVIDIA_SMI = shutil.which("nvidia-smi") is not None

    if _GPU_HAS_NVIDIA_SMI:
        try:
            out = (
                subprocess.check_output(
                    [
                        "nvidia-smi",
                        "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    timeout=2,
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
        except Exception:
            return {}
        # Take the first GPU only — most installs have one, and the inline pill
        # has no room for per-card breakdowns.
        line = out.splitlines()[0] if out else ""
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            try:
                name, util_pct, vram_used_mb, vram_total_mb = parts[:4]
                temp_c = parts[4] if len(parts) >= 5 else ""
                result: dict[str, object] = {
                    "gpu_present": True,
                    "gpu_vendor": "nvidia",
                    "gpu_model": name,
                    "gpu_pct": float(util_pct),
                    "vram_used_gb": round(float(vram_used_mb) / 1024, 1),
                    "vram_total_gb": round(float(vram_total_mb) / 1024, 1),
                }
                if temp_c:
                    try:
                        result["gpu_temp_c"] = int(float(temp_c))
                    except ValueError:
                        pass
                return result
            except (ValueError, IndexError):
                return {}

    # macOS Apple Silicon — system_profiler can identify the GPU but reading
    # utilisation requires elevated privileges (powermetrics). Just report the
    # presence and model so the UI can show it.
    if sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["system_profiler", "SPDisplaysDataType", "-json"],
                timeout=3,
                stderr=subprocess.DEVNULL,
            ).decode()
            import json as _json

            blob = _json.loads(out)
            cards = blob.get("SPDisplaysDataType", [])
            if cards:
                first = cards[0]
                model = first.get("sppci_model") or first.get("_name") or ""
                if model:
                    return {
                        "gpu_present": True,
                        "gpu_vendor": "apple",
                        "gpu_model": model,
                    }
        except Exception:
            pass

    return {}


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

    # System-wide memory — cross-platform
    try:
        if sys.platform == "darwin":
            out = (
                subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=2).decode().strip()
            )
            total_bytes = int(out)
            data["mem_total_gb"] = round(total_bytes / (1024**3), 1)
            vm = subprocess.check_output(["vm_stat"], timeout=2).decode()
            page_size = 16384
            for line in vm.splitlines():
                if "page size of" in line:
                    page_size = int(line.split()[-2])
                    break
            free_pages = 0
            for line in vm.splitlines():
                if "Pages free" in line:
                    free_pages = int(line.split()[-1].rstrip("."))
                elif "Pages inactive" in line:
                    free_pages += int(line.split()[-1].rstrip("."))
            mem_free: float = round(free_pages * page_size / (1024**3), 1)
            mem_total: float = round(total_bytes / (1024**3), 1)
            data["mem_free_gb"] = mem_free
            data["mem_used_gb"] = round(mem_total - mem_free, 1)
        else:
            with open("/proc/meminfo") as f:
                meminfo: dict[str, int] = {}
                for line in f:
                    parts = line.split()
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
                mem_total = round(meminfo.get("MemTotal", 0) / (1024**2), 1)
                mem_free = round(
                    (
                        meminfo.get("MemFree", 0)
                        + meminfo.get("Buffers", 0)
                        + meminfo.get("Cached", 0)
                    )
                    / (1024**2),
                    1,
                )
                data["mem_total_gb"] = mem_total
                data["mem_free_gb"] = mem_free
                data["mem_used_gb"] = round(mem_total - mem_free, 1)
    except Exception:
        pass

    # CPU usage
    cores = os.cpu_count() or 1
    try:
        load1, load5, load15 = os.getloadavg()
        data["load_1m"] = round(load1, 2)
        data["load_5m"] = round(load5, 2)
        data["load_15m"] = round(load15, 2)
    except Exception:
        pass
    try:
        ps_cpu = subprocess.check_output(
            ["ps", "-A", "-o", "%cpu"], timeout=2, stderr=subprocess.DEVNULL
        ).decode()
        total_cpu = sum(float(x) for x in ps_cpu.strip().splitlines()[1:] if x.strip())
        data["cpu_pct"] = min(100.0, round(total_cpu / cores, 1))
    except Exception:
        data["cpu_pct"] = 0

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


# Cached system metrics (avoid subprocess spawning on every 1s poll)
_metrics_cache: dict[str, object] = {}
_metrics_cache_ts: float = 0.0
_METRICS_CACHE_TTL = 2.0  # seconds


async def api_system(request: web.Request) -> web.Response:
    """System information endpoint with live CPU, memory, network metrics.

    Caches results for 2 seconds to avoid spawning subprocesses on every
    poll when multiple dashboard tabs are open.
    """
    global _metrics_cache, _metrics_cache_ts
    now = time.monotonic()
    if now - _metrics_cache_ts < _METRICS_CACHE_TTL and _metrics_cache:
        return web.json_response(_metrics_cache)
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _collect_system_metrics)
    _metrics_cache = data
    _metrics_cache_ts = now
    return web.json_response(data)


async def api_onboarding(request: web.Request) -> web.Response:
    """First-run onboarding signal.

    Reports whether a usable **chat model** is configured, so the dashboard can
    nudge the user to add one. The default agent is the in-process native
    runtime, which inferences through Settings → Models — so with no model
    provider configured, chat cannot work and we surface a setup prompt.

    Returns ``{needs_model: bool, has_model_provider: bool, has_chat_binding: bool}``.
    No secrets.
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
            if Capability.CHAT in caps:
                has_provider = True
                break
    except Exception:
        logger.debug("onboarding: provider probe failed", exc_info=True)
    try:
        from personalclaw.providers.use_cases import active_model_refs

        has_binding = bool(active_model_refs("chat"))
    except Exception:
        logger.debug("onboarding: active-model probe failed", exc_info=True)

    # ``needs_model`` is the single source of truth: a dry-run of what the bridge
    # would actually resolve for chat (X3). The has_provider/has_binding fields
    # remain for UI breakdown, but the nudge decision agrees with real resolution
    # rather than re-deriving it from a coarser heuristic that could diverge.
    try:
        from personalclaw.providers.provider_bridge import can_resolve_use_case

        needs_model = not can_resolve_use_case("chat")
    except Exception:
        logger.debug("onboarding: resolve probe failed; falling back", exc_info=True)
        needs_model = not (has_provider or has_binding)
    return web.json_response(
        {
            "needs_model": needs_model,
            "has_model_provider": has_provider,
            "has_chat_binding": has_binding,
        }
    )


async def api_auth_status(request: web.Request) -> web.Response:
    """Auth configuration status — mode, bind_host, and session validity.

    Returns a JSON object with no secret values:
    ``{mode, bind_host, valid, minutes_remaining?, oauth2_issuer?}``

    ``valid`` is always ``true`` for an authenticated request (unauthenticated
    requests are rejected before reaching this handler).  ``minutes_remaining``
    is populated for ``local_token`` mode by reading the session expiry from the
    validated token stored in the request; for other modes it is omitted.
    """
    import time

    from personalclaw.auth.modes import AuthConfig

    auth_cfg: AuthConfig = request.app.get("auth_cfg", AuthConfig())
    body: dict[str, object] = {
        "mode": auth_cfg.mode.value,
        "bind_host": auth_cfg.bind_host,
        "valid": True,
    }
    if auth_cfg.oauth2_issuer:
        body["oauth2_issuer"] = auth_cfg.oauth2_issuer
    # Compute remaining session minutes for local_token mode
    if auth_cfg.mode.value == "local_token":
        token_state = request.get("token_state")
        session_exp = getattr(token_state, "session_exp", None) if token_state else None
        if session_exp:
            remaining_secs = max(0, session_exp - time.time())
            body["minutes_remaining"] = int(remaining_secs / 60)
    return web.json_response(body)
