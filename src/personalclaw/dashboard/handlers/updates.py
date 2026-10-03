"""Update check/apply, log level, ring buffer, and SSE stream handlers."""

import asyncio
import collections
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

from personalclaw import __version__ as _local_version
from personalclaw import log_sinks, self_update, shutdown_event
from personalclaw.cancellation import kill_timed_out
from personalclaw.config import loader as config_loader
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.sse import GatewayStopping, next_unless_stopping
from personalclaw.dashboard.state import DashboardState
from personalclaw.frontend import build_frontend_async
from personalclaw.net.git import git_argv, git_env, transport_refusal
from personalclaw.request_validation import bool_field, json_object_body
from personalclaw.security import MaskingFormatter, mask_child_output


def config_path() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_path`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_path()


logger = logging.getLogger(__name__)

# ── Update ──

# Cached update check result
_update_info: dict[str, object] = {"available": False, "changes": "", "checked": False}
#: When an update check last reached out, automatic or asked for. The schedule counts from it.
_last_update_check: float = 0.0
#: The scheduled checks running in the background, held so none is collected mid-flight.
_scheduled_checks: "set[asyncio.Task[None]]" = set()


def get_update_info() -> dict[str, object]:
    """Return a copy of the cached update-check state."""
    return dict(_update_info)


def _scheduled_check_due(cfg: AppConfig, last_check: float, now: float) -> bool:
    """Whether the next automatic check is due: ``updates.check_interval_hours`` since the last.

    Only the schedule, read from config: there is no interval literal. Whether PersonalClaw may
    reach out on its own at all is the gate's question (:func:`self_update.may_check_for_updates`),
    asked before this one.
    """
    return now - last_check > cfg.updates.check_interval_hours * 3600


def automatic_check_due() -> bool:
    """Whether PersonalClaw would check for updates on its own right now: allowed, and due."""
    return self_update.may_check_for_updates(asked=False) and _scheduled_check_due(
        AppConfig.load(), _last_update_check, time.time()
    )


def _claim_update_check(*, asked: bool, at_start: bool) -> bool:
    """Whether this check may reach out now; when it may, the schedule counts from now.

    The gate answers first (:func:`self_update.may_check_for_updates`). An automatic check then
    has to be due as well, except the gateway's start-up check, the first of its run. Claimed
    before any await, so two requests arriving together do not both reach out.
    """
    global _last_update_check
    if not self_update.may_check_for_updates(asked=asked):
        return False
    now = time.time()
    if not (asked or at_start) and not _scheduled_check_due(
        AppConfig.load(), _last_update_check, now
    ):
        return False
    _last_update_check = now
    return True


def start_scheduled_check_if_due() -> None:
    """Start an automatic check in the background when one is due.

    ``GET /api/status`` calls this, so a dashboard's polling is the schedule's clock; there is no
    timer of its own, and a gateway nobody is looking at checks only when it starts.
    """
    if not automatic_check_due():
        return
    task = asyncio.create_task(_scheduled_check())
    _scheduled_checks.add(task)
    task.add_done_callback(_scheduled_checks.discard)


async def _scheduled_check() -> None:
    try:
        await update_status(asked=False)
    except Exception:
        logger.debug("scheduled update check failed", exc_info=True)


async def update_status(*, asked: bool, at_start: bool = False) -> dict[str, object]:
    """The update status, after the one check this caller may run (contract C2).

    Every update check comes here: the gateway's start (*at_start*), the status poll's schedule,
    a page showing the status (``GET /api/update/check``) and the owner's Check now (``POST
    /api/update/check``, *asked*). A check that may run (:func:`_claim_update_check`) reaches
    out once for each of its two answers: the releases list on every kind, and on a source
    checkout a ``git fetch`` of its origin for the changelog view. One that may not reads both
    from what the last check left, and nothing leaves the machine.

    The payload is the tag-driven cross-kind status ({kind, current, latest, update_available,
    commits_behind, apply_method, instructions}) merged with the git changelog-diff fields
    (available/changes). On the git ``nightly`` channel (branch-tracking) a non-zero
    ``commits_behind`` also sets ``available``, so the check agrees with what the apply does.

    ``checked_now`` is there only when this call ran a check: true when GitHub (or the
    checkout's origin) answered it, false when nothing did. So the Updates screen can show the
    answer the owner just asked for even with automatic checks off, and say so when none came.
    """
    ran = _claim_update_check(asked=asked, at_start=at_start)
    answered = False
    answered_before = self_update.releases_checked_at()
    if ran:
        answered = await _do_update_check(asked=asked)
    # The gate is read again for the releases list: a checkout's fetch can take a while, and
    # automatic checks can be turned off while it runs.
    fetch = ran and self_update.may_check_for_updates(asked=asked)
    cfg = AppConfig.load()
    try:
        status = await self_update.build_update_status(_local_version, fetch=fetch)
    except Exception:
        logger.debug("build_update_status failed; returning legacy view", exc_info=True)
        status = {}
    # Prefer the tag-driven `update_available` when we have a latest tag; else
    # fall back to the git changelog-diff `available` signal (offline git view).
    merged: dict[str, object] = {**_update_info, **status}
    # Either half is an answer. The git changelog-diff half only runs on a checkout; the
    # release-tag half runs on every kind and is the WHOLE check on pip/container/desktop.
    # Taking `checked` from the git half alone is why Updates → Check never gave a pip install
    # a result: it stayed false there forever, while this endpoint had just compared the
    # install with the newest release. A plain merge would also let the release half's
    # `False` erase a git answer, so the two are OR-ed.
    merged["checked"] = bool(_update_info.get("checked")) or bool(status.get("checked"))
    if status.get("latest"):
        merged["available"] = bool(status.get("update_available"))
    # The `nightly` channel tracks COMMITS, not release tags, so on a git checkout
    # being behind the upstream IS an available update — which is precisely what
    # POST /api/update applies on that channel. Without this clause the two
    # surfaces contradict each other: the panel reports "up to date" while the
    # apply would fast-forward. The tag comparison above cannot express it (`main`
    # carries commits with no newer tag, so `update_available` is False whenever a
    # release is reachable).
    if cfg.updates.channel == "nightly" and status.get("kind") == "git":
        _behind = status.get("commits_behind")
        if isinstance(_behind, int) and _behind > 0:
            merged["available"] = True
    # `updates.auto` (off | staged) is the opt-in unattended-apply mode that RETIRED the
    # legacy `auto_update` bool. The panel renders it as the Auto-update control.
    merged["auto"] = cfg.updates.auto
    merged["channel"] = cfg.updates.channel
    # `pin` + the container `image_tag` (from build_update_status) let the panel render
    # the exact channel/pin-resolved container commands. Empty `instructions` mean there is
    # nothing to move to; an empty `image_tag` too means no release resolved — a pin-miss
    # (`pin_miss` says which) or nothing fetched — so it never silently falls back to `latest`.
    merged["pin"] = cfg.updates.pin
    # The remaining `updates` fields the Settings > Updates screen edits. The panel
    # reads its controls' current values from THIS payload rather than a second
    # `GET /api/config/personalclaw` round trip, so every control on the screen renders from
    # one snapshot and cannot show a channel from one read beside an interval from another.
    merged["check_enabled"] = cfg.updates.check_enabled
    merged["check_interval_hours"] = cfg.updates.check_interval_hours
    # `last_version` is the rollback offer: the version this install ran before the
    # one running now, recorded at startup by `self_update.record_running_version`. Empty
    # until a version change has actually been observed — the panel hides the control then,
    # because "Roll back to v" with nothing after it is worse than no offer.
    merged["last_version"] = cfg.updates.last_version
    merged["version"] = _local_version
    if ran:
        merged["checked_now"] = answered or self_update.releases_checked_at() > answered_before
    return merged


def update_result_line(status: dict[str, object]) -> str:
    """What the gateway's start says its update check found, from the status it produced."""
    latest = str(status.get("latest") or "")
    current = str(status.get("current") or _local_version)
    pin = str(status.get("pin") or "")
    if status.get("available"):
        found = f": v{latest}" if latest else ""
        return f"Update available{found} — see Settings › Updates"
    if not status.get("checked_now"):
        return "Couldn't check for updates — Check now in Settings › Updates tries again"
    if status.get("pin_miss"):
        return f"No release matches the version pin {pin}"
    if not latest:
        return f"Up to date (v{current})"
    if status.get("pin_older"):
        return f"Pinned to v{latest}, older than this build (v{current})"
    return self_update.up_to_date_sentence(latest, current, pin)


async def api_update_check(request: web.Request) -> web.Response:
    """GET /api/update/check — the update status, checking only when an automatic check is due.

    What the Settings hub tile and Settings › Updates read on every visit. It reaches out only
    when PersonalClaw would check on its own anyway (automatic checks on, and the check interval
    passed since the last check); otherwise it reports what the last check found.
    """
    return web.json_response(await update_status(asked=False))


async def api_update_check_now(request: web.Request) -> web.Response:
    """POST /api/update/check — check for updates once, now, even with automatic checks off.

    Check now in Settings › Updates. The owner asked for this one check, so it reaches GitHub even
    with automatic checks off, and turns nothing on: the next check is again theirs to ask for.
    """
    return web.json_response(await update_status(asked=True))


async def _do_update_check(*, asked: bool) -> bool:
    """Run git fetch and compare HEAD with remote — on a GIT install only.

    The changelog-diff half of the check, which only the ``git`` kind can answer. Every
    other kind's "is there a newer version?" is the release-tag comparison
    :func:`self_update.build_update_status` makes, and :func:`update_status` merges the two —
    including ``checked``, which this half sets only for itself. Returns whether this half got
    an answer: the fetch and the comparison both finished.

    **Why the kind gate is here and not a project-dir probe.** ``PERSONALCLAW_PROJECT_DIR``
    is not a proxy for "this is a checkout": the Electron shell sets it to ``…/Resources``
    inside the app bundle (``desktop/gatewayEnv.js``), which carries no ``.git``. So on the
    owner's 2026-09-23 desktop install this function ran ``git fetch`` in a directory that
    is not a repository, twelve times in one session, each logging
    ``git fetch failed (rc=128): fatal: not a git repository`` and returning before it
    reached anything useful. The kind is what decides whether git means anything here, and
    ``self_update.detect_install_kind()`` is the one place that answers it — the same
    decision ``POST /api/update`` already dispatches on.

    The gate is asked again here, at the fetch itself (:func:`self_update.may_check_for_updates`):
    this is where the request would leave, so an automatic check with checks off stops before any
    subprocess, however it got here.
    """
    if not self_update.may_check_for_updates(asked=asked):
        logger.debug("update check: automatic checks are off (updates.check_enabled=false)")
        return False

    kind = self_update.detect_install_kind()
    if kind != "git":
        logger.debug("update check: %s install has no git history to diff; release tags only", kind)
        return False

    # The checkout the running package comes from, never whatever tree the process started in.
    proj = self_update.source_checkout()
    if not proj:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            *git_argv(["fetch", "--quiet"]),
            cwd=proj,
            # The checkout is a directory an agent's shell can write: git runs with the settings
            # that keep its configuration from running a program, and without the gateway's
            # secrets. The fetch keeps the owner's SSH agent and sign-in (`net.git`).
            env=git_env(site="update-check-git", remote=True),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            # Own group: `git fetch` forks a remote helper (git-remote-https, ssh) that
            # inherits stderr, so only a GROUP signal reaches it. See kill_timed_out.
            start_new_session=True,
        )
        try:
            _, fetch_err = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            await kill_timed_out(proc)
            logger.warning("git fetch timed out")
            return False
        if proc.returncode != 0:
            logger.warning(
                "git fetch failed (rc=%s): %s",
                proc.returncode,
                transport_refusal(fetch_err) or mask_child_output(fetch_err, limit=500, tail=True),
            )
            return False

        local = await asyncio.create_subprocess_exec(
            *git_argv(["rev-parse", "HEAD"]),
            cwd=proj,
            env=git_env(site="update-check-git"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            local_out, _ = await asyncio.wait_for(local.communicate(), timeout=10)
        except asyncio.TimeoutError:
            try:
                local.kill()
            except ProcessLookupError:
                pass
            await local.communicate()
            return False
        remote = await asyncio.create_subprocess_exec(
            *git_argv(["rev-parse", "@{u}"]),
            cwd=proj,
            env=git_env(site="update-check-git"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            remote_out, _ = await asyncio.wait_for(remote.communicate(), timeout=10)
        except asyncio.TimeoutError:
            try:
                remote.kill()
            except ProcessLookupError:
                pass
            await remote.communicate()
            return False

        local_sha = local_out.decode(errors="replace").strip()
        remote_sha = remote_out.decode(errors="replace").strip()

        # Check version: compare remote (or on-disk if already pulled) vs running
        available = False
        remote_version = ""
        target_sha = remote_sha if local_sha != remote_sha else local_sha
        if local_sha and remote_sha:
            # Read the version from the SINGLE SOURCE OF TRUTH at the target commit:
            # pyproject.toml's [project].version. Two reasons this is not
            # ``__init__.py``: (1) since version single-sourcing
            # ``__version__`` is computed (``_pkg_version("personalclaw")``), so there
            # is no literal left to scrape; (2) the ``:./path`` pathspec is resolved
            # relative to CWD, so one spelling covers both the standalone layout
            # (repo root IS the package root) and the monorepo layout where the
            # package is nested — a hardcoded repo-root-relative prefix is wrong for
            # whichever layout it was not written for.
            show = await asyncio.create_subprocess_exec(
                *git_argv(["show", f"{target_sha}:./pyproject.toml"]),
                cwd=proj,
                env=git_env(site="update-check-git"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                show_out, _ = await asyncio.wait_for(show.communicate(), timeout=10)
            except asyncio.TimeoutError:
                try:
                    show.kill()
                except ProcessLookupError:
                    pass
                await show.communicate()
                return False
            m = re.search(
                r'^version\s*=\s*"(.+?)"', show_out.decode(errors="replace"), re.MULTILINE
            )
            if m:
                remote_version = m.group(1)
            available = self_update.is_newer(remote_version, _local_version)

        changes = ""
        if available:
            diff_base = f"v{_local_version}" if local_sha == remote_sha else local_sha
            diff = await asyncio.create_subprocess_exec(
                *git_argv(["diff", f"{diff_base}..{target_sha}", "--", "CHANGELOG.md"]),
                cwd=proj,
                env=git_env(site="update-check-git"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                diff_out, _ = await asyncio.wait_for(diff.communicate(), timeout=10)
            except asyncio.TimeoutError:
                try:
                    diff.kill()
                except ProcessLookupError:
                    pass
                await diff.communicate()
                return False
            # Extract added lines from changelog diff
            lines: list[str] = []
            for line in diff_out.decode(errors="replace").splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    lines.append(line[1:])
            changes = "\n".join(lines).strip()

        _update_info["available"] = available
        _update_info["changes"] = changes
        # "latest" is the field name the FE consumes (UpdatesPanel "Update
        # available — vX" + settings search text); it was previously emitted
        # as "remote_version", which nothing read.
        _update_info["latest"] = remote_version
        _update_info["checked"] = True
        return True
    except Exception:
        logger.debug("Update check failed", exc_info=True)
        return False


async def api_changelog(request: web.Request) -> web.Response:
    """GET /api/changelog — read full CHANGELOG.md from project."""
    proj = os.environ.get("PERSONALCLAW_PROJECT_DIR", "")
    if not proj:
        return web.json_response({"content": ""})
    path = Path(proj) / "CHANGELOG.md"
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        content = ""
    return web.json_response({"content": content})


# In-flight guard: only one update apply may run at a time. A plain bool is
# race-free here because the handler sets it synchronously (no await between
# check and set) on the single-threaded event loop; the background task clears
# it in a finally. Concurrent POST /api/update returns 409 instead of spawning
# a second pull/build/restart pipeline against the same working tree.
_apply_in_flight = False


def _busy() -> web.Response | None:
    """The 409 for an update that must not start now, or ``None`` when one may.

    One is already running; or the gateway is stopping to restart. A restart returns to whoever
    asked for it and the gateway then takes a moment to stop, so an apply's own slot is free again
    by then: an Update pressed in that moment would pull, install and build against the tree the
    new image starts from, while the gateway stops under it.
    """
    from personalclaw.restart_request import pending

    if _apply_in_flight:
        return web.json_response({"error": "An update is already in progress"}, status=409)
    if pending() is not None:
        return web.json_response(
            {"error": "PersonalClaw is restarting. Try again once it is back."}, status=409
        )
    return None


async def _apply_pip_update(request: web.Request, state: DashboardState) -> web.Response:
    """Upgrade a pip/uv/pipx install in place, then graceful re-exec (T4.3).

    Runs ``<installer> install -U personalclaw==<resolve_wheel_target(channel,pin)>``
    — the release the ``updates`` channel/pin selects (RUM-6), NOT a blind
    ``releases/latest``. A pin installs exactly that version; ``stable``/``beta``
    resolve their channel's newest release; the git-only ``nightly`` channel has no
    published wheel and rides ``stable``. When a pin matches no release the apply
    REFUSES rather than falling back to latest. Targets the SAME interpreter/prefix
    the gateway runs from — mirrors the git path's editable install step. The
    installer is RESOLVED (uv or pip), not assumed: a uv-created venv ships no pip
    module (issue #51). No web build: the wheel already carries the SPA. The 409
    concurrent-apply guard is shared with the git path.
    """
    global _apply_in_flight

    busy = _busy()
    if busy is not None:
        return busy
    _apply_in_flight = True
    state.push_refresh("updating")

    auth_mode = _live_auth_mode(request)

    async def _apply() -> None:
        global _apply_in_flight
        try:
            # Ride the resolved release, not a blind `releases/latest`: the `updates`
            # channel/pin decides the target tag. resolve_wheel_target never
            # raises and maps the wheel-less `nightly` channel onto `stable`.
            cfg = AppConfig.load()
            try:
                target = await self_update.resolve_wheel_target(
                    cfg.updates.channel, cfg.updates.pin
                )
            except Exception:
                logger.debug(
                    "resolve_target failed; treating as no release resolved", exc_info=True
                )
                target = ""
            if cfg.updates.pin and not target:
                # A pin naming no release must NEVER silently upgrade to latest.
                state.push_update_progress(
                    "error", "No release matches the pinned version — check updates.pin."
                )
                return
            if target and not self_update.moves_to(target, _local_version, cfg.updates.pin):
                # Nothing to install. `-U personalclaw==<target>` for a release that is not
                # newer is a DOWNGRADE carried out under the name "update". No restart either:
                # unlike a checkout, a wheel has no local change for a restart to pick up.
                sentence = self_update.up_to_date_sentence(target, _local_version, cfg.updates.pin)
                state.push_update_progress("done", f"{sentence}.")
                state.clear_update_progress()
                return
            spec = self_update.upgrade_spec(target)

            # Resolve the installer instead of assuming stdlib pip — a uv venv has
            # none, which made self-update impossible on the uv install path while
            # the UI already labelled this kind "pip / uv install" (issue #51).
            from personalclaw._installer import NoInstallerError, install_argv, installer_env

            try:
                argv = install_argv(["-U", spec, "--quiet"])
            except NoInstallerError as exc:
                logger.error("self-update: %s", exc)
                state.push_update_progress("error", str(exc))
                return

            state.push_update_progress("installing", f"Upgrading {spec}…")
            pip_up = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=installer_env(),
                # Own group: pip forks build backends / compilers / `git clone` for VCS
                # specs, all inheriting these pipes. See kill_timed_out.
                start_new_session=True,
            )
            try:
                _, pip_err = await asyncio.wait_for(pip_up.communicate(), timeout=400)
            except asyncio.TimeoutError:
                await kill_timed_out(pip_up)
                state.push_update_progress("error", "pip upgrade timed out")
                return
            if pip_up.returncode != 0:
                detail = mask_child_output(pip_err, limit=None, one_line=False)
                logger.error(
                    "self-update failed (rc=%d): %s",
                    pip_up.returncode,
                    mask_child_output(pip_err, limit=500),
                )
                # Surface the REAL error, not just a static label. The cause was
                # captured and logged but never sent to the UI, so the panel said
                # only "pip upgrade failed" and the user had to read gateway.log
                # to learn anything actionable (issue #51).
                summary = self_update.installer_error_summary(detail)
                state.push_update_progress(
                    "error", f"Upgrade failed: {summary}" if summary else "Upgrade failed"
                )
                return
            # No frontend build — assets ship in the wheel.
            state.push_update_progress("restarting", "Restarting server…")
            await _graceful_reexec(state, auth_mode=auth_mode)
        except Exception:
            logger.exception("pip self-update failed")
            state.push_update_progress("failed", "Update failed — check logs")
            state.push_refresh("update_failed")
        finally:
            _apply_in_flight = False

    task = asyncio.create_task(_apply())
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    return web.json_response({"ok": True, "status": "updating", "kind": "pip"})


async def api_update_apply(request: web.Request) -> web.Response:
    """POST /api/update — advance the checkout to its release, rebuild, restart.

    Release-based, per the ``updates`` channel/pin (RUM-4): the git kind rides
    release TAGS like every other install kind — ``git fetch --tags`` +
    ``git checkout <tag>`` — it never fast-forwards ``main`` nor hard-resets the
    tree onto a branch. The git-only ``nightly`` channel is the ONE path that
    tracks the current
    branch, and it advances by FAST-FORWARD (clean tree required), never a reset.
    Then ``pip install -e .`` (same interpreter) → frontend rebuild
    (``npm ci && npm run build`` in ``web/``) → graceful re-exec. Progress is
    broadcast as ``update_progress`` WS events with steps ``pulling`` →
    ``installing`` → ``building`` → ``restarting`` (→ ``error``/``failed``).

    Graceful degradation: when there is NOTHING to advance (already on the
    resolved release tag / pinned version, no upstream, or offline with no
    release resolved) the pipeline short-circuits straight to the ``restarting``
    step — the user asked for "Update & Restart", and a restart is still
    meaningful (applies committed local changes). The dirty-tree gate only guards
    a REAL advance (moving HEAD over a dirty tree is dangerous); if nothing will
    be advanced, dirtiness doesn't matter, so the target probe runs BEFORE it.
    """
    global _apply_in_flight
    state: DashboardState = request.app["state"]

    kind = self_update.detect_install_kind()

    # Container / desktop: no in-place apply. Return the structured instructions
    # (honest commands beat pretending) — the panel renders them. No in-flight
    # slot is claimed because nothing runs here.
    if kind in ("container", "desktop"):
        try:
            # The owner asked for this update, so its release is looked up now.
            status = await self_update.build_update_status(_local_version, fetch=True)
        except Exception:
            status = {"kind": kind, "instructions": [], "apply_method": ""}
        return web.json_response(
            {
                "ok": True,
                "status": "instructions",
                "kind": kind,
                "apply_method": status.get("apply_method", ""),
                "instructions": status.get("instructions", []),
                # The channel/pin-resolved container image tag; "" for desktop, or when no
                # release resolved for a container. `instructions` are empty then too, and
                # whenever that release is no move from the one running.
                "image_tag": status.get("image_tag", ""),
                # The desktop wording says what the shell ACTUALLY does today. It shipped
                # claiming "the app updates itself", which described the electron-updater
                # half — still unbuilt: `desktop/package.json` carries no
                # electron-updater dependency and nothing in the shell checks for a release
                # (#2673). A user told the app self-updates simply never updates. The CLI's
                # own desktop branch (`cli_server._update_desktop`) has always named the
                # re-download; this is the same answer in the panel.
                "detail": (
                    "This is a container install — update by pulling the new "
                    "image and recreating."
                    if kind == "container"
                    else "This is a desktop install — install the new version from the "
                    "PersonalClaw releases page, then reopen the app."
                ),
            }
        )

    # pip / uv / pipx: upgrade the wheel into the RUNNING interpreter's prefix,
    # then graceful re-exec. No web build (assets ship in the wheel).
    if kind == "pip":
        return await _apply_pip_update(request, state)

    # git: ride release tags by channel/pin; nightly tracks the branch. The tree it
    # advances is the checkout the running package comes from, the same one the kind was read
    # from, never a tree the process only started in.
    proj = self_update.source_checkout()
    if not proj:
        return web.json_response(
            {"error": "No source checkout to update: this PersonalClaw does not run from one."},
            status=400,
        )

    busy = _busy()
    if busy is not None:
        return busy
    # Claim the in-flight slot BEFORE the first await below — otherwise two
    # concurrent POSTs could both pass the check while one parks on a subprocess.
    # A rejected concurrent request therefore does NO config read and NO network.
    # Every return path from here must release it.
    _apply_in_flight = True

    # Signal updating state via SSE
    state.push_refresh("updating")

    # What does "advance" mean for this checkout? The `updates` channel/pin decides,
    # replacing the retired `update_dev_mode` bool. Every channel but `nightly` rides
    # a release TAG (fetch --tags + checkout); `nightly` is the ONE branch-tracking
    # path (fast-forward only). resolve_target reads the ETag-cached releases list
    # and never raises.
    _cfg = AppConfig.load()
    _channel = _cfg.updates.channel
    _pin = _cfg.updates.pin
    _target_tag = ""
    if _channel != "nightly":
        try:
            _target_tag = await self_update.resolve_target(_channel, _pin)
        except Exception:
            logger.debug("resolve_target failed; treating as no release resolved", exc_info=True)
            _target_tag = ""
    logger.debug("git update apply: channel=%s pin=%s target=%s", _channel, _pin, _target_tag)

    # Nothing-to-advance probe FIRST (before the dirty gate): "Update & Restart"
    # with nothing to advance degrades to a plain restart instead of 409ing on
    # tree state that can't matter. Nightly rides commits (fast-forward when
    # behind); every other channel rides the resolved release tag (skip when we
    # are already on or past it, so an unreleased `main` commit never triggers a
    # move — the whole point of retiring pull-from-main).
    advance = False
    if _channel == "nightly":
        # The owner asked for this update: fetch the branch it would advance, then count.
        behind = await self_update.commits_behind_upstream(proj, fetch=True)
        if behind is None:
            note = "No upstream configured — restarting…"
        elif behind == 0:
            note = "Already up to date — restarting…"
        else:
            advance = True
            note = ""
    else:
        if not _target_tag:
            note = "No newer release found — restarting…"
        elif not self_update.moves_to(_target_tag, _local_version, _pin):
            sentence = self_update.up_to_date_sentence(_target_tag, _local_version, _pin)
            note = f"{sentence} — restarting…"
        else:
            advance = True
            note = ""
    if not advance:
        logger.info("Update apply: nothing to advance (%s) — restarting only", note)
        _auth_mode = _live_auth_mode(request)

        async def _restart_only() -> None:
            global _apply_in_flight
            try:
                # First (and only) step is `restarting` — the FE overlay renders
                # its simplified restart-only view for exactly this shape.
                state.push_update_progress("restarting", note)
                await _graceful_reexec(state, auth_mode=_auth_mode)
            except Exception:
                logger.exception("Restart (nothing-to-advance update) failed")
                state.push_update_progress("error", "Restart failed — check logs")
            finally:
                _apply_in_flight = False

        task = asyncio.create_task(_restart_only())
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)
        return web.json_response({"ok": True, "status": "restarting", "detail": note})

    # Clean-tree gate before advancing HEAD. Both advance mechanisms are
    # non-destructive on their own (checkout refuses to clobber, ff-only can't
    # rewrite history), but requiring a clean tree keeps one clear contract and a
    # readable message rather than a raw git refusal mid-apply.
    tracked = await asyncio.to_thread(self_update.git_tracked_changes, proj)
    if tracked:
        logger.warning("Update skipped: working tree has uncommitted changes")
        _apply_in_flight = False
        return web.json_response(
            {"error": "Working tree has uncommitted changes — commit or stash first"},
            status=409,
        )

    async def _apply() -> None:
        global _apply_in_flight
        try:
            if _channel == "nightly":
                # Nightly: advance the current branch by fast-forward only — never a
                # silent reset. A diverged branch fails cleanly and is left untouched.
                branch = await asyncio.to_thread(self_update.resolve_default_branch, proj)
                state.push_update_progress("pulling", f"Fast-forwarding {branch}…")
                ff = await asyncio.to_thread(self_update.git_fast_forward, proj, branch)
                if ff.returncode != 0:
                    state.push_update_progress(
                        "error", f"Could not fast-forward {branch} (diverged?) — no changes made"
                    )
                    return
            else:
                # Ride the release tag: fetch tags, then check the resolved tag out
                # (detaches HEAD onto that exact release). No pull, no reset.
                state.push_update_progress("pulling", f"Fetching release {_target_tag}…")
                fetched = await asyncio.to_thread(self_update.git_fetch_tags, proj)
                if fetched.returncode != 0:
                    state.push_update_progress("error", "git fetch --tags failed")
                    return
                checked = await asyncio.to_thread(self_update.git_checkout, proj, _target_tag)
                if checked.returncode != 0:
                    state.push_update_progress("error", f"git checkout {_target_tag} failed")
                    return

            # Reinstall the package into the RUNNING interpreter's env so new
            # dependencies land before the re-exec (sys.executable is the venv
            # python the gateway was launched with). Git ran at the repo root;
            # pip + the frontend build run at the package root (may be nested).
            pkg_root = self_update.package_root(proj)
            state.push_update_progress("installing", "Installing package…")
            from personalclaw._installer import installer_env

            pip_install = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "pip",
                "install",
                "-e",
                ".",
                "--quiet",
                cwd=pkg_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=installer_env(),
                # Own group: pip forks build backends / compilers, all inheriting these
                # pipes. See kill_timed_out.
                start_new_session=True,
            )
            try:
                _, pip_err = await asyncio.wait_for(pip_install.communicate(), timeout=400)
            except asyncio.TimeoutError:
                await kill_timed_out(pip_install)
                state.push_update_progress("error", "pip install timed out")
                return
            if pip_install.returncode != 0:
                logger.error(
                    "Update: pip install failed (rc=%d): %s",
                    pip_install.returncode,
                    mask_child_output(pip_err, limit=500),
                )
                state.push_update_progress("error", "pip install failed")
                return

            # Build frontend assets (npm ci && npm run build in <pkg>/web/)
            state.push_update_progress("building", "Building frontend…")
            await build_frontend_async(pkg_root, push_progress=state.push_update_progress)

            # Restart: the gateway stops the way every stop runs, then starts the new image
            state.push_update_progress("restarting", "Restarting server…")
            logger.info("Update complete — restarting")
            await _graceful_reexec(state, auth_mode=_live_auth_mode(request))
        except Exception:
            logger.exception("Update failed")
            state.push_update_progress("failed", "Update failed — check logs")
            state.push_refresh("update_failed")
        finally:
            # Reached on success too, since a restart returns once it is asked for; `_busy`
            # refuses the next apply while the gateway stops to restart.
            _apply_in_flight = False

    task = asyncio.create_task(_apply())
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    return web.json_response({"ok": True, "status": "updating"})


def _live_auth_mode(request: web.Request) -> str:
    """The running gateway's resolved auth mode (e.g. 'none' / 'local_token'), read
    from ``app['auth_cfg']``, for preserving across a re-exec (#46). Empty string if
    unavailable — the restart then inherits the env as-is (prior behavior)."""
    try:
        auth_cfg = request.app.get("auth_cfg")
        mode = getattr(auth_cfg, "mode", None)
        # AuthMode is a str-Enum; .value ('none'…) round-trips through from_env().
        return str(getattr(mode, "value", mode) or "")
    except Exception:
        return ""


async def _graceful_reexec(state: DashboardState, *, auth_mode: str = "") -> None:
    """Restart the gateway: stop it the way every stop runs, then start a fresh image in place.

    Shared by the update-apply restart, the standalone restart endpoint and the staged
    auto-update, so all three restart the same way. It used to re-exec from here, after saving
    chat history, closing sessions and stopping app processes — and nothing else, so every other
    step of a stop was skipped: time travel's pending commits were never written (the edits made
    just before a Restart had no history), and the durability loop, search indexer and sign-in
    tally were never stopped or flushed. It now asks for the restart
    (:mod:`personalclaw.restart_request`) and the gateway, when its own shutdown is done, starts
    the new image (``GatewayOrchestrator._finish``) — the new image keeps this PID.

    *auth_mode* is the LIVE ``AuthConfig.mode`` (an AuthMode str-enum: 'none' / 'local_token' / …)
    the caller read from ``request.app['auth_cfg']``, pinned into the new image's environment (#46)
    so a Restart re-applies code without ever changing whether auth is on or off."""
    exe = sys.executable
    if not os.path.isfile(exe) or not os.access(exe, os.X_OK):
        state.push_update_progress("error", "Cannot restart: invalid Python executable path")
        return
    from personalclaw.restart_request import request_restart

    await asyncio.sleep(0.5)  # let pending SSE/WS frames drain to clients
    request_restart(auth_mode=auth_mode)


async def api_restart(request: web.Request) -> web.Response:
    """POST /api/system/restart — bounce the gateway to apply committed backend
    changes WITHOUT advancing the checkout (the update-free counterpart of
    ``/api/update``).

    GET-style pre-flight: ``?probe=1`` returns the active-work snapshot (running
    agents + sessions) so the UI can warn before confirming, without restarting.
    A real POST kicks off the graceful re-exec in the background and returns
    immediately (the connection drops as the process restarts)."""
    state: DashboardState = request.app["state"]

    if request.query.get("probe"):
        return web.json_response({"ok": True, **state.active_work_snapshot()})

    logger.info("Manual gateway restart requested via /api/system/restart")
    state.push_update_progress("restarting", "Restarting gateway…")
    # Preserve the live auth mode across the re-exec (#46) — read it here where the
    # aiohttp app (and its resolved auth_cfg) is in scope.
    _auth_mode = _live_auth_mode(request)

    async def _restart() -> None:
        try:
            await _graceful_reexec(state, auth_mode=_auth_mode)
        except Exception:
            logger.exception("Manual restart failed")
            state.push_update_progress("error", "Restart failed — check logs")

    task = asyncio.create_task(_restart())
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    return web.json_response({"ok": True, "status": "restarting"})


async def api_update_cancel(request: web.Request) -> web.Response:
    """POST /api/update/cancel — dismiss a stuck/failed update overlay."""
    state: DashboardState = request.app["state"]
    state.clear_update_progress()
    state.push_update_progress("failed", "Update cancelled by user")
    # Give clients a moment to receive the failed event, then clear
    await asyncio.sleep(0.2)
    state.clear_update_progress()
    return web.json_response({"ok": True})


async def api_update_simulate(request: web.Request) -> web.Response:
    """POST /api/update/simulate — walk through update steps with delays.

    For local testing only. Cycles through each progress step with a
    configurable delay (default 2s per step).
    """
    state: DashboardState = request.app["state"]
    body = await json_object_body(request)

    # Simulate a pre-flight rejection (e.g. dirty working tree)
    if bool_field(body, "reject", default=False):
        msg = body.get(
            "reject_message", "Working tree has uncommitted changes — commit or stash first"
        )
        return web.json_response({"error": msg}, status=409)

    delay = body.get("delay", 2)
    fail_at = body.get("fail_at", "")  # optional: step name to fail at

    async def _sim() -> None:
        # Mirrors the real apply pipeline's step order (see api_update_apply).
        steps = [
            ("pulling", "Pulling latest changes…"),
            ("installing", "Installing package…"),
            ("building", "Building frontend…"),
            ("restarting", "Restarting server…"),
        ]
        for step, detail in steps:
            if fail_at and step == fail_at:
                state.push_update_progress("failed", f"Simulated failure at {step}")
                return
            state.push_update_progress(step, detail)
            await asyncio.sleep(delay)
        # Simulate completion — broadcast "done" so frontend clears the overlay
        state.push_update_progress("done", "Update complete")
        state.clear_update_progress()

    task = asyncio.create_task(_sim())
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    return web.json_response({"ok": True, "status": "simulating"})


# ── Logs SSE ──


_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def apply_log_level(level_name: str) -> bool:
    """Set the backend log level LIVE, for every sink of the gateway's log at once
    (``log_sinks.set_level``). Returns False for an unrecognized level so a caller
    can decide whether that is an error.

    Shared by ``POST /api/logs/level`` and the config PATCH path so a write to
    ``agent.log_level`` from EITHER Settings surface takes effect immediately
    rather than only at the next restart.
    """
    name = (level_name or "").upper()
    if name not in _LOG_LEVELS:
        return False
    log_sinks.set_level(_LOG_LEVELS[name])
    return True


async def api_log_level(request: web.Request) -> web.Response:
    """POST /api/logs/level — change the backend logger level at runtime.

    Also persists the new level to config so it survives restarts.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    raw_level = body.get("level")
    if not isinstance(raw_level, str):
        return web.json_response({"error": "level must be a string"}, status=400)
    level_name = raw_level.upper()
    if level_name not in _LOG_LEVELS:
        return web.json_response({"error": f"invalid level: {level_name}"}, status=400)
    # Apply live (every sink), then persist so it survives a
    # restart. level_name is already validated against _LOG_LEVELS above.
    apply_log_level(level_name)
    logger.info("Log level changed to %s via dashboard", level_name)

    # Persist to config so the level survives restarts.
    persisted = False
    try:
        cfg = AppConfig.load()
        cfg.agent.log_level = level_name
        cfg.save()
        persisted = True
    except Exception:
        logger.warning("Failed to persist log level to config", exc_info=True)

    return web.json_response({"ok": True, "level": level_name, "persisted": persisted})


async def api_log_level_get(request: web.Request) -> web.Response:
    """GET /api/logs/level — current backend logger level."""
    return web.json_response({"level": logging.getLevelName(log_sinks.level())})


class _QueueLogHandler(logging.Handler):
    """Logging handler that enqueues formatted log entries for SSE delivery. Masked by the
    formatter it is given (``security.MaskingFormatter``), like every log sink."""

    def __init__(self, queue: asyncio.Queue) -> None:  # type: ignore[type-arg]
        super().__init__()
        self._queue: asyncio.Queue[str] = queue  # type: ignore[type-arg]

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            data = json.dumps({"level": record.levelname, "msg": msg})
            self._queue.put_nowait(data)
        except Exception:
            pass


# ── Persistent log ring buffer ──

_LOG_RING_SIZE = 1000
_log_ring: collections.deque[str] = collections.deque(maxlen=_LOG_RING_SIZE)
_log_ring_handler: "_RingLogHandler | None" = None


class _RingLogHandler(logging.Handler):
    """Always-on handler that keeps the last N log entries in a ring buffer.

    Also pushes log events to WebSocket log subscribers. Masked by the formatter it is given
    (``security.MaskingFormatter``), like every log sink.
    """

    def __init__(
        self,
        ring: collections.deque[str],
        max_size: int = _LOG_RING_SIZE,
    ) -> None:
        super().__init__()
        self._ring = ring
        self._max = max_size
        self._state: DashboardState | None = None

    def set_state(self, state: DashboardState) -> None:
        """Attach DashboardState for WS log broadcasting."""
        self._state = state

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            data = json.dumps({"level": record.levelname, "msg": msg})
            self._ring.append(data)
            # Push to WS log subscribers through the state's ONE gated fan-out, which is
            # thread-safe and consults each socket's app permissions. This handler used
            # to walk `state._ws_log_subscribers` and write to every socket itself, so an
            # app-scoped socket that declared no `log` event still received the owner's
            # entire backend log stream (issue 2963).
            if self._state is not None:
                self._state.broadcast_ws_log_subscribers({"level": record.levelname, "msg": msg})
        except Exception:
            pass


def install_log_ring_handler() -> _RingLogHandler:
    """Make the persistent ring buffer one of the log's sinks (``log_sinks.attach``) — the
    history Diagnostics replays on connect. One buffer per process; installing it again only
    attaches that one."""
    global _log_ring_handler  # noqa: PLW0603
    if _log_ring_handler is None:
        handler = _RingLogHandler(_log_ring, _LOG_RING_SIZE)
        handler.setFormatter(MaskingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        _log_ring_handler = handler
    log_sinks.attach(_log_ring_handler)
    return _log_ring_handler


async def api_logs(request: web.Request) -> web.StreamResponse:
    """GET /api/logs — SSE stream of live log entries.

    Query params:
      - ``lines``: max ring-buffer entries to replay on connect (default 200, max 1000).

    On connect, replays the last *lines* log entries from the ring buffer
    so the client sees history even if the Logs page wasn't open.
    """
    try:
        lines_cap = min(max(int(request.query.get("lines", "200")), 1), _LOG_RING_SIZE)
    except (TypeError, ValueError):
        lines_cap = 200
    resp = web.StreamResponse()
    resp.content_type = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    try:
        await resp.prepare(request)
    except (ConnectionResetError, ClientConnectionResetError):
        return resp

    # First byte immediately: on a quiet logger with an empty ring the stream
    # otherwise stays byte-silent until the 30s keepalive, and a client (or an
    # intermediary) cannot tell "connected and idle" from "hung". A comment
    # frame is invisible to EventSource consumers — same idiom as the
    # keepalive below.
    try:
        await resp.write(b": connected\n\n")
    except (ConnectionResetError, ClientConnectionResetError):
        return resp

    # Replay buffered history first (capped by ?lines=N)
    ring_snapshot = list(_log_ring)
    for data in ring_snapshot[-lines_cap:]:
        try:
            await resp.write(f"data: {data}\n\n".encode())
        except (ConnectionResetError, ClientConnectionResetError):
            return resp

    log_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
    handler = _QueueLogHandler(log_queue)
    handler.setFormatter(MaskingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log_sinks.attach(handler)
    try:
        while not shutdown_event.is_set():
            # Drain any queued log entries
            while not log_queue.empty():
                try:
                    data = log_queue.get_nowait()
                    await resp.write(f"data: {data}\n\n".encode())
                except asyncio.QueueEmpty:
                    break

            # Wait for new entries or keepalive timeout, or the stop
            try:
                data = await next_unless_stopping(log_queue.get(), 30)
                await resp.write(f"data: {data}\n\n".encode())
            except asyncio.TimeoutError:
                await resp.write(b": keepalive\n\n")
    except (
        ConnectionResetError,
        ClientConnectionResetError,
        asyncio.CancelledError,
        GatewayStopping,
    ):
        pass
    finally:
        log_sinks.detach(handler)
    return resp
