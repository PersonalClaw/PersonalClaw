"""Self-update: what kind of install is this, and what does updating it mean.

Every surface that can update PersonalClaw in place — the dashboard's
``POST /api/update``, the ``personalclaw update`` CLI, and the gateway's
unattended auto-update — has to answer the same two questions first: *how was
this copy installed?* and *what is the correct way to advance it?* This module
owns both answers, and the primitives that carry them out.

It lives in the core package, not under ``dashboard/``, deliberately. The
install-kind taxonomy shipped as ``dashboard/handlers/updates_kind.py``, which
made it reachable only by importing an HTTP handler — so the CLI kept its own
git-only pipeline and, on the pip/pipx/uv-tool installs the README documents
first, ``personalclaw update`` dead-ended on "PERSONALCLAW_PROJECT_DIR not set"
with the correct machinery one module away (DIST-13). A decision layer that only
one frontend can import will drift from the other one; this module is the seam
both call.

**Resolution order** (first hit wins, contract C1)::

    env PERSONALCLAW_INSTALL_KIND in {"container","desktop"}  -> that
        (baked into the Dockerfiles; set by the Electron shell, plan 45)
    a resolvable project dir that contains a .git directory     -> "git"
    else                                                        -> "pip"

``"pip"`` is one member covering every wheel install — ``pip``, ``pipx``,
``uv tool`` — because the apply is identical for all three: upgrade the wheel in
the running interpreter's environment. Which program performs it is resolved
separately by :mod:`personalclaw._installer` (a uv venv ships no pip).

**What this module does NOT own.** Sequencing and reporting stay with each
frontend, because the two lifecycles genuinely differ: the dashboard applies
asynchronously, publishes ``update_progress`` over the websocket, holds a 409
in-flight guard and re-execs the live gateway; the CLI applies synchronously,
prints to stdout, prompts a TTY and re-execs nothing (there is no server in that
process). A single ``apply(kind, progress=...)`` would be a callback-shaped
abstraction over two different lifecycles, so it was rejected — the shared part
is the decision plus these primitives.

Pre-1.0 clean break (owner 2026-07-20): implemented directly, WITHOUT a
lifecycle gate — there is no lifecycle/gates.py machinery yet, so this is the one
behavior, not a gated alternative to the old git-only path.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Literal, get_args

logger = logging.getLogger(__name__)

InstallKind = Literal["git", "pip", "container", "desktop"]

#: Every member of the taxonomy, in resolution order. Callers that branch per
#: kind assert against this so adding a member reds their dispatch test instead
#: of silently falling into someone's default arm.
INSTALL_KINDS: tuple[InstallKind, ...] = get_args(InstallKind)

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

#: The repository's real default branch, used only as the last fallback of
#: :func:`resolve_default_branch` when every probe fails. It must stay in sync
#: with the repo: a literal naming a branch this project does not have fetches a
#: ref that cannot resolve, which is exactly the bug DIST-13 closed.
DEFAULT_BRANCH_FALLBACK = "main"


def project_dir() -> str:
    """The resolved source-tree dir, or "".

    ``PERSONALCLAW_PROJECT_DIR`` is set at startup by ``cli._detect_project_dir``
    when the gateway runs from a checkout (it finds ``agents/`` + ``skills/``
    walking up from CWD, or a saved path). A wheel/container/desktop install has
    no such tree, so the env is unset.
    """
    return os.environ.get("PERSONALCLAW_PROJECT_DIR", "") or ""


def _git_dir_candidates(proj: str) -> list[Path]:
    """``proj`` and its parent — the two places the repo root can be.

    The project dir may be the repo root, or nested one level under it (monorepo
    layout — see :func:`package_root`).
    """
    root = Path(proj)
    return [root, root.parent]


def git_root(proj: str) -> str:
    """The working tree that carries ``.git`` for *proj*, or "" if neither does.

    A ``.git`` entry is normally a directory, but in a git *worktree* or a
    submodule it is a file pointing at the real gitdir — accept either. Git
    commands run here; :func:`package_root` is where the installer runs.
    """
    if not proj:
        return ""
    for cand in _git_dir_candidates(proj):
        if (cand / ".git").exists():
            return str(cand)
    return ""


def detect_install_kind() -> InstallKind:
    """Classify the running install as git / pip / container / desktop (C1)."""
    env_kind = (os.environ.get("PERSONALCLAW_INSTALL_KIND") or "").strip().lower()
    if env_kind in _ENV_KINDS:
        return env_kind  # type: ignore[return-value]
    if git_root(project_dir()):
        return "git"
    return "pip"


def container_instructions() -> list[str]:
    """The two commands that update a container install, in order.

    Pure and network-free so the CLI's container branch needs neither a release
    probe nor a source tree to print an honest answer.
    """
    return [
        "docker compose -f deploy/compose/compose.yaml pull",
        "docker compose -f deploy/compose/compose.yaml up -d",
    ]


def package_root(proj: str) -> str:
    """Resolve the directory ``pip install -e .`` and the frontend build run
    from. Git operations run at the repo root (``proj`` =
    ``PERSONALCLAW_PROJECT_DIR``), but the installable package may live one
    level down: a standalone checkout has ``pyproject.toml`` at the top,
    while the monorepo layout nests it at ``<repo>/PersonalClaw``. Falls
    back to ``proj`` unchanged when neither probe hits."""
    root = Path(proj)
    if (root / "pyproject.toml").is_file():
        return str(root)
    nested = root / "PersonalClaw"
    if (nested / "pyproject.toml").is_file():
        return str(nested)
    return proj


# ── Tag-driven update check (contract C2) ───────────────────────────────────


def normalize_version(v: str) -> str:
    """Strip a leading ``v`` from a release tag so ``v0.1.3`` == ``0.1.3``."""
    v = (v or "").strip()
    return v[1:] if v[:1] == "v" else v


def version_tuple(v: str) -> tuple[int, ...]:
    """Parse a dotted version to a tuple for numeric comparison (best-effort)."""
    core = normalize_version(v).split("+", 1)[0].split("-", 1)[0]
    try:
        return tuple(int(x) for x in core.split("."))
    except (ValueError, AttributeError):
        return (0,)


def _cache_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _CACHE_FILENAME


def read_release_cache() -> dict[str, object]:
    """The last fetched ``releases/latest`` view, or ``{}``. Never raises."""
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_release_cache(data: dict[str, object]) -> None:
    """Persist the release view for the next (ETag-conditional) check."""
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

    cache = read_release_cache()
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
                write_release_cache(view)
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
    latest = normalize_version(latest_tag)

    update_available = bool(latest) and version_tuple(latest) > version_tuple(current)

    commits_behind: int | None = None
    if kind == "git":
        proj = project_dir()
        if proj:
            try:
                commits_behind = await commits_behind_upstream(proj)
            except Exception:
                commits_behind = None

    instructions: list[str] = container_instructions() if kind == "container" else []

    return {
        "kind": kind,
        "current": normalize_version(current),
        "latest": latest,
        "update_available": update_available,
        "commits_behind": commits_behind,
        "apply_method": _APPLY_METHOD.get(kind, "instructions"),
        "instructions": instructions,
        "release_name": str(release.get("name") or ""),
        "release_notes": str(release.get("body") or ""),
    }


# ── Installer diagnostics ───────────────────────────────────────────────────


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def installer_error_summary(stderr: str, *, limit: int = 200) -> str:
    """One human-safe line describing why an install failed.

    Two things the raw text can't do (both found by driving the real panel):

    * **Strip ANSI.** uv colorizes its diagnostics, so the raw bytes carry SGR
      escapes. Rendered in the browser they show up literally (``\x1b[31m``),
      making the message look corrupted.
    * **Take the FIRST meaningful line, not the last.** uv's resolver error is a
      multi-line tree whose headline comes first ("No solution found when
      resolving dependencies") and whose last line is a fragment ("unsatisfiable.")
      that says nothing on its own. pip's single-line ``ERROR:`` output is
      unaffected either way.
    """
    clean = _ANSI_RE.sub("", stderr or "")
    lines = [ln.strip(" \t│╰─▶×") for ln in clean.splitlines()]
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        return ""
    # Prefer an explicit error line when one exists (pip), else the headline (uv).
    head = next((ln for ln in lines if ln.lower().startswith(("error", "error:"))), lines[0])
    # Fold the following continuation lines in so a wrapped reason stays readable.
    joined = " ".join([head, *[ln for ln in lines[lines.index(head) + 1 :]]])
    return joined[:limit].strip()


def upgrade_spec(latest: str) -> str:
    """The requirement to hand the installer for a wheel-install upgrade.

    Pinned to the latest release tag when one is known, so the upgrade lands on
    the same release the check reported; plain ``personalclaw`` (unpinned ``-U``)
    when the tag is unknown — offline, that still upgrades to whatever the index
    offers rather than refusing to try.
    """
    latest = normalize_version(latest)
    return f"personalclaw=={latest}" if latest else "personalclaw"


# ── Git primitives (sync; the CLI's pipeline runs on these) ─────────────────


def _run_git(args: list[str], *, cwd: str, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run one ``git`` command in *cwd*, never raising.

    A timeout and a missing ``git`` binary are ordinary failures for an updater —
    the caller reports them and stops — so they come back as a non-zero
    CompletedProcess with the reason in ``stderr`` rather than as an exception
    every call site would have to wrap. Every git spawn in this module funnels
    through here, which also makes the whole git layer fakeable at one seam.
    """
    argv = ["git", *args]
    try:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv, 124, "", f"`{' '.join(argv)}` timed out after {timeout:g}s"
        )
    except (FileNotFoundError, OSError) as exc:
        return subprocess.CompletedProcess(argv, 127, "", f"cannot run git: {exc}")


def current_branch(proj: str) -> str:
    """The checked-out branch name, "" when detached or unresolvable."""
    res = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=proj, timeout=10)
    if res.returncode != 0:
        return ""
    name = (res.stdout or "").strip()
    return "" if name == "HEAD" else name


