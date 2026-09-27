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

A source a registry LISTING named is not the owner's choice, so it is held to the listing
rules (``apps/catalog.py`` "What a registry listing may name"): its form is checked, its host
is resolved now and refused when it is this computer, a private network or the metadata
service, and the clone runs through ``net/git.run_git_guarded``, which judges every host git
connects to, redirects included. Everything else here is the owner's own act and fetches as
it always has.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personalclaw.apps.manager import APP_MANIFEST_FILENAME

logger = logging.getLogger(__name__)

_CLONE_TIMEOUT = 120  # seconds — bounded git clone
_MULTI_APP_PREVIEW = 5  # apps named in the multi-app hint before it says "and N more"


class SourceError(Exception):
    """The install source could not be resolved (bad path / clone failed)."""


class SourceRefused(SourceError):
    """A registry listing named a download address PersonalClaw will not fetch from for it.
    The message is the sentence the Store shows for the same listing."""


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


def git_pointer(source: str) -> tuple[str, str] | None:
    """``(repository URL, subdirectory)`` of an install source that names a git repository —
    ``url``, or ``url#subdirectory`` for one app of a multi-app repository, the form a Store
    card installs from and ``installed.json`` records — or ``None`` for a local path.

    The one reading of that form: :func:`resolve` clones by it, and the Store's update check
    finds what the same place offers now by it."""
    s = str(source).strip()
    base, subdir = s, ""
    if "#" in s and _looks_like_git_url(s.split("#", 1)[0]):
        base, subdir = s.rsplit("#", 1)
    if not _looks_like_git_url(base):
        return None
    return base, subdir.strip("/")


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


def resolve(source: str, *, listed_by: str = "") -> ResolvedSource:
    """Resolve an install source string to a local directory.

    A local directory path resolves in place (no cleanup). A git URL is
    shallow-cloned into a temp dir (caller cleans up). Supports the
    ``url#subdirectory`` format for installing a specific app from a
    multi-app git repo — a multi-app repo given WITHOUT that suffix raises
    with the ``#app`` form and the app names it found. Raises
    :class:`SourceError` on a missing path or a failed clone.

    ``listed_by`` is the registry whose listing named *source*, as a Store card sends it. A
    source is also a listing's when an index this process read names it. Either way it is
    fetched under the listing rules, and :class:`SourceRefused` says why when it may not be."""
    s = str(source).strip()
    if not s:
        raise SourceError("empty install source")

    # A git repository, with an optional #subdirectory (one app of a multi-app repo).
    pointer = git_pointer(s)
    policy = _listing_fetch_policy(s, pointer[0] if pointer else s, listed_by)

    if pointer is not None:
        base, subdir = pointer
        resolved = _clone_git(base, policy=policy)
        if subdir:
            target = resolved.path / subdir
            # The `#subdirectory` is text a registry index supplies verbatim, so it is
            # untrusted: `../` or a link in the clone must not make a folder BESIDE the
            # clone the bundle that gets staged.
            if not _within(resolved.path, target):
                _rmtree(resolved.path)
                raise SourceError(f"subdirectory {subdir!r} leads outside the cloned repo")
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


def _listing_fetch_policy(source: str, base: str, listed_by: str) -> Any:
    """The egress policy to clone *base* under when a registry listing named *source*, or
    ``None`` when *source* is the owner's own. Raises :class:`SourceRefused` for a listing
    that may not be fetched at all, before anything is fetched."""
    from personalclaw.apps import catalog
    from personalclaw.net.git import preflight

    registry = listed_by.strip() or catalog.listing_source_for(source)
    if registry is None:
        return None
    refused = catalog.listing_repo_refusal(base)
    if refused:
        raise SourceRefused(refused)
    policy = catalog.listing_policy(registry)
    decision = preflight(base, policy)
    if decision.allow:
        return policy
    if decision.category == "unresolvable":
        raise SourceError(catalog.listing_unreachable(decision.host, "it does not resolve"))
    raise SourceRefused(catalog.listing_address_refusal(decision))


def _clone_git(url: str, *, policy: Any = None) -> ResolvedSource:
    """Shallow-clone *url*. With a ``policy`` (a listing's fetch), git runs through the egress
    guard's tunnel; without one (the owner's own URL), it runs as a plain ``git clone``."""
    from personalclaw.net.git import GitEgressRefused, GitHostUnreachable, run_git_guarded

    tmp = Path(tempfile.mkdtemp(prefix="pclaw-app-clone-"))
    clone = ["clone", "--depth", "1", "--", url, str(tmp)]
    try:
        if policy is None:
            proc = subprocess.run(
                ["git", *clone], capture_output=True, text=True, timeout=_CLONE_TIMEOUT
            )
        else:
            proc = run_git_guarded(clone, policy=policy, timeout=_CLONE_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        _rmtree(tmp)
        raise SourceError(f"git clone timed out after {_CLONE_TIMEOUT}s") from exc
    except FileNotFoundError as exc:
        _rmtree(tmp)
        raise SourceError("git is not available to clone the app source") from exc
    except GitEgressRefused as exc:
        _rmtree(tmp)
        from personalclaw.apps.catalog import listing_fetch_refusal

        raise SourceRefused(listing_fetch_refusal(url, exc.refusal)) from exc
    except GitHostUnreachable as exc:
        _rmtree(tmp)
        from personalclaw.apps.catalog import listing_unreachable

        raise SourceError(listing_unreachable(exc.host, exc.reason)) from exc
    if proc.returncode != 0:
        _rmtree(tmp)
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise SourceError(f"git clone failed: {tail}")
    # The clone keeps its `.git`: staging leaves version-control metadata out of every
    # bundle, at any depth (`supply_chain.never_installed`), so it is never scanned,
    # digested or installed, and the caller's cleanup removes it with the clone.
    return ResolvedSource(path=tmp, origin="external", cleanup=True)


def _within(root: Path, target: Path) -> bool:
    """Whether ``target``, resolved through every link, is ``root`` or inside it."""
    real_root = os.path.realpath(root)
    return os.path.commonpath([real_root, os.path.realpath(target)]) == real_root


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
