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
with the correct machinery one module away. A decision layer that only
one frontend can import will drift from the other one; this module is the seam
both call.

**Resolution order** (first hit wins, contract C1)::

    env PERSONALCLAW_INSTALL_KIND in {"container","desktop"}  -> that
        (baked into the Dockerfiles; set by the Electron shell)
    a FROZEN process (PyInstaller bundle)                       -> "desktop"
    the running package is a git checkout's (source_checkout)  -> "git"
    else                                                        -> "pip"

The ``git`` answer comes from where the running ``personalclaw`` package was imported from,
and from nothing else: not the working directory, not ``PERSONALCLAW_PROJECT_DIR``, not a path
``personalclaw setup`` once saved. Those all describe some tree near the process, which is not
the same thing as the install the process runs. See :func:`source_checkout`.

The frozen clause is not redundant with the env. The env is what the Electron shell
declares, and it is the *right* signal when the shell spawned the gateway — but the frozen
`personalclaw-backend` binary is also reachable without the shell (run straight out of
`…/Contents/Resources/backend-dist/`, or out of an extracted `dist/`), and with the env
unset the taxonomy fell through to ``"pip"``. That is the one kind that must never claim a
frozen bundle: ``_apply_pip_update`` runs ``<installer> install -U personalclaw==<tag>``
against ``sys.executable``, which inside a bundle is the PyInstaller launcher — there is no
interpreter there to upgrade. Answering ``"desktop"`` from the artefact itself means the
correct refusal ("download the new version") no longer depends on an environment variable
being present.

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

from packaging.version import InvalidVersion, Version

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


def _package_dir() -> Path:
    """The directory the running ``personalclaw`` package was imported from."""
    import personalclaw

    return Path(personalclaw.__file__).resolve().parent


def source_checkout() -> str:
    """The source checkout the RUNNING ``personalclaw`` package comes from, or "" for a wheel.

    Read from the package's own location, the one fact about an install this process cannot get
    wrong. An editable install (``pip install -e .``, ``uv pip install -e .``) imports
    ``<checkout>/src/personalclaw``, and its metadata agrees: ``direct_url.json`` names the
    checkout and says ``editable``. A wheel (pip, pipx, ``uv tool``, the image) imports its own
    copy from ``site-packages``, and its metadata can still name a checkout: ``uv tool install
    <checkout>`` records the checkout it was built from as its ``direct_url`` and runs none of its
    files. So the metadata is not asked. The location is also right when a path override puts a
    checkout ahead of an installed copy, because then the checkout's files are the ones running.

    It used to be the working directory. The CLI walked up from it for a checkout, or read a path
    ``personalclaw setup`` had saved from such a walk, and exported the result as
    ``PERSONALCLAW_PROJECT_DIR``. So a ``uv tool`` install started inside a PersonalClaw checkout
    counted as a git install, and "Update & Restart" ran ``git fetch`` and ``git checkout`` on
    that tree while the wheel the gateway runs from stayed where it was.

    The markers are the two things every source layout has: ``pyproject.toml`` and
    ``src/personalclaw``. Both the standalone checkout and the monorepo layout
    (``<repo>/PersonalClaw``) have them, and :func:`git_root` then finds the ``.git`` at the
    package root or one level up.
    """
    pkg = _package_dir()
    root = pkg.parent.parent
    if pkg.parent.name == "src" and (root / "pyproject.toml").is_file():
        return str(root)
    return ""


