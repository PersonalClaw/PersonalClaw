"""Platform detection and shared service constants."""

import enum
import os
import shutil
import subprocess
import sys

SERVICE_NAME = "personalclaw"  # systemd unit name (without .service)
LAUNCHD_LABEL = "io.personalclaw.gateway"  # launchd Label


#: How much of what sudo, systemctl or launchctl said a failure keeps: the end, where the
#: reason and what to do are.
COMMAND_SAID_CHARS = 600


def command_said(result: "subprocess.CompletedProcess[str]") -> str:
    """What a service command (sudo, systemctl, launchctl) printed, fit for the terminal.

    Its stderr, else its stdout: masked the way every view masks, cut to the end, where the
    reason is (``Unit not found``, ``a terminal is required to read the password``), with its
    line breaks kept for the person reading it and every control character written as a
    visible escape, so the command cannot move the cursor or recolour the terminal it is shown
    in. A status block goes through ``mask_child_output`` whole, the same way.
    """
    from personalclaw.security import mask_child_output

    return mask_child_output(
        result.stderr or result.stdout, limit=COMMAND_SAID_CHARS, tail=True, one_line=False
    )


def personalclaw_bin() -> str:
    """Return the resolved personalclaw executable path, or fall back to sys.argv[0].

    Used by both the systemd unit and the launchd plist as ``ExecStart`` /
    ``ProgramArguments``. Falls back to ``sys.argv[0]`` for development
    installs where ``personalclaw`` isn't on the global PATH.
    """
    found = shutil.which("personalclaw")
    if found:
        return found
    return os.path.realpath(sys.argv[0])


def service_path(home: str) -> str:
    """Build the PATH for the gateway's service environment.

    Snapshots the installer's current ``$PATH`` so subprocesses spawned
    by the gateway (git, agent CLIs, etc.) resolve the same way they did
    in the interactive shell that ran ``personalclaw service install``.
    Always-required user dirs (``~/.local/bin``) and POSIX defaults are
    prepended in case the installer's ``$PATH`` is missing them.
    Duplicates are removed while preserving order.
    """
    required = [
        f"{home}/.local/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    env_path = [p for p in os.environ.get("PATH", "").split(":") if p]
    seen: set[str] = set()
    out: list[str] = []
    for entry in required + env_path:
        if entry not in seen:
            seen.add(entry)
            out.append(entry)
    return ":".join(out)


class Platform(enum.Enum):
    """Supported service-management platforms."""

    # System-level systemd. Unit lives at /etc/systemd/system/, write
    # and control commands require sudo. The name reflects the privilege
    # model: a user-level (~/.config/systemd/user/) variant doesn't work
    # on some older Linux systemd versions, so we don't ship one.
    SYSTEMD = "systemd"
    LAUNCHD = "launchd"
    # The published container image. Its service manager is the container runtime on the host,
    # which starts, stops and restarts the gateway (the container's own main process) and which
    # nothing inside the container can drive, so every service action here says what the host
    # runs instead (`personalclaw.container_host`).
    CONTAINER = "container"
    UNSUPPORTED = "unsupported"


def current_platform() -> Platform:
    """Return the platform whose service manager we should target.

    The container image → CONTAINER, whatever is on PATH.
    Linux with systemctl on PATH → SYSTEMD.
    macOS with launchctl on PATH → LAUNCHD.
    Anything else → UNSUPPORTED.
    """
    from personalclaw import container_host

    if container_host.in_container():
        return Platform.CONTAINER
    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        return Platform.SYSTEMD
    if sys.platform == "darwin" and shutil.which("launchctl"):
        return Platform.LAUNCHD
    return Platform.UNSUPPORTED
