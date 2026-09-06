"""Auto-nudge, riding the automations substrate (WF2AUT-11 half 2 — the loop-ticker port).

This is the thin adapter that replaced ``personalclaw/autonudge.py``. The OLD module was two
things fused: a per-session nudge STORE (``autonudge.json``) and a private TICK ENGINE (one
``asyncio`` timer per loop). Half 1 of WF2AUT-11 shipped ``kind:idle`` + ``idle_poll`` for user
automations; this module is half 2 — the loop tick engine now rides the same substrate:

* **State** lives in the trigger store: each nudge loop is a ``Trigger{kind: "idle"}`` row whose
  spec carries ``message`` (the marker that routes the fire to this adapter instead of the wake
  path), ``max_cycles`` and ``stop_sentinel_path`` beside the kind's original
  ``scope``/``idle_secs``/``first_idle_secs``. Turn-churning counters (``armed_at``,
  ``cycle_count``, ``error_count``, …) live in the idle SIDECAR (`trigger-idle/`), exactly as the
  user rows' do — writing them on the row would rewrite ``triggers.json`` every turn (S61d).
* **The tick** is the substrate's: ``triggers/loop.tick_once`` → ``idle_poll.due_fires`` decides
  due-ness from ``armed_at`` + ``wait_secs`` on a ≤30s cadence. The private timers are GONE.
* **Delivery** stays injected: the gateway constructs this service with the same ``on_fire``
  callback (the loop-cycle driver — nudge injection, ``run_chat``, deliverable-forcing re-prompts,
  watchdog reporting). ``idle_poll.poll`` hands a due message-bearing row to :meth:`deliver`,
  which keeps the OLD timer's fire path verbatim in meaning: stop-sentinel → remove; ``max_cycles``
  → deactivate; delivered-only counting via ``idle_poll.record_delivery``.
* **Backpressure keeps its exact call-site contracts.** ``notify_turn_complete`` re-arms by
  restamping ``armed_at`` (the timer re-arm, as state — the translation half 1 already ratified
  for ``notify_activity``) and still deactivates after ``_MAX_CONSECUTIVE_ERRORS`` consecutive
  errored turns; ``notify_user_input`` restamps too (the cancel, as state — the turn the input
  starts restamps again on completion, so the arm point converges on turn-end exactly as the
  cancelled-timer world's did). Both stay reachable through the module singleton
  ``get_instance()`` from fail-open try/except on the chat hot paths.

Everything that consumed the old service — ``loop/manager.py``, ``loop/watchdog.py``,
``planning/runner.py``, the ``/api/autonudge`` HTTP surface — speaks the same duck type:
``add/update/remove/get_by_session/list_all/subscribe/notify_*`` and the :class:`NudgeLoop` view,
which is assembled from row + sidecar so ``asdict()`` keeps the wire byte-compatible.

What restarts look like now: rows and arm points PERSIST (the timers did not), and ``start()``
restamps ``armed_at`` for every active loop — the old boot re-arm, so a gateway restart still
waits a full quiet period before the first fire. A legacy ``autonudge.json`` found at start is
migrated losslessly into rows + sidecars and renamed ``autonudge.json.migrated``.

Feature-flagged via env ``PERSONALCLAW_AUTONUDGE`` (on by default; ``0`` disables) — the flag
gates THIS adapter only; plain (message-less) user idle triggers were never gated by it and still
are not.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from personalclaw import shutdown_event
from personalclaw.triggers import idle_poll
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

logger = logging.getLogger(__name__)

_MIN_IDLE_SECS = 15
_MAX_IDLE_SECS = 86400  # 24h
# Consecutive errored turns before a loop is deactivated (defensive cap so a
# loop can't spin forever on a hard error). Resets on any clean turn.
_MAX_CONSECUTIVE_ERRORS = 3

# Sentinel file per loop: creating it halts the loop on next cycle.
STOP_SENTINEL = "STOP"

#: New rows are minted under this id namespace; migrated legacy loops KEEP their original 8-hex
#: ids verbatim, because the id is the wire handle (`PATCH /api/autonudge/{loop_id}`) and the
#: dashboard may hold one across the restart that migrates.
_ID_PREFIX = "nudge:"

_LEGACY_FILE = "autonudge.json"


def enabled() -> bool:
    """Feature flag — on by default. Set ``PERSONALCLAW_AUTONUDGE=0`` to disable."""
    return os.environ.get("PERSONALCLAW_AUTONUDGE", "1").lower() not in ("0", "false", "no")


# Module-level singleton so hooks in chat_handlers.py / chat_runner.py can notify the
# service without needing a reference to the gateway. Set by AutoNudgeService
# on start(); cleared on stop().
_INSTANCE: "AutoNudgeService | None" = None


def get_instance() -> "AutoNudgeService | None":
    return _INSTANCE


@dataclass
class NudgeLoop:
    """A single auto-nudge loop bound to one session — the row+sidecar VIEW.

    Field-compatible with the deleted ``autonudge.NudgeLoop`` so ``asdict()`` keeps the
    ``/api/autonudge`` wire and the ``autonudge_state`` WS payloads byte-identical.
    """

    id: str
    session_name: str
    message: str
    idle_secs: int = 60
    max_cycles: int = 0  # 0 = unlimited
    cycle_count: int = 0
    active: bool = True
    last_fire_ts: float = 0.0
    created_ts: float = 0.0
    stop_sentinel_path: str = ""  # optional absolute path; if present loop halts
    error_count: int = 0  # consecutive errored turns; resets on a clean turn
    # One-shot short delay for the FIRST fire (0 = disabled). Presented as 0 once a fire has
    # been delivered — `idle_poll.wait_secs` keys the one-shot on `cycle_count == 0`, so the
    # spec value going stale after the first fire is impossible by construction.
    first_idle_secs: int = 0


def is_nudge(trigger: Any) -> bool:
    """Whether an idle trigger is a NUDGE row (delivered by this adapter, not the wake path).

    The discriminator is semantic, not an ownership tag: an idle row with a ``message`` means
    "inject this into the bound conversation when it goes quiet" — autonudge's contract — and a
    hand-authored one behaves identically to a loop-minted one.
    """
    spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
    return bool(str(spec.get("message") or "").strip())


class AutoNudgeService:
    """Manages reactive per-session nudge loops over the trigger store + idle sidecars."""

    def __init__(
        self,
        base_dir: Path | None = None,
        on_fire: Callable[[NudgeLoop], Awaitable[bool]] | None = None,
    ) -> None:
        from personalclaw.config.loader import config_dir

        self._base_dir = base_dir or config_dir()
        self._store = TriggerStore(base_dir=self._base_dir)
        self._on_fire = on_fire
        self._observers: list[Callable[[str, NudgeLoop | None], None]] = []
        self._lock = asyncio.Lock()

    # ── row <-> view ──

    def _rows(self) -> list[Trigger]:
        """Every nudge row, INCLUDING deactivated ones.

        Deliberately not ``provider.armable``: a deactivated loop must stay visible —
        ``list_all`` renders it, and the watchdog's ``_loop_exhausted`` reads its
        ``cycle_count`` AFTER deactivation to tell "budget spent" from "paused mid-budget".
        """
        try:
            rows = self._store.list_triggers(kind="idle")
        except Exception:  # noqa: BLE001 - a broken store must not take the chat hot path down
            logger.warning("nudge: trigger store unreadable", exc_info=True)
            return []
        return [t for t in rows if is_nudge(t)]

    def _to_loop(self, trigger: Trigger) -> NudgeLoop:
        spec = trigger.spec if isinstance(trigger.spec, dict) else {}
        state = idle_poll.load_state(trigger.id, base_dir=self._base_dir)

        def _int(key: str, default: int = 0) -> int:
            try:
                return int(spec.get(key, default) or default)
            except (TypeError, ValueError):
                return default

        first = _int("first_idle_secs")
        return NudgeLoop(
            id=trigger.id,
            session_name=idle_poll.scope_session(trigger),
            message=str(spec.get("message") or ""),
            idle_secs=_int("idle_secs", 60),
            max_cycles=_int("max_cycles"),
            cycle_count=state.cycle_count,
            active=bool(trigger.enabled),
            last_fire_ts=state.last_fire,
            created_ts=state.created_ts,
            stop_sentinel_path=str(spec.get("stop_sentinel_path") or ""),
            error_count=state.error_count,
            # One-shot: spent once a fire has been DELIVERED, exactly as the old engine
            # cleared the field after the first fire.
            first_idle_secs=0 if state.cycle_count > 0 else first,
        )

    def _get_row(self, loop_id: str) -> Trigger | None:
        loaded = self._store.get(loop_id)
        if loaded is None or not is_nudge(loaded.trigger):
            return None
        return loaded.trigger

    # ── Observer hook (for WS broadcasts) ──

    def subscribe(self, cb: Callable[[str, NudgeLoop | None], None]) -> None:
        self._observers.append(cb)

    def _emit(self, event: str, loop: NudgeLoop | None) -> None:
        for cb in self._observers:
            try:
                cb(event, loop)
            except Exception:
                logger.warning("AutoNudge observer failed", exc_info=True)

    # ── Lifecycle ──

    async def start(self) -> None:
        if not enabled():
            logger.info("AutoNudge disabled (PERSONALCLAW_AUTONUDGE=0)")
            return
        self._migrate_legacy()
        # Boot re-arm: the old engine re-armed a timer per active loop on start, so a restart
        # waited a full quiet period before the first fire. Restamping `armed_at` is the same
        # thing as state — without it, a loop idle across a long downtime would fire on the
        # very first tick, mid-boot, before its worker session exists.
        now = time.time()
        count = 0
        for trigger in self._rows():
            if trigger.enabled:
                state = idle_poll.load_state(trigger.id, base_dir=self._base_dir)
                state.armed_at = now
                idle_poll.save_state(trigger.id, state, base_dir=self._base_dir)
                count += 1
        global _INSTANCE
        _INSTANCE = self
        logger.info("AutoNudge started (%d loops re-armed)", count)

    def stop(self) -> None:
        global _INSTANCE
        if _INSTANCE is self:
            _INSTANCE = None

    def _migrate_legacy(self) -> None:
        """Absorb a legacy ``autonudge.json`` into trigger rows + sidecars, LOSSLESSLY, once.

        Every ``NudgeLoop`` field has a destination: message/idle_secs/first_idle_secs/
        max_cycles/stop_sentinel_path → spec; cycle_count/last_fire_ts/error_count/created_ts →
        sidecar; active → ``enabled``; the loop id is kept VERBATIM (it is the wire handle).
        The file is renamed, not deleted, so a rollback to a pre-port build finds its store.
        """
        legacy = self._base_dir / _LEGACY_FILE
        if not legacy.exists():
            return
        import json

        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
            loops = data.get("loops", []) if isinstance(data, dict) else []
        except Exception:  # noqa: BLE001 - an unreadable legacy store must not stop the gateway
            logger.warning("AutoNudge: legacy %s unreadable — leaving in place", legacy)
            return
        migrated = 0
        for raw in loops:
            if not isinstance(raw, dict):
                continue
            try:
                loop_id = str(raw.get("id") or "") or f"{_ID_PREFIX}{uuid.uuid4().hex[:8]}"
                session_name = str(raw.get("session_name") or "")
                if not session_name:
                    continue
                self._store.upsert(
                    self._build_row(
                        loop_id=loop_id,
                        session_name=session_name,
                        message=str(raw.get("message") or ""),
                        idle_secs=int(raw.get("idle_secs", 60) or 60),
                        max_cycles=int(raw.get("max_cycles", 0) or 0),
                        stop_sentinel_path=str(raw.get("stop_sentinel_path") or ""),
                        first_idle_secs=int(raw.get("first_idle_secs", 0) or 0),
                        enabled=bool(raw.get("active", True)),
                    )
                )
                state = idle_poll.load_state(loop_id, base_dir=self._base_dir)
                state.cycle_count = int(raw.get("cycle_count", 0) or 0)
                state.last_fire = float(raw.get("last_fire_ts", 0.0) or 0.0)
                state.error_count = int(raw.get("error_count", 0) or 0)
                state.created_ts = float(raw.get("created_ts", 0.0) or 0.0)
                state.armed_at = time.time()
                idle_poll.save_state(loop_id, state, base_dir=self._base_dir)
                migrated += 1
            except Exception:  # noqa: BLE001 - one malformed loop must not strand the rest
                logger.warning("AutoNudge: skipping malformed legacy loop: %r", raw, exc_info=True)
        try:
            legacy.rename(legacy.with_suffix(".json.migrated"))
        except Exception:  # noqa: BLE001
            logger.warning("AutoNudge: could not rename migrated %s", legacy, exc_info=True)
        logger.info("AutoNudge: migrated %d legacy loops into the trigger store", migrated)

    # ── Loop CRUD ──

    def _build_row(
        self,
        *,
        loop_id: str,
        session_name: str,
        message: str,
        idle_secs: int,
        max_cycles: int,
        stop_sentinel_path: str,
        first_idle_secs: int,
        enabled: bool = True,
    ) -> Trigger:
        return Trigger(
            id=loop_id,
            name=f"Auto-nudge — {session_name}",
            kind="idle",
            enabled=enabled,
            created_by="system",
            spec={
                "scope": f"session:{session_name}",
                "idle_secs": idle_secs,
                "first_idle_secs": first_idle_secs,
                "message": message,
                "max_cycles": max_cycles,
                "stop_sentinel_path": stop_sentinel_path,
            },
            # The fire renders into the live conversation, and a mid-turn fire is dropped —
            # the same overlap semantics the kind always had.
            session=f"conversation:{session_name}",
            overlap="skip",
            delivery="none",
        )

    async def add(
        self,
        session_name: str,
        message: str,
        idle_secs: int = 60,
        max_cycles: int = 0,
        stop_sentinel_path: str = "",
        first_idle_secs: int = 0,
    ) -> NudgeLoop:
        idle_secs = max(_MIN_IDLE_SECS, min(_MAX_IDLE_SECS, int(idle_secs)))
        # The first-fire delay is clamped to [_MIN_IDLE_SECS, idle_secs] when set —
        # it's only ever a shortcut, never longer than the steady-state interval.
        first_idle_secs = (
            min(idle_secs, max(_MIN_IDLE_SECS, int(first_idle_secs))) if first_idle_secs else 0
        )
        async with self._lock:
            # One loop per session — replace any existing loop on this session.
            existing = self._find_by_session(session_name)
            if existing:
                self.remove_sync(existing.id)
            trigger = self._build_row(
                loop_id=f"{_ID_PREFIX}{uuid.uuid4().hex[:8]}",
                session_name=session_name,
                message=message,
                idle_secs=idle_secs,
                max_cycles=max(0, int(max_cycles)),
                stop_sentinel_path=stop_sentinel_path,
                first_idle_secs=first_idle_secs,
            )
            self._store.upsert(trigger)
            # Arm from creation (the old engine armed the timer on add) and stamp the birth
            # time — the trigger entity deliberately keeps no birth time (its LEGACY_FIELD_MAP
            # says so), so the view's `created_ts` lives in the sidecar.
            now = time.time()
            state = idle_poll.IdleState(armed_at=now, created_ts=now)
            idle_poll.save_state(trigger.id, state, base_dir=self._base_dir)
            loop = self._to_loop(trigger)
        self._emit("added", loop)
        logger.info(
            "AutoNudge: added loop %s on session %s (idle=%ds)", loop.id, session_name, idle_secs
        )
        return loop

    async def update(
        self,
        loop_id: str,
        *,
        message: str | None = None,
        idle_secs: int | None = None,
        max_cycles: int | None = None,
        active: bool | None = None,
    ) -> NudgeLoop | None:
        async with self._lock:
            trigger = self._get_row(loop_id)
            if trigger is None:
                return None
            spec = dict(trigger.spec or {})
            if message is not None:
                spec["message"] = message
            if idle_secs is not None:
                spec["idle_secs"] = max(_MIN_IDLE_SECS, min(_MAX_IDLE_SECS, int(idle_secs)))
            if max_cycles is not None:
                spec["max_cycles"] = max(0, int(max_cycles))
            trigger.spec = spec
            if active is not None:
                trigger.enabled = bool(active)
            self._store.upsert(trigger)
            # Re-arm with the new settings — the old engine cancelled and re-armed the timer
            # here; restamping the arm point is the same thing as state.
            if trigger.enabled:
                state = idle_poll.load_state(loop_id, base_dir=self._base_dir)
                state.armed_at = time.time()
                idle_poll.save_state(loop_id, state, base_dir=self._base_dir)
            loop = self._to_loop(trigger)
        self._emit("updated", loop)
        return loop

    def remove_sync(self, loop_id: str) -> None:
        trigger = self._get_row(loop_id)
        if trigger is None:
            return
        loop = self._to_loop(trigger)
        self._store.delete(loop_id)
        idle_poll.clear_state(loop_id, base_dir=self._base_dir)
        self._emit("removed", loop)

    async def remove(self, loop_id: str) -> None:
        async with self._lock:
            self.remove_sync(loop_id)

    def get_by_session(self, session_name: str) -> NudgeLoop | None:
        return self._find_by_session(session_name)

    def list_all(self) -> list[NudgeLoop]:
        return [self._to_loop(t) for t in self._rows()]

    def _find_by_session(self, session_name: str) -> NudgeLoop | None:
        for trigger in self._rows():
            if idle_poll.scope_session(trigger) == session_name:
                return self._to_loop(trigger)
        return None

    # ── Reactive arming (the backpressure contract, verbatim in meaning) ──

    def notify_turn_complete(self, session_name: str, *, errored: bool = False) -> None:
        """Called after a turn ends — (re)arm the quiet period for this session.

        Re-arms on EVERY turn (success or error) so a loop survives a failed
        turn. A persistently-broken loop is bounded by ``_MAX_CONSECUTIVE_ERRORS``
        consecutive errored turns: past the cap the loop is deactivated (kept,
        not removed, so it can be resumed) instead of spinning forever.
        ``errored`` resets to 0 on any clean turn.
        """
        loop = self._find_by_session(session_name)
        if not loop or not loop.active:
            return
        state = idle_poll.load_state(loop.id, base_dir=self._base_dir)
        if errored:
            state.error_count += 1
            if state.error_count >= _MAX_CONSECUTIVE_ERRORS:
                logger.warning(
                    "AutoNudge: loop %s hit %d consecutive errors — deactivating",
                    loop.id,
                    state.error_count,
                )
                idle_poll.save_state(loop.id, state, base_dir=self._base_dir)
                trigger = self._get_row(loop.id)
                if trigger is not None:
                    trigger.enabled = False
                    self._store.upsert(trigger)
                loop.active = False
                loop.error_count = state.error_count
                self._emit("errored_out", loop)
                return
        else:
            state.error_count = 0
        # The timer re-arm, as state: the next quiet period is measured from THIS turn's end.
        state.armed_at = time.time()
        idle_poll.save_state(loop.id, state, base_dir=self._base_dir)

    def notify_user_input(self, session_name: str) -> None:
        """Called when user sends a message — defer the pending nudge (user takes priority).

        The old engine CANCELLED a timer; a poll has no timer, so the equivalent is moving the
        arm point to now. The turn this input starts restamps again on completion, so the arm
        point still converges on turn-end.
        """
        loop = self._find_by_session(session_name)
        if not loop:
            return
        state = idle_poll.load_state(loop.id, base_dir=self._base_dir)
        state.armed_at = time.time()
        idle_poll.save_state(loop.id, state, base_dir=self._base_dir)

    # ── The fire path (called by idle_poll for due message-bearing rows) ──

    async def deliver(self, trigger: Any, *, now: float = 0.0) -> tuple[bool, str]:
        """Fire one due nudge row. Returns ``(delivered, reason_when_not)``.

        The old ``_timer``'s post-sleep body, verbatim in meaning and order: shutdown → stop
        sentinel (present → the loop is REMOVED) → cycle cap (reached → DEACTIVATED, kept) →
        ``on_fire``; state advances ONLY on actual delivery (``record_delivery``), so a skipped
        nudge (session mid-turn) neither counts toward ``max_cycles`` nor loses its quiet period.
        """
        now = now or time.time()
        loop = self._to_loop(trigger)
        if shutdown_event.is_set():
            return False, "shutting_down"
        # Kill switch: sentinel file present?
        if loop.stop_sentinel_path and Path(loop.stop_sentinel_path).exists():
            logger.info("AutoNudge: stop sentinel found for %s — removing loop", loop.id)
            await self.remove(loop.id)
            return False, "stop_sentinel"
        # Cycle cap reached?
        if loop.max_cycles and loop.cycle_count >= loop.max_cycles:
            logger.info("AutoNudge: loop %s reached max_cycles — deactivating", loop.id)
            await self.update(loop.id, active=False)
            return False, "max_cycles"
        # Fire. Update state only if the callback reports actual delivery —
        # otherwise skipped nudges (e.g. session mid-turn) inflate cycle_count and
        # prematurely trip max_cycles. Missing callback → nothing to deliver.
        if self._on_fire is None:
            return False, "no_deliverer"
        try:
            delivered = await self._on_fire(loop)
        except Exception:
            logger.exception("AutoNudge fire callback failed for %s", loop.id)
            delivered = False
        if not delivered:
            return False, idle_poll.SKIP_SESSION_BUSY
        idle_poll.record_delivery(loop.id, delivered=True, now=now, base_dir=self._base_dir)
        loop.cycle_count += 1
        loop.last_fire_ts = now
        loop.first_idle_secs = 0  # one-shot — later fires use the full idle_secs
        self._emit("fired", loop)
        return True, ""
