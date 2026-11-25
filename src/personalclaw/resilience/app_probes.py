"""The Doctor's checks of installed apps: their backends and interrupted updates, and each
capability's state (#3843).

Kept out of ``resilience/doctor.py``, which registers them (:func:`app_probes`), as
``store_files`` keeps its checks.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # the Doctor imports this module to register the checks below
    from personalclaw.resilience.doctor import DoctorContext, Probe, ProbeResult


async def probe_apps(ctx: DoctorContext) -> ProbeResult:
    """apps — per enabled backend app: subprocess alive (watchdog) + leftover
    ``.{name}.rollback`` dirs from interrupted updates.

    Read-only: consults the backend supervisor's live table (``get(name)`` returns
    None for a dead entry) and globs the apps dir for rollback leftovers (does NOT
    call ``recover_interrupted_updates`` — that mutates).

    Scope note: installed-copy-vs-repo manifest drift has no stored hash to diff
    — it waits for a manifest checksum; this
    pack probes liveness + rollback leftovers, the real signals available today.
    """
    from personalclaw.apps.backend_runtime import get_backend_supervisor
    from personalclaw.apps.manager import apps_dir, list_apps
    from personalclaw.resilience.doctor import ProbeResult

    def _read() -> dict[str, Any]:
        sup = get_backend_supervisor()
        backends: dict[str, Any] = {}
        for app in list_apps():
            if not app.get("enabled", False):
                continue
            manifest = app.get("manifest", {}) or {}
            if not (manifest.get("backend", {}) or {}).get("entryPoint"):
                continue
            name = app.get("name", "")
            rb = sup.get(name)
            # How a backend that is down last ended, and the last lines it printed.
            backends[name] = {
                "alive": rb is not None,
                "exit": None if rb is not None else sup.last_exit(name),
            }
        rollbacks: list[str] = []
        ad = apps_dir()
        if ad.exists():
            for child in ad.iterdir():
                if (
                    child.is_dir()
                    and child.name.startswith(".")
                    and child.name.endswith(".rollback")
                ):
                    rollbacks.append(child.name)
        return {"backends": backends, "rollback_leftovers": rollbacks}

    ev = await asyncio.to_thread(_read)
    dead = [n for n, v in ev["backends"].items() if not v["alive"]]
    problems = []
    if dead:
        ended = [
            f"{n} {x['ended']}" + (f": {x['cause']}" if x["cause"] else "")
            for n in dead
            if (x := ev["backends"][n]["exit"])
        ]
        problems.append(
            f"{len(dead)} backend{'s' if len(dead) != 1 else ''} not running"
            + (f" ({'; '.join(ended)})" if ended else "")
        )
    if ev["rollback_leftovers"]:
        problems.append(
            f"{len(ev['rollback_leftovers'])} interrupted "
            f"update{'s' if len(ev['rollback_leftovers']) != 1 else ''}"
        )
    return ProbeResult(
        ok=not problems,
        detail=(
            "; ".join(problems)
            if problems
            else f"{len(ev['backends'])} app backend{'s' if len(ev['backends']) != 1 else ''} ok"
        ),
        evidence=ev,
        # An interrupted update's leftover is what the orphan prune reconciles (restore or drop,
        # decided by the apps reconciler) — a working Fix for that half.
        fix_id="serving-fs.orphan-prune" if ev["rollback_leftovers"] else None,
        remedy=(
            "No automatic fix for a backend that is down: the app watchdog already relaunches one "
            "every 30 seconds, so a backend that stays down is failing to start — the app's panel "
            "under Apps shows the last lines it printed, as does Settings → Health → Diagnostics → "
            "Live logs. Disable the app under Settings → Apps & Packs → Apps to stop the retries."
            if dead
            else ""
        ),
    )


async def probe_app_capabilities(ctx: DoctorContext) -> ProbeResult:
    """apps — one line per enabled app, with each capability's effective state
    (``capability_switches.capability_status``, the reading ``/api/apps`` shows).

    Fails naming the capability that failed or cannot run on this machine, and an app whose
    switches could not be read. A capability switched off reads as off, never as a failure, and
    one waiting for an account field reads as needing setup."""
    from personalclaw.apps import capability_switches as cs
    from personalclaw.resilience.doctor import ProbeResult

    def _read() -> dict[str, Any]:
        from personalclaw.apps.manager import InstalledApp, list_apps
        from personalclaw.apps.manifest import AppManifest

        apps: dict[str, Any] = {}
        for app in list_apps():
            if not app.get("enabled", False) or not app.get("manifest"):
                continue
            manifest = AppManifest.from_dict(app["manifest"])
            try:
                ids = manifest.capability_ids()
            except ValueError:
                continue
            if not ids:
                continue
            name, meta = manifest.name, InstalledApp.from_dict(app)
            label = manifest.displayName or name
            rows = []
            for cid, provider in zip(ids, manifest.all_providers()):
                status = cs.capability_status(
                    name,
                    cid,
                    on=cs.is_on(meta, cid),
                    app_enabled=True,
                    app_label=label,
                    readable=cs.readable(meta, cid),
                )
                rows.append((provider.label or cid, cid, status))
            apps[name] = {
                "summary": cs.summary(label, [(lb, st) for lb, _, st in rows], app_enabled=True),
                "failing": any(_failing(st, label) for _, _, st in rows),
                "capabilities": [{"id": cid, "label": lb, **st.to_wire()} for lb, cid, st in rows],
            }
        return {"apps": apps}

    ev = await asyncio.to_thread(_read)
    failing = {n: a["summary"] for n, a in ev["apps"].items() if a["failing"]}
    n = len(ev["apps"])
    return ProbeResult(
        ok=not failing,
        detail=(
            "; ".join(failing.values())
            if failing
            else f"{n} app{'s' if n != 1 else ''} running the capabilities switched on"
        ),
        evidence=ev,
        remedy=(
            "Open the app under Apps: each capability says why it is not running. Fix its "
            "settings or install what it needs and it starts again, or switch it off."
            if failing
            else ""
        ),
    )


def _failing(status: Any, app_label: str) -> bool:
    """Whether a capability's status is a fault the Doctor reports: its build failed, it cannot
    run on this machine, or its switch could not be read."""
    from personalclaw.apps import capability_switches as cs

    return status.state in {cs.FAILED, cs.UNAVAILABLE} or (
        status.state == cs.OFF and status.reason == cs.unreadable_sentence(app_label)
    )


def app_probes() -> list[Probe]:
    """The checks, as the Doctor registers them."""
    from personalclaw.resilience.doctor import Probe, Tier

    return [
        Probe(
            "apps.backends",
            "apps",
            Tier.CAPABILITY,
            probe_apps,
            "App backends + interrupted updates",
        ),
        Probe(
            "apps.capabilities",
            "apps",
            Tier.CAPABILITY,
            probe_app_capabilities,
            "Each app's capabilities",
        ),
    ]
