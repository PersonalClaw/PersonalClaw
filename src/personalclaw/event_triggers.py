"""Data-event triggers (#38) — the event-pattern layer of the triggers facade.

The third trigger kind alongside ``schedule`` (clock) and ``lifecycle`` (agent-loop
event): an **event** trigger fires when PClaw's own state changes. v1 exposes the
cheapest source — memory writes (``vector_memory._log_event``):

- **MemoryUpdate**     — any memory write (create/update/delete).
- **MemoryKeyPattern** — a write whose key matches a glob (``project.acme.*``).
- **ContentMatch**     — a write whose value matches a regex/substring.

Each spec carries an action (reusing the action-provider registry) + an optional
``max_fires`` so a trigger auto-disables once exhausted ("alert me the NEXT time X").
A per-spec debounce + a global rate cap guard against trigger storms.

This is deliberately a small, decoupled engine: ``vector_memory`` calls
``emit_memory_event`` best-effort (never blocking a write), and the registry persists
specs as JSON like crons. Folds into ``triggers-unification`` as its event layer.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

# Event-pattern kinds.
MEMORY_UPDATE = "MemoryUpdate"
MEMORY_KEY_PATTERN = "MemoryKeyPattern"
CONTENT_MATCH = "ContentMatch"
EVENT_PATTERNS = (MEMORY_UPDATE, MEMORY_KEY_PATTERN, CONTENT_MATCH)

# Global rate cap: at most this many event-trigger fires per window (storm guard).
_RATE_WINDOW_SECS = 60.0
_RATE_MAX_FIRES = 30
_DEFAULT_DEBOUNCE_SECS = 5.0


@dataclass
class EventTrigger:
    """One data-event trigger spec."""

    id: str
    pattern: str  # one of EVENT_PATTERNS
    action_provider: str = "notify"  # action-provider name
    action_config: dict = field(default_factory=dict)
    key_glob: str = ""  # for MemoryKeyPattern
    content_re: str = ""  # for ContentMatch
    enabled: bool = True
    max_fires: int = 0  # 0 = unlimited
    fire_count: int = 0
    debounce_secs: float = _DEFAULT_DEBOUNCE_SECS
    last_fired_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "pattern": self.pattern,
            "action_provider": self.action_provider,
            "action_config": self.action_config,
            "key_glob": self.key_glob,
            "content_re": self.content_re,
            "enabled": self.enabled,
            "max_fires": self.max_fires,
            "fire_count": self.fire_count,
            "debounce_secs": self.debounce_secs,
            "last_fired_at": self.last_fired_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EventTrigger":
        return cls(
            id=str(d.get("id", "")),
            pattern=str(d.get("pattern", MEMORY_UPDATE)),
            action_provider=str(d.get("action_provider", "notify")),
            action_config=dict(d.get("action_config") or {}),
            key_glob=str(d.get("key_glob", "")),
            content_re=str(d.get("content_re", "")),
            enabled=bool(d.get("enabled", True)),
            max_fires=int(d.get("max_fires", 0) or 0),
            fire_count=int(d.get("fire_count", 0) or 0),
            debounce_secs=float(d.get("debounce_secs", _DEFAULT_DEBOUNCE_SECS) or 0.0),
            last_fired_at=float(d.get("last_fired_at", 0.0) or 0.0),
        )


def matches(trigger: EventTrigger, *, event_type: str, key: str, value: str) -> bool:
    """Pure: does *trigger* match this memory event?"""
    if not trigger.enabled:
        return False
    if trigger.max_fires and trigger.fire_count >= trigger.max_fires:
        return False
    if trigger.pattern == MEMORY_UPDATE:
        return True
    if trigger.pattern == MEMORY_KEY_PATTERN:
        return bool(trigger.key_glob) and fnmatch.fnmatch(key or "", trigger.key_glob)
    if trigger.pattern == CONTENT_MATCH:
        if not trigger.content_re:
            return False
        try:
            return re.search(trigger.content_re, value or "") is not None
        except re.error:
            return trigger.content_re in (value or "")
    return False


class EventTriggerStore:
    """Per-home persisted event triggers (``<config_dir>/event_triggers.json``)."""

    def __init__(self, path: Path):
        self._path = path

    def load(self) -> list[EventTrigger]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [EventTrigger.from_dict(d) for d in raw if isinstance(d, dict) and d.get("id")]

    def save(self, triggers: list[EventTrigger]) -> None:
        atomic_write(self._path, json.dumps([t.to_dict() for t in triggers], indent=2))

    def upsert(self, t: EventTrigger) -> None:
        items = [x for x in self.load() if x.id != t.id]
        items.append(t)
        self.save(items)

    def delete(self, trigger_id: str) -> bool:
        items = self.load()
        kept = [x for x in items if x.id != trigger_id]
        if len(kept) == len(items):
            return False
        self.save(kept)
        return True

    def record_fire(self, trigger_id: str, *, now: float) -> None:
        """Bump fire_count + last_fired_at; auto-disable when max_fires reached."""
        items = self.load()
        for t in items:
            if t.id == trigger_id:
                t.fire_count += 1
                t.last_fired_at = now
                if t.max_fires and t.fire_count >= t.max_fires:
                    t.enabled = False  # exhausted → self-retire
                break
        self.save(items)


# ── runtime engine (module-level singleton; subscribed by vector_memory) ──

_engine: "EventTriggerEngine | None" = None


def get_engine() -> "EventTriggerEngine":
    global _engine
    if _engine is None:
        _engine = EventTriggerEngine()
    return _engine


class EventTriggerEngine:
    """Matches memory events against stored triggers + fires their actions.

    Memory writes call :meth:`on_memory_event` (best-effort, never blocking). A
    match schedules the action on the event loop; debounce + a global rate cap
    prevent storms. Actions reuse the action-provider registry."""

    def __init__(self, store: EventTriggerStore | None = None):
        self._store = store
        self._fire_times: list[float] = []  # for the global rate cap

    def _get_store(self) -> EventTriggerStore:
        if self._store is None:
            from personalclaw.config.loader import config_dir

            self._store = EventTriggerStore(config_dir() / "event_triggers.json")
        return self._store

    def on_memory_event(self, *, event_type: str, key: str, value: str, now: float) -> None:
        """Notified by vector_memory on a write. Fires matching triggers. Never raises."""
        try:
            triggers = self._get_store().load()
        except Exception:
            return
        if not triggers:
            return
        for t in triggers:
            if not matches(t, event_type=event_type, key=key, value=value):
                continue
            # Debounce only a trigger that has actually fired before (last_fired_at>0).
            if t.debounce_secs and t.last_fired_at and (now - t.last_fired_at) < t.debounce_secs:
                continue
            if not self._rate_ok(now):
                logger.warning("event-trigger rate cap hit — dropping fire for %s", t.id)
                break
            self._fire_times.append(now)
            self._schedule_fire(t, event_type=event_type, key=key, value=value, now=now)

    def _rate_ok(self, now: float) -> bool:
        self._fire_times = [ts for ts in self._fire_times if now - ts < _RATE_WINDOW_SECS]
        return len(self._fire_times) < _RATE_MAX_FIRES

    def _schedule_fire(
        self, t: EventTrigger, *, event_type: str, key: str, value: str, now: float
    ) -> None:
        # Record the fire synchronously (auto-disable is immediate); dispatch async.
        try:
            self._get_store().record_fire(t.id, now=now)
        except Exception:
            logger.debug("event-trigger record_fire failed", exc_info=True)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (e.g. a sync CLI write) — the fire is recorded, action skipped
        loop.create_task(self._fire(t, event_type=event_type, key=key, value=value))

    async def _fire(self, t: EventTrigger, *, event_type: str, key: str, value: str) -> None:
        try:
            from personalclaw.action_providers import ActionContext, get_action_provider

            provider = get_action_provider(t.action_provider)
            if provider is None:
                return
            payload = {
                "event_type": event_type,
                "key": key,
                "value": value[:2000],
                "trigger_id": t.id,
            }
            ctx = ActionContext(
                event=f"memory.{event_type}", context=f"{key}: {value[:200]}", payload=payload
            )
            await provider.execute(t.action_config, ctx)
        except Exception:
            logger.debug("event-trigger action failed for %s", t.id, exc_info=True)


def emit_memory_event(*, event_type: str, key: str, value: str | None, now: float) -> None:
    """The seam vector_memory calls after logging a memory event. Best-effort."""
    try:
        get_engine().on_memory_event(event_type=event_type, key=key, value=value or "", now=now)
    except Exception:
        logger.debug("emit_memory_event failed", exc_info=True)
