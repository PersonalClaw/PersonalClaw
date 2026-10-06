"""Platform dispatch for service install/uninstall/status.

CLI entry points should call functions in this module rather than
importing :mod:`personalclaw.service.linux` or :mod:`personalclaw.service.macos`
directly. This keeps the dispatch logic in one place and makes the
``UNSUPPORTED`` path produce consistent error output.
"""

import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from personalclaw import container_host, gateway_base, tmux_substrate
from personalclaw.config import loader as config_loader
from personalclaw.service import linux, macos
from personalclaw.service.common import (
    LAUNCHD_LABEL,
    SERVICE_NAME,
    Platform,
    current_platform,
)
from personalclaw.service.environment import Capture, describe, summary


def _unsupported_message() -> None:
    print(
        "❌ personalclaw service management is only supported on Linux (systemd)\n"
        "   and macOS (launchd). On other platforms run `personalclaw gateway`\n"
        "   directly or wrap it in tmux/screen yourself.",
        file=sys.stderr,
    )


def _in_a_container(outcome: str) -> None:
    """Say who keeps the gateway running in a container, and *outcome*: that nothing changed.

    Not the unsupported message. That one advises running the gateway by hand or under
    tmux/screen, which in a container is wrong: the gateway already runs, as the container's
    own process, and the container runtime brings it back after a crash. On stdout, because
    nothing failed.
    """
    print(f"{container_host.keeps_running()} {outcome}")


def _print_installed(file: str, path: Path, carried: Capture) -> None:
    """Say the service is installed, where its file is, what its environment carries from this
    shell (and what it left out), and the commands that act on it.

    The service opens no browser and makes no sign-in link at any of its starts, the first one
    included (``gateway._announce_dashboard``), so the person who installed it is told the one
    command that makes a link: ``personalclaw token``, which signs a browser in when it is opened.
    """
    print("✅ personalclaw service installed and started.")
    print(f"   {file}: {path}")
    for line in summary(carried):
        print(f"   {line}")
    print()
    print("   Sign in: personalclaw token")
    print("   Status:  personalclaw service status")
    print("   Logs:    personalclaw logs -f")
    print("   Remove:  personalclaw service uninstall")


def install_service(*, extra: Iterable[str] = (), without: Iterable[str] = ()) -> int:
    """Install and start the platform service.

    *extra* and *without* are ``--env NAME`` and ``--no-env NAME``: one more variable to carry
    into the service's environment, one to leave out (:mod:`personalclaw.service.environment`).

    Returns 0 on success, non-zero otherwise. On Linux the install
    prompts for sudo on first use to write
    ``/etc/systemd/system/personalclaw.service`` and to run
    ``systemctl daemon-reload / enable / restart``. The gateway itself
    runs as ``User=$USER`` once started — personalclaw code is never
    invoked under sudo. On macOS no sudo is required. The CLI is
    expected to surface the sudo prompt to a real terminal.

    In a container there is nothing to install, and 0 says the job is done: the container
    runtime keeps the gateway running, the way ``personalclaw update`` there exits 0 after
    saying what the host runs.
    """
    plat = current_platform()
    if plat == Platform.CONTAINER:
        _in_a_container("There is no service to install here, and nothing was changed.")
        return 0
    if plat == Platform.SYSTEMD:
        try:
            carried = linux.install(extra=extra, without=without)
        except linux.ServiceInstallError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        _print_installed("unit", linux.UNIT_PATH, carried)
        return 0
    if plat == Platform.LAUNCHD:
        try:
            carried = macos.install(extra=extra, without=without)
        except macos.ServiceInstallError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        _print_installed("plist", macos.PLIST_PATH, carried)
        return 0
    _unsupported_message()
    return 2


