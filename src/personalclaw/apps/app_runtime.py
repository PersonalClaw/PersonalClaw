"""What of an installed app runs in this gateway, and the one path that starts and stops it.

Install, enable, update, disable and the three uninstall rungs each used to carry their own
sequence of "stop the backend, drop the MCP servers, disable the providers, re-seed the
prompts", and the copies had drifted: an update never stopped the app's background worker,
install never registered its proposal kinds, and none of them took the app's Python back out of
the process. The loader caches an app's modules and an update swaps the new files in at the same
path, so an update's new code only ran after a restart — for every kind of app. Now every
transition is :func:`unload` and then :func:`load`:

* :func:`load` starts everything an enabled app runs, from the files on disk now: its providers
  (which imports its code), prompts, skills, MCP servers, proposal kinds, backend and background
  worker.
* :func:`unload` stops all of it and takes the app's code out of the process. The backend and
  the worker stop and stay down until the next load (the watchdogs start nothing held), the MCP
  servers close with the processes they spawned, the providers go and a channel's receiver with
  them, and :func:`personalclaw.app_code.release` takes back whatever the app's code registered
  and removes its modules from ``sys.modules``.

What cannot be taken back in-process — a compiled extension it loaded, a thread or a task it
started that is still running, a Python package the gateway had loaded that an update replaced
— becomes the app's restart reason (:func:`restart_reason`). The update says so, the Apps page
says so, and only a restart clears it.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from personalclaw import app_code

if TYPE_CHECKING:
    from personalclaw.apps.manifest import AppManifest

logger = logging.getLogger(__name__)

_lock = threading.Lock()
#: The app → why its previous code is still in this process. In memory on purpose: restarting
#: the gateway is the one thing that clears it.
_restart: dict[str, list[str]] = {}


def load(manifest: AppManifest) -> None:
    """Start everything an enabled app runs, from the files on disk now.

    The app's code is imported here (its providers are enabled), so this is also where the
    version on disk starts answering. A provider that fails to start says why on its own record
    (Settings → Providers) and holds up nothing else.
    """
    name = manifest.name
    if manifest.all_providers():
        try:
            _provider_registry().register(manifest, enabled=True)
        except Exception:  # noqa: BLE001 — the rest of the app still starts
            logger.exception("app %s: provider registration failed", name)
    _seed_prompts(manifest)
    _seed_skills(manifest)
    _register_mcp(manifest)
    _register_proposal_kinds(manifest)
    _start_backend(manifest)
    _start_workers(manifest)


def record(manifest: AppManifest) -> None:
    """List a DISABLED app's providers (off) without running any of its code."""
    if manifest.all_providers():
        _provider_registry().register(manifest, enabled=False)


def unload(name: str, manifest: AppManifest | None, *, forget: bool = False) -> list[str]:
    """Stop everything *name* runs and take its code out of this process.

    *manifest* is the version being unloaded — its prompts, skills and proposal kinds are the
    ones to drop — or ``None`` when it cannot be read. *forget* drops the app's provider records
    too, for a removal; otherwise they stay, disabled, for the Providers page to list.

    Returns what could not be taken out (the sentences :func:`restart_reason` joins); empty when
    nothing of the app is left in the process.
    """
    _stop_backend(name)
    _stop_workers(name)
    _deregister_mcp(name)
    registry = _provider_registry()
    if forget:
        registry.deregister(name)
    else:
        registry.disable(name)
    # A channel's receiver is stopped by a reconciliation on the gateway's loop; the code it runs
    # has to be done running before that code's modules go.
    _settle_channel_receivers()
    if manifest is not None:
        _remove_prompts(manifest)
        _remove_skills(manifest)
        _deregister_proposal_kinds(manifest)
    _forget_availability(name)
    left = app_code.release(name).left_running
    if left:
        note_restart(name, left)
        logger.warning(
            "app %s: its previous code is still in the gateway until a restart: %s",
            name,
            "; ".join(left),
        )
    return left


