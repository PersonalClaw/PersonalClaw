"""Ingestion runner — orchestrates one item through its node-graph (#30).

Entry point ``ingest_item``: load the item → pick its code-owned graph → execute the
DAG (each node output → the extracted-content pool) → run terminal stages over the
whole bundle (consolidated text → insights → embed) → set ``processing_status``.
Per-node + per-stage progress is broadcast over per-resource SSE so the detail view
can show live ingestion transparency.

Pure-python in Task A (text/document graphs); model-backed media/video nodes layer on
in Task B (#47) and degrade gracefully (skipped when their use-case has no model).
"""

from __future__ import annotations

import logging

from personalclaw.knowledge.pipeline import ensure_nodes_registered, graph_for
from personalclaw.knowledge.pipeline.executor import PipelineExecutor
from personalclaw.knowledge.pipeline.types import NodeContext
from personalclaw.knowledge_providers.base import ENRICHMENT_FULL, ENRICHMENT_RAW

logger = logging.getLogger(__name__)


def _enrichment_for(store, item: dict) -> str:
    """The enrichment mode governing this item's ingestion (WATCHED-SOURCES §6.3).

    ``full`` for everything the user created locally (no ``source_id``) — the native path
    is unchanged. For an item a WatchedSource wrote, its source's setting decides.

    The unresolvable case is deliberately asymmetric: a source item whose ``sources`` row
    is gone or unreadable degrades to ``raw``, not ``full``. The no-AI setting is a promise
    made to the user about specific content, and content whose promise we can no longer
    READ must not be handed to a model on the assumption it was fine; the cost of guessing
    raw is a missing summary, the cost of guessing full is a broken guarantee.
    """
    source_id = (item or {}).get("source_id")
    if not source_id:
        return ENRICHMENT_FULL
    try:
        source = store.get_source(source_id)
    except Exception:  # noqa: BLE001 — an unreadable source row must not fail the ingest
        logger.debug("enrichment lookup failed for source %s", source_id, exc_info=True)
        return ENRICHMENT_RAW
    if not source:
        return ENRICHMENT_RAW
    # Matched explicitly against the closed vocabulary: an unknown value is treated as raw
    # rather than defaulted to full, so a typo in the column can never turn a no-AI source
    # into an enriched one (the default-branch-swallows-an-unmapped-value failure).
    return ENRICHMENT_FULL if source.get("enrichment") == ENRICHMENT_FULL else ENRICHMENT_RAW


# SSE feed key for an item's ingestion progress (per-resource; transport doctrine).
def progress_feed(item_id: str) -> str:
    return f"knowledge:ingest:{item_id}"


