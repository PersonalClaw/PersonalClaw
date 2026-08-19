"""Atomic file write using unique temp filenames to avoid race conditions.

All atomic-write sites in PersonalClaw should use this helper instead of
deterministic ``.tmp`` filenames, which cause ENOENT when concurrent
writers target the same file.
"""

import logging
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

_umask_lock = threading.Lock()
_default_mode: int | None = None

# ── post-write notifier seam (DURABILITY-AND-SYNC §5) ──────────────────────
#
# Every JSON store in PersonalClaw funnels its writes through `_atomic_write`,
# which makes this the ONE callsite where "state just changed on disk" is
# knowable without teaching thirty stores to announce themselves. Time-travel's
# adaptive-debounce committer subscribes here; nothing else may assume it is the
# only subscriber.
#
# Two invariants a hook must never break:
#   * a failing hook MUST NOT fail the write — the write already succeeded when
#     the notifier runs, and losing the user's data because a history commit
#     hiccuped would invert the whole point of this subsystem;
#   * a hook that itself calls `atomic_write` MUST NOT recurse — the thread-local
#     guard below drops the nested notification instead of looping.
_post_write_hooks: list[Callable[[Path], object]] = []
_hooks_lock = threading.Lock()
_notifying = threading.local()


def register_post_write_hook(hook: Callable[[Path], object]) -> None:
    """Subscribe *hook* to every successful atomic write (called with the path).

    The return type is ``object``, not ``None``: a subscriber's own signature is its
    business (time-travel's returns whether the path was tracked), and demanding
    ``None`` would force every subscriber to wrap itself in a discarding lambda —
    which is also how a hook stops being findable by name in a test.

    Idempotent: registering the same callable twice subscribes it once, so a
    module that re-runs its wiring (a gateway restart inside one process, a test
    fixture) cannot double-fire.
    """
    with _hooks_lock:
        if hook not in _post_write_hooks:
            _post_write_hooks.append(hook)


def unregister_post_write_hook(hook: Callable[[Path], object]) -> None:
    """Unsubscribe *hook*. Unknown hooks are ignored (teardown is idempotent)."""
    with _hooks_lock:
        try:
            _post_write_hooks.remove(hook)
        except ValueError:
            pass


def post_write_hooks() -> tuple[Callable[[Path], object], ...]:
    """The current subscribers — for tests and the doctor surface."""
    with _hooks_lock:
        return tuple(_post_write_hooks)


def _notify_post_write(path: Path) -> None:
    with _hooks_lock:
        hooks = tuple(_post_write_hooks)
    if not hooks:
        return
    if getattr(_notifying, "active", False):
        return
    _notifying.active = True
    try:
        for hook in hooks:
            try:
                hook(path)
            except Exception:  # noqa: BLE001 — a hook must never fail a write
                logger.debug("atomic_write: post-write hook failed", exc_info=True)
    finally:
        _notifying.active = False


def _get_default_mode() -> int:
    """Return umask-based default file mode, cached after first call (thread-safe)."""
    global _default_mode
    if _default_mode is None:
        with _umask_lock:
            if _default_mode is None:
                u = os.umask(0)
                os.umask(u)
                _default_mode = 0o666 & ~u
    return _default_mode


def atomic_write(
    path: Path | str,
    content: str,
    *,
    fsync: bool = False,
    mode: int | None = None,
) -> None:
    """Write *content* to *path* atomically via unique temp file + rename.

    Uses ``tempfile.mkstemp`` so concurrent writers never collide on the
    same temp filename.  On error the temp file is cleaned up.

    *mode* sets explicit permissions (e.g. ``0o600`` for secrets).
    ``None`` (default) applies umask-based permissions (matching ``open()``).
    """
    _atomic_write(path, content, text=True, fsync=fsync, mode=mode)


def atomic_write_bytes(
    path: Path | str,
    data: bytes,
    *,
    fsync: bool = False,
    mode: int | None = None,
) -> None:
    """Binary sibling of :func:`atomic_write` — write *data* bytes atomically.

    Same mkstemp+rename guarantee; for binary artifact bodies (images) that must
    not pass through text encoding.
    """
    _atomic_write(path, data, text=False, fsync=fsync, mode=mode)


def _atomic_write(
    path: Path | str,
    payload: "str | bytes",
    *,
    text: bool,
    fsync: bool,
    mode: int | None,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    open_mode = "w" if text else "wb"
    encoding = "utf-8" if text else None
    try:
        with os.fdopen(fd, open_mode, encoding=encoding) as f:
            fd = -1  # fdopen took ownership; prevent double-close
            os.fchmod(f.fileno(), mode if mode is not None else _get_default_mode())
            f.write(payload)
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # Outside the try: the write is DONE and durable here. Notifying inside would
    # route a notifier bug into the cleanup path, which would try to unlink a temp
    # file that no longer exists and re-raise over a successful write.
    _notify_post_write(path)
