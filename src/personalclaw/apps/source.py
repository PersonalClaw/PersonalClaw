"""Install-source resolution — local path or git URL → a local directory.

``install``/``update`` (A1/A2) operate on a local source directory. The REST API
(A4) accepts two source kinds:

* **local path** — a directory already on disk (dev installs, bundled fixtures).
* **git URL** — ``https://…``, ``git@…``, or a ``.git`` URL — shallow-cloned into a
  temp dir the caller is responsible for cleaning up.

This module turns either into a directory + a derived ``origin`` for the scanner
trust tier (``local`` for a path, ``external`` for a remote clone). The clone is
bounded (``--depth 1`` + timeout) and never runs hooks — that's the lifecycle's
job, behind the scanner gate.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_CLONE_TIMEOUT = 120  # seconds — bounded git clone


class SourceError(Exception):
    """The install source could not be resolved (bad path / clone failed)."""


@dataclass
class ResolvedSource:
    path: Path
    origin: str          # "local" | "external"
    cleanup: bool        # caller should rmtree(cleanup_root or path) when done
    _cleanup_root: Path | None = None  # when set, rmtree this instead of path (subdir installs)

    @property
    def cleanup_path(self) -> Path:
        """The directory to remove when cleanup=True (the clone root)."""
        return self._cleanup_root or self.path


def _looks_like_git_url(source: str) -> bool:
    s = source.strip()
    return (
        s.startswith(("http://", "https://", "git://", "ssh://", "git@"))
        or s.endswith(".git")
    )


def resolve(source: str) -> ResolvedSource:
    """Resolve an install source string to a local directory.

    A local directory path resolves in place (no cleanup). A git URL is
    shallow-cloned into a temp dir (caller cleans up). Supports the
    ``url#subdirectory`` format for installing a specific app from a
    multi-app git repo. Raises :class:`SourceError` on a missing path or
    a failed clone."""
    s = str(source).strip()
    if not s:
        raise SourceError("empty install source")

    # Parse optional #subdirectory suffix (multi-app git repos).
    subdir: str | None = None
    base = s
    if "#" in s and _looks_like_git_url(s.split("#", 1)[0]):
        base, subdir = s.rsplit("#", 1)
        subdir = subdir.strip("/") or None

    if _looks_like_git_url(base):
        resolved = _clone_git(base)
        if subdir:
            target = resolved.path / subdir
            if not target.is_dir():
                _rmtree(resolved.path)
                raise SourceError(
                    f"subdirectory '{subdir}' not found in cloned repo"
                )
            resolved = ResolvedSource(
                path=target, origin="external", cleanup=True,
                _cleanup_root=resolved.path,
            )
        return resolved

    path = Path(s).expanduser()
    if not path.is_dir():
        raise SourceError(f"source is not a directory: {source}")
    return ResolvedSource(path=path, origin="local", cleanup=False)


def _clone_git(url: str) -> ResolvedSource:
    tmp = Path(tempfile.mkdtemp(prefix="pclaw-app-clone-"))
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--", url, str(tmp)],
            capture_output=True, text=True, timeout=_CLONE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        _rmtree(tmp)
        raise SourceError(f"git clone timed out after {_CLONE_TIMEOUT}s") from exc
    except FileNotFoundError as exc:
        _rmtree(tmp)
        raise SourceError("git is not available to clone the app source") from exc
    if proc.returncode != 0:
        _rmtree(tmp)
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise SourceError(f"git clone failed: {tail}")
    # Drop the VCS metadata — it's not app content (and shouldn't ship into the
    # installed tree). The scanner skips .git too, but removing it keeps the
    # staged/installed copy clean.
    _rmtree(tmp / ".git")
    return ResolvedSource(path=tmp, origin="external", cleanup=True)


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
