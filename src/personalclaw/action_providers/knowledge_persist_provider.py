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

        if claims_raw:
            merged, appended = _merge_claims(
                existing=metadata.get("claims") or [],
                incoming=[c for c in claims_raw if isinstance(c, dict)],
                source_ref=source_ref,
            )
            metadata["claims"] = merged

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
                        "reason": decision.reason,
                    }
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

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

        _enqueue_enrichment(item_id)
        return ActionResult(
            success=True,
            stdout=json.dumps(
                {
                    "item_id": item_id,
                    "logical_key": check.logical_key,
                    "created": decision.action == "create",
                    "mentions_appended": appended,
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
    from personalclaw.config.loader import config_dir
    from personalclaw.knowledge.store import KnowledgeStore

    path = config_dir() / "knowledge" / "knowledge.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return KnowledgeStore(db_path=str(path))


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
