"""CLI server lifecycle commands — update, stop, token, logout, status, gateway."""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from personalclaw import __version__
from personalclaw.config import AppConfig
from personalclaw.config.loader import _DEFAULT_PORT, config_dir, config_path
from personalclaw.constants import DATA_WARNING
from personalclaw.dashboard.origin import dashboard_origin, parse_dashboard_url
from personalclaw.dashboard.token_auth import parse_duration
from personalclaw.frontend import build_frontend_sync, ensure_dev_dist_symlink
from personalclaw.history import ConversationLog, HistoryConsolidator
from personalclaw.learn import LessonStore
from personalclaw.memory import MemoryStore
from personalclaw.sel import sel
from personalclaw.service import controller as service_controller
from personalclaw.service import linux as svc_linux
from personalclaw.service import macos as svc_macos
from personalclaw.service.common import SERVICE_NAME, Platform, current_platform
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader
from personalclaw.gateway import run_gateway
from personalclaw.vector_memory import VectorMemoryStore


def resolve_client_port(cli_port: int | None) -> int:
    """Return the dashboard port a *client* CLI command (token/status/logout/stop)
    should talk to.

    Resolution order:

    1. Explicit ``--port`` CLI flag if the user passed one (``cli_port`` is not ``None``).
    2. ``PERSONALCLAW_PORT`` env var if set to a valid integer.
    3. Port parsed from ``dashboard.url`` in the config file (``~/.personalclaw/config.json``)
       if present and parseable.
    4. ``_DEFAULT_PORT`` (10000) as the final fallback.

    This matches the server-side ``parse_dashboard_url()`` logic so that
    ``personalclaw token`` / ``status`` / ``logout`` / ``stop`` all hit the same
    port the gateway is actually bound to when the user has configured a
    non-default ``dashboard.url`` (for example a dev instance on 6777 or an
    alternative prod port like 7778).
    """
    if cli_port is not None:
        return cli_port
    env_port = os.environ.get("PERSONALCLAW_PORT")
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            # Fall through to config/default — main() validates this early,
            # but guard here too in case the helper is reached via another path.
            pass
    try:
        cfg = AppConfig.load()
        url = cfg.dashboard.url or ""
        if url:
            _, port = parse_dashboard_url(url)
            if port:
                return port
    except Exception:
        # Config load failures must not break client commands — fall through.
        pass
    return _DEFAULT_PORT


def _token(args: argparse.Namespace) -> None:
    """Print a dashboard URL with a fresh auth token."""
    ttl = parse_duration(args.ttl)
    if ttl is None:
        print(f"❌ Invalid TTL: {args.ttl} (use e.g. 1h, 30m)")
        sys.exit(1)

    port = resolve_client_port(args.port)
    secret_path = config_dir() / ".local_secret"
    try:
        secret = secret_path.read_text().strip()
    except FileNotFoundError:
        print("❌ Gateway not running — start it with: personalclaw gateway")
        sys.exit(1)

    url = f"http://localhost:{port}/api/token/local?ttl={args.ttl}"
    req = urllib.request.Request(url, headers={"X-Local-Secret": secret})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            token = data.get("token", "")
    except Exception as exc:
        print(f"❌ Could not reach gateway on port {port}: {exc}")
        sys.exit(1)

    if not token:
        print("❌ Gateway returned empty token")
        sys.exit(1)
    print(f"http://localhost:{port}?token={token}")
    origin = dashboard_origin(AppConfig.load().dashboard.url)
    if origin and "localhost" not in origin:
        print(f"{origin}/?token={token}")


