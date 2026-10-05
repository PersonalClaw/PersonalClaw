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

**Resolution order** (first hit wins)::

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

**What this module does NOT own.** The update of a source checkout itself: moving it to its
release, installing it and building its frontend, and putting it back where it was when that
fails, is one sequence every surface runs (:mod:`personalclaw.checkout_update`). Each surface
keeps what comes before and after it, because those genuinely differ: the dashboard answers a
request, holds a 409 in-flight guard, publishes ``update_progress`` over the websocket and
re-execs the live gateway; the CLI prints to the terminal and re-execs nothing (there is no
server in that process). The dashboard's Update and the gateway's staged auto-update are one
lifecycle, and so one function (``dashboard.handlers.updates.start_checkout_update``). A wheel's
upgrade is one install with nothing to put back, run by each surface through ``_installer``
(the tool that made the environment).

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, get_args

from personalclaw import versions
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
_HTTP_TIMEOUT_S = 10.0

# The version this install was running the last time a gateway started, kept OUTSIDE
# `config.json` because it is machine state, not a user preference: nothing should
# offer it as a setting, and a `config set` of it would be meaningless. It is the
# input `record_running_version` compares against to derive `updates.last_version`.
_RUN_STATE_FILENAME = "update_run.json"

# apply_method per kind (the update-check wire shape).
_APPLY_METHOD: dict[str, str] = {
    "git": "pipeline",
    "pip": "pip_upgrade",
    "container": "instructions",
    "desktop": "desktop_delegate",
}


def applies_updates_unattended(kind: str) -> bool:
    """Whether ``updates.auto = "staged"`` can install an update on this kind of install.

    Only a source checkout: the gateway moves it to the resolved release and restarts
    (``GatewayOrchestrator._auto_apply_update``). A pip or uv install is upgraded when the owner
    applies the update, a container is replaced from the host by pulling its new image, and the
    desktop app is reinstalled from the release page — nothing inside any of them runs unattended.
    The gateway, the update check and first-run setup all ask this, so none of them offers or
    describes an unattended update on a kind that cannot have one.
    """
    return kind == "git"


#: The repository's real default branch, used only as the last fallback of
#: :func:`resolve_default_branch` when every probe fails. It must stay in sync
#: with the repo: a literal naming a branch this project does not have fetches a
#: ref that cannot resolve, which is exactly the bug an earlier literal here had.
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


def cli_argv() -> list[str]:
    """The command that runs THIS install's own ``personalclaw`` CLI, before its subcommand.

    The one answer for every install kind, and for everything that starts the CLI again: a
    restart re-launching the gateway with its own arguments (``restart_request``), and every
    child that runs a subcommand of it. A frozen bundle's executable IS the CLI (its entry
    script is ``personalclaw/__main__.py``, and the desktop shell starts it as
    ``<bundle> gateway …``), so the subcommand follows it directly: an interpreter's ``-m`` is
    not an argument its parser accepts, and a gateway re-launched with one exited at once.
    Every other install (a checkout, a wheel, a ``uv tool`` or ``pipx`` install) runs it as this
    interpreter's ``-m personalclaw``, never as a console script found on ``PATH``, which can
    belong to another install.
    """
    import sys

    if is_frozen():
        return [sys.executable]
    return [sys.executable, "-m", "personalclaw"]


