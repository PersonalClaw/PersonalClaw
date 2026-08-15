"""SDK: the few cross-cutting helpers an app legitimately needs from core.

- ``config_dir()`` — PersonalClaw's home dir (``~/.personalclaw`` or ``PERSONALCLAW_HOME``).
- ``app_data_dir(name)`` — an app's private, persisted data dir (survives updates).
- ``shared_app_data_dir(name)`` — a READ-ONLY handle to another app's data dir, when
  this app holds a consented APE-10 ``storageRead`` grant on it (else ``None``).
- ``sandbox_wrap_argv(argv, mode)`` — wrap a command in the host sandbox (an app that
  shells out runs under the same confinement core does).
- ``atomic_write(path, data)`` — crash-safe file write (an app persisting config/state
  uses the same durable write core does).

Keep this surface tiny: an app reaching for more than these is a sign the boundary is
wrong (promote the need to a proper SDK submodule, or vendor it into the app).
"""

from __future__ import annotations

import os
from pathlib import Path

from personalclaw.apps.manager import app_data_dir, shared_dir_env_name  # noqa: F401
from personalclaw.atomic_write import atomic_write  # noqa: F401
from personalclaw.config.loader import config_dir  # noqa: F401
from personalclaw.sandbox import wrap_argv as sandbox_wrap_argv  # noqa: F401


class _ReadOnlyPath(type(Path())):  # type: ignore[misc]
    """A ``Path`` into ANOTHER app's data dir, handed to a CONSUMER under an APE-10
    ``storageRead`` grant. Read-only is the contract: the consumer is never handed a
    writable handle. Reads pass through unchanged; every mutating operation raises
    ``PermissionError``. Child paths (``shared / "notes.json"``) inherit read-only,
    because pathlib rebuilds children through ``with_segments`` as the same class."""

    _RO_MSG = (
        "shared app data is read-only (APE-10 storageRead grant): another app's data "
        "cannot be written — send it data over the appMessaging broker (APE-9) instead"
    )

    def _readonly(self, *_args: object, **_kwargs: object):
        raise PermissionError(self._RO_MSG)

    def open(self, mode: str = "r", *args: object, **kwargs: object):  # type: ignore[override]
        if any(c in mode for c in "wax+"):
            raise PermissionError(self._RO_MSG)
        return super().open(mode, *args, **kwargs)  # type: ignore[arg-type]

    write_text = _readonly
    write_bytes = _readonly
    mkdir = _readonly
    touch = _readonly
    unlink = _readonly
    rmdir = _readonly
    rename = _readonly
    replace = _readonly
    chmod = _readonly
    symlink_to = _readonly
    hardlink_to = _readonly


def shared_app_data_dir(name: str) -> Path | None:
    """A READ-ONLY handle to app ``name``'s data dir, or ``None`` if not granted (APE-10).

    Returns the dir the gateway mounts for this backend as
    ``PERSONALCLAW_APP_SHARED_DIR_<NAME>`` (``name`` upper-snaked, matching
    ``shared_dir_env_name``) — present ONLY when this app holds a consented, double-
    declared ``storageRead`` grant on ``name`` (this app named it AND ``name`` declared
    ``storageShared``). No grant → no env var → ``None`` (deny by default). The returned
    path (and any child) refuses writes with ``PermissionError``; cross-app writes stay
    broker-only (``appMessaging``, APE-9)."""
    raw = os.environ.get(shared_dir_env_name(name))
    if not raw:
        return None
    return _ReadOnlyPath(raw)


__all__ = [
    "config_dir",
    "app_data_dir",
    "shared_app_data_dir",
    "sandbox_wrap_argv",
    "atomic_write",
]
