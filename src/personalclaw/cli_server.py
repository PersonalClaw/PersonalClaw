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
from personalclaw.config import loader as config_loader
from personalclaw.config.loader import _DEFAULT_PORT
from personalclaw.constants import DATA_WARNING
from personalclaw.dashboard.origin import (
    container_port_note,
    dashboard_origin,
    parse_dashboard_url,
)
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


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


def config_path() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_path`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_path()


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
    # On stderr, so stdout stays a list of URLs a script can open; a person running
    # `docker exec … personalclaw token` sees both.
    note = container_port_note(port)
    if note:
        print(note, file=sys.stderr)
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


def _refuse_dirty_tree(tracked: list[str], operation: str) -> None:
    """Print which tracked edits block *operation* and exit non-zero.

    RUM-4 advances a checkout by ``git checkout <tag>`` or a fast-forward — both
    non-destructive, so a dirty tree is refused, never discarded. There is no
    "discard my work?" prompt any more: the safe answer is always to keep the
    edits and ask the user to commit or stash. Exits 1 because the update the user
    asked for did not happen.
    """
    print(f"  ⚠️  Local tracked-file changes would block {operation}:")
    for line in tracked[:10]:
        print(f"      {line}")
    print("     Commit or `git stash` them, then re-run `personalclaw update`.")
    sys.exit(1)


def _update_git(proj: str) -> None:
    """Advance a git checkout to its release (RUM-4): ride release tags by channel.

    The ``updates`` channel decides the cadence, replacing the retired
    ``update_dev_mode`` bool: ``stable``/``beta`` (and any pin) ride release TAGS —
    ``git fetch --tags`` + ``git checkout <tag>`` — so being on the resolved tag is
    "up to date" even when ``main`` has newer commits. The git-only ``nightly``
    channel tracks the current branch, advancing by FAST-FORWARD only (clean tree
    required); it never ``reset --hard``s.
    """
    git_dir = self_update.git_root(proj)
    if not git_dir:
        # Detection said "git" because a .git was found; losing it between then and
        # now means the tree moved. Say so rather than touching something else.
        print(f"❌ No git repo at {proj}")
        sys.exit(1)
    print(f"  📂 {git_dir}")

    cfg = AppConfig.load()
    if cfg.updates.channel == "nightly":
        _update_git_nightly(git_dir)
    else:
        _update_git_release(git_dir, cfg.updates.channel, cfg.updates.pin)


def _update_git_nightly(git_dir: str) -> None:
    """Nightly/developer channel: track the current branch by fast-forward only."""
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
        _refuse_dirty_tree(tracked, "a fast-forward")

    print(f"  ⏩ git merge --ff-only origin/{branch}…")
    ff = self_update.git_fast_forward(git_dir, branch)
    if ff.returncode != 0:
        # A diverged branch cannot fast-forward — we do NOT reset over it.
        print(f"  ❌ fast-forward failed (branch diverged?):\n{(ff.stderr or '').strip()}")
        sys.exit(1)

    _finish_git_update(git_dir)


def _update_git_release(git_dir: str, channel: str, pin: str) -> None:
    """Release channels (stable/beta) and pins: ride a release TAG, not a branch."""
    import asyncio

    try:
        target = asyncio.run(self_update.resolve_target(channel, pin))
    except Exception:
        logging.getLogger(__name__).debug("resolve_target failed", exc_info=True)
        target = ""
    if not target:
        print("\n⚠️  No matching release found for this channel/pin (offline?).")
        print("   Nothing to update to — try again when a release is reachable.")
        return
    target_v = self_update.normalize_version(target)
    if pin:
        if target_v == self_update.normalize_version(__version__):
            print(f"\n✅ Already on the pinned release (v{target_v}).")
            return
    elif _is_current(target_v):
        print(f"\n✅ Already on the latest release (v{target_v}).")
        return

    tracked = self_update.git_tracked_changes(git_dir)
    if tracked:
        _refuse_dirty_tree(tracked, f"checking out {target}")

    print("  ⬇️  git fetch --tags…")
    fetched = self_update.git_fetch_tags(git_dir)
    if fetched.returncode != 0:
        print(f"  ❌ git fetch --tags failed:\n{(fetched.stderr or '').strip()}")
        sys.exit(1)
    print(f"  🏷  git checkout {target}…")
    checked = self_update.git_checkout(git_dir, target)
    if checked.returncode != 0:
        print(f"  ❌ git checkout {target} failed:\n{(checked.stderr or '').strip()}")
        sys.exit(1)

    _finish_git_update(git_dir)


def _finish_git_update(git_dir: str) -> None:
    """Rebuild the SPA and reinstall after the tree has been advanced.

    The SPA is built from source here (a checkout has no bundled dist), and both
    the build and the editable install run at the PACKAGE root — which is nested
    one level under the repo root in the monorepo layout, where git runs.
    """
    pkg_root = self_update.package_root(git_dir)
    build_frontend_sync(Path(pkg_root))
    _install(["-e", ".", "--quiet"], cwd=pkg_root, label="install -e .")

    print("\n✅ PersonalClaw updated!")
    print(f"\n{DATA_WARNING}\n")
    _refresh_agent_config(pkg_root)


def _update_pip() -> None:
    """Upgrade a wheel install (pip / pipx / uv tool) in the running environment.

    Rides the ``updates`` channel/pin (RUM-6): the wheel installed is
    ``personalclaw==<resolve_wheel_target(channel, pin)>`` — the release the
    channel/pin selects — NOT a blind ``releases/latest``. A pin installs exactly
    that version; ``stable``/``beta`` resolve their channel's newest release; the
    git-only ``nightly`` channel has no published wheel and rides ``stable``.

    No source tree is required — that requirement is exactly the dead end this
    replaced. The installer is RESOLVED (uv or pip): a uv-created venv, and a
    `uv tool install`, ship no pip module. Unlike the dashboard's apply there is no
    re-exec: this process is a short-lived CLI, not the gateway, so it prints the
    restart command instead of bouncing a running server nobody asked it to touch.
    """
    import asyncio

    cfg = AppConfig.load()
    channel = cfg.updates.channel
    pin = cfg.updates.pin
    try:
        target = asyncio.run(self_update.resolve_wheel_target(channel, pin))
    except Exception:
        logging.getLogger(__name__).debug("resolve_target failed", exc_info=True)
        target = ""

    if pin and not target:
        # A pin naming no release must NEVER silently upgrade to the latest wheel —
        # that would defeat the whole point of pinning.
        print("\n⚠️  No release matches the pinned version (offline?).")
        print(f"   Nothing to install for pin {pin!r} — check `updates.pin` or retry online.")
        return

    target_v = self_update.normalize_version(target)
    if pin:
        if target_v == self_update.normalize_version(__version__):
            print(f"\n✅ Already on the pinned release (v{target_v}).")
            return
    elif _is_current(target_v):
        print(f"\n✅ Already on the latest release (v{target_v}).")
        return

    spec = self_update.upgrade_spec(target)
    if target_v:
        print(f"  ⬆️  v{__version__} → v{target_v}")
    _install(["-U", spec, "--quiet"], cwd="", label=f"install -U {spec}")

    print("\n✅ PersonalClaw updated!")
    print(f"\n{DATA_WARNING}\n")
    _refresh_agent_config("")
    print("\n  ↻ Restart the gateway to run the new code: personalclaw restart")


def _update_container() -> None:
    """A container image cannot be updated in place — print the two commands.

    Rides the ``updates`` channel/pin (RUM-7): the printed
    ``docker compose pull``+``up -d`` carry the resolved image tag —
    ``stable`` -> the moving minor ``:X.Y``, ``beta`` -> ``:beta``, a pin -> the
    exact ``:X.Y.Z``. A pin naming no release REFUSES (mirrors ``_update_pip``'s
    pin-miss) rather than pulling ``latest`` behind the user's back.

    Exit code is 0 (see `_update`): the install is healthy and correctly
    configured, and the command did the only thing it can do here — say exactly
    how to become current.
    """
    import asyncio

    cfg = AppConfig.load()
    pin = cfg.updates.pin
    try:
        image_tag = asyncio.run(self_update.resolve_image_tag(cfg.updates.channel, pin))
    except Exception:
        logging.getLogger(__name__).debug("resolve_image_tag failed", exc_info=True)
        image_tag = ""

    if pin and not image_tag:
        # A pin naming no release must NEVER silently pull the latest image.
        print("\n⚠️  No release matches the pinned version (offline?).")
        print(f"   Nothing to pull for pin {pin!r} — check `updates.pin` or retry online.")
        return

    print("  📦 This is a container install — the image is replaced, not patched.")
    print("  Run these on the host:\n")
    for cmd in self_update.container_instructions(image_tag):
        print(f"      {cmd}")
    print("\n  See docs/guides/containers.md. Your data lives in the mounted volume")
    print("  and survives the recreate; `personalclaw snapshot` first if you want a copy.")


def _update_desktop() -> None:
    """The desktop SHELL owns this install — delegate, don't fight it.

    No in-place apply: the gateway is a child of the Electron shell, so upgrading the
    wheel under it or re-execing it is the wrong move even where it would work (a packaged
    app has no interpreter to upgrade — the backend is a frozen PyInstaller bundle).

    What the shell then does is a RE-DOWNLOAD, not an in-app update: the electron-updater
    half of `DC-1` is unbuilt (no electron-updater dependency in ``desktop/package.json``,
    nothing in the shell checks for a release), so "accept the update it offers" named an
    offer that never arrives (#2673). Restore that wording when the updater lands, not
    before — the rail in ``tests/test_desktop_install_kind.py`` reds when it does.

    Exit code 0 for the same reason as the container branch.
    """
    print("  🖥  This is a desktop install — the PersonalClaw app manages its own version.")
    print("  Install the new version from the releases page, then reopen the app:")
    print("  https://github.com/PersonalClaw/PersonalClaw/releases")


def _is_current(latest: str) -> bool:
    """True when *latest* is known and not newer than the running version.

    An UNKNOWN latest (offline, or no release ever published) is deliberately not
    "current": the update proceeds rather than claiming a state it cannot see.
    """
    return bool(latest) and self_update.version_tuple(latest) <= self_update.version_tuple(
        __version__
    )


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


def _pin_before_update(to: str) -> None:
    """`--to <version>`: pin ``updates.pin`` so this and every later apply target it.

    RUM-9's rollback entry point, and it is a PIN rather than a one-shot install for
    the reason the pin exists: a bare downgrade would be undone by the next scheduled
    check, which would resolve the channel's newest release and offer to jump straight
    back to the version the user just left. Pinning first means the resolvers already
    in place — :func:`select_target`, ``resolve_wheel_target``, ``select_image_tag`` —
    do the work on every surface, so there is no downgrade-specific install path
    anywhere.

    Exits 1 without touching config on an unusable version, and on a failed write: a
    silent fall-through would run the CHANNEL's apply instead, i.e. upgrade the user
    who asked to roll back.
    """
    target = self_update.normalize_version(to)
    if not self_update.set_version_pin(target):
        print(f"❌ Not a usable version to pin: {to!r}")
        print("   Give a release version, e.g. `personalclaw update --to 0.1.3`.")
        sys.exit(1)
    print(f"  📌 Pinned updates.pin = {target} (clear it to follow the channel again)")
    if self_update.version_tuple(target) < self_update.version_tuple(__version__):
        # A downgrade can meet state written by the newer build. Pre-1.0 there is no
        # migration machinery either way, so the honest advice is a snapshot.
        print(f"  ⏪ Rolling BACK: v{__version__} → v{target}")
        print("     Take a snapshot first if you have not: `personalclaw snapshot`")
    print()


def _update(to: str = "") -> None:
    """`personalclaw update` — advance this install, per how it was installed.

    | kind | what happens | exit |
    |---|---|---|
    | git | fetch + checkout the channel/pin release tag (nightly: | 0; 1 on failure or a |
    |  | fast-forward) + SPA build + editable install | dirty tree blocking it |
    | pip | resolved installer `-U personalclaw==<channel/pin tag>`, | 0; 1 on install failure |
    |  | then "restart the gateway" (pip / pipx / uv tool) |  |
    | container | prints `docker compose pull` + `up -d` | 0 |
    | desktop | defers to the app's own updater | 0 |
    | *unmapped* | names what it detected and refuses to guess | 1 |

    ``--to <version>`` (*to*) pins ``updates.pin`` first and then runs the same
    branch, which is how a ROLLBACK works: the pin overrides the channel in every
    resolver, so the git branch checks out that tag, the pip branch installs
    ``personalclaw==<version>``, and the container branch prints the ``:X.Y.Z`` pull.

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

    # `--to <version>` pins BEFORE the kind dispatch, so every branch below resolves the
    # pinned release through the resolver it already uses (RUM-9). Pinning is what makes
    # this a rollback and not just a one-time install of an older wheel.
    if to:
        _pin_before_update(to)

    kind = self_update.detect_install_kind()
    if kind not in _UPDATE_HANDLED_KINDS:
        # No silent fall-through to the git pipeline: advancing a tree this kind may
        # not even own is the worst possible guess.
        print(f"❌ Unrecognized install kind: {kind!r} — refusing to guess how to update it.")
        print("   Check PERSONALCLAW_INSTALL_KIND, or update the way you installed:")
        print("   pip/pipx/uv tool → upgrade the `personalclaw` package;")
        print("   container → docker compose pull && up -d; git checkout → check out the new tag.")
        sys.exit(1)

    if kind == "git":
        _update_git(self_update.project_dir())
    elif kind == "pip":
        _update_pip()
    elif kind == "container":
        _update_container()
    elif kind == "desktop":
        _update_desktop()


# The ONE place `personalclaw status` names where each counter lives in the
# `GET /api/status` payload — a (label, key-path) table rather than six inline
# `data.get(...)` calls, because inline is exactly what hid #2903: three lines read
# `crons` / `messages` / `tool_calls`, keys the endpoint has NEVER emitted, and each
# defaulted to `0`. A home with five system schedules printed `Cron jobs: 0` while the
# same payload carried `cron.total == 5` and `personalclaw cron list` listed all five.
#
# `/api/status` owns this contract, not the CLI: the dashboard reads the same payload,
# and `status_snapshot` deliberately DROPPED its writerless `messages` counter (see
# `dashboard/state.py` + `test_dashboard_status_snapshot.py`) rather than ship a
# permanent 0. So the reader moves to the emitter's shape, and the two unproduced lines
# go with it — `messages` and `tool_calls` have no writer anywhere in the payload, and
# `stats.py`'s own rule is that a counter without a call site is removed, not surfaced.
# `Turns` replaces them: the same "is anything happening?" signal, from a counter that
# is genuinely written (`stats.inc_turns` on every completed turn).
#
# `tests/test_cli_status_wire_contract.py` walks this table against a REAL `/api/status`
# response, so the next rename on either side fails a test instead of zeroing a line.
_STATUS_LINES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Uptime", ("uptime",)),
    ("Sessions", ("sessions",)),
    ("Subagents", ("subagents",)),
    ("Cron jobs", ("cron", "total")),
    ("Turns", ("stats", "total_turns")),
    ("Lessons", ("lessons",)),
)


def _status_value(data: object, path: tuple[str, ...]) -> str:
    """The payload value at *path*, or ``—`` when the payload does not carry it.

    NEVER substitutes ``0`` for a missing key. An absent field is NO MEASUREMENT, and
    printing it as ``0`` is indistinguishable from a genuinely idle gateway — which is
    why #2903 survived unnoticed through the payload change that removed the keys. A
    ``—`` puts the next such drift on the surface a user actually reads.
    """
    cur = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return "—"
        cur = cur[key]
    return "—" if cur is None or cur == "" else str(cur)


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
    for label, path in _STATUS_LINES:
        print(f"  {label + ':':<12} {_status_value(data, path)}")


def _boot_config() -> AppConfig:
    """The gateway's config boot step — the ONE place a pending migration is PERSISTED.

    ``AppConfig.load()`` is a pure read: it applies pending migrations to the object it
    returns and writes nothing, so no library reader (and no module imported during test
    collection) can rewrite the user's ``config.json``. The gateway is the process that
    legitimately owns that file, so the write-back happens here instead.

    A named function rather than four inline lines because that is what makes the write
    testable: a test points ``config_dir`` at ``tmp_path`` and drives the real startup
    path, instead of asserting on a string in ``_gateway``'s body.
    """
    if not config_path().exists():
        AppConfig().save()
        print(f"Created default config: {config_path()}")

    from personalclaw.config.migrations import load_and_persist_migrations

    return load_and_persist_migrations()


async def _gateway(
    *,
    no_dashboard: bool = False,
    no_crons: bool = False,
    no_open: bool = False,
    port_override: str | None = None,
    json_ready: bool = False,
    approval_mode: str | None = None,
    safe_surfaces: bool = False,
) -> None:
    """Load config and start the gateway (dashboard + channel transports)."""
    # Latch safe-surfaces mode BEFORE anything serves a page: the SPA's layer ceiling is
    # stamped into index.html at serve time, so setting it later would let one early load
    # resolve app/user layers the operator asked to keep out (§6).
    if safe_surfaces:
        from personalclaw.surface_layers import set_safe_surfaces

        set_safe_surfaces(True)
        logging.getLogger(__name__).warning(
            "safe-surfaces mode: only core (L0) surfaces will resolve — no app pages, "
            "no app-contributed components, no user/agent overlays"
        )
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

    cfg = _boot_config()

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
