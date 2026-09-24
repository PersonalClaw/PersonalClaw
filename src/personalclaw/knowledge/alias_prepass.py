"""Deterministic mention linking for knowledge ingestion (MEMORY-GRAPH-AND-VAULT §1.3).

The entities stage of the ingestion pipeline is **LLM-only**: `EntityExtractor` asks a model
what the item is about, and everything downstream — mentions, relations, graph edges — depends
on that call. Which means:

* **With no model bound, nothing links at all.** A user running local-only, or between
  providers, ingests a document that plainly names an entity the graph already knows and gets
  zero mentions. The graph looks empty because the extractor never ran, not because the
  document said nothing.
* **A model can miss what a string match cannot.** Extraction is generative: an entity named
  once in passing, or written as a declared alias rather than its canonical name, is exactly
  the case a model drops and a trie hits every time.

So this pre-pass runs **before** extraction and independently of it: it walks the item's text
with the same `AliasIndex` the memory store uses, and records a mention for every known entity
whose name or alias literally appears. Zero LLM calls, zero tokens, no network.

**It ADDS, never replaces.** Extraction still runs and still creates new entities — the
pre-pass can only link to entities that already exist, so it cannot discover anything. The
two are complementary: the model finds what is new, the trie guarantees what is already known
is never missed. `add_mention` is `INSERT OR IGNORE`, so an entity both stages find is one
mention, not two.

**Why the same matcher as the memory store.** Reusing `AliasIndex` means the knowledge graph
and the push reflex agree about what counts as a mention. Two matchers would drift, and the
symptom would be a document that links in one surface and not the other — with nothing to
point at.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Cap on entities loaded into the index for one item. A trie over the whole entity table is
#: cheap at personal scale, but this bounds a pathological store from turning every ingest
#: into a long pause. Beyond it the pre-pass links what it can and logs the shortfall rather
#: than silently doing half the job.
MAX_INDEXED_ENTITIES = 5000

#: Mentions recorded per item. An item that names one entity forty times is one mention;
#: this caps how many DISTINCT entities a single item may link, so a glossary page can't
#: attach itself to the entire graph and drown the real signal.
MAX_MENTIONS_PER_ITEM = 60


def build_index(store: Any) -> tuple[Any, dict[str, str]]:
    """An `AliasIndex` over every known entity name + alias, and an id→name map.

    Returns `(index, names)`. The index is empty when there are no entities, which makes the
    caller a no-op — the common case on a fresh install, and it must cost nothing.
    """
    from personalclaw.memory_graph import AliasIndex

    index = AliasIndex()
    names: dict[str, str] = {}
    try:
        rows = store.db.execute(
            "SELECT id, name, aliases FROM entities LIMIT ?", (MAX_INDEXED_ENTITIES + 1,)
        ).fetchall()
    except Exception:
        logger.debug("alias pre-pass: entity read failed", exc_info=True)
        return index, names

    if len(rows) > MAX_INDEXED_ENTITIES:
        logger.info(
            "alias pre-pass: %d entities exceeds the %d cap — linking against the first %d",
            len(rows),
            MAX_INDEXED_ENTITIES,
            MAX_INDEXED_ENTITIES,
        )
        rows = rows[:MAX_INDEXED_ENTITIES]

    for row in rows:
        eid = row["id"]
        name = (row["name"] or "").strip()
        if not eid or not name:
            continue
        names[eid] = name
        index.add(eid, name)
        # Aliases are stored as a JSON array; a malformed value is skipped rather than
        # failing the whole index — one bad row must not cost every other entity its links.
        raw = row["aliases"]
        if not raw:
            continue
        try:
            aliases = json.loads(raw)
        except (TypeError, ValueError):
            logger.debug("alias pre-pass: entity %s has malformed aliases", eid)
            continue
        if not isinstance(aliases, list):
            continue
        for alias in aliases:
            surface = str(alias or "").strip()
            if surface:
                index.add(eid, surface)
    return index, names


def link_known_entities(store: Any, item_id: str, text: str) -> int:
    """Record a mention for every known entity named in *text*. Returns how many.

    Deterministic and zero-LLM by construction. Safe to call before extraction, after it, or
    when no model is available at all — `add_mention` is `INSERT OR IGNORE`, so an entity both
    stages find yields one mention.
    """
    if not item_id or not text or not text.strip():
        return 0
    try:
        index, names = build_index(store)
    except Exception:
        logger.debug("alias pre-pass: index build failed", exc_info=True)
        return 0
    if len(index) == 0:
        return 0

    try:
        mentions = index.find(text)
    except Exception:
        logger.debug("alias pre-pass: matcher failed for %s", item_id, exc_info=True)
        return 0
    if not mentions:
        return 0

    # One mention per DISTINCT entity: the matcher returns every occurrence, but the mentions
    # table is (item, entity) — recording forty hits of one name is forty identical rows the
    # INSERT OR IGNORE would collapse anyway, at forty times the write cost.
    seen: dict[str, Any] = {}
    for mention in mentions:
        if mention.entity_id not in seen:
            seen[mention.entity_id] = mention
        if len(seen) >= MAX_MENTIONS_PER_ITEM:
            break

    linked = 0
    for entity_id, mention in seen.items():
        try:
            # The surrounding text as context, exactly as the memory store records it — so a
            # reader can see WHY this item was linked without opening the document.
            store.add_mention(item_id, entity_id, context=mention.context(text))
            linked += 1
        except Exception:
            logger.debug(
                "alias pre-pass: could not link %s → %s", item_id, entity_id, exc_info=True
            )
    if linked:
        logger.debug(
            "alias pre-pass linked %d known entit%s for %s",
            linked,
            "y" if linked == 1 else "ies",
            item_id,
        )
    return linked
