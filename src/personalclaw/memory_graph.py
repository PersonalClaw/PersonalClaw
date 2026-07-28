"""The typed entity graph over memory.db (MEMORY-GRAPH-AND-VAULT §1).

Recall today is flat: 0.6·vector + 0.4·keyword, with no notion that a persona
fact, an episodic row about a standup, and a lesson about a repo all concern the
same *person* or *project*. This module adds the skeleton that makes those the
same thing — deterministically, at write time, for zero tokens.

Three properties are load-bearing and easy to lose:

* **Zero LLM.** Linking is string matching against a known alias set. Not "cheap";
  *none*. A write stays synchronous and fast.
* **Propose, don't invent.** An unknown capitalized word does NOT become an entity.
  Junk entities degrade recall, so unknown names accumulate in a tally and only
  surface as *proposals* once they recur — the human accepts them.
* **Reversible.** Every link write appends a ``memory_events`` row, so graph
  mutations undo through the WAL machinery that already exists rather than growing
  a parallel one.

Scale note: this is one person's memory (hundreds to low thousands of records),
so the matcher is a token trie rebuilt in-process, not a service. See
``docs/roadmap/plans/MEMORY-GRAPH-AND-VAULT.md`` §1 for the design.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field

# MUST mirror vector_memory's import: on Linux/x86_64 the store connects through
# `pysqlite3` (a newer bundled SQLite), whose exception classes are DISTINCT objects
# from the stdlib's. Importing plain `sqlite3` here made `except sqlite3.IntegrityError`
# silently never match the error the connection actually raises, so the duplicate-edge
# path crashed on CI while passing on macOS (no pysqlite3 → same class by accident).
try:
    import pysqlite3 as sqlite3
except ImportError:  # pragma: no cover — platform-dependent
    import sqlite3  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# The closed entity vocabulary (§1). Closed on purpose: an open type set turns
# into a taxonomy debate, and every consumer would have to handle the unknown case.
ENTITY_TYPES = ("person", "project", "tool", "org", "topic", "place")

# The closed edge vocabulary (§1.2). `mentions` is the deterministic floor; the
# others are upgrades the cascade applies when structure justifies them.
LINK_TYPES = (
    "mentions",
    "about",
    "same_project",
    "references",
    "temporal_proximity",
    "same_topic",
)

# An alias shorter than this is not matched. "AI", "ML", "go" as bare words
# produce far more false links than true ones, and a bad link is worse than a
# missing one — it pollutes ranking for every future query.
MIN_ALIAS_TOKEN_LEN = 3

# How many distinct records must mention an unknown name before it is worth
# proposing as an entity (§1.1 notability gate).
PROPOSAL_THRESHOLD = 3

# Context snippet budget around a mention, per side.
_CONTEXT_CHARS = 100

# The graph arm's ranking weight (§2.1's β) — scales with how connected the entity is.
GRAPH_BOOST_BETA = 0.1

# Floor for a record linked to an entity the query NAMED BY NAME. β·log1p(inbound)
# alone lands around 0.07 for a lightly-linked entity, which loses to the ~0.1 an
# unrelated record picks up from incidental word overlap ("is", "on") — so the record
# that actually concerns the person asked about ranked below noise. Naming an entity is
# a strong, deliberate signal and has to clear that floor. Still well under the 0.3+ a
# real keyword match earns, so typed words continue to win.
GRAPH_BOOST_FLOOR = 0.25

_WORD = re.compile(r"[0-9a-z]+(?:'[a-z]+)?", re.IGNORECASE)


def new_entity_id() -> str:
    return f"e-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class Entity:
    """One thing memory can be *about*."""

    id: str
    name: str
    entity_type: str
    aliases: tuple[str, ...] = ()
    source: str = "user"

    def surface_forms(self) -> tuple[str, ...]:
        """Every string that should resolve to this entity."""
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class Mention:
    """One matched surface form inside a record's text."""

    entity_id: str
    matched: str
    start: int
    end: int

    def context(self, text: str) -> str:
        lo = max(0, self.start - _CONTEXT_CHARS)
        hi = min(len(text), self.end + _CONTEXT_CHARS)
        return text[lo:hi].strip()


