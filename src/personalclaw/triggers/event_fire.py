"""Firing `kind: "event"` triggers — the gateway half of the event bus.

The bus (`personalclaw.event_triggers.emit_event`) is called by every source — a memory write, an
accepted inbox message, an app's trigger source — from whatever thread did the work. The gateway
attaches ONE `EventRouter` to it at boot, and for each event the router:

1. **matches** the event against the store's `event` rows (`provider.armable`, so a broken or
   foreign row is never a candidate), with the pattern grammar in `event_triggers.matches`;
2. **admits** each match through `service.admit_fire` — the same gate walk a clock fire takes:
   incident, spacing (`debounce_secs`/`cooldown_secs`), rate, quiet, duty, budget (`max_fires`),
   the overlap claim, slots, liveness, yield, the frozen capability fence — writing a typed row for
   every refusal;
3. **dispatches** each admitted fire through the one store dispatch
   (`gateway._fire_store_trigger`): screen, fence, secrets, denylist, rungs, day budget, execute,
   the run record, autopause, delivery, chaining. The claim is released when that returns.

Beside the per-trigger gates there is one guard across ALL event triggers: at most
`STORM_MAX_FIRES` fires per `STORM_WINDOW_SECS`. It is the feedback-loop backstop (decision 5a) —
an action that writes memory re-emits an event, and while `overlap: skip` and a debounce stop one
trigger feeding itself, only a global cap stops two triggers feeding each other. A fire it refuses
leaves a `skipped_gate` row naming the cap, never a silent drop.

A trigger with a `max_fires` budget is RETIRED — switched off, left visible — by the fire that
spends its last allowance: "tell me the NEXT time X" is a one-shot, and a spent trigger that stays
enabled writes a `skipped_budget` row for every matching event afterwards and reads as live on the
Triggers page.

Events are delivered one at a time (an `asyncio.Lock`), so two events arriving together see each
other's claims and budget — the property the tick gets by being one sequential walk.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from personalclaw.event_triggers import BusEvent, fire_payload

logger = logging.getLogger(__name__)

#: The cross-trigger storm guard: at most this many event fires per window (decision 5a). The value
#: the retired `event_triggers` engine enforced, carried over unchanged.
STORM_WINDOW_SECS = 60.0
STORM_MAX_FIRES = 30

#: `dispatch(trigger, payload, *, event, context)` — the gateway's `_fire_store_trigger`.
Dispatch = Callable[..., Awaitable[Any]]


class EventRouter:
    """Matches, admits and dispatches bus events inside the gateway process.

    Callable, because that is the bus's router contract: `emit_event` hands each event to
    `router(event)` from whatever thread reported it, and this must return at once and never raise.
    The work itself runs on the gateway's loop.

    ``enabled=False`` is the ``--no-crons`` gateway: automations are off there, so an event is
    declined (and logged) rather than left for the dispatch spool — a spooled event would fire on
    the next normal boot, hours late, which is not what "automations disabled" means.
    """

    def __init__(
        self,
        *,
        dispatch: Dispatch,
        loop: asyncio.AbstractEventLoop,
        enabled: bool = True,
        base_dir: Any = None,
    ) -> None:
        self._dispatch = dispatch
        self._loop = loop
        self._enabled = enabled
        self._base_dir = base_dir
        self._lock = asyncio.Lock()
        self._pending: set[asyncio.Future[Any]] = set()
        self._fire_times: list[float] = []

    # ── the bus's side: any thread, never raises ──

    def __call__(self, event: BusEvent) -> None:
        if not self._enabled:
            logger.debug(
                "event %s.%s declined: automations are disabled (--no-crons)",
                event.source,
                event.event_type,
            )
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        try:
            if running is self._loop:
                self._spawn(event)
            else:
                # A worker thread (a memory write run through `asyncio.to_thread`) or the gateway's
                # own code on another loop: hand it over rather than running it here.
                self._loop.call_soon_threadsafe(self._spawn, event)
        except RuntimeError:
            # The loop is closed — the gateway is shutting down after the router was left attached.
            logger.warning(
                "event %s.%s arrived after the gateway loop closed; not fired",
                event.source,
                event.event_type,
            )

    def _spawn(self, event: BusEvent) -> None:
        self._track(self._loop.create_task(self.deliver(event)))

    def _track(self, task: asyncio.Future[Any]) -> None:
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def settle(self) -> None:
        """Wait until every event handed over so far — and every fire it started — has finished.

        Shutdown awaits it, and so do the tests: a caller that asserts on a fire must first know the
        fire is over, and polling for a side effect is how a slow run reads as a missing one.
        """
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    # ── the gateway's side ──

    async def deliver(self, event: BusEvent) -> list[str]:
        """Match, admit and dispatch ONE event. Returns the trigger ids that fired. Never raises."""
        async with self._lock:
            try:
                return await self._deliver(event)
            except Exception:  # noqa: BLE001 - one bad event must not stop the next
                logger.warning(
                    "could not deliver the %s.%s event",
                    event.source,
                    event.event_type,
                    exc_info=True,
                )
                return []

    async def _deliver(self, event: BusEvent) -> list[str]:
        from personalclaw.config.loader import config_dir
        from personalclaw.triggers.provider import armable
        from personalclaw.triggers.service import admit_fire, budget_spent
        from personalclaw.triggers.store import TriggerStore

        store = TriggerStore(base_dir=self._base_dir or config_dir())
        base_dir = store.base_dir
        fired: list[str] = []
        for trigger in armable(store):
            if not event.matches(trigger):
                continue
            # The moment of ADMISSION, not the event's own timestamp: a spooled event can be minutes
            # old, and the spacing and rate gates measure how often this trigger FIRES.
            now = time.time()
            if not self._storm_allows(now):
                await self._record_storm(trigger.id, now=now, base_dir=base_dir)
                continue
            admission = await admit_fire(
                store, trigger, now=now, base_dir=base_dir, holder=f"event:{int(now)}"
            )
            if not admission.allowed:
                continue
            self._fire_times.append(now)
            if budget_spent(trigger):
                # The last allowance: the trigger retires, visibly, exactly as the fire that spends
                # a one-shot clock's slot retires it.
                trigger.enabled = False
                store.upsert(trigger)
                logger.info("event trigger %s spent its max_fires budget; switched off", trigger.id)
            payload, context = fire_payload(trigger.id, event)
            self._track(
                asyncio.create_task(
                    self._run(trigger, payload, context=context, event=event, base_dir=base_dir)
                )
            )
            fired.append(trigger.id)
        return fired

    async def _run(
        self, trigger: Any, payload: dict[str, Any], *, context: str, event: BusEvent, base_dir: Any
    ) -> None:
        """Run one admitted fire through the store dispatch, then give its claim back."""
        from personalclaw.triggers.executor import release_claim_for

        try:
            await self._dispatch(
                trigger, payload, event=f"{event.source}.{event.event_type}", context=context
            )
        except Exception:  # noqa: BLE001 - the dispatch records its own outcome; never re-raise
            logger.warning("event trigger %s: dispatch raised", trigger.id, exc_info=True)
        finally:
            # `admit_fire` wrote a claim so `overlap` can enforce; a run that has settled must hand
            # it back, or the trigger reads as running and refuses its next event for the claim's
            # full lifetime.
            release_claim_for(trigger.id, base_dir=base_dir)

    def _storm_allows(self, now: float) -> bool:
        self._fire_times = [t for t in self._fire_times if now - t < STORM_WINDOW_SECS]
        return len(self._fire_times) < STORM_MAX_FIRES

    async def _record_storm(self, trigger_id: str, *, now: float, base_dir: Any) -> None:
        from personalclaw.triggers.models import Outcome
        from personalclaw.triggers.service import persist_suppression

        reason = (
            f"the event storm guard held: {STORM_MAX_FIRES} event fires in the last "
            f"{int(STORM_WINDOW_SECS)}s across all event triggers"
        )
        logger.warning("event trigger %s not fired: %s", trigger_id, reason)
        await persist_suppression(
            {"trigger_id": trigger_id, "outcome": Outcome.SKIPPED_GATE.value, "reason": reason},
            now=now,
            base_dir=base_dir,
        )
