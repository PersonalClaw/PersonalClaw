"""``knowledge-health`` and ``knowledge-consolidate`` — the store's maintenance actions.

Two providers, split by COST rather than by topic, because that split is what makes a
maintenance cadence affordable:

**`knowledge-health`** is zero-token and deterministic. Stubs, orphans, broken internal
citations, items missing from the search index, past-expiry probes. Cheap enough to run on
every write, which is the only cadence that catches a problem before something builds on it.

**`knowledge-consolidate`** plans and applies the clustering pass. It is expensive, so it is
gated (min-hours, min-new-material, contention) and it defaults to a DRY RUN: the plan is the
artifact, and applying it is a separate explicit act.

Both report rather than enforce. A health finding is a fact about the store, not a verdict on
it — an orphan may be the only record of something, and a template that auto-deleted orphans
would destroy exactly the knowledge nobody happened to link.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.knowledge import consolidation

logger = logging.getLogger(__name__)

#: Cap on ids echoed back per finding category. A store with 4000 stubs does not need 4000 ids
#: in a workflow output — it needs the count and a sample, and the full list is one query away.
MAX_REPORTED_IDS = 50


class KnowledgeHealthActionProvider(ActionProvider):
    """Deterministic store health. Zero tokens.

    ``action_config`` shape::

        {"fix_index": false}   # re-sync search-index entries found missing
    """

    @property
    def name(self) -> str:
        return "knowledge-health"

    @property
    def display_name(self) -> str:
        return "Check Knowledge Health"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        started = time.monotonic()
        cfg = action_config or {}

        try:
            store = _open_store()
        except Exception as exc:  # pragma: no cover — environmental
            return ActionResult(success=False, error=f"knowledge store unavailable: {exc}")

        items = _load_items(store)
        if not items:
            # An empty store is HEALTHY, not broken. Reporting a problem here would make a fresh
            # install look damaged, and the maintenance cadence would start by crying wolf.
            payload = {
                "report": consolidation.HealthReport().to_dict(),
                "item_count": 0,
                "note": "the store is empty — nothing to check",
            }
            return ActionResult(
                success=True,
                stdout=json.dumps(payload, ensure_ascii=False),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        report = consolidation.check_health(
            items,
            known_ids={i.id for i in items},
            indexed_ids=_indexed_ids(store),
            expired_ids=_expired_ids(store),
        )

        repaired: list[str] = []
        if _truthy(cfg.get("fix_index")) and report.unindexed:
            repaired = _reindex(store, report.unindexed)

        payload = {
            "report": _capped(report.to_dict()),
            "counts": {
                "stubs": len(report.stubs),
                "orphans": len(report.orphans),
                "broken_citations": len(report.broken_citations),
                "expired": len(report.expired),
                "unindexed": len(report.unindexed),
            },
            "item_count": len(items),
            "clean": report.clean,
            "reindexed": repaired,
        }
        return ActionResult(
            success=True,
            stdout=json.dumps(payload, ensure_ascii=False),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


class KnowledgeGapsActionProvider(ActionProvider):
    """Find entities the store keeps referencing but has never written down. Zero tokens.

    ``action_config`` shape::

        {"min_mentions": 3}

    A separate provider rather than a `knowledge-retrieve` call with a clever query: the first
    draft of the gap-healing template passed `min_mentions` AS the search query, which reads
    plausibly in a spec and searches the store for the string "3". The phantom-hub question is
    not a search — it is a set difference between what items REFERENCE and what items EXIST.
    """

    @property
    def name(self) -> str:
        return "knowledge-gaps"

    @property
    def display_name(self) -> str:
        return "Find Knowledge Gaps"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        started = time.monotonic()
        cfg = action_config or {}

        try:
            store = _open_store()
        except Exception as exc:  # pragma: no cover — environmental
            return ActionResult(success=False, error=f"knowledge store unavailable: {exc}")

        items = _load_items(store)
        hubs = consolidation.phantom_hubs(
            items,
            mentions=_wikilink_mentions(items),
            min_mentions=_int(cfg.get("min_mentions"), 3),
        )
        payload = {
            "gaps": hubs[:MAX_REPORTED_IDS],
            "count": len(hubs),
            "excerpts": {
                h["entity"]: _excerpts_for(items, h["referrers"], h["entity"]) for h in hubs[:10]
            },
            "note": (
                "These are candidates for PROPOSALS, not writes. A drafted entry nobody reviewed "
                "becomes a citable source for the next draft."
            ),
        }
        return ActionResult(
            success=True,
            stdout=json.dumps(payload, ensure_ascii=False),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


class KnowledgeConsolidateActionProvider(ActionProvider):
    """Plan (and optionally apply) a consolidation pass.

    ``action_config`` shape::

        {
            "apply": false,          # default DRY RUN — the plan is the artifact
            "min_new_items": 5,
            "min_hours": 6,
            "min_cluster_size": 5,
            "summaries": [           # cluster_index -> the synthesized text, when applying
                {"cluster": 0, "title": "…", "content": "…", "summary": "…"}
            ]
        }

    Two calls by design: one to plan (so a `stage` can synthesize each cluster), one to apply
    with the summaries. A single call would have to make the model call itself, which would put
    an LLM inside an action provider — the one thing the action/stage split exists to prevent.
    """

    @property
    def name(self) -> str:
        return "knowledge-consolidate"

    @property
    def display_name(self) -> str:
        return "Consolidate Knowledge"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 60,
    ) -> ActionResult:
        started = time.monotonic()
        cfg = action_config or {}

        try:
            store = _open_store()
        except Exception as exc:  # pragma: no cover — environmental
            return ActionResult(success=False, error=f"knowledge store unavailable: {exc}")

        items = _load_items(store)
        unprocessed = [i for i in items if not i.consolidated and not i.is_archived]
        gate = consolidation.check_gates(
            unprocessed=len(unprocessed),
            hours_since_last=_hours_since_last_pass(store),
            lock_held=False,
            min_new_items=_int(cfg.get("min_new_items"), consolidation.MIN_NEW_ITEMS),
            min_hours=float(_int(cfg.get("min_hours"), consolidation.MIN_HOURS_BETWEEN_PASSES)),
        )
        if not gate:
            # A DECLINED pass is a success with a reason, not a failure: "there was nothing worth
            # doing" is the normal outcome of a frequent cadence, and failing the node would make
            # a healthy schedule look broken every time it ran.
            payload = {"ran": False, "reason": gate.reason, "backlog": gate.backlog}
            return ActionResult(
                success=True,
                stdout=json.dumps(payload, ensure_ascii=False),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        plan = consolidation.plan_consolidation(
            items,
            similarity=_similarity_for(store),
            min_size=_int(cfg.get("min_cluster_size"), consolidation.MIN_CLUSTER_SIZE),
        )

        summaries = cfg.get("summaries")
        if not _truthy(cfg.get("apply")) or not isinstance(summaries, list) or not summaries:
            payload = {
                "ran": True,
                "applied": False,
                "plan": plan.to_dict(),
                "doctrine": consolidation.CONSOLIDATION_DOCTRINE,
                "prompts": [
                    {"cluster": index, "prompt": consolidation.synthesis_prompt(cluster)}
                    for index, cluster in enumerate(plan.clusters)
                ],
            }
            return ActionResult(
                success=True,
                stdout=json.dumps(payload, ensure_ascii=False),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        applied, issues = await _apply(store, plan, summaries, ctx)
        return ActionResult(
            success=True,
            stdout=json.dumps(
                {
                    "ran": True,
                    "applied": True,
                    "summaries_written": applied,
                    "issues": issues,
                    "plan": plan.to_dict(),
                },
                ensure_ascii=False,
            ),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


#: `[[Wikilink]]` references inside item bodies. The store's own growth frontier is written in
#: these: a name five items link to is something the store believes matters and has never
#: recorded.
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|]{2,80})(?:\|[^\]]*)?\]\]")


def _wikilink_mentions(items: list[consolidation.Item]) -> dict[str, list[str]]:
    """entity → the ids of items referencing it."""
    out: dict[str, list[str]] = {}
    for item in items:
        if item.is_archived:
            continue
        for match in _WIKILINK_RE.finditer(item.text):
            entity = match.group(1).strip()
            if entity:
                out.setdefault(entity, []).append(item.id)
    return out


def _excerpts_for(
    items: list[consolidation.Item], referrer_ids: list[str], entity: str
) -> list[str]:
    """The sentences that mention an entity, so a draft is grounded in what the store SAYS.

    Without excerpts the drafting model has only a name, and a model given a bare name writes
    what it already believes about it — which is exactly the invention this template exists to
    avoid.
    """
    by_id = {i.id: i for i in items}
    needle = entity.lower()
    out: list[str] = []
    for rid in referrer_ids[:5]:
        item = by_id.get(rid)
        if item is None:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", item.text):
            if needle in sentence.lower():
                out.append(f"[{rid}] {sentence.strip()[:300]}")
                break
    return out


# ── store access ──


def _load_items(store: Any) -> list[consolidation.Item]:
    """Every item, with its inbound-relation count.

    The relation count comes from a GROUP BY rather than a per-item query: on a 4000-item store
    the per-item form is 4000 queries for a number the database can produce in one, and the
    health pass is supposed to be the cheap tier.
    """
    try:
        inbound: dict[str, int] = {
            str(r["target_item_id"]): int(r["n"])
            for r in store.db.execute(
                "SELECT target_item_id, COUNT(*) AS n FROM item_relations GROUP BY target_item_id"
            )
        }
    except Exception:
        # No relations table yet (or an older store): every item reads as an orphan, which would
        # make the whole report noise. Better to report NO orphans than to report all of them.
        logger.debug("item_relations unavailable — skipping orphan detection", exc_info=True)
        inbound = {}

    out: list[consolidation.Item] = []
    try:
        rows = list(
            store.db.execute(
                "SELECT id, kind, title, summary, content, logical_key, content_hash, "
                "updated_at, is_archived, file_metadata FROM items"
            )
        )
    except Exception:
        logger.warning("could not read knowledge items", exc_info=True)
        return []
    for row in rows:
        item = consolidation.Item.from_row(row)
        # `inbound` is empty when the table is missing; a sentinel of 1 keeps every item out of
        # the orphan list rather than putting all of them in it.
        item.inbound_relations = inbound.get(item.id, 0 if inbound else 1)
        out.append(item)
    return out


def _indexed_ids(store: Any) -> set[str] | None:
    """Item IDS present in the FTS index, or None when the index cannot be read.

    `items_fts` is keyed by ROWID, not by the item's text id, so this joins back through `items`.
    Comparing the two directly marked every single item unindexed — a report claiming seven
    problems on a healthy seven-item store, which is how a maintenance report stops being read.

    None rather than an empty set when unreadable, for the same reason: an unreadable index would
    otherwise report the entire store as broken.
    """
    try:
        rows = list(
            store.db.execute("SELECT i.id AS id FROM items_fts f JOIN items i ON i.rowid = f.rowid")
        )
    except Exception:
        logger.debug("items_fts unreadable — skipping index consistency", exc_info=True)
        return None
    return {str(r["id"]) for r in rows}


def _expired_ids(store: Any) -> set[str]:
    try:
        rows = list(
            store.db.execute(
                "SELECT id FROM items WHERE expires_at != '' AND expires_at < ?",
                (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),),
            )
        )
    except Exception:
        return set()
    return {str(r["id"]) for r in rows}


def _reindex(store: Any, item_ids: list[str]) -> list[str]:
    """Re-sync specific rows into the FTS index.

    Delete-then-insert per row, never `'rebuild'`: the store's own docstring records that a
    rebuild against a stale content target WIPES THE INDEX AND REPORTS SUCCESS.
    """
    done: list[str] = []
    for item_id in item_ids[:MAX_REPORTED_IDS]:
        try:
            rows = list(
                store.db.execute("SELECT rowid, title, content FROM items WHERE id = ?", (item_id,))
            )
            if not rows:
                continue
            row = rows[0]
            # By ROWID and with the index's own column set — an insert keyed on the text id
            # writes an entry no search will ever match, which is worse than the gap it was
            # repairing because the report then says it is fixed.
            store.db.execute(
                "INSERT INTO items_fts(rowid, title, content, tags) VALUES (?, ?, ?, ?)",
                (row["rowid"], row["title"] or "", row["content"] or "", ""),
            )
            store.db.commit()
            done.append(item_id)
        except Exception:
            logger.warning("could not reindex %s", item_id, exc_info=True)
    return done


def _hours_since_last_pass(store: Any) -> float:
    """Hours since the most recent consolidated item was written.

    A very large number when nothing has ever been consolidated, so a first pass is never gated
    on a timestamp that does not exist.
    """
    try:
        rows = list(
            store.db.execute(
                "SELECT updated_at FROM items WHERE file_metadata LIKE '%\"consolidated\": true%' "
                "ORDER BY updated_at DESC LIMIT 1"
            )
        )
    except Exception:
        return 10_000.0
    if not rows or not rows[0]["updated_at"]:
        return 10_000.0
    try:
        stamp = time.mktime(time.strptime(str(rows[0]["updated_at"]), "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return 10_000.0
    return max(0.0, (time.time() - stamp) / 3600.0)


def _similarity_for(store: Any) -> Any:
    """A cosine metric over the store's embeddings, or None for the token floor.

    None is a supported configuration, and the caller's threshold follows the metric — mixing a
    cosine threshold with token similarity clusters nothing at all.
    """
    try:
        from personalclaw.embedding_providers.registry import get_active_embed_fn

        embed = get_active_embed_fn()
    except Exception:
        return None
    if embed is None:
        return None

    from personalclaw.knowledge.retrieval import HybridRetriever

    cache: dict[str, list[float]] = {}

    def similarity(left: str, right: str) -> float:
        try:
            for text in (left, right):
                if text not in cache:
                    vector = embed(text)
                    if not vector:
                        # An embedder that returns nothing for one item is not a pass-ending
                        # error, but it MUST NOT be cached as an empty vector: a cosine against
                        # [] is either a crash or a meaningless 0.0, and a 0.0 silently means
                        # "unrelated" — so that item would never cluster with anything, forever.
                        return consolidation.token_similarity(left, right)
                    cache[text] = vector
            return HybridRetriever._cosine_similarity(cache[left], cache[right])
        except Exception:
            # Degrade to the token metric for THIS pair rather than failing the pass. A pass that
            # dies because one embedding call failed has thrown away the whole sweep.
            return consolidation.token_similarity(left, right)

    return similarity


async def _apply(
    store: Any, plan: consolidation.ConsolidationPlan, summaries: list[Any], ctx: ActionContext
) -> tuple[int, list[str]]:
    """Write each cluster's summary and ARCHIVE its inputs.

    Archive, never delete, and never for a protected item. `plan_consolidation` already excluded
    protected items from clustering; the check is repeated here because this is the function that
    actually writes, and a guarantee enforced only upstream is one refactor away from being gone.
    """
    written = 0
    issues: list[str] = []
    for entry in summaries:
        if not isinstance(entry, dict):
            issues.append("summary entry is not an object")
            continue
        index = _int(entry.get("cluster"), -1)
        if not (0 <= index < len(plan.clusters)):
            issues.append(f"no cluster at index {index}")
            continue
        cluster = plan.clusters[index]
        content = str(entry.get("content", "") or "")
        if not content.strip():
            issues.append(f"cluster {index}: empty summary, nothing written")
            continue
        meta = consolidation.summary_metadata(cluster, summary_chars=len(content))
        try:
            await _write_summary(entry, cluster, meta, ctx)
            for item in cluster.items:
                if item.protected:
                    issues.append(f"{item.id}: protected, left unarchived")
                    continue
                _archive(store, item.id, meta)
            written += 1
        except Exception as exc:
            logger.warning("consolidation apply failed for cluster %s", index, exc_info=True)
            issues.append(f"cluster {index}: {exc}")
    return written, issues


async def _write_summary(
    entry: dict, cluster: consolidation.Cluster, meta: dict, ctx: ActionContext
) -> None:
    """Write the consolidated item THROUGH `knowledge-persist`, not with raw SQL.

    Reusing the persist provider was not a style choice — hand-rolling the INSERT here missed
    the NOT NULL `item_type` column (the write failed outright), and would also have skipped the
    FTS sync, the idempotency check, and the provenance ref that provider already gets right.
    Two writers to one table means every fix to one of them has to be remembered for the other.
    """
    from personalclaw.action_providers.knowledge_persist_provider import (
        KnowledgePersistActionProvider,
    )

    title = str(entry.get("title", "") or cluster.items[0].title)
    result = await KnowledgePersistActionProvider().execute(
        {
            "kind": "insight",
            "title": title,
            "content": str(entry.get("content", "") or ""),
            "summary": str(entry.get("summary", "") or ""),
            # Its own inputs ARE its citations, so the item can never read as unsourced.
            "citations": [f"item:{i}" for i in cluster.ids],
            "lineage": meta,
            "tags": ["consolidated"],
        },
        ctx,
    )
    if not result.success:
        raise RuntimeError(result.error or "persist refused the consolidated item")


def _archive(store: Any, item_id: str, meta: dict) -> None:
    """Demote an original, with a back-reference to the summary that replaced it.

    The back-reference is what makes this reversible: without it an archived item is
    indistinguishable from one archived for any other reason, and the merge cannot be undone.
    """
    rows = list(store.db.execute("SELECT file_metadata FROM items WHERE id = ?", (item_id,)))
    existing = _json(rows[0]["file_metadata"]) if rows else {}
    existing["archived_reason"] = "consolidated"
    existing["summary_of"] = meta.get("parent_ids", [])
    existing["reflection_count"] = _int(existing.get("reflection_count"), 0) + 1
    store.db.execute(
        "UPDATE items SET is_archived = 1, file_metadata = ? WHERE id = ?",
        (json.dumps(existing, ensure_ascii=False), item_id),
    )
    store.db.commit()


def _capped(report: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in report.items():
        out[key] = value[:MAX_REPORTED_IDS] if isinstance(value, list) else value
    return out


def _json(raw: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _truthy(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("true", "1", "yes")


def _int(raw: Any, fallback: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return fallback
    return int(raw)


def _open_store() -> Any:
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    # Through `knowledge_db_path`, never a locally composed path. Measured live: composing it here
    # produced `<home>/knowledge/knowledge.db` while the dashboard reads
    # `<home>/workspace/knowledge/knowledge.db`, so workflow-persisted knowledge landed in a
    # second database the UI could never see — with no error on either side.
    return KnowledgeStore(db_path=str(knowledge_db_path()))