@dataclass
class _Node:
    children: dict[str, "_Node"] = field(default_factory=dict)
    # Entity ids whose alias ends exactly here, with the alias's own token count.
    terminal: list[str] = field(default_factory=list)


def _tokenize(text: str) -> list[tuple[str, int, int]]:
    """Lowercase word tokens with their character spans.

    Tokenizing both sides is what gives word-boundary matching for free: "Ann" can
    never match inside "Announcement", because they are different tokens. A
    character-level automaton would need explicit boundary checks at every hit.
    """
    return [(m.group(0).lower(), m.start(), m.end()) for m in _WORD.finditer(text)]


class AliasIndex:
    """A token trie over every known surface form — the one matcher both stores use.

    Hand-rolled rather than adding an Aho-Corasick dependency: at personal scale a
    token trie is the same complexity class for our access pattern (one pass over
    the text, bounded by the longest alias), and the repo already prefers
    hand-rolled over a dependency for exactly this kind of primitive (see
    ``SimpleDiGraph`` in ``knowledge/store.py``).

    Longest match wins, so "Keyur Golani" beats a bare "Keyur" when both are known.
    """

    def __init__(self) -> None:
        self._root = _Node()
        self._forms = 0
        self._max_depth = 0

    def __len__(self) -> int:
        return self._forms

    @property
    def max_phrase_tokens(self) -> int:
        return self._max_depth

    def add(self, entity_id: str, surface: str) -> bool:
        """Register one surface form. Returns whether it was indexable."""
        tokens = [t for t, _, _ in _tokenize(surface)]
        if not tokens:
            return False
        # A single short token is too ambiguous to match on (see MIN_ALIAS_TOKEN_LEN).
        # Multi-token phrases are inherently specific, so they bypass the floor.
        if len(tokens) == 1 and len(tokens[0]) < MIN_ALIAS_TOKEN_LEN:
            return False
        node = self._root
        for tok in tokens:
            node = node.children.setdefault(tok, _Node())
        if entity_id not in node.terminal:
            node.terminal.append(entity_id)
        self._forms += 1
        self._max_depth = max(self._max_depth, len(tokens))
        return True

    def add_entity(self, entity: Entity) -> int:
        return sum(1 for form in entity.surface_forms() if self.add(entity.id, form))

    def find(self, text: str) -> list[Mention]:
        """Every non-overlapping longest match in ``text``, in order."""
        if not text or self._forms == 0:
            return []
        tokens = _tokenize(text)
        out: list[Mention] = []
        i = 0
        while i < len(tokens):
            best: tuple[int, list[str]] | None = None  # (end_index, entity_ids)
            node = self._root
            for j in range(i, len(tokens)):
                node = node.children.get(tokens[j][0])  # type: ignore[assignment]
                if node is None:
                    break
                if node.terminal:
                    best = (j, list(node.terminal))
            if best is None:
                i += 1
                continue
            end_idx, entity_ids = best
            start_char = tokens[i][1]
            end_char = tokens[end_idx][2]
            matched = text[start_char:end_char]
            for eid in entity_ids:
                out.append(Mention(eid, matched, start_char, end_char))
            # Skip past the match so one phrase yields one mention per entity.
            i = end_idx + 1
        return out

    def unknown_capitalized(self, text: str) -> list[str]:
        """Capitalized multi-word names in ``text`` that matched nothing.

        Feeds the notability tally (§1.1) — these are *candidates*, never entities.
        Restricted to multi-word Title Case because single capitalized words are
        overwhelmingly sentence starts and acronyms.
        """
        known = {m.matched.lower() for m in self.find(text)}
        out: list[str] = []
        for match in re.finditer(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})+\b", text or ""):
            phrase = match.group(0)
            if phrase.lower() not in known:
                out.append(phrase)
        return out


# ── Schema (migration v7 lives in vector_memory._migrate_v7) ────────────────────

