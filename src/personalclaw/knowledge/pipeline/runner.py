"""Ingestion runner — orchestrates one item through its node-graph (#30).

Entry point ``ingest_item``: load the item → pick its code-owned graph → execute the
DAG (each node output → the extracted-content pool) → run terminal stages over the
whole bundle (consolidated text → insights → embed) → set ``processing_status``.
Per-node + per-stage progress is broadcast over per-resource SSE so the detail view
can show live ingestion transparency.
"""

from __future__ import annotations

import json
import logging

from personalclaw.knowledge.pipeline import ensure_nodes_registered, graph_for
from personalclaw.knowledge.pipeline.executor import PipelineExecutor
from personalclaw.knowledge.pipeline.types import NodeContext
from personalclaw.knowledge.searchability import UNSEARCHABLE, reason_detail, verdict_for_ingest
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


# The terminal stages, in execution order — the ones that run AFTER the graph over the
# consolidated bundle. Owned HERE, beside the code that actually runs them, and imported
# by the graph-shape endpoint: the list was previously hand-copied into
# `dashboard/handlers/knowledge.py`, and the copy silently fell out of date (it omitted
# `dedup`, so the runner emitted a `node` SSE event for a stage the shape denied existed —
# a phase no surface could render). One list, one truth.
TERMINAL_STAGES = ("insights", "entities", "intents", "embed", "dedup")

