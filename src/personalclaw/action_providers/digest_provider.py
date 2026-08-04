"""The notification-digest action (INBOX-NOTIFICATIONS-UNIFICATION T5.1).

Drains `digest_queue.jsonl` into one grouped inbox item. Registered as a system cron so the
schedule is user-configurable through the same rules store as the modes that feed it.

**Deterministic, no model call.** A digest is a grouping of things that already happened, so
an LLM would add latency, cost, and a chance of inventing detail — for a summary whose whole
value is that it's accurate. It runs on the action-provider path (like `bash`/`notify`),
never the agent path.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.action_providers.services import get_action_services

logger = logging.getLogger(__name__)

#: The cron job name. Prefixed like the app crons so a reconcile can find its own job
#: without touching a user's hand-made ones.
DIGEST_JOB_NAME = "system:notification-digest"


class NotificationDigestActionProvider(ActionProvider):
    """Collapse the queued notifications into a single digest inbox item."""

    @property
    def name(self) -> str:
        return "notification-digest"

    @property
    def display_name(self) -> str:
        return "Notification Digest"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        from personalclaw import notification_rules

        services = get_action_services()
        state = getattr(services, "state", None) if services is not None else None
        try:
            item_id = notification_rules.run_digest(state)
        except Exception as exc:  # noqa: BLE001 - surface as a result, never raise
            return ActionResult(success=False, error=f"digest failed: {exc}")
        if not item_id:
            # An empty queue is a SUCCESS with nothing to show. Reporting it as a failure
            # would light up the cron's error surface every quiet day.
            return ActionResult(success=True, exit_code=0, stdout="digest: nothing queued")
        return ActionResult(success=True, exit_code=0, stdout=f"digest: created {item_id}")


def create_provider(config: dict[str, Any] | None = None) -> "NotificationDigestActionProvider":
    return NotificationDigestActionProvider()


def reconcile_digest_cron(store: Any) -> None:
    """Make the digest trigger exist and match the configured schedule. Idempotent.

    Converges the schedule rather than only creating the trigger: the schedule lives in the rules
    store, so a user who edits it in Settings must not have to know that a cron exists
    somewhere to be re-registered. Best-effort — a scheduler problem must never block startup.

    **🔴 S108 — this wrote `crons.json`, so the digest DID NOT RUN.** The clock engine reads the
    unified store only, and the boot migration that imports `crons.json` ran BEFORE this
    reconciliation — so the digest trigger this function registered was inert until the NEXT boot
    imported it, and a schedule edited in Settings took two restarts to take effect. Writes the
    store directly now.

    The row is built as a `Trigger` rather than through `tools.create` for the reason `app_crons`
    records: convergence needs a DETERMINISTIC id, and `tools.create` mints its own unique slug, so
    every restart would add another digest instead of recognizing its own.
    """
    from personalclaw import notification_rules
    from personalclaw.triggers import screen as _screen
    from personalclaw.triggers.arm import arm as _arm
    from personalclaw.triggers.models import Trigger

    try:
        schedule = notification_rules.digest_settings()["schedule"]
    except Exception:
        logger.debug("digest cron: could not read the schedule", exc_info=True)
        return

    try:
        row = store.get(DIGEST_JOB_NAME)
    except Exception:
        logger.debug("digest cron: could not read the trigger store", exc_info=True)
        return

    if row is None:
        try:
            trigger = Trigger(
                id=DIGEST_JOB_NAME,
                name=DIGEST_JOB_NAME,
                kind="clock",
                enabled=True,
                created_by="system",
                spec={"kind": "cron", "expr": schedule},
                workflow={"inline": {"provider": "notification-digest", "config": {}}},
                # `delivery: none` is the store's spelling of the legacy `silent=True`: the digest's
                # OUTPUT is an inbox item, so a cron-result notification about it would be a
                # notification about your notifications.
                delivery="none",
            )
            # The digest writes an inbox item, so `notification-digest` is write-capable and the
            # fence needs the frozen grant (decision 7 — S116). A system-created trigger's opt-in is
            # the code path that created it.
            trigger.capabilities = _screen.capabilities_for_action(trigger)
            armed = _arm(trigger)
            if armed:
                trigger.next_fire_at = armed
            store.upsert(trigger)
            logger.info("registered the notification digest trigger (%s)", schedule)
        except Exception:
            logger.warning("digest cron: registration failed", exc_info=True)
        return

    # The expression lives at `spec.expr` in the store (the legacy read went through
    # `job.schedule.cron_expr`, and reading a FLAT attribute off the job always returned None —
    # which made the old convergence fire on every single startup).
    trigger = row.trigger
    spec = trigger.spec if isinstance(trigger.spec, dict) else {}
    current = spec.get("expr")
    if current != schedule:
        try:
            # Preserve the quietly-losable spec keys (`timezone`/`skip_dates`/`strict`) rather than
            # replacing the spec wholesale — the same contract §1.3 and S101 record for a cadence
            # edit. Re-armed because the next fire is computed FROM the expression.
            trigger.spec = {**spec, "kind": "cron", "expr": schedule}
            armed = _arm(trigger)
            if armed:
                trigger.next_fire_at = armed
            store.upsert(trigger)
            logger.info("digest cron: schedule converged %s → %s", current, schedule)
        except Exception:
            logger.debug("digest cron: schedule update failed", exc_info=True)
