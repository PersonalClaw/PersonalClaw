"""Platform dispatch for service install/uninstall/status.

CLI entry points should call functions in this module rather than
importing :mod:`personalclaw.service.linux` or :mod:`personalclaw.service.macos`
directly. This keeps the dispatch logic in one place and makes the
``UNSUPPORTED`` path produce consistent error output.
"""

import sys
from collections.abc import Iterable

from personalclaw import container_host, gateway_base, tmux_substrate
from personalclaw.config import loader as config_loader
from personalclaw.service import linux, macos
from personalclaw.service.common import Platform, current_platform
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


def _print_carried(carried: Capture) -> None:
    """Say what the service's environment carries from this shell, and what it left out."""
    for line in summary(carried):
        print(f"   {line}")


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
        print("✅ personalclaw service installed and started.")
        print(f"   unit: {linux.UNIT_PATH}")
        _print_carried(carried)
        print()
        print("   Status: personalclaw service status")
        print("   Logs:   personalclaw logs -f")
        print("   Remove: personalclaw service uninstall")
        return 0
    if plat == Platform.LAUNCHD:
        try:
            carried = macos.install(extra=extra, without=without)
        except macos.ServiceInstallError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        print("✅ personalclaw service installed and started.")
        print(f"   plist: {macos.PLIST_PATH}")
        _print_carried(carried)
        print()
        print("   Status: personalclaw service status")
        print("   Logs:   personalclaw logs -f")
        print("   Remove: personalclaw service uninstall")
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


def stop_service() -> bool:
    """Stop the platform service if active. Returns True if a service was stopped."""
    plat = current_platform()
    if plat == Platform.SYSTEMD:
        if linux.is_active():
            linux.stop()
            return True
        return False
    if plat == Platform.LAUNCHD:
        if macos.is_active():
            macos.stop()
            return True
        return False
    return False


def restart_service() -> bool:
    """Restart the platform service if installed. Returns True if a service was
    restarted (so the caller knows not to spawn a foreground gateway itself)."""
    plat = current_platform()
    if plat == Platform.SYSTEMD:
        if linux.is_active():
            linux.restart()
            return True
        return False
    if plat == Platform.LAUNCHD:
        if macos.is_active():
            macos.restart()
            return True
        return False
    return False