async def ingest_item(
    store,
    item_id: str,
    *,
    embedder=None,
    insights_pool=None,
    params_for=None,
    publish=None,
) -> str:
    """Run the full ingestion graph for *item_id*. Returns the final status
    (``done`` | ``partial`` | ``failed``). Never raises — a failure is recorded on
    the item as ``processing_status='failed'`` + ``processing_error``.

    *publish* (optional) is a ``(event: str, data: dict) -> None`` SSE emitter for
    live progress; *params_for* layers user node-execution-param config.
    """
    ensure_nodes_registered()
    item = store.get_item(item_id)
    if not item:
        return "failed"

    item_type = item.get("type") or item.get("item_type") or "note"
    # The owning WatchedSource's no-AI setting (WATCHED-SOURCES §6.3). Resolved ONCE here
    # and threaded through both halves of the guarantee — the graph shape and the terminal
    # stages — because a raw item that skipped the LLM nodes but still ran the model-backed
    # terminal stages would keep the promise structurally and break it in practice.
    enrichment = _enrichment_for(store, item)
    raw_mode = enrichment == ENRICHMENT_RAW

    def _emit(event: str, **data) -> None:
        if publish:
            try:
                publish(event, {"item_id": item_id, **data})
            except Exception:
                logger.debug("knowledge ingest publish failed", exc_info=True)

    store.update_item(item_id, processing_status="processing", processing_error=None, touch=False)
    store.db.commit()
    _emit("ingest_started", item_type=item_type)

    try:
        graph = graph_for(item_type, enrichment=enrichment)
    except Exception as exc:
        logger.exception("graph build failed for %s", item_type)
        store.update_item(
            item_id, processing_status="failed", processing_error=str(exc), touch=False
        )
        store.db.commit()
        _emit("ingest_failed", error=str(exc))
        return "failed"

    ctx = NodeContext(
        item_id=item_id,
        item_type=item_type,
        file_path=item.get("file_path") or "",
        content=item.get("content") or "",
        url=item.get("url") or "",
    )
    executor = PipelineExecutor(
        graph,
        params_for=params_for,
        on_node=lambda nt, phase: _emit("node", node=nt, phase=phase),
    )

    # Everything from here is wrapped so an unhandled error in any stage marks the
    # item `failed` instead of stranding it in `processing` forever (the in-memory
    # queue can't retry a half-done item, and a restart only resumes whole items).
    try:
        result = await executor.run(ctx)

        # The item may have been DELETED while this ran (a user cancels a wrong video
        # mid-ingest). The delete handler swept the artifacts that existed AT that moment,
        # but nodes that finished after wrote MORE (frames/audio) — which would now be
        # orphaned, plus we'd persist extracted rows for a gone item. If the item is gone,
        # clean up any derived artifacts this run produced and stop.
        if store.get_item(item_id) is None:
            _cleanup_orphaned_artifacts(item_id)
            return "deleted"

        # Persist each pooled node output into the extracted-content pool.
        store.clear_extracted_contents(item_id)
        for out in result.pooled_outputs():
            store.add_extracted_content(
                item_id,
                out.node_type,
                backend=out.backend,
                text=out.text,
                metadata=out.metadata,
            )
        # …then the extra self-named rows (a node whose product is a SET of outputs —
        # the fetch-and-slice brief/body/meta cut). Same item, no child rows anywhere:
        # slices are role-sized VIEWS of one document, not chunks.
        for row in result.pool_rows():
            store.add_extracted_content(
                item_id,
                row.node_type,
                backend=row.backend,
                text=row.text,
                metadata=row.metadata,
            )

        # Persist structural metadata from non-pooled media nodes onto the item:
        # exif → file_metadata (width/height/format/…); thumbnail → thumbnail_path.
        # Without this the Image/Video graph computes these and discards them.
        _persist_structural_metadata(store, item_id, item, result)

        # Consolidated text = the merged bundle (the 'consolidate' node when present,
        # else the single pooled text, else the item's existing content).
        pooled = result.pooled_outputs()
        consolidated = ""
        if "consolidate" in result.outputs and result.outputs["consolidate"].success:
            consolidated = result.outputs["consolidate"].text
        elif pooled:
            consolidated = pooled[0].text
        consolidated = consolidated or (item.get("content") or "")

        # Fallback descriptor: a file-backed item whose text extractors all degraded
        # (e.g. an image with no OCR/vision model configured) would otherwise be left
        # content-less — no pool entry, no title basis, unsearchable. Synthesize a
        # minimal human-readable line from the structural metadata we DID extract so
        # the item is still identifiable and findable, honoring graceful degradation.
        if not consolidated.strip() and (item.get("file_path") or ""):
            fresh = (
                store.get_item(item_id) or item
            )  # _persist_structural_metadata just merged file_metadata
            consolidated = _structural_descriptor(fresh) or consolidated

        # Backfill the item's content with extracted text when it had none (file types).
        # update_item recomputes word_count from the new content.
        if consolidated and not (item.get("content") or "").strip():
            store.update_item(item_id, content=consolidated, touch=False)
            store.db.commit()
        else:
            # Content already present (typed item, or a re-ingest) — ensure word_count
            # matches it (older file items were created at word_count=0 and never fixed).
            wc = len((item.get("content") or "").split())
            if wc != (item.get("word_count") or 0):
                store.update_item(item_id, word_count=wc, touch=False)
                store.db.commit()

        # Terminal stages run serially: they share the store's single sqlite connection,
        # so overlapping their BEGIN/COMMIT transactions (e.g. via asyncio.gather) lets one
        # stage's open transaction abort another's — silently dropping its writes. Keep
        # them sequential for correctness. (The LLM calls dominate latency; if that ever
        # needs cutting, give each concurrent stage its own DB connection first.)
        if raw_mode:
            # §6.3: the three model-backed terminal stages are NOT CALLED for a raw source.
            # Not "called with a disabled pool" — not reached at all, which is the only form
            # of the promise that survives someone binding a model later. They report
            # "skipped" (never "done"), so the detail UI distinguishes a no-AI source from
            # an item whose enrichment silently produced nothing.
            insights_phase = entities_phase = intents_phase = "skipped"
            insights_ok = True  # nothing failed; a raw item is not under-enriched
            for stage in ("insights", "entities", "intents"):
                _emit("node", node=stage, phase="skipped")
        else:
            _emit("node", node="insights", phase="running")
            insights_ok = await _run_insights(store, item_id, consolidated, insights_pool)
            insights_phase = "done" if insights_ok else "failed"
            _emit("node", node="insights", phase=insights_phase)

            # Entity/relation extraction over the consolidated text → the entity graph
            # (one logical doc = one extraction; no per-chunk fan-out).
            _emit("node", node="entities", phase="running")
            entities_phase = await _run_entities_stage(store, item_id, consolidated, insights_pool)
            _emit("node", node="entities", phase=entities_phase)

            # Tier-3 intent matching — natural-language user intents run against the
            # consolidated text; relevant matches are recorded as intent_outcomes by value.
            _emit("node", node="intents", phase="running")
            intents_phase = await _run_intents_stage(
                store, item_id, item_type, consolidated, insights_pool
            )
            _emit("node", node="intents", phase=intents_phase)

        # Terminal: embed (title + summary), reusing the existing embedder path.
        _emit("node", node="embed", phase="running")
        embed_phase = _embed(store, item_id, embedder)
        _emit("node", node="embed", phase=embed_phase)

        # P12 TIER-2 semantic dedup — must run AFTER embed (the vector doesn't exist at
        # create time). Fuzzy-matches this item against same-type neighbours (filename +
        # cosine + date-gate) and archives the format-recall loser on a confirmed dup.
        # Inert when no embedder / no vector (behaves as pre-P12); never fails the ingest.
        _emit("node", node="dedup", phase="running")
        dedup_result = _dedup(store, item_id, embedder)
        _emit("node", node="dedup", phase="done")
        if dedup_result:
            _emit("dedup", **dedup_result)
    except Exception as exc:
        # The item may have been DELETED mid-enrichment — the terminal stages above (the
        # ~30s insights/entity model calls) are exactly the window a user cancels a wrong
        # item in. Its parent `items` row is then gone, so the next terminal write
        # raises `sqlite3.IntegrityError: FOREIGN KEY constraint failed` (e.g.
        # `_write_item_tags` re-inserting `item_tags` from the AI topics). That is not a
        # processing fault: there is nothing left to enrich. Abort quietly exactly like
        # the post-graph delete guard above — sweep any late-written artifacts, emit no
        # traceback, and do NOT write `processing_status='failed'` to a row that no longer
        # exists. Genuine mid-pipeline failures (item still present) fall through and are
        # recorded `failed` as before.
        if store.get_item(item_id) is None:
            _cleanup_orphaned_artifacts(item_id)
            return "deleted"
        logger.exception("knowledge ingest failed mid-pipeline for %s", item_id)
        store.update_item(
            item_id, processing_status="failed", processing_error=str(exc)[:500], touch=False
        )
        store.db.commit()
        _emit("ingest_failed", error=str(exc))
        return "failed"

    status = result.status
    # On a non-clean run, surface WHY so the detail UI shows a reason instead of a
    # bare "partial"/"failed" badge after a reload (live SSE node phases are gone by
    # then). Prefer real failures; otherwise explain the skips (the common case is
    # model-backed nodes — vision/ocr — gracefully skipped with no model configured).
    proc_error = None
    if status in ("failed", "partial") and result.failed:
        msgs = []
        for nt in result.failed:
            fout = result.outputs.get(nt)
            err = (getattr(fout, "error", "") or "").strip() if fout else ""
            msgs.append(f"{nt}: {err}" if err else nt)
        proc_error = "; ".join(msgs)[:500]
        # A bookmark whose ONLY failure is reaching the URL (network/DNS/timeout/HTTP
        # error) isn't an unexpected processing fault — the URL is saved + clickable and
        # a later retry may succeed. Mark it 'unreachable' (a distinct, retryable state)
        # rather than 'failed', so the UI can say "Unreachable · Retry" not "Failed".
        if status == "failed":
            scrape = result.outputs.get("bookmark_scrape")
            scrape_meta = getattr(scrape, "metadata", None) or {} if scrape else {}
            only_scrape_failed = result.failed == ["bookmark_scrape"]
            if only_scrape_failed and scrape_meta.get("error_kind") == "unreachable":
                status = "unreachable"
    elif status == "partial" and result.skipped:
        proc_error = "Skipped (optional steps unavailable): " + ", ".join(result.skipped[:12])
    # The insights stage failing (model error / cold pool) must not leave the item
    # silently under-enriched — downgrade to 'partial' and say why so a re-enrich isn't
    # needed to discover the gap. This is an actionable failure, so it must surface even
    # when the graph already went 'partial' from benign optional-node skips: lead with
    # the insights reason (the benign "Skipped (…)" prefix is what the UI suppresses, so
    # never let it mask a real failure) and append the skip context if present.
    if not insights_ok:
        if status == "done":
            status = "partial"
        insights_msg = "insights: model unavailable (insights not refreshed — try regenerating)"
        if not proc_error:
            proc_error = insights_msg
        elif not proc_error.startswith(insights_msg):
            proc_error = f"{insights_msg}; {proc_error}"
    # Persist the GROUND-TRUTH per-node phase map so the detail UI shows what actually
    # ran on reload — not a lossy reconstruction from processing_error (which can't
    # tell a skipped node from a done one once a real failure also occurred). Covers
    # the graph nodes (ran/failed/skipped) + the terminal stages (insights/entities/
    # intents/embed). A node absent from all three sets never became ready → skipped.
    node_phases: dict[str, str] = {}
    for nt in result.ran:
        node_phases[nt] = "done"
    for nt in result.failed:
        node_phases[nt] = "failed"
    for nt in result.skipped:
        node_phases[nt] = "skipped"
    for nt in getattr(graph, "nodes", {}):
        node_phases.setdefault(nt, "skipped")
    # The terminal stages are NOT graph nodes, so nothing above ever supplies them —
    # each one reports the phase its own run returned. These were previously forced to
    # "done" unconditionally, which reported a step that never ran as healthy: with no
    # embedding model bound, `embed` claimed "done" while writing zero vectors. A stage
    # that legitimately had nothing to do says "skipped", not "done".
    node_phases["insights"] = insights_phase
    node_phases["entities"] = entities_phase
    node_phases["intents"] = intents_phase
    node_phases["embed"] = embed_phase
    _merge_file_metadata(store, item_id, {"node_phases": node_phases})

    store.update_item(item_id, processing_status=status, processing_error=proc_error, touch=False)
    store.db.commit()
    _emit(
        "ingest_complete",
        status=status,
        ran=result.ran,
        skipped=result.skipped,
        failed=result.failed,
    )
    return status


