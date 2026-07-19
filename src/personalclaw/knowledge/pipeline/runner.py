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

logger = logging.getLogger(__name__)

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
        graph = graph_for(item_type)
    except Exception as exc:
        logger.exception("graph build failed for %s", item_type)
        store.update_item(item_id, processing_status="failed", processing_error=str(exc), touch=False)
        store.db.commit()
        _emit("ingest_failed", error=str(exc))
        return "failed"

    ctx = NodeContext(
        item_id=item_id, item_type=item_type,
        file_path=item.get("file_path") or "", content=item.get("content") or "",
        url=item.get("url") or "",
    )
    executor = PipelineExecutor(
        graph, params_for=params_for,
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
                item_id, out.node_type, backend=out.backend, text=out.text, metadata=out.metadata,
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
            fresh = store.get_item(item_id) or item  # _persist_structural_metadata just merged file_metadata
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
        _emit("node", node="insights", phase="running")
        insights_ok = await _run_insights(store, item_id, consolidated, insights_pool)
        _emit("node", node="insights", phase="done" if insights_ok else "failed")

        # Entity/relation extraction over the consolidated text → the entity graph
        # (one logical doc = one extraction; no per-chunk fan-out).
        _emit("node", node="entities", phase="running")
        await _run_entities_stage(store, item_id, consolidated, insights_pool)
        _emit("node", node="entities", phase="done")

        # Tier-3 intent matching — natural-language user intents run against the
        # consolidated text; relevant matches are recorded as intent_outcomes by value.
        _emit("node", node="intents", phase="running")
        await _run_intents_stage(store, item_id, item_type, consolidated, insights_pool)
        _emit("node", node="intents", phase="done")

        # Terminal: embed (title + summary), reusing the existing embedder path.
        _emit("node", node="embed", phase="running")
        _embed(store, item_id, embedder)
        _emit("node", node="embed", phase="done")

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
        logger.exception("knowledge ingest failed mid-pipeline for %s", item_id)
        store.update_item(item_id, processing_status="failed", processing_error=str(exc)[:500], touch=False)
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
            out = result.outputs.get(nt)
            err = (getattr(out, "error", "") or "").strip() if out else ""
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
        node_phases.setdefault(nt, "skipped")  # never reached (branch not taken)
    node_phases["insights"] = "done" if insights_ok else "failed"
    for nt in ("entities", "intents", "embed"):
        node_phases[nt] = "done"
    _merge_file_metadata(store, item_id, {"node_phases": node_phases})

    store.update_item(item_id, processing_status=status, processing_error=proc_error, touch=False)
    store.db.commit()
    _emit("ingest_complete", status=status, ran=result.ran, skipped=result.skipped, failed=result.failed)
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
    doc = result.outputs.get("document_read")
    if doc is not None and getattr(doc, "success", False) and getattr(doc, "metadata", None):
        shape = {k: v for k, v in doc.metadata.items()
                 if k in ("page_count", "format", "sheet_count", "slide_count",
                          "row_count", "paragraph_count") and v is not None}
        if shape:
            merged = dict(fields.get("file_metadata") or (item or {}).get("file_metadata") or {})
            merged.update(shape)
            fields["file_metadata"] = merged

    # Bookmark scrape → derived link-card title/description onto the item.
    scrape = result.outputs.get("bookmark_scrape")
    if scrape is not None and getattr(scrape, "success", False) and getattr(scrape, "metadata", None):
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
        title_is_url_placeholder = not cur_title or normalize_url(cur_title) == normalize_url(cur_url)
        if scraped_title and title_is_url_placeholder:
            fields["title"] = scraped_title

    if fields:
        store.update_item(item_id, touch=False, **fields)
        store.db.commit()


async def _run_entities_stage(store, item_id: str, content: str, pool) -> None:
    """Extract entities + relations from the item's consolidated text and write them
    to the entity graph (entities + mentions + entity_relations). Re-runs cleanly:
    clears this item's prior mentions/relations first so a re-ingest doesn't dup."""
    if not content.strip() or pool is None:
        return
    try:
        from personalclaw.knowledge.extractor import EntityExtractor

        extraction = await EntityExtractor(pool=pool).extract(content)
    except Exception:
        logger.debug("entity extraction failed for %s", item_id, exc_info=True)
        return
    entities = extraction.get("entities") or []
    relations = extraction.get("relations") or []
    if not entities:
        return
    try:
        store.clear_item_entities(item_id)
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
                    name=name, entity_type=ent.get("type", "concept"),
                    description=ent.get("description"),
                )
            entity_map[name] = eid
            store.add_mention(item_id, eid, context=ent.get("description"))
        for rel in relations:
            src = entity_map.get((rel.get("source") or "").strip())
            tgt = entity_map.get((rel.get("target") or "").strip())
            if src and tgt:
                store.add_entity_relation(
                    source_id=src, target_id=tgt,
                    relation_type=rel.get("type", "uses") or "uses",
                    description=rel.get("description"), source_item_id=item_id,
                )
        store.db.commit()
        # Rebuild the in-memory graph so cleared edges drop and the new ones show.
        store._load_graph()
    except Exception:
        logger.debug("entity graph write failed for %s", item_id, exc_info=True)


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
    # AI-generated values are identified by matching the PREVIOUS enrichment's output:
    # insights.topics/summary always reflect the content they were extracted from, so
    # if the item's current tags/summary still equal those, they're AI-seeded and
    # untouched → refresh them on a re-ingest. If they differ, the user edited them →
    # preserve. This keeps a content edit from leaving stale AI tags/summary while
    # never clobbering user-authored ones.
    prev_topics = [t for t in (prev_insights.get("topics") or []) if isinstance(t, str)]
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
        is_file_type = item_type in ("image", "audio", "video", "pdf", "document", "sheet", "slides")
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
        orig_fn = str(((item or {}).get("file_metadata") or {}).get("original_filename") or "").strip()
        titled_by_filename = cur_title == orig_fn if orig_fn else True
        if (is_file_type and titled_by_filename) or not cur_title or titled_by_content:
            fields["title"] = ai_title
    # AI tags come from the extracted topics. Set them when the item has none (first
    # enrichment) OR when its current tags still equal the previous enrichment's topics
    # (AI-seeded + untouched → refresh on a content edit). User-authored tags (which
    # differ from prev_topics) are preserved.
    cur_tags = [t for t in ((item or {}).get("tags") or []) if isinstance(t, str)]
    topics = [t for t in (insights.get("topics") or []) if isinstance(t, str) and t.strip()]
    if topics and (not cur_tags or cur_tags == prev_topics):
        fields["tags"] = topics
    store.update_item(item_id, touch=False, **fields)
    store.db.commit()
    return True


