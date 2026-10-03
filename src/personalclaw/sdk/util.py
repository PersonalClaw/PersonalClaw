"""SDK: the few cross-cutting helpers an app legitimately needs from core.

- ``config_dir()`` — PersonalClaw's home dir (``~/.personalclaw`` or ``PERSONALCLAW_HOME``).
- ``app_data_dir(name)`` — an app's private, persisted data dir (survives updates).
- ``shared_app_data_dir(name)`` — a READ-ONLY handle to another app's data dir, when
  this app holds a consented APE-10 ``storageRead`` grant on it (else ``None``).
- ``sandbox_wrap_argv(argv, mode)`` — wrap a command in the host sandbox (an app that
  shells out runs under the same confinement core does). It raises, with the sentence to show,
  where that sandbox cannot be applied (the desktop app on a Linux host); the command then must
  not run.
- ``atomic_write(path, data)`` — crash-safe file write (an app persisting config/state
  uses the same durable write core does).
- ``single_flight(key)`` — the host's cross-process/cross-thread "only one of us does this"
  lock. Exposed because an app that downloads a large artifact into the SHARED home has
  exactly the problem it solves: two gateways started against one home would otherwise both
  pull the same 138 MiB file. Rolling a lockfile per app would be N implementations of one
  invariant in a directory they all share, and the failure they would trade for is a
  half-written artifact a later run treats as complete.

- ``child_process_env(extra, installer=..., ssh_agent=...)`` — the environment for any other child
  process an app's provider starts: a binary it runs, an ``npx`` tool. A provider runs inside the
  gateway, and the gateway's environment holds every secret saved in PersonalClaw, so a child
  started with ``env`` left out inherits all of them. This is the child allowlist (PATH, home,
  locale, proxy and CA settings, and what the owner passed through by name), *extra* over it, with
  ``installer="npm"`` or ``"pip"`` that installer's own settings, and with ``ssh_agent=True`` the
  owner's SSH agent socket and nothing else, for a program that signs in over ssh.
- ``app_packages_env()`` — the environment for a child process that must import the
  packages apps declare (``<home>/app-python``): an app running one of its declared packages
  as ``python -m <package>``. The child allowlist (PATH, home, locale, proxy and CA settings),
  never the gateway's own environment and the secrets in it, with the app packages on
  ``PYTHONPATH`` when there are any.
- ``outside_home_path(place)`` — a READ-ONLY handle to a place outside the PersonalClaw home
  (the machine-wide Hugging Face folder, ``"huggingface-cache"``) once the owner allowed it in
  Settings → Security, else ``None``. An app keeps what it writes in its own data dir.

Keep this surface tiny: an app reaching for more than these is a sign the boundary is
wrong (promote the need to a proper SDK submodule, or vendor it into the app).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from personalclaw.apps.app_python import app_packages_env  # noqa: F401
from personalclaw.apps.manager import app_data_dir, shared_dir_env_name  # noqa: F401
from personalclaw.atomic_write import atomic_write  # noqa: F401
from personalclaw.concurrency import single_flight  # noqa: F401
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


def child_process_env(
    extra: Mapping[str, str] | None = None, *, installer: str = "", ssh_agent: bool = False
) -> dict[str, str]:
    """The environment for a child process an app's provider starts, as ``env=`` to its spawn.

    The child allowlist (``PATH``, the home, locale, proxy and certificate settings, and the names
    the owner passed through in Settings → Security → Child environment passthrough), never a copy
    of the gateway's environment: a provider runs inside the gateway, whose environment holds every
    secret saved in PersonalClaw. *extra* goes over it (a credential-shaped name in it is refused).
    ``installer="npm"`` or ``"pip"`` adds that installer's own settings, for an ``npx`` or ``pip``
    run. ``ssh_agent=True`` adds the owner's SSH agent socket (``SSH_AUTH_SOCK``) and nothing else,
    for a program that signs in over ssh with the owner's keys, such as rsync to their host; no
    other child gets it. A child that must import the packages apps declare wants
    :func:`app_packages_env`, and a git wants ``personalclaw.sdk.git``."""
    from personalclaw.sandbox import build_child_env

    return build_child_env(
        site="app-child", extra=dict(extra or {}), installer=installer, ssh_agent=ssh_agent
    )


def outside_home_path(place: str) -> Path | None:
    """A READ-ONLY handle to the place outside the home named ``place``, or ``None``.

    The places are declared in core (``personalclaw.outside_home``); the one an app has use for
    is ``"huggingface-cache"``, the Hugging Face folder other tools share (``$HF_HOME``, or
    ``~/.cache/huggingface``). Each is off until the owner allows it in Settings → Security →
    Outside PersonalClaw's home, so ``None`` is the usual answer: the app then uses only what it
    keeps in :func:`app_data_dir`. The returned path, and any child, refuses writes with
    ``PermissionError``; deleting or downloading there is never the app's to do."""
    from personalclaw import outside_home

    found = outside_home.place_path(place)
    return _ReadOnlyPath(found) if found is not None else None


__all__ = [
    "config_dir",
    "app_data_dir",
    "shared_app_data_dir",
    "sandbox_wrap_argv",
    "atomic_write",
    "single_flight",
    "child_process_env",
    "app_packages_env",
    "outside_home_path",
]
