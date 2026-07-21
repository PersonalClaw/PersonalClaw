"""Install-kind detection (plan 34 S4, contract C1).

Self-update behaves differently per how PersonalClaw was installed, so the
updater first asks *what kind of install is this?* — a git checkout (contributor
/ dev), a pip/uv/pipx install (a wheel in ``sys.prefix``), a container image, or
the desktop shell. The check + apply surfaces (updates.py) branch on this.

Resolution order (first hit wins), per C1::

    env PERSONALCLAW_INSTALL_KIND in {"container","desktop"}  -> that
        (baked into the Dockerfiles; set by the Electron shell, plan 45)
    a resolvable project dir that contains a .git directory     -> "git"
    else                                                        -> "pip"

Pre-1.0 clean break (owner 2026-07-20): implemented directly, WITHOUT a
lifecycle gate — there is no lifecycle/gates.py machinery yet, so this is the
one behavior, not a gated alternative to the old git-only path.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

InstallKind = Literal["git", "pip", "container", "desktop"]

_ENV_KINDS: frozenset[str] = frozenset({"container", "desktop"})

# GitHub releases are the release truth (tags), not `main`. Unauthenticated:
# 60 req/hr/IP is ample for a personal gateway that checks <= hourly + ETag'd.
_RELEASES_LATEST_URL = "https://api.github.com/repos/PersonalClaw/PersonalClaw/releases/latest"
_CACHE_FILENAME = "update_check.json"
_HTTP_TIMEOUT_S = 10.0

# apply_method per kind (C2 wire shape).
_APPLY_METHOD: dict[str, str] = {
    "git": "pipeline",
    "pip": "pip_upgrade",
    "container": "instructions",
    "desktop": "desktop_delegate",
}


def _project_dir() -> str:
    """The resolved source-tree dir, or "" — mirrors updates.py's probe.

    ``PERSONALCLAW_PROJECT_DIR`` is set at startup by ``cli._detect_project_dir``
    when the gateway runs from a checkout (it finds ``agents/`` + ``skills/``
    walking up from CWD, or a saved path). A wheel/container/desktop install has
    no such tree, so the env is unset.
    """
    return os.environ.get("PERSONALCLAW_PROJECT_DIR", "") or ""


def _has_git_dir(proj: str) -> bool:
    """True when *proj* (or its monorepo parent) is a git working tree.

    A ``.git`` entry is normally a directory, but in a git *worktree* or a
    submodule it is a file pointing at the real gitdir — accept either.
    """
    if not proj:
        return False
    root = Path(proj)
    # The project dir may be the repo root, or nested one level under it
    # (monorepo layout — see updates._package_root). Check both.
    for cand in (root, root.parent):
        if (cand / ".git").exists():
            return True
    return False


def detect_install_kind() -> InstallKind:
    """Classify the running install as git / pip / container / desktop (C1)."""
    env_kind = (os.environ.get("PERSONALCLAW_INSTALL_KIND") or "").strip().lower()
    if env_kind in _ENV_KINDS:
        return env_kind  # type: ignore[return-value]
    if _has_git_dir(_project_dir()):
        return "git"
    return "pip"


# ── Tag-driven update check (T4.2, contract C2) ─────────────────────────────


def _normalize_version(v: str) -> str:
    """Strip a leading ``v`` from a release tag so ``v0.1.3`` == ``0.1.3``."""
    v = (v or "").strip()
    return v[1:] if v[:1] == "v" else v


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse a dotted version to a tuple for numeric comparison (best-effort)."""
    core = _normalize_version(v).split("+", 1)[0].split("-", 1)[0]
    try:
        return tuple(int(x) for x in core.split("."))
    except (ValueError, AttributeError):
        return (0,)


def _cache_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _CACHE_FILENAME


def _read_cache() -> dict[str, object]:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache(data: dict[str, object]) -> None:
    from personalclaw.atomic_write import atomic_write

    try:
        atomic_write(_cache_path(), json.dumps(data, indent=2) + "\n", fsync=True)
    except Exception:
        logger.debug("could not persist update-check cache", exc_info=True)


async def fetch_latest_release() -> dict[str, object]:
    """Return the latest GitHub release view, ETag-cached and offline-tolerant.

    Sends ``If-None-Match`` with the cached ETag: a 304 (or any network error)
    returns the cached view unchanged; a 200 refreshes and re-caches. The
    returned dict has ``{tag, name, body, etag, checked_at}`` (empty ``tag`` when
    nothing has ever been fetched and we're offline).
    """
    import aiohttp

    cache = _read_cache()
    etag = str(cache.get("etag") or "")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "personalclaw-update-check",
    }
    if etag:
        headers["If-None-Match"] = etag

    try:
        timeout = aiohttp.ClientTimeout(total=_HTTP_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(_RELEASES_LATEST_URL, headers=headers) as resp:
                if resp.status == 304:
                    return cache  # unchanged since last check
                if resp.status != 200:
                    logger.debug("releases/latest returned HTTP %s", resp.status)
                    return cache
                payload = await resp.json()
                view: dict[str, object] = {
                    "tag": str(payload.get("tag_name") or ""),
                    "name": str(payload.get("name") or ""),
                    "body": str(payload.get("body") or ""),
                    "etag": resp.headers.get("ETag", "") or etag,
                    "checked_at": time.time(),
                }
                _write_cache(view)
                return view
    except Exception:
        # Offline / DNS / TLS — degrade to the cached view without raising.
        logger.debug("update check: network error, using cache", exc_info=True)
        return cache


async def build_update_status(current: str) -> dict[str, object]:
    """Assemble the C2 update-check payload for the running install.

    ``current`` is ``importlib.metadata.version("personalclaw")`` (the caller
    passes ``personalclaw.__version__``). ``latest`` comes from the tag-driven
    release probe; ``update_available`` compares the two numerically. The git
    kind additionally surfaces ``commits_behind`` as secondary info; the
    container kind carries ``instructions``.
    """
    kind = detect_install_kind()
    release = await fetch_latest_release()
    latest_tag = str(release.get("tag") or "")
    latest = _normalize_version(latest_tag)

    update_available = bool(latest) and _version_tuple(latest) > _version_tuple(current)

    commits_behind: int | None = None
    if kind == "git":
        proj = _project_dir()
        if proj:
            try:
                from personalclaw.dashboard.handlers.updates import (
                    _commits_behind_upstream,
                )

                commits_behind = await _commits_behind_upstream(proj)
            except Exception:
                commits_behind = None

    instructions: list[str] = []
    if kind == "container":
        instructions = [
            "docker compose -f deploy/compose/compose.yaml pull",
            "docker compose -f deploy/compose/compose.yaml up -d",
        ]

    return {
        "kind": kind,
        "current": _normalize_version(current),
        "latest": latest,
        "update_available": update_available,
        "commits_behind": commits_behind,
        "apply_method": _APPLY_METHOD.get(kind, "instructions"),
        "instructions": instructions,
        "release_name": str(release.get("name") or ""),
        "release_notes": str(release.get("body") or ""),
    }
