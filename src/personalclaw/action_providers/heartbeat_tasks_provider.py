"""The HEARTBEAT.md task queue as one visible trigger (``system:heartbeat-tasks``).

**What this replaces.** `HeartbeatService` read `workspace/HEARTBEAT.md` every 60 seconds and ran
each task as an unattended agent turn, with no UI: no schedule, no last run, no way to turn it
off. The agent is told to use the file for "keep checking until done" work
(`config/prompts/background.md`), so it was a real automation that ran real turns, and the one
place a user manages automations did not list it. It is a system trigger now, like
`system:self-remediation`: the Triggers page shows its cadence, its runs and their outcomes, and
its switch — off means the queue is not read.

Nothing here re-implements scheduling, overlap or ledgering: the clock tick arms the interval,
`overlap: skip` keeps a long pass from stacking a second one, and `run_record.record_run` writes
each pass's run. The TASKS still run through the gateway's heartbeat turn (`heartbeat
.task_runner`): the background prompt, the unattended approval policy, and delivery of each
finished task's result to its `<!-- deliver:… -->` target. A task runs only once the owner
allowed it (`heartbeat`): the agent writes this file, and its words do not run unattended with its
tools until the owner says so. A pass leaves every other task where it is, and says how many wait.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult

logger = logging.getLogger(__name__)

#: Deterministic, like every system reconciler's id, so a boot recognizes its own row instead of
#: adding a second one.
HEARTBEAT_TASKS_TRIGGER_ID = "system:heartbeat-tasks"

#: The provider name. Present in FOUR places that must agree — `action_providers.registry`,
#: `validation.ALLOWED_HOOK_PROVIDERS`, `triggers.screen.WRITE_CAPABLE_PROVIDERS` and
#: `guardrails.rungs` — because a provider missing from one of them saves and then refuses to run.
PROVIDER_NAME = "heartbeat-tasks"

#: How often the queue is read: the cadence the heartbeat loop always had.
INTERVAL_SECS = 60

#: Where a task runs: a session of its own, fresh for every task, named the way a scheduled run's
#: is (``cron:<trigger>:<run>``). A task is not a chore (a call of its own to a model offered no
#: tools, ``chores.run_chore``): an allowed task runs with the owner's agent and its tools, as the
#: Allow dialog says (`heartbeat.consent`).
TASK_SESSION_PREFIX = f"cron:{HEARTBEAT_TASKS_TRIGGER_ID}:"


def task_session_key() -> str:
    """A new session key for one heartbeat task."""
    return TASK_SESSION_PREFIX + uuid.uuid4().hex[:8]


# A pass the gateway missed while it was down is done by the next pass, so the restart review
# offers no card for it: `triggers.models.MISSED_FIRE_SUPERSEDED_PROVIDERS` lists this provider.


class HeartbeatTasksActionProvider(ActionProvider):
    """Run every task in HEARTBEAT.md once, and keep the unfinished ones for the next pass."""

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "Heartbeat tasks"

    @property
    def internal(self) -> bool:
        """The system trigger's plumbing: its config is written by the reconciler, not the form."""
        return True

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        from personalclaw.heartbeat import HEARTBEAT_FILE, run_tasks, task_runner

        runner = task_runner()
        if runner is None:
            return ActionResult(
                success=False,
                error="the heartbeat task runner is not wired: the gateway has not started",
            )
        try:
            done = await run_tasks(runner)
        except Exception as exc:  # noqa: BLE001 - a broken pass reports, never crashes the tick
            logger.warning("heartbeat tasks pass failed", exc_info=True)
            return ActionResult(success=False, error=f"could not run {HEARTBEAT_FILE}: {exc}")
        waiting = (
            f"{done.waiting} waiting for your Allow on the Triggers page" if done.waiting else ""
        )
        if done.ran == 0:
            # `skip`: recorded as the inert `skipped_noop` (`schedule_history.status_for_result`),
            # which folds out of the default history, so a pass every minute over a queue with
            # nothing to run does not bury the passes that ran a task. The trigger's panel lists
            # the waiting tasks, each with its Allow.
            return ActionResult(
                success=True,
                stdout=f"no task ran: {waiting}" if waiting else f"no tasks in {HEARTBEAT_FILE}",
                outcome="skip",
            )
        summary = (
            f"{done.ran} task{'s' if done.ran != 1 else ''} ran: {done.done} done, "
            f"{done.kept} kept for the next pass"
        ) + (f"; {waiting}" if waiting else "")
        if done.failed:
            return ActionResult(
                success=False,
                exit_code=1,
                stdout=summary,
                error=(
                    f"{len(done.failed)} task(s) failed and are kept to retry: "
                    + "; ".join(done.failed)
                ),
            )
        return ActionResult(success=True, stdout=summary)


def create_provider(config: dict[str, Any] | None = None) -> "HeartbeatTasksActionProvider":
    return HeartbeatTasksActionProvider()


def reconcile_heartbeat_tasks_trigger(store: Any) -> None:
    """Make the queue's trigger exist. Idempotent, and it never overrides your choices.

    Creates the row on a home that has none: enabled, every `INTERVAL_SECS`. Every later boot
    converges only what the code owns, the action and its grant, so a user who switched it off or
    gave it another cadence on the Triggers page finds that choice after a restart. Also creates
    HEARTBEAT.md, so the agent finds the queue.

    Best-effort: a scheduler problem must never block startup.
    """
    from personalclaw.heartbeat import ensure_heartbeat_file
    from personalclaw.triggers import screen as _screen
    from personalclaw.triggers.arm import arm as _arm
    from personalclaw.triggers.models import Trigger

    try:
        ensure_heartbeat_file()
    except Exception:
        logger.debug("heartbeat tasks: could not create the queue file", exc_info=True)
    try:
        existing = store.get(HEARTBEAT_TASKS_TRIGGER_ID)
    except Exception:
        logger.debug("heartbeat tasks: could not read the trigger store", exc_info=True)
        return
    try:
        trigger = (
            existing.trigger
            if existing is not None
            else Trigger(
                id=HEARTBEAT_TASKS_TRIGGER_ID,
                name="Heartbeat tasks",
                kind="clock",
                created_by="system",
                enabled=True,
                # Each finished task delivers its OWN result to its `deliver:` target, so a pass's
                # report would be a note about the notes; a failed pass still reaches the inbox
                # through `failure_delivery`.
                delivery="none",
            )
        )
        if existing is None:
            trigger.spec = {"kind": "interval", "interval_secs": INTERVAL_SECS}
        trigger.workflow = {"inline": {"provider": PROVIDER_NAME, "config": {}}}
        # The frozen grant (decision 7): each task is an unattended agent turn, and a
        # system-created trigger's opt-in is the code path that created it. Whether a task runs at
        # all is the owner's yes to that task, not this grant (`heartbeat.allowed`).
        trigger.capabilities = _screen.capabilities_for_action(trigger)
        if not trigger.next_fire_at:
            armed = _arm(trigger)
            if armed:
                trigger.next_fire_at = armed
        store.upsert(trigger)
        if existing is None:
            logger.info("registered the heartbeat tasks trigger (every %ds)", INTERVAL_SECS)
    except Exception:
        logger.warning("heartbeat tasks trigger reconcile failed", exc_info=True)
