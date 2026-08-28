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
    #
    # 🔴 AND IT IS NO LONGER A WARNING, because it no longer describes a problem. When the check was
    # fixed, `no_session` was a fire that reached nobody. Now `_execute_delivery` runs that fire
    # directly, so this counts triggers with no session inbox — which is EVERY scheduled trigger, on
    # every gateway. A WARNING on the healthy path is how a log stops being read; the count is still
    # worth having, at the level a normal event belongs.
    no_session = int((summary.get("by_disposition") or {}).get("no_session", 0) or 0)
    if no_session:
        logger.debug("clock dispatch: %d wakeups have no session inbox (run directly)", no_session)

    _hold_resumes(wk, deliveries, pending_resumes)

    # 🔴 EXECUTE PER DELIVERY, NOT PER FIRE. Two defects lived in the loop this replaces, and both
    # come from re-deriving what the dispatcher already decided.
    #
    # 1. It drained `session_key_for(fire.trigger.id)` — WITHOUT the trigger's `session` binding,
    #    which `wakeup_for` DOES pass. For a trigger bound to `conversation:<key>` the two disagree:
    #    the wakeup was queued onto the conversation and the drain looked in `cron:<id>`. One key,
    #    computed once, carried on the delivery.
    # 2. It drained unconditionally, so a fire that reached NO INBOX was silently dropped — which,
    #    measured, is every scheduled fire on a real gateway, because nothing in the tree creates a
    #    trigger's session. See `_execute_delivery`.
    for delivery in deliveries:
        try:
            await _execute_delivery(delivery, runner, sessions=sessions, now=now, base_dir=base_dir)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one trigger's execution must not strand the others
            logger.warning("execution failed for %s", delivery.wakeup.trigger_id, exc_info=True)
    return result


async def _execute_delivery(
    delivery: Any,
    runner: Callable[[dict[str, Any]], Awaitable[Any]],
    *,
    sessions: Any,
    now: float = 0.0,
    base_dir: Any = None,
) -> list[Any]:
    """Execute (or correctly abandon) ONE dispatched fire. Returns its run outcomes.

    🔴 **THE BUG THIS CLOSES: no scheduled trigger had ever executed its action.** `tick` decided
    the fire, wrote a ledger row, persisted a claim and advanced `next_fire_at` — and then the fire
    went nowhere. Measured on a live gateway: 46 cycles of accumulated state contained run records
    from `manual` and `replay` only, never one from a schedule. The UI showed each trigger enabled
    with an advancing next-run time the whole time.

    The break was that `deliver` queues onto a session inbox, and **nothing in the tree creates a
    trigger's session** — `get_or_create` has no caller with a trigger key. So `enqueue` returned
    False, `deliver` reported `NO_SESSION`, and the old loop drained an inbox that had never
    received anything. `deliver`'s own docstring names the two ways out: *"the caller creates the
    session or spools the payload"*. It did neither.

    **A trigger fire does not need a model session.** That is why this takes neither of those two
    ways. In this chain the session is used ONLY as a queue: `drain` pops a payload and hands it to
    the injected `runner`, and in production that runner is the gateway's action dispatch, which
    creates whatever it needs itself. `get_or_create` would spin up a provider process per fire —
    an ACP CLI started at 3am so a `notify` action can print a line — and then the payload would
    still have to be drained back out of the queue it was only ever passed through. So a fire with
    no inbox runs directly, through the same `run_one` a drained fire runs through.

    **A fire that does not run releases its claim.** `tick` persists a claim so `overlap` can
    enforce, and `run_one` releases it in a `finally`. Every path that never reaches `run_one` was
    therefore leaving a claim held for its full `max_duration_secs` (3600s), which left the trigger
    reporting `is_running: true`, recording `skipped_overlap` on every later tick, and answering
    `409 already running` to a manual Run for an hour. `SKIPPED_RUNNING` is a deliberate skip — the
    session is mid-turn and `overlap: skip` says don't pile on — but "we chose not to start a run"
    and "a run is in flight" are different facts, and only the second is what a claim means.
    """
    from personalclaw.triggers import executor as ex
    from personalclaw.triggers import wakeup as wk

    wakeup = delivery.wakeup
    key = wakeup.session_key

    if delivery.disposition == wk.Disposition.RESUME_TARGET.value:
        # 🔴 BEFORE the disposition branches, and it must stay here. A resume names a parked RUN,
        # and neither branch below can serve it: `drain` would execute it as the trigger's ordinary
        # ACTION (measured — nothing dispatches on `Wakeup.kind`), and `run_one` would do the same
        # thing directly. Both would report a healthy run having never touched the target.
        return [await _apply_resume(wakeup, now=now, base_dir=base_dir)]

    if delivery.disposition == wk.Disposition.QUEUED.value:
        drained = await ex.drain(sessions, key, runner, now=now, base_dir=base_dir)
        return list(drained.outcomes)

    if delivery.disposition == wk.Disposition.NO_SESSION.value:
        # The normal path on a real gateway, not an edge case. `run_one` owns the classification,
        # the timing and the claim release, so this is the same execution a drained fire gets.
        outcome = await ex.run_one(
            wakeup.payload, runner, session_key=key, now=now, base_dir=base_dir
        )
        logger.info(
            "clock fire for %s ran without a session inbox: %s",
            wakeup.trigger_id,
            outcome.outcome,
        )
        return [outcome]

    # SKIPPED_RUNNING (a wake dropped because the session is mid-turn) or REQUEUED (a resume held
    # for the next tick — never a fire, since `wakeup_for` always builds a wake). Nothing ran, so
    # the claim must come back or this trigger is jammed until the claim expires.
    released = ex.release_claim_for(wakeup.trigger_id, base_dir=base_dir)
    logger.debug(
        "clock fire for %s not executed (%s); claim released=%s",
        wakeup.trigger_id,
        delivery.disposition,
        released,
    )
    return []