def _intents_path(store):
    """The intents.json sibling of the knowledge DB (per-store, cwd-partition model)."""
    from pathlib import Path

    db_path = getattr(store, "db_path", "") or ""
    return Path(db_path).parent / "intents.json" if db_path else Path("intents.json")


async def _run_intents_stage(store, item_id: str, item_type: str, content: str, pool) -> None:
    """Run Tier-3 user intents over the consolidated content. Each relevant match is
    persisted as an outcome BY VALUE in the intent_outcomes table, with only a soft
    back-reference to this item — so the gathered insight survives item deletion."""
    if not content.strip():
        return
    try:
        from personalclaw.knowledge.intents import IntentStore, run_intents

        intents = IntentStore(_intents_path(store)).load()
        if not intents:
            return
        matches = await run_intents(intents, item_type, content, pool=pool)
    except Exception:
        logger.debug("intent stage failed for %s", item_id, exc_info=True)
        return
    # Clear this item's prior outcomes before recording the current matches, so a
    # re-ingest of edited content can't leave a stale outcome from the old content
    # (e.g. an item that no longer matches an intent it once did). Outcomes orphaned
    # by a deleted item (item_id NULL) are preserved — only THIS item's are cleared.
    store.clear_item_intent_outcomes(item_id)
    if not matches:
        return
    item = store.get_item(item_id)
    item_title = (item or {}).get("title") or (item or {}).get("ai_title") or ""
    by_id = {i.id: i for i in intents}
    for m in matches:
        try:
            store.record_intent_outcome(
                m.intent_id, intent_name=(by_id.get(m.intent_id).goal if by_id.get(m.intent_id) else ""),
                item_id=item_id, item_title=item_title,
                takeaway=m.takeaway, fields=m.fields,
            )
        except Exception:
            logger.debug("recording outcome for intent %s failed", m.intent_id, exc_info=True)


def _embed(store, item_id: str, embedder) -> None:
    if not embedder:
        return
    try:
        from personalclaw.knowledge.embedder import floats_to_bytes

        item = store.get_item(item_id)
        if not item:
            return
        # Embed title + summary, anchored by a body slice when the summary is thin —
        # a title-only vector gives poor semantic recall (see compose_item_text).
        vec = embedder.embed_for_item(
            item.get("title") or "", item.get("summary"), item.get("content"),
        )
        if vec:
            store.db.execute("UPDATE items SET embedding = ? WHERE id = ?", (floats_to_bytes(vec), item_id))
            store.db.commit()
    except Exception:
        logger.debug("knowledge embed failed for %s", item_id, exc_info=True)


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
        raw = (row["embedding"] if row is not None and not isinstance(row, tuple) else (row[0] if row else None))
        vec = bytes_to_floats(raw or b"")
        if not vec:
            return None  # this item has no vector → nothing to compare (behaves as today)
        # content_len is the format-recall richness signal: measured LIVE from the item's
        # current content, NOT the word_count column (which can lag the dedup stage in the
        # ingest ordering, and is 0 for a type whose body is pooled) — so the winner pick is
        # apples-to-apples + current on both sides (find_fuzzy_dup_candidates returns the
        # existing rows' LENGTH(content) the same way).
        candidate = {
            "id": item_id, "title": item.get("title") or "", "file_path": item.get("file_path") or "",
            "summary": item.get("summary") or "", "item_type": item.get("item_type") or "",
            "word_count": item.get("word_count", 0), "content_len": len(item.get("content") or ""),
            "processing_status": item.get("processing_status", ""),
            "created_at": item.get("created_at", ""), "embedding": vec,
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
            logger.info("knowledge dedup: item %s duplicates %s (cos=%.3f, fsim=%.3f) — archived loser %s",
                        item_id, existing.get("id"), verdict.cosine, verdict.filename_sim, loser_id)
            return {"winner_id": winner_id, "loser_id": loser_id,
                    "cosine": round(verdict.cosine, 3), "filename_sim": round(verdict.filename_sim, 3)}
        return None
    except Exception:
        logger.debug("knowledge dedup failed for %s (non-fatal)", item_id, exc_info=True)
        return None