SCHEMA_V7 = """
CREATE TABLE IF NOT EXISTS mem_entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_deleted INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mem_entities_name ON mem_entities(name);
CREATE INDEX IF NOT EXISTS idx_mem_entities_type ON mem_entities(entity_type);

CREATE TABLE IF NOT EXISTS mem_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_kind TEXT NOT NULL,
    from_ref TEXT NOT NULL,
    to_entity TEXT,
    to_ref TEXT,
    link_type TEXT NOT NULL,
    provenance TEXT NOT NULL DEFAULT 'extracted',
    confidence REAL NOT NULL DEFAULT 1.0,
    context TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_links_to_entity ON mem_links(to_entity);
CREATE INDEX IF NOT EXISTS idx_mem_links_from ON mem_links(from_kind, from_ref);
CREATE INDEX IF NOT EXISTS idx_mem_links_type ON mem_links(link_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mem_links_edge
    ON mem_links(from_kind, from_ref, IFNULL(to_entity, ''), IFNULL(to_ref, ''), link_type);

CREATE TABLE IF NOT EXISTS mem_link_stats (
    entity_id TEXT PRIMARY KEY,
    inbound_count INTEGER NOT NULL DEFAULT 0,
    last_linked_at TEXT,
    community INTEGER
);

CREATE TABLE IF NOT EXISTS mem_entity_proposals (
    name TEXT PRIMARY KEY,
    mention_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    refs TEXT NOT NULL DEFAULT '[]'
);
"""


# ── The graph ──────────────────────────────────────────────────────────────────


