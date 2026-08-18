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

from personalclaw.apps.manager import APP_MANIFEST_FILENAME

logger = logging.getLogger(__name__)

_CLONE_TIMEOUT = 120  # seconds — bounded git clone
_MULTI_APP_PREVIEW = 5  # apps named in the multi-app hint before it says "and N more"


class SourceError(Exception):
    """The install source could not be resolved (bad path / clone failed)."""


@dataclass
class ResolvedSource:
    path: Path
    origin: str  # "local" | "external"
    cleanup: bool  # caller should rmtree(cleanup_root or path) when done
    _cleanup_root: Path | None = None  # when set, rmtree this instead of path (subdir installs)

    @property
    def cleanup_path(self) -> Path:
        """The directory to remove when cleanup=True (the clone root)."""
        return self._cleanup_root or self.path


def _looks_like_git_url(source: str) -> bool:
    s = source.strip()
    return s.startswith(("http://", "https://", "git://", "ssh://", "git@")) or s.endswith(".git")


def _subdir_app_names(root: Path) -> list[str]:
    """The immediate subdirectories of a clone that hold an ``app.json`` — i.e. the
    installable apps of a multi-app repository (the published apps repo's shape)."""
    out: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / APP_MANIFEST_FILENAME).is_file():
            out.append(child.name)
    return out


def _multi_app_hint(url: str, apps: list[str]) -> str:
    """The install error for a multi-app repo pasted WITHOUT a ``#app`` suffix.

    Renders verbatim in the Store, so it names the count, the exact source string the
    user has to type instead, and a bounded preview rather than a 45-name dump."""
    preview = ", ".join(apps[:_MULTI_APP_PREVIEW])
    if len(apps) > _MULTI_APP_PREVIEW:
        preview += f", and {len(apps) - _MULTI_APP_PREVIEW} more"
    return (
        f"{url} holds {len(apps)} apps, not one — install a single app by appending "
        f"#app to the URL, e.g. {url}#{apps[0]}. Available: {preview}."
    )


def resolve(source: str) -> ResolvedSource:
    """Resolve an install source string to a local directory.

    A local directory path resolves in place (no cleanup). A git URL is
    shallow-cloned into a temp dir (caller cleans up). Supports the
    ``url#subdirectory`` format for installing a specific app from a
    multi-app git repo — a multi-app repo given WITHOUT that suffix raises
    with the ``#app`` form and the app names it found. Raises
    :class:`SourceError` on a missing path or a failed clone."""
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
                raise SourceError(f"subdirectory '{subdir}' not found in cloned repo")
            resolved = ResolvedSource(
                path=target,
                origin="external",
                cleanup=True,
                _cleanup_root=resolved.path,
            )
        elif not (resolved.path / APP_MANIFEST_FILENAME).is_file():
            # A multi-app repo (no root manifest, apps in subdirs) pasted as a bare URL.
            # Without this the install dies deep in staging as "no app.json in source",
            # which is true of the ROOT and useless to a user holding a 45-app repo.
            apps = _subdir_app_names(resolved.path)
            if apps:
                _rmtree(resolved.path)
                raise SourceError(_multi_app_hint(base, apps))
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
            capture_output=True,
            text=True,
            timeout=_CLONE_TIMEOUT,
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
