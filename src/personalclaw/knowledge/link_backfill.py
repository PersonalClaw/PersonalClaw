"""Resumable batched entity-link backfill — the third job KL-14 clause 7 named (the
"graph linker backfill") and could not register, because no such callable existed.

`maintenance_passes.py` recorded the gap with its evidence: the only linker code was
`action_providers/knowledge_maintain_provider._reindex` / `_wikilink_mentions`, both private
helpers inside an action provider and neither a resumable library sweep. This module is that
callable, built the way the host's `batched=True` contract wants: one bounded batch per call,
returning how many items it claimed, so `_claim_batches` can drive it to 0.

**What it closes.** `link_known_entities` (the deterministic, zero-LLM alias linker) runs on
the ingest path — but only for items ingested SINCE it existed, and only against the entities
that existed at that moment. Everything ingested before it, and every item whose graph
neighbours were extracted from a LATER document, has no `mentions` rows it should have. The
entity graph, `/entities`, orphan pruning and every "what else mentions this" surface read
that table, so on a library with history they are all quietly thin.

**Resume state is the rows, not a cursor.** The backlog is a query
(`store.count_items_missing_mention_sweep` / `store.items_missing_mention_sweep`), so an item
leaves it the instant its sweep row commits. A killed run resumes by asking again — nothing to
persist, nothing to reconcile, and no crash window in which a stranded cursor could disagree
with the data. Same idiom as `chunk_backfill`, `count_items_missing_embedding`'s boot
auto-resume, and the ingest queue's `recover_pending`.

🔴 **Why the backlog is NOT "items with no `mentions` rows".** That is the obvious reading and
it does not terminate. An item may legitimately name no known entity, so it never gains a
mention, never leaves the backlog, and gets re-linked forever; because the host re-invokes a
batched pass until it returns 0, the head of the backlog would absorb every sub-batch of every
tick and the tail would never be reached at all. `mentions` records what was FOUND;
`mention_sweeps` records that the linker LOOKED. The backlog needs the second fact, so the
sweep row is written whether or not anything matched.

**Known limitation, stated rather than hidden.** A sweep is once-per-item, so entities created
AFTER an item was swept do not pull it back into the backlog. The correct fix is an
ENTITY-keyed incremental pass (given one new entity, find the items naming it), which is a
different shape of work — the alternative, invalidating every sweep whenever any entity is
created, would re-sweep the whole library on every ingest, and since `link_known_entities`
rebuilds the alias index on every call that is O(items x entities) index builds per tick. This
pass closes the standing historical gap; the incremental arm is its own atom.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Items claimed per call. Bounds peak memory — a batch holds this many items' full text at
#: once, so a library of any size costs the same — and bounds how long one sub-batch holds
#: the store, since the maintenance host relies on the lock being released between them.
BATCH_SIZE = 25


def _open_store() -> Any:
    """Open the knowledge store the same way every other reader does.

    Through `knowledge_db_path`, never a locally composed path: composing it produces
    `<home>/knowledge/knowledge.db` while the real store is `<home>/workspace/knowledge/
    knowledge.db`, so a sweep would link entities in a second database no surface reads.

    🔴 Measured: `KnowledgeStore.__init__` PRUNES every entity with no mentions and no
    relations on every open (`store.py`, "Prune orphan entities"). So a brand-new entity that
    nothing has mentioned yet cannot survive until this pass runs — it is deleted by the very
    open that would have linked it. That is the store's standing behaviour for every reader
    (the knowledge action provider opens the same way), not something this pass introduces,
    and it does not affect the case that matters: an entity extracted from document B already
    carries B's mention, so it survives, and linking it to document A is the whole job.
    """
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    return KnowledgeStore(db_path=str(knowledge_db_path()))


def _linkable_text(row: dict) -> str:
    """The text the linker matches against: title + summary + content.

    Deliberately NOT `embedder.compose_item_text`, which is the WHOLE-ITEM VECTOR text —
    title + summary only, with its docstring stating that `content` is "accepted for a stable
    signature but unused" since KL-9 moved body recall to the chunk index. A vector wants a
    compact identity signal; the linker wants every place a name could appear, and a name
    mentioned once in a body is exactly the edge this backfill exists to record. It is also a
    superset of what the ingest path passes (`runner.py` gives `link_known_entities` the bare
    `content`), so the sweep additionally catches entities named only in a title or summary.
    """
    parts = (
        str(row.get("title") or "").strip(),
        str(row.get("summary") or "").strip(),
        str(row.get("content") or "").strip(),
    )
    return "\n\n".join(p for p in parts if p)


def count_link_backlog() -> int:
    """How many items the linker has never swept. 0 means the graph is fully linked.

    Exposed so a caller (Health panel, digest, a CLI report) can state the backlog without
    running it. Never raises: an unavailable store reports no backlog, not a crash.
    """
    try:
        store = _open_store()
    except Exception:  # noqa: BLE001 — an unopenable store is not a caller's problem
        logger.debug("link backfill: knowledge store unavailable", exc_info=True)
        return 0
    try:
        return int(store.count_items_missing_mention_sweep())
    except Exception:  # noqa: BLE001
        logger.debug("link backfill: backlog count failed", exc_info=True)
        return 0


def link_backfill_pass(*, batch_size: int = BATCH_SIZE) -> int:
    """Claim ONE bounded batch of the link backlog. Returns items processed; 0 == drained.

    The return value is remaining-work-shaped, which is what `batched=True` means to the
    maintenance host: it re-invokes this until it returns 0 (bounded by `max_batches`), so
    every call must make real progress on the backlog. It does, because each item's sweep row
    commits before the next item starts.

    Never raises. A linking hiccup must cost its own item, not the maintenance tick — the
    failure mode this replaces was "the linker had no cadence at all", and a pass that can
    take down a tick is worse than one that skips an item.
    """
    want = max(0, int(batch_size))
    if want <= 0:
        return 0

    try:
        store = _open_store()
    except Exception:  # noqa: BLE001
        logger.debug("link backfill: knowledge store unavailable", exc_info=True)
        return 0

    try:
        if not _has_entities(store):
            # An empty entity graph is a PRECONDITION, not a per-item concern. Sweeping
            # against zero entities would mint sweep rows for the whole library while linking
            # nothing, and because a sweep is once-per-item those items would then never be
            # linked once extraction does create entities. A fresh library must stay in the
            # backlog until there is something to link it to.
            return 0

        batch = store.items_missing_mention_sweep(limit=want)
        if not batch:
            return 0

        from personalclaw.knowledge.alias_prepass import link_known_entities

        processed = linked = 0
        for row in batch:
            item_id = row.get("id")
            if not item_id:
                continue
            try:
                text = _linkable_text(row)
                if text:
                    linked += int(link_known_entities(store, item_id, text) or 0)
            except Exception:  # noqa: BLE001 — one bad item must not end the sweep
                logger.debug("link backfill failed for %s", item_id, exc_info=True)

            # OUTSIDE the link attempt, deliberately: the sweep row records that the linker
            # LOOKED, and it must land whether that found nothing or raised. Inside the try, a
            # permanently-failing item would be re-claimed on every tick and starve every item
            # behind it — the same non-termination the sweep table exists to prevent, just
            # reached by the error path. Its own statement too, so a kill mid-batch keeps the
            # items already swept out of the next run's backlog.
            #
            # `processed` counts items that LEFT the backlog, not items looked at: if the
            # marker cannot be written the store is unwritable, and reporting progress the
            # rows contradict would make the host re-claim the same batch `max_batches` times.
            try:
                store.record_mention_sweep(item_id)
            except Exception:  # noqa: BLE001
                logger.debug("link backfill: sweep marker failed for %s", item_id, exc_info=True)
                continue
            processed += 1
    except Exception:  # noqa: BLE001 — a backfill fault leaves the graph exactly as it was
        logger.debug("link backfill: batch failed", exc_info=True)
        return 0

    if processed:
        logger.debug("link backfill swept %d item(s), recording %d mention(s)", processed, linked)
    return processed


def _has_entities(store: Any) -> bool:
    """Whether the graph has anything to link against. A presence check, not a COUNT —
    the answer is only ever used as a boolean and the library may hold many entities."""
    try:
        return store.db.execute("SELECT 1 FROM entities LIMIT 1").fetchone() is not None
    except Exception:  # noqa: BLE001
        logger.debug("link backfill: entity presence check failed", exc_info=True)
        return False