def detect_install_kind() -> InstallKind:
    """Classify the running install as git / pip / container / desktop."""
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
    """Resolve the directory the checkout's install and the frontend build run
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


# ── Tag-driven update check ─────────────────────────────────────────────────


def normalize_version(v: str) -> str:
    """Strip a leading ``v`` from a release tag so ``v0.1.3`` == ``0.1.3``.

    A spelling for display and for an image tag. Whether two versions are one, or which is
    newer, is :mod:`personalclaw.versions`' to say: a candidate's tag ``v0.3.0-rc.1`` and its
    installed ``0.3.0rc1`` are one version that no stripping makes the same text.
    """
    v = (v or "").strip()
    return v[1:] if v[:1] == "v" else v


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
        return not versions.same_version(target, current)
    return versions.is_newer(target, current)


def up_to_date_sentence(target: str, current: str, pin: str = "") -> str:
    """What a surface says when installing *target* would not move the install (``moves_to``).

    It names what is RUNNING, not only what resolved: a build newer than every published
    release is told it is ahead of that release, not that it is "already on" a release it
    does not run.
    """
    target, current = normalize_version(target), normalize_version(current)
    if (pin or "").strip():
        return f"Already on the pinned release (v{target})"
    if versions.same_version(target, current):
        return f"You're on the newest release (v{target})"
    return f"You're on v{current}, newer than the newest release (v{target})"


#: How an update stopped before it finished begins to say what that left: the owner pressed Cancel
#: (``POST /api/update/cancel``), or something else stopped it (a stopping gateway, a Ctrl-C of
#: ``personalclaw update``). What follows is what it left, in the words a failure uses.
UPDATE_CANCELLED = "The update was cancelled"
UPDATE_STOPPED = "The update was stopped before it finished"


def stopped_upgrade_sentence(lead: str, changed: str, current: str) -> tuple[str, bool]:
    """What a wheel's upgrade stopped before it finished left, after *lead*, and whether everything
    is as it was.

    A wheel has nothing to put back: what stopping its installer left is what the environment
    holds. Nothing was changed when its distributions are as they were before the install began
    (*changed* is ``""``, ``_installer.changed_distributions``); otherwise the sentence names the
    ones the installer had already replaced, and updating again finishes the upgrade.
    """
    if not changed:
        still = f"PersonalClaw is still on v{normalize_version(current)}"
        return f"{lead}. Nothing was changed: {still}.", True
    return (
        f"{lead}, but the upgrade had already changed {changed} in PersonalClaw's environment; "
        "update again to finish it."
    ), False


# ── Rollback: who writes `updates.last_version`, and how a pin is set ──
#
# A rollback needs exactly one fact the product did not previously keep: *which
# version was I on before this one?* `updates.last_version` is the field that holds
# it, and at first NOTHING wrote it — so its own `_meta` ("Maintained by the
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
    if previous == current or versions.same_version(previous, current):
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

    A pin names one exact release, and :func:`select_target` finds it by VERSION
    (:mod:`personalclaw.versions`), so a candidate is pinned in either of its spellings: its tag's
    ``0.3.0-rc.1``, or the ``0.3.0rc1`` ``personalclaw --version`` prints. Nothing that is not
    one release can be pinned: not a version line (``0.2``, ``0.2.x``), not a range
    (``>=0.2``), and not a version no release tag carries (a ``+local`` label, an epoch).

    ``""`` (after trimming) clears the pin; a leading ``v`` is stripped, as the pin is shown
    after one. Emptiness is judged BEFORE that strip, so a bare ``v`` is refused as the
    malformed version it is rather than quietly clearing the pin.
    """
    raw = (pin or "").strip()
    if not raw:
        return ""
    value = normalize_version(raw)
    release = versions.parse_version(value) if value[:1].isdigit() else None
    if (
        len(value) > 64
        or release is None
        or len(release.release) != 3
        or release.local is not None
        or release.epoch
    ):
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


def may_check_for_updates(*, asked: bool) -> bool:
    """Whether an update check may reach the network now: the one gate every check path asks.

    *asked* is true only for a check the owner starts by hand, Check now in Settings › Updates.
    That runs whatever the setting says, because it is exactly the request they asked for. Every
    other check is PersonalClaw reaching out on its own: the check at gateway start, the
    scheduled one, the one a page showing the update status runs when one is due, and the staged
    install that follows a check. Those run only while ``updates.check_enabled`` is on.

    Read from config at every call and never cached, so turning automatic checks off stops the
    next one without a restart. An update or rollback the owner applies (Update, Roll back,
    ``personalclaw update``) is not a check: it reaches GitHub because they asked it to, and does
    not ask this.
    """
    if asked:
        return True
    from personalclaw.config.loader import AppConfig

    return AppConfig.load().updates.check_enabled


