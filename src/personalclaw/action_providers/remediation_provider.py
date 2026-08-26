"""The remediation engine as ONE adaptive-clock trigger (PLATFORM-RESILIENCE §4.3/§4.4 — PR2-8).

**What this replaces.** The engine used to hang off the heartbeat as
`HeartbeatService._maybe_remediate`, carrying its own private scheduler in a
`_remediation_next_ts` float and its own private ownership protocol (a bool return that decided
whether `_legacy_maintenance` ran that tick). §4.3 always said that form was provisional: *"Once
AUTOMATION-SUBSTRATE lands, the engine IS one trigger (adaptive clock kind, `created_by: system`)
on the Automations page … Before that, it hangs off the heartbeat loop as one job."* The substrate
has landed, so the heartbeat job is DELETED and this is the engine's only driver — one mechanism,
not two.

**What re-homing actually buys.** Nothing here re-implements scheduling, capability fencing,
ledgering or delivery: the trigger tick arms the clock, `triggers/screen.py` freezes the grant,
`_record_fire_outcome` writes the run record, and `_deliver_fire_outcome` → `state.notify` →
`notification_rules` routes the outcome. That last one is §4.4's second clause — *"the runs-inbox
'learned overnight' digest picks them up like any other run"* — and it is satisfied by NOT having a
private notification path: a remediation run reaches the digest queue through exactly the code that
carries every other automation's run, so a user whose rule for that kind is `digest` gets it in the
grouped item without anything here knowing the digest exists.

**The adaptive half.** `triggers/arm.cadence_next_fire` picks between two declared cadences using
`spec.health_state`, and stays pure. This provider is the ONE writer of that state, and it re-arms
after the run rather than letting the tick's pre-fire arm stand: the tick arms BEFORE dispatch (§3.1
crash safety), so it can only ever see the PREVIOUS run's state, and a degradation that had to wait
a full healthy sleep before shortening the tick would defeat the cadence entirely.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult

logger = logging.getLogger(__name__)

#: The trigger id. Deterministic, like every other system reconciler's, for the reason
#: `reconcile_digest_cron` records: `tools.create` mints a unique slug, so a convergence keyed on a
#: generated id would add a second engine on every boot instead of recognizing its own.
REMEDIATION_TRIGGER_ID = "system:self-remediation"

#: The provider name. Present in FOUR places that must agree — `action_providers.registry`,
#: `validation.ALLOWED_HOOK_PROVIDERS`, `triggers.screen.WRITE_CAPABLE_PROVIDERS` and here —
#: because a provider registered but missing from one of the sets is a trigger that saves and
#: then refuses to dispatch.
PROVIDER_NAME = "self-remediation"


class SelfRemediationActionProvider(ActionProvider):
    """Run one remediation pass, then re-arm the adaptive clock from the resulting score."""

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "Self-Remediation"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        from personalclaw.config.loader import AppConfig
        from personalclaw.resilience import remediation as _rem

        try:
            cfg = AppConfig.load().resilience.remediation
        except Exception as exc:  # noqa: BLE001 - surface as a result, never raise
            return ActionResult(success=False, error=f"remediation config unreadable: {exc}")

        if not cfg.enabled:
            # Reachable even though the reconciler disables the trigger when the engine is off: a
            # user can re-enable the row by hand on the Triggers page, and a fire that ran the
            # engine against an explicit `enabled=false` would be the config lying.
            return ActionResult(success=True, exit_code=0, stdout="remediation: disabled by config")

        now = time.time()
        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: _rem.run_remediation(
                    target_score=float(cfg.target_score),
                    max_cost_usd=cfg.max_cost_usd,
                    now=now,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - a broken pass must report, not crash the tick
            logger.warning("remediation run failed", exc_info=True)
            return ActionResult(success=False, error=f"remediation failed: {exc}")

        healthy = result.score_after >= _rem.HEALTHY_SCORE
        rearmed = _rearm(healthy=healthy, cfg=cfg, now=now)

        # A maintenance job that failed must not read as a quiet success — the engine owns passes
        # whose absence is invisible (prunes, index reconciliation). Carried through the FIRE's
        # outcome rather than only a log line, so it takes the failure route the user configured
        # (`failure_delivery`) and escalates past a `digest` rule the way any other broken
        # automation does.
        failed = [str(j.get("id")) for j in result.jobs if j.get("status") == "error"]
        summary = (
            f"score {result.score_before:.0f}→{result.score_after:.0f} "
            f"({result.stopped_reason or 'no plan'}); "
            f"{len(result.jobs)} job(s); next in {rearmed or 'unchanged'}"
        )
        if failed:
            return ActionResult(
                success=False,
                exit_code=1,
                stdout=summary,
                error=f"remediation job(s) failed: {', '.join(failed)}",
            )
        return ActionResult(success=True, exit_code=0, stdout=summary)


def create_provider(config: dict[str, Any] | None = None) -> "SelfRemediationActionProvider":
    return SelfRemediationActionProvider()


def _cadence_secs(cfg: Any) -> tuple[int, int]:
    """`(healthy_secs, degraded_secs)` from the remediation config.

    Minutes → seconds here rather than storing minutes on the spec, because `CLOCK_KINDS`' other
    members all measure in seconds (`interval_secs`) and a spec with two units is a spec somebody
    reads wrong.
    """
    healthy = max(1, int(getattr(cfg, "idle_minutes_healthy", 60) or 60)) * 60
    degraded = max(1, int(getattr(cfg, "tick_minutes_degraded", 5) or 5)) * 60
    return healthy, degraded


def _rearm(*, healthy: bool, cfg: Any, now: float) -> str:
    """Write the run's health verdict onto the spec and recompute `next_fire_at`. Returns the label.

    Best-effort: a store write that fails leaves the tick's own pre-fire arm standing, which is a
    cadence that is merely stale rather than a trigger that stops firing. Returns `""` when nothing
    was re-armed, so the run summary says "unchanged" instead of claiming a cadence it did not set.
    """
    try:
        from personalclaw.config.loader import config_dir
        from personalclaw.triggers.arm import arm as _arm
        from personalclaw.triggers.store import TriggerStore

        store = TriggerStore(base_dir=config_dir())
        row = store.get(REMEDIATION_TRIGGER_ID)
        if row is None:
            return ""
        trigger = row.trigger
        healthy_secs, degraded_secs = _cadence_secs(cfg)
        spec = dict(trigger.spec or {})
        spec.update(
            {
                "kind": "adaptive",
                "interval_secs_healthy": healthy_secs,
                "interval_secs_degraded": degraded_secs,
                "health_state": "healthy" if healthy else "degraded",
            }
        )
        trigger.spec = spec
        armed = _arm(trigger, now=now)
        if armed:
            trigger.next_fire_at = armed
        store.upsert(trigger)
        secs = healthy_secs if healthy else degraded_secs
        return f"{max(1, round(secs / 60))}m"
    except Exception:  # noqa: BLE001 - see the docstring
        logger.debug("could not re-arm the remediation trigger", exc_info=True)
        return ""


def reconcile_remediation_trigger(store: Any) -> None:
    """Make the engine's adaptive-clock trigger exist and match the configured cadence. Idempotent.

    CONVERGES rather than only creating, following `reconcile_digest_cron`: both cadences and
    the on/off switch live in `resilience.remediation` config, so a user who edits them in
    Settings must not have to know that a trigger exists somewhere to be re-registered.
    `health_state` is deliberately NOT converged — it is run-produced state, and resetting it on
    every boot would make a degraded install sleep for the healthy interval after a restart.

    Writes the unified trigger store directly, never `crons.json` — S108's bug, recorded in
    `reconcile_digest_cron`'s docstring: the boot import runs BEFORE reconciliation, so a row
    written to the legacy file stays inert until the next boot.

    Best-effort: a scheduler problem must never block startup.
    """
    from personalclaw.config.loader import AppConfig
    from personalclaw.triggers import screen as _screen
    from personalclaw.triggers.arm import arm as _arm
    from personalclaw.triggers.models import Trigger

    try:
        cfg = AppConfig.load().resilience.remediation
    except Exception:
        logger.debug("remediation trigger: config unreadable", exc_info=True)
        return

    try:
        existing = store.get(REMEDIATION_TRIGGER_ID)
    except Exception:
        logger.debug("remediation trigger: could not read the trigger store", exc_info=True)
        return

    try:
        trigger = (
            existing.trigger
            if existing is not None
            else Trigger(
                id=REMEDIATION_TRIGGER_ID,
                name="Self-remediation",
                kind="clock",
                created_by="system",
                # `delivery: inbox` — unlike the digest and the recap, whose OUTPUT is itself a
                # notification, a remediation run's output is a ledger row nobody is watching. §4.4
                # asks for these runs to reach the runs-inbox digest "like any other run", and a
                # `none` destination is muted at `delivery.deliver` before any rule is consulted.
                delivery="inbox",
            )
        )
        healthy_secs, degraded_secs = _cadence_secs(cfg)
        spec = dict(trigger.spec or {})
        spec.update(
            {
                "kind": "adaptive",
                "interval_secs_healthy": healthy_secs,
                "interval_secs_degraded": degraded_secs,
            }
        )
        spec.setdefault("health_state", "healthy")
        trigger.spec = spec
        trigger.enabled = bool(cfg.enabled)
        trigger.workflow = {"inline": {"provider": PROVIDER_NAME, "config": {}}}
        # The engine prunes, re-indexes and (in the judgment lane) spends, unattended, forever. The
        # frozen grant is decision 7's requirement; a system-created trigger's opt-in is the code
        # path that created it.
        trigger.capabilities = _screen.capabilities_for_action(trigger)
        armed = _arm(trigger)
        if armed:
            trigger.next_fire_at = armed
        store.upsert(trigger)
        if existing is None:
            logger.info(
                "registered the self-remediation trigger (healthy %ds / degraded %ds)",
                healthy_secs,
                degraded_secs,
            )
    except Exception:
        logger.warning("remediation trigger: registration failed", exc_info=True)
