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
(``mcp.json`` and the agent configs too, through :func:`atomic_json_write`): one rule at the
chokepoint covers the writers that exist and the ones that do not yet, and no enumeration of
"files that can hold a secret" can drift out of date. The three single-secret files with writers
of their own — ``.local_secret``, ``telemetry_salt`` and an app's ``.app_secret`` — create
theirs 0600. A file too large to write whole — an upload's parts, streamed chunk by chunk — gets
the same mode through :func:`open_streamed`. A file written any other way (a log, a lock, a SQLite
database, a few ``write_text`` caches) keeps the umask mode; the 0700 home it sits in is what
shields it. An explicit mode wider than 0600 for a home path is REFUSED, not honoured — that
refusal is the rail. Outside the home the umask default still applies to these writers: a file
written there is the user's to share.

🔴 **An archive or an export of the user's data is private wherever it is written.** A snapshot
carries the audit log's signing key and every memory; a shard export, a project archive and a
memory export carry the user's records. :func:`private_file` (and :func:`write_private_file`) is
the one writer for those: the bytes go to a temp file beside the destination that is created 0600
— ``mkstemp`` opens it ``O_CREAT|O_EXCL`` at that mode, whatever the umask — and the finished file
is renamed over the destination, so no reader ever sees it partial or readable by anyone else, in
the home, in a folder the user named, or in a temp folder. The folders it makes for one are 0700
(:func:`make_private_dirs`). A writer that opens the destination itself and tightens it afterwards
leaves it readable for as long as the write takes: a snapshot's temp file was ``-rw-r--r--`` for
the minute its 400 MB took. Every archive core writes to disk goes through it, a pack's too (its
owner hands it on by reading it, which 0600 allows), and ``tests/test_archive_writer_census.py``
holds every archive writer and every declared export writer to it.
"""

import json
import logging
import os
import shutil
import stat
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any, BinaryIO, cast

logger = logging.getLogger(__name__)

_umask_lock = threading.Lock()
_default_mode: int | None = None

# ── post-write notifier seam ───────────────────────────────────────────────
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


def ensure_home_for(path: Path | str) -> None:
    """Make the PersonalClaw home, 0700, when ``path`` is inside it: the writer's half of a path.

    Every path in the home is worked out without making anything (``config.loader``), so the first
    write into a home that is not there yet makes it. A folder made inside it with
    ``mkdir(parents=True)`` would make the home as one of its parents, at the default mode; this
    makes it first, 0700 like ``~/.ssh``, as ``config_dir()`` does. Called by a writer just before
    it makes its folder, never by a reader.
    """
    if is_in_home(path):
        from personalclaw.config import loader  # lazy: loader imports this module

        loader.config_dir()


def ensure_private_dir(directory: Path | str) -> None:
    """Create ``directory`` at 0700 if absent, and tighten it to 0700 if it is looser.

    Inside the home, the home is made first (:func:`ensure_home_for`). Tightening is best-effort:
    a directory this process may not chmod (a volume root owned by another uid) is logged and left,
    because refusing the write would lose the user's data over a mode the files inside are
    protected from anyway (they are written 0600).
    """
    d = Path(directory)
    ensure_home_for(d)
    d.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    try:
        if d.stat().st_mode & 0o077:
            os.chmod(d, PRIVATE_DIR_MODE)
    except OSError:
        logger.warning("could not make %s private (0700)", d)


def is_in_home(path: Path | str) -> bool:
    """Whether ``path`` lies inside the PersonalClaw home this process writes into.

    Asks ``config.loader.resolve_config_dir`` — the one resolver of the home, patched per test by
    the suite's isolation fixture — at call time, never at import. Where the home is, not the home
    made: every atomic write asks this, and one outside the home (a build stamp in the checkout)
    must not create a home by asking. ``False`` when the home cannot be resolved at all, which
    leaves the write at the pre-existing umask default rather than guessing.
    """
    try:
        from personalclaw.config import loader  # lazy: loader imports this module

        home = os.path.abspath(str(loader.resolve_config_dir()))
    except Exception:  # noqa: BLE001 — an unresolvable home must not fail an unrelated write
        return False
    target = os.path.abspath(str(path))
    return target == home or target.startswith(home + os.sep)


def private_mode_for(path: Path | str, requested: int | None = None) -> int | None:
    """The mode a write to ``path`` must use, or ``None`` when the home rule does not apply.

    Under the home: ``requested`` if it grants no group/other bit, else :class:`ValueError`;
    :data:`PRIVATE_FILE_MODE` when nothing was requested.
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


#: The open flags :func:`open_streamed` takes for each mode it serves.
_STREAMED_FLAGS: dict[str, int] = {
    "wb": os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
    "ab": os.O_WRONLY | os.O_CREAT | os.O_APPEND,
    "r+b": os.O_RDWR,
}


def open_streamed(path: Path | str, mode: str = "wb") -> Any:
    """Open *path* for a write that streams its content chunk by chunk, too large to hold whole
    for :func:`atomic_write_bytes` (an upload's parts and the file they are assembled into), or
    that adds to what the file holds without rewriting it (the lines a sync appends to a one-file
    append-only store, ``durability.writeback``).

    The file gets the mode the shared writer gives one there: 0600 in a 0700 directory under the
    home, set on the open descriptor, so a file that existed at a looser mode is tightened too;
    elsewhere the umask's, as ``open`` gives it. Not atomic and not announced: the caller owns what
    a half-written file means (an upload's part is written again on a resume). *mode* is ``"wb"``,
    ``"ab"`` or ``"r+b"``.
    """
    flags = _STREAMED_FLAGS.get(mode)
    if flags is None:
        raise ValueError(
            f"open_streamed writes bytes: mode {mode!r} is not one of {_STREAMED_FLAGS}"
        )
    path = Path(path)
    home_mode = private_mode_for(path)
    if home_mode is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        return open(path, mode)  # noqa: SIM115 — the caller's `with` closes it
    ensure_private_dir(path.parent)
    fd = os.open(str(path), flags | getattr(os, "O_CLOEXEC", 0), home_mode)
    try:
        os.fchmod(fd, home_mode)
        return os.fdopen(fd, mode)
    except BaseException:
        os.close(fd)
        raise


