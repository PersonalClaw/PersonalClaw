"""One writer at a time for ``config.json``, across every process that shares the home.

The gateway, the CLI, the setup wizard and a second terminal all write ``config.json``. Each
write was atomic (a temp file renamed over the old one), so the file was never half-written, but
nothing stopped two writers from reading the same document and each writing back its own copy:
the second write put the first one's change back. The dashboard's handlers held an
``asyncio.Lock``, which one process can see and no other can.

:func:`mutate_config` is the one read-modify-write. It takes an OS lock beside the file
(``config.json.lock``), reads the document UNDER the lock, lets the caller change it, stores any
secret the change carries in the credential store (``secret_refs.store_config_secrets``), and
replaces the file before letting go. Every writer takes the same lock, so no read can predate a
write it has not seen:

* ``AppConfig.save()`` applies what the object changed since it was loaded
  (``loader.PendingConfigChanges``), so a stale object only writes its own change;
* :func:`update_config` loads a fresh ``AppConfig`` under the lock for a change that has to
  check the current state first (an agent name that must not exist yet);
* the handlers and CLI paths that edit the raw document call :func:`mutate_config` directly.

``fcntl.flock``, the established primitive here (``concurrency.py``): the operating system
drops it when the holding process exits, so a crash cannot leave the file locked, and it is
per open file, so two threads of one process exclude each other as two processes do. A
per-path ``threading.Lock`` queues a process's own threads in front of it, so one of them polls
at a time. A writer waits a bounded time and then refuses (:class:`ConfigLockTimeout`) rather
than hanging a request; a transaction opened inside another on the same thread refuses at once
(:class:`NestedConfigTransaction`) rather than waiting for itself. A refused write changes
nothing.

Whole-home restores (a snapshot restore, an import into an empty home, a time-travel rollback)
replace the file wholesale through their own mechanisms and do not take this lock.
"""

from __future__ import annotations

import asyncio
import copy
import errno
import fcntl
import json
import os
import stat
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from personalclaw.config import loader as config_loader
from personalclaw.config.loader import ConfigPreserveError, ConfigWriteError

if TYPE_CHECKING:
    from personalclaw.config.loader import AppConfig

__all__ = [
    "DEFAULT_TIMEOUT_SECS",
    "ConfigChangedError",
    "ConfigLockTimeout",
    "ConfigPreserveError",
    "ConfigWriteError",
    "NestedConfigTransaction",
    "lock_path_for",
    "mutate_config",
    "mutate_config_async",
    "replace_unreadable_config",
    "update_config",
    "update_config_async",
]

#: How long a writer waits for another one before it refuses. A config write holds the lock for
#: milliseconds; five seconds is a writer that is stuck, not one that is busy.
DEFAULT_TIMEOUT_SECS = 5.0
_POLL_SECS = 0.02

T = TypeVar("T")


class ConfigLockTimeout(ConfigWriteError):
    """Another writer held the config lock for longer than this one would wait."""


class NestedConfigTransaction(ConfigWriteError):
    """A config transaction was opened inside another one on the same thread."""


class ConfigChangedError(ConfigWriteError):
    """The file changed after it was read for an edit, so the edit was not written over it."""


def lock_path_for(path: Path) -> Path:
    """The lock file beside *path* (``config.json.lock``). It holds nothing; it is the lock."""
    return path.with_name(f"{path.name}.lock")


_held = threading.local()
_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def _held_here() -> set[str]:
    held: set[str] | None = getattr(_held, "paths", None)
    if held is None:
        held = set()
        _held.paths = held
    return held


class _ConfigLock:
    """The cross-process lock for one config file, waited for at most ``timeout`` seconds."""

    def __init__(self, path: Path, timeout: float) -> None:
        self._lock_file = lock_path_for(path)
        self._key = os.path.realpath(self._lock_file)
        self._timeout = max(0.0, float(timeout))
        self._fd = -1
        with _thread_locks_guard:
            self._thread_lock = _thread_locks.setdefault(self._key, threading.Lock())
        self._thread_lock_held = False

    def __enter__(self) -> _ConfigLock:
        held = _held_here()
        if self._key in held:
            raise NestedConfigTransaction(
                f"a config transaction was opened inside another one on the same thread "
                f"({self._lock_file.parent / self._lock_file.stem}); nothing was written — make "
                f"the whole change in the one transaction"
            )
        deadline = time.monotonic() + self._timeout
        if not self._thread_lock.acquire(timeout=self._timeout):
            raise self._timed_out()
        self._thread_lock_held = True
        try:
            self._fd = self._open()
            while True:
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EACCES):
                        raise ConfigWriteError(
                            f"could not lock {self._lock_file}: {exc}; nothing was written"
                        ) from exc
                    if time.monotonic() >= deadline:
                        raise self._timed_out() from exc
                    time.sleep(_POLL_SECS)
        except BaseException:
            self._release()
            raise
        held.add(self._key)
        return self

    def __exit__(self, *_exc: object) -> None:
        _held_here().discard(self._key)
        self._release()

    def _open(self) -> int:
        from personalclaw.atomic_write import ensure_private_dir, is_in_home

        parent = self._lock_file.parent
        if is_in_home(parent):
            ensure_private_dir(parent)
        else:
            parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(self._lock_file, flags, 0o600)
        except OSError as exc:
            raise ConfigWriteError(
                f"could not open the config lock {self._lock_file}: {exc}; nothing was written"
            ) from exc
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise ConfigWriteError(
                f"{self._lock_file} is not a regular file, so it cannot be the config lock; "
                f"nothing was written"
            )
        os.fchmod(fd, 0o600)
        return fd

    def _timed_out(self) -> ConfigLockTimeout:
        return ConfigLockTimeout(
            f"waited {self._timeout:g}s for another PersonalClaw process to finish writing "
            f"{self._lock_file.parent / self._lock_file.stem}; nothing was written — try again"
        )

    def _release(self) -> None:
        if self._fd >= 0:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = -1
        if self._thread_lock_held:
            self._thread_lock_held = False
            self._thread_lock.release()


