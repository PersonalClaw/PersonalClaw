"""Auto-nudge service — reactive same-session self-prompting loop.

Each active loop is bound to a dashboard chat session. When the session's turn
completes (``HOOK_EVENT_STOP``), we arm an idle timer. If no new user input
arrives within ``idle_secs``, we inject the configured nudge message as the
next turn into the same session.

State is persisted to ``~/.personalclaw/autonudge.json`` (fcntl-locked, atomic
write). On gateway restart, active loops are reloaded and timers re-armed.

The browser observes the loop through the normal chat stream path — nudges
appear as user-style messages tagged ``[auto-nudge cycle N]`` so they are
visually distinct from human input.

Feature-flagged via env ``PERSONALCLAW_AUTONUDGE`` (on by default; set to ``0`` to disable).
"""

import asyncio
import fcntl
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator

from personalclaw import shutdown_event
from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

_NUDGES_FILE = "autonudge.json"
_STORE_VERSION = 1
_MIN_IDLE_SECS = 15
_MAX_IDLE_SECS = 86400  # 24h
# Consecutive errored turns before a loop is deactivated (defensive cap so a
# loop can't spin forever on a hard error). Resets on any clean turn.
_MAX_CONSECUTIVE_ERRORS = 3

# Sentinel file per loop: creating it halts the loop on next cycle.
STOP_SENTINEL = "STOP"


def enabled() -> bool:
    """Feature flag — on by default. Set ``PERSONALCLAW_AUTONUDGE=0`` to disable."""
    return os.environ.get("PERSONALCLAW_AUTONUDGE", "1").lower() not in ("0", "false", "no")


# Module-level singleton so hooks in chat.py / messaging.py can notify the
# service without needing a reference to the gateway. Set by AutoNudgeService
# on start(); cleared on stop().
_INSTANCE: "AutoNudgeService | None" = None


def get_instance() -> "AutoNudgeService | None":
    return _INSTANCE


@dataclass
class NudgeLoop:
    """A single auto-nudge loop bound to one session."""

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
    # One-shot short delay for the FIRST fire so a freshly-armed loop (a brand-new
    # worker session with no prior turn) starts promptly instead of sitting idle
    # for the full ``idle_secs``. 0 = disabled (the first fire waits ``idle_secs``,
    # the original behavior). Cleared to 0 after the first fire.
    first_idle_secs: int = 0


