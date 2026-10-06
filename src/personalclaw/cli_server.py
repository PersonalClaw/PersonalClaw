"""CLI server lifecycle commands — update, stop, token, logout, status, gateway."""

import argparse
import json
import logging
import os
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from personalclaw import (
    __version__,
    checkout_update,
    container_host,
    gateway_base,
    home_gateway,
    process_facts,
    self_update,
    versions,
)
from personalclaw.atomic_write import open_streamed
from personalclaw.auth import lifetimes
from personalclaw.config import AppConfig
from personalclaw.config import loader as config_loader
from personalclaw.constants import DATA_WARNING
from personalclaw.dashboard.origin import (
    container_port_note,
    dashboard_origin,
    parse_dashboard_url,
)
from personalclaw.dashboard.token_auth import (
    MAX_SESSION_TTL_SECS,
    duration_words,
    parse_duration,
)
from personalclaw.env import NO_OPEN
from personalclaw.frontend import ensure_dev_dist_symlink
from personalclaw.gateway import run_gateway
from personalclaw.history import ConversationLog, HistoryConsolidator
from personalclaw.memory import MemoryStore
from personalclaw.sel import sel
from personalclaw.service import controller as service_controller
from personalclaw.service import linux as svc_linux
from personalclaw.service import macos as svc_macos
from personalclaw.service.common import (
    SERVICE_NAME,
    Platform,
    current_platform,
    private_output_log,
)
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


def _bind_port() -> int:
    """The port a gateway of this home binds when it is not told one: the gateway's own decision
    (``PERSONALCLAW_PORT``, else the port in ``dashboard.url``, else the default). For ``restart``
    to start one there and say where; never where a command connects (``home_gateway.reach``)."""
    try:
        url = AppConfig.load().dashboard.url or ""
    except Exception:  # noqa: BLE001 - an unreadable config names no port; the gateway's default
        url = ""
    return parse_dashboard_url(url)[1]


def _token(args: argparse.Namespace) -> None:
    """Print a dashboard URL with a fresh auth token, from this home's gateway
    (``home_gateway.reach``): the one it recorded, or the one ``--port`` or ``PERSONALCLAW_PORT``
    names, once it has shown it serves this home. The home's local secret goes to no other.

    A ``--ttl`` that is not a lifetime, or one over the 90-day limit, is refused HERE, with the
    sentence saying why — before the gateway is asked for anything.
    """
    ttl = parse_duration(args.ttl)
    if ttl is None or ttl > MAX_SESSION_TTL_SECS:
        refusal = lifetimes.unreadable(args.ttl) if ttl is None else lifetimes.too_long(ttl)
        print(f"❌ {refusal}", file=sys.stderr)
        sys.exit(1)

    try:
        gateway = home_gateway.reach(args.port)
        data = gateway.sign_in(args.ttl)
    except home_gateway.GatewayError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)
    port = gateway.port
    token = str(data.get("token") or "")

    if not token:
        print("❌ Gateway returned empty token", file=sys.stderr)
        sys.exit(1)
    print(f"http://localhost:{port}?token={token}")
    # On stderr, so stdout stays a list of URLs a script can open; a person running
    # `docker exec … personalclaw token` sees both.
    lifetime = _token_lifetime_note(data)
    if lifetime:
        print(lifetime, file=sys.stderr)
    note = container_port_note(port)
    if note:
        print(note, file=sys.stderr)
    origin = dashboard_origin(AppConfig.load().dashboard.url)
    if origin and "localhost" not in origin:
        print(f"{origin}/?token={token}")


def _token_lifetime_note(reply: dict) -> str:
    """What the minted token is and how long it lasts, from the gateway's own numbers.

    Empty when the gateway did not say (an older one still running beside a newer CLI):
    stating a lifetime this side cannot know would be a guess.
    """
    try:
        lasts = int(reply["expires_in"])
        open_within = int(reply.get("open_within") or lasts)
        until = time.strftime("%H:%M on %d %B", time.localtime(float(reply["expires_at"])))
    except (KeyError, TypeError, ValueError):
        return ""
    if open_within >= lasts:
        how = (
            f"It works for {duration_words(lasts)}, until {until}: open it in a browser to sign "
            "that browser in, or send the token after ?token= as an "
            '"Authorization: Bearer" header from a script.'
        )
    else:
        how = (
            f"Open it in a browser within {duration_words(open_within)} to sign that browser in "
            f"until {until}. From a script, send the token after ?token= as an "
            f'"Authorization: Bearer" header; it works for {duration_words(lasts)}.'
        )
    return (
        f"This is a sign-in link for your PersonalClaw dashboard. {how}\n"
        "Treat it like a password: anyone who has it can use your dashboard. Settings → Devices "
        "lists every sign-in, and signs any of them out."
    )


