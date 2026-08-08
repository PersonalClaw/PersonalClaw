"""SourceEngine — the one poll loop over WatchedSources (WATCHED-SOURCES §1.2).

The generalized form of the loop ``triggers/web_poll.py`` prefigured for one kind: a
SINGLE re-armed asyncio task (never one task per source) that wakes, polls every source
whose interval has elapsed, and sleeps until the next is due (capped, like the trigger
scheduler, so an external edit is picked up within one poll). It enrolls the poll-capable
providers registered in ``knowledge_providers.registry`` — a provider is poll-capable when
it subclasses :class:`~personalclaw.knowledge_providers.base.KnowledgeSourceProvider` (the
``poll`` contract, §1.1) — and drives ``provider.poll(source_id, cursor)`` per due source.

Crash-safety is the whole design (SC#4). Per new item the engine calls the source-aware
:meth:`~personalclaw.knowledge.store.KnowledgeStore.create_typed_item`, which folds the
``source_seen`` novelty-gate INSERT into the item's own transaction, then enqueues it on
the ONE ingestion path (``ingest_queue.enqueue``). Only after every item is committed does
it advance the cursor (:meth:`~personalclaw.knowledge.store.KnowledgeStore.record_poll`).
So a crash between item-persist and cursor-persist re-yields the same items on the next
poll, and the UNIQUE ``(source_id, guid)`` gate drops them — at-least-once poll, exactly-
once persist. :meth:`recover_pending` on startup re-enqueues any item whose ingestion did
not finish (via the ingest queue's own recovery), so no written-but-unprocessed item is
stranded by a restart.

The engine NEVER fetches web content itself — it drives PROVIDERS, whose fetches route
through ``net.fetch`` under the ``SOURCE`` egress profile (WS-3+). Re-implementing a fetch
here would bypass host classification, private-IP denial and the redirect-hop re-check.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

#: Ceiling on one loop iteration's sleep. Mirrors the trigger scheduler's POLL_CEILING: a
#: source added or re-enabled out of band (an MCP tool in another process editing the
#: store) must be picked up within one poll rather than waiting out a multi-hour interval.
POLL_CEILING_SECS = 30.0


def _default_providers() -> list[Any]:
    """The process-wide knowledge provider registry (prod path). Injected in tests so a
    fixture provider is enrolled + driven without touching the real registry."""
    from personalclaw.knowledge_providers.registry import list_providers

    return list_providers()


class SourceEngine:
    """One re-armed asyncio poll loop over the WatchedSource store (§1.2).

    ``store`` owns the tables + the atomic write/seen/cursor primitives; ``ingest_queue``
    is the single ingestion path new items enqueue onto. ``providers_lister`` yields the
    registered knowledge providers (defaults to the knowledge registry; injected in tests)
    — enrollment AND per-source resolution both read this ONE seam, so a source is driven
    by exactly the provider that was enrolled. ``now_fn`` is a seam so a test drives an
    exact instant."""

    def __init__(
        self,
        store: Any,
        ingest_queue: Any,
        *,
        providers_lister: Callable[[], list[Any]] | None = None,
        config_loader: Callable[[], Any] | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        self._queue = ingest_queue
        self._providers_lister = providers_lister or _default_providers
        self._config_loader = config_loader or self._load_config
        import time

        self._now_fn = now_fn or time.time
        import asyncio

        self._task: asyncio.Task | None = None

    def _provider_for(self, name: str) -> Any:
        for prov in self._providers_lister():
            if getattr(prov, "name", None) == name:
                return prov
        return None

    @staticmethod
    def _load_config() -> Any:
        from personalclaw.config.loader import AppConfig

        return AppConfig.load().sources

    # ── enrollment ─────────────────────────────────────────────────────────────────

    def _is_poll_capable(self, provider: Any) -> bool:
        """A provider is enrolled iff it implements the poll contract (§1.1). Duck-typed
        on the base class so an app subclass and the core fixture both qualify without a
        registration flag the provider could forget to set."""
        from personalclaw.knowledge_providers.base import KnowledgeSourceProvider

        return isinstance(provider, KnowledgeSourceProvider)

    def egress_policy(self) -> Any:
        """The egress posture a source poll's fetches must use (WATCHED-SOURCES §11).

        The ``SOURCE`` profile with the operator's ``security.egress`` config layered via
        ``egress_policy_for`` (a self-hoster's LAN allow-list, deny-list, private-network
        opt-in). Resolved HERE so the engine owns the policy and a provider never picks its
        own — a provider re-implementing the fetch (and its guard) is the exact bypass the
        boundary exists to prevent. Handed to a provider's :meth:`poll` when its signature
        accepts a ``policy`` (the web/feed fetching providers land in WS-3+); a plain corpus
        poll ignores it."""
        from personalclaw.net.policy import SOURCE, egress_policy_for

        return egress_policy_for(SOURCE)

    def enrolled_provider_names(self) -> set[str]:
        """Names of registered providers the engine will drive — the poll-capable subset
        of the registry (read through the SAME seam as resolution). A source whose provider
        is not here is skipped (its provider is a plain corpus provider, or disabled)."""
        return {
            p.name
            for p in self._providers_lister()
            if self._is_poll_capable(p) and getattr(p, "name", None)
        }

    # ── scheduling ─────────────────────────────────────────────────────────────────

    def _interval_for(self, source: dict, cfg: Any) -> float:
        """A source's effective poll interval: its own value (or the config default when
        unset), clamped UP to the network floor. The floor is the R1-class rate discipline
        web_poll enforces — a too-frequent poll is abusive to someone else's server."""
        want = int(source.get("poll_interval_secs") or 0) or int(cfg.poll_interval_default_secs)
        return float(max(want, int(cfg.network_floor_secs)))

    def _due_delay(self, source: dict, cfg: Any, now: float) -> float:
        """Seconds until this source is next due (<=0 means due now). Never-polled sources
        are due immediately; the interval is measured from the last poll's completion."""
        last = source.get("last_poll_at")
        if not last:
            return 0.0
        from datetime import datetime

        try:
            last_ts = datetime.fromisoformat(last).timestamp()
        except (TypeError, ValueError):
            return 0.0
        return (last_ts + self._interval_for(source, cfg)) - now

    # ── one poll ───────────────────────────────────────────────────────────────────

    async def poll_source(self, source: dict, cfg: Any) -> int:
        """Poll one source once; return the count of NEW items written. Never raises — a
        provider fault becomes a degraded health status, not a dead loop (§1.1)."""
        sid = source["id"]
        provider = self._provider_for(source["provider"])
        if provider is None or not self._is_poll_capable(provider):
            self._store.record_poll(
                sid,
                cursor=self._store.get_source_cursor(sid),
                new_count=0,
                health_status="error",
                error_summary=f"provider {source['provider']!r} not enrolled (poll-capable)",
            )
            return 0
        cursor = self._store.get_source_cursor(sid)
        try:
            # Hand the SOURCE egress policy to a provider whose poll() accepts one (the
            # WS-3+ fetching providers), so its net.fetch runs under the engine-owned
            # posture; the base corpus contract (source_id, cursor) is called as-is.
            import inspect

            if "policy" in inspect.signature(provider.poll).parameters:
                result = await provider.poll(sid, cursor, policy=self.egress_policy())
            else:
                result = await provider.poll(sid, cursor)
        except Exception as exc:  # noqa: BLE001 — a provider that raises must not kill the loop
            logger.warning("source %s poll raised", sid, exc_info=True)
            self._store.record_poll(
                sid,
                cursor=cursor,
                new_count=0,
                health_status="error",
                error_summary=str(exc)[:200],
            )
            return 0
        if result.error:
            # A soft failure the provider chose to report: keep the cursor (retry from the
            # same position), surface the reason, do not treat the source as dead.
            self._store.record_poll(
                sid,
                cursor=result.cursor or cursor,
                new_count=0,
                health_status="degraded",
                error_summary=result.error[:200],
            )
            return 0
        max_items = int(cfg.max_items_per_poll)
        item_type = source.get("item_type") or "bookmark"
        new_count = 0
        for item in result.items[:max_items]:
            # create_typed_item writes the seen-row + item atomically and returns None when
            # the guid was already seen — the novelty gate. Only a genuinely-new item (an
            # id) is enqueued, so a page that changes every render cannot storm the queue.
            item_id = self._store.create_typed_item(
                item_type=item_type,
                title=item.title or item.url or item.guid,
                content=item.content,
                url=item.url,
                provider=source["provider"],
                source_id=sid,
                guid=item.guid,
                extra={"processing_status": "queued"},
            )
            if item_id is None:
                continue
            new_count += 1
            try:
                self._queue.enqueue(item_id)
            except Exception:  # noqa: BLE001 — a queue hiccup must not lose the written item
                logger.debug("source item enqueue failed for %s", item_id, exc_info=True)
        # Cursor advanced LAST, in its own txn: every item above is already durable, so a
        # crash here re-yields them next poll and the UNIQUE gate drops them (SC#4).
        from datetime import datetime

        next_at = datetime.fromtimestamp(
            self._now_fn() + self._interval_for(source, cfg)
        ).isoformat()
        self._store.record_poll(
            sid,
            cursor=result.cursor or cursor,
            new_count=new_count,
            health_status="ok",
            next_poll_at=next_at,
        )
        return new_count

    async def tick(self) -> float:
        """One loop iteration: poll every due source, return seconds to sleep until the
        next is due (capped at :data:`POLL_CEILING_SECS`). Separate from the loop so a test
        (and a future ``sources doctor``) can drive exactly one pass deterministically."""
        cfg = self._config_loader()
        if not getattr(cfg, "enabled", True):
            return POLL_CEILING_SECS
        now = self._now_fn()
        sources = self._store.list_sources(enabled_only=True)
        enrolled = self.enrolled_provider_names()
        # Cap the enrolled working set so a runaway config cannot arm thousands of polls.
        active = [s for s in sources if s["provider"] in enrolled][: int(cfg.max_sources)]
        next_delay = POLL_CEILING_SECS
        for source in active:
            delay = self._due_delay(source, cfg, now)
            if delay <= 0:
                await self.poll_source(source, cfg)
                # Re-derive this source's next delay from the interval just recorded.
                delay = self._interval_for(source, cfg)
            next_delay = min(next_delay, delay)
        return max(0.0, min(next_delay, POLL_CEILING_SECS))

    # ── crash recovery + lifecycle ──────────────────────────────────────────────────

    def recover_pending(self) -> int:
        """Resume cleanly after a restart (SC#4). A source item written before a crash may
        not have finished ingesting; the ingest queue's own recovery re-enqueues every item
        left in ``queued``/``processing`` (source items included). The seen-set already
        makes any re-poll idempotent, so this is the only recovery the engine owes: no
        written-but-unprocessed item is stranded. Returns the count re-enqueued."""
        try:
            return int(self._queue.recover_pending())
        except Exception:  # noqa: BLE001 — recovery is best-effort; the loop still runs
            logger.debug("source engine recovery failed", exc_info=True)
            return 0

    async def run_forever(self, sleep_fn: Callable[[float], Awaitable[None]] | None = None) -> None:
        """Drive the poll loop until shutdown. Cancellation propagates so the gateway can
        stop it; any other tick fault is logged and the loop continues — a loop that died
        on one bad poll would silently retire every watched source (the failure a scheduler
        can least afford)."""
        import asyncio

        from personalclaw import shutdown_event

        while not shutdown_event.is_set():
            delay = POLL_CEILING_SECS
            try:
                delay = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — the loop must outlive any single tick failure
                logger.warning("source engine tick failed; continuing", exc_info=True)
            if sleep_fn is not None:
                await sleep_fn(delay)
                continue
            # Sleep on the shutdown event so a stop is immediate rather than waiting out the
            # interval; a timeout is a normal wake to poll again.
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=max(0.5, delay))
                return  # shutdown signalled
            except asyncio.TimeoutError:
                continue

    def start(self) -> None:
        """Launch the single loop task and run crash recovery once (idempotent)."""
        import asyncio

        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_forever())
            self.recover_pending()
            logger.info("Source engine started")

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