def _structural_descriptor(item: dict) -> str:
    """A minimal human-readable line for a file item whose text extraction degraded —
    derived from the filename + structural metadata (dimensions, format, pages, size).
    Gives an otherwise content-less media item something to title, embed, and find on."""
    import os

    meta = item.get("file_metadata") or {}
    item_type = (item.get("item_type") or item.get("type") or "file").strip()
    name = os.path.basename(item.get("file_path") or "") or item_type
    bits: list[str] = []
    if meta.get("width") and meta.get("height"):
        bits.append(f"{meta['width']}×{meta['height']}")
    if meta.get("format"):
        bits.append(str(meta["format"]))
    if meta.get("page_count"):
        bits.append(f"{meta['page_count']} pages")
    if meta.get("duration_seconds"):
        bits.append(f"{round(float(meta['duration_seconds']))}s")
    if item.get("file_size"):
        kb = item["file_size"] / 1024
        bits.append(f"{kb:.0f} KB" if kb < 1024 else f"{kb / 1024:.1f} MB")
    shape = ", ".join(bits)
    label = item_type.capitalize()
    return f"{label}: {name}" + (f" ({shape})" if shape else "")


def _cleanup_orphaned_artifacts(item_id: str) -> None:
    """Delete any derived files this item's pipeline wrote (``<item_id>.audio.wav`` /
    ``<item_id>.frame_NNN.jpg`` / ``<item_id>.dense*``) when the item was deleted while
    processing — the delete handler's sweep ran before these late-written files existed.
    Mirrors the delete handler's guard: only inside the knowledge files dir, item_id is a
    UUID so the glob has no metacharacters."""
    from pathlib import Path

    from personalclaw.knowledge import knowledge_files_dir

    try:
        files_root = Path(knowledge_files_dir()).resolve()
        for p in files_root.glob(f"{item_id}.*"):
            resolved = p.resolve()
            if resolved.is_relative_to(files_root) and resolved.is_file():
                resolved.unlink(missing_ok=True)
    except OSError:
        logger.debug("orphaned-artifact cleanup failed for %s", item_id, exc_info=True)


