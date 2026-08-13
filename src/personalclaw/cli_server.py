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

from personalclaw import __version__, self_update
from personalclaw.config import AppConfig
from personalclaw.config.loader import _DEFAULT_PORT, config_dir, config_path
from personalclaw.constants import DATA_WARNING
from personalclaw.dashboard.origin import dashboard_origin, parse_dashboard_url
from personalclaw.dashboard.token_auth import parse_duration
from personalclaw.frontend import build_frontend_sync, ensure_dev_dist_symlink
from personalclaw.gateway import run_gateway
from personalclaw.history import ConversationLog, HistoryConsolidator
from personalclaw.memory import MemoryStore
from personalclaw.sel import sel
from personalclaw.service import controller as service_controller
from personalclaw.service import linux as svc_linux
from personalclaw.service import macos as svc_macos
from personalclaw.service.common import SERVICE_NAME, Platform, current_platform
from personalclaw.session import SessionManager
from personalclaw.skills import SkillsLoader
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
            caller="cli",
            operation="gateway_stop",
            outcome="allowed",
            source="cli",
            resources=f"port={port} via=service",
        )
        print("✅ Stopped personalclaw service. To remove it: personalclaw service uninstall")
        return

    try:
        out = subprocess.check_output(
            ["lsof", "-ti", f"TCP:{port}", "-sTCP:LISTEN"], text=True
        ).strip()
    except FileNotFoundError:
        sel().log_api_access(
            caller="cli",
            operation="gateway_stop",
            outcome="error",
            source="cli",
            resources=f"port={port} reason=lsof_not_found",
        )
        print(
            "❌ `lsof` not found — cannot look up gateway process. "
            f"Install lsof or use `ss -tlnp | grep {port}` to find the PID manually."
        )
        sys.exit(1)
    except subprocess.CalledProcessError:
        out = ""

    if not out:
        sel().log_api_access(
            caller="cli",
            operation="gateway_stop",
            outcome="no_target",
            source="cli",
            resources=f"port={port}",
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
            caller="cli",
            operation="gateway_stop",
            outcome="error",
            source="cli",
            resources=f"port={port} reason=ps_not_found",
        )
        print(
            "❌ `ps` not found — cannot verify gateway process. "
            "Install procps or manually kill the process."
        )
        sys.exit(1)
    if not pids:
        sel().log_api_access(
            caller="cli",
            operation="gateway_stop",
            outcome="no_target",
            source="cli",
            resources=f"port={port} reason=no_personalclaw_process",
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
            caller="cli",
            operation="gateway_stop",
            outcome="allowed",
            source="cli",
            resources=f"pids={sorted(sent)} port={port}",
        )
        print(f"✅ Sent SIGTERM to gateway (pid {', '.join(str(p) for p in sorted(sent))}).")
    if denied:
        sel().log_api_access(
            caller="cli",
            operation="gateway_stop",
            outcome="denied",
            source="cli",
            resources=f"pids={denied} port={port}",
        )
        print(
            f"❌ No permission to stop pid {', '.join(str(p) for p in denied)} — try: sudo personalclaw stop"  # noqa: E501
        )
        sys.exit(1)
    if not sent:
        sel().log_api_access(
            caller="cli",
            operation="gateway_stop",
            outcome="no_target",
            source="cli",
            resources=f"port={port} reason=process_already_exited",
        )
        print(f"No PersonalClaw gateway currently running on port {port} (process already exited).")
        sys.exit(1)


def _is_personalclaw_process(pid: int) -> bool:
    """Return True if *pid* looks like a PersonalClaw gateway process."""
    try:
        out = (
            subprocess.check_output(["ps", "-p", str(pid), "-o", "args="], text=True)
            .strip()
            .lower()
        )
        return (
            "backend.gateway" in out
            or "personalclaw.dashboard" in out
            or "personalclaw gateway" in out
            or "personalclaw start" in out
        )
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
        caller="cli",
        operation="gateway_spawn",
        outcome="allowed",
        source="cli",
        resources=f"port={port}",
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
            caller="cli",
            operation="gateway_restart",
            outcome="allowed",
            source="cli",
            resources=f"port={port} via=service",
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


