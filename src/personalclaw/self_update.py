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

from personalclaw.cancellation import kill_timed_out

logger = logging.getLogger(__name__)

InstallKind = Literal["git", "pip", "container", "desktop"]

# The two async git deadlines in `commits_behind_upstream`, named so a test can inject
# one instead of sleeping on it.
_BEHIND_FETCH_TIMEOUT = 15.0
_BEHIND_REVLIST_TIMEOUT = 10.0

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

# The version this install was running the last time a gateway started, kept OUTSIDE
# `config.json` because it is machine state, not a user preference: nothing should
# offer it as a setting, and a `config set` of it would be meaningless. It is the
# input `record_running_version` compares against to derive `updates.last_version`.
_RUN_STATE_FILENAME = "update_run.json"

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


_COMPOSE_CMD = "docker compose -f deploy/compose/compose.yaml"


def container_instructions(image_tag: str = "") -> list[str]:
    """The two commands that update a container install, in order.

    Pure and network-free. When *image_tag* is given (the channel/pin-resolved tag,
    RUM-7) each command carries an inline ``PERSONALCLAW_IMAGE_TAG=<tag>`` assignment
    so the pull AND the recreate both target that exact tag. The compose file selects
    the image via ``ghcr.io/…:${PERSONALCLAW_IMAGE_TAG:-latest}``
    (``deploy/compose/compose.yaml``), and the two commands run as INDEPENDENT
    processes — setting the variable on only one would let the other fall back to
    ``latest`` — so the prefix is repeated on both. An empty tag emits the bare
    commands (compose's own ``latest`` default), which is the pre-RUM-7 answer a
    caller that resolved nothing still gets.
    """
    tag = (image_tag or "").strip()
    prefix = f"PERSONALCLAW_IMAGE_TAG={tag} " if tag else ""
    return [f"{prefix}{_COMPOSE_CMD} pull", f"{prefix}{_COMPOSE_CMD} up -d"]


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


# ── Rollback: who writes `updates.last_version`, and how a pin is set (RUM-9) ──
#
# A rollback needs exactly one fact the product did not previously keep: *which
# version was I on before this one?* `updates.last_version` is the field that holds
# it, and until RUM-9 NOTHING wrote it — so its own `_meta` ("Maintained by the
# updater") was false and any "Roll back to v<last_version>" control would have read
# an always-empty string.
#
# The writer is a STARTUP recorder, not an instrumented apply path, and that choice
# is load-bearing. Writing "the version I am leaving" inside each apply would need
# the write repeated in five places (the dashboard's git + pip applies, the CLI's
# git + pip applies, the staged auto-apply), each AFTER its own "already current"
# short-circuit — and it would still miss the three ways a version changes without
# our apply code running at all: a container recreated onto a new image tag, a
# desktop app replaced by its own installer, and a plain `pip install -U
# personalclaw` typed by hand. Comparing the running version against the version
# that ran last is one call site that catches all of them, and it cannot fire when
# nothing changed.


def _run_state_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _RUN_STATE_FILENAME


def read_run_state() -> dict[str, object]:
    """The last recorded run view (``{"version": ...}``), or ``{}``. Never raises."""
    try:
        data = json.loads(_run_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_run_state(version: str) -> None:
    """Persist *version* as the version this install is running. Never raises."""
    from personalclaw.atomic_write import atomic_write

    try:
        atomic_write(
            _run_state_path(),
            json.dumps({"version": version, "recorded_at": time.time()}, indent=2) + "\n",
            fsync=True,
        )
    except Exception:
        logger.debug("could not persist update run state", exc_info=True)


def write_updates_fields(fields: dict[str, str]) -> bool:
    """Persist ``updates.<key> = value`` for each of *fields* into ``config.json``.

    A read-modify-write of the raw JSON, exactly as ``PATCH
    /api/config/personalclaw`` does it — deliberately NOT ``AppConfig.save()``,
    which serialises the whole dataclass tree and so would rewrite every block from
    an in-memory view. Touching only the keys named here means an app-owned block
    this build does not model (``providers``, ``use_cases``, ``slack``) is carried
    through untouched, and a concurrent settings edit to an unrelated field is not
    clobbered by a stale snapshot.

    Returns ``True`` on a completed write. Returns ``False`` — never raises — when
    the existing file cannot be read or parsed: an unreadable config is exactly when
    you cannot know what you are about to overwrite, and losing a user's providers to
    record a rollback hint would be a catastrophic trade. The caller degrades to "no
    rollback offer", which is the safe direction.
    """
    from personalclaw.atomic_write import atomic_write
    from personalclaw.config.loader import config_path

    path = config_path()
    data: dict[str, object] = {}
    if path.exists():
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("refusing to write updates state: %s exists but is unreadable", path)
            return False
        if raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("refusing to write updates state: %s is not valid JSON", path)
                return False
            if not isinstance(parsed, dict):
                logger.warning("refusing to write updates state: %s is not a JSON object", path)
                return False
            data = parsed

    block = data.get("updates")
    if not isinstance(block, dict):
        block = {}
    block.update(fields)
    data["updates"] = block
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(data, indent=2) + "\n", fsync=True)
    except OSError:
        logger.warning("could not write updates state to %s", path, exc_info=True)
        return False
    return True


