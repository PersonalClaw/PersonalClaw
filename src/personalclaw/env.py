"""Shared environment helpers for subprocess spawning."""

import os

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