def stop_processes() -> None:
    """Stop every app process this gateway started: its backends and its workers.

    For a gateway about to replace its own image — a Restart, or the restart an update of
    PersonalClaw itself ends with. ``os.execve`` keeps the PID, so a process left running stays
    a child of the new image, whose supervisors start with empty tables and whose boot reap
    spares any process with a live parent. Every Restart used to leave one more backend and one
    more worker per app running, still at the version of that moment, and an app update after it
    stopped only the processes the new image had started. The watchdogs stop first, so no sweep
    revives a process between its stop and the exec.
    """
    try:
        from personalclaw.providers.loader import stop_extension_watchdogs

        stop_extension_watchdogs()
    except Exception:  # noqa: BLE001 — the processes below still have to stop
        logger.debug("app watchdogs did not stop", exc_info=True)
    from personalclaw.apps.backend_runtime import get_backend_supervisor
    from personalclaw.apps.worker_runtime import get_worker_supervisor

    for supervisor in (get_backend_supervisor(), get_worker_supervisor()):
        try:
            supervisor.stop_all()
        except Exception:  # noqa: BLE001 — one that fails must not leave the other running
            logger.debug(
                "%s did not stop every app process", type(supervisor).__name__, exc_info=True
            )


def note_restart(name: str, reasons: list[str]) -> None:
    """Record why *name* needs a gateway restart before only its installed version runs."""
    with _lock:
        known = _restart.setdefault(name, [])
        known.extend(r for r in reasons if r not in known)


def restart_reason(name: str) -> str:
    """Why *name* needs a gateway restart to run only its installed version; ``""`` if it does not.

    One clause per reason, joined — the caller leads it ("Restart the gateway to finish: …").
    """
    with _lock:
        return "; ".join(_restart.get(name, []))


# ── the parts, each best-effort: one that fails must not leave the others undone ─────────────


def _provider_registry() -> Any:
    from personalclaw.providers.registry import get_provider_registry

    return get_provider_registry()


def _start_backend(manifest: AppManifest) -> None:
    """Release the app's hold and launch its backend subprocess, if it declares one."""
    try:
        from personalclaw.apps.backend_runtime import get_backend_supervisor

        supervisor = get_backend_supervisor()
        supervisor.unhold(manifest.name)
        if manifest.backend.entryPoint:
            supervisor.start(manifest)
    except Exception:  # noqa: BLE001
        logger.debug("app %s: backend start failed", manifest.name, exc_info=True)


def _stop_backend(name: str) -> None:
    """Hold the app's backend down, then stop it — held first, so no watchdog pass fits between."""
    try:
        from personalclaw.apps.backend_runtime import get_backend_supervisor

        supervisor = get_backend_supervisor()
        supervisor.hold(name)
        supervisor.stop(name)
    except Exception:  # noqa: BLE001
        logger.debug("app %s: backend stop failed", name, exc_info=True)


def _start_workers(manifest: AppManifest) -> None:
    """Release the app's hold and start its declared workers now, not at the next sweep."""
    try:
        from personalclaw.apps.worker_runtime import get_worker_supervisor, start_app_workers

        get_worker_supervisor().unhold(manifest.name)
        start_app_workers(manifest)
    except Exception:  # noqa: BLE001
        logger.debug("app %s: worker start failed", manifest.name, exc_info=True)


def _stop_workers(name: str) -> None:
    """Hold *name*'s workers down, stop them, then reap anything a prior gateway orphaned.

    APE-3's V1 clause is "uninstall leaves no orphan worker", and the sweep alone cannot deliver
    it: the sweep stops workers whose app went away, but a process re-parented to init by an
    ungraceful gateway exit is in no supervisor's table, so nothing would ever look for it once
    the app directory is gone. So this runs while the entry path is still resolvable.

    Best-effort by construction: an app being turned off must not fail because its worker was
    already dead.
    """
    try:
        from personalclaw.apps.background import WORKER_ENTRY_POINT
        from personalclaw.apps.manager import app_dir
        from personalclaw.apps.worker_runtime import get_worker_supervisor

        supervisor = get_worker_supervisor()
        supervisor.hold(name)
        supervisor.stop(name)  # `worker=None` stops every worker this app has
        supervisor.reap_orphans(name, (app_dir(name) / WORKER_ENTRY_POINT).resolve())
    except Exception:  # noqa: BLE001
        logger.debug("app %s: worker stop failed", name, exc_info=True)


def _register_mcp(manifest: AppManifest) -> None:
    """Wire the app's declared mcpServers into the live MCP config."""
    if not manifest.mcpServers:
        return
    try:
        from personalclaw.apps import mcp_bridge

        mcp_bridge.register_app_mcp_servers(manifest)
    except Exception:  # noqa: BLE001
        logger.debug("app %s: MCP register failed", manifest.name, exc_info=True)


