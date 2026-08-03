"""The one clock loop: tick → dispatch → execute (§3 / §3.1 — S100).

**🔴 THE CUTOVER, and what measuring made unavoidable.** Until this session
`ScheduleService._arm_timer`
was the ONLY thing that fired a clock trigger. S88 shipped `service.tick()`, S96 taught it to arm,
S97 made `overlap` enforce, S98 imported the crons and S99 re-pointed the API's read — but no loop
ever CALLED the tick. Measured directly:

    boot starts ScheduleService: True
    boot starts a TICK loop    : False

So a trigger created the new way (store only, as `tools.create` writes it) had **no firing path at
all**: the legacy service cannot see `triggers.json`, and nothing ran the engine that can.
Re-pointing
the API's writes before this would have produced silently dead automations.

**And running both loops would DOUBLE-FIRE.** Measured against the owner's real store after the boot
migration: the legacy timer would fire `['j-at','j-cron','j-every','j-seq']` and the tick would fire
`['j-at','j-cron']` — a real overlap of two live automations. So this is a switch-over, not an
addition: the tick becomes the sole clock engine and the legacy timer is **not armed**.

**What the legacy service keeps doing.** `_arm_timer` is only its firing loop; the rest of the class
is still the CRUD surface and the run-history store the API reads (verified: `_load()` + `list_runs`
work with no timer armed, `_timer_task is None`). So `ScheduleService` is still constructed and
loaded, and only its timer is retired here. Its CRUD retires when the writes re-point (the next
session), and the class itself when nothing reads it.

**The loop owns no policy.** It sleeps on `TickResult.next_sleep`, hands each fire to S89's
dispatcher and S90's executor, and releases claims through them. Every decision — due-ness,
gates, arming, retirement — already belongs to `service.tick`, so this file is a driver: what was
missing,
and nothing more.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

#: Ceiling on one loop iteration's sleep. `TickResult.next_sleep` is already capped at
#: `service.MAX_SLEEP_SECS` (30s, the mtime-`_sync` propagation contract for a store another process
#: writes); this is a belt-and-braces bound so a malformed result cannot park the loop for hours.
MAX_ITERATION_SLEEP_SECS = 60.0


async def run_forever(
    store: Any,
    *,
    runner: Callable[[dict[str, Any]], Awaitable[Any]],
    sessions: Any = None,
    base_dir: Any = None,
    user_active: Callable[[], bool] | None = None,
) -> None:
    """Drive the clock forever: tick, dispatch what fired, execute, sleep. NEVER returns normally.

    Cancellation-safe: `asyncio.CancelledError` propagates so `_shutdown` can stop the loop, while
    every other exception is logged and the loop continues. A clock loop that died on one bad tick
    would silently retire every automation on the machine — the failure mode this whole
    program keeps
    finding, and the one a scheduler can least afford.

    `runner` is injected (the LLM turn / action dispatch), matching the seam `ScheduleService` uses
    for `_on_job` and S90's executor uses for its runner. That is what lets the entire chain
    be driven
    end to end in a test without a model.
    """
    while True:
        sleep_for = MAX_ITERATION_SLEEP_SECS
        try:
            result = await tick_once(
                store,
                runner=runner,
                sessions=sessions,
                base_dir=base_dir,
                user_active=bool(user_active()) if user_active else False,
            )
            sleep_for = min(max(0.5, float(result.next_sleep)), MAX_ITERATION_SLEEP_SECS)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must outlive any single tick's failure
            logger.warning("clock tick failed; continuing", exc_info=True)
        await asyncio.sleep(sleep_for)


async def tick_once(
    store: Any,
    *,
    runner: Callable[[dict[str, Any]], Awaitable[Any]],
    sessions: Any = None,
    base_dir: Any = None,
    user_active: bool = False,
    now: float = 0.0,
) -> Any:
    """One iteration: tick, dispatch each fire, drain each target session. Returns the `TickResult`.

    Separate from `run_forever` so a test (and `automation doctor`) can drive exactly one iteration
    rather than racing a background task — the same reason `tick()` itself is a pure decision.

    §3.2's order is preserved: the scheduler never executes directly. `tick` decides, S89's
    dispatcher enqueues onto the target session's inbox, and S90's executor drains it — so a
    crash between decision and execution leaves the payload in the inbox, not lost.
    """
    from personalclaw.triggers import executor as ex
    from personalclaw.triggers import service as svc
    from personalclaw.triggers import wakeup as wk

    # `now` is threaded so a test (and the doctor's dry run) can drive an exact instant. Without
    # it the loop is only testable against wall-clock, which makes an armed-for-later trigger
    # untestable — the first probe of this file silently fired nothing for exactly that reason.
    result = await svc.tick(store, now=now, base_dir=base_dir, user_active=user_active)
    if not result.fires:
        return result

    if sessions is None:
        # No session manager (an API-only process): the fires stay decided-but-undispatched rather
        # than being dropped silently. `tick` already persisted each next fire and wrote a
        # ledger row,
        # so this is visible rather than lost.
        logger.debug("clock tick produced %d fires with no session manager", len(result.fires))
        return result

    deliveries = wk.dispatch_fires(sessions, result.fires, now=now)
    summary = wk.summary(deliveries)
    if summary.get("dropped"):
        logger.warning("clock dispatch dropped %s wakeups", summary["dropped"])

    for fire in result.fires:
        key = wk.session_key_for(fire.trigger.id)
        try:
            await ex.drain(sessions, key, runner, now=now, base_dir=base_dir)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one trigger's drain must not strand the others
            logger.warning("drain failed for %s", fire.trigger.id, exc_info=True)
    return result