def _merge_file_metadata(store, item_id: str, new_keys: dict) -> None:
    """Merge keys into the item's file_metadata, re-reading current state first so a
    prior merge (structural metadata) in the same run isn't clobbered."""
    fresh = store.get_item(item_id) or {}
    merged = dict(fresh.get("file_metadata") or {})
    merged.update(new_keys)
    store.update_item(item_id, file_metadata=merged, touch=False)
    store.db.commit()


def _persist_structural_metadata(store, item_id: str, item, result) -> None:
    """Persist non-pooled media-node outputs onto the item. The exif node yields
    width/height/format/mode → merged into ``file_metadata`` (it sets ``pooled=False``
    so it never reaches the text pool — without this its output would be discarded).
    The thumbnail is made inline at upload, so the graph produces none here."""
    fields: dict[str, object] = {}

    exif = result.outputs.get("exif")
    if exif is not None and getattr(exif, "success", False) and getattr(exif, "metadata", None):
        merged = dict((item or {}).get("file_metadata") or {})
        merged.update(exif.metadata)
        fields["file_metadata"] = merged

    # Document read (pdf/doc/sheet/slides) yields structural shape — page_count, format —
    # that the detail metadata strip + the agent's knowledge_get ("N pages") read off
    # file_metadata. Persist it (keeping only the shape keys; the text already pooled),
    # else every document shows no page count despite the reader having extracted it.
    # `bookmark_scrape` is read here too: a paper fetched from an arXiv/DOI/PDF URL has no
    # `document_read` node (it is a bookmark), so without this a FETCHED paper would report
    # no page count while an uploaded one does — the same document, two answers.
    doc = result.outputs.get("document_read") or result.outputs.get("bookmark_scrape")
    if doc is not None and getattr(doc, "success", False) and getattr(doc, "metadata", None):
        shape = {
            k: v
            for k, v in doc.metadata.items()
            if k
            in (
                "page_count",
                "format",
                "sheet_count",
                "slide_count",
                "row_count",
                "paragraph_count",
            )
            and v is not None
        }
        if shape:
            _fm = fields.get("file_metadata") or (item or {}).get("file_metadata") or {}
            merged = dict(_fm) if isinstance(_fm, dict) else {}
            merged.update(shape)
            fields["file_metadata"] = merged

    # Fetch-and-slice (WATCHED-SOURCES §5) → the document's detected sections and its
    # extracted references onto the item. The SLICES are pool rows; these are the
    # structural findings ABOUT the document, which belong on the item the same way
    # page_count does. Reference LINKING is deliberately not here — §5 extracts and
    # stores, and KNOWLEDGE-SYNTHESIS's relate-on-persist step resolves.
    sliced = result.outputs.get("document_slice")
    if (
        sliced is not None
        and getattr(sliced, "success", False)
        and (getattr(sliced, "metadata", None) or {}).get("sliced")
    ):
        keep = ("sections", "section_strategies", "references", "references_unkeyed")
        found = {k: v for k, v in sliced.metadata.items() if k in keep}
        if found:
            _fm = fields.get("file_metadata") or (item or {}).get("file_metadata") or {}
            merged = dict(_fm) if isinstance(_fm, dict) else {}
            merged.update(found)
            fields["file_metadata"] = merged

    # Bookmark scrape → derived link-card title/description onto the item.
    scrape = result.outputs.get("bookmark_scrape")
    if (
        scrape is not None
        and getattr(scrape, "success", False)
        and getattr(scrape, "metadata", None)
    ):
        meta = scrape.metadata
        scraped_title = (meta.get("url_title") or "").strip()
        if scraped_title and not ((item or {}).get("url_title") or "").strip():
            fields["url_title"] = scraped_title
        if meta.get("url_description") and not ((item or {}).get("url_description") or "").strip():
            fields["url_description"] = meta["url_description"]
        # A bookmark's title is seeded with the URL at create (no title known yet).
        # Once we've scraped the page's real title, promote it to the displayed title
        # so the Library shows "Example Domain" instead of "https://example.com".
        # Compare normalized URLs so a title seeded with any URL form (raw, trailing
        # slash, tracking params) is still recognized as a placeholder to replace.
        from personalclaw.knowledge.store import normalize_url

        cur_title = ((item or {}).get("title") or "").strip()
        cur_url = ((item or {}).get("url") or "").strip()
        title_is_url_placeholder = not cur_title or normalize_url(cur_title) == normalize_url(
            cur_url
        )
        if scraped_title and title_is_url_placeholder:
            fields["title"] = scraped_title

    if fields:
        store.update_item(item_id, touch=False, **fields)
        store.db.commit()


