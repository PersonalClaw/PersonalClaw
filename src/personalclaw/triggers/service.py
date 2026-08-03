"""`TriggerService` — the one scheduler's TICK (§3 / §3.1 — S88).

§3: "One asyncio loop — **the existing single re-armed `_arm_timer` task generalized** … The task
computes the earliest `next_fire_at` across all clock/idle triggers and sleeps until it
(capped at 30s
for external-edit pickup via mtime `_sync`), coalescing same-second firings so N triggers
replacing one
60s heartbeat don't wake the laptop N times."

**Why this is buildable now.** S83 and S86 recorded "the store and the service are one unbuilt
foundation"; S87 showed that was half wrong (the service needs the store, not the reverse) and
shipped
`triggers.json`. With the store in place every dependency this file needs is present and was
verified
importable before a line was written: `store.TriggerStore`, `firepath.evaluate` (S86's gate order),
`scheduling.{is_due,boot_recovery,coalesce_wakes,next_wake_delay,recompute_from_completion,revalidate}`,
`missed.{review_at_boot,catch_up_plan,roll_forward}`, `dispatch.drain_spool`,
`delivery.build_delivery`
and `autopause.needs_attention`.

**What this owns, and the boundary.**

It owns the DECISIONS a tick makes: what is due, what a boot recovers, how wakes coalesce,
when the next
wake is, and — for each due trigger — walking S86's fire path and recording the typed ledger
row. It is
`async` and side-effect-free apart from the store writes it is explicitly asked to make.

It does NOT own EXECUTION. §3.2 is explicit that "the scheduler never executes directly": a
fired trigger
enqueues onto the target session's inbox plus a wakeup signal, and a **WakeupDispatcher**
claims and drives
runs. That dispatcher needs the session inbox seam, which is a different subsystem. So
`tick()` returns the
list of fires that PASSED every gate, and the caller dispatches. A service that both decided
and executed
would make the two untestable together — and §3.2's whole point is that crash-safety comes from the
payload surviving in the inbox, which is only true if deciding and running are separate.

**Three properties the tick has to get right, each measured rather than assumed:**

**Persist-before-execute (§3.1).** `next_fire_at` is persisted BEFORE the fire is handed out,
so a crash
mid-fire cannot double-fire. The alternative — recompute-on-poll — re-derives the same due
time after a
crash and fires again.

**Recompute from COMPLETION, anchored to `created_at` (§3.1).** Not from the missed slot: a run that
overruns its interval would otherwise produce a fire storm catching up. And anchored to the
creation grid,
so a recompute does not silently re-phase a 9am job to "whenever the last run finished".

**Boot stagger (§3.1).** Overdue fires are pushed and deterministically staggered, so a
restart does not
fire every automation in the same second.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from personalclaw.triggers.models import Outcome, Trigger

logger = logging.getLogger(__name__)

#: Wake-up ceiling, in seconds. §3: "capped at 30s for external-edit pickup via mtime
#: `_sync`". The cap is
#: not a scheduling nicety — it IS the propagation contract for a store another process can
#: write, which
#: §6's MCP gotcha makes mandatory.
MAX_SLEEP_SECS = 30.0

#: Floor on a computed sleep. A zero or negative delay would spin the loop; §3's mechanism is
#: one task
#: sleeping, and a busy-wait would be a battery bug rather than a scheduler.
MIN_SLEEP_SECS = 0.5


@dataclass
class DueFire:
    """One trigger that passed every gate and is ready to DISPATCH.

    Carries the decision, not a result: §3.2 says the scheduler never executes. The caller
    enqueues this
    onto the session inbox, and the crash-safety §3.2 promises comes from that payload
    surviving — which
    is only true if this object is handed over rather than run here.
    """

    trigger: Trigger
    #: The claim `firepath` granted. The DISPATCHER releases it when the run settles; a tick
    #: that released
    #: it would let a second fire in while the first is still running.
    claim: Any = None
    scheduled_for: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger.id,
            "kind": self.trigger.kind,
            "scheduled_for": self.scheduled_for,
            "reason": self.reason,
        }


@dataclass
class TickResult:
    """Everything one tick decided. The whole return value, so a caller needs no second query.

    `ledger_rows` is present for EVERY evaluated trigger, fired or suppressed — §7 criterion
    8's "zero
    silent drops" is a property of the tick, not of the caller remembering to log.
    """

    fires: list[DueFire] = field(default_factory=list)
    ledger_rows: list[dict[str, Any]] = field(default_factory=list)
    #: Seconds until the next wake. The caller sleeps this; it is already capped and floored.
    next_sleep: float = MAX_SLEEP_SECS
    #: Trigger ids whose `next_fire_at` this tick advanced and persisted.
    rescheduled: list[str] = field(default_factory=list)
    #: Set when the store changed under us — the caller should re-read before acting on stale state.
    store_changed: bool = False

    @property
    def suppressed(self) -> int:
        return sum(1 for row in self.ledger_rows if row.get("outcome") != Outcome.RAN.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fires": [f.to_dict() for f in self.fires],
            "ledger_rows": list(self.ledger_rows),
            "next_sleep": self.next_sleep,
            "rescheduled": list(self.rescheduled),
            "store_changed": self.store_changed,
            "suppressed": self.suppressed,
        }


def to_epoch(value: Any) -> float:
    """An entity timestamp (ISO string) as an epoch float. 0.0 when absent or unparseable.

    🔴 THE TYPE SEAM, found by driving a tick against a real store. `Trigger.next_fire_at` is
    declared
    `str` — the entity keeps every timestamp as ISO (`last_success_at`, `last_failure_at`, …
    all `str`),
    which is right for a JSON row a human may edit. But `scheduling.is_due`, `boot_recovery` and
    `next_wake_delay` all take `float` epochs. Nothing converted, so a round-tripped trigger
    came back
    with `next_fire_at` as `'1234.5'` and every comparison against `now` raised `TypeError: '>' not
    supported between instances of 'str' and 'float'`.

    The conversion belongs HERE rather than in either module: the entity owns the persisted
    schema and
    `scheduling` owns the arithmetic, and changing either to match the other would break the
    half that
    is already correct. Accepts a numeric too, since a caller holding a fresh epoch should not
    have to
    stringify it first.
    """
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)  # a stringified epoch, which `to_dict` round-trips
    except ValueError:
        pass
    try:
        from datetime import datetime

        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        logger.debug("unparseable trigger timestamp %r; treating as unset", text)
        return 0.0


def to_iso(epoch: float) -> str:
    """An epoch as the ISO string the entity persists.

    The inverse of `to_epoch`, so a reschedule writes what the schema declares instead of leaving a
    float in a `str` field for the next reader to trip over.
    """
    if epoch <= 0:
        return ""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _interval_secs(trigger: Trigger) -> float:
    """The trigger's interval, or 0 when it is not an interval kind.

    Reads `spec['interval_secs']` — the key S87 had to add to `SPEC_KEYS['clock']` alongside the
    `interval` clock kind, because the migration emits it for every legacy `every` cron and nothing
    accepted it.
    """
    spec = trigger.spec or {}
    if str(spec.get("kind") or "") != "interval":
        return 0.0
    try:
        return max(0.0, float(spec.get("interval_secs") or 0))
    except (TypeError, ValueError):
        return 0.0


def _created_at(trigger: Trigger) -> float:
    """The trigger's creation epoch, for the recurrence ANCHOR.

    §3.1 anchors recomputes to `created_at` so they do not re-phase to "now". `Trigger` has no
    `created_at` field (checked — it carries `created_by`), so the caller's `now` is the
    honest fallback:
    inventing an anchor would silently re-phase every trigger on its first recompute, which is
    the exact
    drift the anchoring rule exists to prevent.
    """
    for name in ("created_at", "created_ts"):
        value = getattr(trigger, name, None)
        if value:
            epoch = to_epoch(value)
            if epoch > 0:
                return epoch
    return 0.0


def plan_boot(triggers: list[Trigger], *, now: float) -> list[tuple[str, float, str]]:
    """What boot does to each trigger's `next_fire_at`. Returns `(id, new_next_fire_at, reason)`.

    §3.1's "exactly-one-upcoming invariant … recovered/re-armed on gateway boot" plus the boot
    stagger.
    Delegates to `scheduling.boot_recovery`, which owns the +60s push and the deterministic
    per-id jitter —
    re-deriving the stagger here would give two different answers for the same restart.

    Only ENABLED triggers are planned: a disabled one has no upcoming fire by definition, and
    arming one
    would resurrect it at the next tick.
    """
    from personalclaw.triggers.scheduling import boot_recovery

    out: list[tuple[str, float, str]] = []
    for trigger in triggers:
        if not trigger.enabled:
            continue
        new_at, reason = boot_recovery(
            next_fire_at=to_epoch(trigger.next_fire_at),
            now=now,
            trigger_id=trigger.id,
            catch_up=bool(getattr(trigger, "catch_up", False)),
        )
        out.append((trigger.id, new_at, reason))
    return out


def due_ids(triggers: list[Trigger], *, now: float, window_secs: float = 1.0) -> list[str]:
    """Which triggers are due, COALESCED into one wake.

    §3: "coalescing same-second firings so N triggers replacing one 60s heartbeat don't wake
    the laptop N
    times". `coalesce_wakes` owns the window; this assembles its input from the store's rows.

    A trigger with no `next_fire_at` is skipped rather than treated as due-now. An unarmed
    trigger means
    boot has not planned it yet, and firing it immediately would ignore the stagger that
    exists to stop a
    restart stampede.
    """
    from personalclaw.triggers.scheduling import coalesce_wakes, is_due

    candidates: dict[str, float] = {}
    for trigger in triggers:
        if not trigger.enabled:
            continue
        next_at = to_epoch(trigger.next_fire_at)
        if next_at <= 0:
            continue
        ok, _why = is_due(
            next_fire_at=next_at,
            now=now,
            fires_automatically=bool(trigger.fires_automatically),
            expires_at=to_epoch(getattr(trigger, "expires_at", "")),
        )
        if ok:
            candidates[trigger.id] = next_at
    return coalesce_wakes(candidates, now, window_secs=window_secs)


def sleep_for(triggers: list[Trigger], *, now: float) -> float:
    """Seconds until the next wake — capped at `MAX_SLEEP_SECS`, floored at `MIN_SLEEP_SECS`.

    The cap is the store-propagation contract (§3/§6), not a nicety: another process can write
    `triggers.json`, and a loop sleeping until a far-future fire would not notice for hours.
    The floor
    stops a due-now trigger from spinning the loop.
    """
    from personalclaw.triggers.scheduling import next_wake_delay

    upcoming = [
        to_epoch(t.next_fire_at) for t in triggers if t.enabled and to_epoch(t.next_fire_at) > 0
    ]
    delay = next_wake_delay(upcoming, now) if upcoming else MAX_SLEEP_SECS
    return max(MIN_SLEEP_SECS, min(MAX_SLEEP_SECS, delay))


def next_after_completion(trigger: Trigger, *, completed_at: float, now: float) -> float:
    """The next fire after a run settles — from COMPLETION, anchored to creation (§3.1).

    Two rules, both load-bearing and both from §3.1: computed from completion time (never the
    missed slot,
    which produces a re-fire storm when a run overruns its interval) and anchored to the
    trigger's own
    creation grid (so a recompute does not re-phase a 9am job to whenever the last run
    happened to end).

    Returns 0.0 for a non-interval trigger: `cron`/`at`/`sequence` recurrences are the
    recurrence engine's
    job, and guessing one here would compete with it.
    """
    from personalclaw.triggers.scheduling import recompute_from_completion

    interval = _interval_secs(trigger)
    if interval <= 0:
        return 0.0
    anchor = _created_at(trigger) or now
    return recompute_from_completion(
        interval_secs=interval, created_at=anchor, completed_at=completed_at
    )


async def tick(
    store: Any,
    *,
    now: float = 0.0,
    persist: bool = True,
    user_active: bool = False,
) -> TickResult:
    """One tick: decide what fires, persist the reschedule, and return the dispatch list.

    The order inside a tick is itself a contract:

    1. Read the store (fresh — another process may have written it).
    2. Coalesce the due set.
    3. For each due trigger: **persist the next fire FIRST** (§3.1 persist-before-execute),
    then walk
       S86's fire path.
    4. Record a ledger row for every evaluated trigger, fired or not (§7 crit 8).
    5. Compute the next sleep from the rows as they now stand.

    `persist=False` makes the whole tick a dry run for the `automation doctor` and for tests —
    the fire
    path still runs, so a dry run reports exactly what a real one would do.
    """
    now = now or time.time()
    result = TickResult()

    result.store_changed = bool(getattr(store, "changed_on_disk", lambda: False)())
    rows = store.load()
    triggers = [row.trigger for row in rows if getattr(row, "ok", True)]
    by_id = {t.id: t for t in triggers}

    from personalclaw.triggers import firepath as fp

    for trigger_id in due_ids(triggers, now=now):
        trigger = by_id.get(trigger_id)
        if trigger is None:
            continue
        scheduled_for = to_epoch(trigger.next_fire_at)

        # ── §3.1: persist the NEXT fire before handing this one out. A crash between here and the
        # dispatch loses one fire; a crash with the old `next_fire_at` still on disk fires
        # twice, and
        # a double-fire is the one failure a user cannot undo.
        advanced = next_after_completion(trigger, completed_at=now, now=now)
        if advanced > 0 and persist:
            trigger.next_fire_at = to_iso(advanced)
            store.upsert(trigger)
            result.rescheduled.append(trigger.id)

        ctx = fp.FireContext(
            trigger_id=trigger.id,
            gates=trigger.gates or {},
            capabilities=trigger.capabilities,
            holder=f"tick:{int(now)}",
            overlap=str(getattr(trigger, "overlap", "skip") or "skip"),
            now=now,
            user_active=user_active,
            yield_to_user=bool(getattr(trigger, "yield_to_user", False)),
        )
        decision = await fp.evaluate(ctx)
        row = fp.ledger_row(decision, ctx)
        row["scheduled_for"] = scheduled_for
        result.ledger_rows.append(row)

        if decision.allowed:
            result.fires.append(
                DueFire(
                    trigger=trigger,
                    claim=decision.claim,
                    scheduled_for=scheduled_for,
                    reason="due",
                )
            )

    result.next_sleep = sleep_for(list(by_id.values()), now=now)
    return result


def boot(store: Any, *, now: float = 0.0, persist: bool = True) -> dict[str, Any]:
    """Re-arm every trigger at gateway boot. Returns what changed.

    §3.1's "exactly-one-upcoming invariant, recovered/re-armed on gateway boot". Sync because
    it runs
    before the loop exists and touches no async gate — the fire path is not walked here, since
    boot arms
    triggers rather than firing them.

    Also returns the missed-fire REVIEW rather than acting on it: §3.4 is "review, don't lie
    and don't
    storm", and a boot that silently caught up would be the storm. The caller surfaces the review.
    """
    from personalclaw.triggers.missed import review_at_boot

    now = now or time.time()
    rows = store.load()
    triggers = [row.trigger for row in rows if getattr(row, "ok", True)]

    rearmed: list[dict[str, Any]] = []
    for trigger_id, new_at, reason in plan_boot(triggers, now=now):
        trigger = next((t for t in triggers if t.id == trigger_id), None)
        if trigger is None:
            continue
        if new_at != to_epoch(trigger.next_fire_at):
            trigger.next_fire_at = to_iso(new_at)
            if persist:
                store.upsert(trigger)
            rearmed.append({"id": trigger_id, "next_fire_at": new_at, "reason": reason})

    review = review_at_boot([t.to_dict() for t in triggers], now=now)
    return {
        "rearmed": rearmed,
        "total": len(triggers),
        "review": review.to_dict() if hasattr(review, "to_dict") else {},
        "next_sleep": sleep_for(triggers, now=now),
    }


def drain_spooled_fires(*, limit: int = 500) -> tuple[list[Any], int]:
    """Drain the sync-context spool (§3.2). Returns `(envelopes, dropped)`.

    §3: "sync-context fires spool to `~/.personalclaw/trigger-spool.jsonl`, drained on next
    tick". Exposed
    from the service rather than called inside `tick()` because the spool is a SEPARATE wake
    source: a
    tick with no due clock trigger must still drain it, and burying the drain inside the
    due-set walk
    would skip it exactly when the machine was otherwise idle.
    """
    from personalclaw.triggers.dispatch import drain_spool

    return drain_spool(limit=limit)
