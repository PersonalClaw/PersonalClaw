"""The periodic identity report as ONE clock trigger (LEARNING-VISIBILITY T2.5 — LV-4).

**What this closes.** `learning_report.py` shipped the whole composition and delivery half —
`compose_identity_report`, the narrative pass, the versioned artifact, the notify-gated inbox
item — plus a `POST /api/learning/identity-report` a user could reach by hand. Nothing
scheduled it. So the plan's "scheduled (default monthly, configurable) background job" existed
as a function with one caller, which is the shape this codebase calls a delivery path nothing
drives.

**Nothing here re-implements scheduling.** The trigger tick arms the clock,
`triggers/screen.py` freezes the grant, `guardrails/rungs.py` classifies the action,
`_record_fire_outcome` writes the run record and `_deliver_fire_outcome` routes the outcome.
This module owns exactly two things a scheduler cannot know: which cron expression each cadence
means, and that `off` means do not run.

**`off` is refused on the PRODUCING side, not just the arming side.** The reconciler disables
the row, AND `execute` returns before composing anything. Both, because they answer different
questions: a disabled row does not fire, but a user can re-enable one by hand on the Triggers
page, and a fire that produced a report against an explicit `off` would be the config lying.
This is `remediation_provider`'s doubled defence and it is stated there for the same reason —
a cadence that computes a next-fire time and then discards the output is an inert control, and
computing the schedule is not the part that matters.

**One delivery function, two callers.** The cron and the POST route both call
`deliver_identity_report`, so the hand-run and the scheduled run cannot diverge — and the
period-keyed `delivery_dedup_key` means a hand-run followed by a scheduled one inside the same
period reuses the row instead of pinging twice.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.action_providers.services import get_action_services

logger = logging.getLogger(__name__)

#: The trigger id. Deterministic, like every other system reconciler's, for the reason
#: `reconcile_digest_cron` records: `tools.create` mints a unique slug, so a convergence keyed on
#: a generated id would add a second report trigger on every boot instead of recognizing its own.
IDENTITY_REPORT_TRIGGER_ID = "system:learning-identity-report"

#: The provider name. Present in FIVE places that must agree — `action_providers.registry`,
#: `validation.ALLOWED_HOOK_PROVIDERS`, `triggers.screen.WRITE_CAPABLE_PROVIDERS`,
#: `guardrails.rungs`' action table and here — because a provider registered but missing from one
#: of them is a trigger that validates, saves, and then refuses to dispatch.
PROVIDER_NAME = "identity-report"

#: Cron per cadence. 09:00 on the 1st for monthly (the same hour and the same reasoning as
#: `usage_recap_provider`: the report describes a period that has CLOSED), 09:00 Monday for
#: weekly. `off` is absent on purpose — there is no expression that means "never", so the
#: reconciler disables the row instead of inventing one, which is also what keeps a hand
#: re-enable meaningful.
_CADENCE_CRON: dict[str, str] = {"monthly": "0 9 1 * *", "weekly": "0 9 * * 1"}


class IdentityReportActionProvider(ActionProvider):
    """Compose, persist and surface one identity report on the configured cadence."""

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "Identity Report"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        from personalclaw.learning_report import (
            CADENCE_OFF,
            cadence_window_days,
            configured_cadence,
            deliver_identity_report,
        )

        cadence = configured_cadence()
        if not cadence:
            # `configured_cadence` returns "" only when the config could not be READ. Reported as a
            # failure rather than defaulted, following `remediation_provider`: defaulting to
            # `monthly` here would deliver a report to someone who had turned it off.
            return ActionResult(success=False, error="identity report: cadence config unreadable")
        if cadence == CADENCE_OFF:
            # 🔴 THE PRODUCING-SIDE REFUSAL. Not "compose and drop" and not "arm for never" —
            # nothing is gathered, no artifact version is minted and no inbox row is raised. A
            # SUCCESS with nothing to do, like `usage_recap`'s already-sent branch: reporting a
            # deliberate opt-out as a failure would light up the trigger's error surface forever.
            return ActionResult(success=True, exit_code=0, stdout="identity report: cadence is off")

        services = get_action_services()
        state = getattr(services, "state", None) if services is not None else None
        if state is None:
            return ActionResult(
                success=False, error="identity report: no dashboard state to deliver through"
            )

        try:
            delivery = await deliver_identity_report(
                state,
                window_days=cadence_window_days(cadence),
                vs=_vector_store(state),
                narrate=True,
            )
        except Exception as exc:  # noqa: BLE001 - surface as a result, never raise
            logger.warning("identity report: delivery failed", exc_info=True)
            return ActionResult(success=False, error=f"identity report: delivery failed: {exc}")

        summary = (
            f"identity report ({cadence}): artifact "
            f"{delivery.artifact_slug or '—'} v{delivery.artifact_version} · "
            f"inbox {delivery.inbox_item_id or '—'}"
        )
        # A PARTIAL delivery is a failure, not a quiet success. `deliver_identity_report` never
        # raises — it returns the record with an empty slug or item id — so without this the one
        # failure mode that matters (the document was not persisted) would read as a green run.
        # Quiet hours does NOT land here: the gate drops the notification and the inbox row is
        # still created, so `inbox_item_id` stays non-empty.
        missing = [
            what
            for what, got in (
                ("artifact", delivery.artifact_slug),
                ("inbox item", delivery.inbox_item_id),
            )
            if not got
        ]
        if missing:
            return ActionResult(
                success=False,
                exit_code=1,
                stdout=summary,
                error=f"identity report: no {' and no '.join(missing)} was written",
            )
        return ActionResult(success=True, exit_code=0, stdout=summary)


def create_provider(config: dict[str, Any] | None = None) -> "IdentityReportActionProvider":
    return IdentityReportActionProvider()


def _vector_store(state: Any) -> Any:
    """The live memory store's vector half, or ``None``.

    READ off whatever the gateway already wired, never constructed here. Two reasons: an action
    provider that imported `dashboard.handlers._shared._get_memory` would break the layering
    `action_providers/services.py` keeps (it imports `DashboardState` under `TYPE_CHECKING`
    only), and a second `VectorMemoryStore` would be a second connection to `memory.db` opened
    on a cron for a read that the live store can already serve.

    ``None`` is an honest degrade, not a silent one: `compose_identity_report` documents that
    omitting ``vs`` yields the skill and proposal sections only, and the facet/lesson sections
    then read as empty because there is nothing attached to read.
    """
    builder = getattr(state, "context_builder", None)
    memory = (
        getattr(builder, "memory", None)
        if builder is not None
        # `_get_memory`'s API-only fallback caches the standalone store on the state object; read
        # it if it is there rather than building a third one.
        else getattr(state, "_standalone_memory", None)
    )
    return getattr(memory, "vector_store", None)


def reconcile_identity_report_trigger(store: Any) -> None:
    """Make the report's clock trigger exist and match the configured cadence. Idempotent.

    CONVERGES rather than only creating, following `reconcile_digest_cron`: the cadence lives in
    `learning.identity_report_cadence`, so a user who changes it on the Learning page must not
    have to know that a trigger exists somewhere to be re-registered. `off` DISABLES the row
    rather than deleting it — a deleted row is indistinguishable from a feature that was never
    built, and `remediation_provider` makes the same call so the switch stays visible on the
    Triggers page.

    Writes the unified trigger store directly, never `crons.json` — S108's bug, recorded in
    `reconcile_digest_cron`'s docstring: the boot import runs BEFORE reconciliation, so a row
    written to the legacy file stays inert until the next boot.

    Best-effort: a scheduler problem must never block startup. An unreadable cadence leaves the
    row exactly as it was found, rather than guessing one.
    """
    from personalclaw.learning_report import CADENCE_OFF, DEFAULT_CADENCE, configured_cadence
    from personalclaw.triggers import screen as _screen
    from personalclaw.triggers.arm import arm as _arm
    from personalclaw.triggers.models import Trigger

    cadence = configured_cadence()
    if not cadence:
        logger.debug("identity-report trigger: cadence unreadable")
        return

    try:
        existing = store.get(IDENTITY_REPORT_TRIGGER_ID)
    except Exception:
        logger.debug("identity-report trigger: could not read the trigger store", exc_info=True)
        return

    try:
        trigger = (
            existing.trigger
            if existing is not None
            else Trigger(
                id=IDENTITY_REPORT_TRIGGER_ID,
                name="Identity report",
                kind="clock",
                created_by="system",
                # `delivery: none` is the store's spelling of the legacy `silent=True`, and the
                # same call `digest_provider` and `usage_recap_provider` make: this run's OUTPUT
                # is an inbox item and a notification, so a cron-result notification about it
                # would be a notification about your notification.
                delivery="none",
            )
        )
        spec = dict(trigger.spec or {})
        # An `off` row keeps the expression it would use if switched back on, so the Triggers page
        # reads "monthly, disabled" instead of a blank schedule — and a hand re-enable does
        # something coherent. `enabled` is the only thing `off` changes here.
        spec.update(
            {"kind": "cron", "expr": _CADENCE_CRON.get(cadence, _CADENCE_CRON[DEFAULT_CADENCE])}
        )
        trigger.spec = spec
        trigger.enabled = cadence != CADENCE_OFF
        trigger.workflow = {"inline": {"provider": PROVIDER_NAME, "config": {}}}
        # The run writes a durable artifact, raises an inbox row and spends one background model
        # call, unattended, forever. The frozen grant is decision 7's requirement; a
        # system-created trigger's opt-in is the code path that created it.
        trigger.capabilities = _screen.capabilities_for_action(trigger)
        armed = _arm(trigger)
        if armed:
            trigger.next_fire_at = armed
        store.upsert(trigger)
        if existing is None:
            logger.info("registered the identity-report trigger (%s)", cadence)
    except Exception:
        logger.warning("identity-report trigger: registration failed", exc_info=True)