async def _run_entities_stage(store, item_id: str, content: str, pool) -> str:
    """Link + extract entities for the item, writing to the entity graph.

    Two passes, deliberately in this order:

    1. **The deterministic alias pre-pass** (MEMORY-GRAPH §1.3) — every entity the graph
       ALREADY knows whose name or alias literally appears gets a mention. Zero LLM calls,
       and crucially it runs **even when `pool is None`**: without it, a user with no model
       bound ingests a document that plainly names a known entity and gets nothing, because
       the extractor is the only thing that ever linked.
    2. **LLM extraction** — finds what is NEW (entities the graph has never seen, and the
       relations between them), which a string matcher structurally cannot.

    They are complementary, not redundant: the model discovers, the trie guarantees. Both
    write through `add_mention` (`INSERT OR IGNORE`), so an entity both find is one mention.

    Re-runs cleanly: the extraction path clears this item's prior mentions/relations first so
    a re-ingest doesn't dup — and the pre-pass is re-applied after that clear, so its links
    survive the very stage that wipes them.

    Returns the phase to report. Unlike the intents stage, this one is NOT wholly
    model-dependent: pass 1 is the deliberate model-free guarantee, so with no pool the stage
    still ran and linked — ``done``, not ``skipped``. Only a contentless item skips outright;
    an errored extraction reports ``failed`` (pass 1's links stand regardless).
    """
    if not content.strip():
        return "skipped"

    # Pass 1 runs unconditionally — no model required, and no reason to make linking wait on
    # one. Best-effort: a failure here must not stop extraction from running.
    try:
        from personalclaw.knowledge.alias_prepass import link_known_entities

        link_known_entities(store, item_id, content)
    except Exception:
        logger.debug("alias pre-pass failed for %s", item_id, exc_info=True)

    if pool is None:
        return "done"  # pass 1 (the model-free half) ran — the stage did its work
    try:
        from personalclaw.knowledge.extractor import EntityExtractor

        extraction = await EntityExtractor(pool=pool).extract(content)
    except Exception:
        logger.debug("entity extraction failed for %s", item_id, exc_info=True)
        return "failed"
    entities = extraction.get("entities") or []
    relations = extraction.get("relations") or []
    if not entities:
        return "done"  # extraction ran and found nothing new to add
    try:
        # SNAPSHOT the pre-pass links before clearing, then restore them after.
        #
        # `clear_item_entities` does more than drop this item's mentions: it also deletes any
        # entity left with no mentions and no relations. So on a single-item store the
        # pre-pass's entity is GONE after the clear, and simply re-running the pre-pass finds
        # nothing to link to — the index it builds is empty. Measured, not assumed: a
        # re-link-after-clear returned 0.
        #
        # Snapshotting (name, context) survives that deletion because a name can be re-found
        # or re-created, whereas an id cannot.
        prepass_links: list[tuple[str, str, str]] = []
        try:
            for row in store.db.execute(
                "SELECT m.entity_id, e.name, e.entity_type, m.context "
                "FROM mentions m JOIN entities e ON e.id = m.entity_id "
                "WHERE m.item_id = ?",
                (item_id,),
            ).fetchall():
                prepass_links.append(
                    (row["name"], row["entity_type"] or "concept", row["context"] or "")
                )
        except Exception:
            logger.debug("alias pre-pass snapshot failed for %s", item_id, exc_info=True)

        store.clear_item_entities(item_id)

        # Restore. `find_entity` first, because the extractor may be about to re-create the
        # same entity and two rows for one name is worse than a lost link.
        for name, etype, context in prepass_links:
            try:
                existing = store.find_entity(name)
                eid = existing["id"] if existing else store.add_entity(name=name, entity_type=etype)
                store.add_mention(item_id, eid, context=context or None)
            except Exception:
                logger.debug("alias re-link failed for %s → %r", item_id, name, exc_info=True)
        entity_map: dict[str, str] = {}
        for ent in entities:
            name = (ent.get("name") or "").strip()
            if not name:
                continue
            existing = store.find_entity(name)
            if existing:
                eid = existing["id"]
                # An entity first extracted without a description can gain one from a
                # later, richer mention (no-op if it already has one).
                store.backfill_entity_description(eid, ent.get("description"))
            else:
                eid = store.add_entity(
                    name=name,
                    entity_type=ent.get("type", "concept"),
                    description=ent.get("description"),
                )
            entity_map[name] = eid
            store.add_mention(item_id, eid, context=ent.get("description"))
        for rel in relations:
            src = entity_map.get((rel.get("source") or "").strip())
            tgt = entity_map.get((rel.get("target") or "").strip())
            if src and tgt:
                store.add_entity_relation(
                    source_id=src,
                    target_id=tgt,
                    relation_type=rel.get("type", "uses") or "uses",
                    description=rel.get("description"),
                    source_item_id=item_id,
                )
        store.db.commit()
        # Rebuild the in-memory graph so cleared edges drop and the new ones show.
        store._load_graph()
        return "done"
    except Exception:
        logger.debug("entity graph write failed for %s", item_id, exc_info=True)
        return "failed"