def _logout(port: int) -> None:
    """Revoke all dashboard sessions by calling the gateway's /api/logout endpoint."""
    secret_path = config_dir() / ".local_secret"
    try:
        secret = secret_path.read_text().strip()
    except FileNotFoundError:
        print("❌ Gateway not running — start it with: personalclaw gateway")
        sys.exit(1)

    url = f"http://localhost:{port}/api/logout"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={"X-Local-Secret": secret, "Content-Type": "application/json"},
        data=b"{}",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            if data.get("ok"):
                print("✅ All dashboard sessions revoked.")
            else:
                print(f"❌ Failed to revoke sessions: {data.get('error', 'unknown error')}")
                sys.exit(1)
    except urllib.error.HTTPError as e:
        print(f"❌ Failed to revoke sessions: HTTP {e.code}")
        sys.exit(1)
    except (urllib.error.URLError, OSError):
        print("❌ Gateway not running — start it with: personalclaw gateway")
        sys.exit(1)


def _stop(port: int) -> None:
    """Stop a running PersonalClaw gateway.

    If a user-level service (systemd/launchd) is active, prefer
    ``service stop`` so the process manager does not immediately
    restart the gateway under us. Otherwise fall back to the
    SIGTERM-by-port path used for foreground gateways.
    """
    if service_controller.stop_service():
        sel().log_api_access(
            caller="cli", operation="gateway_stop", outcome="allowed",
            source="cli", resources=f"port={port} via=service",
        )
        print("✅ Stopped personalclaw service. To remove it: personalclaw service uninstall")
        return

    try:
        out = subprocess.check_output(
            ["lsof", "-ti", f"TCP:{port}", "-sTCP:LISTEN"], text=True
        ).strip()
    except FileNotFoundError:
        sel().log_api_access(
            caller="cli", operation="gateway_stop", outcome="error",
            source="cli", resources=f"port={port} reason=lsof_not_found",
        )
        print("❌ `lsof` not found — cannot look up gateway process. "
              f"Install lsof or use `ss -tlnp | grep {port}` to find the PID manually.")
        sys.exit(1)
    except subprocess.CalledProcessError:
        out = ""

    if not out:
        sel().log_api_access(
            caller="cli", operation="gateway_stop", outcome="no_target",
            source="cli", resources=f"port={port}",
        )
        print(f"No PersonalClaw gateway currently running on port {port}.")
        sys.exit(1)

    pids = list(dict.fromkeys(int(p) for p in out.splitlines() if p.strip().isdigit()))

    # Only kill processes that are actually PersonalClaw gateways.
    # Note: TOCTOU race exists between this check and os.kill — the PID could be
    # recycled. Acceptable risk for an interactive CLI tool with low blast radius.
    try:
        pids = [p for p in pids if _is_personalclaw_process(p)]
    except FileNotFoundError:
        sel().log_api_access(
            caller="cli", operation="gateway_stop", outcome="error",
            source="cli", resources=f"port={port} reason=ps_not_found",
        )
        print("❌ `ps` not found — cannot verify gateway process. "
              "Install procps or manually kill the process.")
        sys.exit(1)
    if not pids:
        sel().log_api_access(
            caller="cli", operation="gateway_stop", outcome="no_target",
            source="cli", resources=f"port={port} reason=no_personalclaw_process",
        )
        print(f"No PersonalClaw gateway currently running on port {port}.")
        sys.exit(1)

    sent: set[int] = set()
    denied: list[int] = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            sent.add(pid)
        except ProcessLookupError:
            pass
        except PermissionError:
            denied.append(pid)

    # Wait briefly for processes to exit so the port is freed
    if sent:
        for _ in range(10):  # up to 1s
            time.sleep(0.1)
            if all(_pid_exited(p) for p in sent):
                break

    if sent:
        sel().log_api_access(
            caller="cli", operation="gateway_stop", outcome="allowed",
            source="cli", resources=f"pids={sorted(sent)} port={port}",
        )
        print(f"✅ Sent SIGTERM to gateway (pid {', '.join(str(p) for p in sorted(sent))}).")
    if denied:
        sel().log_api_access(
            caller="cli", operation="gateway_stop", outcome="denied",
            source="cli", resources=f"pids={denied} port={port}",
        )
        print(f"❌ No permission to stop pid {', '.join(str(p) for p in denied)} — try: sudo personalclaw stop")
        sys.exit(1)
    if not sent:
        sel().log_api_access(
            caller="cli", operation="gateway_stop", outcome="no_target",
            source="cli", resources=f"port={port} reason=process_already_exited",
        )
        print(f"No PersonalClaw gateway currently running on port {port} (process already exited).")
        sys.exit(1)