async def build_update_status(current: str, *, fetch: bool) -> dict[str, object]:
    """Assemble the update-check payload for the running install.

    ``current`` is ``importlib.metadata.version("personalclaw")`` (the caller
    passes ``personalclaw.__version__``). ``latest`` names the release this
    install's channel/pin RESOLVES to, and ``release_name``/``release_notes``
    describe that same release; ``update_available`` says whether it is newer than
    ``current`` (:func:`personalclaw.versions.is_newer`, pre-releases in order). The git kind
    additionally
    surfaces ``commits_behind`` as secondary info; the container kind carries
    ``instructions``, the commands that pull that same release.

    **``fetch`` is the caller's answer to whether this check may reach out.** True asks GitHub for
    the releases list (and with it the release notes) first; False reads what the last fetch
    cached, so a pinned install with automatic checks off still sees its pinned release named.
    Every check decides it through :func:`may_check_for_updates`; nothing here reads the setting.
    This never fetches git: a source checkout's check fetches its origin once, in the check's git
    half, and ``commits_behind`` counts against what that fetch brought in.

    **One selection rule, for the check and for every apply.** The release is
    :func:`select_target`'s over the releases LIST, on the line :func:`_release_line` names:
    under a ``pin`` exactly that release, on ``beta`` the highest version including
    candidates, and otherwise the highest non-pre-release version. That is the rule every
    apply resolves with (:func:`resolve_target`, :func:`resolve_wheel_target`,
    :func:`select_image`), so the check cannot report one release while an apply installs
    another. GitHub's "Latest" marker is not read: the release pipeline marks every stable cut
    Latest, so a back-patch of an older line would take it, and a check that followed the
    marker would compare against the back-patch while the apply installed the newer line.

    **``checked`` says whether this comparison had anything to compare against.** It is
    THE update check for every kind that is not a git checkout, and it used to report
    nothing about itself: the dashboard's ``checked`` came only from the git half, so a
    pip, container or desktop install that had just compared itself with the newest
    release still read "No update check yet", and the hub tile, which ignored ``checked``,
    read "Up to date" for an install that had never been compared with anything. True when
    a releases list was known (fetched now or cached from an earlier check); False offline
    with nothing cached.

    **``pin_miss`` is the one "no release matches this pin" signal.** True only when a pin is
    set AND a releases list was actually read AND no release in it carries that version.
    ``latest == ""`` alone cannot say it: an offline install with nothing cached reads the
    same, and telling that user their pin names no release would be a guess.

    **``pin_older`` says a pin names a release OLDER than the one running**: a rollback set up
    and not applied yet. ``update_available`` is false then, since nothing newer is offered,
    and the panel said "Up to date" for an install its own pin was about to move back.
    """
    from personalclaw.config.loader import AppConfig

    kind = detect_install_kind()
    cfg = AppConfig.load()
    channel, pin = cfg.updates.channel, cfg.updates.pin
    pinned = bool((pin or "").strip())
    releases = await fetch_releases() if fetch else _releases_from_cache(read_releases_cache())
    resolved_tag = select_target(releases, _release_line(channel), pin)
    release: dict[str, object] = {}
    if resolved_tag:
        release = next(
            (r for r in releases if str(r.get("tag") or "") == resolved_tag),
            {"tag": resolved_tag},
        )
    checked = bool(releases)
    # A pin naming no release reports nothing available rather than the channel's newest,
    # which is the release the pin exists to refuse.
    pin_miss = pinned and not resolved_tag and checked
    latest = normalize_version(str(release.get("tag") or ""))

    update_available = versions.is_newer(latest, current)
    pin_older = pinned and versions.is_newer(current, latest)

    commits_behind: int | None = None
    if kind == "git":
        proj = source_checkout()
        if proj:
            try:
                commits_behind = await commits_behind_upstream(proj, fetch=False)
            except Exception:
                commits_behind = None

    # The container kind's pull+recreate commands carry the image tag of the release compared
    # ABOVE, so the check cannot name one release while its commands pull another. They exist
    # only for a move (`moves_to`, the CLI's answer too): no commands to pull the release that
    # already runs, or an older one a channel never asks for — a pin back is the rollback, and
    # its commands stand. No release — a pin naming none, or nothing fetched or cached — means
    # no tag and no commands, never a silent `latest`, mirroring the pip pin-miss refusal. The
    # commands are the documented install's own (`container_host`), for whichever of the two
    # ran it.
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
        "pin_older": pin_older,
        "commits_behind": commits_behind,
        "apply_method": _APPLY_METHOD.get(kind, "instructions"),
        "unattended_apply": applies_updates_unattended(kind),
        "instructions": instructions,
        "image_tag": image_tag,
        "release_name": str(release.get("name") or ""),
        "release_notes": str(release.get("body") or ""),
    }


