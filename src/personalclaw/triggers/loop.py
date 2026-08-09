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
import time
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

#: Ceiling on one loop iteration's sleep. `TickResult.next_sleep` is already capped at
#: `service.MAX_SLEEP_SECS` (30s, the mtime-`_sync` propagation contract for a store another process
#: writes); this is a belt-and-braces bound so a malformed result cannot park the loop for hours.
MAX_ITERATION_SLEEP_SECS = 60.0

#: Cap on resumes held across ticks for a session that never becomes ready (crit 7 — S142).
#: §3.2 says a resume is never dropped, and this is the bounded exception to that: a session that
#: stays unready forever would otherwise grow the queue without limit, and an OOM takes down every
#: automation rather than one. Two hundred is far past any real parked-approval population, so
#: reaching it means something is wrong — which is why crossing it logs rather than trimming
#: quietly.
MAX_PENDING_RESUMES = 200


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
    # Resumes whose session was not ready last tick. Owned HERE, by the only thing that outlives a
    # tick: `tick_once` is deliberately stateless so a test can drive one iteration, and a retry
    # queue held inside it would be discarded on every return — which is the same silent drop
    # §3.2 refuses. Bounded, and the bound drops the OLDEST: see `MAX_PENDING_RESUMES`.
    pending: list[Any] = []
    while True:
        sleep_for = MAX_ITERATION_SLEEP_SECS
        try:
            result = await tick_once(
                store,
                runner=runner,
                sessions=sessions,
                base_dir=base_dir,
                user_active=bool(user_active()) if user_active else False,
                pending_resumes=pending,
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
    pending_resumes: list[Any] | None = None,
) -> Any:
    """One iteration: tick, dispatch each fire, drain each target session. Returns the `TickResult`.

    Separate from `run_forever` so a test (and `automation doctor`) can drive exactly one iteration
    rather than racing a background task — the same reason `tick()` itself is a pure decision.

    §3.2's order is preserved: the scheduler never executes directly. `tick` decides, S89's
    dispatcher enqueues onto the target session's inbox, and S90's executor drains it — so a
    crash between decision and execution leaves the payload in the inbox, not lost.

    `pending_resumes` is the caller's cross-tick retry list, mutated in place. Owned by
    `run_forever` because this function is deliberately stateless — see that function.
    """
    from personalclaw.triggers import executor as ex
    from personalclaw.triggers import service as svc
    from personalclaw.triggers import wakeup as wk

    # `now` is threaded so a test (and the doctor's dry run) can drive an exact instant. Without
    # it the loop is only testable against wall-clock, which makes an armed-for-later trigger
    # untestable — the first probe of this file silently fired nothing for exactly that reason.
    result = await svc.tick(store, now=now, base_dir=base_dir, user_active=user_active)

    # 🔴 THE TWO SEPARATE WAKE SOURCES, both BEFORE the early return (§3.2 / crit 7 — S142).
    #
    # The spool: §3 says "sync-context fires spool to `trigger-spool.jsonl`, drained on next tick",
    # and `service.drain_spooled_fires`'s own docstring says the spool is a SEPARATE wake source —
    # "a tick with no due clock trigger must still drain it, and burying the drain inside the
    # due-set walk would skip it exactly when the machine was otherwise idle". It had no caller, so
    # a fire parked by a sync CLI memory write sat on disk forever.
    #
    # The resume retries: a resume carries a gate answer for a parked run, and §3.2 refuses to let
    # anyone drop one. Both sit above `if not result.fires` for the same reason — an idle due-set is
    # exactly when a spooled fire and a stranded approval are waiting, and returning early there
    # would skip them precisely then.
    spooled = _drain_spool(now=now)
    retried = _retry_pending_resumes(sessions, pending_resumes)

    if not result.fires:
        if spooled or retried:
            logger.debug("idle tick drained %d spooled, retried %d resumes", spooled, retried)
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
    # 🔴 `summary()` has NO "dropped" key — measured, it returns
    # `{total, delivered, by_disposition, retry}` — so this warning could never fire and a
    # `no_session` delivery (a fire that reached nobody) was logged nowhere. Third key-mismatch in
    # this criterion, same shape as the missed-review one: a live check reading a name its producer
    # does not emit is worse than no check, because the silence reads as "nothing wrong".
    no_session = int((summary.get("by_disposition") or {}).get("no_session", 0) or 0)
    if no_session:
        logger.warning("clock dispatch reached no session for %d wakeups", no_session)

    _hold_resumes(wk, deliveries, pending_resumes)

    for fire in result.fires:
        key = wk.session_key_for(fire.trigger.id)
        try:
            await ex.drain(sessions, key, runner, now=now, base_dir=base_dir)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one trigger's drain must not strand the others
            logger.warning("drain failed for %s", fire.trigger.id, exc_info=True)
    return result


