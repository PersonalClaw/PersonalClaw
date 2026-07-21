"""App-declared cron reconciliation (untrusted-app sandbox P3).

An app manifest may declare ``crons: list[CronEntry]`` — scheduled agent jobs the
app wants run on a cadence. These are only honored when the app declares the
``cron`` permission (``can_use_cron``); without it the declaration is inert.

Rather than couple app_manager to the ScheduleService, the gateway calls
:func:`reconcile_app_crons` once at startup (after the scheduler is built and apps
are loaded). Reconciliation is idempotent + declarative: every app-owned job is
tagged ``created_by="app:<name>"``, so we can diff the desired set (enabled apps ×
their permitted manifest crons) against the registered ``app:*`` jobs and add /
prune to match. This covers enable, disable, uninstall, permission changes, and
manifest edits without per-lifecycle wiring — the next start reconciles.

Job id/name convention: ``app:<app-name>:<cron-name>``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.schedule import ScheduleService

logger = logging.getLogger(__name__)

_APP_JOB_PREFIX = "app:"


def _desired_app_crons() -> dict[str, dict]:
    """The app jobs that SHOULD exist: for every enabled app that declares the
    ``cron`` permission, one entry per manifest cron. Keyed by job name
    ``app:<app>:<cron>`` → the params to register."""
    from personalclaw.apps.app_manager import _manifest_of
    from personalclaw.apps.manager import _read_installed, apps_dir
    from personalclaw.apps.permissions import checker_for
    from personalclaw.schedule import make_agent_action

    root = apps_dir()
    if not root.is_dir():
        return {}
    desired: dict[str, dict] = {}
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        meta = _read_installed(entry.name)
        if meta is None or not meta.enabled:
            continue
        checker = checker_for(meta.name)
        if checker is None or not checker.can_use_cron():
            continue
        manifest = _manifest_of(meta.name)
        if manifest is None:
            continue
        for cron in manifest.crons:
            if not cron.name:
                continue
            if not (cron.every or cron.cron_expr):
                continue  # nothing to schedule on
            job_name = f"{_APP_JOB_PREFIX}{meta.name}:{cron.name}"
            desired[job_name] = {
                "action": make_agent_action(
                    message=cron.message,
                    agent=cron.agent,
                    # App crons are unattended background runs — auto-approve so a
                    # backgrounded turn can't wedge waiting on a human.
                    approval_mode="auto",
                ),
                "every_secs": int(cron.every) if cron.every else None,
                "cron_expr": cron.cron_expr or None,
                "created_by": f"{_APP_JOB_PREFIX}{meta.name}",
                # App crons are headless — there is no owner conversation to post
                # to. Always silent: otherwise every run tried to open a channel DM to
                # the job's created_by (an "app:<name>" pseudo-id, not a real user)
                # and logged a channel-delivery failure. An app surfaces a cron
                # result itself (its backend, or the send_message tool), never via
                # cron auto-delivery. The manifest ``silent`` flag is thus advisory
                # and already the effective default.
                "silent": True,
            }
    return desired


def reconcile_app_crons(crons: "ScheduleService") -> None:
    """Make the scheduler's ``app:*`` jobs match what the installed+permitted apps
    declare. Idempotent: safe to call on every startup. Best-effort — a single bad
    entry is logged and skipped, never blocking the others or startup."""
    try:
        desired = _desired_app_crons()
    except Exception:
        logger.warning("app-cron reconcile: could not compute desired set", exc_info=True)
        return

    existing = {
        j.name: j
        for j in crons.list_jobs(include_disabled=True)
        if j.name.startswith(_APP_JOB_PREFIX)
    }

    # Prune app jobs no longer desired (app disabled/uninstalled, permission
    # revoked, or the manifest dropped the entry).
    for name, job in existing.items():
        if name not in desired:
            try:
                crons.remove_job(job.id)
                logger.info("app-cron reconcile: pruned %s", name)
            except Exception:
                logger.debug("app-cron reconcile: prune failed for %s", name, exc_info=True)

    # Add newly-desired app jobs (skip ones already registered — leave a user's
    # enable/disable toggle on an existing app job untouched). For an existing
    # job, converge the manifest-driven ``silent`` flag: it is NOT user-editable
    # (app crons are headless, always silent), so a pre-fix job persisted with
    # silent=False must be corrected here rather than kept until re-install.
    for name, params in desired.items():
        cur = existing.get(name)
        if cur is not None:
            if getattr(cur, "silent", False) is not True:
                try:
                    crons.update_job(cur.id, silent=True)
                    logger.info("app-cron reconcile: set %s silent", name)
                except Exception:
                    logger.debug(
                        "app-cron reconcile: silent-fix failed for %s", name, exc_info=True
                    )
            continue
        try:
            crons.add_job(
                name,
                action=params["action"],
                every_secs=params["every_secs"],
                cron_expr=params["cron_expr"],
                created_by=params["created_by"],
                silent=params.get("silent", True),
            )
            logger.info("app-cron reconcile: registered %s", name)
        except Exception:
            logger.warning("app-cron reconcile: failed to register %s", name, exc_info=True)
