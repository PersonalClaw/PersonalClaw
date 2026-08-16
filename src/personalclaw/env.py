"""Shared environment helpers for subprocess spawning and browser reachability."""

import os
import sys

# Path probed to detect the Windows Subsystem for Linux. Module-level so tests
# can redirect it at a fixture file without monkeypatching builtins.
_PROC_VERSION = "/proc/version"


def _is_wsl() -> bool:
    """Return True when running under the Windows Subsystem for Linux.

    Detected by ``microsoft`` appearing in ``/proc/version`` (case-insensitive),
    which both WSL1 and WSL2 kernels report. The read is guarded: on non-Linux
    platforms ``/proc/version`` is absent, so this returns False and never
    raises. Pure and side-effect-free — safe to call from any layer.
    """
    try:
        with open(_PROC_VERSION, encoding="utf-8") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def browser_available() -> bool:
    """Return True when this process can plausibly hand a URL to a browser.

    One predicate, one owner. Two callers ask this question and must agree: the
    gateway decides whether to auto-open the dashboard, and ``personalclaw setup``
    decides whether to point at the dashboard's guided first run. A second copy of
    the heuristic would drift, and the failure is silent either way — a suppressed
    pointer, or an instruction the user cannot follow.

    The case with no browser is a headless REMOTE session: an SSH shell with no
    display server. macOS is exempt because ``open(1)`` reaches the console user's
    browser even from an SSH shell. WSL reports True — it has neither ``$DISPLAY``
    nor the SSH variables — which is correct, because ``wslview`` hands the URL to
    the Windows default browser (see :func:`personalclaw.gateway._open_dashboard`).

    Deliberately NOT the same question as "is this host remote"
    (``dashboard/origin.py``, ``cli_doctor``): a remote host WITH a display can open
    a browser, and those two format a URL differently for a remote user whether or
    not one is available. Pure and side-effect-free.
    """
    if sys.platform == "darwin":
        return True
    is_ssh = bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"))
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return has_display or not is_ssh


# Common directories where MCP server binaries may be installed.
# Order matters — earlier entries take precedence.
_EXTRA_PATH_DIRS = (
    "{home}/.local/bin",
    "{home}/.npm-packages/bin",
    "{home}/.local/share/mise/shims",
)


def augmented_path(base_path: str = "") -> str:
    """Return *base_path* prepended with well-known MCP binary directories.

    When PersonalClaw runs under systemd or another non-login shell the
    inherited ``$PATH`` may not include directories like ``~/.local/bin``.
    This helper prepends standard install locations to keep the PATH
    consistent across login and non-login contexts.
    """
    home = os.path.expanduser("~")
    extra = [d.format(home=home) for d in _EXTRA_PATH_DIRS]
    parts = extra + ([base_path] if base_path else [])
    return os.pathsep.join(parts)
