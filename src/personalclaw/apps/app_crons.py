"""App-declared cron reconciliation (untrusted-app sandbox P3).

An app manifest may declare ``crons: list[CronEntry]`` — scheduled agent jobs the
app wants run on a cadence. These are only honored when the app declares the
``cron`` permission (``can_use_cron``); without it the declaration is inert.

Rather than couple app_manager to the scheduler, the gateway calls
:func:`reconcile_app_crons` once at startup (after apps are loaded). Reconciliation is
idempotent + declarative: every app-owned trigger is tagged ``created_by="app:<name>"``, so we can
diff the desired set (enabled apps × their permitted manifest crons) against the registered
``app:*`` triggers and add / prune to match. This covers enable, disable, uninstall, permission
changes, and manifest edits without per-lifecycle wiring — the next start reconciles.

Trigger id/name convention: ``app:<app-name>:<cron-name>``.

**🔴 S108 — this wrote to `crons.json`, so app crons DID NOT FIRE.** The clock engine
(`triggers.service.tick`) reads the unified store and nothing else, and the boot migration that
imports `crons.json` runs BEFORE reconciliation. Measured: a job written here landed in `crons.json`
with `triggers.json` empty, so an app's declared cron stayed inert until the NEXT gateway boot
imported it — every app cron was one restart behind its own manifest, and a freshly installed app's
cron never ran on the session that installed it. Reconciliation now writes the store directly.

The rows are built as `Trigger` objects rather than through `tools.create`, deliberately: this
reconciler's whole mechanism is a diff against a DETERMINISTIC id (``app:<app>:<cron>``), and
`tools.create` mints its own slug-derived unique id. Going through it would leave every restart
unable to recognize its own previous rows, so the diff would add duplicates forever instead of
converging.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_APP_JOB_PREFIX = "app:"


def _desired_app_crons() -> dict[str, dict]:
    """The app triggers that SHOULD exist: for every enabled app that declares the
    ``cron`` permission, one entry per manifest cron. Keyed by trigger id
    ``app:<app>:<cron>`` → the params to register."""
    from personalclaw.apps.app_manager import _manifest_of
    from personalclaw.apps.manager import _read_installed, apps_dir
    from personalclaw.apps.permissions import checker_for

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
                # The action in the STORE's shape, matching what the migration produces for an
                # `invoke-agent` cron — verified field-for-field against a real conversion, so an
                # app cron written here and one imported from `crons.json` are the same row.
                "workflow": {
                    "inline": {
                        "provider": "invoke-agent",
                        "config": {
                            "task_template": cron.message,
                            "agent": cron.agent or "",
                            "model": "",
                            # App crons are unattended background runs — auto-approve so a
                            # backgrounded turn can't wedge waiting on a human.
                            "approval_mode": "auto",
                        },
                    }
                },
                "spec": (
                    {"kind": "interval", "interval_secs": int(cron.every)}
                    if cron.every
                    else {"kind": "cron", "expr": cron.cron_expr}
                ),
                "created_by": f"{_APP_JOB_PREFIX}{meta.name}",
                # App crons are headless — there is no owner conversation to post to. `delivery`
                # is always `none` (the store's spelling of the legacy `silent=True`): otherwise
                # every run tried to open a channel DM to the trigger's created_by (an
                # "app:<name>" pseudo-id, not a real user) and logged a delivery failure. An app
                # surfaces a cron result itself (its backend, or the send_message tool), never via
                # cron auto-delivery.
                "delivery": "none",
            }
    return desired


def reconcile_app_crons(store: Any) -> None:
    """Make the store's ``app:*`` triggers match what the installed+permitted apps declare.

    Idempotent: safe to call on every startup. Best-effort — a single bad entry is logged and
    skipped, never blocking the others or startup.
    """
    from personalclaw.triggers.arm import arm as _arm
    from personalclaw.triggers.models import Trigger

    try:
        desired = _desired_app_crons()
    except Exception:
        logger.warning("app-cron reconcile: could not compute desired set", exc_info=True)
        return

    try:
        rows = store.load()
    except Exception:
        logger.warning("app-cron reconcile: could not read the trigger store", exc_info=True)
        return
    existing = {
        row.trigger.id: row.trigger
        for row in rows
        if str(row.trigger.id).startswith(_APP_JOB_PREFIX)
    }

    # Prune app triggers no longer desired (app disabled/uninstalled, permission revoked, or the
    # manifest dropped the entry).
    for trigger_id in existing:
        if trigger_id not in desired:
            try:
                store.delete(trigger_id)
                logger.info("app-cron reconcile: pruned %s", trigger_id)
            except Exception:
                logger.debug("app-cron reconcile: prune failed for %s", trigger_id, exc_info=True)

    # Add newly-desired app triggers (skip ones already registered — leave a user's enable/disable
    # toggle on an existing app trigger untouched). For an existing one, converge the
    # manifest-driven `delivery`: it is NOT user-editable (app crons are headless, always silent),
    # so a row persisted with a channel must be corrected here rather than kept until re-install.
    for trigger_id, params in desired.items():
        cur = existing.get(trigger_id)
        if cur is not None:
            if str(getattr(cur, "delivery", "") or "") != "none":
                try:
                    cur.delivery = "none"
                    store.upsert(cur)
                    logger.info("app-cron reconcile: set %s silent", trigger_id)
                except Exception:
                    logger.debug(
                        "app-cron reconcile: silent-fix failed for %s", trigger_id, exc_info=True
                    )
            continue
        try:
            trigger = Trigger(
                id=trigger_id,
                name=trigger_id,
                kind="clock",
                enabled=True,
                created_by=params["created_by"],
                spec=dict(params["spec"]),
                workflow=dict(params["workflow"]),
                delivery=params["delivery"],
            )
            # ARM IT NOW, for the reason `tools.create` records: `service.due_ids` only surfaces
            # rows that HAVE a `next_fire_at`, so an unarmed trigger never fires. Arming here rather
            # than leaving it to the next boot sweep is the difference between an app's cron running
            # tonight and running after the user restarts.
            armed = _arm(trigger)
            if armed:
                trigger.next_fire_at = armed
            store.upsert(trigger)
            logger.info("app-cron reconcile: registered %s", trigger_id)
        except Exception:
            logger.warning("app-cron reconcile: failed to register %s", trigger_id, exc_info=True)