async def _run_insights(store, item_id: str, content: str, pool) -> bool:
    """Extract + persist insights for the item. Returns False when the model call
    errored (e.g. cold/unavailable pool) so the caller can mark the item ``partial``
    instead of silently leaving it ``done`` with stale/empty insights. Returns True on
    success or when there's legitimately nothing to do (no content / empty result)."""
    if not content.strip():
        return True
    try:
        from personalclaw.knowledge.insights import InsightsExtractor

        insights = await InsightsExtractor(pool=pool).extract(content, raise_on_error=True)
    except Exception:
        logger.debug("insights extraction failed for %s", item_id, exc_info=True)
        return False
    if not insights:
        return True
    item = store.get_item(item_id)
    # `title` is an item field, not an insight category — pull it out of the bundle.
    ai_title = str(insights.pop("title", "") or "").strip()
    prev_insights = dict((item or {}).get("insights") or {})
    # An AI-generated SUMMARY is identified by matching the PREVIOUS enrichment's output:
    # insights.summary always reflects the content it was extracted from, so if the item's
    # current summary still equals it, it's AI-seeded and untouched → refresh on a
    # re-ingest. If it differs, the user edited it → preserve. This keeps a content edit
    # from leaving a stale AI summary while never clobbering a user-authored one.
    # (TAGS no longer use this inference — their provenance is recorded per membership
    # row; see the tags block below.)
    prev_summary = str(prev_insights.get("summary") or "")
    merged = dict(prev_insights)
    merged.update(insights)
    fields: dict[str, object] = {"insights": merged}
    cur_summary = ((item or {}).get("summary") or "").strip()
    if insights.get("summary") and (not cur_summary or cur_summary == prev_summary.strip()):
        fields["summary"] = insights["summary"]
    # AI title: record it, and promote to the displayed title per the vision —
    # ALWAYS for files (the filename is never a good display title), and for non-files
    # when the user left the title blank or a fleeting note is titled by its raw
    # content prefix. Journals are date-driven records — they never carry an AI title
    # (it's never displayed, and the detail page's "use AI title" affordance shouldn't
    # offer to overwrite a journal's date heading).
    item_type = (item or {}).get("item_type") or (item or {}).get("type") or ""
    if ai_title and item_type != "journal":
        fields["ai_title"] = ai_title
        cur_title = ((item or {}).get("title") or "").strip()
        is_file_type = item_type in (
            "image",
            "audio",
            "video",
            "pdf",
            "document",
            "sheet",
            "slides",
        )
        content = (item or {}).get("content") or ""
        # When a text item is created with a blank title, the handler seeds the title
        # with the content's first 60 chars. Treat that truncated-content placeholder
        # like a blank title so the AI title (a real headline) replaces it — for any
        # text type, not just fleeting notes.
        titled_by_content = bool(cur_title) and cur_title == content[:60].strip()
        # File items promote only while still filename-titled: the create form lets the
        # user type a real title for an upload, and that must survive enrichment. The
        # seeded filename is recorded as file_metadata.original_filename at store time;
        # legacy items without it keep the old always-promote behavior.
        orig_fn = str(
            ((item or {}).get("file_metadata") or {}).get("original_filename") or ""
        ).strip()
        titled_by_filename = cur_title == orig_fn if orig_fn else True
        if (is_file_type and titled_by_filename) or not cur_title or titled_by_content:
            fields["title"] = ai_title
    # AI tags come from the extracted topics. Set them when the item has none (first
    # enrichment) OR when every tag it currently carries was written by a previous
    # enrichment (AI-seeded + untouched → refresh on a content edit). A tag the user
    # authored is never overwritten.
    #
    # Provenance is now RECORDED on the membership row (`item_tags.source`) rather than
    # INFERRED by comparing the item's tags against the previous run's topics. The old
    # comparison was an ordered-list equality against a JSON blob, which broke the moment
    # tags became rows: rows come back in name order, so `["redis","caching"]` vs
    # `["caching","redis"]` would compare unequal and the refresh branch would silently
    # stop firing — leaving a content-edited item with stale AI tags forever. Asking the
    # store who wrote each tag is both correct and order-independent.
    topics = [t for t in (insights.get("topics") or []) if isinstance(t, str) and t.strip()]
    if topics and store.tags_are_all_ai_authored(item_id):
        fields["tags"] = topics
        # Mark the refreshed set as AI-authored too, so the NEXT enrichment can still
        # tell them apart from anything the user adds in the meantime.
        fields["tag_source"] = "ai"
    store.update_item(item_id, touch=False, **fields)
    store.db.commit()
    return True


