"""CLI server lifecycle commands — update, stop, token, logout, status, gateway."""

import argparse
import contextlib
import json
import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NoReturn

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
from personalclaw.cli_output import explain, print_json
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
from personalclaw.security import terminal_text
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


#: Why ``token --remote`` has no address, after "❌ ", by what ``dashboard.url`` holds. The fix is
#: the same for each: name the address other devices open, or take this computer's link.
_NO_REMOTE_ADDRESS_FIX = (
    "so there is no address for another device's link. Set it to the address other devices "
    "open (personalclaw config set dashboard.url https://…), or leave out --remote for this "
    "computer's link."
)


def _remote_origin() -> tuple[str, str]:
    """The origin of ``dashboard.url``, the address other devices open, and ``""``; or ``""`` and
    the sentence saying why there is none: it is unset, it names no http or https address, or
    ``config.json`` could not be read (``AppConfig.load`` never raises, so a discarded read is
    told apart through ``config_discard``). The sentence never echoes the value, which may hold a
    user name and password."""
    url = (AppConfig.load().dashboard.url or "").strip()
    discard = config_loader.config_discard()
    if discard is not None:
        return "", (
            f"config.json could not be read: {discard.reason}, so there is no address for another "
            "device's link. Repair it (personalclaw config edit opens it as it is, and the fixed "
            "copy replaces it), or leave out --remote for this computer's link."
        )
    if not url:
        return "", f"dashboard.url is not set, {_NO_REMOTE_ADDRESS_FIX}"
    origin = dashboard_origin(url)
    if not origin:
        return "", f"dashboard.url names no http or https address, {_NO_REMOTE_ADDRESS_FIX}"
    return origin, ""


def _token(args: argparse.Namespace) -> None:
    """Print a sign-in link from this home's gateway (``home_gateway.reach``): the one it
    recorded, or the one ``--port`` or ``PERSONALCLAW_PORT`` names, once it has shown it serves
    this home. The home's local secret goes to no other.

    stdout is the one thing the form typed asks for, so ``personalclaw token | pbcopy`` copies
    it: this computer's link, the link at ``dashboard.url`` (``--remote``), the token alone
    (``--bearer``), or one JSON document (``--json``). What it is and how long it lasts is
    ``--explain``'s, on stderr, and never holds the token.

    Everything that can refuse is checked before the gateway is asked for anything, so a refusal
    mints no sign-in: a ``--ttl`` that is not a lifetime, or one over the 90-day limit, and a
    ``--remote`` with no address to put in the link.
    """
    ttl = parse_duration(args.ttl)
    if ttl is None or ttl > MAX_SESSION_TTL_SECS:
        refusal = lifetimes.unreadable(args.ttl) if ttl is None else lifetimes.too_long(ttl)
        print(f"❌ {refusal}", file=sys.stderr)
        sys.exit(1)
    remote = getattr(args, "remote", False)
    bearer = getattr(args, "bearer", False)
    as_json = getattr(args, "as_json", False)
    explaining = getattr(args, "explain", False)
    # `dashboard.url` is read once, and only by a form that uses it: the default link is the
    # address the gateway proved, and needs nothing from config.json.
    origin, no_origin = _remote_origin() if (remote or as_json or explaining) else ("", "")
    if remote and not origin:
        print(f"❌ {no_origin}", file=sys.stderr)
        sys.exit(1)

    try:
        gateway = home_gateway.reach(args.port)
        data = gateway.sign_in(args.ttl)
    except home_gateway.GatewayError as exc:
        print(f"❌ {terminal_text(str(exc))}", file=sys.stderr)
        sys.exit(1)
    port = gateway.port
    token = str(data.get("token") or "")

    if not token:
        print("❌ Gateway returned empty token", file=sys.stderr)
        sys.exit(1)
    # The address the reach proved this home's gateway at, where it signed this command in: never a
    # host read from this command's own environment, which can name another address, or a name.
    link = f"{gateway.url}?token={token}"
    remote_link = f"{origin}?token={token}" if origin else None
    if as_json:
        print_json(
            {
                "url": link,
                "remote_url": remote_link,
                "token": token,
                "expires_in": data.get("expires_in"),
                "expires_at": data.get("expires_at"),
                "open_within": data.get("open_within"),
            }
        )
    elif remote:
        print(remote_link)
    elif bearer:
        print(token)
    else:
        print(link)
    local_link = not (remote or bearer)
    explain(
        args,
        _token_lifetime_note(data, bearer=bearer),
        # The port the printed URL carries: only a form that prints this computer's link has it.
        (container_port_note(port) or "") if local_link else "",
        (
            f"For another device, personalclaw token --remote prints the link at {origin}."
            if origin and not (remote or bearer or as_json)
            else ""
        ),
    )