def _drain_spool(*, now: float = 0.0) -> int:
    """Consume the sync-context spool. Returns how many fires were drained.

    Peek-then-deliver-then-ack, which is why `drain_spool` and `clear_spool` are two calls: the
    spool is only truncated after the envelopes have been handed on, so a crash mid-drain re-drains
    rather than losing them. `clear_spool(handled=...)` keeps whatever arrived during the drain.

    A spooled fire re-enters through `emit_event`, the SAME seam a live source write uses —
    not through a second dispatch path. That is what stops a spooled fire skipping the gates a live
    one walks, which is exactly how the `web_watch` screen gap (S134) happened.
    """
    from personalclaw.triggers import service as svc

    try:
        envelopes, bad = svc.drain_spooled_fires()
    except Exception:  # noqa: BLE001 - a broken spool must not stop the clock
        logger.warning("spool drain failed; leaving the spool for the next tick", exc_info=True)
        return 0
    if bad:
        # Loud, not silent: a damaged line is a fire nobody will ever run, which criterion 8 counts
        # as a drop. It is skipped rather than retried forever (an unparseable line cannot become
        # parseable), so the log is the only record there will be.
        logger.warning("spool drain skipped %d unparseable line(s)", bad)
    if not envelopes:
        return 0

    handled = 0
    for envelope in envelopes:
        payload = getattr(envelope, "payload", None) or {}
        try:
            from personalclaw.event_triggers import SOURCE_MEMORY, emit_event

            # `kind` is `f"{source}.{event_type}"` (EIAT-1); split on the first dot so the
            # spooled fire re-enters scoped to the source it came from. Legacy envelopes with
            # no source prefix fall back to memory — the only source that spooled before EIAT-1.
            kind = str(getattr(envelope, "kind", "") or "")
            source, _, event_type = kind.partition(".")
            if not event_type:
                source, event_type = SOURCE_MEMORY, kind
            meta = payload.get("meta")
            emit_event(
                source=source,
                event_type=event_type,
                key=str(payload.get("key", "") or ""),
                value=str(payload.get("value", "") or ""),
                now=now or time.time(),
                meta=dict(meta) if isinstance(meta, dict) else None,
            )
        except Exception:  # noqa: BLE001 - one bad envelope must not strand the rest
            logger.warning("spooled fire failed to re-enter the event path", exc_info=True)
        # Counted as handled either way: `emit_event` is itself best-effort, and holding a
        # line whose re-entry raised would retry it on every tick forever — the poison pill
        # `drain_decision` names. The warning above is the record.
        handled += 1

    try:
        from personalclaw.triggers.dispatch import clear_spool

        clear_spool(handled=handled)
    except Exception:  # noqa: BLE001
        # NOT re-raised, and the ack is what failed: the fires already ran, so the next tick will
        # re-run them. Logged loudly because a double-fire is the one outcome criterion 7 bans, and
        # an unwritable spool file is the only way to reach it.
        logger.warning("spool ack failed; %d fire(s) may re-run next tick", handled, exc_info=True)
    return handled


def _hold_resumes(wk: Any, deliveries: list[Any], pending: list[Any] | None) -> None:
    """Park the resumes that could not be delivered, for the next tick to retry.

    `wakeup.retry_queue` is the shipped predicate for "which of these must come back" and had no
    caller, so criterion 7's "pending approvals re-arm" was implemented and unreachable: a resume
    whose session was not ready was built, classified REQUEUED, and thrown away.

    Retried on the NEXT tick rather than spun on here — a session becoming ready is not something
    this loop can hurry, and a tight retry would burn the tick that other triggers need.
    """
    if pending is None:
        return
    try:
        again = wk.retry_queue(deliveries)
    except Exception:  # noqa: BLE001
        logger.debug("retry_queue raised", exc_info=True)
        return
    if not again:
        return
    pending.extend(again)
    if len(pending) > MAX_PENDING_RESUMES:
        dropped = len(pending) - MAX_PENDING_RESUMES
        # Drops the OLDEST. A stranded resume answers a question a parked run asked, and the run
        # that asked longest ago is likeliest to be gone entirely; the newest answer is the one a
        # user is still waiting on. Loud, because §3.2 bans dropping a resume and this is the
        # bounded exception — an unbounded queue on a permanently unready session is its own outage.
        logger.warning(
            "resume retry queue full (%d); dropping the %d oldest",
            MAX_PENDING_RESUMES,
            dropped,
        )
        del pending[:dropped]
    logger.info("holding %d resume(s) for the next tick", len(again))


def _retry_pending_resumes(sessions: Any, pending: list[Any] | None) -> int:
    """Re-attempt last tick's undeliverable resumes. Returns how many were delivered.

    Delivered ones leave the queue; still-undeliverable ones stay, so the queue drains exactly as
    sessions become ready rather than on a timer.
    """
    if not pending or sessions is None:
        return 0
    from personalclaw.triggers import wakeup as wk

    try:
        deliveries = wk.deliver_all(sessions, list(pending))
    except Exception:  # noqa: BLE001 - a delivery failure must leave the queue intact
        logger.debug("resume retry failed; keeping the queue", exc_info=True)
        return 0
    delivered = 0
    still: list[Any] = []
    for delivery in deliveries:
        if getattr(delivery, "delivered", False):
            delivered += 1
        else:
            still.append(delivery.wakeup)
    pending[:] = still
    if delivered:
        logger.info("re-delivered %d held resume(s)", delivered)
    return delivered
