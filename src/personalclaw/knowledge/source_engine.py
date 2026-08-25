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
        event_spool: Any | None = None,
    ) -> None:
        self._store = store
        self._queue = ingest_queue
        self._providers_lister = providers_lister or _default_providers
        self._config_loader = config_loader or self._load_config
        # Built lazily (see `_spool`) rather than here: a test that sets
        # PERSONALCLAW_HOME after constructing the engine must still get the isolated path,
        # and resolving config_dir in __init__ would have frozen the real home.
        self._event_spool = event_spool
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

    # ── stream events (§6.1) ───────────────────────────────────────────────────────

    @property
    def _spool(self) -> Any:
        """The interim JSONL spool the stream events land on (§6.1).

        Interim by the plan's own dependency note — AUTOMATION-SUBSTRATE's event bus does not
        exist yet, and the note explicitly sanctions spooling until it does. The engine holds
        exactly one emit seam so the bus replaces one object, not N call sites."""
        if self._event_spool is None:
            from personalclaw.knowledge.source_streams import SourceEventSpool

            self._event_spool = SourceEventSpool()
        return self._event_spool

    def _emit_ingested(self, source: dict, item: Any, item_id: str, change: str) -> None:
        """``SourceItemIngested`` for one (re-)indexed item (§6.1).

        Emitted HERE — inside the persist path, after the item is durable and enqueued —
        rather than from a batch at the end of the poll, because only this frame knows the
        ``item_id`` the store minted, and an event announcing an item the store rejected (the
        novelty gate returned None) would be a phantom no consumer could resolve.

        The title rides FENCED (``fenced_snippet``): a digest reads these records and hands
        them to a model, so the fence goes on at write time, not at every future read."""
        from personalclaw.knowledge.source_streams import SOURCE_ITEM_INGESTED, fenced_snippet

        sid = source["id"]
        self._spool.emit(
            SOURCE_ITEM_INGESTED,
            {
                "source_id": sid,
                "item_id": item_id,
                "guid": item.guid,
                "title": fenced_snippet(getattr(item, "title", "") or "", sid),
                "url": getattr(item, "url", "") or "",
                "change": change,
            },
        )

    def _emit_poll_completed(
        self,
        source_id: str,
        *,
        new_count: int,
        escalations: list[str],
        budget_spent: int = 0,
    ) -> None:
        """``SourcePollCompleted`` for one poll (§6.1).

        Emitted on EVERY exit of :meth:`poll_source`, not just the successful one — the same
        reasoning that made ``next_poll_at`` unconditional. A poll event that appears only on
        success makes a source that stopped producing indistinguishable from one that is
        producing nothing, which is the single question a stream consumer asks.

        ``budget_spent`` reads a provider-declared ``requests_used`` when the poll result
        carries one. 🔴 MEASURED (2026-08-24): ``SourcePollResult`` has no such field today —
        only ``SourcePreview`` does (``knowledge_providers/base.py:163``), while
        ``web_source.poll`` builds a ``_Budget`` counter it never returns
        (``web_source.py:1314``). So this reads 0 for every shipped provider until that field
        is added; it is duck-typed rather than hardcoded 0 so adding it is a one-line change
        on the provider side, and NOT invented here because fabricating a request count would
        make the audit surface lie."""
        from personalclaw.knowledge.source_streams import SOURCE_POLL_COMPLETED

        self._spool.emit(
            SOURCE_POLL_COMPLETED,
            {
                "source_id": source_id,
                "new_count": int(new_count),
                "escalations": list(escalations),
                "budget_spent": int(budget_spent),
            },
        )

    # ── enrollment ─────────────────────────────────────────────────────────────────

    def _is_poll_capable(self, provider: Any) -> bool:
        """A provider is enrolled iff it implements the poll contract (§1.1). Duck-typed
        on the base class so an app subclass and the core fixture both qualify without a
        registration flag the provider could forget to set."""
        from personalclaw.knowledge_providers.base import KnowledgeSourceProvider

        return isinstance(provider, KnowledgeSourceProvider)

    @staticmethod
    def egress_policy() -> Any:
        """The egress posture a source poll's fetches must use (WATCHED-SOURCES §11).

        The ``SOURCE`` profile with the operator's ``security.egress`` config layered via
        ``egress_policy_for`` (a self-hoster's LAN allow-list, deny-list, private-network
        opt-in). Resolved HERE so the engine owns the policy and a provider never picks its
        own — a provider re-implementing the fetch (and its guard) is the exact bypass the
        boundary exists to prevent. Handed to a provider's :meth:`poll` when its signature
        accepts a ``policy`` (the web/feed fetching providers land in WS-3+); a plain corpus
        poll ignores it.

        A ``staticmethod`` because the create flow's PREVIEW (WS-9) is a real fetch on the
        same targets and must run under the same posture, and it happens in an HTTP handler
        with no engine instance in reach. Two callers resolving the profile independently
        would be two egress postures for one act."""
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

    def _next_poll_at(self, source: dict, cfg: Any) -> str:
        """When this source is due again, as an ISO timestamp — a DISPLAY rollup only.

        Scheduling reads ``last_poll_at`` (see :meth:`_due_delay`), so this never decides
        anything; it exists so a reader of the row can say "retrying in 9 minutes" instead of
        leaving a failing source looking abandoned."""
        from datetime import datetime

        return datetime.fromtimestamp(self._now_fn() + self._interval_for(source, cfg)).isoformat()

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
        """Poll one source once; return how many items were (re-)indexed this pass — new
        items plus re-enqueued edits, excluding archives (:meth:`_persist`). Never raises —
        a provider fault becomes a degraded health status, not a dead loop (§1.1)."""
        from personalclaw.knowledge_providers.base import (
            HEALTH_DEGRADED,
            HEALTH_ERROR,
            HEALTH_OK,
        )

        sid = source["id"]
        # Recorded on EVERY exit below, not just the successful one. Scheduling does not depend
        # on it (`_due_delay` measures from `last_poll_at`), so this is purely the rollup that
        # tells a reader when the source will be tried again — and a failing source is exactly
        # the one whose reader needs to know that a retry is coming. Withholding it on failure
        # is the same shape WS-3 fixed for `last_escalations`: a rollup visible only on success
        # makes the interesting case the invisible one.
        next_at = self._next_poll_at(source, cfg)
        provider = self._provider_for(source["provider"])
        if provider is None or not self._is_poll_capable(provider):
            self._store.record_poll(
                sid,
                cursor=self._store.get_source_cursor(sid),
                new_count=0,
                health_status=HEALTH_ERROR,
                error_summary=f"provider {source['provider']!r} not enrolled (poll-capable)",
                next_poll_at=next_at,
            )
            self._emit_poll_completed(sid, new_count=0, escalations=[])
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
                health_status=HEALTH_ERROR,
                error_summary=str(exc)[:200],
                next_poll_at=next_at,
            )
            self._emit_poll_completed(sid, new_count=0, escalations=[])
            return 0
        escalations = list(getattr(result, "escalations", None) or [])
        if result.error:
            # A soft failure the provider chose to report: keep the cursor (retry from the
            # same position), surface the reason, do not treat the source as dead. A provider
            # that KNOWS why it failed declares its own health status (a page needing the
            # render tier, §2.3); flattening that into `degraded` would hide the one
            # remediation the user could act on.
            self._store.record_poll(
                sid,
                cursor=result.cursor or cursor,
                new_count=0,
                health_status=getattr(result, "health_status", "") or HEALTH_DEGRADED,
                error_summary=result.error[:200],
                escalations=escalations,
                next_poll_at=next_at,
            )
            self._emit_poll_completed(
                sid,
                new_count=0,
                escalations=escalations,
                budget_spent=int(getattr(result, "requests_used", 0) or 0),
            )
            return 0
        max_items = int(cfg.max_items_per_poll)
        new_count = 0
        for item in result.items[:max_items]:
            try:
                new_count += self._persist(source, item)
            except Exception:  # noqa: BLE001 — one bad item must not abandon the rest
                logger.warning("source %s item %r persist failed", sid, item.guid, exc_info=True)
        # Cursor advanced LAST, in its own txn: every item above is already durable, so a
        # crash here re-yields them next poll and the UNIQUE gate drops them (SC#4).
        self._store.record_poll(
            sid,
            cursor=result.cursor or cursor,
            new_count=new_count,
            health_status=HEALTH_OK,
            next_poll_at=next_at,
            escalations=escalations,
        )
        # After the cursor, so a consumer that sees `SourcePollCompleted` knows the poll is
        # fully durable — the event is the poll's commit marker, not a progress ping.
        self._emit_poll_completed(
            sid,
            new_count=new_count,
            escalations=escalations,
            budget_spent=int(getattr(result, "requests_used", 0) or 0),
        )
        return new_count

    # ── persisting one sighting (WS-5: created / modified / deleted) ────────────────

    def _persist(self, source: dict, item: Any) -> int:
        """Persist ONE sighting; return 1 if it was (re-)indexed, else 0.

        The engine — not the provider — owns what a change KIND means, because the
        dangerous direction lives here: a provider that could decide "deleted" means
        "remove the row" would destroy the user's only remaining copy of a file it no
        longer sees. The three outcomes are deliberately asymmetric:

        * ``created`` → a NEW item through the novelty gate (dedup returns None).
        * ``modified`` → the SAME item updated + re-enqueued (never a second row).
        * ``deleted`` → the item ARCHIVED with ``source_deleted_at``, never deleted, and
          never enqueued (a vanished file has nothing to re-index).

        The kind is matched EXPLICITLY against the closed vocabulary; an unknown value is
        refused rather than falling through to a create, so a future kind cannot be
        silently mis-persisted as an ingestion.
        """
        from personalclaw.knowledge_providers.base import (
            CHANGE_CREATED,
            CHANGE_DELETED,
            CHANGE_MODIFIED,
        )

        change = getattr(item, "change", CHANGE_CREATED) or CHANGE_CREATED
        if change == CHANGE_DELETED:
            return self._archive_deleted(source, item)
        if change == CHANGE_MODIFIED:
            return self._reindex_modified(source, item)
        if change != CHANGE_CREATED:
            logger.warning(
                "source %s item %r has unknown change kind %r; skipped",
                source["id"],
                item.guid,
                change,
            )
            return 0
        return self._create_new(source, item)

    @staticmethod
    def _declared_attributions(item: Any) -> list[str]:
        """A provider's own ``also_seen_in`` claims, normalized (§3.3). A provider that
        already knows a story ran in two places (an aggregator echoing its upstream) says
        so and the engine records it verbatim rather than re-deriving it."""
        raw = getattr(item, "also_seen_in", None) or []
        if isinstance(raw, str):  # a provider that meant one label, not a char sequence
            raw = [raw]
        return [str(x).strip() for x in raw if str(x).strip()]

    def _merge_cross_source(self, source: dict, item: Any) -> bool:
        """Fold this sighting into an item ANOTHER source already wrote, if it is the same
        story; return True when it was merged (so no second row is written) — §3.3, SC#3.

        The identity rule is canonicalized-URL equality and nothing else
        (:func:`~personalclaw.knowledge.source_identity.merge_key`, which returns no key
        for a link-less item or a bare origin). Deliberately narrow: a duplicate item is
        visible and one delete away, while a WRONG merge destroys one of two distinct
        stories and stamps the survivor with an attribution that is false. So an ambiguous
        identity yields two items, and this method simply declines.

        A merge does two writes, and BOTH are required:

        * the second source's seen-set gets the guid (so the merge happens once, not every
          poll — this path bypasses ``create_typed_item``'s folded-in gate, so nothing else
          would record the sighting), and
        * the surviving item gains the second source's attribution, APPENDED. Dropping the
          existing list here would make a merge look exactly like a lost sighting.
        """
        from personalclaw.knowledge.source_identity import merge_key

        key = merge_key(getattr(item, "url", "") or "")
        if not key:
            return False
        existing = self._store.find_item_by_merge_key(key, exclude_source_id=source["id"])
        if existing is None:
            return False
        self._store.mark_source_seen(source["id"], item.guid)
        self._store.record_also_seen_in(
            existing["id"], source["id"], *self._declared_attributions(item)
        )
        logger.debug(
            "source %s guid %r merged into existing item %s (%s)",
            source["id"],
            item.guid,
            existing["id"],
            key,
        )
        return True

    def _create_new(self, source: dict, item: Any) -> int:
        """First sighting → a new item, unless another source already has this story.

        Cross-source dedupe runs FIRST (§3.3): a story the library already holds from a
        different feed becomes an attribution on that item, not a second row. Otherwise
        ``create_typed_item`` writes the seen-row + item atomically and returns None when
        this source's guid was already seen — the per-source novelty gate. Only a
        genuinely-new item (an id) is enqueued, so a page that changes every render cannot
        storm the queue.
        """
        if self._merge_cross_source(source, item):
            return 0
        item_id = self._store.create_typed_item(
            item_type=source.get("item_type") or "bookmark",
            title=item.title or item.url or item.guid,
            content=item.content,
            url=item.url,
            provider=source["provider"],
            source_id=source["id"],
            guid=item.guid,
            extra={"processing_status": "queued"},
        )
        if item_id is None:
            return 0
        declared = self._declared_attributions(item)
        if declared:
            self._store.record_also_seen_in(item_id, *declared)
        self._enqueue(item_id)
        from personalclaw.knowledge_providers.base import CHANGE_CREATED

        self._emit_ingested(source, item, item_id, CHANGE_CREATED)
        return 1

    def _reindex_modified(self, source: dict, item: Any) -> int:
        """An edited item → update the EXISTING row and re-enqueue it.

        No second row: a mutable corpus (a watched directory) re-emits the same guid
        every time the file changes, so keying off the existing item is what makes an
        edit a re-index instead of a duplicate. A guid with no item yet (the source's
        first pass only SEEDED it, so it was never ingested) legitimately becomes a
        create — the alternative would drop the edit entirely."""
        existing = self._store.find_source_item(source["id"], item.guid)
        if existing is None:
            return self._create_new(source, item)
        item_id = existing["id"]
        fields: dict[str, Any] = {
            "title": item.title or existing.get("title") or item.guid,
            "content": item.content,
            "processing_status": "queued",
        }
        if existing.get("is_archived"):
            # The guid came BACK (a deleted file restored, a volume remounted): revive the
            # original item and drop the delete stamp, rather than leaving the user with an
            # archived row plus no live one for a file that is plainly there again.
            meta = existing.get("file_metadata")
            meta = dict(meta) if isinstance(meta, dict) else {}
            meta.pop("source_deleted_at", None)
            fields["is_archived"] = 0
            fields["file_metadata"] = meta
        self._store.update_item(item_id, **fields)
        self._enqueue(item_id)
        from personalclaw.knowledge_providers.base import CHANGE_MODIFIED

        self._emit_ingested(source, item, item_id, CHANGE_MODIFIED)
        return 1

    def _archive_deleted(self, source: dict, item: Any) -> int:
        """The upstream copy is gone → ARCHIVE the item with ``source_deleted_at`` (SC#5).

        Never a hard delete, and the engine has no code path that could become one: the
        store exposes only :meth:`~personalclaw.knowledge.store.KnowledgeStore.archive_source_item`
        for this, which is an UPDATE. Returns 0 — archiving is not a re-index, so a delete
        never enqueues ingestion work for content that no longer exists."""
        existing = self._store.find_source_item(source["id"], item.guid)
        if existing is None:
            return 0
        self._store.archive_source_item(
            existing["id"], deleted_at=item.metadata.get("source_deleted_at", "")
        )
        return 0

    def _enqueue(self, item_id: str) -> None:
        try:
            self._queue.enqueue(item_id)
        except Exception:  # noqa: BLE001 — a queue hiccup must not lose the written item
            logger.debug("source item enqueue failed for %s", item_id, exc_info=True)

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
