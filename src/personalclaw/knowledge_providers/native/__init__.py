"""Native knowledge provider (#30 Task A).

The ONE bundled provider. It offers the 12 typed-create entry points, stores items
in the knowledge library (the ``items`` table = the uber-pool), and **enqueues each
new item for node-graph ingestion**. External providers register their items into the
same library via the queue (see ``knowledge/ingest_queue.py``).

This is a thin orchestration seam over :class:`KnowledgeStore` + the ingestion
runner — it does NOT duplicate storage. Vendor-neutral; lives in the always-on
native bundle.
"""

from __future__ import annotations

import logging

from personalclaw.knowledge_providers.base import KnowledgeItem, KnowledgeProvider, KnowledgeSource

logger = logging.getLogger(__name__)

# The 12 native typed-create entry points.
NATIVE_TYPES = (
    "note",
    "fleeting",
    "journal",
    "gist",
    "bookmark",
    "image",
    "audio",
    "video",
    "pdf",
    "document",
    "sheet",
    "slides",
)
_TEXT_TYPES = {"note", "fleeting", "journal", "gist", "bookmark"}


class NativeKnowledgeProvider(KnowledgeProvider):
    """Wraps the knowledge store; every create registers into the library + enqueues
    ingestion. *enqueue* is a ``(item_id) -> None`` callback (the ingest queue) so the
    provider stays decoupled from the queue substrate + the gateway app."""

    def __init__(self, store, *, enqueue=None):
        self._store = store
        self._enqueue = enqueue

    @property
    def name(self) -> str:
        return "native"

    @property
    def display_name(self) -> str:
        return "PersonalClaw Knowledge"

    def create_typed(
        self,
        *,
        item_type: str,
        title: str = "",
        content: str = "",
        url: str = "",
        tags=None,
        summary: str = "",
        file_path: str = "",
        mime_type: str = "",
        file_size: int = 0,
        gist_language: str = "",
        extra: dict | None = None,
    ) -> str:
        """Create a typed item, register it into the library, and enqueue ingestion.

        Returns the new item id. Text types store content inline; file types carry a
        stored ``file_path``. All flow through the SAME ingest queue → node-graph.
        """
        if item_type not in NATIVE_TYPES:
            raise ValueError(f"unknown knowledge type {item_type!r}")
        ex = dict(extra or {})
        if file_path:
            ex["file_path"] = file_path
        if mime_type:
            ex["mime_type"] = mime_type
        if file_size:
            ex["file_size"] = file_size
        if gist_language:
            ex["gist_language"] = gist_language
        ex["processing_status"] = "queued"
        item_id = self._store.create_typed_item(
            item_type=item_type,
            title=title or (url or content[:60].strip() or "Untitled"),
            content=content,
            tags=tags or [],
            url=url,
            summary=summary,
            provider="native",
            extra=ex,
        )
        if self._enqueue:
            try:
                self._enqueue(item_id)
            except Exception:
                logger.debug("knowledge enqueue failed for %s", item_id, exc_info=True)
        return item_id

    # ── KnowledgeProvider ABC ──

    async def list_sources(self) -> list[KnowledgeSource]:
        # The native provider is one library (no sub-partitioning); report its live count.
        count = self._store.db.execute(
            "SELECT COUNT(*) c FROM items WHERE status='active' AND provider='native'"
        ).fetchone()["c"]
        return [
            KnowledgeSource(
                id="native",
                name="PersonalClaw Knowledge",
                source_type="library",
                item_count=count,
                provider="native",
            )
        ]

    async def search(self, query: str, limit: int = 10) -> list[KnowledgeItem]:
        rows = self._store.search_items_fts(query, limit=limit)
        return [
            KnowledgeItem(
                id=r["id"],
                title=r.get("title", ""),
                content=r.get("content", ""),
                metadata={"type": r.get("item_type", ""), "provider": "native"},
            )
            for r in rows
        ]

    async def get_item(self, item_id: str) -> KnowledgeItem | None:
        it = self._store.get_item(item_id)
        if not it:
            return None
        return KnowledgeItem(
            id=it["id"],
            title=it.get("title", ""),
            content=it.get("content", ""),
            metadata={"type": it.get("type", ""), "provider": it.get("provider", "native")},
        )

    async def delete_item(self, item_id: str) -> bool:
        # store.delete_item returns None — report found/not-found ourselves.
        if not self._store.get_item(item_id):
            return False
        self._store.delete_item(item_id)
        return True


def create_native_provider(store, *, enqueue=None) -> NativeKnowledgeProvider:
    return NativeKnowledgeProvider(store, enqueue=enqueue)