# ── Channel + pin resolver ──────────────────────────────────────────────────
#
# The full releases LIST is the source of truth for the check and for every apply, on every
# channel. GitHub's ``releases/latest`` answers only the release marked "Latest", which cannot
# answer the ``beta`` channel — which must see prereleases — nor a ``pin`` to an older release,
# and on ``stable`` is not the highest version once a back-patch of an older line takes the
# marker. This endpoint returns every release, newest first; ``per_page=100`` is far more than a
# personal project cuts. ETag-cached and offline-tolerant.
_RELEASES_LIST_URL = "https://api.github.com/repos/PersonalClaw/PersonalClaw/releases?per_page=100"
_LIST_CACHE_FILENAME = "update_releases.json"


def _list_cache_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _LIST_CACHE_FILENAME


def read_releases_cache() -> dict[str, object]:
    """The last fetched releases-LIST view, or ``{}``. Never raises.

    Kept in its own file (``update_releases.json``), machine state beside the config.
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


def releases_checked_at() -> float:
    """When GitHub last answered a fetch of the releases list (a new list or "unchanged"), or 0.0.

    A check compares it before and after its fetch to tell an answer from a fetch that failed
    and fell back to the cache, which return the same list.
    """
    value = read_releases_cache().get("checked_at")
    return float(value) if isinstance(value, (int, float)) else 0.0


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

    Two independent signals, either sufficient: GitHub's own ``prerelease`` flag, and a tag
    whose version is a pre-release (:mod:`personalclaw.versions`): a candidate in either
    spelling (``v0.3.0-rc.1``, ``v0.3.0rc1``), an alpha, a beta or a dev build. Taking both
    means a release flagged prerelease with a plain tag, and a plainly-flagged release with a
    candidate's tag, are each kept out of stable and offered to beta.
    """
    if bool(release.get("prerelease")):
        return True
    version = versions.parse_version(str(release.get("tag") or ""))
    return version is not None and version.is_prerelease


def select_target(releases: list[dict[str, object]], channel: str, pin: str = "") -> str:
    """The release tag a *channel*/*pin* selects from *releases* (pure, no I/O).

    ``pin`` (a version, with or without a leading ``v``) OVERRIDES the channel:
    the tag of the release whose version equals the pin, or ``""`` when no such
    release exists. Otherwise the channel decides:

    * ``stable`` — the newest **non-prerelease** release.
    * ``beta`` — the newest release **including** prereleases.
    * ``nightly`` — ``""``: nightly tracks the checked-out branch, not a release
      tag (the git kind follows the branch), so there is no tag to name.

    Versions are compared as :mod:`personalclaw.versions` reads them. A pin finds its release
    by version, so either spelling of a candidate (``0.3.0-rc.1``, ``0.3.0rc1``) finds the tag
    ``v0.3.0-rc.1``. "Newest" is the highest version in pre-release order, so the answer does
    not depend on the order of the list: ``v0.3.0-rc.2`` beats ``v0.3.0-rc.1``, and a published
    ``v0.3.0`` beats both on ``beta``. A tag that is not a version is never selected, because it
    cannot be ordered against the others or the running version.
    Returns ``""`` when no candidate matches. Never raises — every field access is
    defensive, so a malformed cache degrades to ``""`` rather than an exception on
    the update path. An unrecognized channel falls to the ``stable`` arm (safest).
    """
    pin = (pin or "").strip()
    if pin:
        for rel in releases:
            tag = str(rel.get("tag") or "")
            if tag and versions.same_version(tag, pin):
                return tag
        return ""

    if channel == "nightly":
        return ""
    if channel == "beta":
        candidates = list(releases)
    else:  # "stable" and any unrecognized channel -> the safe, non-prerelease line
        candidates = [r for r in releases if not _is_prerelease(r)]

    tags = [str(rel.get("tag") or "") for rel in candidates]
    readable = [tag for tag in tags if versions.parse_version(tag) is not None]
    return max(readable, key=versions.order_key, default="")


def _release_line(channel: str) -> str:
    """The line a channel selects on wherever a RELEASE is what moves (not a branch).

    ``beta`` is its own line. Every other channel rides ``stable``: the git-only ``nightly``
    tracks a branch, and a wheel or an image has no build of a branch to move to; an unknown
    channel takes the safe line. The update check, the wheel apply and the container apply all
    select through here, so none of them can pick a different release for the same config.
    """
    return "beta" if channel == "beta" else "stable"