def _git_dir_candidates(proj: str) -> list[Path]:
    """``proj`` and its parent — the two places the repo root can be.

    A checkout's package root may be the repo root, or nested one level under it
    (monorepo layout — see :func:`package_root`).
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


def is_frozen() -> bool:
    """True when this process is a PyInstaller bundle rather than an interpreter.

    THE one test for "am I the packaged artefact?", and the only place that question is
    answered — before 2026-09-23 nothing in ``src/personalclaw`` asked it at all, which is
    why every packaged-only defect in that finding set was invisible to a suite that only
    ever runs from a checkout. ``sys.frozen`` is the attribute PyInstaller's bootloader sets
    on the module object; ``sys._MEIPASS`` is the unpacked-resources root it also sets, taken
    as a second independent signal so a future bootloader that drops one still reads
    correctly.
    """
    import sys

    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def detect_install_kind() -> InstallKind:
    """Classify the running install as git / pip / container / desktop (C1)."""
    env_kind = (os.environ.get("PERSONALCLAW_INSTALL_KIND") or "").strip().lower()
    if env_kind in _ENV_KINDS:
        return env_kind  # type: ignore[return-value]
    if is_frozen():
        # The frozen bundle is only ever produced for the desktop app, and "desktop" is
        # the kind whose apply path is the honest one for it (download + reinstall).
        return "desktop"
    if git_root(source_checkout()):
        return "git"
    return "pip"


def package_root(proj: str) -> str:
    """Resolve the directory ``pip install -e .`` and the frontend build run
    from. Git operations run in the checkout (``proj`` =
    :func:`source_checkout`), but the installable package may live one
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


def parse_version(v: str) -> Version | None:
    """*v* as an orderable version, or ``None`` when it is not a version.

    One version reaches this module spelled two ways, and both have to read as it: a release
    TAG spells a pre-release ``v0.3.0-rc.1``, while the installed package reports the same
    version as ``0.3.0rc1`` (the build normalizes it). PEP 440 reads both, and orders a
    pre-release before its release — ``0.3.0-rc.1 < 0.3.0-rc.2 < 0.3.0`` — the order the release
    lines ship in.

    Dropping the suffix instead, which is what this replaced, made every candidate of a line
    equal to the others and to its own release, and read the installed spelling as no version
    at all: a running candidate compared lower than everything, so every release, older ones
    included, was offered to it as an update.
    """
    try:
        return Version(normalize_version(v))
    except InvalidVersion:
        return None


def is_newer(candidate: str, current: str) -> bool:
    """True only when *candidate* is a later version than *current*.

    An empty or unreadable side is never newer: a release this cannot order against the running
    one is not a release to offer.
    """
    new, now = parse_version(candidate), parse_version(current)
    return new is not None and now is not None and new > now


def same_version(a: str, b: str) -> bool:
    """True when *a* and *b* are one version, however each is spelled.

    ``v0.3.0-rc.1`` (a tag) and ``0.3.0rc1`` (what that release reports once installed) are one.
    """
    version = parse_version(a)
    return version is not None and version == parse_version(b)


def moves_to(target: str, current: str, pin: str = "") -> bool:
    """Whether installing release *target* moves an install running *current* the way it asked.

    The one answer for every surface that installs, pulls or checks out a resolved release — the
    dashboard's apply, the CLI's, and the staged auto-apply — so none of them acts on a release
    that is no move at all. A pin names one exact release, so any OTHER version is a move to it:
    a pin to an older release is the rollback. A channel only ever moves forward, so only a NEWER
    release is a move, and a build newer than every release is not taken back to the newest one.
    No target is no move; each caller decides what finding no release means for its kind.
    """
    if not normalize_version(target):
        return False
    if (pin or "").strip():
        return not same_version(target, current)
    return is_newer(target, current)


def up_to_date_sentence(target: str, current: str, pin: str = "") -> str:
    """What a surface says when installing *target* would not move the install (``moves_to``).

    It names what is RUNNING, not only what resolved: a build newer than every published
    release is told it is ahead of that release, not that it is "already on" a release it
    does not run.
    """
    target, current = normalize_version(target), normalize_version(current)
    if (pin or "").strip():
        return f"Already on the pinned release (v{target})"
    if same_version(target, current):
        return f"You're on the newest release (v{target})"
    return f"You're on v{current}, newer than the newest release (v{target})"


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


