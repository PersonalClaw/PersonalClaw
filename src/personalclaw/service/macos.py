"""launchd LaunchAgent generation and control for macOS.

The plist lives at ``~/Library/LaunchAgents/io.personalclaw.gateway.plist``
and is loaded via ``launchctl load -w``. The service starts on user login
and is restarted on crash by ``KeepAlive``. Its environment is ``HOME``, the
PATH ``service_path`` builds, and what :mod:`personalclaw.service.environment`
carries from the shell that installed it.
"""

import os
import plistlib
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

from personalclaw.security import mask_child_output
from personalclaw.service.common import (
    LAUNCHD_LABEL,
    command_said,
    personalclaw_bin,
    service_path,
)
from personalclaw.service.environment import Capture, capture

PLIST_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = PLIST_DIR / f"{LAUNCHD_LABEL}.plist"
LOG_DIR = Path.home() / "Library" / "Logs" / "PersonalClaw"
STDOUT_LOG = LOG_DIR / "gateway.log"
STDERR_LOG = LOG_DIR / "gateway.err"


def render_plist(carried: Mapping[str, str] | None = None) -> str:
    """Render the launchd LaunchAgent plist contents, carrying *carried* in its environment.

    Through :mod:`plistlib`, which escapes every value, so a path or a proxy URL with ``&`` or
    ``<`` in it cannot break the document ``launchctl`` loads.
    """
    home = str(Path.home())
    environment = {"HOME": home, "PATH": service_path(home), **dict(carried or {})}
    document = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [personalclaw_bin(), "gateway"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "EnvironmentVariables": environment,
        "StandardOutPath": str(STDOUT_LOG),
        "StandardErrorPath": str(STDERR_LOG),
    }
    return plistlib.dumps(document, sort_keys=False).decode("utf-8")


def installed_environment() -> dict[str, str]:
    """The environment the installed plist starts the gateway in, or ``{}`` with none."""
    try:
        with PLIST_PATH.open("rb") as fh:
            document = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}
    env = document.get("EnvironmentVariables") if isinstance(document, dict) else None
    return {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}


class ServiceInstallError(RuntimeError):
    """Raised when LaunchAgent install can't proceed without manual user action."""


def _write_plist_atomic(contents: str) -> None:
    """Write the plist atomically.

    Writes to a sibling temp file in the same directory, then
    ``os.replace`` to swap into place. ``os.replace`` is atomic on POSIX
    when source and destination are on the same filesystem, so a SIGINT
    or crash mid-write leaves either the old plist or no plist at all —
    never a partial XML document that ``launchctl load`` would reject.
    """
    fd, tmp_path = tempfile.mkstemp(prefix=PLIST_PATH.name + ".", suffix=".tmp", dir=str(PLIST_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(contents)
        os.replace(tmp_path, PLIST_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def _launchctl(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def install(*, extra: Iterable[str] = (), without: Iterable[str] = ()) -> Capture:
    """Write the plist and load+start the agent; return what its environment carries.

    Idempotent — unloads first if already loaded so the new plist takes
    effect without leaving the prior agent stale. *extra* and *without* are
    ``--env`` / ``--no-env`` (:func:`personalclaw.service.environment.capture`).

    Raises :class:`ServiceInstallError` with a human-readable message if
    ``launchctl load`` fails. The CLI catches this and prints the message
    instead of letting a CalledProcessError surface.
    """
    carried = capture(os.environ, extra=extra, without=without)
    PLIST_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if PLIST_PATH.exists():
        _launchctl("unload", "-w", str(PLIST_PATH))
    _write_plist_atomic(render_plist(carried.env))
    load_res = _launchctl("load", "-w", str(PLIST_PATH))
    if load_res.returncode != 0:
        raise ServiceInstallError(
            f"`launchctl load` failed: {command_said(load_res)}\n"
            f"   Plist: {PLIST_PATH}\n"
            f"   Tail the agent logs at {STDOUT_LOG} / {STDERR_LOG} for details."
        )
    return carried


def uninstall() -> None:
    """Unload and remove the plist, and the logs launchd wrote for the agent. Idempotent.

    Install created ``LOG_DIR`` for the agent's output, so it goes with the service unless
    something the service did not write is in it: a folder in the user's Library must not
    outlive the service that made it.
    """
    if PLIST_PATH.exists():
        _launchctl("unload", "-w", str(PLIST_PATH))
        PLIST_PATH.unlink()
    for log in (STDOUT_LOG, STDERR_LOG):
        log.unlink(missing_ok=True)
    try:
        LOG_DIR.rmdir()
    except OSError:
        pass  # already gone, or it holds something that is not the service's


def is_active() -> bool:
    """Return True if launchd reports the agent loaded with a PID."""
    res = _launchctl("list", LAUNCHD_LABEL)
    if res.returncode != 0:
        return False
    # `launchctl list <label>` prints a plist-ish dict with PID = <int>;
    # an unloaded agent returns nonzero. A loaded-but-not-running agent
    # has PID = "-" instead of a number.
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith('"PID"'):
            return "=" in line and line.split("=")[-1].strip().rstrip(";").isdigit()
    return True  # `list <label>` succeeded; treat as active even if PID line absent


def stop() -> None:
    """Stop the running agent.

    ``launchctl stop`` only sends SIGTERM, which the plist's
    ``KeepAlive={SuccessfulExit: false}`` treats as an unsuccessful exit
    and immediately restarts — so a plain ``stop`` is effectively a
    no-op. Use ``unload`` (without ``-w``) so the agent stops and stays
    stopped for the current session, but reloads automatically on next
    login. This mirrors ``systemctl stop`` semantics on Linux.
    """
    if PLIST_PATH.exists():
        _launchctl("unload", str(PLIST_PATH))


def restart() -> None:
    """Restart the agent by reloading the plist.

    ``unload`` then ``load`` (both ``-w``) so the agent stops cleanly and
    starts fresh, mirroring ``systemctl restart`` semantics. A no-op if the
    plist isn't installed.
    """
    if not PLIST_PATH.exists():
        return
    _launchctl("unload", "-w", str(PLIST_PATH))
    _launchctl("load", "-w", str(PLIST_PATH))


def status() -> str:
    """Return a human-readable status block from launchctl."""
    res = _launchctl("list", LAUNCHD_LABEL)
    if res.returncode != 0:
        said = mask_child_output(res.stderr) or "no entry"
        return f"personalclaw service is not loaded ({said})\n"
    return mask_child_output(res.stdout, limit=None, one_line=False) + "\n"
