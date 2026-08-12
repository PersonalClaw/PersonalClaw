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

    # 🔴 THE THIRD WAKE SOURCE: `kind:idle` (§1.2 / §7 step 9 — WF2AUT-11). Same placement, same
    # reason as the two above — an idle trigger is due EXACTLY when the clock's due-set is empty, so
    # putting this below `if not result.fires` would skip it precisely when it should fire. And it
    # cannot ride the due-set itself: `service.due_ids` only surfaces triggers with a
    # `next_fire_at`, and an idle trigger has none because its due-ness is a predicate over session
    # activity rather than a schedule. That is why the kind was declared-and-inert until now.
    idle_fired = await _poll_idle(store, sessions, runner, now=now, base_dir=base_dir)

    if not result.fires:
        if spooled or retried or idle_fired:
            logger.debug(
                "idle tick drained %d spooled, retried %d resumes, fired %d idle",
                spooled,
                retried,
                idle_fired,
            )
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


async def _poll_idle(
    store: Any,
    sessions: Any,
    runner: Callable[[dict[str, Any]], Awaitable[Any]],
    *,
    now: float = 0.0,
    base_dir: Any = None,
) -> int:
    """Fire the due `kind:idle` triggers. Returns how many were DELIVERED.

    A thin driver, like the rest of this file: `idle_poll` owns every decision (quiet-period
    elapsed, autonudge ownership, delivered-only counting), and this only calls it and survives it.

    Never raises: an idle poll that threw would take the whole tick with it, and one bad idle
    trigger must not stop the clock for every automation on the machine.

    The skipped rows are LOGGED rather than dropped (§7 crit 8) — "the session was mid-turn" is
    exactly the decision a user needs to find when they ask why a nudge went quiet.
    """
    from personalclaw.triggers import idle_poll

    try:
        delivered, skipped = await idle_poll.poll(
            store, sessions, runner, now=now, base_dir=base_dir
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - the tick must outlive one idle poll's failure
        logger.warning("idle poll failed; continuing the tick", exc_info=True)
        return 0
    for row in skipped:
        logger.debug("idle %s did not fire: %s", row.get("trigger_id"), row.get("reason"))
    return delivered


def _reenter_spooled(envelope: Any, *, now: float) -> tuple[str, str]:
    """Re-enter one spooled envelope through `emit_event`. Returns `(handling, detail)`.

    🔴 **THE SIDE-EFFECT BOUNDARY, drawn explicitly because HOLD makes it load-bearing**
    (WF2AUT-13). A held envelope is retried on a later tick. That is right for a failure that
    happened before anything observable, and it is data corruption for one that happened after —
    the double-fire §3.2/criterion 7 bans. So the boundary is the `emit_event(...)` call itself,
    and the split is enforced structurally by two separate `try` blocks rather than by a comment:

    * **Above the line** the function only reads the envelope, builds keyword arguments and resolves
      the engine. Nothing there can fire a trigger, write a ledger row or touch a store, so a
      failure there provably delivered nothing and is honestly retryable.
    * **Below the line** the envelope is DELIVERED, full stop, whatever happens next. `emit_event`
      matches every stored trigger and schedules their actions; once entered, the drain cannot know
      how far it got, and "I don't know" must resolve to delivered rather than to a retry.

    `get_engine()` is resolved as the LAST pre-flight step, immediately above the boundary, and that
    placement is the point: it is a pure lazy constructor (its store is bound on first use, not
    here), so asking for it is side-effect-free, and asking BEFORE the boundary converts "the event
    engine is not reachable" from a fact `emit_event` swallows into a `logger.debug` — silently
    losing every spooled fire — into a pre-delivery TRANSIENT that earns a bounded retry.

    Re-entry stays through `emit_event`, the SAME seam a live source write uses, not a second
    dispatch path: that is what stops a spooled fire skipping a gate a live one walks, which is
    exactly how the `web_watch` screen gap (S134) opened.
    """
    from personalclaw.triggers.dispatch import Handling, classify_handler_outcome

    try:
        from personalclaw.event_triggers import SOURCE_MEMORY, emit_event, get_engine

        # `kind` is `f"{source}.{event_type}"` (EIAT-1); split on the first dot so the
        # spooled fire re-enters scoped to the source it came from. Legacy envelopes with
        # no source prefix fall back to memory — the only source that spooled before EIAT-1.
        kind = str(getattr(envelope, "kind", "") or "")
        source, _, event_type = kind.partition(".")
        if not event_type:
            source, event_type = SOURCE_MEMORY, kind
        if not event_type:
            # PERMANENT, not transient: an envelope with no event kind names no event, so no
            # source can ever route it and no retry can make it routable. It used to reach
            # `emit_event` with an empty `event_type`, match nothing, and count as a delivered
            # fire — a drop wearing a success.
            return Handling.PERMANENT.value, "envelope carries no event kind; nothing can route it"
        payload = getattr(envelope, "payload", None) or {}
        meta = payload.get("meta")
        kwargs = {
            "source": source,
            "event_type": event_type,
            "key": str(payload.get("key", "") or ""),
            "value": str(payload.get("value", "") or ""),
            "now": now,
            "meta": dict(meta) if isinstance(meta, dict) else None,
        }
        get_engine()
    except Exception as exc:  # noqa: BLE001 - classified, not swallowed
        # `classify_handler_outcome` maps an unclassified throw to TRANSIENT — its "never drop"
        # rule. This is its first production call site; it was written for exactly this seam.
        return classify_handler_outcome(exc), f"re-entry could not be prepared: {exc!r}"

    # ─────────────── SIDE-EFFECT BOUNDARY. Past this line: DELIVERED. ───────────────
    try:
        emit_event(**kwargs)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 - defensive; production `emit_event` never raises
        logger.warning("spooled fire raised after the side-effect boundary", exc_info=True)
        return (
            Handling.DELIVERED.value,
            "re-entry raised after the side-effect boundary; counted delivered because a retry "
            "could double-fire an action that already ran",
        )
    return Handling.DELIVERED.value, ""


def _drain_spool(*, now: float = 0.0) -> int:
    """Consume the sync-context spool under §3.3's cursor rule. Returns how many lines were ACKED.

    Peek-then-deliver-then-ack, which is why `drain_spool` and `clear_spool` are two calls: the
    spool is only truncated after the envelopes have been handed on, so a crash mid-drain re-drains
    rather than losing them. `clear_spool(handled=...)` keeps whatever arrived during the drain.

    🔴 **§3.3's cursor rule had a complete decision layer and no caller** (WF2AUT-13). `Handling`,
    `DrainAction`, `drain_decision` and `classify_handler_outcome` were written, documented and unit
    tested; `grep` found zero production references to any of them. Meanwhile this loop did
    `handled += 1` unconditionally and this very comment block said the alternative would be "the
    poison pill `drain_decision` names" — naming the function it never called. So the retry/hold
    policy, its bounded budget and the poison-pill give-up were all inert, and a transient re-entry
    failure was swallowed as a delivered fire.

    This is the call site. Each envelope is classified at the side-effect boundary
    (`_reenter_spooled`), `drain_decision` turns that into a `DrainAction`, and every member has a
    branch below with a raising tail so a new one cannot inherit another's semantics.

    **At-most-once is preserved, not traded.** HOLD is head-of-line (see `clear_spool`'s prefix-ack
    note) and only ever reachable from a pre-delivery failure, so nothing whose side effect may have
    run is ever retried. A hold that cannot persist its budget is acked instead of held, because an
    unbounded retry is worse than the swallow this replaced.
    """
    from personalclaw.triggers import service as svc
    from personalclaw.triggers.dispatch import (
        DEDUP_WINDOW_SECS,
        DrainAction,
        clear_spool,
        clear_spool_hold,
        drain_decision,
        is_duplicate,
        read_spool_hold,
        write_spool_hold,
    )

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
        # An empty spool has nothing left to hold a budget for. Clearing here rather than only on
        # the ack path stops a stale count outliving the envelope it belonged to.
        clear_spool_hold()
        return 0

    now_ts = now or time.time()
    held_id, held_retries = read_spool_hold()
    seen: dict[str, float] = {}
    handled = 0
    holding = ""

    for envelope in envelopes:
        event_id = str(getattr(envelope, "event_id", "") or "")
        # The budget belongs to ONE envelope: a recorded id that is not this one means the head
        # moved on, and a carried-over count would give this envelope a shorter budget than the
        # first got (`Cursor.held_retries`'s own rule).
        retries = held_retries if event_id and event_id == held_id else 0
        emitted = float(getattr(envelope, "emitted_at", 0.0) or 0.0) or now_ts

        if is_duplicate(envelope, seen, emitted, DEDUP_WINDOW_SECS):
            # Honest producer for SKIP_DUPLICATE: `event_id`/`payload_hash` are DETERMINISTIC over
            # (source, kind, payload), so two lines with one hash inside `DEDUP_WINDOW_SECS` are the
            # work the window exists to collapse — "a webhook retried by its sender and an fs event
            # fired twice for one save". Compared on emit times, not on the tick's clock, so a
            # family seen an hour apart stays two facts. Decided before re-entry: no handler runs,
            # which is why this is not a `drain_decision` outcome.
            action, why = DrainAction.SKIP_DUPLICATE.value, "identical payload already re-entered"
            detail = ""
        else:
            handling, detail = _reenter_spooled(envelope, now=now_ts)
            action, why = drain_decision(handling=handling, held_retries=retries)

        if action == DrainAction.CONSUME.value:
            if why or detail:
                # A consume WITH a reason is a permanent failure or a post-boundary raise, i.e. a
                # fire that will never run. Criterion 8 counts a silent one as a drop.
                logger.warning("spooled fire %s consumed undelivered: %s %s", event_id, why, detail)
            seen[envelope.payload_hash] = emitted
            handled += 1
            continue

        if action == DrainAction.SKIP_DUPLICATE.value:
            logger.info("spooled fire %s skipped: %s", event_id, why)
            handled += 1
            continue

        if action == DrainAction.GIVE_UP.value:
            # The poison pill, given its ledger row at last. Loud and at WARNING because this is the
            # one branch that drops a fire that COULD have run — the alternative (hold forever on
            # one unreachable engine) stops every other automation on the machine.
            logger.warning(
                "spooled fire %s GIVEN UP after %d attempts: %s %s",
                event_id,
                retries + 1,
                why,
                detail,
            )
            handled += 1
            continue

        if action == DrainAction.HOLD.value:
            if write_spool_hold(event_id=event_id, held_retries=retries + 1):
                logger.info("spooled fire %s held: %s %s", event_id, why, detail)
                holding = event_id
                # Head-of-line: stop here so the ack covers only the prefix already consumed. This
                # envelope and everything behind it stay on disk for the next tick, in order.
                break
            logger.warning(
                "spooled fire %s could not persist its retry budget; acking rather than retrying "
                "unbounded: %s",
                event_id,
                detail,
            )
            handled += 1
            continue

        raise AssertionError(
            f"no branch for DrainAction {action!r} — a new member must declare what the drain does "
            "with it rather than fall through to another member's handling"
        )

    if not holding:
        clear_spool_hold()
    if handled:
        try:
            clear_spool(handled=handled)
        except Exception:  # noqa: BLE001
            # NOT re-raised, and the ack is what failed: the fires already ran, so the next tick
            # will re-run them. Logged loudly because a double-fire is the one outcome criterion 7
            # bans, and an unwritable spool file is the only way to reach it.
            logger.warning(
                "spool ack failed; %d fire(s) may re-run next tick", handled, exc_info=True
            )
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