def record_running_version(current: str) -> str:
    """Record *current* as the running version; return the version it REPLACED.

    The single writer of ``updates.last_version``. Called once per gateway start:

    * first ever recorded start — remember *current*, write nothing to config and
      return ``""`` (there is no earlier version, so there is nothing to offer);
    * same version as last start — nothing changed, so nothing is written;
    * a DIFFERENT version — the version that ran last becomes
      ``updates.last_version`` (what "Roll back to v…" offers) and *current* becomes
      the new run state.

    Returns the newly recorded previous version, or ``""`` when nothing was
    recorded. Never raises: a failed record costs a rollback offer, and must never
    cost a gateway start.
    """
    current = normalize_version(current)
    if not current:
        return ""
    previous = normalize_version(str(read_run_state().get("version") or ""))
    if previous == current:
        return ""
    if not previous:
        write_run_state(current)
        return ""
    if not write_updates_fields({"last_version": previous}):
        # Config unwritable — do NOT advance the run state, or the previous version
        # is lost for good and the next start reads "nothing changed".
        return ""
    write_run_state(current)
    logger.info(
        "recorded rollback point: updates.last_version=%s (now running %s)", previous, current
    )
    return previous


def set_version_pin(version: str) -> bool:
    """Pin ``updates.pin`` to *version* so every apply path targets that release.

    The one write behind ``personalclaw update --to <version>`` and the dashboard's
    rollback control (which reaches the same field through the config PATCH). A pin
    already OVERRIDES the channel in every resolver — :func:`select_target`,
    :func:`resolve_wheel_target`, :func:`select_image_tag` — so pinning IS the
    rollback mechanism; nothing else needs a downgrade-specific code path.

    *version* is normalized (a leading ``v`` stripped) to match what the resolvers
    compare and what ``_EDITABLE_CONFIG`` accepts on the same field. Returns
    ``False`` without writing when it is empty or longer than the field's 64-char
    bound, so the CLI and the PATCH boundary refuse the same values.
    """
    version = normalize_version(version)
    if not version or len(version) > 64:
        return False
    return write_updates_fields({"pin": version})


