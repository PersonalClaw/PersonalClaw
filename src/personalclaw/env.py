"""Shared environment helpers for subprocess spawning."""

import os

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