def _logout(port: int | None) -> None:
    """Revoke all dashboard sessions through this home's gateway's ``/api/logout``.

    *port* is the ``--port`` typed, or None. The gateway is the one ``home_gateway.reach`` finds,
    so the home's local secret goes to no other.
    """
    try:
        gateway = home_gateway.reach(port)
        status, data = gateway.post("/api/logout", {}, secret_header="X-Local-Secret")
    except home_gateway.GatewayError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)
    if status != 200:
        print(f"❌ Failed to revoke sessions: HTTP {status}", file=sys.stderr)
        sys.exit(1)
    if not data.get("ok"):
        print(
            f"❌ Failed to revoke sessions: {data.get('error', 'unknown error')}",
            file=sys.stderr,
        )
        sys.exit(1)
    print("✅ All dashboard sessions revoked.")


#: How long `stop` waits for the gateway to exit once asked. The gateway bounds its own graceful
#: shutdown at 10 s (`GatewayOrchestrator._finish`) and then exits, so this is that bound with a
#: margin for its last cleanup. Returning only once it is gone is what lets `restart` start the
#: next gateway, and `restore` run, without the old one still writing the home.
_STOP_WAIT_SECS = 15.0
_STOP_POLL_SECS = 0.1


def _runs_the_gateway(command: str) -> bool:
    """Whether *command*, a process's command line, is PersonalClaw's gateway: the ``gateway``
    subcommand of the ``personalclaw`` program, however it was started (its console script,
    ``python -m personalclaw``, or the desktop app's bundled backend)."""
    words = command.split()
    for at, word in enumerate(words):
        if os.path.basename(word) in ("personalclaw", "personalclaw-backend"):
            return "gateway" in words[at + 1 :]
    return False


