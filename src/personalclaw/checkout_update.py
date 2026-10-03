"""The update of a source checkout: move it to its release, install it and build its frontend, or
put it back where it was.

Every way a checkout updates runs :func:`update_checkout`: the dashboard's Update and the staged
auto-update (``dashboard.handlers.updates.start_checkout_update``), and ``personalclaw update``
(``cli_server``). Each decides first whether there is anything to move to, refuses what it must
(uncommitted tracked edits, the environment's installer missing), and shows the steps its own way:
the dashboard over ``update_progress``, the CLI on the terminal. What follows is its own as well:
the gateway restarts into the new release, and the CLI says to restart it.

**An update that fails once the checkout has moved puts it back.** The checkout's sources are the
code PersonalClaw runs (an editable install), and its environment holds the packages that code
needs. An install that does not finish, whatever stopped it (the network, a resolver conflict, a
full disk, its deadline, a stop half-way), leaves the environment with the packages it had. A
checkout left on the new release over them starts the new code on the old packages, and may not
start at all. So the checkout goes back to the exact commit, and the branch, it was on
(``self_update.git_position``, read before anything moves: an update that cannot read it does not
start), and the failure says what failed and that nothing changed. Putting it back never forces:
git will not overwrite a change in the tree, and when it does not go back the failure says where the
checkout is and the one command that puts it back.

The install comes before the frontend build: a build that ran first would leave the new release's
dashboard behind a checkout put back on the old release.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import subprocess
import sysconfig
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from personalclaw import self_update
from personalclaw.cancellation import kill_timed_out
from personalclaw.frontend import build_frontend_async
from personalclaw.security import mask_child_output
from personalclaw.self_update import CheckoutPosition

logger = logging.getLogger(__name__)

#: How an update tells whoever started it what it is doing: ``progress(step, detail)``, where the
#: steps are ``pulling``, ``installing``, ``building``, ``warning`` (a note from the frontend
#: build) and ``error``.
Progress = Callable[[str, str], None]

#: How long the install may run before it is stopped and reaped, named so a test can inject one
#: instead of sleeping on it. pip and uv both run the package's build backend, and a dependency
#: the release changed may have to be downloaded.
_INSTALL_TIMEOUT = 400.0

#: How long a stopping gateway waits for an update to stop its install and put the checkout back
#: (:func:`stop`). Both take a moment; the bound is for a git that hangs.
_STOP_TIMEOUT = 30.0

#: The update moving or installing the checkout now, which :func:`stop` stops.
_running: "asyncio.Task[Any] | None" = None

_T = TypeVar("_T")


async def update_checkout(proj: str, target_tag: str, progress: Progress) -> str:
    """Move the checkout at *proj* to release *target_tag*, install it into PersonalClaw's own
    environment with the tool that made it, and build its frontend. No *target_tag* fast-forwards
    the branch the checkout is on (the ``nightly`` channel).

    Returns ``""`` once that is done. Otherwise it returns what failed and where that left things,
    in one sentence for the owner to read: the checkout was put back, and nothing changed; or what
    is not as it was, and how to put it right.

    Raises only a cancellation, which stops the install and puts the checkout back first.
    """
    global _running
    from personalclaw._installer import NoInstallerError, require_own_installer

    _running = asyncio.current_task()
    try:
        # Settled before anything moves: where to put the checkout back, and that the tool that
        # installs it is there.
        before = await _in_thread(self_update.git_position, proj)
        if before is None:
            return (
                "Nothing was changed: git could not tell which commit the checkout is on, so the "
                "update could not have put it back had the install failed."
            )
        try:
            tool = require_own_installer()
        except NoInstallerError as exc:
            return str(exc)
        label = "uv sync" if tool == "uv" else "pip install"
        pkg_root = self_update.package_root(proj)
        environment = await _in_thread(_environment)

        try:
            failed = await _move(proj, target_tag, progress)
            if not failed:
                progress("installing", "Installing package…")
                failed = await _install(pkg_root, label)
        except asyncio.CancelledError:
            # Stopped half-way: the install has been stopped (`_install`), and the checkout goes
            # back here, in this thread, where nothing can interrupt it.
            where = _put_back(proj, before, environment)
            logger.warning("Update stopped before it finished. %s", where)
            progress("error", f"The update was stopped before it finished. {where}")
            raise
        except Exception:
            logger.exception("Update failed")
            failed = "The update stopped on an unexpected error (see the log)"
        if failed:
            where = await _in_thread(_put_back, proj, before, environment)
            return f"{failed.rstrip(' .:;,')}. {where}"
    finally:
        _running = None

    progress("building", "Building frontend…")
    await build_frontend_async(pkg_root, push_progress=progress)
    return ""


async def stop() -> None:
    """Stop the update moving or installing the checkout, if one is, and wait while it puts the
    checkout back: the gateway is stopping, and its next start must run the release its
    environment has. ``GatewayOrchestrator._shutdown`` asks this; a stop of ``personalclaw
    update`` (Ctrl-C) cancels the same update directly."""
    running = _running
    if running is None or running.done() or running is asyncio.current_task():
        return
    running.cancel()
    done, _ = await asyncio.wait({running}, timeout=_STOP_TIMEOUT)
    if not done:
        logger.warning("The update did not stop within %.0fs", _STOP_TIMEOUT)


async def _move(proj: str, target_tag: str, progress: Progress) -> str:
    """Check release *target_tag* out, or fast-forward the checked-out branch: ``""`` once the
    checkout has moved, else what failed. Neither one moves over a change in the tree, nor drops a
    commit."""
    if not target_tag:
        branch = await _in_thread(self_update.resolve_default_branch, proj)
        progress("pulling", f"Fast-forwarding {branch}…")
        ff = await _in_thread(self_update.git_fast_forward, proj, branch)
        return "" if ff.returncode == 0 else f"Could not fast-forward {branch}{_said(ff.stderr)}"
    progress("pulling", f"Fetching release {target_tag}…")
    fetched = await _in_thread(self_update.git_fetch_tags, proj)
    if fetched.returncode != 0:
        return f"git fetch --tags failed{_said(fetched.stderr)}"
    checked = await _in_thread(self_update.git_checkout, proj, target_tag)
    if checked.returncode != 0:
        return f"git checkout {target_tag} failed{_said(checked.stderr)}"
    return ""


async def _install(pkg_root: str, label: str) -> str:
    """Install the checkout at *pkg_root* into PersonalClaw's own environment, with the tool that
    made it: ``""`` once it is installed, else what failed. The command is read here, after the
    move, because it names the extras the new release declares. A cancelled install is stopped
    before the cancellation goes on."""
    from personalclaw._installer import NoInstallerError, checkout_install_argv, installer_env

    try:
        argv = checkout_install_argv(pkg_root)
    except NoInstallerError:
        return f"{label} could not start: the tool that made PersonalClaw's environment is gone"
    try:
        install = await asyncio.create_subprocess_exec(
            *argv,
            cwd=pkg_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=installer_env(),
            # Own group: the installer forks build backends and compilers, all inheriting
            # these pipes. See kill_timed_out.
            start_new_session=True,
        )
    except OSError as exc:
        return f"{label} could not start: {exc}"
    try:
        _, err = await asyncio.wait_for(install.communicate(), timeout=_INSTALL_TIMEOUT)
    except asyncio.TimeoutError:
        # The deadline is the WHOLE teardown: `wait_for` cancels the read but leaves the
        # installer and its build backend running. kill_timed_out is the one owner of that.
        await kill_timed_out(install)
        return f"{label} timed out after {_INSTALL_TIMEOUT:g}s"
    except asyncio.CancelledError:
        await kill_timed_out(install)
        raise
    if install.returncode != 0:
        # The failure's sentence carries the reason; this is the installer's own words, for -v.
        logger.info(
            "Update: %s exited %d: %s", label, install.returncode, mask_child_output(err, limit=500)
        )
        return f"{label} failed{_said(err)}"
    return ""


def _put_back(proj: str, before: CheckoutPosition, environment: frozenset[str]) -> str:
    """Put the checkout back on *before* if the update moved it, and say where that leaves things:
    the half of a failure's sentence after what failed."""
    moved = self_update.git_position(proj) != before
    restored = self_update.git_restore(proj, before) if moved else None
    now = self_update.git_position(proj)
    tracked = self_update.git_tracked_changes(proj)
    if now != before or tracked:
        return _not_back(proj, before, now, tracked, restored)
    changed = sorted({name.rsplit("-", 1)[0] for name in environment ^ _environment()})
    if changed:
        on = "back on" if moved else "on"
        return (
            f"The checkout is {on} {before}, but the install had already changed "
            f"{_listed(changed)} in PersonalClaw's environment; update again once that is fixed."
        )
    if moved:
        return f"Nothing was changed: the checkout is back on {before}."
    return "Nothing was changed."


