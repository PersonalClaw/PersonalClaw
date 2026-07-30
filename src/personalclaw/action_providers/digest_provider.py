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


def reconcile_digest_cron(crons: Any) -> None:
    """Make the digest cron exist and match the configured schedule. Idempotent.

    Converges the schedule rather than only creating the job: the schedule lives in the rules
    store, so a user who edits it in Settings must not have to know that a cron exists
    somewhere to be re-registered. Best-effort — a scheduler problem must never block startup.
    """
    from personalclaw import notification_rules

    try:
        schedule = notification_rules.digest_settings()["schedule"]
    except Exception:
        logger.debug("digest cron: could not read the schedule", exc_info=True)
        return

    try:
        existing = next(
            (j for j in crons.list_jobs(include_disabled=True) if j.name == DIGEST_JOB_NAME),
            None,
        )
    except Exception:
        logger.debug("digest cron: could not list jobs", exc_info=True)
        return

    action = {"provider": "notification-digest", "config": {}}
    if existing is None:
        try:
            crons.add_job(
                DIGEST_JOB_NAME,
                action=action,
                cron_expr=schedule,
                created_by="system",
                # Silent: the digest's OUTPUT is an inbox item. A cron-result notification
                # about it would be a notification about your notifications.
                silent=True,
            )
            logger.info("registered the notification digest cron (%s)", schedule)
        except Exception:
            logger.warning("digest cron: registration failed", exc_info=True)
        return

    # The job stores its schedule under `job.schedule.cron_expr` (a ScheduleDefinition);
    # `update_job` takes a flat `cron_expr=` kwarg. Reading a flat attribute off the job
    # would always be None, so this would "converge" on every startup.
    current = getattr(getattr(existing, "schedule", None), "cron_expr", None)
    if current != schedule:
        try:
            crons.update_job(existing.id, cron_expr=schedule)
            logger.info("digest cron: schedule converged %s → %s", current, schedule)
        except Exception:
            logger.debug("digest cron: schedule update failed", exc_info=True)