def _token_lifetime_note(reply: dict, *, bearer: bool = False) -> str:
    """What the minted token is and how long it lasts, from the gateway's own numbers: a sign-in
    link, or with *bearer* the token alone, which a script sends as a header.

    Empty when the gateway did not say (an older one still running beside a newer CLI):
    stating a lifetime this side cannot know would be a guess.
    """
    try:
        lasts = int(reply["expires_in"])
        open_within = int(reply.get("open_within") or lasts)
        until = time.strftime("%H:%M on %d %B", time.localtime(float(reply["expires_at"])))
    except (KeyError, TypeError, ValueError):
        return ""
    if bearer:
        what = (
            "This is a token for your PersonalClaw dashboard: send it as an "
            f'"Authorization: Bearer" header from a script. It works for {duration_words(lasts)}, '
            f"until {until}."
        )
    elif open_within >= lasts:
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
    if not bearer:
        what = f"This is a sign-in link for your PersonalClaw dashboard. {how}"
    return (
        f"{what}\n"
        "Treat it like a password: anyone who has it can use your dashboard. Settings → Devices & "
        "Access → Devices lists every sign-in, and signs any of them out."
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
        print(f"❌ {terminal_text(str(exc))}", file=sys.stderr)
        sys.exit(1)
    if status != 200:
        print(f"❌ Failed to revoke sessions: HTTP {status}", file=sys.stderr)
        sys.exit(1)
    if not data.get("ok"):
        print(
            "❌ Failed to revoke sessions: "
            f"{terminal_text(str(data.get('error', 'unknown error')))}",
            file=sys.stderr,
        )
        sys.exit(1)
    print("✅ All dashboard sessions revoked.")


#: How long `stop` waits for the gateway to exit once asked. The gateway bounds its own graceful
#: shutdown at 7 s, and 1 s more to unwind (`GatewayOrchestrator._finish`), and then exits, so this
#: is that bound with a margin for its last cleanup. Returning only once it is gone is what lets
#: `restart` start the next gateway, and `restore` run, without the old one still writing the home.
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
            print(
                f"❌ Could not stop {service.name}: {terminal_text(str(failed))}", file=sys.stderr
            )
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
        _roll_back_for_the_service()
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
            print(
                f"❌ Could not start {service.name}: {terminal_text(str(failed))}", file=sys.stderr
            )
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


def _roll_back_for_the_service() -> None:
    """A rollback meant (``gateway_base.ROLL_BACK_ENV`` set for this command) on a home a service
    serves: the service manager starts the gateway with an environment of its own, which cannot
    carry the variable, so the home's version stamp is lowered here, before the restart, exactly as
    the new gateway's claim would lower it (``gateway_base.roll_back_for_a_service``). A value that
    names another version, or a stamp that cannot be used, ends the restart with why."""
    meant = os.environ.get(gateway_base.ROLL_BACK_ENV, "")
    if not meant:
        return
    try:
        came_from = gateway_base.roll_back_for_a_service(meant)
    except gateway_base.HomeServedByNewer as newer:
        print(
            f"❌ {gateway_base.ROLL_BACK_ENV}={meant} does not name {newer.newest}, the "
            "PersonalClaw this home was last used by, so it was not rolled back and nothing was "
            f"restarted. To roll it back to {newer.running}: "
            f"`{gateway_base.ROLL_BACK_ENV}={newer.newest} personalclaw restart`",
            file=sys.stderr,
        )
        sys.exit(1)
    except gateway_base.HomeNotClaimed as refused:
        print(f"❌ {refused}", file=sys.stderr)
        sys.exit(1)
    if came_from:
        sel().log_api_access(
            caller="cli",
            operation="home_rolled_back",
            outcome="allowed",
            source="cli",
            resources=f"from={came_from} to={__version__} via=service",
        )
        print(f"  ↩ Rolled this home back from {came_from}, as {gateway_base.ROLL_BACK_ENV} asked.")


# Every InstallKind `_update` maps to a branch. The dispatch is exhaustive over
# `self_update.INSTALL_KINDS` and has NO default arm that falls back to the git
# pipeline: this command used to BE the git pipeline, so a pip/pipx/uv-tool
# user got "PERSONALCLAW_PROJECT_DIR not set" and exit 1 — a dead end with the
# per-kind machinery one module away. A kind added later must be mapped here
# consciously; `test_cli_update_kinds` reds until it is.
_UPDATE_HANDLED_KINDS: frozenset[str] = frozenset({"git", "pip", "container", "desktop"})


def _say_updated(cwd: str, target: str) -> None:
    """How an update that finished ends, a checkout's or a wheel's: that it is updated, the
    data-handling reminder its transcript keeps on stdout, the agent config refreshed from *cwd*
    (:func:`_refresh_agent_config`), and the line saying to restart for *target*."""
    print("\n✅ PersonalClaw updated!")
    print(f"\n{DATA_WARNING}\n")
    _refresh_agent_config(cwd)
    print(_restart_line(target))


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
        print(
            "  ⚠️  Agent config refresh failed — run: personalclaw setup --agent-only",
            file=sys.stderr,
        )


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
        print(f"      {terminal_text(str(line))}", file=sys.stderr)
    print("     Commit or `git stash` them, then re-run `personalclaw update`.", file=sys.stderr)
    sys.exit(1)


def _check_git(proj: str, channel: str, pin: str) -> Callable[[], None]:
    """Check a git checkout's update, and return what runs it once ``update`` has said so.

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
    if channel == "nightly":
        go = _check_git_nightly(git_dir)
    else:
        go = _check_git_release(git_dir, channel, pin)

    def update() -> None:
        print(f"  📂 {git_dir}")
        go()

    return update


def _git_said(result: subprocess.CompletedProcess[str]) -> str:
    """What a failed git step printed, for the owner's terminal: masked like every view, with
    its line breaks kept and every control character escaped, so a remote's message cannot
    move the cursor or hide text. The updater has already put a refused transport in
    PersonalClaw's own words (``self_update._run_git``)."""
    from personalclaw.security import mask_child_output

    return mask_child_output(result.stderr, limit=None, one_line=False)


def _check_git_nightly(git_dir: str) -> Callable[[], None]:
    """Nightly/developer channel: track the current branch by fast-forward only. The fetch, and
    the clean tree a fast-forward needs, are checked here, before anything prints."""
    branch = self_update.resolve_default_branch(git_dir)
    fetched = self_update.git_fetch(git_dir, branch)
    if fetched.returncode != 0:
        print(
            f"  ❌ git fetch origin {branch} failed:\n{terminal_text(str(_git_said(fetched)))}",
            file=sys.stderr,
        )
        sys.exit(1)
    current = self_update.git_is_up_to_date(git_dir, branch)
    if not current:
        tracked = self_update.git_tracked_changes(git_dir)
        if tracked:
            _refuse_dirty_tree(tracked, "a fast-forward")

    def update() -> None:
        print("  ⬇️  git fetch…")
        if current:
            print("\n✅ Already up to date!")
            return
        _finish_git_update(git_dir, "")

    return update


def _update_git_nightly(git_dir: str) -> None:
    """Check the nightly channel's update of *git_dir*, then run it."""
    _check_git_nightly(git_dir)()


def _check_git_release(git_dir: str, channel: str, pin: str) -> Callable[[], None]:
    """Release channels (stable/beta) and pins: ride a release TAG, not a branch. The release,
    and the clean tree its checkout needs, are checked here, before anything prints."""
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
    if self_update.moves_to(target, __version__, pin):
        tracked = self_update.git_tracked_changes(git_dir)
        if tracked:
            _refuse_dirty_tree(tracked, f"checking out {target}")

    def update() -> None:
        if _already_there(target, pin):
            return
        _finish_git_update(git_dir, target)

    return update


def _update_git_release(git_dir: str, channel: str, pin: str) -> None:
    """Check a release channel's update of *git_dir*, then run it."""
    _check_git_release(git_dir, channel, pin)()


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
    """Print one step of a checkout's update; a warning, and how one that stopped ended, on
    stderr."""
    to_stderr = step in {"error", "cancelled", "warning"}
    print(
        f"  {_STEP_MARKS.get(step, '•')} {terminal_text(str(detail))}",
        file=sys.stderr if to_stderr else None,
    )


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
        print(f"  ❌ {terminal_text(str(failed))}", file=sys.stderr)
        sys.exit(1)
    _say_updated(self_update.package_root(git_dir), target)


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


def _restart_line(target: str) -> str:
    """The last line of an update to *target*: restart the gateway to run it, and, when *target*
    is older than the newest PersonalClaw that has served this home, say the rollback is meant, as
    the new gateway's claim asks (``gateway_base.ROLL_BACK_ENV``): a restart without it is refused.
    """
    newest = gateway_base.rollback_into(target)
    if not newest:
        return "\n  ↻ Restart the gateway to run the new code: personalclaw restart"
    return (
        f"\n  ↻ Restart the gateway to run the new code. This home was last used by {newest}, "
        f"newer than {target}, so say the rollback is meant (take a snapshot first): "
        f"`{gateway_base.ROLL_BACK_ENV}={newest} personalclaw restart`"
    )


def _check_pip(channel: str, pin: str) -> Callable[[], None]:
    """Check a wheel install's upgrade (pip / pipx / uv tool), and return what runs it.

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

    def update() -> None:
        if _already_there(target, pin):
            return
        spec = self_update.upgrade_spec(target)
        if target:
            print(_move_line(target))
        _install(["-U", spec, "--quiet"])
        _say_updated("", target)

    return update


def _check_container(channel: str, pin: str) -> Callable[[], None]:
    """A container image cannot be updated in place — check the release, and return what prints
    the host's commands, or says there is nothing newer.

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

    try:
        target, image_tag = asyncio.run(self_update.resolve_image(channel, pin))
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

    def update() -> None:
        if _already_there(target, pin):
            return
        print(_move_line(target))
        print("  📦 This is a container install — the image is replaced, not patched.")
        print("  Run these on the host:\n")
        for cmd in container_host.update_commands(image_tag):
            print(f"      {cmd}")
        newest = gateway_base.rollback_into(target)
        if newest:
            print(
                f"\n  ↩ This home was last used by {newest}, newer than {target}, so the new "
                "container's gateway will not start on it until you say the rollback is meant: "
                f"start it once with `{gateway_base.ROLL_BACK_ENV}={newest}` in its environment "
                "(take a snapshot first)."
            )
        print("\n  See docs/guides/containers.md. Your data lives in the mounted volume")
        print("  and survives the recreate; `personalclaw snapshot` first if you want a copy.")

    return update


def _update_desktop() -> None:
    """The desktop SHELL owns this install — delegate, don't fight it.

    No in-place apply: the gateway is a child of the Electron shell, so upgrading the
    wheel under it or re-execing it is the wrong move even where it would work (a packaged
    app has no interpreter to upgrade — the backend is a frozen PyInstaller bundle).

    The shell updates itself when its owner asks it to (``desktop/appUpdate.js``): Check for
    Updates… in its menu, or Settings › Updates › This app, and never on its own. A copy it
    cannot replace (a package, one run from the disk image) is pointed at the releases page,
    so that is named too. Before that updater this named only the re-download (#2673), and
    ``tests/test_desktop_install_kind.py`` holds every desktop surface to the updater as built.

    Exit code 0 for the same reason as the container branch.
    """
    print("  🖥  This is a desktop install — the PersonalClaw app updates it when you ask:")
    print("  choose Check for Updates… in its menu, or Settings › Updates › This app.")
    print("  Or install the new version from the releases page:")
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


@contextlib.contextmanager
def _ctrl_c_as_a_flag() -> Iterator[threading.Event | None]:
    """While the block runs, a Ctrl-C (SIGINT) sets the event this yields instead of raising, and
    the handler that was there comes back on every way out. Off the main thread no handler can be
    set: it yields None, and a Ctrl-C is Python's own."""
    if threading.current_thread() is not threading.main_thread():
        yield None
        return
    pressed = threading.Event()
    previous = signal.signal(signal.SIGINT, lambda signum, frame: pressed.set())
    try:
        yield pressed
    finally:
        signal.signal(signal.SIGINT, previous if previous is not None else signal.SIG_DFL)


def _wait_or_stop(
    process: subprocess.Popen[str], pressed: threading.Event
) -> subprocess.CompletedProcess[str]:
    """Wait for *process* to end, its output captured; once *pressed* is set, kill and reap it,
    then raise ``KeyboardInterrupt``.

    ``subprocess.run`` stops its child only when the interrupt finds it waiting in
    ``communicate``. One that lands while ``Popen`` is still handing the started child back
    leaves it running with nothing holding it: the update said it stopped while the installer
    went on. With :func:`_ctrl_c_as_a_flag` around the start, the Ctrl-C lands nowhere wrong,
    and this wait acts on it.
    """
    with process:
        while True:
            try:
                out, err = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if pressed.is_set():
                    process.kill()
                    process.communicate()
                    raise KeyboardInterrupt from None
    if pressed.is_set():
        # It ended on its own as the Ctrl-C came (a terminal's reaches the installer too).
        raise KeyboardInterrupt
    return subprocess.CompletedProcess(process.args, process.returncode, out, err)


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
        print(f"  ❌ {terminal_text(str(exc))}", file=sys.stderr)
        sys.exit(1)

    print(f"  🔨 {shlex.join(argv)}")
    environment = installed_distributions()
    try:
        with _ctrl_c_as_a_flag() as pressed:
            if pressed is None:
                result = subprocess.run(argv, capture_output=True, text=True, env=installer_env())
            else:
                started = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=installer_env(),
                )
                result = _wait_or_stop(started, pressed)
    except KeyboardInterrupt:
        # The installer is stopped (:func:`_wait_or_stop`). A wheel has nothing to put back:
        # what is left is what the environment holds, said as the dashboard's Cancel says it.
        said, _as_it_was = self_update.stopped_upgrade_sentence(
            self_update.UPDATE_STOPPED, changed_distributions(environment), __version__
        )
        print(f"  {_STEP_MARKS['cancelled']} {terminal_text(str(said))}", file=sys.stderr)
        sys.exit(130)
    if result.returncode != 0:
        # Same one-line summary the dashboard shows: uv's stderr is ANSI-colored and
        # leads with the headline, so raw stderr reads as corrupted or as a fragment.
        summary = self_update.installer_error_summary(result.stderr or "", limit=500)
        print(
            terminal_text(
                str(f"  ❌ Install failed: {summary}" if summary else "  ❌ Install failed")
            ),
            file=sys.stderr,
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
    who asked to roll back. ``update`` writes it only once the pinned release resolved
    (:func:`_usable_pin` checks the version first), so a refused update leaves the pin as it was.
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


def _usable_pin(to: str) -> str:
    """The release ``--to`` names, as ``updates.pin`` would hold it; or say it is not one, on
    stderr, and exit 1 with nothing written."""
    try:
        target = self_update.normalize_pin(self_update.normalize_version(to))
    except ValueError:
        target = ""
    if not target:
        print(f"❌ Not a usable version to pin: {to!r}", file=sys.stderr)
        print("   Give a release version, e.g. `personalclaw update --to 0.1.3`.", file=sys.stderr)
        sys.exit(1)
    return target


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

    **Everything that can refuse is checked before anything prints**: the install kind, the
    version ``--to`` names, then the kind's own checks (the git root, the release or the pin's
    release, a clean tree). So a refused update prints nothing on stdout, and ``updates.pin`` is
    written only once the release it names resolved: an unmapped kind or a missing release
    leaves it as it was.
    """
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

    # `--to <version>`: the pinned release is what every branch below resolves, through the
    # resolver it already uses. Pinning is what makes this a rollback and not just a one-time
    # install of an older wheel.
    pinning = _usable_pin(to) if to else ""
    cfg = AppConfig.load()
    channel, pin = cfg.updates.channel, pinning or cfg.updates.pin
    if kind == "git":
        update = _check_git(self_update.source_checkout(), channel, pin)
    elif kind == "pip":
        update = _check_pip(channel, pin)
    elif kind == "container":
        update = _check_container(channel, pin)
    else:
        update = _update_desktop

    print("Updating PersonalClaw…\n")
    if pinning:
        _pin_before_update(pinning)
    update()


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
_STATUS_LINES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # (the line's label, its key in `status --json`'s `stats`, its path in the payload)
    ("Uptime", "uptime", ("uptime",)),
    ("Sessions", "sessions", ("sessions",)),
    ("Subagents", "subagents", ("subagents",)),
    ("Cron jobs", "cron_jobs", ("cron", "total")),
    ("Turns", "turns", ("stats", "total_turns")),
    ("Lessons", "lessons", ("lessons",)),
)


def _status_raw(data: object, path: tuple[str, ...]) -> object:
    """The payload's own value at *path*, or ``None`` when the payload does not carry it."""
    cur = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


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

    A report is its answer: on stdout, exit 0 while this home's gateway runs, and exit 1 when none
    does (none is running, or the one that was went away while it was asked), as the exit table says
    of "no gateway is running". A gateway too busy to give its stats within 3 seconds is running,
    exit 0, and its report says so (``stats_unavailable`` ``timeout``). A port named with
    ``--port`` or ``PERSONALCLAW_PORT`` that is not where this home's gateway answers is a refusal,
    on stderr alone, exit 1: what answers there is not this home's, and its numbers are not this
    home's."""
    service = service_controller.this_homes_service()
    as_json = bool(getattr(args, "as_json", False))

    def report(running: bool, port: int | None, data: object, unavailable: str | None) -> None:
        print_json(
            {
                "running": running,
                "port": port,
                "stats": (
                    None
                    if data is None
                    else {key: _status_raw(data, path) for _label, key, path in _STATUS_LINES}
                ),
                "stats_unavailable": unavailable,
                "service": (
                    None
                    if service is None
                    else {
                        "name": service.name,
                        "path": str(service.path),
                        "running": service_controller.is_service_active(),
                    }
                ),
            }
        )

    try:
        gateway = home_gateway.reach(getattr(args, "port", None))
    except home_gateway.NoGatewayRunning as exc:
        if as_json:
            report(False, None, None, None)
            sys.exit(1)
        print(terminal_text(str(exc)))
        _print_service(service)
        sys.exit(1)
    except home_gateway.GatewayError as exc:
        print(f"❌ {terminal_text(str(exc))}", file=sys.stderr)
        sys.exit(1)
    request = urllib.request.Request(f"{gateway.url}/api/status")
    try:
        with home_gateway.open_loopback(request, timeout=3) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if as_json:
            report(
                True,
                gateway.port,
                None,
                "auth_required" if e.code in (401, 403) else f"http_{e.code}",
            )
            return
        if e.code in (401, 403):
            print("PersonalClaw gateway is running (token auth enabled).")
            print("  For detailed stats, see the Overview page in the dashboard.")
        else:
            print(f"PersonalClaw gateway is running but returned HTTP {e.code}.")
        _print_service(service)
        return
    except (urllib.error.URLError, OSError) as exc:
        if home_gateway.timed_out(exc):
            # `reach` proved it running a moment ago; slow to give its stats is not gone.
            if as_json:
                report(True, gateway.port, None, "timeout")
                return
            print(
                "PersonalClaw gateway is running but did not answer for its stats within 3 seconds."
            )
            _print_service(service)
            return
        if as_json:
            report(False, gateway.port, None, "gone")
            sys.exit(1)
        print(gateway.gone())
        _print_service(service)
        sys.exit(1)
    except Exception:
        if as_json:
            report(True, gateway.port, None, "unexpected_response")
            return
        print("PersonalClaw gateway is running but returned an unexpected response.")
        _print_service(service)
        return

    if as_json:
        report(True, gateway.port, data, None)
        return
    print(f"PersonalClaw v{__version__}\n")
    for label, _key, path in _STATUS_LINES:
        print(f"  {label + ':':<12} {terminal_text(str(_status_value(data, path)))}")
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
    from personalclaw.reply_style import backfill_home
    from personalclaw.routing.rates import adopt_prices_set_before

    # The prices set before in a file of their own join the config before it is loaded.
    adopt_prices_set_before()
    # Natural voice's stored choices become reply styles before anything reads them.
    try:
        backfill_home()
    except Exception:  # noqa: BLE001 - a backfill that cannot run never blocks startup
        logging.getLogger(__name__).warning("Reply style backfill failed", exc_info=True)
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
    # First, so a stall at boot is named too: when the browser harness asks for it, a thread
    # dumps every thread's stack into gateway.log whenever the loop stops answering (`loop_watch`).
    from personalclaw.loop_watch import arm_from_env

    arm_from_env()
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
            "\n⛔ PersonalClaw did not start — governance could not be "
            f"established.\n\n{terminal_text(str(exc))}\n",
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
                print(f"  • {terminal_text(str(key))} skipped: {_KEEPS_NOTHING}")
            elif await consolidator.consolidate_session(key):
                ran += 1
                print(f"  ✓ {terminal_text(str(key))}")
            else:
                print(f"  • {terminal_text(str(key))} (already in flight, skipped)")
        print(f"\n✅ Consolidated {ran}/{len(keys)} session(s).")
        return

    key = args.key
    if not conv_log.has_log(key):
        print(
            f"❌ No conversation history for session '{terminal_text(str(key))}'.", file=sys.stderr
        )
        sys.exit(1)
    if consolidator.keeps_nothing_from(key):
        print(f"• Session '{terminal_text(str(key))}' was not consolidated: {_KEEPS_NOTHING}.")
        return
    print(f"Consolidating session '{terminal_text(str(key))}'…")
    if await consolidator.consolidate_session(key):
        print("✅ Done.")
    else:
        print("⚠️  Already in flight — nothing to do.", file=sys.stderr)


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
      2. launchd's two files (macOS), when its stderr file is there, each under the name ``tail``
         heads it with: the stdout file, what the gateway printed (where the dashboard is and how
         to get a sign-in link), and the stderr file, the console stream it logs to, which also
         holds anything it wrote before its logging started. The systemd journal holds both
         streams the same way.
      3. ``~/.personalclaw/gateway.log`` (foreground gateway)

    The log lines are the same everywhere (``log_sinks``): the gateway's own, its apps', and any
    library's warnings. What the reader prints is relayed a line at a time through
    ``security.terminal_text`` (:func:`_relay`), so a line an older version wrote into a file as it
    came reaches the terminal as text too.
    """
    follow = bool(getattr(args, "follow", False))
    lines = int(getattr(args, "lines", 100) or 100)
    plat = current_platform()
    unit = f"{SERVICE_NAME}.service"

    # Audit before any relay branch: the relay ends this command with the reader's own exit, so
    # an audit call after it would never run.
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
            _relay(base)
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
        # (`less`) from taking over, which behaves badly in
        # piped/non-interactive contexts. sudo prompts on the terminal itself.
        sudo_cmd = ["sudo", *base]
        if follow:
            sudo_cmd.append("-f")
        _relay(sudo_cmd)

    if plat == Platform.LAUNCHD and svc_macos.STDERR_LOG.exists():
        cmd = ["tail", "-n", str(lines)]
        if follow:
            cmd.append("-f")
        if svc_macos.STDOUT_LOG.exists():
            cmd.append(str(svc_macos.STDOUT_LOG))
        cmd.append(str(svc_macos.STDERR_LOG))
        _relay(cmd)

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
    _relay(cmd)


def _relay(cmd: list[str]) -> NoReturn:
    """Run the log reader *cmd* and write what it prints to stdout a line at a time, each through
    ``security.terminal_text``, then end this command with the reader's exit status (128 and the
    signal's number for a reader a signal ended). Its stdin and stderr stay the terminal's, so
    ``sudo`` prompts there and the reader's own errors are its own. A Ctrl-C ends the reader and
    this command with 130, and output whose reader went away (``personalclaw logs | head``) ends
    them with 141, with nothing logged."""
    # Bytes, a line at a time, decoded here: a text pipe would read a lone carriage return as a
    # line break, and it is shown as the escape it is.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    try:
        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, b""):
            sys.stdout.write(terminal_text(line.decode("utf-8", "replace")))
            sys.stdout.flush()
        status = proc.wait()
    except KeyboardInterrupt:
        _stop_reader(proc)
        sys.exit(130)
    except BrokenPipeError:
        _stop_reader(proc)
        # Python flushes stdout as it exits: what is left goes to the null device.
        with contextlib.suppress(OSError, ValueError):
            nowhere = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(nowhere, sys.stdout.fileno())
            finally:
                os.close(nowhere)
        sys.exit(141)
    sys.exit(128 - status if status < 0 else status)


def _stop_reader(proc: "subprocess.Popen[bytes]") -> None:
    """End the log reader *proc*: its pipe is closed, it is asked to stop, and it is given a few
    seconds. A ``sudo`` runs as root, which this user cannot signal; it ends when its reader
    does."""
    if proc.stdout is not None:
        proc.stdout.close()
    with contextlib.suppress(OSError):
        proc.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)