async def fetch_releases() -> list[dict[str, object]]:
    """Return the full GitHub releases list, ETag-cached and offline-tolerant.

    The whole list, which the channel/pin resolver needs. Sends ``If-None-Match`` with the cached
    ETag: a 304 (or any network error) returns the cached list unchanged — empty
    when nothing was ever fetched — a 200 refreshes and re-caches. A 200 and a 304 are both
    answers, so each records when it came (:func:`releases_checked_at`). Never raises.

    It asks no gate itself. A check decides whether it may reach out
    (:func:`may_check_for_updates`) before it calls this; the applies that call it through
    :func:`resolve_target` and its siblings are updates the owner asked for.
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
                    # Unchanged since the last fetch, and GitHub said so: the list is current now.
                    write_releases_cache({**cache, "checked_at": time.time()})
                    return cached_list
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

    Identical to :func:`resolve_target` on the line :func:`_release_line` names: the git-only
    ``nightly`` channel tracks a branch, and there is no published wheel for a branch, so a
    wheel install rides the ``stable`` line instead of resolving to ``""``. A ``pin`` is a pin
    on every install kind and OVERRIDES the channel exactly as in :func:`resolve_target`.
    Never raises; returns ``""`` when nothing matches (offline with no cache, or a ``pin``
    naming no release).
    """
    return await resolve_target(_release_line(channel), pin)


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
    version = versions.parse_version(tag)
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
    tag = select_target(releases, _release_line(channel), pin)
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

    The checkout is a directory an agent's shell can write, so git runs with the settings that
    keep its own configuration from running a program (``net.git.git_argv``) and with the child
    allowlist, not the gateway's secrets; a fetch keeps the owner's SSH agent and sign-in. An
    ``origin`` those settings refuse (a local path) fails with PersonalClaw's reason and what to
    use instead, not git's bare ``transport 'file' not allowed``.
    """
    from personalclaw.net.git import (
        GitTooOld,
        git_argv,
        git_env,
        talks_to_remote,
        transport_refusal,
    )

    try:
        argv = git_argv(args)
    except GitTooOld as exc:
        # Said in place of running a git too old for the settings the checkout needs.
        return subprocess.CompletedProcess(["git", *args], 127, "", str(exc))
    env = git_env(site="self-update-git", remote=talks_to_remote(args))
    try:
        proc = subprocess.run(
            argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        # Named as the caller asked for it, not with the settings git_argv put in front.
        return subprocess.CompletedProcess(
            argv, 124, "", f"`git {' '.join(args)}` timed out after {timeout:g}s"
        )
    except (FileNotFoundError, OSError) as exc:
        return subprocess.CompletedProcess(argv, 127, "", f"cannot run git: {exc}")
    refused = transport_refusal(proc.stderr) if proc.returncode else ""
    if refused:
        return subprocess.CompletedProcess(argv, proc.returncode, proc.stdout, refused)
    return proc


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

    A literal fallback is only defensible if it names a branch that exists. This
    was once hardcoded to a branch name this repository has never carried,
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
    tags", the state a release update leaves the git kind in.
    """
    return _run_git(["checkout", ref], cwd=proj, timeout=30)


def git_fast_forward(proj: str, branch: str) -> subprocess.CompletedProcess[str]:
    """``git merge --ff-only origin/<branch>`` — advance a branch WITHOUT a reset.

    The nightly/developer channel is the one path that tracks the current branch
    instead of a release tag. It advances by fast-forward only: this can add new
    upstream commits but can NEVER rewrite or discard local history — a diverged
    branch makes it fail with a non-zero exit and an untouched tree, which is the
    safe answer. There is deliberately no ``reset --hard`` fallback; that silent
    tracked-change destruction is exactly what was retired.
    """
    return _run_git(["merge", "--ff-only", f"origin/{branch}"], cwd=proj, timeout=30)


def git_is_up_to_date(proj: str, branch: str) -> bool:
    """True when HEAD already matches ``origin/<branch>`` (nothing to apply)."""
    res = _run_git(["diff", "HEAD", f"origin/{branch}", "--quiet"], cwd=proj, timeout=10)
    return res.returncode == 0


