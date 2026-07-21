"""Update check/apply, log level, ring buffer, and SSE stream handlers."""

import asyncio
import collections
import json
import logging
import os
import re
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

from personalclaw import __version__ as _local_version
from personalclaw import shutdown_event
from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import AppConfig, config_path
from personalclaw.dashboard.state import DashboardState
from personalclaw.frontend import build_frontend_async

logger = logging.getLogger(__name__)

# ── Update ──

# Cached update check result
_update_info: dict[str, object] = {"available": False, "changes": "", "checked": False}
_UPDATE_CHECK_INTERVAL = 43200  # 12 hours
_last_update_check: float = 0.0


def get_update_info() -> dict[str, object]:
    """Return a copy of the cached update-check state."""
    return dict(_update_info)


async def api_update_check(request: web.Request) -> web.Response:
    """GET /api/update/check — kind-aware update check (contract C2).

    Returns the tag-driven cross-kind status ({kind, current, latest,
    update_available, commits_behind, apply_method, instructions}) merged with
    the legacy git changelog-diff fields (available/changes) for backward
    compatibility with the existing panel. The git kind still runs the
    commits-behind probe; every kind gets the release-tag comparison.
    """
    from personalclaw.dashboard.handlers.updates_kind import build_update_status

    await _do_update_check()
    cfg = AppConfig.load()
    try:
        status = await build_update_status(_local_version)
    except Exception:
        logger.debug("build_update_status failed; returning legacy view", exc_info=True)
        status = {}
    # Prefer the tag-driven `update_available` when we have a latest tag; else
    # fall back to the git changelog-diff `available` signal (offline git view).
    merged: dict[str, object] = {**_update_info, **status}
    if status.get("latest"):
        merged["available"] = bool(status.get("update_available"))
    merged["auto_update"] = cfg.auto_update
    merged["update_dev_mode"] = cfg.dashboard.update_dev_mode
    merged["version"] = _local_version
    return web.json_response(merged)


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse version string to tuple for safe numeric comparison."""
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0,)


async def _do_update_check() -> None:
    """Run git fetch and compare HEAD with remote."""
    global _last_update_check

    def _redact(text: str) -> str:
        from personalclaw.security import redact_credentials, redact_exfiltration_urls

        text, _ = redact_credentials(text)
        text, _ = redact_exfiltration_urls(text)
        return text

    proj = os.environ.get("PERSONALCLAW_PROJECT_DIR", "")
    if not proj:
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "fetch",
            "--quiet",
            cwd=proj,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, fetch_err = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.communicate()
            logger.warning("git fetch timed out")
            return
        if proc.returncode != 0:
            logger.warning(
                "git fetch failed (rc=%s): %s",
                proc.returncode,
                (fetch_err or b"").decode(errors="replace").strip(),
            )
            return

        local = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "HEAD",
            cwd=proj,
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
            return
        remote = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "@{u}",
            cwd=proj,
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
            return

        local_sha = local_out.decode(errors="replace").strip()
        remote_sha = remote_out.decode(errors="replace").strip()

        # Check version: compare remote (or on-disk if already pulled) vs running
        available = False
        remote_version = ""
        target_sha = remote_sha if local_sha != remote_sha else local_sha
        if local_sha and remote_sha:
            # The package lives under PersonalClaw/src after the core/app workspace
            # split (Slice 0); the git blob path is repo-root-relative.
            show = await asyncio.create_subprocess_exec(
                "git",
                "show",
                f"{target_sha}:PersonalClaw/src/personalclaw/__init__.py",
                cwd=proj,
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
                return
            m = re.search(r'__version__\s*=\s*"(.+?)"', show_out.decode(errors="replace"))
            if m:
                remote_version = m.group(1)
            available = (
                _version_tuple(remote_version) > _version_tuple(_local_version)
                if remote_version
                else False
            )

        changes = ""
        if available:
            diff_base = f"v{_local_version}" if local_sha == remote_sha else local_sha
            diff = await asyncio.create_subprocess_exec(
                "git",
                "diff",
                f"{diff_base}..{target_sha}",
                "--",
                "CHANGELOG.md",
                cwd=proj,
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
                return
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
        _last_update_check = time.time()
    except Exception:
        logger.debug("Update check failed", exc_info=True)


async def api_update_auto(request: web.Request) -> web.Response:
    """POST /api/update/auto — toggle auto-update on/off."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        # Config's `auto_update` gates an unattended git-reset + rebuild + restart
        # (gateway._check_for_updates); a truthy junk value ("banana") persisted
        # here would silently ENABLE that. Booleans only.
        return web.json_response({"error": "enabled must be a boolean"}, status=400)
    # Read, modify, write config
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}
    data["auto_update"] = enabled
    atomic_write(path, json.dumps(data, indent=2) + "\n", fsync=True)
    return web.json_response({"ok": True, "auto_update": enabled})


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