# Which of them need an active model. `embed` has its own embedder and `dedup` is pure
# arithmetic over vectors, so neither is gated on the LLM pool (this is why both still run
# for a `raw` source, whose promise is only about the model-backed three).
MODEL_BACKED_TERMINAL_STAGES = frozenset({"insights", "entities", "intents"})


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
    (``done`` | ``partial`` | ``unsearchable`` | ``unreachable`` | ``failed``). Never
    raises — a failure is recorded on the item as ``processing_status='failed'`` +
    ``processing_error``.

    ``unsearchable`` (RET-2) means the item persisted but nothing can retrieve it: no text
    was extracted, or no vector/chunk was written. It is deliberately NOT ``done`` — see
    :mod:`personalclaw.knowledge.searchability` for the reason vocabulary.

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
        # RET-2: captured HERE, before the synthesis below — afterwards nothing downstream
        # can tell a synthesized descriptor apart from a document that genuinely says that.
        empty_success_extractors = _lying_extractors(result)
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
            # Derived from the model-backed set, not re-listed: a fourth model-backed stage
            # added later must announce its skip here without anyone remembering to edit a
            # second copy of the same list. Filtered through TERMINAL_STAGES for a stable
            # emit order (a frozenset's iteration order is not).
            for stage in TERMINAL_STAGES:
                if stage in MODEL_BACKED_TERMINAL_STAGES:
                    _emit("node", node=stage, phase="skipped")
        else:
            _emit("node", node="insights", phase="running")
            insights_ok = await _run_insights(store, item_id, consolidated, insights_pool)
            insights_phase = "done" if insights_ok else "failed"
            _emit("node", node="insights", phase=insights_phase)

            # KNOWLEDGE-SYNTHESIS §3.2 — contradictions are flagged AT INGEST. Runs here, right
            # after insights, because `insights.key_points` is the claim-shaped output this path
            # produces and the pass has nothing to compare before it exists. Deliberately NOT a
            # new terminal stage: it emits no node and cannot fail the ingest (it is an
            # annotation), so `TERMINAL_STAGES` and the phase map stay exactly as they are.
            if insights_ok:
                _run_conflict_pass(store, item_id)

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
        dedup_phase, dedup_result = _dedup(store, item_id, embedder)
        _emit("node", node="dedup", phase=dedup_phase)
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
    # Keyed by TERMINAL_STAGES so the set of stages that REPORT is the same object as the
    # set that RUNS. `dedup` used to be absent from this map entirely while the live SSE
    # stream claimed `done` for it — so on reload its phase was unknowable, and while the
    # stream was open it was a lie. `test_every_terminal_stage_reports_a_phase` is the rail:
    # it reds if TERMINAL_STAGES gains a member this mapping does not cover. Deliberately
    # NOT a runtime raise — `ingest_item` promises never to raise, and a reporting gap must
    # not become a failed ingest.
    terminal_phases = {
        "insights": insights_phase,
        "entities": entities_phase,
        "intents": intents_phase,
        "embed": embed_phase,
        "dedup": dedup_phase,
    }
    node_phases.update(terminal_phases)

    # RET-2 — the searchability verdict, computed from what actually LANDED (rows in
    # `chunks`, a vector on the item, text in the content) rather than from any stage's
    # self-report. This is the step that stops an ingest yielding nothing retrievable from
    # persisting as `done`; the reason token is written beside `node_phases` so the Doctor
    # row and `knowledge_search` read the SAME recorded fact instead of re-deriving it.
    unsearchable_reason = _searchability_reason(store, item_id, embedder, empty_success_extractors)
    meta_updates: dict[str, object] = {"node_phases": node_phases}
    if unsearchable_reason:
        meta_updates["unsearchable_reason"] = unsearchable_reason
        # Only `done`/`partial` are overridden. `failed` and `unreachable` are already loud
        # and already name their own cause — replacing them would trade a specific reason
        # for a broader one. `deleted` never reaches here.
        if status in ("done", "partial"):
            status = UNSEARCHABLE
        detail = f"{unsearchable_reason}: {reason_detail(unsearchable_reason)}"
        if not proc_error:
            proc_error = detail
        elif unsearchable_reason not in proc_error:
            # Lead with the searchability reason: an item nothing can find is the more
            # actionable fact than a skipped optional node, and the UI suppresses the
            # benign "Skipped (…)" prefix — so it must never be what a user reads first.
            proc_error = f"{detail}; {proc_error}"[:500]
    else:
        # A re-ingest that NOW lands (a provider was bound, a text version uploaded) must
        # clear the stale reason, or the item stays on the attention surface forever.
        meta_updates["unsearchable_reason"] = None
    # KOCR — a TRUNCATED read must say so on the ITEM. `pdf_rasterize` enforces the page cap
    # (ARCC `cnt_eMkU5kkpTaEk65`) and reports what it capped, but it is a structural
    # (`pooled=False`) node: its metadata feeds the next node and reaches no user-visible
    # surface, so a 120-page scan rendered exactly `MAX_OCR_PAGES` pages and then presented
    # 40 pages of text as if it were the whole document. Promoting the three facts onto
    # `file_metadata` is what makes the cap legible to the detail UI, the API and the Doctor —
    # the same promotion `node_phases` gets, and for the same reason: a ceiling nobody can see
    # is indistinguishable from no ceiling. Keys are `ocr_`-prefixed because that is the
    # namespace the item already reports OCR facts under.
    raster = result.outputs.get("pdf_rasterize")
    raster_meta = (raster.metadata or {}) if (raster and raster.success) else {}
    # `None` REMOVES the key (see `_merge_file_metadata`), so a re-ingest of a document that
    # no longer caps — a shorter version uploaded, the cap raised — stops claiming truncation.
    meta_updates["ocr_pages_capped"] = raster_meta.get("pages_capped") or None
    meta_updates["ocr_page_cap"] = raster_meta.get("page_cap") if raster_meta else None
    meta_updates["ocr_pages_rasterized"] = (
        raster_meta.get("pages_rasterized") if raster_meta else None
    )
    _merge_file_metadata(store, item_id, meta_updates)

    store.update_item(item_id, processing_status=status, processing_error=proc_error, touch=False)
    store.db.commit()
    _emit(
        "ingest_complete",
        status=status,
        ran=result.ran,
        skipped=result.skipped,
        failed=result.failed,
    )
    # APE-2: the `knowledge.ingested` platform-event emit site — deliberately the SAME
    # terminal point the SSE `ingest_complete` above fires from, so the app-facing fact and
    # the UI-facing one can never disagree. Identifiers only (item id + terminal status):
    # an app fetches the content through its own granted `api` scope, if it has one.
    from personalclaw.apps.app_events import KNOWLEDGE_INGESTED
    from personalclaw.apps.app_events import emit as emit_platform_event

    # …and only when the item actually GOT ingested: `done` (clean) or `partial` (it is in
    # the store and searchable; some optional steps were skipped). A `failed`/`unreachable`
    # run ingested nothing, and announcing it under a name that says "ingested" would make a
    # subscriber's obvious reading wrong — the `status` field is not a licence to fire the
    # wrong event. It also makes this consistent with the failure paths ABOVE, which return
    # before reaching here: no failure announces, from any exit.
    # `unsearchable` is included: the item IS in the library and its content persisted (a
    # note with no embedding provider is still keyword-reachable), so an app that never
    # heard about it would be missing a real item. Subscribers get `status` and can branch;
    # what they must never get is silence about content the user can see.
    if status in ("done", "partial", UNSEARCHABLE):
        emit_platform_event(KNOWLEDGE_INGESTED, {"item_id": item_id, "status": status})
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
    prior merge (structural metadata) in the same run isn't clobbered.

    A ``None`` value REMOVES the key rather than storing a null. Every key here records
    something a run observed, so "this run observed nothing" is the absence of the key —
    storing ``None`` would leave a re-ingest that fixed the condition still carrying the
    field that says the condition exists."""
    fresh = store.get_item(item_id) or {}
    merged = dict(fresh.get("file_metadata") or {})
    for key, value in new_keys.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    store.update_item(item_id, file_metadata=merged, touch=False)
    store.db.commit()


def _lying_extractors(result) -> list[str]:
    """Pooled nodes that reported SUCCESS and produced no text (RET-2).

    The distinction this draws is the whole basis of the ``no_extractable_text`` verdict:
    a node that reported ``done`` while yielding nothing LIED, and its item ends up
    carrying the synthesized descriptor ("Document: scan.pdf (1 pages)") as its entire
    searchable content. A node that was SKIPPED because its model is absent is a declared
    degradation the product already reports as ``partial`` — an image ingested with no
    vision model must NOT be flagged, or the verdict would fire on every graceful
    degradation and stop meaning anything.

    Non-pooled nodes are excluded because their product never reaches the text pool at all
    (``exif`` writes structural metadata), so "produced no text" is not a claim about them.

    **A node whose gap another node CLOSED has not lied** (KOCR-1). Since a text-less PDF is
    routed to rasterize → OCR, ``document_read`` returning empty on a scan is a TRUE report —
    "this PDF has no text layer" — that a downstream node then covered. Measured on a real
    ingest through the gateway before this condition existed: the item's content was the
    OCR'd text of the scan and its status simultaneously said no text could be extracted from
    it. So the set is emptied when ANY pooled node contributed text, which is precisely the
    claim ``no_extractable_text`` makes and which is then false. This cannot mask the defect
    the verdict exists for: in that case NO pooled node had text — that is what left the item
    carrying only the synthesized descriptor.
    """
    names: list[str] = []
    recovered = False
    for node_type in result.ran:
        out = result.outputs.get(node_type)
        if out is None or not getattr(out, "pooled", False):
            continue
        if (getattr(out, "text", "") or "").strip():
            recovered = True
        else:
            names.append(node_type)
    return [] if recovered else names


def _searchability_reason(store, item_id: str, embedder, empty_success_extractors) -> str | None:
    """The typed reason this item is not retrievable, or ``None`` (RET-2).

    Reads the LANDED state — a count of the item's rows in ``chunks``, whether its own
    vector column is populated, whether it has any text at all — because every stage's
    self-report is exactly what was untrustworthy: ``document_read`` said ``done`` on a
    scan it read no words from, and ``embed`` wrote zero vectors on a home with no
    embedding provider. A read failure here reports ``None`` (no verdict) rather than
    inventing a failure: this function must never be the reason an ingest looks broken.
    """
    item = store.get_item(item_id) or {}
    try:
        chunk_count = int(
            store.db.execute(
                "SELECT COUNT(*) FROM chunks WHERE item_id = ?", (item_id,)
            ).fetchone()[0]
            or 0
        )
        # Read the COLUMN, not ``get_item``: the item dict deliberately exposes only a
        # ``has_embedding`` flag (the vector never leaves the DB), so asking it for
        # ``embedding`` silently answers "absent" for every item and would report a
        # perfectly indexed library as unsearchable. LENGTH(...) rather than IS NOT NULL so
        # a zero-length blob counts as no vector, which is what it is.
        vector_row = store.db.execute(
            "SELECT COALESCE(LENGTH(embedding), 0) FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        has_item_vector = bool(vector_row and int(vector_row[0] or 0) > 0)
    except Exception:  # noqa: BLE001 — the verdict is a report, never a new failure mode
        logger.debug("searchability read failed for %s", item_id, exc_info=True)
        return None
    return verdict_for_ingest(
        chunk_count=chunk_count,
        has_item_vector=has_item_vector,
        has_text=bool((item.get("content") or "").strip()),
        embedder_bound=embedder is not None,
        empty_success_extractors=empty_success_extractors,
    )


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
                # KOCR-1: `ocr: "unavailable"` on a scanned PDF nobody can read. It belongs
                # on the item for the same reason page_count does — it is a fact ABOUT the
                # document the detail strip and `knowledge_get` have to be able to state, and
                # an empty item with no explanation is the silent-empty-ingest defect itself.
                "ocr",
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


def _grounded_aliases(ent: dict, content: str, name: str) -> list[str]:
    """The alias surfaces from one extracted entity that the item's own text actually uses.

    The extraction prompt asks for aliases because the store's alias column has four readers
    and, before this, no writer: the deterministic mention pre-pass
    (`alias_prepass.build_index`), `find_entity`'s alias fallback, the memory graph's
    `seed_from_knowledge`, and the STT lexicon's `rebuild_from_graph`. All four indexed
    canonical names only, so a document that wrote an entity by its handle or its initialism
    linked nothing, resolved to nothing, and was never boosted in transcription.

    **Grounded, not volunteered.** A model asked for aliases will also invent them, and an
    invented surface is not a harmless extra: `find_entity` resolves BY alias, so one bad
    alias silently folds a future distinct entity into this one. So the model's answer is
    treated as a *proposal* and only surfaces that literally occur in this item's text are
    kept — the same standard the pre-pass holds a mention to.

    The occurrence test is `AliasIndex` itself, not a substring scan, so "appears in the text"
    means exactly what "is a mention" means everywhere else: word-boundary token matching,
    with the index's own `MIN_ALIAS_TOKEN_LEN` floor rejecting the ambiguous short forms
    ("AI", "ML") for free rather than in a second rule that could drift from the first.

    Scanned over the model's own evidence window (`MAX_EXTRACTION_CHARS`), not the whole
    document. That is the tighter reading of "grounded" — a surface found only in text the
    model never saw did not come from the model's reading of the document — and it bounds the
    cost, since each entity gets its own index pass so that one entity's long alias cannot
    consume the tokens another's shorter one needed.
    """
    from personalclaw.knowledge.extractor import MAX_EXTRACTION_CHARS
    from personalclaw.memory_graph import AliasIndex

    raw = ent.get("aliases")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    index = AliasIndex()
    candidates: list[str] = []
    seen = {(name or "").strip().lower()}
    for entry in raw:
        if isinstance(entry, (dict, list, tuple, set)):
            continue
        surface = str(entry or "").strip()
        if not surface or surface.lower() in seen:
            continue
        seen.add(surface.lower())
        # The surface is its own id: `find` then reports which candidates the text contains.
        if index.add(surface, surface):
            candidates.append(surface)
    if not candidates:
        return []
    try:
        present = {mention.entity_id for mention in index.find(content[:MAX_EXTRACTION_CHARS])}
    except Exception:  # noqa: BLE001 — a matcher failure means "no aliases", never a lost item
        logger.debug("alias grounding failed for %s", name, exc_info=True)
        return []
    return [surface for surface in candidates if surface in present]


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
        #
        # The ALIASES travel in the snapshot for the same reason the name does. They are the
        # very surfaces the pre-pass matched on, and the entity carrying them is exactly the
        # one this clear is liable to delete — restoring the row without them would let a
        # re-ingest silently erase the alias that made the link, so the SECOND re-ingest would
        # find nothing to match. Established surfaces are not a re-run's to drop.
        prepass_links: list[tuple[str, str, str, list[str]]] = []
        try:
            for row in store.db.execute(
                "SELECT m.entity_id, e.name, e.entity_type, e.aliases, m.context "
                "FROM mentions m JOIN entities e ON e.id = m.entity_id "
                "WHERE m.item_id = ?",
                (item_id,),
            ).fetchall():
                try:
                    carried = json.loads(row["aliases"] or "[]")
                except (TypeError, ValueError):
                    carried = []
                prepass_links.append(
                    (
                        row["name"],
                        row["entity_type"] or "concept",
                        row["context"] or "",
                        carried if isinstance(carried, list) else [],
                    )
                )
        except Exception:
            logger.debug("alias pre-pass snapshot failed for %s", item_id, exc_info=True)

        store.clear_item_entities(item_id)

        # Restore. `find_entity` first, because the extractor may be about to re-create the
        # same entity and two rows for one name is worse than a lost link.
        for name, etype, context, carried in prepass_links:
            try:
                existing = store.find_entity(name)
                if existing:
                    eid = existing["id"]
                    store.merge_entity_aliases(eid, carried)
                else:
                    eid = store.add_entity(name=name, entity_type=etype, aliases=carried)
                store.add_mention(item_id, eid, context=context or None)
            except Exception:
                logger.debug("alias re-link failed for %s → %r", item_id, name, exc_info=True)
        entity_map: dict[str, str] = {}
        for ent in entities:
            name = (ent.get("name") or "").strip()
            if not name:
                continue
            aliases = _grounded_aliases(ent, content, name)
            existing = store.find_entity(name)
            if existing:
                eid = existing["id"]
                # An entity first extracted without a description can gain one from a
                # later, richer mention (no-op if it already has one). Aliases enrich the
                # same way and for the same reason — after the first ingest the entity
                # always exists, so a create-only alias write would never learn the
                # spelling THIS document introduced.
                store.backfill_entity_description(eid, ent.get("description"))
                store.merge_entity_aliases(eid, aliases)
            else:
                eid = store.add_entity(
                    name=name,
                    entity_type=ent.get("type", "concept"),
                    description=ent.get("description"),
                    aliases=aliases,
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


def _run_conflict_pass(store, item_id: str) -> None:
    """Flag contradictions between this item's claims and what is already stored (§3.2).

    Delegates to `knowledge_persist_provider.run_ingest_conflict_pass`, which is the SAME seam
    the action-provider persist path uses — the detector, the conflict record shape and the typed
    `supersedes`/`contradicts` edges are shared, not reimplemented. That indirection is the
    substantive part: detection previously had exactly one caller (the provider), so the Conflicts
    tab could never populate from UI/API/connector ingest (#329).

    Same import direction as `maintenance_passes._consolidation_pass`, and for the same reason:
    the behaviour worth sharing lives inside the provider module, and a second copy here is how
    the two paths would drift apart.
    """
    from personalclaw.action_providers import knowledge_persist_provider as kpp

    try:
        kpp.run_ingest_conflict_pass(store, item_id)
    except Exception:
        # Never fails the ingest: a missing annotation is recoverable, a lost item is not.
        logger.debug("conflict pass failed for %s", item_id, exc_info=True)


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


def active_batch_embed_fn(embedder):
    """The provider's BATCH embedding entry point for the active selection, or ``None``.

    ``None`` is not a failure — it means "this provider has no batch path", and
    ``embed_batch.embed_texts`` then falls back to the per-text fn for an identical result,
    only slower. So every caller passes both and reasons about one code path.

    Gated on *embedder* being the ``UnifiedEmbedder`` that ``create_embedder_from_config``
    builds, because that is the only embedder guaranteed to resolve the SAME active
    ``embedding`` selection the registry accessor resolves — i.e. the only one for which the
    batch fn and the embedder are the same model. An embedder the caller supplied (a test
    stub, or one handed in by an app) therefore keeps going through its own ``.embed``:
    routing it through the registry instead would write vectors from one model beside vectors
    from another in the same index, which is exactly the mixed-model corruption the
    embedding re-index path exists to prevent.

    Shared by ``embed_item_chunks`` here and ``KnowledgeStore.reembed_all`` — the two batch
    call sites — so the gate is decided once rather than re-derived per site.
    """
    from personalclaw.knowledge.embedder import UnifiedEmbedder

    if not isinstance(embedder, UnifiedEmbedder):
        return None
    try:
        from personalclaw.embedding_providers.registry import get_active_embed_many_fn

        return get_active_embed_many_fn()
    except Exception:  # noqa: BLE001 — no batch path degrades to per-text, never fails ingest
        logger.debug("embed batching: batch entry point unresolvable", exc_info=True)
        return None


def embed_item_chunks(store, item_id: str, content: str, embedder) -> None:
    """Structurally chunk *content* and write each chunk (with its embedding) to the
    ``chunks`` table, additive to the item's whole-item vector.

    Public because it is the ONE chunk-write unit: the ingest path calls it for a new item
    and ``knowledge.chunk_backfill`` calls it for every pre-chunking item. Both therefore
    go through ``store.replace_chunks``, which is what keeps the ANN index (KL-11) in step
    — a bulk writer taking any other route would leave that index stale.

    All of an item's chunks are embedded in ONE pass through ``embed_batch.embed_texts``
    (KL-15) rather than one provider call per chunk. Two things change beyond the round
    trips: a transient failure is now retried with backoff instead of being swallowed by an
    inline ``except Exception: vec = None``, and a group that fails in a batch-shaped way is
    bisected, so a provider's undeclared ceiling costs a split rather than an item's whole
    chunk layer. The old inline swallow left an unembeddable library indistinguishable from a
    working one, with no log line anywhere.

    Never raises into the ingest: a chunking/embedding hiccup must not fail an item whose
    whole-item vector already landed. A chunk whose embedding degrades to None is stored
    vector-less (still FTS/keyword reachable) rather than dropped. When the embedder has
    no ``embed`` (a minimal test stub) chunk embedding is skipped, matching the graceful
    no-model path."""
    from personalclaw.knowledge.chunking import chunk_text
    from personalclaw.knowledge.embed_batch import embed_texts
    from personalclaw.knowledge.embedder import floats_to_bytes

    embed_one = getattr(embedder, "embed", None)
    if not callable(embed_one):
        return
    try:
        chunks = chunk_text(content)
        if chunks:
            # `embed_texts` returns exactly one result per text, positionally aligned, so
            # this zip can neither drop a chunk nor attach one chunk's vector to another.
            vectors = embed_texts(
                [c.text for c in chunks],
                embed_many=active_batch_embed_fn(embedder),
                embed_one=embed_one,
            )
            for c, vec in zip(chunks, vectors):
                c.embedding = floats_to_bytes(vec) if vec else None
        # Outside the `if`: an empty chunk list still has to reach `replace_chunks`, which is
        # what clears a previous generation's rows when a re-chunk yields nothing.
        store.replace_chunks(item_id, chunks)
    except Exception:
        logger.debug("knowledge chunk-embed failed for %s", item_id, exc_info=True)


def _dedup(store, item_id: str, embedder) -> tuple[str, dict | None]:
    """P12 TIER-2 semantic dedup — runs AFTER `_embed` (the vector must exist; it doesn't at
    create time in the create-fast/enrich-async model). Fetches same-type candidates carrying
    an embedding and asks the pure `dedup.resolve_duplicate` (filename + cosine + date-gate) if
    the just-enriched item duplicates one. On a confirmed dup it ARCHIVES the format-recall
    LOSER (never deletes — archived is excluded from retrieval + reversible), which may be the
    NEW item or the existing one. Never raises into the pipeline — a dedup fault must not fail
    an ingest.

    Returns ``(phase, verdict)``:

    * ``skipped`` — the comparison never happened because a PREREQUISITE was absent: no
      embedder (embeddings disabled), an unavailable one, the item gone, or the item has no
      vector. Nothing was compared, so nothing can be claimed. Same word, same meaning, and
      the same triggering condition as ``_embed``'s — that is the point.
    * ``done`` — the comparison actually ran against the candidate set. A run that compared
      and found no duplicate is a real, completed pass (mirroring ``_run_intents_stage``,
      which reports ``done`` when the intents ran and nothing matched) — so ``verdict`` is
      ``None`` for "no dup" and a dict for a confirmed one.
    * ``failed`` — the attempt errored.

    *verdict* stays a separate return value rather than being inferred from the phase because
    "the stage ran" and "the stage found something" are different facts, and collapsing them
    is exactly the conflation that made this stage report ``done`` for a no-op (#481). The
    phase was previously hardcoded ``done`` at the call site, so an instance with embeddings
    OFF reported a dedup pass it had never performed.

    TIER-1 exact dedup (URL/byte-hash, create-time in store.py) is unaffected."""
    if not embedder or not getattr(embedder, "is_available", lambda: True)():
        return "skipped", None
    try:
        from personalclaw.knowledge import dedup as dedup_mod

        item = store.get_item(item_id)
        if not item:
            return "skipped", None
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
            # No vector → there is nothing to compare against. The stage did not run.
            return "skipped", None
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
            return "done", {
                "winner_id": winner_id,
                "loser_id": loser_id,
                "cosine": round(verdict.cosine, 3),
                "filename_sim": round(verdict.filename_sim, 3),
            }
        # The candidate set was walked and nothing duplicated this item — a completed pass.
        return "done", None
    except Exception:
        logger.debug("knowledge dedup failed for %s (non-fatal)", item_id, exc_info=True)
        return "failed", None