def _deregister_mcp(name: str) -> None:
    """Drop the app's MCP servers from the config and close the processes they spawned."""
    try:
        from personalclaw.apps import mcp_bridge

        mcp_bridge.deregister_app_mcp_servers(name)
    except Exception:  # noqa: BLE001
        logger.debug("app %s: MCP deregister failed", name, exc_info=True)


def _register_proposal_kinds(manifest: AppManifest) -> None:
    """Register the app's declared ``permissions.proposals`` kinds (INU-7).

    At load, so a declared kind is REGISTERED before the app can post one — the
    ``POST /api/inbox/proposals`` 403 reads the manifest, and delivery policy reads the
    registry, and neither works if the pair was never minted.
    """
    if not manifest.permissions.proposals:
        return
    try:
        from personalclaw.proposals_contract import register_app_proposal_kinds

        register_app_proposal_kinds(manifest.name, manifest)
    except Exception:  # noqa: BLE001
        logger.debug("app %s: proposal kind register failed", manifest.name, exc_info=True)


def _deregister_proposal_kinds(manifest: AppManifest) -> None:
    """Drop the app's proposal kinds so an unloaded app leaves no phantom kind."""
    if not manifest.permissions.proposals:
        return
    try:
        from personalclaw.proposals_contract import deregister_app_proposal_kinds

        deregister_app_proposal_kinds(manifest.name, manifest)
    except Exception:  # noqa: BLE001
        logger.debug("app %s: proposal kind deregister failed", manifest.name, exc_info=True)


def _seed_prompts(manifest: AppManifest) -> None:
    """Seed the app's declared prompts/snippets into the native store (an app OWNS them)."""
    if not manifest.prompts:
        return
    try:
        from personalclaw.apps.manager import app_dir
        from personalclaw.apps.prompt_seed import seed_app_prompts

        seed_app_prompts(manifest, app_dir(manifest.name))
    except Exception:  # noqa: BLE001
        logger.debug("app %s: prompt seed failed", manifest.name, exc_info=True)


def _remove_prompts(manifest: AppManifest) -> None:
    """Remove the app's own seeded prompts + unregister its prompt use-cases."""
    try:
        from personalclaw.apps.manager import app_dir
        from personalclaw.apps.prompt_seed import remove_app_prompts

        remove_app_prompts(manifest, app_dir(manifest.name))
    except Exception:  # noqa: BLE001
        logger.debug("app %s: prompt remove failed", manifest.name, exc_info=True)


def _seed_skills(manifest: AppManifest) -> None:
    """Seed the app's declared SKILL.md skills THROUGH the supply-chain chokepoint, at the
    trust origin its install recorded (an app skill never bypasses the gate)."""
    if not manifest.skills:
        return
    try:
        from personalclaw.apps.manager import _read_installed, app_dir
        from personalclaw.apps.skill_seed import seed_app_skills

        meta = _read_installed(manifest.name)
        origin = (getattr(meta, "origin", "") or "local") if meta is not None else "local"
        seed_app_skills(manifest, app_dir(manifest.name), origin=origin)
    except Exception:  # noqa: BLE001
        logger.debug("app %s: skill seed failed", manifest.name, exc_info=True)


def _remove_skills(manifest: AppManifest) -> None:
    """Remove the app's own seeded skills (provenance-keyed, never a user's skill)."""
    try:
        from personalclaw.apps.manager import app_dir
        from personalclaw.apps.skill_seed import remove_app_skills

        remove_app_skills(manifest, app_dir(manifest.name))
    except Exception:  # noqa: BLE001
        logger.debug("app %s: skill remove failed", manifest.name, exc_info=True)


def _settle_channel_receivers() -> None:
    try:
        from personalclaw.channel_transports import settle_from_thread

        settle_from_thread()
    except Exception:  # noqa: BLE001
        logger.debug("channel receivers did not settle", exc_info=True)


def _forget_availability(name: str) -> None:
    """Its availability answers were its previous version's hook talking."""
    try:
        from personalclaw.providers.availability import get_availability_board

        get_availability_board().forget(name)
    except Exception:  # noqa: BLE001
        logger.debug("app %s: availability not forgotten", name, exc_info=True)
