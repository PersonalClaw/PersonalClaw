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

from personalclaw.triggers.models import Outcome, Trigger, TriggerHealth, TriggerState

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
    #: Trigger ids retired this tick — a one-shot that has no next fire. Named rather than silent:
    #: "it stopped existing" is the one state change a user most needs to see explained, and leaving
    #: an elapsed `next_fire_at` in place instead would re-fire the same past slot every tick.
    retired: list[str] = field(default_factory=list)
    #: Trigger ids brought back from PARKED this tick, their cooldown having elapsed (S159). Named
    #: for the same reason `retired` is: a state change the user did not make must be explainable,
    #: and a revival that only a log knows about is how "why did this start again?" becomes
    #: unanswerable.
    unparked: list[str] = field(default_factory=list)
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
            "retired": list(self.retired),
            "unparked": list(self.unparked),
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

    **🔴 A trigger with NO `next_fire_at` is ARMED from its spec first (S96).** Measured: a migrated
    cron lands `enabled=True` with an empty `next_fire_at`, and `boot_recovery` can only RECOVER an
    existing fire — handed 0.0 it returns 0.0, so the trigger stayed inert forever and `due_ids`
    never surfaced it. `arm.next_fire` computes the first fire from the spec (cron/interval/at/
    sequence); recovery then applies its stagger to that. Without this step the whole clock half of
    the store is present-and-inert, which is why the cutover could not proceed.
    """
    from personalclaw.triggers.arm import next_fire
    from personalclaw.triggers.scheduling import (
        BOOT_STAGGER_WINDOW_SECS,
        boot_recovery,
        jitter_offset,
    )

    out: list[tuple[str, float, str]] = []
    for trigger in triggers:
        if not trigger.enabled:
            continue
        current = to_epoch(trigger.next_fire_at)
        if current <= 0:
            # Nothing to recover — compute the FIRST fire from the spec. An unarmable trigger
            # (invalid cron, elapsed one-shot, non-clock kind) returns 0.0 and is skipped rather
            # than being armed to `now`, which would fire a missed appointment immediately.
            armed = next_fire(trigger, now=now)
            if armed <= 0:
                continue
            out.append((trigger.id, armed, "armed from spec"))
            continue
        catch_up = bool(getattr(trigger, "catch_up", False))
        new_at, reason = boot_recovery(
            next_fire_at=current,
            now=now,
            trigger_id=trigger.id,
            catch_up=catch_up,
        )
        # 🔴 `missed_dropped` RETURNS TO THE GRID when the grid is far enough away (S142). Measured
        # once the sweep was actually wired: a `catch_up: false` 03:00 daily backup, overdue because
        # the laptop was shut, was re-armed by `boot_recovery` to **09:02** — so the slot the
        # function had just decided to DROP fired six hours late anyway, off-schedule, and the
        # trigger's own cron expression was ignored. Latent until now because nothing called
        # `plan_boot`, so a wrong `next_fire_at` was never written; wiring the sweep would ship it.
        #
        # What changes is the ANCHOR, not the jitter: the drop path resumes from the trigger's own
        # next real slot (`arm.next_fire`) instead of from `now`, and keeps the same deterministic
        # per-id spread on top of it. §3.1 requires both — "recovered/re-armed on gateway boot" AND
        # a stagger so a restart does not fire everything in one second — and dropping the jitter
        # satisfies only the first. Driven: six co-phased hourly triggers all resume to exactly
        # `now + 3600` without it, so the stampede returns one interval later instead of being
        # prevented. The jitter window (120s) is small against any real schedule, which is why
        # spreading inside it is not the same thing as re-phasing.
        if reason == "missed_dropped":
            on_grid = next_fire(trigger, now=now)
            if on_grid > 0:
                new_at = on_grid + jitter_offset(trigger.id, BOOT_STAGGER_WINDOW_SECS)
                reason = "missed_dropped_resumed_on_grid"
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

    **🔴 EVERY clock kind reschedules here now (S96).** This returned 0.0 for
    `cron`/`at`/`sequence` on the premise that "the recurrence engine" owned them — but no
    such engine existed, so measured: a cron fired once and then kept `next_fire_at` at its
    ELAPSED slot, which every later tick read as still-due. Not merely inert: a fire storm on
    one past slot. `arm.next_fire` is the one recurrence computation (spec → next fire) and it
    owns all four kinds, so there is no second path to disagree with. A `cron` recomputes from
    ITS OWN expression (never from completion, which would drift a 9am job later every day);
    an `interval` keeps §3.1's completion-anchored rule.
    """
    from personalclaw.triggers.arm import next_fire
    from personalclaw.triggers.scheduling import recompute_from_completion

    interval = _interval_secs(trigger)
    if interval > 0:
        anchor = _created_at(trigger) or now
        return recompute_from_completion(
            interval_secs=interval, created_at=anchor, completed_at=completed_at
        )
    # cron / at: the spec decides. `at` correctly yields 0.0 once elapsed — a one-shot has no next
    # fire, and `delete_after_run` retires the row.
    return next_fire(trigger, now=max(now, completed_at))


