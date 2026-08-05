"""Durability endpoints: snapshots, schedule status, on-demand jobs (§3).

Read and run — this session does NOT expose restore over HTTP. Replace-restore
refuses to run while the gateway is up (`snapshot.py:_is_gateway_running`), so a
useful restore endpoint needs the staged-swap-on-next-boot machinery the plan
describes, and half of that is worse than none: an endpoint that appears to restore
and doesn't is a trap. Restore stays `personalclaw restore` until that lands.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)

# Jobs a caller may trigger by name. Closed set — this maps to real work on disk.
_RUNNABLE = ("export", "snapshot", "drill")


def _sel():
    from personalclaw.sel import sel

    return sel()


async def api_durability_status(request: web.Request) -> web.Response:
    """GET /api/durability/status — schedule state + what's due."""
    from personalclaw.durability import service

    status = await asyncio.get_event_loop().run_in_executor(None, service.status)
    return web.json_response(status)


async def api_durability_snapshots(request: web.Request) -> web.Response:
    """GET /api/durability/snapshots — the archive list with the retention plan.

    Includes which snapshots the current tier budgets would KEEP versus PRUNE, so
    the retention policy is inspectable before it deletes anything.
    """
    from pathlib import Path

    from personalclaw.config.loader import AppConfig
    from personalclaw.durability import retention
    from personalclaw.snapshot import _default_snapshot_dir

    def _collect() -> dict:
        directory = Path(_default_snapshot_dir())
        snapshots = retention.list_snapshots(directory)
        try:
            cfg = AppConfig.load().durability
            daily, weekly, monthly = cfg.keep_daily, cfg.keep_weekly, cfg.keep_monthly
        except Exception:  # noqa: BLE001
            daily, weekly, monthly = (
                retention.DEFAULT_DAILY,
                retention.DEFAULT_WEEKLY,
                retention.DEFAULT_MONTHLY,
            )
        keep, prune = retention.plan_retention(
            snapshots, daily=daily, weekly=weekly, monthly=monthly
        )
        keep_names = {s.name for s in keep}
        return {
            "directory": str(directory),
            "snapshots": [
                {
                    "name": s.name,
                    "taken_at": s.taken_at.isoformat(),
                    "size": s.size,
                    "retained": s.name in keep_names,
                }
                for s in snapshots
            ],
            "would_prune": [s.name for s in prune],
            "tiers": {"daily": daily, "weekly": weekly, "monthly": monthly},
        }

    payload = await asyncio.get_event_loop().run_in_executor(None, _collect)
    return web.json_response(payload)


async def api_durability_run(request: web.Request) -> web.Response:
    """POST /api/durability/run {job} — run one backup job now.

    For "back up before I do something risky" and for verifying the schedule works
    without waiting a month for the drill. Each job is single-flighted, so a
    concurrent scheduled run reports a skip rather than colliding.
    """
    from personalclaw.durability import service

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "body must be JSON"}, status=400)
    job = str(body.get("job", "") or "").strip().lower()
    if job not in _RUNNABLE:
        return web.json_response(
            {"error": f"job must be one of: {', '.join(_RUNNABLE)}"}, status=400
        )

    state = request.app.get("state")
    notifier = getattr(state, "notify", None) if state is not None else None
    runners = {
        "export": service.run_incremental_export,
        "snapshot": service.run_nightly_snapshot,
        "drill": lambda: service.run_restore_drill(notifier=notifier),
    }
    result = await asyncio.get_event_loop().run_in_executor(None, runners[job])
    try:
        _sel().log_api_access(
            caller=request.get("user", "dashboard"),
            operation=f"durability_run:{job}",
            outcome="allowed" if result.ok else "denied",
            resources=result.detail[:200],
        )
    except Exception:  # noqa: BLE001
        logger.debug("durability: audit failed", exc_info=True)
    # 200 even on a failed job: the request succeeded and the report IS the answer.
    # A 500 would imply the endpoint broke rather than the backup.
    return web.json_response(result.to_dict())


async def api_durability_restore(request: web.Request) -> web.Response:
    """POST /api/durability/restore {snapshot, mode?, components?} — the CLI's restore, mirrored.

    🔴 WHY THIS EXISTS. T2-M3 names it and it was absent: the API had `status`, `snapshots`
    and `run`, so a user could take a backup from the dashboard and could not restore one.
    Backup without restore
    is the shape this plan exists to remove ("recoverable through first-class restore endpoints
    — not
    archaeology").

    **Omitting `mode` returns the PLAN and changes nothing.** That is the safe default for an
    endpoint that can overwrite a home: a caller must see what would happen and then ask again
    with an explicit
    mode. `mode=replace` is therefore always deliberate, never inferred.

    Refuses while the gateway runs, exactly as the CLI does — this handler IS the gateway, so a
    restore under it would rewrite state the running process holds open. There is no `--force`
    mirror on purpose: forcing is a local operator decision at a terminal, not something to
    expose over HTTP.
    """
    from personalclaw import snapshot as snap_mod

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "body must be JSON"}, status=400)

    raw = str(body.get("snapshot", "") or "").strip()
    if not raw:
        return web.json_response({"error": "snapshot is required"}, status=400)

    mode = body.get("mode")
    if mode is not None:
        mode = str(mode).strip().lower()
        if mode not in ("merge", "replace"):
            return web.json_response({"error": "mode must be merge or replace"}, status=400)

    components = body.get("components")
    if components is not None:
        if not isinstance(components, list) or not all(isinstance(c, str) for c in components):
            return web.json_response({"error": "components must be a list of strings"}, status=400)
        unknown = [c for c in components if c not in snap_mod.VALID_COMPONENTS]
        if unknown:
            return web.json_response(
                {"error": f"unknown component(s): {', '.join(sorted(unknown))}"}, status=400
            )

    # Path containment: the archive must be one WE produced, named from the snapshot directory.
    # Accepting an arbitrary path over HTTP would let a caller point a restore at any tar on disk.
    from personalclaw.snapshot import _default_snapshot_dir

    snap_dir = Path(_default_snapshot_dir()).resolve()
    candidate = (snap_dir / Path(raw).name).resolve()
    if candidate.parent != snap_dir or not candidate.is_file():
        return web.json_response(
            {"error": "snapshot not found in the snapshot directory"}, status=404
        )

    def _run() -> dict:
        if mode is None:
            return snap_mod.restore_plan(candidate, components)
        return snap_mod.restore_apply(candidate, mode, components)

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _run)
    except Exception as exc:  # noqa: BLE001 — report, never 500 on a restore refusal
        logger.warning("durability restore failed", exc_info=True)
        return web.json_response({"error": str(exc)}, status=400)

    try:
        _sel().log_api_access(
            caller=request.get("user", "dashboard"),
            operation=f"durability_restore:{mode or 'plan'}",
            outcome="allowed" if result.get("ok", True) else "denied",
            resources=candidate.name,
        )
    except Exception:  # noqa: BLE001
        logger.debug("durability: audit failed", exc_info=True)
    return web.json_response(result, status=200 if result.get("ok", True) else 409)