def _not_back(
    proj: str,
    before: CheckoutPosition,
    now: CheckoutPosition | None,
    tracked: list[str],
    restored: subprocess.CompletedProcess[str] | None,
) -> str:
    """Where a checkout that is not back as it was is, and the one command that puts it back.

    The update started on a clean tree, so a change in it now was made while it ran. The command
    discards such changes, and says so, because git refuses to put the checkout back over them."""
    if now == before:
        here = f"The checkout is on {before}"
    else:
        reason = ""
        if restored is not None and restored.returncode != 0:
            reason = _first_line(restored.stderr)
        why = f" ({reason})" if reason else ""
        where = f"it is on {now}" if now is not None else "git cannot tell which commit it is on"
        here = f"The checkout could not be put back on {before}{why}, so {where}"
    if tracked:
        here += f", with changes to {len(tracked)} file{'' if len(tracked) == 1 else 's'}"
    command = shlex.join(["git", "-C", proj, *before.restore_args(force=bool(tracked))])
    discarding = " (discarding those changes)" if tracked else ""
    return (
        f"{here}. To put it back on {before} before PersonalClaw next starts{discarding}, "
        f"run: {command}"
    )


def _site_dirs() -> list[str]:
    """Where PersonalClaw's environment keeps what is installed in it: the running interpreter's
    ``site-packages``."""
    paths = sysconfig.get_paths()
    return sorted({paths["purelib"], paths["platlib"]})