async def fetch_latest_release() -> dict[str, object]:
    """Return the latest GitHub release view, ETag-cached and offline-tolerant.

    Sends ``If-None-Match`` with the cached ETag: a 304 (or any network error)
    returns the cached view unchanged; a 200 refreshes and re-caches. The
    returned dict has ``{tag, name, body, etag, checked_at}`` (empty ``tag`` when
    nothing has ever been fetched and we're offline).

    Honors the egress kill switch (RUM-3): when ``updates.check_enabled`` is
    false the updater makes ZERO outbound calls, so this returns the last cached
    view (or ``{}``) WITHOUT opening a network session — the same offline-tolerant
    answer, reached before any HTTP. ``api.github.com`` is the product's one
    unprompted destination; this is the switch that silences it.
    """
    from personalclaw.config.loader import AppConfig

    cache = read_release_cache()
    if not AppConfig.load().updates.check_enabled:
        logger.debug("update check disabled (updates.check_enabled=false); using cache")
        return cache

    import aiohttp

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
    passes ``personalclaw.__version__``). ``latest`` names the release this
    install's channel/pin RESOLVES to, and ``release_name``/``release_notes``
    describe that same release; ``update_available`` compares it with ``current``
    numerically. The git kind additionally surfaces ``commits_behind`` as secondary
    info; the container kind carries ``instructions``.

    **Why the resolved release and not ``releases/latest``** (RUM-10). The probe
    ``releases/latest`` answers only "the newest NON-prerelease", so on the ``beta``
    channel, and under any ``pin``, it names a release the apply would not install —
    the panel would report "update available — 0.3.0" and then render 0.3.0's notes
    while ``POST /api/update`` installed 0.3.1-rc.1. Resolving here is the same
    correction the ``nightly``/``commits_behind`` clause in ``api_update_check``
    makes for branch-tracking: the check has to agree with the apply.

    The extra releases-list fetch happens ONLY when the resolution can differ — a
    non-empty ``pin`` or the ``beta`` channel. ``stable`` is ``releases/latest`` by
    definition, and ``nightly`` tracks a branch with no release tag at all, so
    neither pays for a second call.
    """
    from personalclaw.config.loader import AppConfig

    kind = detect_install_kind()
    cfg = AppConfig.load()
    channel, pin = cfg.updates.channel, cfg.updates.pin
    release = await fetch_latest_release()
    if pin or channel == "beta":
        # 🔴 THE EGRESS KILL SWITCH COVERS THIS SECOND PROBE TOO (RUM-3). `check_enabled=false`
        # promises ZERO outbound calls from the check, and `fetch_releases` — unlike its sibling
        # `fetch_latest_release` — carries no guard of its own, because until now it was only
        # reached from a user-typed apply. Reading the cache here keeps the promise without
        # giving up the resolution: a pinned user who disabled checking still sees their pinned
        # release named, from whatever the last fetch stored.
        releases = (
            await fetch_releases()
            if cfg.updates.check_enabled
            else _releases_from_cache(read_releases_cache())
        )
        resolved_tag = select_target(releases, channel, pin)
        if resolved_tag:
            release = next(
                (r for r in releases if str(r.get("tag") or "") == resolved_tag),
                {"tag": resolved_tag},
            )
        elif pin:
            # A pin naming no release: report nothing available rather than the
            # stable latest, which is the release the pin exists to refuse.
            release = {}
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

    # The container kind rides the `updates` channel/pin (RUM-7): the pull+recreate
    # commands carry the resolved image tag, not a bare `latest`. A pin naming no
    # release resolves to "" — emit NO commands (the panel/CLI say why) rather than
    # silently offering `latest`, mirroring the pip pin-miss refusal (RUM-6).
    image_tag = ""
    instructions: list[str] = []
    if kind == "container":
        image_tag = await resolve_image_tag(channel, pin)
        instructions = container_instructions(image_tag) if image_tag else []

    return {
        "kind": kind,
        "current": normalize_version(current),
        "latest": latest,
        "update_available": update_available,
        "commits_behind": commits_behind,
        "apply_method": _APPLY_METHOD.get(kind, "instructions"),
        "instructions": instructions,
        "image_tag": image_tag,
        "release_name": str(release.get("name") or ""),
        "release_notes": str(release.get("body") or ""),
    }


# ── Channel + pin resolver (RUM-2) ──────────────────────────────────────────
#
# The full releases LIST is the source of truth for channel/pin resolution.
# ``releases/latest`` (:func:`fetch_latest_release`) only ever names the newest
# *non-prerelease*, so it cannot answer the ``beta`` channel — which must see
# prereleases — nor a ``pin`` to any older release. This endpoint returns every
# release, newest first; ``per_page=100`` is far more than a personal project
# cuts. ETag-cached and offline-tolerant, exactly like the latest probe.
_RELEASES_LIST_URL = "https://api.github.com/repos/PersonalClaw/PersonalClaw/releases?per_page=100"
_LIST_CACHE_FILENAME = "update_releases.json"


def _list_cache_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _LIST_CACHE_FILENAME


def read_releases_cache() -> dict[str, object]:
    """The last fetched releases-LIST view, or ``{}``. Never raises.

    Kept in its own file (``update_releases.json``) so it never clobbers the
    ``releases/latest`` cache :func:`read_release_cache` owns.
    """
    try:
        return json.loads(_list_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_releases_cache(data: dict[str, object]) -> None:
    """Persist the releases-list view for the next (ETag-conditional) fetch."""
    from personalclaw.atomic_write import atomic_write

    try:
        atomic_write(_list_cache_path(), json.dumps(data, indent=2) + "\n", fsync=True)
    except Exception:
        logger.debug("could not persist releases-list cache", exc_info=True)


def _release_view(item: dict[str, object]) -> dict[str, object]:
    """Reduce one GitHub release object to the fields the resolver needs."""
    return {
        "tag": str(item.get("tag_name") or ""),
        "name": str(item.get("name") or ""),
        "body": str(item.get("body") or ""),
        "prerelease": bool(item.get("prerelease")),
    }


def _releases_from_cache(cache: dict[str, object]) -> list[dict[str, object]]:
    """The list of release views inside a cache dict, or ``[]``."""
    raw = cache.get("releases")
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)]


def _is_prerelease(release: dict[str, object]) -> bool:
    """A release the ``stable`` channel must skip and ``beta`` must include.

    Two independent signals, either sufficient: GitHub's own ``prerelease`` flag,
    and a PEP 440 pre-release suffix on the tag (``v0.3.0-rc.1`` / ``-beta.N``) —
    the tag convention §3.6 names. Taking both means a release flagged prerelease
    with a plain tag, and a plainly-flagged release with a ``-rc``/``-beta`` tag,
    are each kept out of stable and offered to beta.
    """
    if bool(release.get("prerelease")):
        return True
    return "-" in normalize_version(str(release.get("tag") or ""))


def select_target(releases: list[dict[str, object]], channel: str, pin: str = "") -> str:
    """The release tag a *channel*/*pin* selects from *releases* (pure, no I/O).

    ``pin`` (a version, with or without a leading ``v``) OVERRIDES the channel:
    the tag of the release whose version equals the pin, or ``""`` when no such
    release exists. Otherwise the channel decides:

    * ``stable`` — the newest **non-prerelease** release.
    * ``beta`` — the newest release **including** prereleases.
    * ``nightly`` — ``""``: nightly tracks the checked-out branch, not a release
      tag (the git kind follows the branch — RUM-4), so there is no tag to name.

    "Newest" is the highest :func:`version_tuple`, ties broken toward the stable
    release (so a published ``v0.3.0`` beats its own ``v0.3.0-rc.1`` on ``beta``).
    Returns ``""`` when no candidate matches. Never raises — every field access is
    defensive, so a malformed cache degrades to ``""`` rather than an exception on
    the update path. An unrecognized channel falls to the ``stable`` arm (safest).
    """
    pin = (pin or "").strip()
    if pin:
        want = normalize_version(pin)
        for rel in releases:
            tag = str(rel.get("tag") or "")
            if tag and normalize_version(tag) == want:
                return tag
        return ""

    if channel == "nightly":
        return ""
    if channel == "beta":
        candidates = list(releases)
    else:  # "stable" and any unrecognized channel -> the safe, non-prerelease line
        candidates = [r for r in releases if not _is_prerelease(r)]

    best_tag = ""
    best_key: tuple[tuple[int, ...], bool] | None = None
    for rel in candidates:
        tag = str(rel.get("tag") or "")
        if not tag:
            continue
        key = (version_tuple(tag), not _is_prerelease(rel))
        if best_key is None or key > best_key:
            best_key, best_tag = key, tag
    return best_tag


async def fetch_releases() -> list[dict[str, object]]:
    """Return the full GitHub releases list, ETag-cached and offline-tolerant.

    Mirrors :func:`fetch_latest_release` but hits ``/releases`` (the whole list),
    which the channel/pin resolver needs. Sends ``If-None-Match`` with the cached
    ETag: a 304 (or any network error) returns the cached list unchanged — empty
    when nothing was ever fetched — a 200 refreshes and re-caches. Never raises.
    """
    import aiohttp

    cache = read_releases_cache()
    cached_list = _releases_from_cache(cache)
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
            async with session.get(_RELEASES_LIST_URL, headers=headers) as resp:
                if resp.status == 304:
                    return cached_list  # unchanged since last check
                if resp.status != 200:
                    logger.debug("releases returned HTTP %s", resp.status)
                    return cached_list
                payload = await resp.json()
                releases = [_release_view(it) for it in payload if isinstance(it, dict)]
                view: dict[str, object] = {
                    "releases": releases,
                    "etag": resp.headers.get("ETag", "") or etag,
                    "checked_at": time.time(),
                }
                write_releases_cache(view)
                return releases
    except Exception:
        # Offline / DNS / TLS — degrade to the cached list without raising.
        logger.debug("releases fetch: network error, using cache", exc_info=True)
        return cached_list


async def resolve_target(channel: str, pin: str = "") -> str:
    """The release tag a *channel*/*pin* selects, from the ETag-cached list.

    Fetches the releases list (offline-tolerant — a cached or empty list on any
    network failure) and applies :func:`select_target`. Never raises; returns
    ``""`` when nothing matches: offline with no cache, a ``pin`` naming no
    release, or the branch-tracking ``nightly`` channel.
    """
    releases = await fetch_releases()
    return select_target(releases, channel, pin)


async def resolve_wheel_target(channel: str, pin: str = "") -> str:
    """The release tag a WHEEL install (pip/pipx/uv) installs for *channel*/*pin*.

    Identical to :func:`resolve_target`, with one wheel-specific policy: the
    git-only ``nightly`` channel tracks a branch, and there is no published wheel
    for a branch, so a wheel install rides the ``stable`` line instead of resolving
    to ``""``. A ``pin`` is a pin on every install kind and OVERRIDES the channel
    exactly as in :func:`resolve_target` (RUM-2), so ``nightly`` is only remapped to
    ``stable`` when no pin is set. Never raises; returns ``""`` when nothing matches
    (offline with no cache, or a ``pin`` naming no release).
    """
    if channel == "nightly" and not (pin or "").strip():
        channel = "stable"
    return await resolve_target(channel, pin)


# ── Container image tag resolver (RUM-7) ────────────────────────────────────
#
# A container install advances by pulling a new image and recreating; the compose
# file picks the image tag from ``${PERSONALCLAW_IMAGE_TAG:-latest}``. So the
# container analogue of :func:`resolve_wheel_target` maps the ``updates``
# channel/pin onto that IMAGE tag rather than a release tag. The tag scheme is
# §3.6's: ``:X.Y.Z`` (immutable, for a pin), ``:X.Y`` (moving minor, for stable),
# ``:beta`` (moving prerelease line), ``:latest`` (stable fallback). RUM-8 publishes
# the moving ``:X.Y`` / ``:beta`` tags in the release pipeline; RUM-7 emits them.


def _moving_minor(tag: str) -> str:
    """The ``X.Y`` moving-minor image tag for a release *tag*, or "" if unparseable."""
    vt = version_tuple(tag)
    return f"{vt[0]}.{vt[1]}" if tag and len(vt) >= 2 else ""


def select_image_tag(releases: list[dict[str, object]], channel: str, pin: str = "") -> str:
    """The container IMAGE tag a *channel*/*pin* selects from *releases* (pure, no I/O).

    Maps RUM-2's release selection onto the container tag scheme §3.6:

    * a non-empty ``pin`` -> the exact ``X.Y.Z`` of the pinned release, or ``""``
      when no release matches it — a pin-miss must REFUSE, never ride ``latest``
      (mirrors the pip/wheel pin-miss refusal, RUM-6);
    * ``beta`` -> the moving ``beta`` tag (newest prerelease line);
    * ``stable`` — and ``nightly``/unknown, which have no container image of their
      own — the moving minor ``X.Y`` of the newest stable release, or ``latest``
      when none resolves (offline / no cache).

    ``""`` is returned ONLY for a pin-miss: every channel path yields a tag, so a
    caller reads ``""`` as "refuse", never as "offline". Never raises — a malformed
    cache degrades through :func:`select_target`'s defensive field access.
    """
    pin = (pin or "").strip()
    if pin:
        tag = select_target(releases, "stable", pin)  # a pin overrides the channel (RUM-2)
        return normalize_version(tag) if tag else ""
    if channel == "beta":
        return "beta"
    return _moving_minor(select_target(releases, "stable", "")) or "latest"


async def resolve_image_tag(channel: str, pin: str = "") -> str:
    """The container image tag for *channel*/*pin*, from the ETag-cached list.

    The container analogue of :func:`resolve_wheel_target`: fetches the releases
    list (offline-tolerant) and applies :func:`select_image_tag`. Never raises;
    returns ``""`` only on a pin-miss (a pinned version naming no release must not
    silently pull ``latest``).
    """
    releases = await fetch_releases()
    return select_image_tag(releases, channel, pin)


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
    """``git fetch origin <branch>`` — advance the remote-tracking ref for *branch*.

    The nightly (branch-tracking) path uses this; the release paths use
    :func:`git_fetch_tags`, which also brings the tags a checkout needs.
    """
    return _run_git(["fetch", "origin", branch], cwd=proj, timeout=60)


def git_fetch_tags(proj: str) -> subprocess.CompletedProcess[str]:
    """``git fetch --tags origin`` — fetch origin's branches AND its release tags.

    The release-based apply resolves a tag from GitHub's releases API and then
    checks it out locally; the tag has to be present in the local repository
    first, which a plain ``git fetch origin <branch>`` does not guarantee. This is
    the fetch half of the "ride release tags" path that replaced pull-from-main.
    """
    return _run_git(["fetch", "--tags", "origin"], cwd=proj, timeout=60)


def git_checkout(proj: str, ref: str) -> subprocess.CompletedProcess[str]:
    """``git checkout <ref>`` — move HEAD to a release tag (or any ref).

    Non-destructive by construction: git refuses to overwrite uncommitted local
    modifications and leaves the tree untouched with a non-zero exit, so this can
    never silently discard a user's work the way ``reset --hard`` did. Checking out
    a tag detaches HEAD onto that exact release — which is precisely "ride release
    tags", the state RUM-4 leaves the git kind in.
    """
    return _run_git(["checkout", ref], cwd=proj, timeout=30)


def git_fast_forward(proj: str, branch: str) -> subprocess.CompletedProcess[str]:
    """``git merge --ff-only origin/<branch>`` — advance a branch WITHOUT a reset.

    The nightly/developer channel is the one path that tracks the current branch
    instead of a release tag. It advances by fast-forward only: this can add new
    upstream commits but can NEVER rewrite or discard local history — a diverged
    branch makes it fail with a non-zero exit and an untouched tree, which is the
    safe answer. There is deliberately no ``reset --hard`` fallback; that silent
    tracked-change destruction is exactly what RUM-4 retired.
    """
    return _run_git(["merge", "--ff-only", f"origin/{branch}"], cwd=proj, timeout=30)


def git_is_up_to_date(proj: str, branch: str) -> bool:
    """True when HEAD already matches ``origin/<branch>`` (nothing to apply)."""
    res = _run_git(["diff", "HEAD", f"origin/{branch}", "--quiet"], cwd=proj, timeout=10)
    return res.returncode == 0


def git_tracked_changes(proj: str) -> list[str]:
    """Porcelain status lines for TRACKED paths only — what an advance could clobber.

    Untracked entries (``??``) are safe across both an advance mechanism RUM-4
    uses (``git checkout`` refuses to touch them; a fast-forward leaves them), so
    they are excluded: warning about files that are not at risk trains the reader
    to click through the warning that matters. The auto/CLI paths use this to
    require a clean tree before advancing, so an in-progress edit is never at risk.
    """
    res = _run_git(["status", "--porcelain"], cwd=proj, timeout=10)
    if res.returncode != 0:
        return []
    # NOT stripped: porcelain status codes are column-significant (" M" unstaged vs
    # "M " staged), and stripping the blob eats the first line's leading space.
    return [ln for ln in (res.stdout or "").splitlines() if ln.strip() and not ln.startswith("??")]


# ── Git primitives (async; the dashboard's pipeline runs on these) ──────────


async def commits_behind_upstream(proj: str) -> int | None:
    """How many commits the configured upstream is ahead of HEAD, or ``None``
    when no upstream exists (or the probe fails) — i.e. a ``git pull`` cannot
    produce anything. Runs a best-effort ``git fetch`` first (short timeout,
    failure tolerated — offline, the count then reflects the last-fetched
    view, which is also what drove the "update available" signal)."""
    import asyncio

    try:
        # start_new_session: `git fetch` forks a remote helper (git-remote-https, ssh),
        # and that helper is what a stalled fetch is actually waiting on — `fetch.kill()`
        # reached only the `git` wrapper and left the helper running. Only a GROUP signal
        # reaches it. See kill_timed_out.
        fetch = await asyncio.create_subprocess_exec(
            "git",
            "fetch",
            "--quiet",
            cwd=proj,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            await asyncio.wait_for(fetch.communicate(), timeout=_BEHIND_FETCH_TIMEOUT)
        except asyncio.TimeoutError:
            await kill_timed_out(fetch)
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
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=_BEHIND_REVLIST_TIMEOUT)
        except asyncio.TimeoutError:
            # No start_new_session on the spawn above: `git rev-list` is leaf plumbing
            # that never forks, so a session would only widen what a group signal can
            # reach. kill_timed_out falls back to the single pid for exactly this case —
            # what it adds here over `kill()` + an unbounded drain is the BOUND.
            await kill_timed_out(proc)
            return None
        if proc.returncode != 0:
            return None  # no upstream configured (or not a git checkout)
        try:
            return int(out.decode(errors="replace").strip())
        except ValueError:
            return None
    except Exception:
        return None