# ── Rollback: who writes `updates.last_version`, and how a pin is set ──
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

    A read-modify-write of the raw JSON in the config transaction, exactly as ``PATCH
    /api/config/personalclaw`` does it. Touching only the keys named here means an
    app-owned block this build does not model (``providers``, ``use_cases``, ``slack``)
    is carried through untouched, and the transaction means a settings edit another
    process makes at the same moment is not clobbered either.

    Returns ``True`` on a completed write. Returns ``False`` — never raises — when
    the existing file cannot be read or parsed: an unreadable config is exactly when
    you cannot know what you are about to overwrite, and losing a user's providers to
    record a rollback hint would be a catastrophic trade. The caller degrades to "no
    rollback offer", which is the safe direction. The same when the write cannot get
    its turn, or the disk refuses it.
    """
    from personalclaw.config.loader import ConfigWriteError
    from personalclaw.config.transactions import mutate_config

    def _apply(data: dict) -> None:
        block = data.get("updates")
        if not isinstance(block, dict):
            block = data["updates"] = {}
        block.update(fields)

    try:
        mutate_config(_apply)
    except ConfigWriteError as exc:
        logger.warning("refusing to write updates state: %s", exc)
        return False
    except OSError:
        logger.warning("could not write updates state", exc_info=True)
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


#: The shape of a version a pin can name: ``X.Y.Z`` or ``X.Y.Z-<prerelease>`` (``0.3.0-rc.1``),
#: the release-TAG convention with its leading ``v`` already stripped. A pin is matched
#: EXACTLY against the tags (:func:`select_target`), so nothing else can ever name a release:
#: not a version line (``0.2``, ``0.2.x``), not a PEP 440 range (``>=0.2``), not the PyPI
#: spelling of a prerelease (``0.3.0rc1``, whose tag is ``v0.3.0-rc.1``).
_PIN_SHAPE = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?")


def normalize_pin(pin: str) -> str:
    """The storable spelling of a version pin; raises :class:`ValueError` for one that is not.

    THE one rule for what ``updates.pin`` may hold, shared by the dashboard PATCH (through its
    ``_EDITABLE_CONFIG`` sanitizer) and ``personalclaw update --to`` (:func:`set_version_pin`),
    so the two cannot disagree about which pins are storable.

    A pin that could never match a release used to be stored at 200, and then every update
    quietly stopped: the check reported nothing available and every apply refused, because a
    pin-miss must never fall back to the channel's newest release. Refusing the SHAPE at write
    time turns that into an answer while the user is still looking at the box. A well-shaped
    pin that matches no release (``0.2.1`` before 0.2.1 exists) cannot be refused here without
    the network, so :func:`build_update_status` reports it as ``pin_miss`` instead.

    ``""`` (after trimming) clears the pin; a leading ``v`` is stripped, the spelling the
    resolvers compare. Emptiness is judged BEFORE that strip, so a bare ``v`` is refused as
    the malformed version it is rather than quietly clearing the pin.
    """
    raw = (pin or "").strip()
    if not raw:
        return ""
    value = normalize_version(raw)
    if len(value) > 64 or not _PIN_SHAPE.fullmatch(value):
        raise ValueError(
            f"{raw!r} is not a release version — pin an exact release such as 0.1.3 "
            "(or 0.3.0-rc.1 for a release candidate), or leave it empty to follow the channel"
        )
    return value


def set_version_pin(version: str) -> bool:
    """Pin ``updates.pin`` to *version* so every apply path targets that release.

    The one write behind ``personalclaw update --to <version>`` and the dashboard's
    rollback control (which reaches the same field through the config PATCH). A pin
    already OVERRIDES the channel in every resolver — :func:`select_target`,
    :func:`resolve_wheel_target`, :func:`select_image` — so pinning IS the
    rollback mechanism; nothing else needs a downgrade-specific code path.

    *version* goes through :func:`normalize_pin`, the rule the PATCH boundary applies to
    the same field. Returns ``False`` without writing when it is not a release version,
    or empty — ``--to`` names a release to go to, so an empty one is not a request to
    clear the pin.
    """
    try:
        version = normalize_pin(version)
    except ValueError:
        return False
    if not version:
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
    describe that same release; ``update_available`` says whether it is newer than
    ``current`` (:func:`is_newer`, pre-releases in order). The git kind additionally
    surfaces ``commits_behind`` as secondary info; the container kind carries
    ``instructions``, the commands that pull that same release.

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

    **``checked`` says whether this comparison had anything to compare against.** It is
    THE update check for every kind that is not a git checkout, and it used to report
    nothing about itself: the dashboard's ``checked`` came only from the git half, so a
    pip, container or desktop install that had just compared itself with the newest
    release still read "No update check yet", and the hub tile, which ignored ``checked``,
    read "Up to date" for an install that had never been compared with anything. True when
    the release this channel/pin resolves against was known (fetched now or cached from an
    earlier check); False offline with nothing cached.

    **``pin_miss`` is the one "no release matches this pin" signal.** True only when a pin is
    set AND a releases list was actually read AND no release in it carries that version.
    ``latest == ""`` alone cannot say it: an offline install with nothing cached reads the
    same, and telling that user their pin names no release would be a guess.
    """
    from personalclaw.config.loader import AppConfig

    kind = detect_install_kind()
    cfg = AppConfig.load()
    channel, pin = cfg.updates.channel, cfg.updates.pin
    release = await fetch_latest_release()
    checked = bool(release.get("tag"))
    pin_miss = False
    if pin or channel == "beta":
        # 🔴 THE EGRESS KILL SWITCH COVERS THIS SECOND PROBE TOO. `check_enabled=false`
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
        # The list is what this arm resolves against, so it — not `releases/latest` — decides
        # whether there was an answer to give.
        checked = bool(releases)
        if resolved_tag:
            release = next(
                (r for r in releases if str(r.get("tag") or "") == resolved_tag),
                {"tag": resolved_tag},
            )
        elif pin:
            # A pin naming no release: report nothing available rather than the
            # stable latest, which is the release the pin exists to refuse.
            release = {}
            pin_miss = bool(releases)
    latest_tag = str(release.get("tag") or "")
    latest = normalize_version(latest_tag)

    update_available = is_newer(latest, current)

    commits_behind: int | None = None
    if kind == "git":
        proj = source_checkout()
        if proj:
            try:
                commits_behind = await commits_behind_upstream(proj)
            except Exception:
                commits_behind = None

    # The container kind's pull+recreate commands carry the image tag of the release compared
    # ABOVE, so the check cannot name one release while its commands pull another, and no second
    # probe of the releases list runs (a second one ignored `check_enabled`). They exist only for
    # a move (`moves_to`, the CLI's answer too): no commands to pull the release that already
    # runs, or an older one a channel never asks for. No release — a pin naming none, or nothing
    # fetched or cached — means no tag and no commands, never a silent `latest`, mirroring the
    # pip pin-miss refusal. The commands are the documented install's own (`container_host`),
    # for whichever of the two ran it.
    image_tag = ""
    instructions: list[str] = []
    if kind == "container":
        from personalclaw import container_host

        image_tag = _image_tag_for(release, pin)
        if image_tag and moves_to(latest, current, pin):
            instructions = container_host.update_commands(image_tag)

    return {
        "kind": kind,
        "current": normalize_version(current),
        "latest": latest,
        "update_available": update_available,
        "checked": checked,
        "pin_miss": pin_miss,
        "commits_behind": commits_behind,
        "apply_method": _APPLY_METHOD.get(kind, "instructions"),
        "instructions": instructions,
        "image_tag": image_tag,
        "release_name": str(release.get("name") or ""),
        "release_notes": str(release.get("body") or ""),
    }