def resolve_default_branch(proj: str) -> str:
    """The branch an update should track, resolved honestly rather than guessed.

    Order, cheapest and most specific first:

    1. **The checked-out branch.** Updating means "advance the branch I am on";
       a contributor on a feature branch must not be reset onto another one.
    2. **The remote's own HEAD**, read locally from ``refs/remotes/origin/HEAD``
       (git writes it at clone time) — the answer when HEAD is detached, e.g. a
       checkout parked on a release tag. Offline-safe.
    3. **``git remote show origin``**, which asks the remote. Only reached when
       the local ref is absent (older clone, or a manually added remote), and it
       needs the network, so it is last among the probes.
    4. :data:`DEFAULT_BRANCH_FALLBACK` — the repository's real default branch.

    A literal fallback is only defensible if it names a branch that exists. Before
    DIST-13 this was hardcoded to a branch name this repository has never carried,
    so a detached-HEAD update fetched an unresolvable ref and failed confusingly.
    """
    branch = current_branch(proj)
    if branch:
        return branch

    sym = _run_git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=proj, timeout=10)
    if sym.returncode == 0:
        ref = (sym.stdout or "").strip()
        if ref:
            # "origin/main" -> "main"; a bare name is returned as-is.
            return ref.split("/", 1)[1] if ref.startswith("origin/") else ref

    show = _run_git(["remote", "show", "origin"], cwd=proj, timeout=30)
    if show.returncode == 0:
        for line in (show.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("HEAD branch:"):
                name = line.split(":", 1)[1].strip()
                # A remote with no branches reports "HEAD branch: (unknown)".
                if name and not name.startswith("("):
                    return name

    logger.debug("could not resolve a default branch in %s; using the literal fallback", proj)
    return DEFAULT_BRANCH_FALLBACK


def git_fetch(proj: str, branch: str) -> subprocess.CompletedProcess[str]:
    """``git fetch origin <branch>``."""
    return _run_git(["fetch", "origin", branch], cwd=proj, timeout=60)


def git_is_up_to_date(proj: str, branch: str) -> bool:
    """True when HEAD already matches ``origin/<branch>`` (nothing to apply)."""
    res = _run_git(["diff", "HEAD", f"origin/{branch}", "--quiet"], cwd=proj, timeout=10)
    return res.returncode == 0


def git_tracked_changes(proj: str) -> list[str]:
    """Porcelain status lines for TRACKED paths only — what a reset would destroy.

    Untracked entries (``??``) survive ``reset --hard``, so they are excluded:
    warning about files that are not at risk trains the reader to click through
    the warning that matters.
    """
    res = _run_git(["status", "--porcelain"], cwd=proj, timeout=10)
    if res.returncode != 0:
        return []
    # NOT stripped: porcelain status codes are column-significant (" M" unstaged vs
    # "M " staged), and stripping the blob eats the first line's leading space.
    return [ln for ln in (res.stdout or "").splitlines() if ln.strip() and not ln.startswith("??")]


def git_reset_hard(proj: str, branch: str) -> subprocess.CompletedProcess[str]:
    """``git reset --hard origin/<branch>`` — DESTRUCTIVE to tracked changes."""
    return _run_git(["reset", "--hard", f"origin/{branch}"], cwd=proj, timeout=10)


# ── Git primitives (async; the dashboard's pipeline runs on these) ──────────


async def commits_behind_upstream(proj: str) -> int | None:
    """How many commits the configured upstream is ahead of HEAD, or ``None``
    when no upstream exists (or the probe fails) — i.e. a ``git pull`` cannot
    produce anything. Runs a best-effort ``git fetch`` first (short timeout,
    failure tolerated — offline, the count then reflects the last-fetched
    view, which is also what drove the "update available" signal)."""
    import asyncio

    try:
        fetch = await asyncio.create_subprocess_exec(
            "git",
            "fetch",
            "--quiet",
            cwd=proj,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(fetch.communicate(), timeout=15)
        except asyncio.TimeoutError:
            try:
                fetch.kill()
            except ProcessLookupError:
                pass
            await fetch.communicate()
    except Exception:
        pass  # no git / no remote — the rev-list probe below decides
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-list",
            "--count",
            "HEAD..@{u}",
            cwd=proj,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.communicate()
            return None
        if proc.returncode != 0:
            return None  # no upstream configured (or not a git checkout)
        try:
            return int(out.decode(errors="replace").strip())
        except ValueError:
            return None
    except Exception:
        return None
