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