def _package_root(proj: str) -> str:
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


async def _commits_behind_upstream(proj: str) -> int | None:
    """How many commits the configured upstream is ahead of HEAD, or ``None``
    when no upstream exists (or the probe fails) — i.e. a ``git pull`` cannot
    produce anything. Runs a best-effort ``git fetch`` first (short timeout,
    failure tolerated — offline, the count then reflects the last-fetched
    view, which is also what drove the "update available" signal)."""
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


# In-flight guard: only one update apply may run at a time. A plain bool is
# race-free here because the handler sets it synchronously (no await between
# check and set) on the single-threaded event loop; the background task clears
# it in a finally. Concurrent POST /api/update returns 409 instead of spawning
# a second pull/build/restart pipeline against the same working tree.
_apply_in_flight = False


async def _apply_pip_update(request: web.Request, state: DashboardState) -> web.Response:
    """Upgrade a pip/uv/pipx install in place, then graceful re-exec (T4.3).

    Runs ``pip install -U personalclaw==<tag>`` (pinned to the latest release
    tag when known; unpinned ``-U`` otherwise) using ``sys.executable`` so the
    upgrade lands in the SAME interpreter/prefix the gateway runs from — mirrors
    the git path's ``pip install -e .`` step. No web build: the wheel already
    carries the SPA. The 409 concurrent-apply guard is shared with the git path.
    """
    global _apply_in_flight

    if _apply_in_flight:
        return web.json_response({"error": "An update is already in progress"}, status=409)
    _apply_in_flight = True
    state.push_refresh("updating")

    from personalclaw.dashboard.handlers.updates_kind import build_update_status

    try:
        status = await build_update_status(_local_version)
    except Exception:
        status = {}
    latest = str(status.get("latest") or "")
    spec = f"personalclaw=={latest}" if latest else "personalclaw"
    auth_mode = _live_auth_mode(request)

    async def _apply() -> None:
        global _apply_in_flight
        try:
            state.push_update_progress("installing", f"Upgrading {spec}…")
            pip_up = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "pip",
                "install",
                "-U",
                spec,
                "--quiet",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, pip_err = await asyncio.wait_for(pip_up.communicate(), timeout=400)
            except asyncio.TimeoutError:
                try:
                    pip_up.kill()
                except ProcessLookupError:
                    pass
                await pip_up.communicate()
                state.push_update_progress("error", "pip upgrade timed out")
                return
            if pip_up.returncode != 0:
                logger.error(
                    "pip self-update failed (rc=%d): %s",
                    pip_up.returncode,
                    (pip_err or b"").decode(errors="replace")[:500],
                )
                state.push_update_progress("error", "pip upgrade failed")
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
    """POST /api/update — git pull, reinstall, rebuild, restart gateway.

    Public pipeline: ``git pull`` → ``pip install -e .`` (same interpreter)
    → frontend rebuild (``npm ci && npm run build`` in ``web/``) → graceful
    re-exec. Progress is broadcast as ``update_progress`` WS events with steps
    ``pulling`` → ``installing`` → ``building`` → ``restarting``
    (→ ``error``/``failed`` on failure).

    Graceful degradation: when there is NOTHING to pull (no upstream
    configured, or the upstream has zero new commits) the pipeline
    short-circuits straight to the ``restarting`` step — the user asked for
    "Update & Restart", and a restart is still meaningful (applies committed
    local changes). The dirty-tree gate only guards a REAL pull (pulling onto
    a dirty tree is dangerous); if nothing will be pulled, dirtiness doesn't
    matter, so the upstream probe runs BEFORE the dirty check.
    """
    global _apply_in_flight
    state: DashboardState = request.app["state"]

    from personalclaw.dashboard.handlers.updates_kind import (
        build_update_status,
        detect_install_kind,
    )

    kind = detect_install_kind()

    # Container / desktop: no in-place apply. Return the structured instructions
    # (honest commands beat pretending) — the panel renders them. No in-flight
    # slot is claimed because nothing runs here.
    if kind in ("container", "desktop"):
        try:
            status = await build_update_status(_local_version)
        except Exception:
            status = {"kind": kind, "instructions": [], "apply_method": ""}
        return web.json_response(
            {
                "ok": True,
                "status": "instructions",
                "kind": kind,
                "apply_method": status.get("apply_method", ""),
                "instructions": status.get("instructions", []),
                "detail": (
                    "This is a container install — update by pulling the new "
                    "image and recreating."
                    if kind == "container"
                    else "This is a desktop install — the app updates itself."
                ),
            }
        )

    # pip / uv / pipx: upgrade the wheel into the RUNNING interpreter's prefix,
    # then graceful re-exec. No web build (assets ship in the wheel).
    if kind == "pip":
        return await _apply_pip_update(request, state)

    # git: the existing source-tree pipeline (below).
    proj = os.environ.get("PERSONALCLAW_PROJECT_DIR", "")
    if not proj:
        return web.json_response({"error": "PERSONALCLAW_PROJECT_DIR not set"}, status=400)

    # Ride releases by default vs track every commit: dashboard.update_dev_mode
    # selects the git updater's cadence. The existing commits-behind probe below
    # already short-circuits to a plain restart when there is nothing new to
    # pull, so tag-vs-commit gating is advisory here — the tag-driven
    # availability signal is surfaced by GET /api/update/check (build_update_status);
    # the apply path stays hermetic (no network) and reuses the pull pipeline.
    _dev_mode = AppConfig.load().dashboard.update_dev_mode
    logger.debug("git update apply: update_dev_mode=%s", _dev_mode)

    if _apply_in_flight:
        return web.json_response(
            {"error": "An update is already in progress"},
            status=409,
        )
    # Claim the in-flight slot BEFORE the first await below — otherwise two
    # concurrent POSTs could both pass the check while one parks on the
    # dirty-tree subprocess. Every return path from here must release it.
    _apply_in_flight = True

    # Signal updating state via SSE
    state.push_refresh("updating")

    # Nothing-to-pull probe FIRST (before the dirty gate): "Update & Restart"
    # with no upstream or an already-up-to-date checkout degrades gracefully
    # to a plain restart instead of 409ing on tree state that can't matter.
    behind = await _commits_behind_upstream(proj)
    if behind is None or behind == 0:
        note = (
            "No upstream configured — restarting…"
            if behind is None
            else "Already up to date — restarting…"
        )
        logger.info("Update apply: nothing to pull (%s) — restarting only", note)
        _auth_mode = _live_auth_mode(request)

        async def _restart_only() -> None:
            global _apply_in_flight
            try:
                # First (and only) step is `restarting` — the FE overlay renders
                # its simplified restart-only view for exactly this shape.
                state.push_update_progress("restarting", note)
                await _graceful_reexec(state, auth_mode=_auth_mode)
            except Exception:
                logger.exception("Restart (nothing-to-pull update) failed")
                state.push_update_progress("error", "Restart failed — check logs")
            finally:
                _apply_in_flight = False

        task = asyncio.create_task(_restart_only())
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)
        return web.json_response({"ok": True, "status": "restarting", "detail": note})

    # Check for dirty working tree before updating
    dirty = await asyncio.create_subprocess_exec(
        "git",
        "status",
        "--porcelain",
        cwd=proj,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        dirty_out, _ = await asyncio.wait_for(dirty.communicate(), timeout=10)
    except asyncio.TimeoutError:
        try:
            dirty.kill()
        except ProcessLookupError:
            pass
        await dirty.communicate()
        _apply_in_flight = False
        return web.json_response(
            {"error": "Timed out checking working tree status"},
            status=500,
        )
    if dirty_out and dirty_out.strip():
        logger.warning("Update skipped: working tree has uncommitted changes")
        _apply_in_flight = False
        return web.json_response(
            {"error": "Working tree has uncommitted changes — commit or stash first"},
            status=409,
        )

    async def _apply() -> None:
        global _apply_in_flight
        try:
            # git pull
            state.push_update_progress("pulling", "Pulling latest changes…")
            pull = await asyncio.create_subprocess_exec(
                "git",
                "pull",
                cwd=proj,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(pull.communicate(), timeout=60)
            except asyncio.TimeoutError:
                try:
                    pull.kill()
                except ProcessLookupError:
                    pass
                await pull.communicate()
                state.push_update_progress("error", "git pull timed out")
                return
            if pull.returncode != 0:
                state.push_update_progress("error", "git pull failed")
                return

            # Reinstall the package into the RUNNING interpreter's env so new
            # dependencies land before the re-exec (sys.executable is the venv
            # python the gateway was launched with). Git ran at the repo root;
            # pip + the frontend build run at the package root (may be nested).
            pkg_root = _package_root(proj)
            state.push_update_progress("installing", "Installing package…")
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
            )
            try:
                _, pip_err = await asyncio.wait_for(pip_install.communicate(), timeout=400)
            except asyncio.TimeoutError:
                try:
                    pip_install.kill()
                except ProcessLookupError:
                    pass
                await pip_install.communicate()
                state.push_update_progress("error", "pip install timed out")
                return
            if pip_install.returncode != 0:
                logger.error(
                    "Update: pip install failed (rc=%d): %s",
                    pip_install.returncode,
                    (pip_err or b"").decode(errors="replace")[:500],
                )
                state.push_update_progress("error", "pip install failed")
                return

            # Build frontend assets (npm ci && npm run build in <pkg>/web/)
            state.push_update_progress("building", "Building frontend…")
            await build_frontend_async(pkg_root, push_progress=state.push_update_progress)

            # Restart: save history + clean up sessions then exec the same process
            state.push_update_progress("restarting", "Restarting server…")
            logger.info("Update complete — saving history and cleaning up before restart")
            await _graceful_reexec(state, auth_mode=_live_auth_mode(request))
        except Exception:
            logger.exception("Update failed")
            state.push_update_progress("failed", "Update failed — check logs")
            state.push_refresh("update_failed")
        finally:
            # Reached on every failure path (and harmlessly never observed on
            # success — the process image is replaced by the re-exec above).
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
    """Save history, close sessions, drain frames, then exec a fresh gateway
    in-place. Shared by the update-apply restart and the standalone restart
    endpoint so both use the identical proven sequence. ``os.execv`` replaces
    this process image (same PID) — the kernel hands the listen socket to the
    new image after it binds, so there is no window where nothing is running.
    Uses ``-m personalclaw`` (not ``sys.argv[0]``) because a build-artifact
    clean may have removed the original ``__main__`` path.

    Preserves the resolved AUTH MODE across the re-exec (#46): the gateway reads
    ``PERSONALCLAW_AUTH_MODE`` from the env at boot, but the original launcher's env
    may not survive (e.g. the parent shell that exported ``=none`` exits, the
    process gets reparented to PID 1, and a plain ``os.execv`` that relied on that
    var being in ``os.environ`` would come back token-required). That's a SURPRISING
    security-posture flip on a Restart. So snapshot the LIVE mode from the running
    app's ``auth_cfg`` and pass it explicitly via ``os.execve`` — a Restart re-applies
    code without ever changing whether auth is on/off."""
    exe = sys.executable
    if not os.path.isfile(exe) or not os.access(exe, os.X_OK):
        state.push_update_progress("error", "Cannot restart: invalid Python executable path")
        return
    from personalclaw.dashboard.chat import save_all_sessions_to_history

    # Snapshot the currently-active auth mode into the child's env so the re-exec'd
    # gateway resolves the SAME posture regardless of the inherited env (#46).
    # ``auth_mode`` is the live ``AuthConfig.mode`` (an AuthMode str-enum: 'none' /
    # 'local_token' / …) passed by the caller from ``request.app['auth_cfg']``;
    # AuthConfig.from_env() reads PERSONALCLAW_AUTH_MODE lowercased, so the enum
    # value round-trips exactly.
    child_env = dict(os.environ)
    if auth_mode:
        child_env["PERSONALCLAW_AUTH_MODE"] = str(auth_mode)

    try:
        save_all_sessions_to_history(state)
    except Exception:
        logger.debug("History save before restart failed", exc_info=True)
    try:
        await state.sessions.close_all()
    except Exception:
        logger.debug("Session cleanup before restart failed", exc_info=True)
    sys.stdout.flush()
    sys.stderr.flush()
    await asyncio.sleep(0.5)  # let pending SSE/WS frames drain to clients
    os.execve(exe, [exe, "-m", "personalclaw"] + sys.argv[1:], child_env)


def _active_work_snapshot(state: DashboardState) -> dict[str, int]:
    """Count in-flight work a restart would interrupt, for the confirm gate:
    running (not-done) background subagents + live chat sessions."""
    running_agents = 0
    subs = getattr(state, "subagents", None)
    if subs is not None:
        try:
            running_agents = sum(1 for a in subs.all_agents if not a.done)
        except Exception:
            running_agents = 0
    try:
        sessions = len(state.sessions._sessions)
    except Exception:
        sessions = 0
    return {"running_agents": running_agents, "sessions": sessions}


async def api_restart(request: web.Request) -> web.Response:
    """POST /api/system/restart — bounce the gateway to apply committed backend
    changes WITHOUT a git pull (the update-free counterpart of ``/api/update``).

    GET-style pre-flight: ``?probe=1`` returns the active-work snapshot (running
    agents + sessions) so the UI can warn before confirming, without restarting.
    A real POST kicks off the graceful re-exec in the background and returns
    immediately (the connection drops as the process restarts)."""
    state: DashboardState = request.app["state"]

    if request.query.get("probe"):
        return web.json_response({"ok": True, **_active_work_snapshot(state)})

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
    try:
        body = await request.json()
    except Exception:
        body = {}

    # Simulate a pre-flight rejection (e.g. dirty working tree)
    if body.get("reject"):
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
    # Apply to the personalclaw logger AND the app-bundle logger roots (they
    # log under their own namespaces — installed_logger_roots()), AND to the
    # persistent RotatingFileHandler(s), whose boot-time level would otherwise
    # keep filtering gateway.log at the old verbosity ("live" change was
    # SSE-only before this).
    from personalclaw.apps.catalog import installed_logger_roots

    for _lname in ("personalclaw", *installed_logger_roots()):
        _lg = logging.getLogger(_lname)
        _lg.setLevel(_LOG_LEVELS[level_name])
        for _h in _lg.handlers:
            if isinstance(_h, RotatingFileHandler):
                _h.setLevel(_LOG_LEVELS[level_name])
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
    root = logging.getLogger("personalclaw")
    return web.json_response({"level": logging.getLevelName(root.level)})


class _QueueLogHandler(logging.Handler):
    """Logging handler that enqueues formatted log entries for SSE delivery."""

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
_log_ring_handler_installed = False
_log_ring_handler: "_RingLogHandler | None" = None


async def _safe_ws_send(ws: web.WebSocketResponse, msg: str, state: DashboardState) -> None:
    """Send to WS, removing dead subscribers on failure."""
    try:
        await ws.send_str(msg)
    except Exception:
        state._ws_log_subscribers.discard(ws)


class _RingLogHandler(logging.Handler):
    """Always-on handler that keeps the last N log entries in a ring buffer.

    Also pushes log events to WebSocket log subscribers.
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
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_state(self, state: DashboardState) -> None:
        """Attach DashboardState for WS log broadcasting."""
        self._state = state
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            data = json.dumps({"level": record.levelname, "msg": msg})
            self._ring.append(data)
            # Push to WS log subscribers (thread-safe via call_soon_threadsafe)
            if self._state and self._loop and self._state._ws_log_subscribers:
                ws_msg = json.dumps(
                    {"type": "log", "data": {"level": record.levelname, "msg": msg}}
                )
                for ws in list(self._state._ws_log_subscribers):
                    try:
                        self._loop.call_soon_threadsafe(
                            self._loop.create_task,
                            _safe_ws_send(ws, ws_msg, self._state),
                        )
                    except RuntimeError:
                        pass
        except Exception:
            pass


def install_log_ring_handler() -> _RingLogHandler | None:
    """Install the persistent ring buffer handler (call once at startup)."""
    global _log_ring_handler_installed, _log_ring_handler  # noqa: PLW0603
    if _log_ring_handler_installed:
        return _log_ring_handler
    _log_ring_handler_installed = True
    handler = _RingLogHandler(_log_ring, _LOG_RING_SIZE)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger("personalclaw").addHandler(handler)
    _log_ring_handler = handler
    return handler


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

    # Replay buffered history first (capped by ?lines=N)
    ring_snapshot = list(_log_ring)
    for data in ring_snapshot[-lines_cap:]:
        try:
            await resp.write(f"data: {data}\n\n".encode())
        except (ConnectionResetError, ClientConnectionResetError):
            return resp

    log_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
    handler = _QueueLogHandler(log_queue)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("personalclaw")
    root.addHandler(handler)
    try:
        while not shutdown_event.is_set():
            # Drain any queued log entries
            while not log_queue.empty():
                try:
                    data = log_queue.get_nowait()
                    await resp.write(f"data: {data}\n\n".encode())
                except asyncio.QueueEmpty:
                    break

            # Wait for new entries or keepalive timeout
            try:
                data = await asyncio.wait_for(log_queue.get(), timeout=30)
                await resp.write(f"data: {data}\n\n".encode())
            except asyncio.TimeoutError:
                await resp.write(b": keepalive\n\n")
    except (ConnectionResetError, ClientConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        root.removeHandler(handler)
    return resp
