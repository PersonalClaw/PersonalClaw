"""Atomic file write using unique temp filenames to avoid race conditions.

All atomic-write sites in PersonalClaw should use this helper instead of
deterministic ``.tmp`` filenames, which cause ENOENT when concurrent
writers target the same file.

🔴 **The PersonalClaw home is private, and this is where that is enforced.** A file written
under the home is created 0600 and the directory it is written into is made 0700 — the home
itself included, the first time a file lands in it (a new home is created 0700 by
``config.loader.config_dir``, which never re-modes an existing one: it runs on every
resolution, imports included). Before this, every write defaulted to the umask mode, so
``config.json`` — which carried a provider's API key — and an app's ``data/config.json`` —
which carried its channel tokens — were 0644 on disk, world-readable, unless the author of
each of those writers had remembered ``mode=0o600``. Most had not. The rule lives HERE rather
than at each writer because the settings and credential stores funnel through ``_atomic_write``
(``mcp.json``'s own writer asks :func:`private_mode_for` too): one rule at the chokepoint covers
the writers that exist and the ones that do not yet, and no enumeration of "files that can hold
a secret" can drift out of date. The three single-secret files with writers of their own —
``.local_secret``, ``telemetry_salt`` and an app's ``.app_secret`` — create theirs 0600. A file
written any other way (a log, a lock, a SQLite database, a few ``write_text`` caches) keeps the
umask mode; the 0700 home it sits in is what shields it. An explicit mode wider than 0600 for a
home path is REFUSED, not honoured — that refusal is the rail. Outside the home (an export the
user saves into Downloads) the umask default still applies: that file is theirs to share.
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


#: Mode of every file written under the PersonalClaw home.
PRIVATE_FILE_MODE = 0o600
#: Mode of the home and of every directory a home file is written into.
PRIVATE_DIR_MODE = 0o700


def ensure_private_dir(directory: Path | str) -> None:
    """Create ``directory`` at 0700 if absent, and tighten it to 0700 if it is looser.

    Tightening is best-effort: a directory this process may not chmod (a volume root owned by
    another uid) is logged and left, because refusing the write would lose the user's data over
    a mode the files inside are protected from anyway (they are written 0600).
    """
    d = Path(directory)
    d.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    try:
        if d.stat().st_mode & 0o077:
            os.chmod(d, PRIVATE_DIR_MODE)
    except OSError:
        logger.warning("could not make %s private (0700)", d)


def is_in_home(path: Path | str) -> bool:
    """Whether ``path`` lies inside the PersonalClaw home this process writes into.

    Asks ``config.loader.config_dir`` — the one resolver of the home, patched per test by the
    suite's isolation fixture — at call time, never at import. ``False`` when the home cannot be
    resolved at all, which leaves the write at the pre-existing umask default rather than
    guessing.
    """
    try:
        from personalclaw.config import loader  # lazy: loader imports this module

        home = os.path.abspath(str(loader.config_dir()))
    except Exception:  # noqa: BLE001 — an unresolvable home must not fail an unrelated write
        return False
    target = os.path.abspath(str(path))
    return target == home or target.startswith(home + os.sep)


def private_mode_for(path: Path | str, requested: int | None = None) -> int | None:
    """The mode a write to ``path`` must use, or ``None`` when the home rule does not apply.

    Under the home: ``requested`` if it grants no group/other bit, else :class:`ValueError`;
    :data:`PRIVATE_FILE_MODE` when nothing was requested. Shared with
    ``agent._atomic_json_write`` — the ``mcp.json`` writer, which does its own temp-and-rename —
    so the rule has one statement.
    """
    if not is_in_home(path):
        return None
    if requested is None:
        return PRIVATE_FILE_MODE
    if requested & 0o077:
        raise ValueError(
            f"refusing to write {path} at {oct(requested)}: files under the PersonalClaw home "
            f"can hold secrets and are written {oct(PRIVATE_FILE_MODE)}"
        )
    return requested


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

    Under the PersonalClaw home the file is 0600 in a 0700 directory, and a *mode* that
    grants a group/other bit raises :class:`ValueError` (see the module docstring).
    Elsewhere *mode* sets explicit permissions, and ``None`` (default) applies the
    umask-based mode (matching ``open()``).
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
    home_mode = private_mode_for(path, mode)  # raises BEFORE any byte lands
    if home_mode is None:
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        ensure_private_dir(path.parent)
        mode = home_mode
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