# ── Channel + pin resolver ──────────────────────────────────────────────────
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

    "Newest" is the highest version in pre-release order (:func:`parse_version`), so the
    answer does not depend on the order of the list: ``v0.3.0-rc.2`` beats ``v0.3.0-rc.1``,
    and a published ``v0.3.0`` beats both on ``beta``. A tag that is not a version is never
    selected, because it cannot be ordered against the others or the running version.
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
    best: Version | None = None
    for rel in candidates:
        tag = str(rel.get("tag") or "")
        version = parse_version(tag)
        if version is None:
            continue
        if best is None or version > best:
            best, best_tag = version, tag
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


# ── Container image tag resolver ────────────────────────────────────────────
#
# A container install advances by pulling a new image and recreating: the README's
# ``docker run`` names the tag on the image ref, and the compose file picks it from
# ``${PERSONALCLAW_IMAGE_TAG:-latest}`` (``container_host.update_commands`` spells both). So
# the container analogue of :func:`resolve_wheel_target` resolves the ``updates`` channel/pin
# to a release and names the IMAGE tag that carries it. The release pipeline publishes
# ``:X.Y.Z`` for every release (immutable, what a pin pulls), and moves ``:X.Y`` and
# ``:latest`` for a stable release and ``:beta`` for a prerelease (``scripts/release_tags.py``).