#: Service codes that mean "the target is not ready YET" rather than "the target is wrong".
#:
#: Both are states a parked run leaves on its own, so both are DEFERRED and re-evaluated on the
#: trigger's next scheduled fire — no retry queue, because a scheduled trigger's own cadence IS the
#: retry, and `pending_resumes` feeds `deliver_all`, which would put the resume back onto the inbox
#: this path exists to avoid.
#:
#: `WF_RUN_NOT_LIVE` is transient because the watchdog adopts parked runs on its poll
#: (`store.active_runs()` includes `needs_input`), so "no live controller" means "not adopted yet",
#: not "gone". `WF_NO_PENDING_GATE` is transient because a run between gates has no continuation
#: for a moment and will mint one.
_RESUME_NOT_YET: frozenset[str] = frozenset({"WF_RUN_NOT_LIVE", "WF_NO_PENDING_GATE"})


def _supervisor() -> Any:
    """The workflow supervisor, or None when the gateway has not wired one yet.

    A copy of `mcp_workflows._supervisor` rather than an import of it, because that module is the
    MCP tool surface and importing it from the clock loop would drag the whole tool registry into
    the scheduler's import graph for one getattr. The CONTRACT is what matters and it is identical:
    resolved per call, never cached, never raising.

    `None` is survivable and honest: `resume_run` answers `WF_RUN_NOT_LIVE`, which this path treats
    as "not ready yet" and re-evaluates on the next scheduled fire — the correct behaviour in a
    process with no workflow engine (a CLI tick, `automation doctor`) as well as during the window
    before the gateway attaches the watchdog.
    """
    try:
        from personalclaw.action_providers.services import get_action_services

        services = get_action_services()
    except Exception:  # noqa: BLE001 - a missing service registry is not an error here
        return None
    return getattr(services, "workflows", None) if services else None