async def tick(
    store: Any,
    *,
    now: float = 0.0,
    persist: bool = True,
    user_active: bool = False,
    base_dir: Any = None,
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

    Claims are read before the gate walk and written after a grant, which is what makes `overlap`
    enforce across ticks AND across processes — see `claims.py` for the defects that closed.

    🔴 The claim root is DERIVED FROM THE STORE (`base_dir` only overrides it), so claims
    always land beside the `triggers.json` they describe. Measured the alternative: defaulting
    to the active home made a tick over a `tmp_path` store write claims into the REAL
    `~/.personalclaw`, where leftovers then blocked unrelated tests' fires. A claim describing
    one store must not live in another.
    """
    from personalclaw.triggers import claims, screen

    now = now or time.time()
    base_dir = base_dir if base_dir is not None else getattr(store, "base_dir", None)
    result = TickResult()

    result.store_changed = bool(getattr(store, "changed_on_disk", lambda: False)())
    rows = store.load()
    triggers = [row.trigger for row in rows if getattr(row, "ok", True)]
    by_id = {t.id: t for t in triggers}

    from personalclaw.triggers import firepath as fp

    # Named resource slots, read ONCE per tick (§3.5 — S135). Per-trigger would re-scan every claim
    # for every due trigger; once per tick also makes the answer consistent within a tick, so two
    # triggers wanting `local-llm` in the same wake cannot both be told it is free.
    slot_map = claims.slot_holders(store, now=now, base_dir=base_dir)

    # 🔴 UNPARK, before the due set is computed (§3.7 / decision 9 — S159). A parked trigger has
    # `state != ACTIVE`, so `fires_automatically` is False and `due_ids` filters it out — so
    # unparking AFTER that walk would never bring anything back. `autopause.unpark_due` has always
    # implemented this decision and had NO caller, and `retry_after` was never persisted, so a
    # trigger parked by one transient outage stayed parked forever: measured, 5 fires while active
    # and 0 over the next 5 slots after a single `transport_unavailable`.
    #
    # Driven by the CLOCK rather than by an outcome, exactly as `unpark_due`'s docstring says: a
    # parked trigger produces no fires, so nothing in the outcome path could ever revive it.
    result.unparked.extend(_unpark_ready(store, triggers, now=now, persist=persist))

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
        if persist:
            if advanced > 0:
                trigger.next_fire_at = to_iso(advanced)
                store.upsert(trigger)
                result.rescheduled.append(trigger.id)
            else:
                # 🔴 A trigger with no next fire is RETIRED here, never left holding its elapsed
                # `next_fire_at`. Measured: a one-shot `at` kept the past timestamp, so EVERY later
                # tick read it as still-due and re-fired it — a storm on a single past slot, not
                # merely an inert row. `delete_after_run` (declared in the clock spec, defaulting
                # True for a migrated `at`, and until now consumed by nothing) decides which:
                # delete the row, or clear the fire and disable so it stays visible in the UI.
                spec = trigger.spec if isinstance(trigger.spec, dict) else {}
                if bool(spec.get("delete_after_run", False)):
                    store.delete(trigger.id)
                    result.retired.append(trigger.id)
                else:
                    trigger.next_fire_at = ""
                    trigger.enabled = False
                    store.upsert(trigger)
                    result.retired.append(trigger.id)

        ctx = fp.FireContext(
            trigger_id=trigger.id,
            gates=trigger.gates or {},
            # 🔴 `payload_text` is deliberately LEFT EMPTY here (§7/R4 rule a — S134), and that is
            # correct rather than the omission it looks like. A clock trigger carries no external
            # content: at tick time there is a schedule and no payload. The screen's real input
            # arrives with a POLLED payload — web_watch items, file changes — which is dispatched
            # through `gateway._fire_store_trigger`, NOT through this walk. S134 screens there.
            #
            # Written down because the DEFAULT is what hid the gap: `payload_text=""` made
            # `if ctx.payload_text:` false, so every clock fire's ledger row listed `screen` among
            # the gates PASSED while the screen had never run on a single real fire.
            capabilities=trigger.capabilities,
            holder=f"tick:{int(now)}",
            overlap=str(getattr(trigger, "overlap", "skip") or "skip"),
            now=now,
            user_active=user_active,
            yield_to_user=bool(getattr(trigger, "yield_to_user", False)),
            # 🔴 THE RESOURCE SLOT (§3.5 — S135). `resource_slots` was declared, persisted and
            # round-tripped, and read by NOTHING — the only field in 41 trigger dataclasses with
            # zero non-declaration readers. Supplied here from the claim store, so a fire that
            # needs `local-llm` while another trigger holds it defers instead of contending.
            # 🔴 The SPACING meter (S151). `debounce_secs`/`cooldown_secs` were declared in
            # `GATE_KEYS` and read by nothing because no last-FIRE timestamp existed —
            # `last_success_at`/`last_failure_at` describe an outcome, and a suppressed fire is
            # neither. `_since_last_fire` returns None for a trigger that has never fired, which
            # the gate reads as "nothing to space against" rather than "0 seconds ago".
            # 🔴 The RATE meter (S152). Three cap keys waited on a windowed history query that
            # did not exist; `ScheduleRunStore.count_since` is it. Read per DUE trigger rather
            # than once per tick because it is per-job JSONL — a tick with one due trigger must
            # not scan every trigger's history. None (unreadable) is NOT zero: see the gate.
            fires_in_window=await _fires_in_window(trigger, now=now),
            since_last_fire=_since_last_fire(trigger, now=now),
            busy_slot=claims.busy_slot(trigger, holders=slot_map),
            # 🔴 The EXISTING claim, read from the shared claim store. Measured: this was never
            # supplied, so `claim_fire` always saw `existing=None` and always granted — a trigger
            # whose previous run was still going fired again anyway, which is the precise failure
            # `overlap` exists to prevent. The gate was present, reviewed, and enforcing nothing.
            existing_claim=claims.read_claim(trigger.id, now=now, base_dir=base_dir),
            # 🔴 WHAT THE TRIGGER ACTUALLY ASKS FOR (S116). This was omitted, so `evaluate`'s
            # `if ctx.requested:` was always false and the frozen-capability fence — decision 7's
            # enforcement point — had never run on a single real fire. Exactly the `existing_claim`
            # defect one line up, in the gate directly below it.
            requested=screen.requested_capabilities(trigger),
            # 🔴 THE BUDGET, actually supplied (§7 crit 8 / §3.6 — S133). Measured: `tick` never set
            # either budget field, so `if ctx.budget_remaining is not None` was always False and the
            # budget gate had NEVER refused a real fire — the third instance of this exact shape
            # after S97's `existing_claim` and S116's `requested`. `gates.max_fires` was the
            # user-visible cost: set to 2, a trigger fired 8 times in 8 slots.
            budget_remaining=_budget_remaining(trigger),
        )
        decision = await fp.evaluate(ctx)
        row = fp.ledger_row(decision, ctx)
        row["scheduled_for"] = scheduled_for
        result.ledger_rows.append(row)

        if decision.allowed:
            # Persist the granted claim so the NEXT tick (and any other process — the MCP tools and
            # the API read the same store) can see this run in flight. `firepath` already notes "the
            # caller must release it"; the executor's drain releases on completion.
            if persist and decision.claim is not None:
                claims.write_claim(decision.claim, base_dir=base_dir)
            # The counter the budget READS. Nothing incremented `run_count` on this path, so even a
            # wired budget would have compared against a permanent zero — a cap needs a meter.
            # Incremented on a GRANTED fire, before dispatch: `max_fires` bounds attempts the
            # substrate authorised, and deferring the increment to completion would let a storm of
            # in-flight fires all pass a cap of one.
            #
            # 🔴 NOT persisted for a RETIRED trigger. Found by a red test rather than by reading: the
            # retirement branch above `store.delete()`s a `delete_after_run` one-shot, and an
            # unconditional upsert here RESURRECTED the row it had just removed — turning a retired
            # one-shot back into a live trigger holding an elapsed slot, which is the storm S112's
            # retirement exists to prevent. The in-memory count still rides along on the DueFire.
            trigger.run_count = int(getattr(trigger, "run_count", 0) or 0) + 1
            # The meter the SPACING gate reads (S151). Written here and nowhere else, for the
            # same reason `run_count` is: this is the one point a fire is GRANTED. Writing it
            # at completion would let a burst of in-flight fires all see the same stale
            # timestamp and every one pass a debounce; writing it on a SUPPRESSED fire would
            # make a blocked fire space out the next real one.
            trigger.last_fired_at = to_iso(now)
            if persist and trigger.id not in result.retired:
                store.upsert(trigger)
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


async def _fires_in_window(trigger: Any, *, now: float) -> int | None:
    """Fires recorded in the last hour, or None when the ledger could not be read (S152).

    Returns None — not 0 — on ANY failure. Zero would hand a runaway trigger a fresh allowance
    every time the ledger hiccuped, which is the opposite of what a rate cap is for. The gate treats
    None as fail-open (§1.4's storm-guard class) but the distinction is kept so a future session can
    tighten it without first re-deriving why the two cases differ.

    Skipped entirely when the trigger declares no hourly cap: this is a file read on the fire path,
    and paying for it to answer a question nobody asked would tax every automation on the machine.
    """
    gates = getattr(trigger, "gates", None)
    gates = gates if isinstance(gates, dict) else {}
    if not any(gates.get(k) for k in ("rate_cap", "max_runs_per_hour", "max_actions_per_hour")):
        return None
    try:
        from personalclaw.config.loader import config_dir
        from personalclaw.schedule_history import ScheduleRunStore

        return await ScheduleRunStore(config_dir()).count_since(trigger.id, now - 3600.0)
    except Exception:  # noqa: BLE001 - an unreadable ledger must not break the tick
        logger.debug("could not read the rate window for %s", getattr(trigger, "id", "?"))
        return None


def _unpark_ready(store: Any, triggers: list[Any], *, now: float, persist: bool) -> list[str]:
    """Return PARKED triggers to ACTIVE once their cooldown has elapsed (§3.7 / decision 9 — S159).

    🔴 WHY THIS EXISTS. `autopause.unpark_due` implements the clock decision and had **no caller**,
    and `evaluate`'s `retry_after` was never persisted — so parking was a one-way door.
    Measured on a real store: a trigger fired 5 times over 5 slots while active, then one
    `transport_unavailable` parked it and it fired **0 times over the next 5 slots and stayed
    `parked` indefinitely**. `TriggerState.PARKED`'s own docstring says parking "is not a
    failure — it is 'the resource this needs is busy', which resolves on its own", and
    nothing made it resolve.

    Mutates the passed `triggers` list in place as well as the store, because the caller has already
    built `by_id` from it and computes `due_ids` next: a revived trigger has to be visible to THIS
    tick, or unparking would always cost an extra full cooldown before anything fired.

    Only `PARKED` is revived. `autopaused` is five true failures and wants a human; `quarantined` is
    an injection match that `resume_state` refuses even from a button; `paused` is the user's own
    decision. Reviving any of them on a timer would override a judgement someone made.

    The counter is NOT reset here. Parking never spent the failure budget in the first place (a
    parking exit leaves `consecutive_failures` untouched, deliberately, so a flapping credential
    cannot clear a real streak), so clearing it on unpark would hand a genuinely failing trigger a
    fresh budget every time an unrelated outage parked it.
    """
    from personalclaw.triggers import autopause

    unparked: list[str] = []
    for trigger in triggers:
        if str(getattr(trigger, "state", "")) != TriggerState.PARKED.value:
            continue
        if not autopause.unpark_due(
            retry_after=float(getattr(trigger, "park_retry_after", 0.0) or 0.0), now=now
        ):
            continue
        trigger.state = TriggerState.ACTIVE.value
        trigger.health_status = TriggerHealth.OK.value
        trigger.park_retry_after = 0.0
        unparked.append(trigger.id)
        if persist:
            store.upsert(trigger)
    if unparked:
        logger.info("unparked %d trigger(s) whose cooldown elapsed: %s", len(unparked), unparked)
    return unparked


def _since_last_fire(trigger: Any, *, now: float) -> float | None:
    """Seconds since this trigger last fired, or None when it never has (S151).

    None rather than 0.0, and rather than a large number: "never fired" is a different
    fact from "fired long ago", and only None lets the spacing gate tell "nothing to
    space against" from a real interval. Reading an absent timestamp as 0.0 would block
    every trigger's FIRST fire behind its own debounce — a first-run deadlock.

    A timestamp in the FUTURE (a clock that moved backwards, a hand-edited row) clamps to
    0.0 rather than going negative. A negative "seconds since" compares as less than
    every window and would suppress forever, so the safe reading is "it just fired":
    one skipped fire, not a permanently dead trigger.
    """
    stamp = to_epoch(str(getattr(trigger, "last_fired_at", "") or ""))
    if stamp <= 0:
        return None
    return max(0.0, now - stamp)


def _budget_remaining(trigger: Any) -> float | None:
    """Fires this trigger may still make, or None when it declares no cap (§3.6 — S133).

    🔴 WHY THIS EXISTS. `firepath`'s budget gate reads `ctx.budget_remaining`, and `tick` never set
    it — so `if ctx.budget_remaining is not None` was always False and the gate had never refused a
    real fire. Third instance of the same shape: S97's `existing_claim`, S116's `requested`, this.
    The user-visible cost was `gates.max_fires`, which is declared in `GATE_KEYS`, validated,
    carried by `LEGACY_FIELD_MAP` — and bounded nothing. Measured: `max_fires: 2` produced 8 fires
    in 8 slots, identical to no cap at all.

    Scoped deliberately to `max_fires`. `max_runs_per_hour`/`max_actions_per_hour`/`rate_cap` got
    their meter at S152 (the `rate` gate) and `cost_cap`/`max_cost_usd_per_run` got per-run spend
    ATTRIBUTION at S153 — but attribution is not yet enforcement here: nothing on this path reads a
    run's accrued dollars against the cap, so those two stay in `UNMETERED_CAPS`. Historic note:
    `cost_cap` / `max_cost_usd_per_run` needed per-run spend attribution and `max_runs_per_hour` /
    `max_actions_per_hour` need a windowed history query — neither exists on this path, and
    inventing a meter to satisfy a cap would be the inverted dependency this program keeps refusing
    (S119's webhook token, S129's rule (e)). A doctor finding names the still-unenforced caps
    instead of implying they work.

    None (no cap) rather than infinity: the gate distinguishes "no budget configured" from "budget
    exhausted", and a sentinel would make an unset cap indistinguishable from a very large one.
    """
    gates = trigger.gates if isinstance(getattr(trigger, "gates", None), dict) else {}
    try:
        cap = int(gates.get("max_fires", 0) or 0)
    except (TypeError, ValueError):
        # A malformed cap is NOT treated as unlimited. `validate_gates` already reports the shape;
        # here the safe reading of "I asked for a limit and typed it wrong" is zero allowance, which
        # refuses visibly rather than running unbounded.
        return 0.0
    if cap <= 0:
        return None
    used = int(getattr(trigger, "run_count", 0) or 0)
    return float(max(0, cap - used))


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

    🔴 THE REVIEW IS SNAPSHOT BEFORE RE-ARMING (S142), and that ordering is the whole function.
    `plan_boot`'s recovery pushes an overdue `next_fire_at` into the stagger window, IN PLACE on the
    same `Trigger` objects. Measured with the review taken afterwards: a trigger overdue by an hour
    (61 missed minutely slots) reported **0 review rows** — because the missed anchor is derived
    from `next_fire_at`, and by then that pointed into the FUTURE. Re-arming destroys the only
    evidence that anything was missed, so the evidence has to be read first.
    """
    from personalclaw.triggers.missed import review_at_boot

    now = now or time.time()
    rows = store.load()
    triggers = [row.trigger for row in rows if getattr(row, "ok", True)]

    # Snapshot BEFORE `plan_boot` re-arms — see the docstring.
    review = review_at_boot([t.to_dict() for t in triggers], now=now)
    caught_up = catch_up_at_boot(triggers, now=now)

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

    return {
        "rearmed": rearmed,
        "total": len(triggers),
        "review": review.to_dict() if hasattr(review, "to_dict") else {},
        "catch_up": caught_up,
        "next_sleep": sleep_for(triggers, now=now),
    }


def catch_up_at_boot(triggers: list[Trigger], *, now: float) -> list[dict[str, Any]]:
    """Which triggers get an automatic catch-up fire at this boot, and why the rest do not.

    A thin adapter over `missed.catch_up_plan` so `boot` reports one shape and the storm guards
    live in exactly one place. Returns EVERY candidate including the refused ones: §3.4's rule is
    that a `catch_up: true` trigger which did NOT catch up needs an explanation as much as one that
    did, and a list of only the winners cannot answer "why not mine".

    Snapshot before re-arming for the same reason the review is — `missed_last_slot` is "is the
    armed fire in the past", which recovery makes false by design.
    """
    from personalclaw.triggers.missed import catch_up_plan

    out: list[dict[str, Any]] = []
    for trigger_id, fire_at, reason in catch_up_plan([t.to_dict() for t in triggers], now=now):
        out.append(
            {
                "id": trigger_id,
                "fire_at": fire_at,
                "reason": reason,
                "catching_up": fire_at > 0,
            }
        )
    return out


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