def _intents_path(store):
    """The intents.json sibling of the knowledge DB (per-store, cwd-partition model)."""
    from pathlib import Path

    db_path = getattr(store, "db_path", "") or ""
    return Path(db_path).parent / "intents.json" if db_path else Path("intents.json")


async def _run_intents_stage(store, item_id: str, item_type: str, content: str, pool) -> str:
    """Run Tier-3 user intents over the consolidated content. Each relevant match is
    persisted as an outcome BY VALUE in the intent_outcomes table, with only a soft
    back-reference to this item — so the gathered insight survives item deletion.

    Returns the phase to report: ``skipped`` when the stage had nothing to run (no
    content, no user intents defined, or no model to match with — matching is the whole
    stage, so without a pool nothing happened), ``failed`` when the run errored,
    ``done`` when intents were actually matched against the content."""
    if not content.strip():
        return "skipped"
    try:
        from personalclaw.knowledge.intents import IntentStore, run_intents

        intents = IntentStore(_intents_path(store)).load()
        if not intents:
            return "skipped"
        # `run_intents` returns [] both for "no model bound" and "no intent matched".
        # Only the former is a step that did not run, so check the pool here rather than
        # inferring it from an empty match list.
        if not pool:
            return "skipped"
        matches = await run_intents(intents, item_type, content, pool=pool)
    except Exception:
        logger.debug("intent stage failed for %s", item_id, exc_info=True)
        return "failed"
    # Clear this item's prior outcomes before recording the current matches, so a
    # re-ingest of edited content can't leave a stale outcome from the old content
    # (e.g. an item that no longer matches an intent it once did). Outcomes orphaned
    # by a deleted item (item_id NULL) are preserved — only THIS item's are cleared.
    store.clear_item_intent_outcomes(item_id)
    if not matches:
        return "done"  # the intents ran; nothing this item matched
    item = store.get_item(item_id)
    item_title = (item or {}).get("title") or (item or {}).get("ai_title") or ""
    by_id = {i.id: i for i in intents}
    for m in matches:
        try:
            store.record_intent_outcome(
                m.intent_id,
                intent_name=(_bi.goal if (_bi := by_id.get(m.intent_id)) else ""),
                item_id=item_id,
                item_title=item_title,
                takeaway=m.takeaway,
                fields=m.fields,
            )
        except Exception:
            logger.debug("recording outcome for intent %s failed", m.intent_id, exc_info=True)
    return "done"


def _embed(store, item_id: str, embedder) -> str:
    """Embed the item and return the phase to report: ``done`` only when a vector was
    actually written, ``skipped`` when there was no embedder / no vector to write (the
    common case — no embedding model bound), ``failed`` when the attempt errored.

    The phase is the item's ONLY record that this step ran, so it must reflect whether a
    vector exists. Reporting "done" for a no-op made an item with no embedding look
    fully processed, hiding the missing-vector condition from the ingest view.

    KL-9: after the WHOLE-ITEM vector, the item's consolidated text is structurally
    chunked (``knowledge.chunking``) and each chunk embedded into the ``chunks`` table.
    Chunks are ADDITIVE — the item row keeps its own vector; the chunk index is what
    gives retrieval reach into content deep in a long document."""
    if not embedder:
        return "skipped"
    try:
        from personalclaw.knowledge.embedder import floats_to_bytes

        item = store.get_item(item_id)
        if not item:
            return "skipped"
        # The whole-item vector is a compact title+summary identity/topic signal; the
        # body's semantic recall lives in the chunk index built below (KL-9 clean break —
        # the old body top-up is gone; see compose_item_text).
        vec = embedder.embed_for_item(
            item.get("title") or "",
            item.get("summary"),
            item.get("content"),
        )
        if not vec:
            # An unavailable/unbound embedding model returns None rather than raising —
            # a graceful degradation, not a fault. No vector was written either way.
            return "skipped"
        store.db.execute(
            "UPDATE items SET embedding = ? WHERE id = ?", (floats_to_bytes(vec), item_id)
        )
        store.db.commit()
        embed_item_chunks(store, item_id, item.get("content") or "", embedder)
        return "done"
    except Exception:
        logger.debug("knowledge embed failed for %s", item_id, exc_info=True)
        return "failed"