@contextmanager
def _locked_file(path: Path, mode: str) -> Iterator[Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if "r" in mode and not path.exists():
        path.write_text(json.dumps({"version": _STORE_VERSION, "loops": []}))
    with open(path, mode, encoding="utf-8") as fh:
        lock_mode = fcntl.LOCK_EX if "w" in mode or "+" in mode else fcntl.LOCK_SH
        fcntl.flock(fh.fileno(), lock_mode)
        try:
            yield fh
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class AutoNudgeService:
    """Manages reactive per-session nudge loops with restart-survival."""

    def __init__(
        self,
        base_dir: Path | None = None,
        on_fire: Callable[[NudgeLoop], Awaitable[bool]] | None = None,
    ) -> None:
        self._base_dir = base_dir or config_dir()
        self._path = self._base_dir / _NUDGES_FILE
        self._on_fire = on_fire
        self._loops: dict[str, NudgeLoop] = {}
        self._timers: dict[str, asyncio.Task] = {}
        self._observers: list[Callable[[str, NudgeLoop | None], None]] = []
        self._lock = asyncio.Lock()

    # ── Persistence ──

    def _load(self) -> None:
        with _locked_file(self._path, "r") as fh:
            data = json.load(fh)
        for raw in data.get("loops", []):
            try:
                loop = NudgeLoop(**{k: raw[k] for k in raw if k in NudgeLoop.__dataclass_fields__})
            except Exception:
                logger.warning("AutoNudge: skipping malformed loop entry: %r", raw, exc_info=True)
                continue
            self._loops[loop.id] = loop
        logger.info("AutoNudge: loaded %d loops", len(self._loops))

    def _save(self) -> None:
        # Atomic write so readers always see either the old or new complete
        # file, never a partial one (avoids the truncate-before-flock race of
        # a plain open(path, "w")).
        payload = json.dumps(
            {
                "version": _STORE_VERSION,
                "loops": [asdict(lp) for lp in self._loops.values()],
            },
            indent=2,
        )
        atomic_write(self._path, payload, fsync=True)

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
        self._load()
        # Re-arm timers for active loops on startup.
        for loop in self._loops.values():
            if loop.active:
                self._arm_timer(loop)
        global _INSTANCE
        _INSTANCE = self
        logger.info("AutoNudge started")

    def stop(self) -> None:
        for t in self._timers.values():
            t.cancel()
        self._timers.clear()
        global _INSTANCE
        if _INSTANCE is self:
            _INSTANCE = None

    # ── Loop CRUD ──

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
        first_idle_secs = min(idle_secs, max(_MIN_IDLE_SECS, int(first_idle_secs))) if first_idle_secs else 0
        async with self._lock:
            # One loop per session — replace any existing loop on this session.
            existing = self._find_by_session(session_name)
            if existing:
                self.remove_sync(existing.id)
            loop = NudgeLoop(
                id=uuid.uuid4().hex[:8],
                session_name=session_name,
                message=message,
                idle_secs=idle_secs,
                max_cycles=max(0, int(max_cycles)),
                created_ts=time.time(),
                stop_sentinel_path=stop_sentinel_path,
                first_idle_secs=first_idle_secs,
            )
            self._loops[loop.id] = loop
            self._save()
            self._arm_timer(loop)
        self._emit("added", loop)
        logger.info("AutoNudge: added loop %s on session %s (idle=%ds)", loop.id, session_name, idle_secs)
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
            loop = self._loops.get(loop_id)
            if not loop:
                return None
            if message is not None:
                loop.message = message
            if idle_secs is not None:
                loop.idle_secs = max(_MIN_IDLE_SECS, min(_MAX_IDLE_SECS, int(idle_secs)))
            if max_cycles is not None:
                loop.max_cycles = max(0, int(max_cycles))
            if active is not None:
                loop.active = bool(active)
            self._save()
            # Re-arm timer with new settings.
            self._cancel_timer(loop_id)
            if loop.active:
                self._arm_timer(loop)
        self._emit("updated", loop)
        return loop

    def remove_sync(self, loop_id: str) -> None:
        loop = self._loops.pop(loop_id, None)
        if loop is None:
            return
        self._cancel_timer(loop_id)
        self._save()
        self._emit("removed", loop)

    async def remove(self, loop_id: str) -> None:
        async with self._lock:
            self.remove_sync(loop_id)

    def get_by_session(self, session_name: str) -> NudgeLoop | None:
        return self._find_by_session(session_name)

    def list_all(self) -> list[NudgeLoop]:
        return list(self._loops.values())

    def _find_by_session(self, session_name: str) -> NudgeLoop | None:
        for lp in self._loops.values():
            if lp.session_name == session_name:
                return lp
        return None

    # ── Reactive arming ──

    def notify_turn_complete(self, session_name: str, *, errored: bool = False) -> None:
        """Called after a turn ends — (re)arm the idle timer for this session.

        Re-arms on EVERY turn (success or error) so a loop survives a failed
        turn. A persistently-broken loop is bounded by ``_MAX_CONSECUTIVE_ERRORS``
        consecutive errored turns: past the cap the loop is deactivated (kept,
        not removed, so it can be resumed) instead of spinning forever.
        ``errored`` resets to 0 on any clean turn.
        """
        loop = self._find_by_session(session_name)
        if not loop or not loop.active:
            return
        if errored:
            loop.error_count += 1
            if loop.error_count >= _MAX_CONSECUTIVE_ERRORS:
                logger.warning(
                    "AutoNudge: loop %s hit %d consecutive errors — deactivating",
                    loop.id, loop.error_count,
                )
                loop.active = False
                self._cancel_timer(loop.id)
                self._save()
                self._emit("errored_out", loop)
                return
        else:
            loop.error_count = 0
        self._arm_timer(loop)

    def notify_user_input(self, session_name: str) -> None:
        """Called when user sends a message — cancel pending nudge (user takes priority)."""
        loop = self._find_by_session(session_name)
        if not loop:
            return
        self._cancel_timer(loop.id)

    def _cancel_timer(self, loop_id: str) -> None:
        t = self._timers.pop(loop_id, None)
        if t and not t.done():
            t.cancel()

    def _arm_timer(self, loop: NudgeLoop) -> None:
        self._cancel_timer(loop.id)
        self._timers[loop.id] = asyncio.create_task(self._timer(loop))

    async def _timer(self, loop: NudgeLoop) -> None:
        # The first armed fire may use a shorter delay (first_idle_secs) so a brand-
        # new loop starts promptly; every later fire waits the full idle_secs.
        delay = loop.first_idle_secs if (loop.first_idle_secs and loop.cycle_count == 0) else loop.idle_secs
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if shutdown_event.is_set():
            return
        # Kill switch: sentinel file present?
        if loop.stop_sentinel_path and Path(loop.stop_sentinel_path).exists():
            logger.info("AutoNudge: stop sentinel found for %s — removing loop", loop.id)
            await self.remove(loop.id)
            return
        # Cycle cap reached?
        if loop.max_cycles and loop.cycle_count >= loop.max_cycles:
            logger.info("AutoNudge: loop %s reached max_cycles — deactivating", loop.id)
            await self.update(loop.id, active=False)
            return
        # Fire. Update state only if the callback reports actual delivery —
        # otherwise skipped nudges (e.g. session mid-turn) inflate cycle_count and
        # prematurely trip max_cycles. Missing callback → nothing to deliver.
        if self._on_fire is None:
            return
        try:
            delivered = await self._on_fire(loop)
        except Exception:
            logger.exception("AutoNudge fire callback failed for %s", loop.id)
            delivered = False
        if not delivered:
            return
        loop.cycle_count += 1
        loop.last_fire_ts = time.time()
        loop.first_idle_secs = 0  # one-shot — later fires use the full idle_secs
        self._save()
        self._emit("fired", loop)
