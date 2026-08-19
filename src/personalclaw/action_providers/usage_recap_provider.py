"""The monthly usage-recap action (MODEL-ROUTING-TELEMETRY MRT-3).

Renders `routing.usage.usage_recap(month)` for the calendar month that just closed and hands it
to `DashboardState.notify()` — so the recap inherits the rules engine wholesale: the global gate
(mute-all / min-severity / quiet hours) runs first, then the per-`(source, kind)` rule, whose
registered default for `system/usage_recap` is `digest`.

**Deterministic, no model call.** Same reasoning as `digest_provider`: the recap is a template
render over a fold of things that already happened, and the one thing a money surface must never
do is invent a figure. `usage_recap` is a pure function of the fold.

**Exactly one delivery per month.** The cron fires monthly, but a monthly cron firing twice is a
real event, not a hypothetical: the boot sweep re-arms an overdue trigger (so a machine asleep
across the 1st fires on wake), `reconcile_usage_recap_cron` re-arms on a schedule change, and a
user can fire a trigger by hand. So the month is the idempotency key, recorded in
`usage_recap_sent.json` and checked BEFORE rendering. Without it the recap would be "one
notification per boot in the first week of the month".

The mark is written whether or not the notification survived the gate. A recap suppressed by the
user's own quiet hours is suppressed, not deferred — retrying until it lands is exactly the
escalation a quiet-hours setting exists to refuse, and a month-old spend summary has no urgency
to justify it. `delivered` records which happened, so the state is legible rather than merely
absent.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.action_providers.services import get_action_services

logger = logging.getLogger(__name__)

#: The cron job name. Prefixed like `digest_provider`'s so a reconcile finds its own row and
#: never a user's hand-made one.
USAGE_RECAP_JOB_NAME = "system:usage-recap"

#: 09:00 on the 1st of the month. The 1st because the recap covers the month that CLOSED — on
#: the 31st there is still a day of spend to come, and a recap that undercounts its own month is
#: worse than one that arrives a few hours late.
USAGE_RECAP_SCHEDULE = "0 9 1 * *"

#: Where the once-per-month mark lives. A sibling of `usage_stats.json` under the home, because
#: it is bookkeeping ABOUT the fold.
_MARK_FILE = "usage_recap_sent.json"


def previous_month(now: datetime | None = None) -> str:
    """The ``YYYY-MM`` of the calendar month before *now* (UTC by default).

    Separate from `execute` so a test can pin the boundary arithmetic — December → the previous
    January is the case an f-string built from `month - 1` gets wrong.
    """
    ref = now or datetime.now(tz=timezone.utc)
    year, month = ref.year, ref.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


def _mark_path(home: Path) -> Path:
    return Path(home) / _MARK_FILE


def read_mark(home: Path) -> dict[str, Any]:
    """The recap bookkeeping doc, or an empty one. Never raises: an unreadable mark must not
    become a failed action, and the worst case of treating it as absent is one extra recap."""
    try:
        raw = json.loads(_mark_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_mark(home: Path, month: str, *, delivered: bool) -> None:
    from personalclaw.atomic_write import atomic_write

    doc = read_mark(home)
    doc["last_month"] = month
    doc["last_at"] = datetime.now(tz=timezone.utc).isoformat()
    doc["delivered"] = bool(delivered)
    try:
        path = _mark_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(doc, indent=2) + "\n")
    except OSError:
        # Best-effort, but say so loudly: a mark that did not persist means the NEXT fire in
        # this month sends a second recap, which is the one property this file exists to hold.
        logger.warning("usage recap: could not persist the once-per-month mark", exc_info=True)


class UsageRecapActionProvider(ActionProvider):
    """Render the closed month's spend recap and emit it through the rules engine."""

    @property
    def name(self) -> str:
        return "usage-recap"

    @property
    def display_name(self) -> str:
        return "Monthly Usage Recap"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        from personalclaw.config.loader import config_dir
        from personalclaw.notification_kinds import USAGE_RECAP
        from personalclaw.routing import usage

        home = Path(config_dir())
        # An explicit `month` is for a hand-fired backfill and for the tests; the cron passes
        # nothing and gets the month that just closed.
        month = str((action_config or {}).get("month") or "").strip() or previous_month()

        if read_mark(home).get("last_month") == month:
            # A SUCCESS with nothing to do. Reporting a duplicate suppression as a failure would
            # light up the trigger's error surface on every boot in the first week of a month.
            return ActionResult(
                success=True, exit_code=0, stdout=f"usage recap: {month} already sent"
            )

        services = get_action_services()
        state = getattr(services, "state", None) if services is not None else None
        if state is None:
            # No mark written: nothing was attempted, so the next fire should still try.
            return ActionResult(success=False, error="usage recap: no dashboard state to notify")

        try:
            body = usage.usage_recap(month, home=home)
        except Exception as exc:  # noqa: BLE001 - surface as a result, never raise
            return ActionResult(success=False, error=f"usage recap: render failed: {exc}")

        try:
            state.notify(USAGE_RECAP, f"Usage recap — {month}", body, meta={"month": month})
        except Exception as exc:  # noqa: BLE001
            return ActionResult(success=False, error=f"usage recap: delivery failed: {exc}")

        _write_mark(home, month, delivered=True)
        return ActionResult(success=True, exit_code=0, stdout=f"usage recap: emitted for {month}")