class MemoryGraph:
    """Read/write surface for the entity graph inside an open memory.db.

    Deliberately takes a connection rather than owning one: the graph is *part of*
    memory.db, not a sidecar. One connection means a link write shares the record
    write's transaction and can never half-commit relative to it.
    """

    def __init__(self, db: sqlite3.Connection, *, log_event=None) -> None:
        self.db = db
        # Injected so the graph appends to the SAME reversible WAL as record
        # writes; passing None is for tests that only care about link rows.
        self._log_event = log_event

    # ── entities ──

    def upsert_entity(
        self,
        name: str,
        entity_type: str,
        *,
        aliases: "list[str] | None" = None,
        source: str = "user",
        entity_id: str | None = None,
    ) -> str:
        """Create or update an entity by name, returning its id.

        Matching is by canonical name (case-insensitive) so re-seeding from the same
        source is idempotent — a rebuild must not fork every entity in two.
        """
        if entity_type not in ENTITY_TYPES:
            raise ValueError(f"unknown entity_type {entity_type!r}")
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("entity name must not be empty")
        now = _now()
        row = self.db.execute(
            "SELECT id, aliases FROM mem_entities WHERE LOWER(name) = LOWER(?) AND is_deleted = 0",
            (clean_name,),
        ).fetchone()
        if row is not None:
            eid = row["id"]
            if aliases:
                merged = _merge_aliases(row["aliases"], aliases)
                self.db.execute(
                    "UPDATE mem_entities SET aliases = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(merged), now, eid),
                )
                self.db.commit()
            return str(eid)
        eid = entity_id or new_entity_id()
        self.db.execute(
            "INSERT INTO mem_entities (id, name, entity_type, aliases, source, "
            "created_at, updated_at, is_deleted) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (
                eid,
                clean_name,
                entity_type,
                json.dumps(sorted(set(aliases or []))),
                source,
                now,
                now,
            ),
        )
        self.db.commit()
        return eid

    def entities(self, *, include_deleted: bool = False) -> list[Entity]:
        sql = "SELECT * FROM mem_entities"
        if not include_deleted:
            sql += " WHERE is_deleted = 0"
        sql += " ORDER BY name"
        out: list[Entity] = []
        for row in self.db.execute(sql).fetchall():
            try:
                aliases = tuple(json.loads(row["aliases"] or "[]"))
            except (json.JSONDecodeError, TypeError):
                aliases = ()
            out.append(
                Entity(
                    id=row["id"],
                    name=row["name"],
                    entity_type=row["entity_type"],
                    aliases=aliases,
                    source=row["source"],
                )
            )
        return out

    def build_index(self) -> AliasIndex:
        """Compile the current entity set into a matcher."""
        index = AliasIndex()
        for entity in self.entities():
            index.add_entity(entity)
        return index

    def delete_entity(self, entity_id: str) -> bool:
        """Tombstone an entity and drop its links (the links have no meaning without it)."""
        cur = self.db.execute(
            "UPDATE mem_entities SET is_deleted = 1, updated_at = ? "
            "WHERE id = ? AND is_deleted = 0",
            (_now(), entity_id),
        )
        if not cur.rowcount:
            return False
        self.db.execute("DELETE FROM mem_links WHERE to_entity = ?", (entity_id,))
        self.db.execute("DELETE FROM mem_link_stats WHERE entity_id = ?", (entity_id,))
        self.db.commit()
        return True

    # ── links ──

    def add_link(
        self,
        *,
        from_kind: str,
        from_ref: str,
        link_type: str,
        to_entity: str | None = None,
        to_ref: str | None = None,
        provenance: str = "extracted",
        confidence: float = 1.0,
        context: str | None = None,
        source: str = "linker",
    ) -> bool:
        """Add one edge. Returns whether a NEW row was inserted.

        A duplicate edge REINFORCES the entity's rollup instead of inserting again
        — otherwise a record rewritten ten times would look ten times as connected
        and dominate ranking for no real reason.
        """
        if link_type not in LINK_TYPES:
            raise ValueError(f"unknown link_type {link_type!r}")
        if (to_entity is None) == (to_ref is None):
            raise ValueError("exactly one of to_entity / to_ref must be set")
        now = _now()
        try:
            self.db.execute(
                "INSERT INTO mem_links (from_kind, from_ref, to_entity, to_ref, link_type, "
                "provenance, confidence, context, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    from_kind,
                    from_ref,
                    to_entity,
                    to_ref,
                    link_type,
                    provenance,
                    float(confidence),
                    context,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            if to_entity:
                self._touch_stats(to_entity, now)
            return False
        if to_entity:
            self._bump_stats(to_entity, now)
        self.db.commit()
        if self._log_event is not None:
            # Reversibility: the WAL row carries enough to reconstruct the edge.
            payload = json.dumps(
                {
                    "from_kind": from_kind,
                    "from_ref": from_ref,
                    "to_entity": to_entity,
                    "to_ref": to_ref,
                    "link_type": link_type,
                }
            )
            self._log_event("link_add", "link", from_ref, None, payload, source)
        return True

    def remove_link(self, link_id: int, *, source: str = "user_explicit") -> bool:
        row = self.db.execute("SELECT * FROM mem_links WHERE id = ?", (link_id,)).fetchone()
        if row is None:
            return False
        self.db.execute("DELETE FROM mem_links WHERE id = ?", (link_id,))
        if row["to_entity"]:
            self.db.execute(
                "UPDATE mem_link_stats SET inbound_count = MAX(0, inbound_count - 1) "
                "WHERE entity_id = ?",
                (row["to_entity"],),
            )
        self.db.commit()
        if self._log_event is not None:
            payload = json.dumps(
                {
                    "from_kind": row["from_kind"],
                    "from_ref": row["from_ref"],
                    "to_entity": row["to_entity"],
                    "to_ref": row["to_ref"],
                    "link_type": row["link_type"],
                }
            )
            self._log_event("link_remove", "link", row["from_ref"], payload, None, source)
        return True

    def links_from(self, from_kind: str, from_ref: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM mem_links WHERE from_kind = ? AND from_ref = ? ORDER BY id",
            (from_kind, from_ref),
        ).fetchall()
        return [dict(r) for r in rows]

    def backlinks(self, entity_id: str, *, limit: int = 100) -> list[dict]:
        """Records that link TO this entity — the graph recall arm's input (S2)."""
        rows = self.db.execute(
            "SELECT * FROM mem_links WHERE to_entity = ? ORDER BY id DESC LIMIT ?",
            (entity_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def drop_links_for(self, from_kind: str, from_ref: str) -> int:
        """Remove a record's outbound links (used before re-linking on rewrite)."""
        rows = self.db.execute(
            "SELECT to_entity FROM mem_links WHERE from_kind = ? AND from_ref = ?",
            (from_kind, from_ref),
        ).fetchall()
        cur = self.db.execute(
            "DELETE FROM mem_links WHERE from_kind = ? AND from_ref = ?", (from_kind, from_ref)
        )
        for row in rows:
            if row["to_entity"]:
                self.db.execute(
                    "UPDATE mem_link_stats SET inbound_count = MAX(0, inbound_count - 1) "
                    "WHERE entity_id = ?",
                    (row["to_entity"],),
                )
        self.db.commit()
        return int(cur.rowcount or 0)

    def resolve_query(self, text: str, index: "AliasIndex | None" = None) -> list[str]:
        """Entity ids named in ``text`` — the graph arm's entry point (§2.1).

        Uses the SAME matcher as write time, so a query resolves exactly the way the
        record that mentioned it did. Without that symmetry the arm would find records
        it never linked.
        """
        matcher = index if index is not None else self.build_index()
        seen: list[str] = []
        for mention in matcher.find(text or ""):
            if mention.entity_id not in seen:
                seen.append(mention.entity_id)
        return seen

    def recall_evidence(self, text: str, *, index: "AliasIndex | None" = None) -> dict:
        """``{from_ref: [entity names]}`` — WHY the graph surfaced each record (§2.2).

        The debuggability contract: a recall hit whose relevance came from a link
        should be able to say which entity connected it, or "the graph found it" is an
        unfalsifiable claim.
        """
        out: dict[str, list[str]] = {}
        by_id = {e.id: e.name for e in self.entities()}
        for entity_id in self.resolve_query(text, index):
            name = by_id.get(entity_id, entity_id)
            for link in self.backlinks(entity_id, limit=60):
                out.setdefault(link["from_ref"], [])
                if name not in out[link["from_ref"]]:
                    out[link["from_ref"]].append(name)
        return out

    def recall_refs(self, text: str, *, index: "AliasIndex | None" = None, limit: int = 60) -> dict:
        """Record refs reachable from the entities named in ``text``.

        Returns ``{from_ref: boost}`` — `β·log1p(inbound_count)`, floored at
        `GRAPH_BOOST_FLOOR` because the query NAMED the entity. Bounded on both sides:
        the floor clears the incidental keyword noise an unrelated record earns from
        stopword overlap, while the magnitude stays under what a genuine keyword match
        scores, so words the user typed still win.
        """
        import math

        entity_ids = self.resolve_query(text, index)
        if not entity_ids:
            return {}
        boosts: dict[str, float] = {}
        for entity_id in entity_ids:
            inbound = int(self.stats(entity_id).get("inbound_count", 0) or 0)
            weight = max(GRAPH_BOOST_FLOOR, GRAPH_BOOST_BETA * math.log1p(inbound))
            for link in self.backlinks(entity_id, limit=limit):
                ref = link["from_ref"]
                # A record linked to several named entities is more relevant than one
                # linked to a single match, so boosts accumulate.
                boosts[ref] = boosts.get(ref, 0.0) + weight
        return boosts

    def stats(self, entity_id: str) -> dict:
        row = self.db.execute(
            "SELECT * FROM mem_link_stats WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        return dict(row) if row else {"entity_id": entity_id, "inbound_count": 0}

    def _bump_stats(self, entity_id: str, now: str) -> None:
        self.db.execute(
            "INSERT INTO mem_link_stats (entity_id, inbound_count, last_linked_at) "
            "VALUES (?, 1, ?) ON CONFLICT(entity_id) DO UPDATE SET "
            "inbound_count = inbound_count + 1, last_linked_at = ?",
            (entity_id, now, now),
        )

    def _touch_stats(self, entity_id: str, now: str) -> None:
        """Reinforce without inflating the count (duplicate-edge case)."""
        self.db.execute(
            "INSERT INTO mem_link_stats (entity_id, inbound_count, last_linked_at) "
            "VALUES (?, 1, ?) ON CONFLICT(entity_id) DO UPDATE SET last_linked_at = ?",
            (entity_id, now, now),
        )
        self.db.commit()

    # ── entity proposals (the notability gate) ──

    def tally_proposal(self, name: str, from_ref: str) -> int:
        """Count an unknown name toward its promotion threshold. Returns the count.

        Counts DISTINCT records, not mentions: a single chatty record repeating a
        name ten times is not evidence that the name matters.
        """
        now = _now()
        row = self.db.execute(
            "SELECT mention_count, refs FROM mem_entity_proposals WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            self.db.execute(
                "INSERT INTO mem_entity_proposals (name, mention_count, first_seen_at, "
                "last_seen_at, refs) VALUES (?, 1, ?, ?, ?)",
                (name, now, now, json.dumps([from_ref])),
            )
            self.db.commit()
            return 1
        try:
            refs = list(json.loads(row["refs"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            refs = []
        if from_ref in refs:
            return int(row["mention_count"])
        refs.append(from_ref)
        count = len(refs)
        self.db.execute(
            "UPDATE mem_entity_proposals SET mention_count = ?, last_seen_at = ?, refs = ? "
            "WHERE name = ?",
            (count, now, json.dumps(refs[:50]), name),
        )
        self.db.commit()
        return count

    def proposals(self, *, threshold: int = PROPOSAL_THRESHOLD) -> list[dict]:
        """Unknown names that have recurred enough to be worth a human decision.

        A name that has SINCE become an entity — accepted here, seeded from facts,
        or typed in by hand — is no longer a proposal. Filtering at read time rather
        than requiring every entity-creation path to remember to clear the tally:
        one query can't drift, four call sites can. Stale rows are dropped as we
        find them so the tally doesn't grow forever.
        """
        rows = self.db.execute(
            "SELECT * FROM mem_entity_proposals WHERE mention_count >= ? "
            "ORDER BY mention_count DESC, name",
            (threshold,),
        ).fetchall()
        known = {e.name.lower() for e in self.entities()}
        for entity in self.entities():
            known.update(a.lower() for a in entity.aliases)
        out: list[dict] = []
        stale: list[str] = []
        for row in rows:
            if str(row["name"]).lower() in known:
                stale.append(row["name"])
            else:
                out.append(dict(row))
        if stale:
            self.db.executemany(
                "DELETE FROM mem_entity_proposals WHERE name = ?", [(n,) for n in stale]
            )
            self.db.commit()
        return out

    def accept_proposal(self, name: str, entity_type: str, *, source: str = "user") -> str:
        """Promote a proposal to a real entity and clear its tally."""
        eid = self.upsert_entity(name, entity_type, source=source)
        self.db.execute("DELETE FROM mem_entity_proposals WHERE name = ?", (name,))
        self.db.commit()
        return eid

    def reject_proposal(self, name: str) -> bool:
        cur = self.db.execute("DELETE FROM mem_entity_proposals WHERE name = ?", (name,))
        self.db.commit()
        return bool(cur.rowcount)

    # ── lint inputs (§2.3) ──

    def orphan_counts(self) -> dict:
        """Records with no links, and entities with no backlinks.

        Reported, never auto-fixed: an orphan is usually a gap in the entity set,
        and deleting the record would destroy the evidence of that gap.
        """
        semantic_orphans = self.db.execute(
            "SELECT COUNT(*) AS n FROM semantic_memory s WHERE s.is_deleted = 0 "
            "AND NOT EXISTS (SELECT 1 FROM mem_links l WHERE l.from_kind = 'semantic' "
            "AND l.from_ref = s.key)"
        ).fetchone()["n"]
        episodic_orphans = self.db.execute(
            "SELECT COUNT(*) AS n FROM episodic_memories e WHERE e.is_deleted = 0 "
            "AND NOT EXISTS (SELECT 1 FROM mem_links l WHERE l.from_kind = 'episodic' "
            "AND l.from_ref = e.id)"
        ).fetchone()["n"]
        phantoms = self.db.execute(
            "SELECT COUNT(*) AS n FROM mem_entities e WHERE e.is_deleted = 0 "
            "AND NOT EXISTS (SELECT 1 FROM mem_links l WHERE l.to_entity = e.id)"
        ).fetchone()["n"]
        return {
            "semantic_orphans": int(semantic_orphans),
            "episodic_orphans": int(episodic_orphans),
            "phantom_entities": int(phantoms),
        }

    def summary(self) -> dict:
        """Graph size, for the health tab and the backfill's before/after report."""
        one = lambda sql: int(self.db.execute(sql).fetchone()[0])  # noqa: E731
        return {
            "entities": one("SELECT COUNT(*) FROM mem_entities WHERE is_deleted = 0"),
            "links": one("SELECT COUNT(*) FROM mem_links"),
            "linked_records": one("SELECT COUNT(DISTINCT from_ref) FROM mem_links"),
            "proposals": one("SELECT COUNT(*) FROM mem_entity_proposals"),
            **self.orphan_counts(),
        }


def _merge_aliases(existing_json: str | None, incoming: "list[str]") -> list[str]:
    try:
        current = set(json.loads(existing_json or "[]"))
    except (json.JSONDecodeError, TypeError):
        current = set()
    current.update(a for a in incoming if a and a.strip())
    return sorted(current)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).isoformat()
