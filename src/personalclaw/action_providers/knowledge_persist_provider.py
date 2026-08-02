"""``knowledge-persist`` action provider — write to the knowledge store from a workflow.

A synthesis workflow's whole point is to leave something behind. Without this provider the
only way to do that is a `stage` — a subagent session spawned to call a tool that writes a
row — which costs a model call and a lane slot for work the engine has already resolved.

So this is a **zero-token** node, and it is where session 34's semantics become behaviour:

**Idempotent by construction.** The logical key and content hash are derived from what is
being written, so a retried, resumed or rewound persist recomputes the same identity and
becomes a no-op returning the existing id. None of those three paths needs to know the
others exist — which is what makes a nightly synthesis loop safe to re-run.

**A budget overrun is a RETURNED failure, not an exception.** The engine's retry semantics
can act on `{success: false, error: "over budget by N — condense and retry"}`; an exception
just kills the node and loses the work. Same for a missing citation on a synthesized kind.

**Duplicate content reinforces.** Re-persisting the same claim appends a mention and
re-aggregates confidence rather than inserting a second article, so corroboration
strengthens what is stored instead of cluttering it.

**Enrichment is fire-and-forget.** The row is written synchronously; embedding and entity
extraction ride the existing ingest queue. A synthesis stage should not wait on an embedder
that may not even be configured.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.knowledge.semantics import (
    Claim,
    Mention,
    check_persist,
    decide_write,
)

logger = logging.getLogger(__name__)

#: `item_type` routes the INGESTION graph and is a separate axis from `kind`. A knowledge
#: item written by a workflow is a note unless the caller says otherwise — the taxonomy that
#: matters for retrieval is `kind`.
DEFAULT_ITEM_TYPE = "note"


class KnowledgePersistActionProvider(ActionProvider):
    """Persist a knowledge item. Zero tokens, idempotent, error-as-return.

    ``action_config`` shape::

        {
            "title": "Cold start latency",   # required; half the logical identity
            "content": "…",                  # required; the body
            "kind": "fact",                  # optional; the typed taxonomy (default fact)
            "summary": "…",                  # optional; one line, for retrieval
            "tags": ["perf"],                # optional
            "claims": [{...}],               # optional; structured claims
            "citations": ["trace-1"],        # required for insight|report|overview
            "unsourced": false,              # explicit opt-out of the citation rule
            "source_ref": "…",               # auto-filled from the run when absent
            "read_when": ["…"],              # optional retrieval triggers (KNOW-R12)
            "ttl": "30d",                    # optional; becomes an absolute expires_at
            "mode": "upsert"                 # create|upsert|append_evidence
        }
    """

    @property
    def name(self) -> str:
        return "knowledge-persist"

    @property
    def display_name(self) -> str:
        return "Persist Knowledge"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        started = time.monotonic()
        cfg = action_config or {}

        title = str(cfg.get("title", "") or "").strip()
        content = cfg.get("content")
        if content is None:
            # `is None`, not falsy: an empty body is legitimate for a `probe` or a stub
            # overview, and treating "" as missing would silently skip that write.
            return ActionResult(
                success=False,
                error="knowledge-persist is missing 'content' — bind it to a node's output",
            )
        body = content if isinstance(content, str) else _stringify(content)

        claims_raw = cfg.get("claims") or []
        check = check_persist(
            kind=str(cfg.get("kind", "fact") or "fact"),
            title=title,
            content=body,
            summary=str(cfg.get("summary", "") or ""),
            claims=[c for c in claims_raw if isinstance(c, dict)],
            citations=[str(c) for c in (cfg.get("citations") or [])],
            unsourced=bool(cfg.get("unsourced")),
            ttl=str(cfg.get("ttl", "") or ""),
            expires_at=str(cfg.get("expires_at", "") or ""),
        )
        if not check.ok:
            # Returned, never raised: the engine can retry a condense-and-shorten, and the
            # error text is written to be actionable by the model that will read it.
            return ActionResult(success=False, error=check.error)

        try:
            store = _open_store()
        except Exception as exc:  # pragma: no cover — environmental
            return ActionResult(success=False, error=f"knowledge store unavailable: {exc}")

        mode = str(cfg.get("mode", "upsert") or "upsert")
        existing_id, existing_hash, existing_meta = _lookup(store, check.logical_key)
        decision = decide_write(
            logical_key=check.logical_key,
            content_hash=check.content_hash,
            existing_id=existing_id,
            existing_hash=existing_hash,
            mode=mode,
        )

        if decision.action == "noop":
            return ActionResult(
                success=True,
                stdout=json.dumps(
                    {
                        "item_id": decision.item_id,
                        "logical_key": check.logical_key,
                        "created": False,
                        "mentions_appended": 0,
                        "reason": decision.reason,
                    }
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        source_ref = str(cfg.get("source_ref", "") or _run_source_ref(ctx))
        metadata = dict(existing_meta)
        appended = 0

        conflicts: list[dict] = []
        if claims_raw:
            incoming_claims = [c for c in claims_raw if isinstance(c, dict)]
            # Conflict check BEFORE the merge, against what is stored NEARBY (§3.2). At ingest,
            # not at query: by the time a contradiction surfaces during retrieval, something has
            # already cited one side of it, and unwinding that means finding everything
            # downstream. This tier is deterministic and costs nothing per write.
            conflicts = _detect_conflicts(
                store,
                incoming_claims,
                item_id=decision.item_id,
                source_ref=source_ref,
                # The item id this write will land on. The claim's own `source_ref` is RUN
                # provenance ("workflow:node:n1"), NOT a row id — measured, using it made
                # `edges_from_conflicts` build an edge whose source was not an item, so the
                # foreign key silently wrote nothing while the conflict record looked fine. The
                # two surfaces then disagreed about whether the store knew about the conflict.
                edge_source=decision.item_id or "",
            )
            merged, appended = _merge_claims(
                existing=metadata.get("claims") or [],
                incoming=incoming_claims,
                source_ref=source_ref,
            )
            metadata["claims"] = merged
            if conflicts:
                # Recorded ON the item, and BOTH claims kept. Silently picking a winner is how a
                # store becomes confidently wrong: the discarded claim was evidence, and its
                # absence is unrecoverable.
                metadata["conflicts"] = conflicts

        if decision.action == "reinforce":
            # Same content, `append_evidence`: record the corroboration and leave the body
            # alone. Rewriting an identical body would bump `updated_at` and make a
            # freshness check think the article had changed.
            _write_metadata(store, decision.item_id, metadata, source_ref=source_ref)
            return ActionResult(
                success=True,
                stdout=json.dumps(
                    {
                        "item_id": decision.item_id,
                        "logical_key": check.logical_key,
                        "created": False,
                        "mentions_appended": appended,
                        "conflicts": conflicts,
                        "reason": decision.reason,
                    }
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        # `lineage` carries a consolidation pass's `parent_ids` / `reflection_count` /
        # `compression_ratio`. A NAMED key rather than an open `metadata` passthrough: an open
        # dict would let any caller overwrite `claims` or `logical_key`, and a caller that
        # silently clobbered the claim ledger would be indistinguishable from one that never
        # wrote it. (Measured: passing `metadata=` was silently DROPPED here, so the whole
        # lineage the consolidation pass depends on never reached the row.)
        raw_lineage = cfg.get("lineage")
        if isinstance(raw_lineage, dict):
            for key in (
                "parent_ids",
                "reflection_count",
                "consolidated",
                "compression_ratio",
                "source_count",
            ):
                if key in raw_lineage:
                    metadata[key] = raw_lineage[key]

        for key in ("read_when", "citations", "source", "extraction"):
            if cfg.get(key):
                metadata[key] = cfg[key]

        item_id = decision.item_id or uuid.uuid4().hex[:12]
        try:
            _upsert_item(
                store,
                item_id=item_id,
                title=title,
                content=body,
                summary=str(cfg.get("summary", "") or ""),
                kind=check.normalized_kind,
                logical_key=check.logical_key,
                content_hash=check.content_hash,
                expires_at=check.expires_at,
                metadata=metadata,
                tags=[str(t) for t in (cfg.get("tags") or [])],
                creating=decision.action == "create",
            )
        except Exception as exc:
            return ActionResult(success=False, error=f"knowledge write failed: {exc}")

        if conflicts:
            # NOW the row exists, so the edges can satisfy the foreign key. Targets come from the
            # conflict records rather than being recomputed — recomputing could find a different
            # neighbour set and produce edges that do not match the recorded conflicts.
            _write_conflict_edges(store, conflicts, source_item=item_id)
        _enqueue_enrichment(item_id)
        return ActionResult(
            success=True,
            stdout=json.dumps(
                {
                    "item_id": item_id,
                    "logical_key": check.logical_key,
                    "created": decision.action == "create",
                    "mentions_appended": appended,
                    "conflicts": conflicts,
                    "reason": decision.reason,
                }
            ),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


# ── store access ──


def _open_store():
    """Open the ONE global knowledge store.

    Knowledge has no partitions by design: a fact learned in one workspace is still true in
    another, and partitioning would mean the same article re-derived per directory.
    """
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    # Through `knowledge_db_path`, never a locally composed path. Measured live: composing it here
    # produced `<home>/knowledge/knowledge.db` while the dashboard reads
    # `<home>/workspace/knowledge/knowledge.db`, so workflow-persisted knowledge landed in a
    # second database the UI could never see — with no error on either side.
    return KnowledgeStore(db_path=str(knowledge_db_path()))


def _lookup(store, logical_key: str) -> tuple[str, str, dict[str, Any]]:
    """Lookup-before-write. Returns (id, content_hash, file_metadata).

    This is the query the logical-key index exists for — it runs on every persist.
    """
    if not logical_key:
        return "", "", {}
    try:
        rows = list(
            store.db.execute(
                "SELECT id, content_hash, file_metadata FROM items WHERE logical_key = ? LIMIT 1",
                (logical_key,),
            )
        )
    except Exception:
        logger.debug("knowledge lookup failed", exc_info=True)
        return "", "", {}
    if not rows:
        return "", "", {}
    row = rows[0]
    try:
        meta = json.loads(row["file_metadata"] or "{}")
    except (TypeError, ValueError):
        meta = {}
    return str(row["id"]), str(row["content_hash"] or ""), meta if isinstance(meta, dict) else {}


def _upsert_item(
    store,
    *,
    item_id: str,
    title: str,
    content: str,
    summary: str,
    kind: str,
    logical_key: str,
    content_hash: str,
    expires_at: str,
    metadata: dict[str, Any],
    tags: list[str],
    creating: bool,
) -> None:
    """Write the row. `created_at` is preserved on update — an article's birthday does not
    change because it was edited."""
    now = _now()
    blob = json.dumps(metadata, ensure_ascii=False)
    # BEFORE the write: what the FTS index currently holds for this row.
    prior = None if creating else _fts_snapshot(store, item_id)
    if creating:
        store.db.execute(
            "INSERT INTO items (id, item_type, title, content, summary, created_at, updated_at, "
            "kind, logical_key, content_hash, expires_at, last_verified, file_metadata) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                item_id,
                DEFAULT_ITEM_TYPE,
                title,
                content,
                summary,
                now,
                now,
                kind,
                logical_key,
                content_hash,
                expires_at,
                now,
                blob,
            ),
        )
    else:
        store.db.execute(
            "UPDATE items SET title=?, content=?, summary=?, updated_at=?, kind=?, "
            "logical_key=?, content_hash=?, expires_at=?, last_verified=?, file_metadata=? "
            "WHERE id=?",
            (
                title,
                content,
                summary,
                now,
                kind,
                logical_key,
                content_hash,
                expires_at,
                now,
                blob,
                item_id,
            ),
        )
    store.db.commit()
    _write_tags(store, item_id, tags)
    _sync_fts(store, item_id, title=title, content=content, creating=creating, prior=prior)


def _write_tags(store, item_id: str, tags: list[str]) -> int:
    """Attach tags through the store's own tag tables. Returns how many attached.

    Both tables have NOT NULL timestamp columns (`tags.created_at`, `item_tags.added_at`) —
    omitting them made every insert fail, and the broad `except` below swallowed it so
    cleanly that a measurement run showed `tags: []` with no error anywhere. Best-effort is
    right here (a tag that will not attach must not lose the article it described) but it
    has to be best-effort about something that WORKS, so the count is returned and logged at
    WARNING rather than debug.
    """
    if not tags:
        return 0
    attached = 0
    now = _now()
    try:
        for tag in tags:
            name = tag.strip().lower()
            if not name:
                continue
            store.db.execute(
                "INSERT OR IGNORE INTO tags (name, created_at) VALUES (?, ?)", (name, now)
            )
            row = list(store.db.execute("SELECT id FROM tags WHERE name = ?", (name,)))
            if row:
                store.db.execute(
                    "INSERT OR IGNORE INTO item_tags (item_id, tag_id, source, added_at) "
                    "VALUES (?, ?, 'workflow', ?)",
                    (item_id, row[0]["id"], now),
                )
                attached += 1
        store.db.commit()
    except Exception:
        logger.warning("knowledge tag write failed for %s", item_id, exc_info=True)
    if tags and not attached:
        logger.warning("knowledge item %s: none of %d tag(s) attached", item_id, len(tags))
    return attached


def _fts_snapshot(store, item_id: str) -> tuple[int, str, str, str] | None:
    """The values currently INDEXED for this item, read before the row is rewritten.

    This has to happen first: `items_fts_src` is a VIEW over the live row, so reading it
    after the UPDATE returns the NEW content — and a delete keyed on the new values removes
    nothing, leaving the old terms searchable forever. Measured: "aardvark" still matched
    after the body had been replaced by "buffalo".
    """
    try:
        rows = list(
            store.db.execute(
                "SELECT rowid, title, content, tags FROM items_fts_src WHERE rowid = "
                "(SELECT rowid FROM items WHERE id = ?)",
                (item_id,),
            )
        )
    except Exception:
        return None
    if not rows:
        return None
    row = rows[0]
    return (
        int(row["rowid"]),
        str(row["title"] or ""),
        str(row["content"] or ""),
        str(row["tags"] or ""),
    )


def _sync_fts(
    store,
    item_id: str,
    *,
    title: str,
    content: str,
    creating: bool,
    prior: tuple[int, str, str, str] | None = None,
) -> None:
    """Keep `items_fts` in step with a direct SQL write.

    `items_fts` is an EXTERNAL-CONTENT fts5 index over a view, and it has NO triggers — so a
    row inserted with plain SQL is simply not searchable. Measured: every retrieve fell
    through to `substring_fallback` until this existed, which looks identical in the output
    to a working search and would have made the whole retrieval tier quietly useless.

    Delete-then-insert, following the store's own manual sync sites. Rebuilding is
    deliberately NOT used: the store's own docstring records that `'rebuild'` against a stale
    content target WIPES THE INDEX AND REPORTS SUCCESS.
    """
    try:
        row = list(store.db.execute("SELECT rowid FROM items WHERE id = ?", (item_id,)))
        if not row:
            return
        rowid = row[0][0]
        tags_row = list(
            store.db.execute(
                "SELECT COALESCE(group_concat(t.name, ' '), '') FROM item_tags it "
                "JOIN tags t ON t.id = it.tag_id WHERE it.item_id = ?",
                (item_id,),
            )
        )
        tag_text = str(tags_row[0][0] or "") if tags_row else ""
        if not creating and prior is not None:
            # Delete with the values the index actually HOLDS — captured before the row was
            # rewritten. fts5 external-content deletes are keyed on the column values, so a
            # delete with the new content is a no-op that silently leaves the old terms
            # searchable.
            store.db.execute(
                "INSERT INTO items_fts (items_fts, rowid, title, content, tags) "
                "VALUES ('delete', ?, ?, ?, ?)",
                (prior[0], prior[1], prior[2], prior[3]),
            )
        store.db.execute(
            "INSERT INTO items_fts (rowid, title, content, tags) VALUES (?, ?, ?, ?)",
            (rowid, title, content, tag_text),
        )
        store.db.commit()
    except Exception:
        logger.warning(
            "knowledge FTS sync failed for %s — it will not be searchable", item_id, exc_info=True
        )


def _write_metadata(store, item_id: str, metadata: dict[str, Any], *, source_ref: str) -> None:
    """Update only the metadata blob and `last_verified` — used by the reinforce path.

    Deliberately does NOT touch `updated_at`: the body did not change, and bumping it would
    make a freshness check believe the article had been rewritten.
    """
    try:
        store.db.execute(
            "UPDATE items SET file_metadata=?, last_verified=? WHERE id=?",
            (json.dumps(metadata, ensure_ascii=False), _now(), item_id),
        )
        store.db.commit()
    except Exception:
        logger.debug("knowledge metadata write failed", exc_info=True)


def _detect_conflicts(
    store, incoming: list[dict], *, item_id: str, source_ref: str, edge_source: str = ""
) -> list[dict]:
    """Deterministic conflicts between arriving claims and stored ones, plus their edges.

    Neighbours come from the hybrid retriever rather than a recency window, because "the claims
    most likely to disagree with this one" is a similarity question and recency is a proxy that
    misses an old contradicted fact entirely — which is the case that matters most, since it has
    had the longest time to be cited.

    Best-effort by design: a conflict pass that failed a WRITE would mean losing the knowledge
    rather than losing the annotation, and the annotation is the cheaper thing to lose.
    """
    try:
        from personalclaw.knowledge import contradiction

        claims = [
            contradiction.Claim.from_dict({**c, "source_ref": c.get("source_ref") or source_ref})
            for c in incoming
        ]
        existing = _neighbour_claims(store, claims, exclude=item_id)
        if not existing:
            return []
        found = contradiction.find_conflicts(claims, existing)
        if not found:
            return []
        # Edges are deferred when the row does not exist yet: a CREATE has no id until the insert,
        # and an edge written against a missing row is refused by the foreign key. The caller
        # writes them after the upsert.
        if edge_source:
            _write_edges(store, contradiction.edges_from_conflicts(found), source_item=edge_source)
        return [c.to_dict() for c in found]
    except Exception:
        logger.warning("conflict detection failed — the write proceeds", exc_info=True)
        return []


def _neighbour_claims(store, incoming: list, *, exclude: str) -> list:
    """Claims on items semantically near the incoming ones.

    Capped, and the SAME item is excluded: an item's own stored claims are what the merge path
    reconciles, and reporting them here would flag every update as a conflict with itself.
    """
    from personalclaw.knowledge import contradiction

    queries = [c.statement for c in incoming if c.statement][:3]
    if not queries:
        return []

    # Two sources, and BOTH are needed. FTS is precise but only sees title/content/tags — a
    # claim lives in `file_metadata`, so an FTS search for claim words finds nothing unless the
    # claim happens to echo the item's title. The claim-bearing scan is the reliable half.
    candidate_ids: list[str] = []
    seen_items: set[str] = set()
    for query in queries:
        for row in _search_rows(store, query):
            item_id = str(row.get("id", "") or "")
            if item_id and item_id != exclude and item_id not in seen_items:
                seen_items.add(item_id)
                candidate_ids.append(item_id)
    for item_id in _claim_bearing_ids(store, exclude=exclude):
        if item_id not in seen_items:
            seen_items.add(item_id)
            candidate_ids.append(item_id)

    out: list = []
    for item_id in candidate_ids:
        for raw in _stored_claims(store, item_id):
            claim = contradiction.Claim.from_dict({**raw, "source_ref": item_id})
            if claim.statement:
                out.append(claim)
        if len(out) >= contradiction.MAX_CONFLICT_CANDIDATES:
            return out[: contradiction.MAX_CONFLICT_CANDIDATES]
    return out


def _claim_bearing_ids(store, *, exclude: str, limit: int = 40) -> list[str]:
    """Recently-updated items that carry claims at all.

    A LIKE prefilter, newest first, bounded. This is the half of the candidate set that actually
    works: conflicts are between CLAIMS, and claims are not in the search index, so an
    FTS-only neighbour set silently returns nothing for any item whose title does not repeat its
    own claim text. Recency is the right order here because a contradiction with something
    written last week is more actionable than one with a two-year-old note, and the cap keeps
    the marginal cost independent of store size.
    """
    try:
        return [
            str(r["id"])
            for r in store.db.execute(
                "SELECT id FROM items WHERE is_archived = 0 AND id != ? "
                "AND file_metadata LIKE '%\"claims\"%' ORDER BY updated_at DESC LIMIT ?",
                (exclude or "", max(1, limit)),
            )
        ]
    except Exception:
        logger.debug("claim-bearing scan failed", exc_info=True)
        return []


def _search_rows(store, query: str) -> list[dict]:
    try:
        return [
            dict(r)
            for r in store.db.execute(
                "SELECT i.id FROM items_fts f JOIN items i ON i.rowid = f.rowid "
                "WHERE items_fts MATCH ? AND i.is_archived = 0 LIMIT 10",
                (_fts_safe(query),),
            )
        ]
    except Exception:
        logger.debug("neighbour search failed", exc_info=True)
        return []


def _fts_safe(query: str) -> str:
    """An FTS5 MATCH expression from arbitrary claim text.

    A claim is prose, and prose contains FTS5 operators — an unquoted `4.2s (measured)` is a
    syntax error, and the broad except above would swallow it into "no neighbours", which reads
    exactly like "no conflicts". So the terms are extracted and OR-ed explicitly.
    """
    terms = re.findall(r"[A-Za-z0-9]{3,}", query or "")
    return " OR ".join(terms[:12]) if terms else '""'


def _stored_claims(store, item_id: str) -> list[dict]:
    try:
        rows = list(store.db.execute("SELECT file_metadata FROM items WHERE id = ?", (item_id,)))
    except Exception:
        return []
    if not rows:
        return []
    try:
        meta = json.loads(rows[0]["file_metadata"] or "{}")
    except (TypeError, ValueError):
        return []
    claims = meta.get("claims") if isinstance(meta, dict) else None
    return [c for c in claims if isinstance(c, dict)] if isinstance(claims, list) else []


def _write_conflict_edges(store, conflicts: list[dict], *, source_item: str) -> int:
    """`contradicts` edges for already-recorded conflicts, once the source row exists."""
    from personalclaw.knowledge import contradiction

    edges = [
        contradiction.Edge(
            source=source_item,
            target=str(c.get("right_item", "") or ""),
            relation="contradicts",
            confidence=float(c.get("confidence", 1.0) or 1.0),
            provenance="extracted" if c.get("basis") == "deterministic" else "inferred",
            justification=str(c.get("detail", "") or ""),
        )
        for c in conflicts
    ]
    return _write_edges(store, [e for e in edges if e.valid], source_item=source_item)


def _write_edges(store, edges: list, *, source_item: str) -> int:
    """Persist typed relations. Upsert on `(source, target, relation)` per the table's own key.

    Best-effort with a returned count and a WARNING log, not a silent pass: an edge that failed
    to write leaves the conflict recorded on the item but invisible to any graph query, and the
    two surfaces then disagree about whether the store has a known contradiction.
    """
    written = 0
    now = _now()
    for edge in edges:
        target = edge.target or ""
        if not (source_item and target) or source_item == target:
            continue
        try:
            store.db.execute(
                "INSERT OR REPLACE INTO item_relations (source_item_id, target_item_id, "
                "relation_type, confidence, provenance, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (source_item, target, edge.relation, edge.confidence, edge.provenance, now),
            )
            written += 1
        except Exception:
            logger.warning(
                "could not write %s edge %s -> %s",
                edge.relation,
                source_item,
                target,
                exc_info=True,
            )
    if written:
        try:
            store.db.commit()
        except Exception:
            logger.warning("could not commit item_relations", exc_info=True)
            return 0
    return written


def _merge_claims(
    *, existing: list, incoming: list[dict], source_ref: str
) -> tuple[list[dict], int]:
    """Merge incoming claims into stored ones, appending mentions.

    A claim already present gains a mention from this source and re-aggregates its
    confidence; a new one is added. Returns (merged, mentions_appended).
    """
    by_id: dict[str, Claim] = {}
    for raw in existing or []:
        claim = Claim.from_dict(raw)
        if claim:
            by_id[claim.id] = claim

    appended = 0
    for raw in incoming:
        claim = Claim.from_dict(raw)
        if claim is None:
            continue
        found = by_id.get(claim.id)
        if found is None:
            # First sighting: seed a mention from this source so support_count starts at 1
            # rather than 0 — a claim nobody is recorded as having said reads as unsourced.
            claim.add_mention(Mention(source_ref=source_ref, confidence=claim.confidence, quote=""))
            by_id[claim.id] = claim
            appended += 1
            continue
        if found.add_mention(Mention(source_ref=source_ref, confidence=claim.confidence, quote="")):
            appended += 1
    return [c.to_dict() for c in by_id.values()], appended


def _enqueue_enrichment(item_id: str) -> None:
    """Hand the item to the existing ingest queue. Fire-and-forget.

    A synthesis stage must not wait on an embedder that may not even be configured, and an
    enrichment failure must not lose a write that already succeeded.
    """
    try:
        from personalclaw.knowledge.ingest import enqueue_item  # type: ignore[attr-defined]

        enqueue_item(item_id)
    except Exception:
        logger.debug("knowledge enrichment enqueue unavailable for %s", item_id, exc_info=True)


def _run_source_ref(ctx: ActionContext) -> str:
    """Provenance the caller did not have to remember.

    Read from `ctx.payload`, which is where the engine puts node identity — `ActionContext`
    itself carries only `event`/`context`/`payload`, so reading attributes off it (as an
    earlier version of this did) silently produced "workflow:unknown" for every item.

    The run id is absent by design at this seam: `dispatch_action` does not receive the run.
    A node-scoped ref still attributes the write to a specific template node, which is what
    makes two sources distinguishable for mention counting.
    """
    payload = getattr(ctx, "payload", None) or {}
    run_id = str(payload.get("run_id", "") or "")
    node_id = str(payload.get("node_id", "") or "")
    if run_id and node_id:
        return f"workflow:{run_id}:{node_id}"
    if node_id:
        return f"workflow:node:{node_id}"
    return "workflow:unattributed"


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)