def _moving_minor(tag: str) -> str:
    """The ``X.Y`` moving-minor image tag for a release *tag*, or "" if unparseable."""
    version = parse_version(tag)
    if version is None or len(version.release) < 2:
        return ""
    return f"{version.major}.{version.minor}"


def _image_tag_for(release: dict[str, object], pin: str = "") -> str:
    """The image tag that carries *release* for a container on the ``updates`` channel/pin.

    A pin pulls the release's exact, immutable ``X.Y.Z``. A channel pulls the moving tag that
    release itself moved: ``beta`` for a prerelease, the moving minor ``X.Y`` for a stable
    release. So ``beta`` names ``:beta`` only while a candidate is its newest release; once a
    stable release is newer, ``:beta`` still carries the older candidate (a stable release never
    moves it, and before the first candidate it does not exist), and the channel follows that
    release's ``:X.Y`` like ``stable`` does. ``""`` when there is no release to carry.
    """
    tag = str(release.get("tag") or "")
    if not tag:
        return ""
    if (pin or "").strip():
        return normalize_version(tag)
    if _is_prerelease(release):
        return "beta"
    return _moving_minor(tag)


def select_image(releases: list[dict[str, object]], channel: str, pin: str = "") -> tuple[str, str]:
    """The release a container on *channel*/*pin* moves to, and the image tag that carries it.

    Pure, no I/O. ``(release tag, image tag)`` from ONE selection, so the release an update
    compares with the running version and the image its commands pull cannot be two different
    releases. The release is :func:`select_target`'s: a ``pin`` overrides the channel, ``beta``
    includes prereleases, and ``stable`` — like ``nightly`` and any unknown channel, which have
    no image of their own — rides the stable line. The tag is :func:`_image_tag_for`'s.

    ``("", "")`` when nothing resolves: a pin naming no release (which must refuse, never ride
    ``latest``), or no release known at all (offline with nothing cached). Never raises — a
    malformed cache degrades through :func:`select_target`'s defensive field access.
    """
    line = "beta" if channel == "beta" else "stable"
    tag = select_target(releases, line, pin)
    if not tag:
        return "", ""
    bare: dict[str, object] = {"tag": tag}
    release = next((r for r in releases if str(r.get("tag") or "") == tag), bare)
    return tag, _image_tag_for(release, pin)


async def resolve_image(channel: str, pin: str = "") -> tuple[str, str]:
    """:func:`select_image` over the ETag-cached releases list (offline-tolerant). Never raises."""
    releases = await fetch_releases()
    return select_image(releases, channel, pin)


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