def _environment() -> frozenset[str]:
    """What is installed in PersonalClaw's environment, as an install changes it: the metadata
    folder of each distribution (``name-version.dist-info``), which an install adds, removes or
    renames. Read before the install, so a failure can say whether the environment is as it was."""
    found: set[str] = set()
    for folder in _site_dirs():
        try:
            found.update(p.name[: -len(".dist-info")] for p in Path(folder).glob("*.dist-info"))
        except OSError:
            continue
    return frozenset(found)


def _listed(names: list[str]) -> str:
    if len(names) > 3:
        return f"{', '.join(names[:3])} and {len(names) - 3} more"
    if len(names) > 1:
        return f"{', '.join(names[:-1])} and {names[-1]}"
    return names[0]


def _said(output: str | bytes | None) -> str:
    """``": <the reason>"`` from what a failed git or installer run printed, to follow what
    failed, or ``""`` when it printed nothing: masked like every view of a child's output, in one
    line, its reason first (``self_update.installer_error_summary``)."""
    reason = self_update.installer_error_summary(
        mask_child_output(output, limit=None, one_line=False)
    )
    return f": {reason}" if reason else ""


def _first_line(output: str | bytes | None) -> str:
    """The first line git printed, masked: where it says why it refused."""
    text = mask_child_output(output, limit=None, one_line=False)
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


async def _in_thread(fn: Callable[..., _T], *args: Any) -> _T:
    """``fn(*args)`` in a thread, outlasting a cancellation: a thread cannot be stopped, so an
    update that is stopped waits for the git it started before it puts the checkout back."""
    running = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(running)
    except asyncio.CancelledError:
        await asyncio.wait({running})
        raise