def embed_item_chunks(store, item_id: str, content: str, embedder) -> None:
    """Structurally chunk *content* and write each chunk (with its embedding) to the
    ``chunks`` table, additive to the item's whole-item vector.

    Public because it is the ONE chunk-write unit: the ingest path calls it for a new item
    and ``knowledge.chunk_backfill`` calls it for every pre-chunking item. Both therefore
    go through ``store.replace_chunks``, which is what keeps the ANN index (KL-11) in step
    — a bulk writer taking any other route would leave that index stale.

    Never raises into the ingest: a chunking/embedding hiccup must not fail an item whose
    whole-item vector already landed. A chunk whose embedding degrades to None is stored
    vector-less (still FTS/keyword reachable) rather than dropped. When the embedder has
    no ``embed`` (a minimal test stub) chunk embedding is skipped, matching the graceful
    no-model path."""
    from personalclaw.knowledge.chunking import chunk_text
    from personalclaw.knowledge.embedder import floats_to_bytes

    embed_one = getattr(embedder, "embed", None)
    if not callable(embed_one):
        return
    try:
        chunks = chunk_text(content)
        for c in chunks:
            vec = None
            try:
                vec = embed_one(c.text)
            except Exception:
                vec = None
            c.embedding = floats_to_bytes(vec) if vec else None
        store.replace_chunks(item_id, chunks)
    except Exception:
        logger.debug("knowledge chunk-embed failed for %s", item_id, exc_info=True)


def _dedup(store, item_id: str, embedder) -> dict | None:
    """P12 TIER-2 semantic dedup — runs AFTER `_embed` (the vector must exist; it doesn't at
    create time in the create-fast/enrich-async model). Fetches same-type candidates carrying
    an embedding and asks the pure `dedup.resolve_duplicate` (filename + cosine + date-gate) if
    the just-enriched item duplicates one. On a confirmed dup it ARCHIVES the format-recall
    LOSER (never deletes — archived is excluded from retrieval + reversible), which may be the
    NEW item or the existing one. Returns a small verdict dict for the SSE phase, or None when
    nothing fired. Never raises into the pipeline — a dedup fault must not fail an ingest.

    Silently no-ops when the embedder is unavailable (no vector to compare) → behaves exactly
    as pre-P12. TIER-1 exact dedup (URL/byte-hash, create-time in store.py) is unaffected."""
    if not embedder or not getattr(embedder, "is_available", lambda: True)():
        return None
    try:
        from personalclaw.knowledge import dedup as dedup_mod

        item = store.get_item(item_id)
        if not item:
            return None
        # get_item strips the raw vector (→ has_embedding); read it back for the resolver.
        from personalclaw.knowledge.embedder import bytes_to_floats

        row = store.db.execute("SELECT embedding FROM items WHERE id = ?", (item_id,)).fetchone()
        raw = (
            row["embedding"]
            if row is not None and not isinstance(row, tuple)
            else (row[0] if row else None)
        )
        vec = bytes_to_floats(raw or b"")
        if not vec:
            return None  # this item has no vector → nothing to compare (behaves as today)
        # content_len is the format-recall richness signal: measured LIVE from the item's
        # current content, NOT the word_count column (which can lag the dedup stage in the
        # ingest ordering, and is 0 for a type whose body is pooled) — so the winner pick is
        # apples-to-apples + current on both sides (find_fuzzy_dup_candidates returns the
        # existing rows' LENGTH(content) the same way).
        candidate = {
            "id": item_id,
            "title": item.get("title") or "",
            "file_path": item.get("file_path") or "",
            "summary": item.get("summary") or "",
            "item_type": item.get("item_type") or "",
            "word_count": item.get("word_count", 0),
            "content_len": len(item.get("content") or ""),
            "processing_status": item.get("processing_status", ""),
            "created_at": item.get("created_at", ""),
            "embedding": vec,
        }
        for existing in store.find_fuzzy_dup_candidates(item_id):
            verdict = dedup_mod.resolve_duplicate(candidate, existing)
            if not verdict.is_dup:
                continue
            loser_id = verdict.loser_id
            winner_id = verdict.winner_id
            if not loser_id or loser_id == winner_id:
                continue
            store.update_item(loser_id, is_archived=True)
            store.db.commit()
            logger.info(
                "knowledge dedup: item %s duplicates %s (cos=%.3f, fsim=%.3f) — archived loser %s",
                item_id,
                existing.get("id"),
                verdict.cosine,
                verdict.filename_sim,
                loser_id,
            )
            return {
                "winner_id": winner_id,
                "loser_id": loser_id,
                "cosine": round(verdict.cosine, 3),
                "filename_sim": round(verdict.filename_sim, 3),
            }
        return None
    except Exception:
        logger.debug("knowledge dedup failed for %s (non-fatal)", item_id, exc_info=True)
        return None