def create_provider(config: dict[str, Any] | None = None) -> "UsageRecapActionProvider":
    return UsageRecapActionProvider()


def reconcile_usage_recap_cron(store: Any) -> None:
    """Make the monthly recap trigger exist. Idempotent, best-effort.

    Built as a `Trigger` with a DETERMINISTIC id rather than through `tools.create`, for the
    reason `digest_provider` and `app_crons` both record: `tools.create` mints its own unique
    slug, so every restart would add another recap trigger instead of recognizing its own.

    Writes the unified trigger store directly — `crons.json` is imported at boot BEFORE
    reconciliation runs, so a row written there would stay inert until the next boot (the S108
    bug `reconcile_digest_cron`'s docstring records).

    Unlike the digest there is no user-facing schedule setting to converge: the recap's cadence
    is "monthly", which is the meaning of the feature rather than a preference. So an existing
    row is left exactly as it is — including a schedule the user edited by hand.
    """
    from personalclaw.triggers import screen as _screen
    from personalclaw.triggers.arm import arm as _arm
    from personalclaw.triggers.models import Trigger

    try:
        row = store.get(USAGE_RECAP_JOB_NAME)
    except Exception:
        logger.debug("usage-recap cron: could not read the trigger store", exc_info=True)
        return
    if row is not None:
        return

    try:
        trigger = Trigger(
            id=USAGE_RECAP_JOB_NAME,
            name=USAGE_RECAP_JOB_NAME,
            kind="clock",
            enabled=True,
            created_by="system",
            spec={"kind": "cron", "expr": USAGE_RECAP_SCHEDULE},
            workflow={"inline": {"provider": "usage-recap", "config": {}}},
            # The recap's OUTPUT is a notification, so a cron-result notification about it
            # would be a notification about your notification (`digest_provider`'s reasoning,
            # and the same spelling of the legacy `silent=True`).
            delivery="none",
        )
        # Emitting a notification is putting something in front of the user unattended, so the
        # action is write-capable and the fence needs the frozen grant (decision 7). A
        # system-created trigger's opt-in is the code path that created it.
        trigger.capabilities = _screen.capabilities_for_action(trigger)
        armed = _arm(trigger)
        if armed:
            trigger.next_fire_at = armed
        store.upsert(trigger)
        logger.info("registered the monthly usage-recap trigger (%s)", USAGE_RECAP_SCHEDULE)
    except Exception:
        logger.warning("usage-recap cron: registration failed", exc_info=True)