def uninstall_service() -> int:
    """Stop and remove the platform service, and its home's tmux server. Idempotent.

    The server holds the gateway's persistent terminals and durable workers, and outlives the
    gateway by design, so stopping the service leaves it running. Its home comes from the
    installed file, read before the file is removed; with no service installed, nothing is
    stopped. A container has no service to remove.
    """
    plat = current_platform()
    if plat == Platform.CONTAINER:
        _in_a_container("There is no service to remove here, and nothing was changed.")
        return 0
    if plat not in (Platform.SYSTEMD, Platform.LAUNCHD):
        _unsupported_message()
        return 2
    platform = linux if plat == Platform.SYSTEMD else macos
    env = platform.installed_environment()
    platform.uninstall()
    if env:
        tmux_substrate.kill_server(config_loader.resolve_config_dir(env))
    print("✅ personalclaw service stopped and removed.")
    return 0


def service_status() -> int:
    """Print the platform service status and the environment its installed file starts the
    gateway in. Returns 0 if active, 1 if inactive, 2 if unsupported.

    In a container the container runtime is the service, and it is active when this home's
    gateway is: the one it runs as the container's process."""
    plat = current_platform()
    if plat == Platform.CONTAINER:
        running = gateway_base.live_gateway()
        _in_a_container(
            f"The gateway is running, on port {running.port}."
            if running
            else "The gateway is not running."
        )
        return 0 if running else 1
    if plat == Platform.SYSTEMD:
        print(linux.status())
        _print_environment(linux.installed_environment())
        return 0 if linux.is_active() else 1
    if plat == Platform.LAUNCHD:
        print(macos.status())
        _print_environment(macos.installed_environment())
        return 0 if macos.is_active() else 1
    _unsupported_message()
    return 2


def _print_environment(env: dict[str, str]) -> None:
    """The installed file's environment, when there is an installed file."""
    if env:
        print("\n".join(describe(env)))


def is_service_active() -> bool:
    """Return True if a personalclaw service is installed and currently running."""
    plat = current_platform()
    if plat == Platform.SYSTEMD:
        return linux.is_active()
    if plat == Platform.LAUNCHD:
        return macos.is_active()
    return False


@dataclass(frozen=True)
class InstalledService:
    """The service installed for this home, whether or not its service manager runs it now."""

    platform: Platform
    #: How the user reads it: ``the launchd job io.personalclaw.gateway``.
    name: str
    #: Its file: the plist or the unit.
    path: Path
    #: When its service manager starts it again by itself, once ``stop`` has stopped it.
    comes_back: str


def this_homes_service() -> InstalledService | None:
    """The service installed for this home, running or not; ``None`` when there is none here.

    The installed FILE decides, not whether its service manager runs it now: ``stop`` leaves the
    launchd plist and the systemd unit in place, and both start the service again by themselves,
    so a stopped service is still how this home's gateway runs. ``restart`` starts it, and
    ``status`` names it. The home it runs is read from the file's environment, as
    ``service uninstall`` reads it: a service of another home is that home's to stop and restart,
    not a command run with this one.
    """
    plat = current_platform()
    if plat == Platform.SYSTEMD:
        path, env = linux.UNIT_PATH, linux.installed_environment
        name = f"the systemd unit {SERVICE_NAME}.service"
        comes_back = "It starts again when this computer next starts"
    elif plat == Platform.LAUNCHD:
        path, env = macos.PLIST_PATH, macos.installed_environment
        name = f"the launchd job {LAUNCHD_LABEL}"
        comes_back = "It starts again when you next log in"
    else:
        return None
    if not path.exists():
        return None
    runs = config_loader.resolve_config_dir(env())
    if runs.resolve() != config_loader.resolve_config_dir().resolve():
        return None
    return InstalledService(plat, name, path, comes_back)


def stop_service(service: InstalledService) -> str:
    """Stop *service*, leaving it installed. Returns what its service manager said when it could
    not, else ``""``."""
    return (linux if service.platform == Platform.SYSTEMD else macos).stop()


def restart_service(service: InstalledService) -> str:
    """Start *service* fresh, whether or not its service manager runs it now. Returns what the
    service manager said when it could not, else ``""``."""
    return (linux if service.platform == Platform.SYSTEMD else macos).restart()
