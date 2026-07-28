"""Write-time linking + alias seeding (MEMORY-GRAPH-AND-VAULT §1.1, §1.3).

`memory_graph` owns the tables; this module owns the *decisions*: which entities a
record mentions, which edge type each mention deserves, and where the entity set
comes from in the first place.

Two rules to keep in mind when extending the cascade:

* **First match wins, most specific first.** A record can carry several structural
  cues; the cascade is an ordered ladder, not a scoring function, because a
  deterministic linker whose output depends on tie-breaks is not deterministic in
  any useful sense.
* **The linker never raises into a write.** Memory writes are the user's data
  arriving; a linking bug must degrade to "no links", never to "your fact was
  rejected". Every entry point here swallows and logs.
"""

from __future__ import annotations

import json
import logging
import re

from personalclaw.memory_graph import (
    ENTITY_TYPES,
    AliasIndex,
    MemoryGraph,
    Mention,
)

logger = logging.getLogger(__name__)

# Key prefixes whose records are *about* a subject rather than merely mentioning
# one (§1.2 `about`). Verified against memory_record._kind_from_key.
_ABOUT_PREFIXES = ("user.persona.", "pref.facet.identity.")

# A `project.<slug>.*` key names its project structurally, so a mention of that
# project in such a record is affiliation, not a passing reference.
_PROJECT_KEY = re.compile(r"^project\.([a-z0-9][a-z0-9_-]*)\.", re.IGNORECASE)

# An explicit pointer to another record or an external artifact.
_REFERENCE = re.compile(r"\b(?:https?://|[a-z]+\.[a-z0-9_]+\.[a-z0-9_.]+)", re.IGNORECASE)


def classify_link(key: str, text: str, mention: Mention, index_names: dict) -> str:
    """The typed-edge cascade (§1.1 step 3). Returns one `LINK_TYPES` member.

    Ordered most-specific-first; the first cue that fits wins.
    """
    entity_type = index_names.get(mention.entity_id, "")
    lowered = (key or "").lower()

    # 1. The record's key declares its subject, and this mention IS that subject.
    if lowered.startswith(_ABOUT_PREFIXES):
        return "about"

    # 2. A project-scoped key mentioning a project entity is affiliation.
    project_match = _PROJECT_KEY.match(key or "")
    if project_match and entity_type == "project":
        return "same_project"

    # 3. The text points explicitly at another record or artifact.
    if _REFERENCE.search(text or ""):
        return "references"

    # 4. Nothing structural — the deterministic floor.
    return "mentions"


def link_record(
    graph: MemoryGraph,
    index: AliasIndex,
    *,
    from_kind: str,
    from_ref: str,
    text: str,
    key: str = "",
    batch_ref: str | None = None,
    replace: bool = True,
) -> dict:
    """Link one record. Returns a small report (never raises).

    ``batch_ref`` groups records written in the same consolidation/conversation so
    they earn `temporal_proximity` edges to each other — the cheap structural
    signal that "these were learned together".
    """
    report: dict = {"mentions": 0, "links": 0, "proposals": 0, "entities": []}
    try:
        haystack = f"{key} {text}" if key else (text or "")
        if replace:
            graph.drop_links_for(from_kind, from_ref)

        entity_types = {e.id: e.entity_type for e in graph.entities()}
        seen: set[tuple[str, str]] = set()
        for mention in index.find(haystack):
            report["mentions"] += 1
            link_type = classify_link(key, text, mention, entity_types)
            dedup_key = (mention.entity_id, link_type)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            created = graph.add_link(
                from_kind=from_kind,
                from_ref=from_ref,
                to_entity=mention.entity_id,
                link_type=link_type,
                provenance="extracted",
                confidence=1.0,
                context=mention.context(haystack),
            )
            if created:
                report["links"] += 1
                report["entities"].append(mention.entity_id)

        # Unknown names accumulate toward a proposal instead of becoming entities.
        for name in index.unknown_capitalized(text or ""):
            graph.tally_proposal(name, from_ref)
            report["proposals"] += 1

        if batch_ref:
            _link_batch_siblings(graph, from_kind=from_kind, from_ref=from_ref, batch=batch_ref)
    except Exception:  # noqa: BLE001 — a linking failure must never fail the write
        logger.debug("memory linker failed for %s/%s", from_kind, from_ref, exc_info=True)
    return report