def _write(path: Path, document: dict[str, Any], previous: dict[str, Any] | None) -> None:
    """Store the secrets *document* carries and replace *path* with it. Under the lock."""
    from personalclaw.atomic_write import atomic_write
    from personalclaw.config.secret_refs import store_config_secrets

    # In place: a mutator that kept a reference to its document reads back what was written.
    store_config_secrets(document, previous=previous)
    # 0600 wherever the file lives: it holds references to credentials, and a value typed into
    # it by hand stays plaintext until the next boot moves it.
    atomic_write(path, json.dumps(document, indent=2) + "\n", fsync=True, mode=0o600)


def mutate_config(
    mutator: Callable[[dict[str, Any]], T],
    *,
    path: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECS,
    on_written: Callable[[], None] | None = None,
) -> T:
    """Change ``config.json`` with *mutator*, as the only writer, and return what it returned.

    *mutator* gets the document as it is on disk now (``{}`` when there is no file, or an empty
    one) and changes it in place. When it changed nothing, nothing is written. Otherwise its
    secrets go to the credential store and the file is replaced, before the lock is released.
    An exception from *mutator* writes nothing and propagates. *path* is the file to write (the
    home's ``config.json`` by default) — a caller that resolves the path through its own seam
    passes it. *on_written* runs after the file is replaced and before the lock is released:
    for a step that must follow the write and precede every other writer, such as deleting the
    stored key of a provider this write removed (a create of the same name, waiting for the
    lock, stores its key only after that).

    Raises :class:`ConfigPreserveError` when the file exists and cannot be read (writing over it
    would destroy the blocks it holds), :class:`ConfigLockTimeout` when another writer held the
    lock past *timeout*, and :class:`NestedConfigTransaction` when called inside another
    transaction on the same thread — all :class:`ConfigWriteError`, and none writes anything.
    """
    target = path if path is not None else config_loader.config_path()
    with _ConfigLock(target, timeout):
        on_disk = config_loader.read_config_for_merge(target)
        document = copy.deepcopy(on_disk)
        result = mutator(document)
        if document != on_disk:
            _write(target, document, on_disk)
            if on_written is not None:
                on_written()
        return result


async def mutate_config_async(
    mutator: Callable[[dict[str, Any]], T],
    *,
    path: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECS,
    on_written: Callable[[], None] | None = None,
) -> T:
    """:func:`mutate_config` on a worker thread, so waiting for the lock never blocks the loop."""
    return await asyncio.to_thread(
        mutate_config, mutator, path=path, timeout=timeout, on_written=on_written
    )


def update_config(change: Callable[[AppConfig], T], *, timeout: float = DEFAULT_TIMEOUT_SECS) -> T:
    """Change the config through a FRESH ``AppConfig`` loaded under the lock, and save it.

    For a change that must check the current state before it acts — "does this agent exist
    yet?" — because the check and the write are then one step no other writer can come
    between. *change* may raise to refuse, and a refusal writes nothing. A change that leaves
    the config as it read writes nothing either, even when the load migrated something in memory:
    persisting a migration is the boot path's decision, not a side effect of a no-op.
    """

    def apply(document: dict[str, Any]) -> T:
        cfg = config_loader.AppConfig.load()
        as_read = json.dumps(cfg.to_dict(), sort_keys=True)
        result = change(cfg)
        if json.dumps(cfg.to_dict(), sort_keys=True) != as_read:
            config_loader.PendingConfigChanges(cfg).apply(document)
        return result

    return mutate_config(apply, timeout=timeout)


async def update_config_async(
    change: Callable[[AppConfig], T], *, timeout: float = DEFAULT_TIMEOUT_SECS
) -> T:
    """:func:`update_config` on a worker thread."""
    return await asyncio.to_thread(update_config, change, timeout=timeout)


def replace_unreadable_config(
    expected: bytes,
    document: dict[str, Any],
    *,
    path: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECS,
) -> None:
    """Write *document* over a ``config.json`` that could not be read — the repair.

    The one write that replaces a file :func:`mutate_config` refuses, and only when the file
    still holds *expected*, the bytes the repair was made from: a file another writer replaced
    since then is not the one the user repaired (:class:`ConfigChangedError`).
    """
    target = path if path is not None else config_loader.config_path()
    with _ConfigLock(target, timeout):
        try:
            current = target.read_bytes()
        except FileNotFoundError:
            current = None
        if current != expected:
            raise ConfigChangedError(
                f"{target} changed while it was being repaired; nothing was written — "
                f"run the edit again"
            )
        _write(target, document, None)
