"""Artifacts as an indexed knowledge source (PRODUCT-EXPERIENCE-PARITY §6).

Content-bearing artifacts are mirrored into the knowledge library so they are FOUND by a
search — and are deliberately never LISTED as knowledge items. An artifact already has a
home (the Artifacts library, its versions, its deploy route); a second copy of it standing
alongside the user's notes would double every count and every list. What was missing is
retrieval: an answer written into an artifact last month was unreachable from the one place
a user looks for what they know.

**This joins the WatchedSource mechanism rather than paralleling it.** The mirror rides the
same four primitives every watched feed and watched directory uses:

* one row in ``sources`` (``provider='artifacts'``, ``kind='artifact'``,
  ``spec={'uri': 'artifact://'}``) — so the mirror appears in the Sources UI, and its
  ``enrichment`` is what :func:`~personalclaw.knowledge.pipeline.runner.ingest_item`
  already consults;
* per-artifact identity as the store's own ``(source_id, guid)`` pair, guid = the artifact
  slug — which is what makes "replace this artifact's mirror, touch nothing else" a
  single-row lookup (:meth:`~personalclaw.knowledge.store.KnowledgeStore.find_source_item`)
  instead of a scan plus a heuristic;
* the ONE ingestion path, ``ingest_queue.enqueue`` — never a hand-written FTS row. An
  ``items`` row inserted without its ``items_fts`` row is invisible to search while looking
  perfectly present in the table, so the writer is always
  :meth:`~personalclaw.knowledge.store.KnowledgeStore.create_typed_item` /
  :meth:`~personalclaw.knowledge.store.KnowledgeStore.update_item`;
* the shared reader conversion (:func:`~personalclaw.knowledge.readers.html_to_prose`) via
  a kind→extension map, so an ``html`` artifact reduces to the same prose an uploaded
  ``.html`` file does.

**Three decisions worth naming.**

1. **Enrichment is RAW, not full.** The mirror is automatic and on by default, so a home
   with 300 artifacts would otherwise spend 300 model calls the user never asked for the
   first time the gateway starts. ``raw`` routes every mirror through the LLM-free graph
   (``FeedItemGraph``) — indexed and embedded locally, never sent to a model — and the
   Sources UI already reads that field back as a "no AI" chip.

2. **A delete FORGETS the sighting** (``KnowledgeStore.forget_source_item``)
   where a watched directory archives it. A vanished file may be back tomorrow and its
   library row is the last copy; a deleted artifact is gone from the store we own, so an
   archived mirror would be an orphan nothing can revive — and leaving the ``source_seen``
   row would make a re-created slug permanently unindexable.

3. **Idempotence is a content hash, not a timestamp.** Every mirror carries
   ``file_metadata['artifact_sha']`` over exactly the title + text it indexed. Re-running
   the whole backfill, or a metadata PATCH that changed nothing observable, therefore
   writes nothing and enqueues nothing. A timestamp comparison would have re-embedded the
   library on every restart.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: The aggregate source row's identity. ONE row for the whole artifact library (not one per
#: artifact): a source row carries poll scheduling and health, and 300 of them would be 300
#: rows in the Sources UI describing one mechanism.
ARTIFACT_SOURCE_PROVIDER = "artifacts"
ARTIFACT_SOURCE_KIND = "artifact"
ARTIFACT_SOURCE_NAME = "Artifacts"
#: The source's ``spec.uri``. Not an http(s) URL on purpose — nothing fetches it; it names
#: where the items came from for a reader of the row.
ARTIFACT_SOURCE_URI = "artifact://"

#: The mirror's ``item_type``. Deliberately OUTSIDE the twelve authorable knowledge types
#: (``_KNOWLEDGE_TYPES`` in the knowledge handlers), which is what makes "searchable but not
#: listed" one predicate on one column rather than a rule spread over every list query — and
#: what stops the create API from ever authoring one directly.
ARTIFACT_ITEM_TYPE = "artifact"

#: Artifact kind → the file extension whose reader treats that body correctly. A CLOSED
#: allowlist, and the fail-closed direction: a kind absent here is not mirrored. The
#: alternative (mirror everything, exclude a denylist) means every artifact kind added later
#: is silently indexed the day it ships — including a binary one, whose "body" on disk is a
#: raw-URL reference, so the library would index the string ``/api/artifacts/x/raw``.
#:
#: ``widget``/``react``/``svg``/``infographic`` are excluded because their bodies are
#: program text, not prose: indexing a widget's JavaScript makes every search for a variable
#: name hit it, which buries the notes the user was looking for. Binary kinds
#: (image/video/docx/xlsx/pptx/pdf) are excluded because their text body IS a reference.
INDEXABLE_KINDS: dict[str, str] = {
    "html": ".html",
    "markdown": ".md",
    "text": ".txt",
    "json": ".json",
    "csv": ".csv",
    "document": ".md",
}

#: Ceiling on one mirrored body, in characters. A generated document can be enormous, and
#: the mirror is a search surface rather than a second copy of record — the artifact itself
#: is always the full text. Truncation is recorded in the item's metadata so a reader can
#: tell a short artifact from a clipped one.
MAX_MIRROR_CHARS = 400_000


def indexable_kind(kind: str) -> bool:
    """Whether an artifact of this kind is mirrored at all."""
    return (kind or "").strip().lower() in INDEXABLE_KINDS


def extract(kind: str, content: str) -> tuple[str, dict]:
    """An artifact body → the text to index, plus its format metadata.

    Routes through the shared reader conversion by KIND rather than by file name, because
    every text artifact is stored on disk as ``current.html`` whatever it is — dispatching
    on the real path would run a markdown artifact through the HTML chrome-stripper and
    index the result.
    """
    from personalclaw.knowledge.readers import html_to_prose

    ext = INDEXABLE_KINDS[(kind or "").strip().lower()]
    body = content or ""
    if ext == ".html":
        text = html_to_prose(body)
    else:
        text = body
    truncated = len(text) > MAX_MIRROR_CHARS
    if truncated:
        text = text[:MAX_MIRROR_CHARS]
    return text, {"format": ext.lstrip("."), "extension": ext, "truncated": truncated}


def redact(text: str) -> str:
    """Strip credentials and exfiltration URLs before the text crosses into the store.

    Applied on the way IN, not on the way out. An artifact body is frequently model-authored
    and may hold a key the model echoed from a tool result; once it is in ``items``/
    ``items_fts`` it is retrievable by every search, embedded into a vector, and quoted back
    into future prompts. Redacting at read time would leave the plaintext in the index.
    """
    from personalclaw.security import redact_credentials, redact_exfiltration_urls

    clean, _ = redact_exfiltration_urls(text or "")
    clean, _ = redact_credentials(clean)
    return clean


def mirror_sha(title: str, text: str) -> str:
    """The identity of what a mirror currently holds: its title AND its text.

    Both, because a rename with an unchanged body still has to refresh the mirror's title
    (that title is what a search result shows), and a hash over content alone would make the
    rename a no-op that leaves the old name in the index.
    """
    h = hashlib.sha256()
    h.update((title or "").encode("utf-8", "replace"))
    h.update(b"\0")
    h.update((text or "").encode("utf-8", "replace"))
    return h.hexdigest()


def find_source(store: Any) -> dict | None:
    """The aggregate artifact source row, or None when it has never been created."""
    for row in store.list_sources():
        if row.get("provider") == ARTIFACT_SOURCE_PROVIDER:
            return row
    return None


def ensure_source(store: Any) -> tuple[str, bool]:
    """The aggregate source row's id, plus whether THIS call created it.

    The row's existence is the first-enable idempotency marker (§6.1 choice 3) — there is no
    separate "did the backfill run" flag to get out of step with it. A caller runs the
    backfill exactly when ``created`` is True, so a reboot (row present) never re-runs it,
    and a home that never enabled the mirror has no row at all.
    """
    from personalclaw.knowledge_providers.base import ENRICHMENT_RAW

    existing = find_source(store)
    if existing is not None:
        return str(existing["id"]), False
    sid = store.create_source(
        name=ARTIFACT_SOURCE_NAME,
        provider=ARTIFACT_SOURCE_PROVIDER,
        kind=ARTIFACT_SOURCE_KIND,
        spec={"uri": ARTIFACT_SOURCE_URI},
        # See the module docstring, decision 1: the mirror never reaches a model.
        enrichment=ENRICHMENT_RAW,
        item_type=ARTIFACT_ITEM_TYPE,
        # Not a poll interval anyone uses — nothing polls this row (no poll-capable provider
        # is registered under its name, so `SourceEngine.tick` filters it out). It is stored
        # only because the column is NOT NULL.
        poll_interval_secs=0,
        created_by="system",
    )
    return sid, True


class ArtifactIndexer:
    """The change-listener half: keeps the mirror in step with the artifact store.

    ``store`` is the :class:`~personalclaw.knowledge.store.KnowledgeStore`; ``enqueue`` is
    the ONE ingestion path (``KnowledgeIngestQueue.enqueue``); ``provider_factory`` resolves
    the artifact provider (injected in tests so a fixture root is used without touching the
    registry); ``config_loader`` returns the knowledge config section so the master switch is
    read per event rather than frozen at construction — a user who turns the mirror off
    should not have to restart the gateway.
    """

    #: Return values of :meth:`index`, so a caller (and a test) can assert WHICH thing
    #: happened rather than inferring it from a row count.
    INDEXED = "indexed"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"
    MISSING = "missing"
    DISABLED = "disabled"

    def __init__(
        self,
        store: Any,
        *,
        enqueue: Callable[[str], None] | None = None,
        provider_factory: Callable[[], Any] | None = None,
        config_loader: Callable[[], Any] | None = None,
    ) -> None:
        self._store = store
        self._enqueue = enqueue
        self._provider_factory = provider_factory or self._default_provider
        self._config_loader = config_loader or self._load_config

    @staticmethod
    def _default_provider() -> Any:
        from personalclaw.artifacts import registry

        return registry.get_provider("native")

    @staticmethod
    def _load_config() -> Any:
        from personalclaw.config.loader import AppConfig

        return AppConfig.load().knowledge

    def enabled(self) -> bool:
        """The master switch. Unreadable config degrades to ON, matching the field's default:
        this is an availability surface (a search index), not a security control, so the
        fail-open cost is an index the user can turn off and the fail-closed cost is a
        library that silently stops being searchable."""
        try:
            return bool(getattr(self._config_loader(), "auto_ingest_artifacts", True))
        except Exception:  # noqa: BLE001 — a config read fault must not break a save
            logger.debug("artifact mirror config read failed; assuming enabled", exc_info=True)
            return True

    # ── the listener ───────────────────────────────────────────────────────────────

    def listener(self, change: str, slug: str) -> None:
        """The single in-process subscriber (``artifacts.changes``). Never raises."""
        from personalclaw.artifacts.changes import DELETE, UPSERT

        if change == DELETE:
            self.remove(slug)
            return
        if change == UPSERT:
            self.index(slug)
            return
        # Matched explicitly against the closed vocabulary rather than falling through to an
        # index: a future change kind must not be silently mis-persisted as an upsert.
        logger.warning("artifact change %r for %s is not handled; ignored", change, slug)

    # ── one artifact ───────────────────────────────────────────────────────────────

    def index(self, slug: str) -> str:
        """Mirror one artifact: create, refresh, or leave alone. Returns the outcome."""
        if not self.enabled():
            return self.DISABLED
        prov = self._provider_factory()
        if prov is None:
            return self.MISSING
        art = prov.get(slug)
        if art is None:
            return self.MISSING
        if not indexable_kind(art.kind):
            # A non-indexable kind may still have a STALE mirror — an artifact cannot change
            # kind today, but a kind removed from the allowlist must not leave its old rows
            # searchable forever. Removing here is what makes the allowlist retroactive.
            source = find_source(self._store)
            if source is not None:
                self._store.forget_source_item(str(source["id"]), slug)
            return self.SKIPPED
        source_id, created = ensure_source(self._store)
        title = (art.name or slug).strip() or slug
        text, meta = extract(art.kind, art.content or "")
        text = redact(text)
        title = redact(title)
        sha = mirror_sha(title, text)
        existing = self._store.find_source_item(source_id, slug)
        file_metadata = {
            "artifact_slug": slug,
            "artifact_kind": art.kind,
            "artifact_sha": sha,
            **meta,
        }
        outcome = self._write(source_id, slug, existing, title, text, art, sha, file_metadata)
        if created:
            # THIS call created the source row, so this is the first enable — which can
            # happen at runtime (the switch flipped on without a restart) rather than only at
            # startup. Backfill after the triggering artifact so its own outcome is reported
            # honestly; the hash gate makes re-visiting it here a no-op.
            self.backfill()
        return outcome

    def _write(
        self,
        source_id: str,
        slug: str,
        existing: dict | None,
        title: str,
        text: str,
        art: Any,
        sha: str,
        file_metadata: dict,
    ) -> str:
        """Create or refresh the one mirror row for *slug*. Split out so :meth:`index` reads
        as the decision it is (skip / unchanged / write) rather than one long branch."""
        summary = redact((art.description or "").strip())
        if existing is not None:
            if (existing.get("file_metadata") or {}).get("artifact_sha") == sha:
                # Nothing observable changed: no write, no re-embed, no queue entry. This is
                # what makes a repeated backfill and a metadata-only PATCH free.
                return self.UNCHANGED
            self._store.update_item(
                existing["id"],
                title=title,
                content=text,
                summary=summary,
                file_metadata=file_metadata,
                processing_status="queued",
            )
            self._enqueue_item(existing["id"])
            return self.INDEXED
        item_id = self._store.create_typed_item(
            item_type=ARTIFACT_ITEM_TYPE,
            title=title,
            content=text,
            provider=ARTIFACT_SOURCE_PROVIDER,
            summary=summary,
            source_id=source_id,
            guid=slug,
            extra={"processing_status": "queued", "file_metadata": file_metadata},
        )
        if item_id is None:
            # The novelty gate refused: this slug's sighting is already recorded, which after
            # `forget_source_item` can only mean a concurrent writer got there first.
            return self.UNCHANGED
        self._enqueue_item(item_id)
        return self.INDEXED

    def remove(self, slug: str) -> bool:
        """Drop an artifact's mirror (and its sighting). True when something was removed.

        Runs REGARDLESS of the master switch: turning the mirror off must not turn deletion
        off too, or a user who disables it after deleting an artifact keeps a searchable
        mirror of content that no longer exists.
        """
        source = find_source(self._store)
        if source is None:
            return False
        return bool(self._store.forget_source_item(str(source["id"]), slug))

    # ── first-enable backfill ──────────────────────────────────────────────────────

    def backfill(self) -> int:
        """Index every existing indexable artifact; return how many were (re-)indexed.

        Idempotent by the same hash gate the save path uses, so calling it twice is a
        measured no-op rather than a promise. Startup calls it only when
        :func:`ensure_source` reports it created the row, which is the once-ever guarantee.
        """
        prov = self._provider_factory()
        if prov is None:
            return 0
        indexed = 0
        for art in prov.list():
            if not indexable_kind(art.kind):
                continue
            try:
                if self.index(art.slug) == self.INDEXED:
                    indexed += 1
            except Exception:  # noqa: BLE001 — one bad artifact must not abandon the rest
                logger.warning("artifact %s could not be indexed", art.slug, exc_info=True)
        return indexed

    def _enqueue_item(self, item_id: str) -> None:
        if self._enqueue is None:
            return
        try:
            self._enqueue(item_id)
        except Exception:  # noqa: BLE001 — a queue hiccup must not lose the written row
            logger.debug("artifact mirror enqueue failed for %s", item_id, exc_info=True)


def start(store: Any, *, enqueue: Callable[[str], None] | None = None) -> ArtifactIndexer:
    """Wire the mirror into a running gateway: subscribe, ensure the row, backfill once.

    The subscription is UNCONDITIONAL and the switch is read per event, because
    ``knowledge.auto_ingest_artifacts`` is a live-editable field: subscribing only when it
    was on at boot would make turning it on a setting that quietly needs a restart. With the
    switch off no source row is created, so turning it on later is still a FIRST enable and
    still backfills exactly once — that first save creates the row and the backfill rides it.

    Returns the indexer so the caller can hold it (and so a test can drive it).
    """
    from personalclaw.artifacts import changes

    indexer = ArtifactIndexer(store, enqueue=enqueue)
    changes.subscribe(indexer.listener)
    if not indexer.enabled():
        return indexer
    _, created = ensure_source(store)
    if created:
        count = indexer.backfill()
        logger.info("Artifact knowledge mirror: backfilled %d artifact(s)", count)
    return indexer