async def _apply_resume(wakeup: Any, *, now: float = 0.0, base_dir: Any = None) -> Any:
    """Apply one trigger-declared resume to its target run. Returns a `RunOutcome`. Never raises.

    🔴 **THE GAP THIS CLOSES.** `wakeup.resume_for`, `WakeKind.RESUME`, `Disposition.REQUEUED` and
    `dispatch.droppable` were all written, documented and unit tested, and `resume_for` had **zero
    production callers** — only `wakeup_for` was reachable, from `dispatch_fires`, and it always
    built a wake. So §3's documented "resolve def / **resume target**" step had no producer and no
    consumer: no trigger could target an existing run, and `WF2LOO-9`'s `goal-pursuit-monitor`
    clause was blocked on exactly that.

    **FAIL-CLOSED on a bad target, and loudly.** This fires unattended, so the two directions are
    not symmetric. Fail-open would mean "the target is gone, so start a new run instead" — which
    runs work the author never asked for, on a schedule, with nobody watching, potentially
    mutating. Refusing costs one automation that was already broken. So a gone / finished / foreign
    target REFUSES, and every refusal carries a mandatory reason (`models.require_reason` makes
    that checkable for `refused`) at `logger.warning` — because "a trigger that silently does
    nothing every hour is worse than one that says its target is gone", and a `logger.debug` on a
    permanently broken automation is that silence.

    **Idempotence is INHERITED, not re-implemented.** Two fires landing close together cannot
    resume one run twice, and none of the three guarantees is added here:

    * the same trigger cannot overlap itself — `firepath`'s `claim` gate already refused the second
      fire with `skipped_overlap` before it ever became a wakeup;
    * a gate answer is single-use — `human_input.consume_continuation` claims the token with
      `os.rename` BEFORE reading it, so the loser gets `WF_RESUME_ALREADY_USED`. That primitive is
      measured (the read-then-unlink version it replaced let multiple callers see one payload in
      36 of 40 races); and
    * the token-less "clear the pause" path pops a key and saves, which is idempotent by
      construction.

    So `concurrency.single_flight` is deliberately NOT used. It is the weaker guard for this job —
    advisory, non-blocking, released the instant the block ends — and the authoritative claim
    already lives inside `resume_run`, one layer down. Wrapping a second lock around it would be
    the parallel guard that makes two mechanisms disagree about who won.

    **Admission is inherited too, with one measured exception — see the module note below.** Every
    fire reaching here passed `firepath.evaluate`'s full walk in `service.tick` (incident, screen,
    spacing, rate, quiet, duty, budget, claim, slot, active, yield, capability), because `tick`
    only builds a `DueFire` for an ALLOWED decision. A resume therefore inherits every admission
    check a start gets; the single deliberate asymmetry is `droppable`, and that is not an
    admission bypass — the overlap admission is the `claim` gate, which already ran.
    """
    from personalclaw.triggers import executor as ex
    from personalclaw.triggers.models import Outcome

    payload = wakeup.payload if isinstance(wakeup.payload, dict) else {}
    trigger_id = str(payload.get("trigger_id") or wakeup.trigger_id or "")
    run_id = str(payload.get("run_id") or "")
    started = now or time.time()

    def _out(outcome: str, reason: str, *, reported: str = "") -> Any:
        return ex.RunOutcome(
            trigger_id=trigger_id,
            session_key=wakeup.session_key,
            outcome=outcome,
            reason=reason,
            duration_secs=round(max(0.0, time.time() - started), 3),
            reported=reported,
            run_id=run_id,
        )

    try:
        try:
            from personalclaw.workflows import service as wfs
            from personalclaw.workflows import store as wf_store
        except Exception as exc:  # noqa: BLE001 - a broken import must not take the tick with it
            logger.warning("resume target for %s unreachable: %r", trigger_id, exc)
            return _out(Outcome.FAILED.value, f"the workflows service is unreachable: {exc!r}")

        run = None
        try:
            run = wf_store.get(run_id)
        except Exception:  # noqa: BLE001 - an unreadable store is a failure, not a refusal
            logger.warning("resume target %s could not be read", run_id, exc_info=True)
            return _out(Outcome.FAILED.value, f"run {run_id!r} could not be read from the store")

        # ── the three fail-closed refusals, each PERMANENT: none of them settles on its own ──
        if run is None:
            reason = (
                f"the resume target {run_id!r} no longer exists, so there is nothing to resume; "
                "point this automation at a live run or remove its resume target"
            )
            logger.warning("trigger %s: %s", trigger_id, reason)
            return _out(Outcome.REFUSED.value, reason)

        if getattr(run, "is_terminal", False):
            reason = (
                f"the resume target {run_id!r} has finished ({getattr(run.status, 'value', '')!r}) "
                "and a run is one attempt, so it cannot be resumed"
            )
            logger.warning("trigger %s: %s", trigger_id, reason)
            return _out(Outcome.REFUSED.value, reason)

        wanted_project = str(payload.get("project_id") or "")
        actual_project = str(getattr(run, "project_id", "") or "")
        if wanted_project and wanted_project != actual_project:
            # A run id is not unique to a project's intent: ids are reused across a restore and a
            # fork, and resuming a stranger's run unattended is the one outcome worth refusing on a
            # merely SUSPICIOUS signal.
            reason = (
                f"the resume target {run_id!r} belongs to project {actual_project or '(none)'!r}, "
                f"not the declared {wanted_project!r}"
            )
            logger.warning("trigger %s: %s", trigger_id, reason)
            return _out(Outcome.REFUSED.value, reason)

        answers_gate = bool(payload.get("answers_gate"))
        try:
            result = wfs.resume_run(
                run_id,
                # 🔴 THE SUPERVISOR IS MANDATORY, and omitting it would have made this whole path
                # inert. `service._live(run_id, None)` returns None unconditionally, so a
                # supervisor-less `resume_run` can only ever answer `WF_RUN_NOT_LIVE` — a resume
                # that never resumes anything, which is the exact defect class this session closes.
                #
                # Resolved PER FIRE rather than threaded through `run_forever`, for the reason
                # `mcp_workflows._supervisor` gives: "a cached None taken at import time would make
                # every tool permanently inert in a process that wires services later". The
                # gateway assigns `svc.workflows` only after the watchdog starts, which is after the
                # clock loop is constructed — so a value captured at loop start would be that None.
                supervisor=_supervisor(),
                token=str(payload.get("resume_token") or ""),
                # `answer=None` with no token is `resume_run`'s "clear the pause" path — it does NOT
                # answer a gate. That is the safe default for an unattended fire: a monitor says
                # "carry on", and auto-approving a gate is something an author has to write down.
                answer=payload.get("gate_answer") if answers_gate else None,
                responder=f"trigger:{trigger_id}" if trigger_id else "trigger",
            )
        except Exception as exc:  # noqa: BLE001 - the outcome IS the error
            logger.warning("resume of %s raised for %s", run_id, trigger_id, exc_info=True)
            return _out(Outcome.FAILED.value, f"the resume raised: {exc!r}")

        result = result if isinstance(result, dict) else {}
        code = str(result.get("code") or "")
        if result.get("ok"):
            return _out(
                Outcome.RAN.value,
                "",
                reported=(
                    "gate answered" if result.get("gate_answered", True) else "pause cleared"
                ),
            )
        if code in _RESUME_NOT_YET:
            reason = (
                f"the resume target {run_id!r} is not ready yet ({code}); the next scheduled fire "
                "re-evaluates it"
            )
            logger.info("trigger %s: %s", trigger_id, reason)
            return _out(Outcome.DEFERRED.value, reason, reported=code)
        reason = (
            f"the resume of {run_id!r} was refused ({code or 'no code'})"
            f"{': ' + str(result.get('message')) if result.get('message') else ''}"
        )
        logger.warning("trigger %s: %s", trigger_id, reason)
        return _out(Outcome.REFUSED.value, reason, reported=code)
    finally:
        # 🔴 The claim, released on EVERY path. `tick` persists one per fire so `overlap` can
        # enforce, and this function never reaches `run_one` — whose `finally` is the only other
        # release. Without this a resume-target trigger would report `is_running` for the claim's
        # full 3600s after its first fire, record `skipped_overlap` on every later tick, and answer
        # `409 already running` to a manual Run for an hour. Exactly the S97 defect, on a new path.
        ex.release_claim_for(trigger_id, base_dir=base_dir)


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