# Every InstallKind `_update` maps to a branch. The dispatch is exhaustive over
# `self_update.INSTALL_KINDS` and has NO default arm that falls back to the git
# pipeline: before DIST-13 this command WAS the git pipeline, so a pip/pipx/uv-tool
# user got "PERSONALCLAW_PROJECT_DIR not set" and exit 1 — a dead end with the
# per-kind machinery one module away. A kind added later must be mapped here
# consciously; `test_cli_update_kinds` reds until it is.
_UPDATE_HANDLED_KINDS: frozenset[str] = frozenset({"git", "pip", "container", "desktop"})


def _refresh_agent_config(cwd: str) -> None:
    """Re-run `setup --agent-only` so new denied commands / MCP servers take effect.

    A subprocess, not an in-process call: this interpreter has the OLD code loaded,
    and the point is to apply the version that was just installed.
    """
    print("  🔒 Refreshing agent config…")
    r = subprocess.run(
        [sys.executable, "-m", "personalclaw", "setup", "--agent-only"],
        cwd=cwd or None,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode == 0:
        print("  ✅ Agent config refreshed (hooks + MCP servers updated)")
    else:
        print("  ⚠️  Agent config refresh failed — run: personalclaw setup --agent-only")


def _confirm_discarding(tracked: list[str]) -> bool:
    """Ask before `git reset --hard` destroys tracked edits. False ⇒ do not reset.

    Non-interactive stdin (cron, a pipe, `< /dev/null`) does NOT prompt. Reading a
    piped "y" — or letting `input()` raise EOFError into a traceback — would let an
    unattended caller destroy uncommitted work that nobody agreed to lose. Refusing
    is recoverable (stash or commit, then re-run); a wrong "yes" is not. The caller
    turns this refusal into a NON-ZERO exit, because the update the user asked for
    did not happen; an interactive "n" exits 0, because declining is a choice.
    """
    print("  ⚠️  Local tracked-file changes would be discarded:")
    for line in tracked[:10]:
        print(f"      {line}")
    if not sys.stdin.isatty():
        print("  ❌ Refusing to discard them without confirmation (stdin is not a terminal).")
        print("     Commit or `git stash` them, or re-run `personalclaw update` in a terminal.")
        return False
    try:
        resp = input("  Continue? [y/N] ").strip().lower()
    except EOFError:
        resp = ""  # no answer is not a yes
    return resp == "y"


def _update_git(proj: str) -> None:
    """Advance a git checkout: fetch → (confirm) reset --hard → build → install.

    Respects ``dashboard.update_dev_mode`` exactly as the dashboard's apply does:
    OFF (default) the checkout rides release TAGS like every other install kind, so
    being on the latest tag is "up to date" even when `main` has newer commits; ON is
    the contributor "track every commit" behavior.
    """
    git_dir = self_update.git_root(proj)
    if not git_dir:
        # Detection said "git" because a .git was found; losing it between then and
        # now means the tree moved. Say so rather than resetting something else.
        print(f"❌ No git repo at {proj}")
        sys.exit(1)
    print(f"  📂 {git_dir}")

    if not AppConfig.load().dashboard.update_dev_mode:
        latest = _latest_release_version()
        if _is_current(latest):
            print(f"\n✅ Already on the latest release (v{latest}).")
            print("   Enable Developer update mode (Settings → Updates) to track every commit.")
            return

    branch = self_update.resolve_default_branch(git_dir)
    print("  ⬇️  git fetch…")
    fetched = self_update.git_fetch(git_dir, branch)
    if fetched.returncode != 0:
        print(f"  ❌ git fetch origin {branch} failed:\n{(fetched.stderr or '').strip()}")
        sys.exit(1)

    if self_update.git_is_up_to_date(git_dir, branch):
        print("\n✅ Already up to date!")
        return

    tracked = self_update.git_tracked_changes(git_dir)
    if tracked:
        interactive = sys.stdin.isatty()
        if not _confirm_discarding(tracked):
            # A human who typed "n" made a choice (0). A non-interactive caller made
            # none and we refused on its behalf, so the update it asked for did not
            # happen (1) — see _confirm_discarding.
            if interactive:
                print("  Aborted.")
            sys.exit(0 if interactive else 1)

    print(f"  🔄 git reset --hard origin/{branch}…")
    reset = self_update.git_reset_hard(git_dir, branch)
    if reset.returncode != 0:
        print(f"  ❌ git reset failed:\n{(reset.stderr or '').strip()}")
        sys.exit(1)

    # The SPA is built from source here (a checkout has no bundled dist), and both
    # the build and the editable install run at the PACKAGE root — which is nested
    # one level under the repo root in the monorepo layout, where git runs.
    pkg_root = self_update.package_root(git_dir)
    build_frontend_sync(Path(pkg_root))
    _install(["-e", ".", "--quiet"], cwd=pkg_root, label="install -e .")

    print("\n✅ PersonalClaw updated!")
    print(f"\n{DATA_WARNING}\n")
    _refresh_agent_config(pkg_root)


def _update_pip() -> None:
    """Upgrade a wheel install (pip / pipx / uv tool) in the running environment.

    No source tree is required — that requirement is exactly the dead end this
    replaced. The installer is RESOLVED (uv or pip): a uv-created venv, and a
    `uv tool install`, ship no pip module. Unlike the dashboard's apply there is no
    re-exec: this process is a short-lived CLI, not the gateway, so it prints the
    restart command instead of bouncing a running server nobody asked it to touch.
    """
    latest = _latest_release_version()
    if _is_current(latest):
        print(f"\n✅ Already on the latest release (v{latest}).")
        return
    spec = self_update.upgrade_spec(latest)
    if latest:
        print(f"  ⬆️  v{__version__} → v{latest}")
    _install(["-U", spec, "--quiet"], cwd="", label=f"install -U {spec}")

    print("\n✅ PersonalClaw updated!")
    print(f"\n{DATA_WARNING}\n")
    _refresh_agent_config("")
    print("\n  ↻ Restart the gateway to run the new code: personalclaw restart")


def _update_container() -> None:
    """A container image cannot be updated in place — print the two commands.

    Exit code is 0 (see `_update`): the install is healthy and correctly
    configured, and the command did the only thing it can do here — say exactly
    how to become current.
    """
    print("  📦 This is a container install — the image is replaced, not patched.")
    print("  Run these on the host:\n")
    for cmd in self_update.container_instructions():
        print(f"      {cmd}")
    print("\n  See docs/guides/containers.md. Your data lives in the mounted volume")
    print("  and survives the recreate; `personalclaw snapshot` first if you want a copy.")


def _update_desktop() -> None:
    """The desktop shell owns its own updater — delegate, don't fight it.

    Exit code 0 for the same reason as the container branch.
    """
    print("  🖥  This is a desktop install — the PersonalClaw app updates itself.")
    print("  Open the app and accept the update it offers (or re-download the latest")
    print("  release from https://github.com/PersonalClaw/PersonalClaw/releases).")


def _is_current(latest: str) -> bool:
    """True when *latest* is known and not newer than the running version.

    An UNKNOWN latest (offline, or no release ever published) is deliberately not
    "current": the update proceeds rather than claiming a state it cannot see.
    """
    return bool(latest) and self_update.version_tuple(latest) <= self_update.version_tuple(
        __version__
    )


def _latest_release_version() -> str:
    """The latest published release version (no leading ``v``), or "".

    Offline-tolerant by construction: `fetch_latest_release` degrades to its cache
    and never raises, and an unknown latest means "don't claim to know", not "fail".
    """
    import asyncio

    try:
        status = asyncio.run(self_update.build_update_status(__version__))
    except Exception:
        logging.getLogger(__name__).debug(
            "release probe failed; continuing without a latest version", exc_info=True
        )
        return ""
    return str(status.get("latest") or "")


def _install(args: list[str], *, cwd: str, label: str) -> None:
    """Run the resolved installer with *args*, or exit 1 with a readable reason."""
    from personalclaw._installer import NoInstallerError, install_argv, installer_name

    try:
        argv = install_argv(args)
    except NoInstallerError as exc:
        print(f"  ❌ {exc}")
        sys.exit(1)

    print(f"  🔨 {installer_name()} {label}")
    result = subprocess.run(argv, cwd=cwd or None, capture_output=True, text=True)
    if result.returncode != 0:
        # Same one-line summary the dashboard shows: uv's stderr is ANSI-colored and
        # leads with the headline, so raw stderr reads as corrupted or as a fragment.
        summary = self_update.installer_error_summary(result.stderr or "", limit=500)
        print(f"  ❌ Install failed: {summary}" if summary else "  ❌ Install failed")
        sys.exit(1)


def _update() -> None:
    """`personalclaw update` — advance this install, per how it was installed.

    | kind | what happens | exit |
    |---|---|---|
    | git | fetch + reset --hard + SPA build + editable install; | 0; 1 on failure or an |
    |  | dev_mode picks commits vs release tags | unconfirmed destructive reset |
    | pip | resolved installer `-U personalclaw==<latest>`, then | 0; 1 on install failure |
    |  | "restart the gateway" (pip / pipx / uv tool) |  |
    | container | prints `docker compose pull` + `up -d` | 0 |
    | desktop | defers to the app's own updater | 0 |
    | *unmapped* | names what it detected and refuses to guess | 1 |

    **Why container/desktop exit 0.** The status answers "did the command do its
    job?", not "did bytes change?" — the git branch already exits 0 on "Already up
    to date", so 0 has never meant "something changed" here. For these kinds the job
    IS delegation: a correctly configured container install is not a failure, and an
    unattended caller cannot act on printed instructions anyway, so a non-zero would
    only add noise where it cannot help. The counter-argument — that a script doing
    `personalclaw update && restart` learns nothing — is real, which is why the
    printed text is unambiguous about who must act; scripts that need the
    distinction should read `apply_method` from `GET /api/update/check`.
    """
    print("Updating PersonalClaw…\n")

    kind = self_update.detect_install_kind()
    if kind not in _UPDATE_HANDLED_KINDS:
        # No silent fall-through to the git pipeline: `reset --hard` on a tree that
        # this kind may not even own is the worst possible guess.
        print(f"❌ Unrecognized install kind: {kind!r} — refusing to guess how to update it.")
        print("   Check PERSONALCLAW_INSTALL_KIND, or update the way you installed:")
        print("   pip/pipx/uv tool → upgrade the `personalclaw` package;")
        print("   container → docker compose pull && up -d; git checkout → git pull.")
        sys.exit(1)

    if kind == "git":
        _update_git(self_update.project_dir())
    elif kind == "pip":
        _update_pip()
    elif kind == "container":
        _update_container()
    elif kind == "desktop":
        _update_desktop()


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

    # Unattended login enrollment (REMOTE-USER-AUTH T2.4). A no-op unless
    # PERSONALCLAW_LOGIN_USER/PASSWORD are set AND no credential exists yet, so a container
    # that keeps them in its environment cannot reset a rotated password on every restart.
    # Never fatal: it logs and continues, because the local token path is still the way in.
    try:
        from personalclaw.auth.credentials import bootstrap_from_env

        bootstrap_from_env()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "login credential bootstrap failed — continuing without it", exc_info=True
        )

    # A governance-boot abort is an OPERATOR-FIXABLE config error, not a crash: render the
    # WHAT/WHY/FIX lines and exit non-zero rather than dumping a traceback that buries them.
    # It is still a hard stop — "governance could not be established" is not a degraded mode.
    from personalclaw.guardrails.ceiling import GovernanceBootError

    try:
        await run_gateway(
            cfg,
            no_dashboard=no_dashboard,
            no_crons=no_crons,
            no_open=no_open,
            port_override=port_override,
            json_ready=json_ready,
            approval_mode=approval_mode,
        )
    except GovernanceBootError as exc:
        print(f"\n⛔ PersonalClaw did not start — governance could not be established.\n\n{exc}\n")
        raise SystemExit(1) from None


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
            caller="cli",
            operation="service_install",
            outcome="allowed" if rc == 0 else "error",
            source="cli",
            resources=f"rc={rc}",
        )
        return rc
    if action == "uninstall":
        rc = service_controller.uninstall_service()
        sel().log_api_access(
            caller="cli",
            operation="service_uninstall",
            outcome="allowed" if rc == 0 else "error",
            source="cli",
            resources=f"rc={rc}",
        )
        return rc
    if action == "status":
        rc = service_controller.service_status()
        sel().log_api_access(
            caller="cli",
            operation="service_status",
            outcome="allowed" if rc == 0 else "error",
            source="cli",
            resources=f"rc={rc}",
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