def git_tracked_changes(proj: str) -> list[str]:
    """Porcelain status lines for TRACKED paths only — what an advance could clobber.

    Untracked entries (``??``) are safe across both advance mechanisms the updater
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


@dataclass(frozen=True)
class CheckoutPosition:
    """Where a checkout is: the commit HEAD names, and the branch HEAD is on (``""`` when it is
    detached, as it is on a release tag). Two positions are the same place when both agree.

    ``tag`` names the commit for a person (``v0.2.1``) when a tag points at it. It takes no part
    in the comparison: the fetch an update runs can bring a tag naming a commit that had none.
    """

    commit: str
    branch: str = ""
    tag: str = field(default="", compare=False)

    def __str__(self) -> str:
        if self.tag:
            return self.tag
        if self.branch:
            return f"{self.branch} at {self.commit[:7]}"
        return f"commit {self.commit[:7]}"

    def restore_args(self, *, force: bool = False) -> list[str]:
        """The git arguments that put a checkout back here: the branch, reset to this commit (a
        fast-forward moved it, and checking a tag out left it where it was), or this commit with
        HEAD detached. Without *force* git refuses to overwrite a change in the tree, so putting a
        checkout back never discards anyone's work; with it, those changes are discarded."""
        checkout = ["checkout", "--force"] if force else ["checkout"]
        if self.branch:
            return [*checkout, "-B", self.branch, self.commit]
        return [*checkout, "--detach", self.commit]


def git_position(proj: str) -> CheckoutPosition | None:
    """Where the checkout at *proj* is, or ``None`` when git cannot say.

    An update reads this before it moves anything, and does not move a checkout it could not put
    back. ``symbolic-ref --quiet`` exits 1 for a detached HEAD and with another code when it
    cannot answer, so a failure is never read as "detached".
    """
    head = _run_git(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], cwd=proj, timeout=10)
    commit = (head.stdout or "").strip() if head.returncode == 0 else ""
    if not commit:
        return None
    ref = _run_git(["symbolic-ref", "--quiet", "HEAD"], cwd=proj, timeout=10)
    if ref.returncode == 0:
        name = (ref.stdout or "").strip()
        if not name.startswith("refs/heads/"):
            return None
        branch = name.removeprefix("refs/heads/")
    elif ref.returncode == 1:
        branch = ""
    else:
        return None
    named = _run_git(["describe", "--tags", "--exact-match", commit], cwd=proj, timeout=10)
    tag = (named.stdout or "").strip() if named.returncode == 0 else ""
    return CheckoutPosition(commit, branch, tag)


def git_restore(proj: str, position: CheckoutPosition) -> subprocess.CompletedProcess[str]:
    """Put the checkout at *proj* back on *position* (:meth:`CheckoutPosition.restore_args`),
    refusing, as git does, to overwrite a change in the tree."""
    return _run_git(position.restore_args(), cwd=proj, timeout=30)


# ── Git primitives (async; the dashboard's pipeline runs on these) ──────────


async def commits_behind_upstream(proj: str, *, fetch: bool) -> int | None:
    """How many commits the configured upstream is ahead of HEAD, or ``None``
    when no upstream exists (or the probe fails) — i.e. a ``git pull`` cannot
    produce anything.

    With *fetch* it runs a best-effort ``git fetch`` first (short timeout, failure tolerated —
    offline, the count then reflects the last-fetched view). Only the nightly apply, an update
    the owner asked for, fetches here. The update check counts without fetching
    (:func:`build_update_status`): its own git half fetched the origin once already, and only
    if it was allowed to reach out."""
    import asyncio

    from personalclaw.net.git import git_argv, git_env

    if fetch:
        try:
            # start_new_session: `git fetch` forks a remote helper (git-remote-https, ssh),
            # and that helper is what a stalled fetch is actually waiting on — `fetch.kill()`
            # reached only the `git` wrapper and left the helper running. Only a GROUP signal
            # reaches it. See kill_timed_out.
            fetching = await asyncio.create_subprocess_exec(
                *git_argv(["fetch", "--quiet"]),
                cwd=proj,
                env=git_env(site="self-update-git", remote=True),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                await asyncio.wait_for(fetching.communicate(), timeout=_BEHIND_FETCH_TIMEOUT)
            except asyncio.TimeoutError:
                await kill_timed_out(fetching)
        except Exception:
            pass  # no git / no remote — the rev-list probe below decides
    try:
        proc = await asyncio.create_subprocess_exec(
            *git_argv(["rev-list", "--count", "HEAD..@{u}"]),
            cwd=proj,
            env=git_env(site="self-update-git"),
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