def _link_batch_siblings(graph: MemoryGraph, *, from_kind: str, from_ref: str, batch: str) -> None:
    """Give this record `temporal_proximity` edges to its batch siblings."""
    rows = graph.db.execute(
        "SELECT DISTINCT from_kind, from_ref FROM mem_links WHERE to_ref = ? "
        "AND link_type = 'temporal_proximity'",
        (batch,),
    ).fetchall()
    graph.add_link(
        from_kind=from_kind,
        from_ref=from_ref,
        to_ref=batch,
        link_type="temporal_proximity",
        provenance="extracted",
        confidence=1.0,
    )
    for row in rows:
        if row["from_ref"] == from_ref:
            continue
        graph.add_link(
            from_kind=from_kind,
            from_ref=from_ref,
            to_ref=row["from_ref"],
            link_type="temporal_proximity",
            provenance="extracted",
            confidence=1.0,
        )


# ── Alias seeding: the three sources (§1.3) ────────────────────────────────────


def seed_from_memory_facts(graph: MemoryGraph) -> int:
    """Seed entities from the user's own stored facts. Returns entities touched.

    Reads record VALUES, not keys. Facet and persona keys end in an md5 slug
    (``pref.facet.identity.<hash>``), so the readable name only exists in the
    payload — a key-parsing seeder would produce entities named after hashes.
    """
    touched = 0
    rows = graph.db.execute(
        "SELECT key, value_json FROM semantic_memory WHERE is_deleted = 0 "
        "AND (key LIKE 'pref.facet.identity.%' OR key LIKE 'project.%')"
    ).fetchall()
    for row in rows:
        key = row["key"]
        try:
            value = json.loads(row["value_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if key.startswith("project."):
            slug = _PROJECT_KEY.match(key)
            name = slug.group(1) if slug else None
            if not name and key == "project.name":
                name = value if isinstance(value, str) else None
            if name:
                # Keys are lowercase slugs, so the slug alone would name the entity
                # "personalclaw". The record's own text usually contains the real
                # casing — prefer it, since this string is what the user reads.
                display = _cased_in_text(str(name), _text_of(value)) or str(name)
                graph.upsert_entity(display, "project", source="facet")
                touched += 1
            continue
        # Identity facets: the payload's `text` carries the human-readable claim.
        text = value.get("text") if isinstance(value, dict) else value
        for name in _person_names(str(text or "")):
            graph.upsert_entity(name, "person", source="facet")
            touched += 1
    return touched


def _text_of(value: object) -> str:
    """The readable text inside a semantic value (payload dicts carry it in `text`)."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for name in ("text", "rule", "value", "description"):
            candidate = value.get(name)
            if isinstance(candidate, str):
                return candidate
    return ""


def _cased_in_text(slug: str, text: str) -> str | None:
    """The slug as it appears in ``text``, preserving the author's capitalization.

    Matches case-insensitively and returns the original span, so a
    `project.personalclaw.*` key whose value says "PersonalClaw runs on…" names the
    entity "PersonalClaw" rather than "personalclaw".
    """
    if not slug or not text:
        return None
    match = re.search(rf"\b{re.escape(slug)}\b", text, re.IGNORECASE)
    return match.group(0) if match else None


_NAME_CUE = re.compile(
    r"(?:my name is|i am|i'm|call me|name's)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
    re.IGNORECASE,
)


def _person_names(text: str) -> list[str]:
    """Person names an identity facet asserts. Conservative by design.

    Only explicit self-identification cues — an identity facet like "prefers terse
    replies" must not mint a person entity out of an incidental capitalized word.
    """
    out = []
    for match in _NAME_CUE.finditer(text or ""):
        candidate = match.group(1).strip()
        if candidate and candidate.lower() not in {"not", "the", "a"}:
            out.append(candidate)
    return out


def seed_from_knowledge(graph: MemoryGraph, knowledge_db_path=None) -> int:
    """Seed from knowledge.db's `entities` table, READ-ONLY.

    The one memory↔knowledge bridge. Opened read-only and by URI so this can never
    write to the knowledge store, and the knowledge id is kept in ``source`` as a
    display-time hint rather than a foreign key — either store must stay
    independently rebuildable, so a missing counterpart degrades to a dangling
    label, never a constraint violation.
    """
    import sqlite3

    if knowledge_db_path is None:
        try:
            from personalclaw.knowledge import knowledge_db_path as _kpath

            knowledge_db_path = _kpath()
        except Exception:  # noqa: BLE001
            logger.debug("no knowledge store to seed from", exc_info=True)
            return 0
    from pathlib import Path

    path = Path(knowledge_db_path)
    if not path.exists():
        return 0
    touched = 0
    try:
        kdb = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        kdb.row_factory = sqlite3.Row
        try:
            rows = kdb.execute("SELECT id, name, entity_type, aliases FROM entities").fetchall()
        finally:
            kdb.close()
    except sqlite3.Error:
        logger.debug("knowledge entity read failed", exc_info=True)
        return 0
    for row in rows:
        etype = (row["entity_type"] or "").lower()
        if etype not in ENTITY_TYPES:
            etype = "topic"  # keep the entity, normalize its unknown type
        try:
            aliases = list(json.loads(row["aliases"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            aliases = []
        try:
            graph.upsert_entity(
                row["name"], etype, aliases=aliases, source=f"knowledge:{row['id']}"
            )
            touched += 1
        except ValueError:
            continue
    return touched


def seed_all(graph: MemoryGraph, *, knowledge_db_path=None) -> dict:
    """Run every seed source. User-edited entities are never overwritten."""
    facts = seed_from_memory_facts(graph)
    knowledge = seed_from_knowledge(graph, knowledge_db_path)
    return {"from_facts": facts, "from_knowledge": knowledge}


# ── Backfill (§ Session 1) ─────────────────────────────────────────────────────


def backfill(graph: MemoryGraph, *, batch_size: int = 200, limit: int | None = None) -> dict:
    """Link every existing record. Idempotent and batched.

    Idempotent because each record's links are dropped and re-derived, and the
    unique edge index collapses repeats — so running this twice is a no-op, and an
    interrupted run just resumes.
    """
    index = graph.build_index()
    before = graph.summary()
    processed = 0
    linked = 0

    def _rows(sql, params=()):
        return graph.db.execute(sql, params).fetchall()

    semantic = _rows(
        "SELECT key, value_json FROM semantic_memory WHERE is_deleted = 0 ORDER BY key"
        + (f" LIMIT {int(limit)}" if limit else "")
    )
    for start in range(0, len(semantic), batch_size):
        for row in semantic[start : start + batch_size]:
            try:
                value = json.loads(row["value_json"])
            except (json.JSONDecodeError, TypeError):
                value = row["value_json"]
            text = value.get("text") if isinstance(value, dict) else value
            report = link_record(
                graph,
                index,
                from_kind="semantic",
                from_ref=row["key"],
                key=row["key"],
                text=str(text or ""),
            )
            processed += 1
            linked += report["links"]

    episodic = _rows(
        "SELECT id, text FROM episodic_memories WHERE is_deleted = 0 ORDER BY id"
        + (f" LIMIT {int(limit)}" if limit else "")
    )
    for start in range(0, len(episodic), batch_size):
        for row in episodic[start : start + batch_size]:
            report = link_record(
                graph,
                index,
                from_kind="episodic",
                from_ref=row["id"],
                text=row["text"] or "",
            )
            processed += 1
            linked += report["links"]

    after = graph.summary()
    return {
        "records_processed": processed,
        "links_created": linked,
        "before": before,
        "after": after,
    }