def _is_personalclaw_process(pid: int) -> bool:
    """Return True if *pid* looks like a PersonalClaw gateway process."""
    try:
        out = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "args="], text=True
        ).strip().lower()
        return ("backend.gateway" in out or "personalclaw.dashboard" in out
                or "personalclaw gateway" in out or "personalclaw start" in out)
    except subprocess.CalledProcessError:
        return False


def _pid_exited(pid: int) -> bool:
    """Return True if *pid* no longer exists."""
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True
    except PermissionError:
        return False  # still alive, just can't signal


def _spawn_detached_gateway(port: int) -> None:
    """Start a fresh foreground gateway, detached from this CLI process.

    Used by ``personalclaw restart`` when no platform service manages the
    gateway. ``start_new_session=True`` puts the child in its own session so it
    survives the CLI exiting (the POSIX ``setsid`` equivalent); stdio is
    redirected to a log file so the detached process has no controlling TTY.
    """
    log_path = config_dir() / "gateway-restart.log"
    args = [sys.executable, "-m", "personalclaw", "gateway", "--port", str(port)]
    try:
        log_fh = open(log_path, "ab")
    except OSError:
        log_fh = subprocess.DEVNULL  # type: ignore[assignment]
    subprocess.Popen(
        args,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    sel().log_api_access(
        caller="cli", operation="gateway_spawn", outcome="allowed",
        source="cli", resources=f"port={port}",
    )
    print(f"✅ Started a fresh PersonalClaw gateway on port {port} (logs: {log_path}).")


def _restart(port: int) -> None:
    """Restart the gateway, service-aware.

    If a platform service (systemd/launchd) manages the gateway, restart it
    through the service manager and stop — it owns the process lifecycle.
    Otherwise stop any foreground gateway on ``port`` and spawn a fresh
    detached one. A ``_stop`` that exits (e.g. nothing was running) is
    swallowed so restart still starts a gateway.
    """
    if service_controller.restart_service():
        sel().log_api_access(
            caller="cli", operation="gateway_restart", outcome="allowed",
            source="cli", resources=f"port={port} via=service",
        )
        print("✅ Restarted personalclaw service.")
        return

    # No managing service — bounce the foreground gateway ourselves.
    try:
        _stop(port)
    except SystemExit:
        # _stop exits nonzero when nothing is running; that's fine for restart —
        # we still want to bring a fresh gateway up.
        pass
    _spawn_detached_gateway(port)


def _update() -> None:
    """Update PersonalClaw via git fetch + reset --hard + rebuild."""
    print("Updating PersonalClaw…\n")

    proj = os.environ.get("PERSONALCLAW_PROJECT_DIR", "")
    if not proj:
        print("❌ PERSONALCLAW_PROJECT_DIR not set — cannot locate source tree")
        print("   Run from the project directory or run `personalclaw setup` first.")
        sys.exit(1)

    proj_path = Path(proj)
    if not (proj_path / ".git").is_dir():
        print(f"❌ No git repo at {proj}")
        sys.exit(1)

    print(f"  📂 {proj}")

    # Detect current branch
    branch_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if branch_result.returncode != 0:
        print("❌ Could not determine current branch")
        sys.exit(1)
    branch = branch_result.stdout.strip() or "mainline"
    if branch == "HEAD":
        branch = "mainline"

    # Fetch + reset --hard: no merge conflicts, untracked files preserved
    print("  ⬇️  git fetch…")
    result = subprocess.run(
        ["git", "fetch", "origin", branch],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        print(f"  ❌ git fetch failed:\n{result.stderr.strip()}")
        sys.exit(1)

    # Check if there are new commits
    diff_result = subprocess.run(
        ["git", "diff", "HEAD", f"origin/{branch}", "--quiet"],
        cwd=proj,
        capture_output=True,
        timeout=10,
    )
    if diff_result.returncode == 0:
        print("\n✅ Already up to date!")
        return

    # Warn about local tracked-file changes before discarding
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=10,
    )
    tracked_changes = [
        line for line in status.stdout.strip().splitlines() if not line.startswith("??")
    ]
    if tracked_changes:
        print("  ⚠️  Local tracked-file changes will be discarded:")
        for line in tracked_changes[:10]:
            print(f"      {line}")
        resp = input("  Continue? [y/N] ").strip().lower()
        if resp != "y":
            print("  Aborted.")
            sys.exit(0)

    print(f"  🔄 git reset --hard origin/{branch}…")
    result = subprocess.run(
        ["git", "reset", "--hard", f"origin/{branch}"],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        print(f"  ❌ git reset failed:\n{result.stderr.strip()}")
        sys.exit(1)

    # Build frontend frontend assets (assumes Node.js is already on PATH)
    build_frontend_sync(proj_path)

    print("  🔨 pip install -e .")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"],
        cwd=proj,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  ❌ Install failed:\n{result.stderr.strip()}")
        sys.exit(1)

    print("\n✅ PersonalClaw updated!")
    print(f"\n{DATA_WARNING}\n")

    # Re-install agent config so new denied commands take effect.
    # Run as subprocess since the current process has old code loaded.
    print("  🔒 Refreshing agent config…")
    r = subprocess.run(
        [sys.executable, "-m", "personalclaw", "setup", "--agent-only"],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode == 0:
        print("  ✅ Agent config refreshed (hooks + MCP servers updated)")
    else:
        print("  ⚠️  Agent config refresh failed — run: personalclaw setup --agent-only")



def _status(args: argparse.Namespace) -> None:
    """Query the running gateway for stats, or print offline message."""
    port = resolve_client_port(getattr(args, "port", None))
    url = f"http://127.0.0.1:{port}/api/status"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            print("PersonalClaw gateway is running (token auth enabled).")
            print("  For detailed stats, see the Overview page in the dashboard.")
        else:
            print(f"PersonalClaw gateway is running but returned HTTP {e.code}.")
        return
    except (urllib.error.URLError, OSError):
        print("PersonalClaw gateway is not running.")
        print("  Start it with: personalclaw gateway")
        return
    except Exception:
        print("PersonalClaw gateway is running but returned an unexpected response.")
        return

    print(f"PersonalClaw v{__version__}\n")
    print(f"  Uptime:      {data.get('uptime', '—')}")
    print(f"  Sessions:    {data.get('sessions', 0)}")
    print(f"  Messages:    {data.get('messages', 0)}")
    print(f"  Tool calls:  {data.get('tool_calls', 0)}")
    print(f"  Subagents:   {data.get('subagents', 0)}")
    print(f"  Cron jobs:   {data.get('crons', 0)}")
    print(f"  Lessons:     {data.get('lessons', 0)}")



async def _gateway(
    *,
    no_dashboard: bool = False,
    no_crons: bool = False,
    no_open: bool = False,
    port_override: str | None = None,
    json_ready: bool = False,
    approval_mode: str | None = None,
) -> None:
    """Load config and start the gateway (dashboard + channel transports)."""
    # Resolve the web React build for the dashboard. Skipped in headless
    # mode since no dashboard will be served. The Docker image ships a
    # pre-bundled dist/ (no-op inside). Source-tree checkouts get a symlink
    # to the repo-root web/dist if present.
    if not no_dashboard and ensure_dev_dist_symlink() is None:
        _hint = Path(__file__).resolve().parent.parent.parent / "web"
        logging.getLogger(__name__).debug(
            "web dist/ not found — SPA served by separate container or "
            "build with `cd %s && npm ci && npm run build`.",
            _hint,
        )

    if not config_path().exists():
        cfg = AppConfig()
        cfg.save()
        print(f"Created default config: {config_path()}")

    cfg = AppConfig.load()
    await run_gateway(
        cfg,
        no_dashboard=no_dashboard,
        no_crons=no_crons,
        no_open=no_open,
        port_override=port_override,
        json_ready=json_ready,
        approval_mode=approval_mode,
    )


def _build_consolidator() -> tuple["SessionManager", HistoryConsolidator, ConversationLog]:
    """Assemble a standalone HistoryConsolidator for one-shot CLI extraction.

    Mirrors the gateway wiring: a real memory + vector store (so structured
    memories land) and a SkillsLoader (so auto skills get written), driven off
    the active embedding selection.
    """
    cfg = AppConfig.load()
    factory = cfg.create_provider_factory()
    sessions = SessionManager(cfg, provider_factory=factory)  # type: ignore[arg-type]

    memory = MemoryStore()
    memory.init()

    from personalclaw.embedding_providers.registry import (
        get_active_embed_fn,
        get_active_embedding_dim,
    )
    vector_memory = VectorMemoryStore(
        confidence_threshold=cfg.memory.semantic_confidence_threshold,
        extra_prefixes=cfg.memory.semantic_keys or None,
        dedup_threshold=cfg.memory.episodic_dedup_threshold,
        episodic_max=cfg.memory.episodic_max_count,
        episodic_limit=cfg.memory.episodic_max_results,
        embedding_dim=get_active_embedding_dim() or 384,
    )
    vector_memory.init()
    embed_fn = get_active_embed_fn()
    if embed_fn:
        vector_memory.embed_fn = embed_fn
    memory.vector_store = vector_memory

    conv_log = ConversationLog()
    conv_log.init()
    consolidator = HistoryConsolidator(
        log=conv_log,
        memory=memory,
        sessions=sessions,
        lesson_store=LessonStore(),
        history_idle_secs=cfg.memory.history_idle_hours * 3600,
        vector_store=vector_memory,
        migrated=cfg.memory.migrated,
        skills_loader=SkillsLoader(),
        auto_skills_enabled=cfg.skills.auto_create_from_sessions,
        auto_refine_enabled=cfg.skills.auto_refine_on_deviation,
        auto_min_tool_calls=cfg.skills.auto_min_tool_calls,
        auto_similarity_threshold=cfg.skills.auto_similarity_threshold,
    )
    return sessions, consolidator, conv_log


async def _consolidate_cmd(args: argparse.Namespace) -> None:
    """Run skill/memory extraction over one session (or every session) on demand.

    The same engine the 3-hour idle poll and session-end triggers use; always
    extracts from the full transcript (``include_history=True``).
    """
    sessions, consolidator, conv_log = _build_consolidator()

    if getattr(args, "all", False):
        keys = [s["key"] for s in conv_log.list_sessions()]
        if not keys:
            print("No sessions to consolidate.")
            return
        print(f"Consolidating {len(keys)} session(s)…")
        ran = 0
        for key in keys:
            if await consolidator.consolidate_session(key):
                ran += 1
                print(f"  ✓ {key}")
            else:
                print(f"  • {key} (already in flight, skipped)")
        print(f"\n✅ Consolidated {ran}/{len(keys)} session(s).")
        return

    key = args.key
    if not conv_log.has_log(key):
        print(f"❌ No conversation history for session '{key}'.", file=sys.stderr)
        sys.exit(1)
    print(f"Consolidating session '{key}'…")
    if await consolidator.consolidate_session(key):
        print("✅ Done.")
    else:
        print("⚠️  Already in flight — nothing to do.")


def _service_cmd(args: argparse.Namespace) -> int:
    """Dispatch ``personalclaw service {install,uninstall,status}``.

    Wraps :mod:`personalclaw.service.controller` so that platform detection
    and the underlying systemctl/launchctl calls live there. The CLI
    layer only handles argument parsing, audit logging, and exit codes.
    """
    action = getattr(args, "service_action", None)
    if action == "install":
        rc = service_controller.install_service()
        sel().log_api_access(
            caller="cli", operation="service_install",
            outcome="allowed" if rc == 0 else "error",
            source="cli", resources=f"rc={rc}",
        )
        return rc
    if action == "uninstall":
        rc = service_controller.uninstall_service()
        sel().log_api_access(
            caller="cli", operation="service_uninstall",
            outcome="allowed" if rc == 0 else "error",
            source="cli", resources=f"rc={rc}",
        )
        return rc
    if action == "status":
        rc = service_controller.service_status()
        sel().log_api_access(
            caller="cli", operation="service_status",
            outcome="allowed" if rc == 0 else "error",
            source="cli", resources=f"rc={rc}",
        )
        return rc
    print("Usage: personalclaw service {install|uninstall|status}", file=sys.stderr)
    return 2


def _logs_cmd(args: argparse.Namespace) -> None:
    """Tail gateway logs from the most appropriate source.

    Order of preference:
      1. systemd journal (if the system service is installed on Linux)
      2. launchd stdout file (macOS)
      3. ``~/.personalclaw/gateway.log`` (foreground gateway)
    """
    follow = bool(getattr(args, "follow", False))
    lines = int(getattr(args, "lines", 100) or 100)
    plat = current_platform()
    unit = f"{SERVICE_NAME}.service"

    # Audit before any os.execvp branch — the exec replaces this process
    # so a post-exec audit call would never run.
    sel().log_api_access(
        caller="cli",
        operation="logs",
        outcome="allowed",
        source="cli",
        resources=f"follow={follow} lines={lines} platform={plat.value}",
    )

    if plat == Platform.SYSTEMD and svc_linux.UNIT_PATH.exists():
        # Try journalctl unprivileged first — it works if the user is in
        # the `systemd-journal` or `adm` group. Only fall back to sudo
        # journalctl if the unprivileged probe returns no rows. Without
        # this fall-through, `personalclaw logs` would hang on hosts without
        # passwordless sudo, which is a surprising failure mode for a
        # read-only log-viewer.
        base = ["journalctl", "--no-pager", "-u", unit, "-n", str(lines)]
        probe = subprocess.run(
            ["journalctl", "-u", unit, "-n", "1", "--no-pager"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0 and probe.stdout.strip():
            if follow:
                base.append("-f")
            os.execvp("journalctl", base)
        # Refuse to invoke sudo without a TTY: in non-interactive
        # contexts (cron, piped scripts, systemd ExecStartPre) the sudo
        # password prompt would block forever with no way to cancel.
        if not sys.stdin.isatty():
            print(
                "Insufficient permissions to read the journal without sudo, "
                "and stdin is not a TTY so sudo can't prompt.\n"
                "   Add your user to the `systemd-journal` or `adm` group, or run:\n"
                f"   sudo journalctl -u {unit} -f",
                file=sys.stderr,
            )
            sys.exit(1)
        # Fall back to sudo journalctl. `--no-pager` prevents the pager
        # (`less`) from taking over after exec, which behaves badly in
        # piped/non-interactive contexts.
        sudo_cmd = ["sudo", *base]
        if follow:
            sudo_cmd.append("-f")
        os.execvp("sudo", sudo_cmd)

    if plat == Platform.LAUNCHD and svc_macos.STDOUT_LOG.exists():
        cmd = ["tail", "-n", str(lines)]
        if follow:
            cmd.append("-f")
        cmd.append(str(svc_macos.STDOUT_LOG))
        os.execvp("tail", cmd)

    fallback = config_dir() / "gateway.log"
    if not fallback.exists():
        print(
            "No gateway logs found. Either install the service "
            "(`personalclaw service install`) or start the gateway "
            "(`personalclaw gateway`).",
            file=sys.stderr,
        )
        sys.exit(1)
    cmd = ["tail", "-n", str(lines)]
    if follow:
        cmd.append("-f")
    cmd.append(str(fallback))
    os.execvp("tail", cmd)