def atomic_json_write(path: Path | str, data: Any) -> None:
    """Write *data* as indented JSON with a trailing newline, atomically. The one JSON writer.

    Under the PersonalClaw home the file is 0600 in a 0700 directory, like every home write.
    Anywhere else the file belongs to another program (an ACP CLI's agent config, another
    tool's MCP file), so a rewrite keeps the mode it already has — 0644 for a new file —
    instead of changing who can read it. A rename the filesystem refuses (a single file
    bind-mounted into a container answers ``EBUSY``) falls back to copying the new content
    over the old, so the write still lands.
    """
    path = Path(path)
    mode: int | None = None
    if not is_in_home(path):
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except FileNotFoundError:
            mode = 0o644
    content = json.dumps(data, indent=2) + "\n"
    _atomic_write(path, content, text=True, fsync=False, mode=mode, replace_fallback=True)


def _atomic_write(
    path: Path | str,
    payload: "str | bytes",
    *,
    text: bool,
    fsync: bool,
    mode: int | None,
    replace_fallback: bool = False,
) -> None:
    path = Path(path)
    home_mode = private_mode_for(path, mode)  # raises BEFORE any byte lands
    if home_mode is None:
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        ensure_private_dir(path.parent)
        mode = home_mode
    with _replacing(
        path,
        mode if mode is not None else _get_default_mode(),
        text=text,
        fsync=fsync,
        replace_fallback=replace_fallback,
    ) as f:
        f.write(payload)


def make_private_dirs(directory: Path | str) -> None:
    """Make ``directory`` and each missing folder above it 0700, whatever the umask.

    For the folders an archive or an export is written into (:func:`private_file`). A folder that
    is already there keeps the mode it has — one the user named and made is theirs — except under
    the home, where every folder a file is written into is 0700 (:func:`ensure_private_dir`).
    ``Path.mkdir(parents=True)`` would make every folder above the last at the umask's mode.
    """
    d = Path(directory)
    ensure_home_for(d)
    missing: list[Path] = []
    probe = d
    while not probe.exists() and probe != probe.parent:
        missing.append(probe)
        probe = probe.parent
    for folder in reversed(missing):
        try:
            folder.mkdir(mode=PRIVATE_DIR_MODE)
        except FileExistsError:
            if not folder.is_dir():
                raise
            continue  # another writer made it a moment ago, at the mode it chose
        # The umask can only take bits away from 0700, never add one; this pins it exact.
        os.chmod(folder, PRIVATE_DIR_MODE)
    if is_in_home(d):
        ensure_private_dir(d)


@contextmanager
def private_file(path: Path | str, *, fsync: bool = True) -> Iterator[BinaryIO]:
    """Stream an archive or an export of the user's data into ``path``: private and atomic.

    Yields a binary file. Its bytes go to ``<name>.<random>.tmp`` beside ``path``, created 0600
    before its first byte, in a folder :func:`make_private_dirs` makes 0700; when the ``with``
    block ends, the file is flushed (and fsynced unless ``fsync`` is false) and renamed over
    ``path``. If the block raises, the temp file is removed and ``path`` is left as it was.
    Inside the home or outside it alike: see the module docstring.
    """
    target = Path(path)
    make_private_dirs(target.parent)
    with _replacing(
        target, PRIVATE_FILE_MODE, text=False, fsync=fsync, prefix=f"{target.name}."
    ) as f:
        yield cast(BinaryIO, f)  # opened "wb"


def write_private_file(path: Path | str, payload: "str | bytes", *, fsync: bool = False) -> None:
    """Write a whole archive or export (a shard, a manifest, an archive built in memory) through
    :func:`private_file`. Text is written as UTF-8."""
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    with private_file(path, fsync=fsync) as f:
        f.write(data)


@contextmanager
def _replacing(
    path: Path,
    mode: int,
    *,
    text: bool,
    fsync: bool,
    prefix: str = "tmp",
    replace_fallback: bool = False,
) -> Iterator[IO[Any]]:
    """The temp file every writer here writes through, renamed over ``path`` when it is done.

    ``mkstemp`` creates it ``O_CREAT|O_EXCL`` at 0600 (narrower under an unusual umask, never
    wider), and its mode is pinned to ``mode`` before the caller writes a byte. A rename the
    filesystem refuses falls back to copying over ``path`` when ``replace_fallback`` is set (see
    :func:`atomic_json_write`). On any failure the temp file is removed. The write is announced
    to the post-write subscribers once it has landed.
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=prefix, suffix=".tmp")
    open_mode = "w" if text else "wb"
    encoding = "utf-8" if text else None
    try:
        with os.fdopen(fd, open_mode, encoding=encoding) as f:
            fd = -1  # fdopen took ownership; prevent double-close
            os.fchmod(f.fileno(), mode)
            yield f
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        try:
            os.replace(tmp, str(path))
        except OSError:
            if not replace_fallback:
                raise
            shutil.copy2(tmp, str(path))
            os.unlink(tmp)
    except BaseException:  # an interrupted write must not leave its temp file behind either
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