def _this_homes_gateway(port: int | None) -> gateway_base.LiveGateway:
    """The gateway ``stop`` may signal, or exit 1 saying why there is none.

    It is the one this home's runtime record names, and three things must hold before its pid
    is signalled: the record is there and its pid alive, a ``--port`` the user typed is the port
    it listens on, and the pid still runs a PersonalClaw gateway. The last one is the guard a
    pid needs: a gateway that crashed leaves its record behind, and the system may since have
    given the pid to another program. Each refusal fails closed, since a signal cannot be
    taken back.
    """
    gateway = gateway_base.live_gateway()
    if gateway is None:
        sel().log_api_access(
            caller="cli",
            operation="gateway_stop",
            outcome="no_target",
            source="cli",
            resources=f"port={port}",
        )
        print(f"No PersonalClaw gateway is running for {config_dir()}.", file=sys.stderr)
        sys.exit(1)
    if port is not None and port != gateway.port:
        sel().log_api_access(
            caller="cli",
            operation="gateway_stop",
            outcome="no_target",
            source="cli",
            resources=f"port={port} reason=port_mismatch gateway_port={gateway.port}",
        )
        print(
            f"This home's gateway listens on port {gateway.port}, not {port}, so nothing was "
            "stopped. Without --port, `personalclaw stop` stops this home's gateway; another "
            "home's is stopped with that home's PERSONALCLAW_HOME.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not _runs_the_gateway(process_facts.command_line(gateway.pid)):
        sel().log_api_access(
            caller="cli",
            operation="gateway_stop",
            outcome="no_target",
            source="cli",
            resources=f"pid={gateway.pid} port={gateway.port} reason=not_a_gateway",
        )
        print(
            f"No PersonalClaw gateway is running for {config_dir()}: pid {gateway.pid}, which "
            "its record names, is not a PersonalClaw gateway now, so it was not signalled.",
            file=sys.stderr,
        )
        sys.exit(1)
    return gateway


def _stop(port: int | None) -> None:
    """Stop this home's gateway, and return once it has exited.

    Service first: if systemd or launchd runs this home's gateway, stopping the PROCESS would only
    have the service manager start it again, so the service is stopped instead, and the message
    says what starts it again. In a container the container runtime is that manager, and nothing
    inside the container can drive it, so this says which host command does it and changes
    nothing.

    Otherwise the gateway is the one this home's runtime record names (``gateway_base``): the
    port it bound and its pid, written once it listens and removed as it stops. It used to be
    found by asking ``lsof`` which process listens on the port and ``ps`` whether that process
    is a gateway, and neither program is installed everywhere; the published image has neither,
    so ``stop`` could only fail there. The record also keeps the stop to THIS home, so a
    gateway of another home is not this command's to stop, even one on the port asked about.

    *port* is the ``--port`` the user typed, or None.
    """
    service = service_controller.this_homes_service()
    if service is not None and service_controller.is_service_active():
        failed = service_controller.stop_service(service)
        sel().log_api_access(
            caller="cli",
            operation="gateway_stop",
            outcome="error" if failed else "allowed",
            source="cli",
            resources="via=service",
        )
        if failed:
            print(f"❌ Could not stop {service.name}: {failed}", file=sys.stderr)
            sys.exit(1)
        print(
            f"✅ Stopped the PersonalClaw service ({service.name}). {service.comes_back}, or "
            "now with `personalclaw restart`. To remove it: personalclaw service uninstall"
        )
        return
    if current_platform() is Platform.CONTAINER:
        sel().log_api_access(
            caller="cli",
            operation="gateway_stop",
            outcome="refused",
            source="cli",
            resources="via=container_runtime",
        )
        print(
            container_host.not_done_here("stopped", container_host.stop_command()),
            file=sys.stderr,
        )
        sys.exit(1)

    gateway = _this_homes_gateway(port)
    try:
        os.kill(gateway.pid, signal.SIGTERM)
    except ProcessLookupError:
        sel().log_api_access(
            caller="cli",
            operation="gateway_stop",
            outcome="no_target",
            source="cli",
            resources=f"pid={gateway.pid} reason=process_already_exited",
        )
        print(
            f"No PersonalClaw gateway is running for {config_dir()} (it exited just now).",
            file=sys.stderr,
        )
        sys.exit(1)
    except PermissionError:
        sel().log_api_access(
            caller="cli",
            operation="gateway_stop",
            outcome="denied",
            source="cli",
            resources=f"pid={gateway.pid} port={gateway.port}",
        )
        print(
            f"❌ No permission to stop the gateway (pid {gateway.pid}): it runs as another "
            "user. Stop it as that user.",
            file=sys.stderr,
        )
        sys.exit(1)

    for _ in range(int(_STOP_WAIT_SECS / _STOP_POLL_SECS)):
        if not gateway_base.pid_is_alive(gateway.pid):
            break
        time.sleep(_STOP_POLL_SECS)
    else:
        sel().log_api_access(
            caller="cli",
            operation="gateway_stop",
            outcome="allowed",
            source="cli",
            resources=f"pid={gateway.pid} port={gateway.port} still_running",
        )
        print(
            f"Asked the gateway to stop (pid {gateway.pid}), and it is still running after "
            f"{_STOP_WAIT_SECS:g} seconds. Nothing else was done.",
            file=sys.stderr,
        )
        sys.exit(1)
    sel().log_api_access(
        caller="cli",
        operation="gateway_stop",
        outcome="allowed",
        source="cli",
        resources=f"pid={gateway.pid} port={gateway.port}",
    )
    print(f"✅ Stopped the gateway (pid {gateway.pid}, port {gateway.port}).")


def _spawn_detached_gateway(port: int) -> None:
    """Start a fresh foreground gateway, detached from this CLI process.

    Used by ``personalclaw restart`` when no platform service is installed for this home.
    ``start_new_session=True`` puts the child in its own session, with no controlling terminal,
    so it survives the CLI exiting (the POSIX ``setsid`` equivalent). Its stdio goes to a log
    file, so it prints no sign-in link, and it starts with ``--no-open``, so it opens no browser:
    nobody started it at a terminal. The log is the owner's alone, and a link an older gateway
    printed there is taken out first (``private_output_log``).
    """
    log_path = config_dir() / "gateway-restart.log"
    args = [*self_update.cli_argv(), "gateway", "--port", str(port), NO_OPEN]
    try:
        private_output_log(log_path)
        log_fh = open_streamed(log_path, "ab")
    except OSError:
        log_fh = subprocess.DEVNULL
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


def _restart(port: int | None) -> None:
    """Restart the gateway, service-aware.

    If a platform service (systemd/launchd) is installed for this home, the service manager owns
    the process lifecycle, so the restart goes through it, whether or not it runs the service
    now: a service ``stop`` stopped is started again, rather than replaced by a gateway with no
    restart on a crash and no start at login. A gateway started outside the service is stopped
    first, since it holds the home and the port the service's gateway needs. In a container the
    container runtime owns the lifecycle, so this says which host command restarts it and changes
    nothing. Otherwise stop this home's gateway, if one runs, and start a fresh detached one on
    the port it had (with none running, on the ``--port`` typed or the configured one).

    A home has one gateway (``gateway_base.claim_home``): the fresh one can claim the home only
    once the old one has exited, so it starts only once this home has none. A stop that could not
    stop the running gateway ends the restart there, with its reason: the restart used to start
    one anyway, which put a second gateway beside the first on the same home.
    """
    service = service_controller.this_homes_service()
    if service is not None:
        was_running = service_controller.is_service_active()
        if not was_running and gateway_base.live_gateway() is not None:
            _stop(port)  # returns only once it has exited; exits 1 when it cannot stop it
        failed = service_controller.restart_service(service)
        sel().log_api_access(
            caller="cli",
            operation="gateway_restart",
            outcome="error" if failed else "allowed",
            source="cli",
            resources="via=service",
        )
        if failed:
            print(f"❌ Could not start {service.name}: {failed}", file=sys.stderr)
            sys.exit(1)
        print(
            f"✅ {'Restarted' if was_running else 'Started'} the PersonalClaw service "
            f"({service.name})."
        )
        return
    if current_platform() is Platform.CONTAINER:
        sel().log_api_access(
            caller="cli",
            operation="gateway_restart",
            outcome="refused",
            source="cli",
            resources="via=container_runtime",
        )
        print(
            container_host.not_done_here("restarted", container_host.restart_command()),
            file=sys.stderr,
        )
        sys.exit(1)

    # No managing service — bounce the foreground gateway ourselves.
    running = gateway_base.live_gateway()
    if running is not None:
        _stop(port)  # returns only once it has exited; exits 1 when it cannot stop it
        _spawn_detached_gateway(running.port)
    else:
        _spawn_detached_gateway(port if port is not None else _bind_port())


# Every InstallKind `_update` maps to a branch. The dispatch is exhaustive over
# `self_update.INSTALL_KINDS` and has NO default arm that falls back to the git
# pipeline: this command used to BE the git pipeline, so a pip/pipx/uv-tool
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
        [*self_update.cli_argv(), "setup", "--agent-only"],
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

    The updater advances a checkout by ``git checkout <tag>`` or a fast-forward — both
    non-destructive, so a dirty tree is refused, never discarded. There is no
    "discard my work?" prompt any more: the safe answer is always to keep the
    edits and ask the user to commit or stash. Exits 1 because the update the user
    asked for did not happen.
    """
    print(f"  ⚠️  Local tracked-file changes would block {operation}:", file=sys.stderr)
    for line in tracked[:10]:
        print(f"      {line}", file=sys.stderr)
    print("     Commit or `git stash` them, then re-run `personalclaw update`.", file=sys.stderr)
    sys.exit(1)


def _update_git(proj: str) -> None:
    """Advance a git checkout to its release: ride release tags by channel.

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
        print(f"❌ No git repo at {proj}", file=sys.stderr)
        sys.exit(1)
    print(f"  📂 {git_dir}")

    cfg = AppConfig.load()
    if cfg.updates.channel == "nightly":
        _update_git_nightly(git_dir)
    else:
        _update_git_release(git_dir, cfg.updates.channel, cfg.updates.pin)


def _git_said(result: subprocess.CompletedProcess[str]) -> str:
    """What a failed git step printed, for the owner's terminal: masked like every view, with
    its line breaks kept and every control character escaped, so a remote's message cannot
    move the cursor or hide text. The updater has already put a refused transport in
    PersonalClaw's own words (``self_update._run_git``)."""
    from personalclaw.security import mask_child_output

    return mask_child_output(result.stderr, limit=None, one_line=False)


def _update_git_nightly(git_dir: str) -> None:
    """Nightly/developer channel: track the current branch by fast-forward only."""
    branch = self_update.resolve_default_branch(git_dir)
    print("  ⬇️  git fetch…")
    fetched = self_update.git_fetch(git_dir, branch)
    if fetched.returncode != 0:
        print(f"  ❌ git fetch origin {branch} failed:\n{_git_said(fetched)}", file=sys.stderr)
        sys.exit(1)

    if self_update.git_is_up_to_date(git_dir, branch):
        print("\n✅ Already up to date!")
        return

    tracked = self_update.git_tracked_changes(git_dir)
    if tracked:
        _refuse_dirty_tree(tracked, "a fast-forward")
    _finish_git_update(git_dir, "")


def _update_git_release(git_dir: str, channel: str, pin: str) -> None:
    """Release channels (stable/beta) and pins: ride a release TAG, not a branch."""
    import asyncio

    try:
        target = asyncio.run(self_update.resolve_target(channel, pin))
    except Exception:
        logging.getLogger(__name__).debug("resolve_target failed", exc_info=True)
        target = ""
    if not target:
        # Exit 1: the update did not happen, and "already current" is a different answer.
        print(
            "\n⚠️  No matching release found for this channel/pin (offline?).\n"
            "   Nothing to update to — try again when a release is reachable.",
            file=sys.stderr,
        )
        sys.exit(1)
    if _already_there(target, pin):
        return

    tracked = self_update.git_tracked_changes(git_dir)
    if tracked:
        _refuse_dirty_tree(tracked, f"checking out {target}")
    _finish_git_update(git_dir, target)


#: How ``personalclaw update`` marks each step of a checkout's update (``checkout_update``).
_STEP_MARKS = {
    "pulling": "⬇️ ",
    "installing": "🔨",
    "building": "🧱",
    "warning": "⚠️ ",
    "error": "❌",
    "cancelled": "⏹ ",
}


def _say_step(step: str, detail: str) -> None:
    """Print one step of a checkout's update; how one that stopped ended, on stderr."""
    ended = step in {"error", "cancelled"}
    print(f"  {_STEP_MARKS.get(step, '•')} {detail}", file=sys.stderr if ended else None)


def _finish_git_update(git_dir: str, target: str) -> None:
    """Update the checkout to release *target* (none: fast-forward its branch), then refresh the
    agent config and say to restart the gateway.

    The update is the one the dashboard's Update runs too (``checkout_update.update_checkout``):
    it moves the checkout, installs it with the tool that made the environment and builds its
    frontend, at the PACKAGE root, which is nested one level under the repo root (where git runs)
    in the monorepo layout. When the install fails it puts the checkout back on the commit it was
    on, and this exits 1 with what it said. A Ctrl-C stops it (:func:`_update_until_ctrl_c`), and
    this exits 130 once it has said what that left.
    """
    import asyncio

    try:
        failed = asyncio.run(_update_until_ctrl_c(git_dir, target))
    except (asyncio.CancelledError, KeyboardInterrupt):
        sys.exit(130)
    if failed:
        print(f"  ❌ {failed}", file=sys.stderr)
        sys.exit(1)

    print("\n✅ PersonalClaw updated!")
    print(f"\n{DATA_WARNING}\n")
    _refresh_agent_config(self_update.package_root(git_dir))
    print("\n  ↻ Restart the gateway to run the new code: personalclaw restart")


async def _update_until_ctrl_c(git_dir: str, target: str) -> str:
    """The checkout's update (``checkout_update.update_checkout``), which a Ctrl-C stops as the
    dashboard's Cancel does: while it moves or installs the checkout, it stops the installer, puts
    the checkout back and says what that left; once the install has finished, it says the update
    will finish, and finishes.

    The first Ctrl-C asks for the stop, and the ones after it are not answered: Python's own
    Ctrl-C raised wherever the program was, and a second one while the checkout was going back
    left it on the new release with nothing said.
    """
    import asyncio

    loop = asyncio.get_running_loop()
    asked: list[asyncio.Task[None] | None] = []

    def _ctrl_c() -> None:
        if asked:
            return
        if checkout_update.stoppable():
            asked.append(loop.create_task(checkout_update.stop()))
        else:
            asked.append(None)
            print(
                "  ⏳ The new release is installed, so the update can no longer be stopped: it "
                "builds the dashboard, then finishes.",
                file=sys.stderr,
            )

    try:
        loop.add_signal_handler(signal.SIGINT, _ctrl_c)
        answering = True
    except (NotImplementedError, RuntimeError):
        # Not the main thread (or no signals here): nothing delivers a Ctrl-C to answer.
        answering = False
    try:
        return await checkout_update.update_checkout(git_dir, target, _say_step)
    finally:
        if answering:
            loop.remove_signal_handler(signal.SIGINT)


def _update_pip() -> None:
    """Upgrade a wheel install (pip / pipx / uv tool) in the running environment.

    Rides the ``updates`` channel/pin: the wheel installed is
    ``personalclaw==<resolve_wheel_target(channel, pin)>`` — the release the
    channel/pin selects — NOT a blind ``releases/latest``. A pin installs exactly
    that version; ``stable``/``beta`` resolve their channel's newest release; the
    git-only ``nightly`` channel has no published wheel and rides ``stable``.

    No source tree is required — that requirement is exactly the dead end this
    replaced. It installs with the tool that made the environment
    (``_installer.install_argv``): uv for a ``uv tool install``, which ships no pip
    module, and pip for a pip or pipx install. Unlike the dashboard's apply there is no
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
        # that would defeat the whole point of pinning. A refusal, so it exits 1.
        print(
            "\n⚠️  No release matches the pinned version (offline?).\n"
            f"   Nothing to install for pin {pin!r} — check `updates.pin` or retry online.",
            file=sys.stderr,
        )
        sys.exit(1)

    if _already_there(target, pin):
        return

    spec = self_update.upgrade_spec(target)
    if target:
        print(_move_line(target))
    _install(["-U", spec, "--quiet"])

    print("\n✅ PersonalClaw updated!")
    print(f"\n{DATA_WARNING}\n")
    _refresh_agent_config("")
    print("\n  ↻ Restart the gateway to run the new code: personalclaw restart")


def _update_container() -> None:
    """A container image cannot be updated in place — print the host's commands, or say
    there is nothing newer.

    The release is the one the ``updates`` channel/pin resolves to, and it is compared with the
    running version exactly as the other kinds compare theirs (:func:`_already_there`): nothing
    newer on the channel, or already on the pinned release, is said and prints no commands. The
    commands are the documented install's own (``container_host.update_commands``): the README's
    pull, remove and ``docker run``, or the compose file's pull and ``up -d``, carrying the image
    tag of that same release — ``stable`` -> the moving minor ``:X.Y``, ``beta`` -> ``:beta``
    while a candidate is its newest release, a pin -> the exact ``:X.Y.Z``.

    Exit 1 when there is no release to compare with: a pin naming none REFUSES (mirrors
    ``_update_pip``'s pin-miss) rather than pulling ``latest`` behind the user's back, and with
    no release list at all nothing can be said to be newer — the git kind's answer to the same
    case. Otherwise the exit code is 0 (see `_update`): the install is healthy and correctly
    configured, and the command did the only thing it can do here — say whether it is current,
    and exactly how to become current when it is not.
    """
    import asyncio

    cfg = AppConfig.load()
    pin = cfg.updates.pin
    try:
        target, image_tag = asyncio.run(self_update.resolve_image(cfg.updates.channel, pin))
    except Exception:
        logging.getLogger(__name__).debug("resolve_image failed", exc_info=True)
        target, image_tag = "", ""

    if pin and not target:
        # A pin naming no release must NEVER silently pull the latest image.
        print(
            "\n⚠️  No release matches the pinned version (offline?).\n"
            f"   Nothing to pull for pin {pin!r} — check `updates.pin` or retry online.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not target:
        print(
            "\n⚠️  No matching release found for this channel (offline?).\n"
            "   Nothing to update to — try again when a release is reachable.",
            file=sys.stderr,
        )
        sys.exit(1)
    if _already_there(target, pin):
        return

    print(_move_line(target))
    print("  📦 This is a container install — the image is replaced, not patched.")
    print("  Run these on the host:\n")
    for cmd in container_host.update_commands(image_tag):
        print(f"      {cmd}")
    print("\n  See docs/guides/containers.md. Your data lives in the mounted volume")
    print("  and survives the recreate; `personalclaw snapshot` first if you want a copy.")


def _update_desktop() -> None:
    """The desktop SHELL owns this install — delegate, don't fight it.

    No in-place apply: the gateway is a child of the Electron shell, so upgrading the
    wheel under it or re-execing it is the wrong move even where it would work (a packaged
    app has no interpreter to upgrade — the backend is a frozen PyInstaller bundle).

    What the shell then does is a RE-DOWNLOAD, not an in-app update: the electron-updater
    half of desktop updates is unbuilt (no electron-updater dependency in ``desktop/package.json``,
    nothing in the shell checks for a release), so "accept the update it offers" named an
    offer that never arrives (#2673). Restore that wording when the updater lands, not
    before — the rail in ``tests/test_desktop_install_kind.py`` reds when it does.

    Exit code 0 for the same reason as the container branch.
    """
    print("  🖥  This is a desktop install — the PersonalClaw app manages its own version.")
    print("  Install the new version from the releases page, then reopen the app:")
    print("  https://github.com/PersonalClaw/PersonalClaw/releases")


def _already_there(target: str, pin: str) -> bool:
    """Say so, and return True, when installing release *target* would not move this install.

    The one answer for every kind the CLI updates — the git checkout, the wheel and the
    container — so none of them can install, check out or print commands for a release that is
    not a move (:func:`self_update.moves_to`): the pinned release when it is already the one
    running, or on a channel, a release that is not newer than it. The sentence names the
    version running, so a build ahead of every release is told it is ahead rather than "already
    on" a release it does not run.

    An UNKNOWN target (offline, or no release ever published) is deliberately not "there": the
    caller decides what finding no release means for its kind, rather than this claiming a
    state it cannot see.
    """
    if not target or self_update.moves_to(target, __version__, pin):
        return False
    print(f"\n✅ {self_update.up_to_date_sentence(target, __version__, pin)}.")
    return True


def _move_line(target: str) -> str:
    """``v<running> → v<target>``, with an arrow for the way the move goes (a pin can go back)."""
    arrow = "⬆️ " if versions.is_newer(target, __version__) else "⏪"
    return f"  {arrow} v{__version__} → v{self_update.normalize_version(target)}"


def _install(args: list[str]) -> None:
    """Install *args* (a wheel's ``-U personalclaw==<tag>``) into PersonalClaw's own environment
    with the tool that made it, or exit 1 with a readable reason."""
    from personalclaw._installer import (
        NoInstallerError,
        changed_distributions,
        install_argv,
        installed_distributions,
        installer_env,
    )

    try:
        argv = install_argv(args)
    except NoInstallerError as exc:
        print(f"  ❌ {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"  🔨 {shlex.join(argv)}")
    environment = installed_distributions()
    try:
        result = subprocess.run(argv, capture_output=True, text=True, env=installer_env())
    except KeyboardInterrupt:
        # Ctrl-C reached the installer too, and `subprocess.run` has stopped it. A wheel has
        # nothing to put back: what is left is what the environment holds, said as the
        # dashboard's Cancel says it.
        said, _as_it_was = self_update.stopped_upgrade_sentence(
            self_update.UPDATE_STOPPED, changed_distributions(environment), __version__
        )
        print(f"  {_STEP_MARKS['cancelled']} {said}", file=sys.stderr)
        sys.exit(130)
    if result.returncode != 0:
        # Same one-line summary the dashboard shows: uv's stderr is ANSI-colored and
        # leads with the headline, so raw stderr reads as corrupted or as a fragment.
        summary = self_update.installer_error_summary(result.stderr or "", limit=500)
        print(
            f"  ❌ Install failed: {summary}" if summary else "  ❌ Install failed", file=sys.stderr
        )
        sys.exit(1)


def _pin_before_update(to: str) -> None:
    """`--to <version>`: pin ``updates.pin`` so this and every later apply target it.

    The rollback entry point, and it is a PIN rather than a one-shot install for
    the reason the pin exists: a bare downgrade would be undone by the next scheduled
    check, which would resolve the channel's newest release and offer to jump straight
    back to the version the user just left. Pinning first means the resolvers already
    in place — :func:`select_target`, ``resolve_wheel_target``, ``select_image`` —
    do the work on every surface, so there is no downgrade-specific install path
    anywhere.

    Exits 1 without touching config on an unusable version, and on a failed write: a
    silent fall-through would run the CHANNEL's apply instead, i.e. upgrade the user
    who asked to roll back.
    """
    target = self_update.normalize_version(to)
    if not self_update.set_version_pin(target):
        print(f"❌ Not a usable version to pin: {to!r}", file=sys.stderr)
        print("   Give a release version, e.g. `personalclaw update --to 0.1.3`.", file=sys.stderr)
        sys.exit(1)
    print(f"  📌 Pinned updates.pin = {target} (clear it to follow the channel again)")
    if versions.is_newer(__version__, target):
        # A downgrade can meet state written by the newer build. Pre-1.0 there is no
        # migration machinery either way, so the honest advice is a snapshot.
        print(f"  ⏪ Rolling BACK: v{__version__} → v{target}")
        print("     Take a snapshot first if you have not: `personalclaw snapshot`")
    print()


def _update(to: str = "") -> None:
    """`personalclaw update` — advance this install, per how it was installed.

    | kind | what happens | exit |
    |---|---|---|
    | git | fetch + checkout the channel/pin release tag (nightly: | 0; 1 on failure (the |
    |  | fast-forward) + install with the tool that made the | checkout put back), no |
    |  | environment + SPA build (`checkout_update`), then | release found, or a |
    |  | "restart the gateway" | dirty tree or a missing |
    |  |  | tool blocking it |
    | pip | that tool's `-U personalclaw==<channel/pin tag>`, | 0; 1 on install failure |
    |  | then "restart the gateway" (pip / pipx / uv tool) | or a pin naming no release |
    | container | prints the host's pull + recreate for the documented | 0; 1 on a pin naming no |
    |  | install (`container_host.update_commands`), or says it is | release, or no release |
    |  | on the newest release | found |
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
    # pinned release through the resolver it already uses. Pinning is what makes
    # this a rollback and not just a one-time install of an older wheel.
    if to:
        _pin_before_update(to)

    kind = self_update.detect_install_kind()
    if kind not in _UPDATE_HANDLED_KINDS:
        # No silent fall-through to the git pipeline: advancing a tree this kind may
        # not even own is the worst possible guess.
        print(
            f"❌ Unrecognized install kind: {kind!r} — refusing to guess how to update it.",
            file=sys.stderr,
        )
        print(
            "   Check PERSONALCLAW_INSTALL_KIND, or update the way you installed:", file=sys.stderr
        )
        print("   pip/pipx/uv tool → upgrade the `personalclaw` package;", file=sys.stderr)
        print(
            "   container → pull the new image and recreate the container "
            "(docs/guides/containers.md#updates); git checkout → check out the new tag.",
            file=sys.stderr,
        )
        sys.exit(1)

    if kind == "git":
        _update_git(self_update.source_checkout())
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
    """Query this home's running gateway for stats (``home_gateway.reach``), or say there is none.
    Each report names the service installed for this home, when there is one.

    No gateway of this home running is a report, on stdout, exit 0. A port named with ``--port`` or
    ``PERSONALCLAW_PORT`` that is not where this home's gateway answers is a refusal, on stderr
    alone, exit 1: what answers there is not this home's, and its numbers are not this home's."""
    service = service_controller.this_homes_service()
    try:
        gateway = home_gateway.reach(getattr(args, "port", None))
    except home_gateway.NoGatewayRunning as exc:
        print(exc)
        _print_service(service)
        return
    except home_gateway.GatewayError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)
    request = urllib.request.Request(f"{gateway.url}/api/status")
    try:
        with home_gateway.open_loopback(request, timeout=3) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            print("PersonalClaw gateway is running (token auth enabled).")
            print("  For detailed stats, see the Overview page in the dashboard.")
        else:
            print(f"PersonalClaw gateway is running but returned HTTP {e.code}.")
        _print_service(service)
        return
    except (urllib.error.URLError, OSError):
        print(gateway.gone())
        _print_service(service)
        return
    except Exception:
        print("PersonalClaw gateway is running but returned an unexpected response.")
        _print_service(service)
        return

    print(f"PersonalClaw v{__version__}\n")
    for label, path in _STATUS_LINES:
        print(f"  {label + ':':<12} {_status_value(data, path)}")
    _print_service(service)


def _print_service(service: service_controller.InstalledService | None) -> None:
    """``status``'s line for the service installed for this home: which it is, and whether its
    service manager runs it now."""
    if service is None:
        return
    state = (
        "running"
        if service_controller.is_service_active()
        else "installed, not running (`personalclaw restart` starts it)"
    )
    print(f"  {'Service:':<12} {service.name}, {state}")


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
    from personalclaw.routing.rates import adopt_prices_set_before

    # The prices set before in a file of their own join the config before it is loaded.
    adopt_prices_set_before()
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
    # resolve app/user layers the operator asked to keep out.
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

    # Unattended login enrollment. A no-op unless
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
        print(
            f"\n⛔ PersonalClaw did not start — governance could not be established.\n\n{exc}\n",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


def _build_consolidator() -> tuple[HistoryConsolidator, ConversationLog]:
    """Assemble a standalone HistoryConsolidator for one-shot CLI extraction.

    Mirrors the gateway wiring: a real memory + vector store (so structured
    memories land) and a SkillsLoader (so auto skills get written), driven off
    the active embedding selection.
    """
    cfg = AppConfig.load()

    memory = MemoryStore()
    memory.init()

    # confidence_threshold is read live by the store (`memory.semantic_confidence_threshold`),
    # and it embeds with the model bound in Settings → Models at each use, as the gateway's does.
    vector_memory = VectorMemoryStore(
        extra_prefixes=cfg.memory.semantic_keys or None,
        dedup_threshold=cfg.memory.episodic_dedup_threshold,
        episodic_max=cfg.memory.episodic_max_count,
        episodic_limit=cfg.memory.episodic_max_results,
    )
    vector_memory.init()
    memory.vector_store = vector_memory
    vector_memory.serve_recall()

    conv_log = ConversationLog()
    conv_log.init()
    consolidator = HistoryConsolidator(
        log=conv_log,
        memory=memory,
        history_idle_secs=cfg.memory.history_idle_hours * 3600,
        vector_store=vector_memory,
        migrated=cfg.memory.migrated,
        skills_loader=SkillsLoader(),
        auto_skills_enabled=cfg.skills.auto_create_from_sessions,
        auto_refine_enabled=cfg.skills.auto_refine_on_deviation,
        auto_min_tool_calls=cfg.skills.auto_min_tool_calls,
        auto_similarity_threshold=cfg.skills.auto_similarity_threshold,
    )
    return consolidator, conv_log


#: Why `personalclaw consolidate` leaves a session alone.
_KEEPS_NOTHING = (
    "it is Incognito or Temporary, or its mode could not be read, so nothing from it is written "
    "to memory"
)


async def _consolidate_cmd(args: argparse.Namespace) -> None:
    """Run skill/memory extraction over one session (or every session) on demand.

    The same engine the 3-hour idle poll and session-end triggers use; always
    extracts from the full transcript (``include_history=True``). Refused while incident mode
    is on, as a trigger's Run now is: consolidation is automation's model call.
    """
    from personalclaw.guardrails.incident import incident_active

    if incident_active():
        print(
            "⛔ Incident mode is on — memory consolidation is suspended.\n"
            "   Resume with: personalclaw incident off",
            file=sys.stderr,
        )
        sys.exit(1)
    consolidator, conv_log = _build_consolidator()

    if getattr(args, "all", False):
        keys = [s["key"] for s in conv_log.list_sessions()]
        if not keys:
            print("No sessions to consolidate.")
            return
        print(f"Consolidating {len(keys)} session(s)…")
        ran = 0
        for key in keys:
            if consolidator.keeps_nothing_from(key):
                print(f"  • {key} skipped: {_KEEPS_NOTHING}")
            elif await consolidator.consolidate_session(key):
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
    if consolidator.keeps_nothing_from(key):
        print(f"• Session '{key}' was not consolidated: {_KEEPS_NOTHING}.")
        return
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
        rc = service_controller.install_service(
            extra=getattr(args, "env", None) or (), without=getattr(args, "no_env", None) or ()
        )
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
    # `status`: the parser allows no other command.
    rc = service_controller.service_status()
    sel().log_api_access(
        caller="cli",
        operation="service_status",
        outcome="allowed" if rc == 0 else "error",
        source="cli",
        resources=f"rc={rc}",
    )
    return rc


def _logs_cmd(args: argparse.Namespace) -> None:
    """Tail the gateway's log from where the running gateway writes it.

    Order of preference:
      1. systemd journal (if the system service is installed on Linux)
      2. launchd's stderr file (macOS): the console stream the gateway logs to, which also holds
         anything it wrote before its logging started. Its stdout file has only what it printed.
      3. ``~/.personalclaw/gateway.log`` (foreground gateway)

    Each shows the same log lines (``log_sinks``): the gateway's own, its apps', and any
    library's warnings.
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

    if plat == Platform.LAUNCHD and svc_macos.STDERR_LOG.exists():
        cmd = ["tail", "-n", str(lines)]
        if follow:
            cmd.append("-f")
        cmd.append(str(svc_macos.STDERR_LOG))
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
