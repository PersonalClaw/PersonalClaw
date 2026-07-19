"""Atomic file write using unique temp filenames to avoid race conditions.

All atomic-write sites in PersonalClaw should use this helper instead of
deterministic ``.tmp`` filenames, which cause ENOENT when concurrent
writers target the same file.
"""

import os
import tempfile
import threading
from pathlib import Path

_umask_lock = threading.Lock()
_default_mode: int | None = None


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
