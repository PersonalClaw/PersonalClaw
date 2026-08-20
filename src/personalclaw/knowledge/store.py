"""KnowledgeStore -- SQLite backed knowledge graph with lightweight in-memory graph."""

import json
import logging
import pathlib
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from personalclaw.sqlite_compat import FTS5_REMEDY, probe, sqlite3

from .vector_index import ChunkVectorIndex

logger = logging.getLogger(__name__)

# Query params that only track marketing/analytics — never identify the resource.
# Stripped during URL normalization so a link saved with a tracking tag dedups
# against the same link saved without it.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "utm_name",
        "utm_reader",
        "fbclid",
        "gclid",
        "gclsrc",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "ref",
        "ref_src",
        "ref_url",
        "yclid",
        "_hsenc",
        "_hsmi",
        "vero_id",
        "spm",
    }
)


def _clean_tag_names(tags) -> list[str]:
    """Caller-supplied tags → the names to store: strings only, stripped, blanks and
    duplicates dropped, first-seen order kept.

    One funnel for every write path (create, update, the agent tools, the HTTP handlers)
    so no surface can put a blank or a duplicate into the tag tables. Blanks were already
    invisible before this change — ``all_tags`` has always filtered falsy names out of
    its results — so dropping them at the boundary matches observed behavior.
    """
    out: list[str] = []
    for entry in tags or []:
        if not isinstance(entry, str):
            continue
        name = entry.strip()
        if name and name not in out:
            out.append(name)
    return out


def _fts_tags(names: list[str]) -> str:
    """The value indexed in the FTS `tags` column: names joined by spaces.

    Recall is unaffected by the switch from the old JSON string. FTS5's default
    unicode61 tokenizer treats ``[``, ``]``, ``"`` and ``,`` as separators, so
    ``'["python", "cli"]'`` and ``'python cli'`` produce identical terms at identical
    offsets (measured, including phrase and NEAR queries).

    It does FIX one thing: ``json.dumps`` defaults to ``ensure_ascii=True``, so a tag
    like ``日本語`` was stored — and therefore indexed — as ``\\u65e5\\u672c\\u8a9e``,
    making it unsearchable. Joined names index the real characters.
    """
    return " ".join(names)


def _sim2(value: float) -> str:
    """A similarity as two decimals, TRUNCATED rather than rounded — 0.999 must not print as
    "1.00" on a surface whose next control deletes a document. Rounding up to a flat 1.00 tells
    the user the two copies are identical, which is a stronger claim than the scorer made."""
    return f"{int(max(0.0, min(1.0, value)) * 100) / 100:.2f}"


def _dup_reason(filename_sim: float, cosine: float) -> str:
    """The per-candidate account of a near-duplicate match, as the UI renders it verbatim.

    This is UI copy that happens to live in Python, so it is written for the person deciding
    whether to delete one of two documents — not as the rule's internal name. It replaced
    ``DupVerdict.reason``, which on the positive branch is a single constant
    ("fuzzy dup (filename+cosine+date-gate)") shown identically for a 0.90 match and a 1.00 one.

    The exact-title case is the DEFINING one, not an edge case: the filename leg gates at 0.85
    Jaccard over date-stripped stems, so a surfaced candidate very often shares the anchor's
    title outright. "Title similarity 1.00" is a strange way to say that, so it says "Same title"
    and spends the words on the leg the user cannot see for themselves.
    """
    title = "Same title" if filename_sim >= 1.0 else f"Title similarity {_sim2(filename_sim)}"
    return f"{title} · content similarity {_sim2(cosine)}"


def normalize_url(url: str) -> str:
    """Canonicalize a URL for dedup: lowercase scheme+host, drop a default port and a
    bare trailing slash, sort query params and strip marketing/tracking ones, drop the
    fragment. Returns the input unchanged if it isn't parseable as http(s). So
    ``https://Example.com/`` and ``https://example.com?utm_source=x`` both canonicalize
    to ``https://example.com`` — saving either one dedups against the other."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    raw = (url or "").strip()
    if not raw:
        return raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return raw  # mailto:, relative, or junk — leave as-is
    host = parts.hostname or ""
    netloc = host.lower()
    if parts.port and not (
        (parts.scheme == "http" and parts.port == 80)
        or (parts.scheme == "https" and parts.port == 443)
    ):
        netloc = f"{netloc}:{parts.port}"
    if parts.username:
        cred = parts.username + (f":{parts.password}" if parts.password else "")
        netloc = f"{cred}@{netloc}"
    path = parts.path
    if path == "/":
        path = ""
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(sorted(kept))
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


class _NodeView:
    """Minimal node-attribute view supporting get, subscript, iteration, and len."""

    def __init__(self, data: dict[str, dict]):
        self._data = data

    def get(self, nid: str, default: dict | None = None) -> dict:
        return self._data.get(nid, default if default is not None else {})

    def __getitem__(self, nid: str) -> dict:
        return self._data[nid]

    def __contains__(self, nid: str) -> bool:
        return nid in self._data

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


class _EdgeView:
    """Minimal edge view supporting iteration and subscript access."""

    def __init__(self, fwd: dict[str, dict[str, dict]]):
        self._fwd = fwd

    def __getitem__(self, key: tuple[str, str]) -> dict:
        u, v = key
        return self._fwd[u][v]

    def __call__(self, *, data: bool = False):  # noqa: ARG002
        for u, targets in self._fwd.items():
            for v, attrs in targets.items():
                yield u, v, attrs


class SimpleDiGraph:
    """Minimal directed graph replacing networkx.DiGraph for the subset of API we use."""

    def __init__(self) -> None:
        self._node_attrs: dict[str, dict] = {}
        self._fwd: dict[str, dict[str, dict]] = defaultdict(dict)
        self._rev: dict[str, dict[str, dict]] = defaultdict(dict)
        self.nodes = _NodeView(self._node_attrs)
        self.edges = _EdgeView(self._fwd)

    def clear(self) -> None:
        self._node_attrs.clear()
        self._fwd.clear()
        self._rev.clear()

    def add_node(self, nid: str, **attrs: object) -> None:
        self._node_attrs[nid] = attrs

    def add_edge(self, u: str, v: str, **attrs: object) -> None:
        self._fwd[u][v] = attrs
        self._rev[v][u] = attrs

    def has_edge(self, u: str, v: str) -> bool:
        return v in self._fwd.get(u, {})

    def has_node(self, nid: str) -> bool:
        return nid in self._node_attrs

    def degree(self, nid: str) -> int:
        return len(self._fwd.get(nid, {})) + len(self._rev.get(nid, {}))

    def successors(self, nid: str):
        return iter(self._fwd.get(nid, {}))

    def predecessors(self, nid: str):
        return iter(self._rev.get(nid, {}))


def knowledge_db_path(home: "pathlib.Path | None" = None, *, create: bool = True) -> pathlib.Path:
    """THE knowledge database path. One store, one path.

    `<home>/workspace/knowledge/knowledge.db` — the path the dashboard's `AppState` has always
    used. Measured live: the providers were opening `<home>/knowledge/knowledge.db` instead, so a
    workflow wrote to a second database the UI could never read. Both writes "succeeded", both
    reads "worked", and the store the user browsed simply never contained what their workflows
    persisted. A split-brain with no error on either side.

    Every knowledge reader and writer must come through here rather than composing the path
    again, because a second copy of the path is how the split-brain happened in the first place.
    ``tests/test_knowledge_contradiction.py`` enforces that as a lint over the whole package.

    ``home`` overrides the home directory for a caller that is handed one (the Doctor gets a
    ``DoctorContext.home``, which is the real home in the gateway and a tmp dir under test), and
    ``create=False`` suppresses the parent ``mkdir`` — a read-only prober must not leave a
    directory behind, since the state-inventory probe would then have an unclaimed path to
    report. Both exist so those callers can still come through here rather than recomposing.
    """
    from personalclaw.config.loader import config_dir

    root = home if home is not None else config_dir()
    path = root / "workspace" / "knowledge" / "knowledge.db"
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


class KnowledgeStore:
    def __init__(self, db_path: str):
        # FTS5 is ESSENTIAL here: the schema creates the `items_fts` virtual table and
        # every knowledge search (store.search_items_fts, HybridRetriever, the agent
        # knowledge tools) goes through it — there is no non-FTS fallback, so a build
        # without FTS5 cannot open a usable store. Fail AT INIT with the fixed remedy
        # rather than letting the CREATE VIRTUAL TABLE (or a later MATCH) throw a raw
        # traceback mid-query. Checked once per open; probe() is memoized.
        if not probe().fts5:
            raise RuntimeError(FTS5_REMEDY)
        self.db_path = db_path
        # check_same_thread=False: the process-wide store (get_knowledge_store) is
        # touched from both the event loop and run_in_executor threads (agent tools).
        # Access is serialized by the single ingest queue + WAL + busy_timeout, so
        # cross-thread use is safe; without this it raises ProgrammingError.
        self.db = sqlite3.connect(
            db_path, timeout=30, isolation_level=None, check_same_thread=False
        )
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.row_factory = sqlite3.Row
        self.graph = SimpleDiGraph()
        self._init_schema()
        self._migrate()
        self._load_graph()
        # The chunk ANN index (KL-11) lives in THIS database file, so a chunk write and its
        # vector write travel together instead of needing a sidecar's consistency story.
        # Construction loads nothing — the sqlite-vec extension is loaded lazily on first
        # index use, so opening a store on a SQLite build that cannot load extensions costs
        # nothing and degrades to the exact scan.
        self.vec_index = ChunkVectorIndex(self.db)

    def _init_schema(self):
        self.db.executescript("""
            -- One item = one logical document. There is no `sources` table and no
            -- `chunk_index`; chunking lives only in the embedding pipeline. Sourcing
            -- is per-item attribution (provider + url/file_path).
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                item_type TEXT NOT NULL,
                summary TEXT,
                embedding BLOB,
                status TEXT DEFAULT 'active',
                -- first-class typed-item fields (P6b)
                gist_language TEXT,
                url TEXT, url_title TEXT, url_description TEXT,
                mime_type TEXT, file_size INTEGER, thumbnail_path TEXT,
                file_path TEXT, file_metadata TEXT DEFAULT '{}',
                word_count INTEGER DEFAULT 0,
                is_pinned INTEGER DEFAULT 0, is_archived INTEGER DEFAULT 0,
                insights TEXT DEFAULT '{}',
                ai_title TEXT,
                provider TEXT DEFAULT 'native',
                -- ingestion node-graph lifecycle (#30)
                processing_status TEXT DEFAULT '', processing_error TEXT,
                -- library curation (KNOWLEDGE-LIBRARY S1)
                read_state TEXT DEFAULT 'unread', favorited INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);

            -- Collections (KNOWLEDGE-LIBRARY S1). A shelf is either MANUAL (an explicit
            -- membership list) or SMART (a saved query re-run on read), which is why the
            -- query lives on the collection rather than membership rows existing for it.
            CREATE TABLE IF NOT EXISTS collections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'manual',   -- manual | smart
                query TEXT DEFAULT '',                 -- smart only
                icon TEXT DEFAULT '',
                position INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            -- Membership. The composite PK makes add-twice a no-op rather than a
            -- duplicate row, and ON DELETE CASCADE means deleting a shelf never leaves
            -- orphan rows pointing at it.
            CREATE TABLE IF NOT EXISTS collection_items (
                collection_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (collection_id, item_id),
                FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_collection_items_item ON collection_items(item_id);
            CREATE INDEX IF NOT EXISTS idx_collections_position ON collections(position);

            -- Tags (KNOWLEDGE-LIBRARY S2, T2.2). AUTHORITATIVE: tags live here, not in a
            -- JSON column on `items` (that column is dropped by _migrate). A surrogate
            -- integer id with `name` merely UNIQUE — rather than `name` as the PK — is
            -- what makes RENAME a single-row update instead of a cascade across every
            -- membership row and every child's parent pointer.
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                -- Adjacency-list hierarchy. NULL (not '') = a root tag: NULL is what the
                -- self-FK can express, and it keeps "no parent" out of the name space.
                -- ON DELETE SET NULL re-parents children to root rather than cascading,
                -- so deleting a parent never silently deletes the tags underneath it
                -- (the chat-folders precedent, plus the cycle guard that one lacks).
                parent_id INTEGER REFERENCES tags(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tags_parent ON tags(parent_id);

            -- Membership. Composite PK makes tagging twice a no-op. `source` records
            -- PROVENANCE explicitly ('user' | 'ai') so the enrichment pipeline no longer
            -- has to INFER whether it may overwrite a tag by comparing the item's
            -- current tags against the previous run's topics — an ordered-list equality
            -- that rows would have broken (see pipeline/runner.py).
            CREATE TABLE IF NOT EXISTS item_tags (
                item_id TEXT NOT NULL,
                tag_id INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'user',
                added_at TEXT NOT NULL,
                PRIMARY KEY (item_id, tag_id),
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag_id);

            -- The FTS source is a VIEW that flattens each item's tags back into one
            -- searchable string. This is what keeps `tags` a searchable column after the
            -- JSON column is gone: an external-content FTS table's column list is fixed
            -- at creation and its `content=` target must be able to produce every column.
            -- Pointing it at `items` with a `tags` column that no longer exists makes
            -- 'rebuild' WIPE THE INDEX AND REPORT SUCCESS (measured; integrity-check
            -- afterwards still says ok), so the view is a correctness requirement, not
            -- tidiness. Ordering by name gives the flattened string a deterministic
            -- shape, which keeps the manual sync sites' delete-then-insert honest.
            CREATE VIEW IF NOT EXISTS items_fts_src AS
                SELECT i.rowid AS rowid, i.title AS title, i.content AS content,
                       (SELECT COALESCE(group_concat(t.name, ' '), '')
                          FROM item_tags it JOIN tags t ON t.id = it.tag_id
                         WHERE it.item_id = i.id
                         ORDER BY t.name) AS tags
                  FROM items i;

            CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
                title, content, tags, content=items_fts_src, content_rowid=rowid
            );

            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                description TEXT,
                aliases TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);

            CREATE TABLE IF NOT EXISTS entity_relations (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES entities(id),
                target_id TEXT NOT NULL REFERENCES entities(id),
                relation_type TEXT NOT NULL,
                description TEXT,
                weight REAL DEFAULT 1.0,
                source_item_id TEXT REFERENCES items(id),
                created_at TEXT NOT NULL
            );

            -- Typed ITEM-level edges (KNOWLEDGE-SYNTHESIS §3.2), sibling to the
            -- entity-level table below. Deliberately item-fields-plus-report rather than a
            -- graph database: five verbs, upserted on (source, target, relation).
            CREATE TABLE IF NOT EXISTS item_relations (
                source_item_id TEXT NOT NULL REFERENCES items(id),
                target_item_id TEXT NOT NULL REFERENCES items(id),
                relation_type TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                provenance TEXT DEFAULT 'extracted',
                created_at TEXT NOT NULL,
                PRIMARY KEY (source_item_id, target_item_id, relation_type)
            );

            CREATE INDEX IF NOT EXISTS idx_item_relations_source
                ON item_relations(source_item_id);
            CREATE INDEX IF NOT EXISTS idx_item_relations_target
                ON item_relations(target_item_id);

            -- Per-MARKER attribution for a synthesized item (WF2KNO-11). Sibling to
            -- item_relations, and deliberately NOT the same thing: a relation says two items
            -- are connected, a citation says WHICH numbered source supports which sentence.
            -- The synthesis path used to store the whole retrieved set as its "citations",
            -- which answers "what did we look at" and cannot answer the question a reader
            -- challenging a claim asks. Keyed on (item_id, marker) because the marker number
            -- the prompt displayed is the identity -- one source cited in three sentences is
            -- one row, and a re-synthesis reusing marker 2 for a different source overwrites
            -- rather than accumulating two answers for [2].
            --
            -- No REFERENCES items(id) on source_item_id on purpose: a source deleted after
            -- the synthesis was written should leave the attribution readable ("this claim
            -- cited an item that is gone") rather than have foreign_keys=ON refuse the
            -- delete or cascade the evidence away.
            CREATE TABLE IF NOT EXISTS item_citations (
                item_id TEXT NOT NULL,
                marker INTEGER NOT NULL,
                source_item_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL DEFAULT -1,
                excerpt TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (item_id, marker)
            );

            -- "What else cites this item" -- the reverse lookup, which has no covering index
            -- from the primary key.
            CREATE INDEX IF NOT EXISTS idx_item_citations_source
                ON item_citations(source_item_id);

            CREATE INDEX IF NOT EXISTS idx_entity_relations_source_id
                ON entity_relations(source_id);
            CREATE INDEX IF NOT EXISTS idx_entity_relations_target_id
                ON entity_relations(target_id);

            CREATE TABLE IF NOT EXISTS mentions (
                item_id TEXT NOT NULL REFERENCES items(id),
                entity_id TEXT NOT NULL REFERENCES entities(id),
                context TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (item_id, entity_id)
            );

            -- Reading annotations (KNOWLEDGE-LIBRARY S3, T3.1). The plan left this an
            -- open question — "annotations as `mentions` vs a dedicated `annotations`
            -- table — default: reuse `mentions`; promote to its own table only if
            -- reading-notes need richer structure (revisit in S3)" — and S3 is where it
            -- gets answered: its own table. `mentions` is (item_id, entity_id) keyed, so
            -- storing a highlight there would require MINTING AN ENTITY per highlighted
            -- sentence, which would put reading debris into the entity graph, the
            -- `/entities` surfaces and orphan-pruning. A highlight is also not an
            -- entity↔item edge: it needs a re-anchoring locator (`quote` + which
            -- `occurrence` of it) that `mentions.context` cannot express.
            --
            -- Anchoring is by TEXT, not character offset: the reader renders markdown, so
            -- offsets into the raw source do not survive the transform. `occurrence` is
            -- the 0-based index of this quote among identical strings in the rendered
            -- article, which is what makes two highlights of the same repeated sentence
            -- distinct rows. An edited body may orphan an anchor — the row survives and
            -- still lists, it just stops re-marking, which is the honest failure.
            -- ON DELETE CASCADE: deleting the item takes its highlights with it.
            CREATE TABLE IF NOT EXISTS annotations (
                id TEXT PRIMARY KEY,
                item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                quote TEXT NOT NULL,
                occurrence INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_annotations_item ON annotations(item_id);

            -- Extracted-content pool (knowledge node-graph engine, #30). Each row is
            -- one node's output for an item — the drillable per-item bundle the
            -- ingestion DAG produces (transcript, video-text, pdf-table, …). Many
            -- rows per item; insights + chunk/embed read the whole bundle.
            CREATE TABLE IF NOT EXISTS extracted_contents (
                id TEXT PRIMARY KEY,
                item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                node_type TEXT NOT NULL,
                backend TEXT DEFAULT '',
                text TEXT DEFAULT '',
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_extracted_item_id ON extracted_contents(item_id);

            -- Chunk index (KL-9). ADDITIVE to the item's whole-item embedding: the item
            -- row keeps its own vector, and each chunk carries a vector over a
            -- structural slice of the document so retrieval can reach content deep in a
            -- long doc and cite it to a section/line span. `chunk_index` is the 0..N-1
            -- order within the item (a chunker detail, distinct from the retired legacy
            -- source/chunk model). ON DELETE CASCADE means a deleted item drops its
            -- chunks with it.
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                embedding BLOB,
                section TEXT,
                line_start INTEGER,
                line_end INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_item_id ON chunks(item_id);

            -- Intent outcomes (Tier-3, redesign). A natural-language intent's match
            -- against one item, stored BY VALUE: the takeaway + typed fields + a
            -- denormalized item title are copied in. item_id is a SOFT back-reference
            -- (nullable, no cascade) — deleting the item or disconnecting a provider
            -- nulls the ref but never loses the gathered insight.
            CREATE TABLE IF NOT EXISTS intent_outcomes (
                id TEXT PRIMARY KEY,
                intent_id TEXT NOT NULL,
                intent_name TEXT DEFAULT '',
                item_id TEXT,
                item_title TEXT DEFAULT '',
                takeaway TEXT DEFAULT '',
                fields TEXT DEFAULT '[]',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_intent_outcomes_intent ON intent_outcomes(intent_id);
            CREATE INDEX IF NOT EXISTS idx_intent_outcomes_item ON intent_outcomes(item_id);

        """)
        self.db.commit()

    # First-class typed-item columns added in P6b (knowledge-entity-vision). Each
    # is nullable/defaulted so older DBs migrate transparently. ``item_type`` stays
    # the storage column; the API exposes it as ``type`` (the 12-value enum).
    _NEW_ITEM_COLUMNS = (
        ("gist_language", "TEXT"),
        ("url", "TEXT"),
        ("url_title", "TEXT"),
        ("url_description", "TEXT"),
        ("mime_type", "TEXT"),
        ("file_size", "INTEGER"),
        ("thumbnail_path", "TEXT"),
        ("file_path", "TEXT"),
        ("file_metadata", "TEXT DEFAULT '{}'"),
        # KNOWLEDGE-SYNTHESIS §2.1: the typed taxonomy, distinct from `item_type` (which
        # routes the ingestion graph). Nullable so every existing row stays valid.
        ("kind", "TEXT"),
        # The derived logical identity `{kind}:{normalized_title}` (semantics.logical_key).
        # Indexed, because lookup-before-write happens on every persist and a table scan
        # there would make idempotency cost more than the duplicate it prevents.
        ("logical_key", "TEXT"),
        # A hash of what was persisted, so a retried/rewound write is a no-op rather than
        # a second copy.
        ("content_hash", "TEXT"),
        # Separate from `updated_at`: an item re-CHECKED yesterday is fresh even if it was
        # written a year ago, and collapsing the two loses exactly that distinction.
        ("last_verified", "TEXT"),
        # Optional absolute expiry for knowledge that goes stale on a known clock.
        ("expires_at", "TEXT"),
        ("word_count", "INTEGER DEFAULT 0"),
        ("is_pinned", "INTEGER DEFAULT 0"),
        ("is_archived", "INTEGER DEFAULT 0"),
        ("insights", "TEXT DEFAULT '{}'"),
        ("ai_title", "TEXT"),
        ("provider", "TEXT DEFAULT 'native'"),
        # Ingestion node-graph lifecycle (#30): queued|processing|done|failed|partial.
        ("processing_status", "TEXT DEFAULT ''"),
        ("processing_error", "TEXT"),
        # Library curation (KNOWLEDGE-LIBRARY S1). Read state is a THREE-value cycle,
        # not a boolean: "reading" is the state a reading list exists to represent, and
        # collapsing it into unread/read is what makes such lists useless.
        ("read_state", "TEXT DEFAULT 'unread'"),
        ("favorited", "INTEGER DEFAULT 0"),
    )

    def _migrate(self):
        """Add columns missing in older DBs, and drop the legacy source/chunk model
        (one item = one logical doc; sourcing is per-item attribution)."""
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(items)").fetchall()}
        # Drop the legacy `namespace` column: it only ever held 'default' and drove just
        # optional filtering — never the cwd/workspace partitioning it was meant for. The
        # index goes too. (SQLite ≥3.35 supports DROP COLUMN; ignore on older engines —
        # a dormant column is harmless, the code no longer references it.)
        if "namespace" in cols:
            self.db.execute("DROP INDEX IF EXISTS idx_items_namespace")
            try:
                self.db.execute("ALTER TABLE items DROP COLUMN namespace")
            except sqlite3.OperationalError:
                pass
        for col, decl in self._NEW_ITEM_COLUMNS:
            if col not in cols:
                self.db.execute(f"ALTER TABLE items ADD COLUMN {col} {decl}")
        # The logical-key index is created HERE rather than in the schema block, because
        # that block runs before `_migrate` adds the column — a CREATE INDEX there fails on a
        # fresh db and silently no-ops on an upgraded one. Creating it after the ALTERs is the
        # only ordering that works for both. Lookup-before-write runs on every persist, so
        # without this the plan is a full SCAN (measured, not assumed).
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_items_logical_key ON items(logical_key)")
        # Clean break: drop the legacy chunk/source model from existing DBs. A
        # previously-chunked doc collapses to its first row (chunk_index 0); the
        # extra chunk rows + their mentions/relations are removed. Legacy tables are
        # dropped first so deleting chunk-item rows can't trip a stale FK. FK
        # enforcement is suspended for the structural rewrite (toggle outside any txn).
        #
        # Keyed on `chunk_index` ALONE — not `source_id` — because WATCHED-SOURCES §3.3
        # reclaims `items.source_id` (and the `sources` table name) with new meaning: a
        # WatchedSource item's origin identity. The legacy chunk model ALWAYS carried
        # `chunk_index`, so it is the reliable marker; keying on `source_id` too would
        # make this block drop the WS-2 column (added below in `_migrate_sources`) on
        # every reopen. The WS-2 schema is created AFTER this block for the same reason.
        if "chunk_index" in cols:
            self.db.execute("PRAGMA foreign_keys=OFF")
            self.db.execute("BEGIN")
            try:
                self.db.execute("DROP TABLE IF EXISTS source_locations")
                self.db.execute("DROP TABLE IF EXISTS ingestion_jobs")
                self.db.execute("DROP TABLE IF EXISTS sources")
                # Drop dependents of the chunk rows we're about to remove.
                chunk_items = "SELECT id FROM items WHERE COALESCE(chunk_index, 0) <> 0"
                self.db.execute(
                    f"DELETE FROM mentions WHERE item_id IN ({chunk_items})"
                )  # noqa: S608
                self.db.execute(
                    f"DELETE FROM entity_relations WHERE source_item_id IN ({chunk_items})"
                )  # noqa: S608
                self.db.execute(
                    f"DELETE FROM extracted_contents WHERE item_id IN ({chunk_items})"
                )  # noqa: S608
                self.db.execute("DELETE FROM items WHERE COALESCE(chunk_index, 0) <> 0")
                self.db.execute("DROP INDEX IF EXISTS idx_items_source_id")
                for col in ("source_id", "chunk_index"):
                    if col in cols:
                        self.db.execute(f"ALTER TABLE items DROP COLUMN {col}")
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            finally:
                self.db.execute("PRAGMA foreign_keys=ON")
        if "tags" in cols:
            self._migrate_tags_to_rows()
        # WATCHED-SOURCES §1.2/§3.3 — add the source store LAST, after the legacy chunk
        # block above has dropped the old `sources` table + `source_id` column. Ordering
        # is load-bearing: the legacy DROP keys on `chunk_index` (not `source_id`) so it
        # can't clobber these, and creating the WS-2 schema here (not in `_init_schema`)
        # means a `DROP TABLE sources` on an upgrading DB is always followed by the fresh
        # CREATE rather than racing it.
        self._migrate_sources()
        # Prune orphan entities (no mentions/relations) + stale relations.
        self.db.execute("BEGIN")
        try:
            self.db.execute(
                "DELETE FROM entity_relations WHERE source_id NOT IN (SELECT id FROM entities) OR target_id NOT IN (SELECT id FROM entities)"  # noqa: E501
            )
            self.db.execute("""
                DELETE FROM entities WHERE id NOT IN (SELECT entity_id FROM mentions)
                AND id NOT IN (SELECT source_id FROM entity_relations)
                AND id NOT IN (SELECT target_id FROM entity_relations)
            """)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _migrate_tags_to_rows(self):
        """Move tags from the legacy `items.tags` JSON column into `tags`/`item_tags`,
        then DROP the column. Clean break: one pass, no dual-path.

        Guarded by the caller on ``"tags" in cols``, which is the whole idempotence
        mechanism — knowledge.db has no schema version to gate on, matching how
        ``_init_schema`` relies on ``IF NOT EXISTS``.

        Three deliberate choices, each guarding a way this could silently lose data:

        1. **A per-row Python parse, not SQL ``json_each``.** ``json_each`` RAISES on a
           malformed value, and malformed values genuinely exist: ``_serialize_item`` has
           always swallowed ``JSONDecodeError``, so the store has tolerated them for the
           column's whole life. SQL would abort the migration (or, wrapped in a bare
           except, drop that item's tags). Parsing here means one bad row costs one
           warning, not everyone's tags.
        2. **Count reconciliation before COMMIT.** Every distinct tag name found must
           exist as a row afterwards; a mismatch rolls back. Once the column is dropped
           the source data is GONE — there is no second attempt, and the pre-1.0 banner
           means no migration safety net either.
        3. **The FTS table is dropped and recreated, never rebuilt.** Its column list is
           fixed at creation and its ``content=`` target changes here (``items`` → the
           ``items_fts_src`` view). Calling ``'rebuild'`` while it still points at the
           dropped column WIPES THE INDEX AND REPORTS SUCCESS — measured, with
           ``integrity-check`` still reporting ok afterwards.
        """
        rows = self.db.execute("SELECT id, tags FROM items").fetchall()
        # item id -> ordered, de-duplicated tag names. Insertion order is preserved
        # because the AI-vs-user provenance backfill below reads it, and because
        # `_serialize_item` used to return tags in exactly this order.
        per_item: dict[str, list[str]] = {}
        found: set[str] = set()
        salvaged = 0
        for row in rows:
            raw = row["tags"]
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                # Not valid JSON. The column has held such values for as long as it has
                # existed (the reader swallowed the error), so salvage rather than lose:
                # treat a bare/comma-separated string as tag names.
                parsed = [p for p in str(raw).strip("[]").replace('"', "").split(",")]
                salvaged += 1
            if not isinstance(parsed, list):
                parsed = [parsed]
            names: list[str] = []
            for entry in parsed:
                if not isinstance(entry, str):
                    continue
                name = entry.strip()
                # Empty strings are dropped, matching `all_tags`, which has always
                # filtered falsy names out of its results.
                if name and name not in names:
                    names.append(name)
            if names:
                per_item[row["id"]] = names
                found.update(names)

        now = datetime.now().isoformat()
        self.db.execute("PRAGMA foreign_keys=OFF")
        self.db.execute("BEGIN")
        try:
            for name in sorted(found):
                self.db.execute(
                    "INSERT OR IGNORE INTO tags (name, created_at) VALUES (?, ?)",
                    (name, now),
                )
            ids = {
                r["name"]: r["id"] for r in self.db.execute("SELECT id, name FROM tags").fetchall()
            }
            for item_id, names in per_item.items():
                for name in names:
                    self.db.execute(
                        "INSERT OR IGNORE INTO item_tags (item_id, tag_id, source, added_at) "
                        "VALUES (?, ?, ?, ?)",
                        (item_id, ids[name], self._migrated_tag_source(item_id, names), now),
                    )

            # Reconcile BEFORE the irreversible step.
            landed = {
                r["name"]
                for r in self.db.execute(
                    "SELECT DISTINCT t.name FROM tags t JOIN item_tags it ON it.tag_id = t.id"
                ).fetchall()
            }
            if landed != found:
                missing = sorted(found - landed)
                raise RuntimeError(
                    f"tag migration would lose {len(missing)} tag(s): {missing[:10]} — "
                    "rolling back, the JSON column is untouched"
                )

            self.db.execute("ALTER TABLE items DROP COLUMN tags")
            # Recreate the FTS table against the new view (see the docstring — never
            # 'rebuild' here), then repopulate from the view itself.
            self.db.execute("DROP TABLE IF EXISTS items_fts")
            self.db.execute(
                "CREATE VIRTUAL TABLE items_fts USING fts5("
                "title, content, tags, content=items_fts_src, content_rowid=rowid)"
            )
            self.db.execute(
                "INSERT INTO items_fts (rowid, title, content, tags) "
                "SELECT rowid, title, content, tags FROM items_fts_src"
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        finally:
            self.db.execute("PRAGMA foreign_keys=ON")

        if salvaged:
            logger.warning(
                "tag migration: salvaged tags from %d item(s) whose tags column held "
                "invalid JSON",
                salvaged,
            )
        logger.info(
            "tag migration: %d distinct tag(s) across %d item(s) moved to rows",
            len(found),
            len(per_item),
        )

    def _migrated_tag_source(self, item_id: str, names: list[str]) -> str:
        """Provenance for a tag being backfilled: 'ai' only when the item's stored
        insights say the AI produced exactly this tag set.

        Getting this right at migration time is what lets the enrichment pipeline stop
        INFERRING provenance. Ties break toward ``'user'``: mislabelling a user's tag as
        AI-authored makes it eligible to be overwritten on the next enrichment, which is
        silent data loss, while mislabelling an AI tag as the user's merely means it
        stops being auto-refreshed.
        """
        row = self.db.execute("SELECT insights FROM items WHERE id = ?", (item_id,)).fetchone()
        if not row or not row["insights"]:
            return "user"
        try:
            topics = json.loads(row["insights"]).get("topics") or []
        except (TypeError, ValueError):
            return "user"
        topics = [t.strip() for t in topics if isinstance(t, str) and t.strip()]
        # The same comparison the pipeline used to make, applied once here instead of on
        # every enrichment — order-insensitive, because row order is not JSON order.
        return "ai" if topics and sorted(set(topics)) == sorted(set(names)) else "user"

    # WATCHED-SOURCES §3.3 — how many seen-guids one source remembers before FIFO prune.
    # The seen-set is the storm guard, so it MUST persist, but an unbounded set grows
    # without limit on a busy feed. Mirrors web_poll.MAX_SEEN_KEYS's reasoning (newest
    # kept); larger here because a feed legitimately carries more distinct items than a
    # single scraped page. A re-appearing very-old guid may re-fire once — the correct
    # trade against a table that grows forever.
    _MAX_SEEN_PER_SOURCE = 5000

    def _migrate_sources(self) -> None:
        """WATCHED-SOURCES §1.2/§3 — the WatchedSource store, added idempotently.

        Three tables + two item columns, all ``IF NOT EXISTS`` / column-presence guarded
        (knowledge.db has no schema-version counter — this matches `_init_schema`'s and
        `_migrate_tags_to_rows`'s idempotence discipline). ``source_seen`` carries the
        ``UNIQUE(source_id, guid)`` novelty gate; the twin index on ``items(source_id,
        guid)`` makes the same key queryable on the item itself (cross-feed dedupe reads
        it in later atoms) and rejects a second item for one sighting even if a caller
        bypasses the seen-set."""
        item_cols = {r[1] for r in self.db.execute("PRAGMA table_info(items)").fetchall()}
        # A source item's origin identity (§3.3). Nullable so every native/imported row
        # (source_id/guid both NULL) stays valid — the partial UNIQUE index below only
        # binds rows that actually carry both.
        if "source_id" not in item_cols:
            self.db.execute("ALTER TABLE items ADD COLUMN source_id TEXT")
        if "guid" not in item_cols:
            self.db.execute("ALTER TABLE items ADD COLUMN guid TEXT")
        src_cols = {r[1] for r in self.db.execute("PRAGMA table_info(sources)").fetchall()}
        if src_cols and "last_escalations" not in src_cols:
            # Added after `sources` shipped, so it is a guarded ALTER rather than a DDL edit
            # (knowledge.db has no schema-version counter — same idempotence discipline as the
            # item columns above). The CREATE below carries it for a fresh database.
            self.db.execute(
                "ALTER TABLE sources ADD COLUMN last_escalations TEXT NOT NULL DEFAULT '[]'"
            )
        self.db.executescript("""
            -- A WatchedSource: user-library configuration (§1.2), not harness state, so it
            -- lives here in knowledge.db beside the items it produces. `spec`/`budget` are
            -- per-kind JSON; the runtime rollups (last_poll_at/health_status/…) are
            -- engine-written so the UI can show a source's health without re-polling it.
            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider TEXT NOT NULL,
                kind TEXT NOT NULL,
                spec TEXT NOT NULL DEFAULT '{}',
                enrichment TEXT NOT NULL DEFAULT 'full',
                poll_interval_secs INTEGER NOT NULL DEFAULT 3600,
                budget TEXT NOT NULL DEFAULT '{}',
                item_type TEXT NOT NULL DEFAULT 'bookmark',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_by TEXT NOT NULL DEFAULT 'user',
                last_poll_at TEXT,
                next_poll_at TEXT,
                last_new_count INTEGER DEFAULT 0,
                health_status TEXT DEFAULT 'ok',
                last_error_summary TEXT DEFAULT '',
                -- The tiers the last poll had to climb, or was refused (WATCHED-SOURCES
                -- §2.3): a render escalation is the expensive one, and an escalation nobody
                -- can see is indistinguishable from a cheap poll. JSON array of strings.
                last_escalations TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sources_enabled ON sources(enabled);

            -- One opaque provider-defined cursor per source (§3.2): {etag,last_modified},
            -- {since_ts}, {mtime_signatures}, … The engine never interprets it — it hands
            -- the stored blob back to poll() and persists whatever comes out, atomically
            -- with the seen-set delta. One row per source, so id is the PK.
            CREATE TABLE IF NOT EXISTS source_cursors (
                source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
                cursor TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            -- The seen-set / novelty gate (§3.3): the storm guard. The composite PK is the
            -- UNIQUE(source_id, guid) constraint — an INSERT OR IGNORE that changes no row
            -- is a repeat sighting. ON DELETE CASCADE so deleting a source reclaims its set.
            CREATE TABLE IF NOT EXISTS source_seen (
                source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                guid TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                PRIMARY KEY (source_id, guid)
            );

            CREATE INDEX IF NOT EXISTS idx_source_seen_source ON source_seen(source_id);

            -- The same (source_id, guid) key on the item itself. Partial (both non-NULL) so
            -- native items — which leave both NULL — are exempt: SQLite treats NULLs as
            -- distinct, so a plain UNIQUE would still admit unlimited native rows, but the
            -- partial index makes the intent explicit and keeps the index small.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_items_source_guid
                ON items(source_id, guid)
                WHERE source_id IS NOT NULL AND guid IS NOT NULL;

            -- The cross-source merge lookup (§3.3). NOT unique: two different sources
            -- legitimately hold the same canonical URL for a moment (the merge collapses
            -- them), and native/imported bookmarks may share a URL with a source item.
            -- Partial, so the index only carries rows a source wrote.
            CREATE INDEX IF NOT EXISTS idx_items_source_url
                ON items(url)
                WHERE source_id IS NOT NULL;
        """)
        self.db.commit()

    # ── WatchedSource store (WATCHED-SOURCES §1.2) ──────────────────────────────────

    def create_source(
        self,
        *,
        name: str,
        provider: str,
        kind: str,
        spec: dict | None = None,
        enrichment: str = "full",
        poll_interval_secs: int = 3600,
        budget: dict | None = None,
        item_type: str = "bookmark",
        enabled: bool = True,
        created_by: str = "user",
    ) -> str:
        """Persist a WatchedSource row and return its ``src-<8hex>`` id (§1.2)."""
        sid = f"src-{uuid4().hex[:8]}"
        now = datetime.now().isoformat()
        self.db.execute(
            "INSERT INTO sources (id, name, provider, kind, spec, enrichment, "
            "poll_interval_secs, budget, item_type, enabled, created_by, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sid,
                name,
                provider,
                kind,
                json.dumps(spec or {}),
                enrichment,
                int(poll_interval_secs),
                json.dumps(budget or {}),
                item_type,
                1 if enabled else 0,
                created_by,
                now,
                now,
            ),
        )
        self.db.commit()
        return sid

    def _serialize_source(self, row) -> dict:
        d = dict(row)
        for key in ("spec", "budget"):
            val = d.get(key)
            try:
                d[key] = json.loads(val) if val else {}
            except (TypeError, ValueError):
                d[key] = {}
        raw_esc = d.get("last_escalations")
        try:
            parsed = json.loads(raw_esc) if raw_esc else []
        except (TypeError, ValueError):
            parsed = []
        d["last_escalations"] = [str(x) for x in parsed] if isinstance(parsed, list) else []
        d["enabled"] = bool(d.get("enabled"))
        return d

    def get_source(self, source_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return self._serialize_source(row) if row else None

    def list_sources(self, *, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM sources"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY created_at"
        return [self._serialize_source(r) for r in self.db.execute(sql).fetchall()]

    #: The `sources` columns a user may edit after creation, and how each is coerced on the
    #: way in. Deliberately a CLOSED map rather than "whatever keys the caller sent": the
    #: same row carries the engine's own rollups (`health_status`, `last_escalations`,
    #: `last_poll_at`, the cursor's twin), and a generic setter would let an edit path
    #: overwrite a poll's verdict with whatever a client believed it to be.
    #:
    #: `provider`/`kind` are NOT here on purpose. They decide which provider polls the row
    #: and therefore what its `spec` even means, so changing them in place would silently
    #: reinterpret a validated spec against a different validator — that is a new source,
    #: not an edit.
    _EDITABLE_SOURCE_FIELDS: dict[str, Callable[[Any], Any]] = {
        "name": str,
        "enabled": lambda v: 1 if v else 0,
        "enrichment": str,
        "poll_interval_secs": int,
        "item_type": str,
        "spec": lambda v: json.dumps(v or {}),
        "budget": lambda v: json.dumps(v or {}),
    }

    def update_source(self, source_id: str, **fields: Any) -> dict | None:
        """Patch a WatchedSource's user-owned fields; return the updated row (None if gone).

        Partial by construction — an absent key is untouched, so a caller flipping
        ``budget.allow_render`` cannot blank a name it never sent. Raises ``KeyError`` on a
        field outside :data:`_EDITABLE_SOURCE_FIELDS` rather than ignoring it: an edit that
        silently does nothing is the shape of bug where a UI reports success and the row
        never moved.
        """
        unknown = sorted(set(fields) - set(self._EDITABLE_SOURCE_FIELDS))
        if unknown:
            raise KeyError(f"not an editable source field: {', '.join(unknown)}")
        if self.get_source(source_id) is None:
            return None
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            vals = [self._EDITABLE_SOURCE_FIELDS[k](v) for k, v in fields.items()]
            self.db.execute(
                f"UPDATE sources SET {cols}, updated_at = ? WHERE id = ?",
                (*vals, datetime.now().isoformat(), source_id),
            )
            self.db.commit()
        return self.get_source(source_id)

    def get_source_cursor(self, source_id: str) -> str:
        """The opaque cursor last persisted for a source (empty string if never polled)."""
        row = self.db.execute(
            "SELECT cursor FROM source_cursors WHERE source_id = ?", (source_id,)
        ).fetchone()
        return row["cursor"] if row else ""

    def record_poll(
        self,
        source_id: str,
        *,
        cursor: str,
        new_count: int,
        health_status: str = "ok",
        error_summary: str = "",
        next_poll_at: str = "",
        escalations: list[str] | None = None,
    ) -> None:
        """Persist the poll's outcome: the new cursor + the source's runtime rollups.

        Called AFTER the poll's new items (each written by :meth:`create_typed_item` with
        its seen-row, in that item's own committed txn). The seen-set is already durable,
        so a crash the instant before this call re-yields the same items next poll and the
        UNIQUE gate drops them — exactly-once persist on top of at-least-once poll (§3.2).
        The cursor upsert + rollup update share one txn so the engine's view of a source
        never shows a fresh cursor against stale rollups.

        ``escalations`` are the tiers this poll had to climb (§2.3), OVERWRITTEN per poll
        rather than appended: they describe the last poll's cost, and an ever-growing list on
        a row the UI reads would be a log in a rollup column. Recorded on the success path too
        — an escalation that only surfaced on failure would make the expensive-but-working
        case the invisible one."""
        now = datetime.now().isoformat()
        self.db.execute("BEGIN")
        try:
            self.db.execute(
                "INSERT INTO source_cursors (source_id, cursor, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(source_id) DO UPDATE SET cursor = excluded.cursor, "
                "updated_at = excluded.updated_at",
                (source_id, cursor, now),
            )
            self.db.execute(
                "UPDATE sources SET last_poll_at = ?, next_poll_at = ?, last_new_count = ?, "
                "health_status = ?, last_error_summary = ?, last_escalations = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    now,
                    next_poll_at or None,
                    int(new_count),
                    health_status,
                    error_summary,
                    json.dumps([str(e) for e in (escalations or [])]),
                    now,
                    source_id,
                ),
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self._prune_seen(source_id)

    def _prune_seen(self, source_id: str) -> None:
        """FIFO-cap the seen-set at ``_MAX_SEEN_PER_SOURCE`` (§3.3). Keeps the newest by
        first_seen_at; a re-appearing very-old guid may re-fire once, which is the correct
        trade against an unbounded table on a busy feed."""
        self.db.execute(
            "DELETE FROM source_seen WHERE source_id = ? AND guid NOT IN ("
            "SELECT guid FROM source_seen WHERE source_id = ? "
            "ORDER BY first_seen_at DESC LIMIT ?)",
            (source_id, source_id, self._MAX_SEEN_PER_SOURCE),
        )
        self.db.commit()

    def _load_graph(self):
        self.graph.clear()
        for row in self.db.execute("SELECT id, name, entity_type FROM entities"):
            self.graph.add_node(row["id"], name=row["name"], entity_type=row["entity_type"])
        for row in self.db.execute(
            "SELECT id, source_id, target_id, relation_type, weight FROM entity_relations"
        ):
            self.graph.add_edge(
                row["source_id"],
                row["target_id"],
                id=row["id"],
                relation_type=row["relation_type"],
                weight=row["weight"],
            )

    def create_typed_item(
        self,
        *,
        item_type: str,
        title: str,
        content: str = "",
        tags=None,
        url: str = "",
        provider: str = "native",
        summary: str = "",
        source_id: str = "",
        guid: str = "",
        extra: dict | None = None,
    ) -> str | None:
        """Create one logical-document typed item (note/gist/bookmark/…) directly.

        This is the one logical document the typed UI + agents work with: it carries
        the first-class fields (type, url, word_count) and is NOT chunked (chunking is
        an embedding-pipeline detail). ``extra`` may set any other first-class column
        (mime_type, file_path, …).

        WATCHED-SOURCES §3.3 — when ``source_id`` AND ``guid`` are both supplied (a
        :class:`~personalclaw.knowledge.source_engine.SourceEngine` writing a polled
        feed item), the ``source_seen`` novelty gate is folded into the SAME
        transaction as the item insert: a first sighting inserts the seen row + the
        item atomically and returns the id; a repeat sighting (the ``UNIQUE`` index /
        composite PK rejects it) rolls back and returns ``None`` — the exactly-once
        persist that makes an at-least-once poll (a crash between item-persist and
        cursor-persist re-yields items) harmless. Native callers omit both and always
        get an id (contract unchanged)."""
        item_id = str(uuid4())
        now = datetime.now().isoformat()
        # Tags are rows now. Normalized here so the caller's shape (list[str], possibly
        # with blanks/dupes) can't reach storage — the public contract is unchanged.
        tag_names = _clean_tag_names(tags)
        word_count = len((content or "").split())
        extra = extra or {}
        is_source_item = bool(source_id and guid)
        # Canonicalize a bookmark's URL at the storage boundary so dedup is consistent
        # regardless of caller (HTTP handler, agent tool, provider) and tracking-param /
        # trailing-slash variants of the same page collapse to one item. EVERY source item
        # is canonicalized too, whatever its item_type (§3.3): the cross-source merge key
        # is this same canonical form, so normalizing here is what lets
        # `find_item_by_merge_key` be one indexed equality instead of a full scan that
        # re-canonicalizes every candidate row.
        if url and (item_type == "bookmark" or is_source_item):
            url = normalize_url(url)
        self.db.execute("BEGIN")
        try:
            if is_source_item:
                # The novelty gate IS the storm guard (§3.3): the INSERT-or-ignore into the
                # UNIQUE seen-set is what makes a page that changes every render fire at most
                # once. Do it FIRST, inside the item's txn — if the guid was already seen,
                # bail before writing a duplicate item (no dup) and before touching the FTS.
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO source_seen (source_id, guid, first_seen_at) "
                    "VALUES (?, ?, ?)",
                    (source_id, guid, now),
                )
                if cur.rowcount == 0:
                    self.db.execute("ROLLBACK")
                    return None
            try:
                self.db.execute(
                    "INSERT INTO items (id, title, content, item_type, summary, status, url, "
                    "word_count, provider, source_id, guid, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)",
                    (
                        item_id,
                        title,
                        content,
                        item_type,
                        summary,
                        url,
                        word_count,
                        provider,
                        source_id or None,
                        guid or None,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                # The never-pruned item-level UNIQUE(source_id, guid) backstop fired: this
                # source+guid already has an item even though the FIFO-capped seen-set had
                # forgotten it (a very-old guid re-appearing after the ~5000-entry prune).
                # Roll back and dedup rather than crash the poll — the item index is the
                # authoritative persist gate, the seen-set is the storm guard on top.
                self.db.execute("ROLLBACK")
                return None
            self._write_item_tags(item_id, tag_names, source="user", now=now)
            rowid = self.db.execute("SELECT rowid FROM items WHERE id = ?", (item_id,)).fetchone()[
                0
            ]
            self.db.execute(
                "INSERT INTO items_fts (rowid, title, content, tags) VALUES (?, ?, ?, ?)",
                (rowid, title, content, _fts_tags(tag_names)),
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        if extra:
            self.update_item(item_id, **extra)
            self.db.commit()
        # KL-14 — a NEW indexed document, so the derived graph is stale. Marked here rather
        # than on entry because both `return None` paths above rolled back and wrote nothing:
        # a watermark moved for a write that then failed would run the maintenance pass over
        # an unchanged library on every tick, forever.
        from personalclaw.knowledge import maintenance

        maintenance.mark_dirty(reason=f"create {item_type}")
        return item_id

    def find_source_item(self, source_id: str, guid: str) -> dict | None:
        """The item a source already wrote for this ``guid``, or None (§3.3, WS-5).

        The read side of the ``(source_id, guid)`` identity that ``create_typed_item``
        writes: a MUTABLE source (a watched directory) needs to reach the EXISTING row
        for a re-index or an archive, and the partial UNIQUE index makes that lookup
        single-row by construction. Archived rows are included on purpose — a deleted
        file that comes back must revive its original item, not mint a second one.
        """
        if not (source_id and guid):
            return None
        row = self.db.execute(
            "SELECT * FROM items WHERE source_id = ? AND guid = ?", (source_id, guid)
        ).fetchone()
        return self._serialize_item(row) if row else None

    def mark_source_seen(self, source_id: str, guid: str) -> bool:
        """Record that *source_id* has now seen *guid*, writing NO item (§3.3).

        The other half of the cross-source merge: when a second feed carries a story the
        library already holds, no item is written — but that source must still remember the
        sighting, or every subsequent poll would re-offer it and re-run the merge forever
        (the storm the seen-set exists to stop, arriving through the one path that skips
        ``create_typed_item``'s folded-in gate). Returns True when this was a first sighting.
        """
        if not (source_id and guid):
            return False
        cur = self.db.execute(
            "INSERT OR IGNORE INTO source_seen (source_id, guid, first_seen_at) "
            "VALUES (?, ?, ?)",
            (source_id, guid, datetime.now().isoformat()),
        )
        self.db.commit()
        return cur.rowcount > 0

    def find_item_by_merge_key(self, merge_key: str, *, exclude_source_id: str = "") -> dict | None:
        """The existing SOURCE item whose canonical URL is *merge_key* (§3.3 cross-feed dedupe).

        Scoped to rows a source wrote (``source_id IS NOT NULL``) — a hand-saved bookmark
        that happens to share a URL is the user's own item and must not silently acquire
        feed attributions. ``exclude_source_id`` keeps a source from merging against
        itself: within one source, identity is the guid the source itself asserted, and two
        of its rows sharing a URL means the source called them different items.

        Oldest-first, so the FIRST source to carry a story owns the item and every later
        feed becomes an attribution on it. That ordering is what makes the outcome
        independent of which source happened to poll first inside one cycle.
        """
        if not merge_key:
            # An item with no derivable cross-source identity. Never a wildcard match: see
            # ``source_identity.merge_key`` — empty means "keep both", not "match anything".
            return None
        row = self.db.execute(
            "SELECT * FROM items WHERE url = ? AND source_id IS NOT NULL "
            "AND (? = '' OR source_id != ?) ORDER BY created_at, id LIMIT 1",
            (merge_key, exclude_source_id or "", exclude_source_id or ""),
        ).fetchone()
        return self._serialize_item(row) if row else None

    def record_also_seen_in(self, item_id: str, *labels: str) -> bool:
        """Add cross-source attributions to an existing item's metadata (§3.3, SC#3).

        ``file_metadata['also_seen_in']`` is a list of strings, the same shape as the
        provider-facing :attr:`~personalclaw.knowledge_providers.base.SourceItem.also_seen_in`
        field, so an engine-derived attribution and a provider-declared one are one
        vocabulary rather than two shapes a reader has to branch on.

        The write is ADDITIVE and idempotent: a story seen in three feeds names all three,
        and re-merging the same source is a no-op. Replacing the list instead of appending
        is the failure mode SC#3 is written against — an item that names only the feed it
        arrived in FIRST is indistinguishable from one whose second sighting was silently
        dropped, which is precisely the duplicate-vs-merge distinction the criterion tests.
        Returns True when the item's attributions changed.
        """
        item = self.get_item(item_id)
        if not item:
            return False
        wanted = [str(x).strip() for x in labels if str(x).strip()]
        if not wanted:
            return False
        meta = item.get("file_metadata")
        meta = dict(meta) if isinstance(meta, dict) else {}
        current = meta.get("also_seen_in")
        seen = [str(x) for x in current if str(x).strip()] if isinstance(current, list) else []
        added = [lbl for lbl in wanted if lbl not in seen]
        if not added:
            return False
        meta["also_seen_in"] = seen + added
        self.update_item(item_id, file_metadata=meta)
        self.db.commit()
        return True

    def archive_source_item(self, item_id: str, *, deleted_at: str = "") -> bool:
        """Archive a source item whose upstream copy is gone, stamping when (SC#5).

        This is deliberately the ONLY thing the engine can do about an upstream delete,
        and it is an UPDATE — never a DELETE. Once the file is gone from the watched
        directory the library row is the last remaining copy of what the user had, so
        removing it would destroy data on a filesystem event the user may not even have
        meant (a moved folder, an unmounted volume, a synced-away directory). Archived
        items already drop out of retrieval/FTS scope everywhere, so the item stops
        surfacing without being lost, and ``source_deleted_at`` on the item's metadata
        records exactly when the source stopped carrying it.
        """
        item = self.get_item(item_id)
        if not item:
            return False
        meta = item.get("file_metadata")
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta["source_deleted_at"] = deleted_at or datetime.now().isoformat()
        # touch=False: the source vanishing is not the user editing the item, so it must
        # not masquerade as recent user activity in "Last updated" / recency ordering.
        self.update_item(item_id, touch=False, is_archived=1, file_metadata=meta)
        self.db.commit()
        return True

    def forget_source_item(self, source_id: str, guid: str) -> bool:
        """Drop a source item AND its ``source_seen`` row, in one transaction.

        The counterpart to :meth:`archive_source_item`, for the opposite kind of upstream.
        A watched directory is not ours: its file may be back tomorrow, so the library row
        is archived and the sighting remembered. An in-app MIRROR (the ``artifact://``
        source, PRODUCT-EXPERIENCE-PARITY §6) is derived state whose upstream we DO own —
        once the artifact is deleted through the app nothing can revive it, so an archived
        row would be a permanently unrevivable orphan sitting in the store.

        Both writes are required and neither is optional. Deleting only the item would
        leave the ``(source_id, guid)`` seen row, and
        :meth:`create_typed_item`'s novelty gate would then refuse to index an artifact
        re-created under the same slug — forever, silently. Deleting only the seen row
        would leave the mirror. One transaction is what makes "the sighting never happened"
        atomic. Returns True when an item was actually removed.
        """
        if not (source_id and guid):
            return False
        row = self.db.execute(
            "SELECT id FROM items WHERE source_id = ? AND guid = ?", (source_id, guid)
        ).fetchone()
        self.db.execute("BEGIN")
        try:
            if row:
                self._delete_item_cascade(row["id"])
            self.db.execute(
                "DELETE FROM source_seen WHERE source_id = ? AND guid = ?", (source_id, guid)
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        if row:
            self._load_graph()
            # KL-14 — same delete as `delete_item`, reached from a source poll instead of the
            # UI. Guarded by `row` on purpose: a call for a guid this source never wrote
            # removes no item, so there is nothing for a maintenance pass to reconcile.
            # (`archive_source_item`, the other source-side write, needs no call of its own:
            # it goes through `update_item(is_archived=1)`, which is on the allowlist.)
            from personalclaw.knowledge import maintenance

            maintenance.mark_dirty(reason="forget source item")
        return bool(row)

    def get_item(self, item_id):
        row = self.db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return self._serialize_item(row) if row else None

    def find_active_by_url(self, url: str):
        """Return an existing active item whose canonical URL matches, or None. Used to
        dedup bookmarks (re-saving the same link returns the original). The lookup URL is
        normalized the same way create stores it, so trailing-slash / tracking-param
        variants of one page dedup against each other."""
        if not url:
            return None
        canon = normalize_url(url)
        row = self.db.execute(
            "SELECT * FROM items WHERE url = ? AND status = 'active' "
            "ORDER BY created_at LIMIT 1",
            (canon,),
        ).fetchone()
        return self._serialize_item(row) if row else None

    def find_active_by_file_hash(self, content_hash: str):
        """Return an existing active item whose stored file content_hash matches, or None.
        Used to dedup byte-identical re-uploads."""
        if not content_hash:
            return None
        row = self.db.execute(
            "SELECT * FROM items WHERE status = 'active' "
            "AND json_extract(file_metadata, '$.content_hash') = ? "
            "ORDER BY created_at LIMIT 1",
            (content_hash,),
        ).fetchone()
        return self._serialize_item(row) if row else None

    def find_fuzzy_dup_candidates(self, item_id: str, *, limit: int = 25) -> list[dict]:
        """P12 TIER-2 prefilter: active, non-archived items of the SAME type as ``item_id``
        that carry an embedding, EXCLUDING the item itself — the small candidate set the pure
        ``dedup.resolve_duplicate`` then scores by filename+cosine+date-gate. Returns lean
        dicts carrying the fields the resolver reads (id/title/file_path/summary/item_type/
        word_count/processing_status/created_at) PLUS the DECODED embedding vector (the normal
        serializer strips it — the resolver needs the raw floats for cosine). Cheap SQL narrows
        by type so the Python cosine loop stays bounded; ordered newest-first, capped."""
        from personalclaw.knowledge.embedder import bytes_to_floats

        anchor = self.db.execute("SELECT item_type FROM items WHERE id = ?", (item_id,)).fetchone()
        if anchor is None:
            return []
        item_type = anchor["item_type"] if not isinstance(anchor, tuple) else anchor[0]
        rows = self.db.execute(
            "SELECT id, title, file_path, summary, item_type, word_count, "
            "LENGTH(content) AS content_len, "
            "processing_status, created_at, embedding "
            "FROM items WHERE status = 'active' AND COALESCE(is_archived, 0) = 0 "
            "AND item_type = ? AND embedding IS NOT NULL AND id != ? "
            "ORDER BY created_at DESC LIMIT ?",
            (item_type, item_id, max(1, int(limit))),
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            d["embedding"] = bytes_to_floats(d.get("embedding") or b"")
            out.append(d)
        return out

    def find_duplicates(self, item_id: str, *, limit: int = 25) -> list[dict]:
        """Near-duplicates of *item_id*, STRONGEST MATCH FIRST — the surfacing half of T3.2.

        Uses the same `dedup.resolve_duplicate` scorer as the ingest-time pipeline rather than
        inventing a second notion of "duplicate": the resolver already encodes the real rule
        (filename/title similarity AND cosine AND the same series-date token), and a second
        heuristic here would disagree with the ingest-time dedup in ways nobody could explain.

        🔴 IT DOES **NOT** REUSE `find_fuzzy_dup_candidates`, AND THAT IS THE POINT OF THIS
        METHOD'S SHAPE. That prefilter is `ORDER BY created_at DESC LIMIT ?` — a deliberate,
        correct bound for the INGEST path, where the anchor is the row being ingested right now
        and a bounded Python cosine loop matters. Read on demand for a UI, the same cap silently
        answers a DIFFERENT question: it spends the caller's `limit` on *how many items are even
        looked at*, newest first, so a duplicate that is not among the N newest same-type embedded
        items is never scored at all. Measured on a 32-item library: an exact-title pair scoring
        `is_dup=True`, `filename_sim=1.0`, `cosine=0.9950` returned `[]` at the shipped default
        `limit=25` — and the route caps `limit` at 50, so past ~50 items the panel whose entire job
        is to say "a second copy exists" said "no duplicates" permanently. That is the SECOND
        silent-empty path on this one surface (the first was the `is_duplicate` typo below), and
        both fail the same way: the honest answer and the broken answer are the same empty list.

        So the scan is now bounded by the RULE instead of by recency, in two phases:

        1. The filename leg over the WHOLE eligible corpus — cheap title/path columns only, no
           `LIMIT`, no blob reads. `dedup.filename_similarity` at `dedup.FILENAME_SIM_MIN`, the
           resolver's own metric at the resolver's own number.
        2. Embeddings decoded and cosine scored ONLY for what survives phase 1. This is strictly
           less blob work than before (which decoded up to 25 unconditionally) while being
           unbounded in reach: to be missed now you would need the anchor's own title to be
           <85% similar to the duplicate's, which is exactly the case the rule calls "not a dup".

        `limit` therefore caps RESULTS — what the route and the UI have always meant by it.

        Returns lean dicts (`id`, `title`, `item_type`, `created_at`, `word_count`, `reason`,
        `similarity`, `title_similarity`) — never the embedding, which is megabytes of floats no
        caller needs. Items without an embedding are simply absent: an un-embedded item cannot be
        scored, and guessing from titles alone is how a merge UI proposes destroying two unrelated
        documents.
        """
        from personalclaw.knowledge.dedup import (
            FILENAME_SIM_MIN,
            filename_similarity,
            resolve_duplicate,
        )
        from personalclaw.knowledge.embedder import bytes_to_floats

        _COLS = (
            "id, title, file_path, summary, item_type, word_count, "
            "LENGTH(content) AS content_len, processing_status, created_at, embedding"
        )
        anchor_row = self.db.execute(
            f"SELECT {_COLS} FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if anchor_row is None:
            return []
        anchor = dict(anchor_row)
        anchor["embedding"] = bytes_to_floats(anchor.get("embedding") or b"")
        if not anchor["embedding"]:
            return []
        anchor_name = anchor.get("title") or anchor.get("file_path") or ""

        # Phase 1 — the free leg, over everything. Same eligibility as the ingest prefilter
        # (active, unarchived, same type, embedded, not the anchor) so the two paths agree on
        # WHICH items are comparable; they differ only in how many they are willing to score.
        gated = [
            r["id"]
            for r in self.db.execute(
                "SELECT id, title, file_path FROM items "
                "WHERE status = 'active' AND COALESCE(is_archived, 0) = 0 "
                "AND item_type = ? AND embedding IS NOT NULL AND id != ?",
                (anchor["item_type"], item_id),
            ).fetchall()
            if filename_similarity(r["title"] or r["file_path"] or "", anchor_name)
            >= FILENAME_SIM_MIN
        ]
        if not gated:
            return []

        # Phase 2 — the paid leg, only for those. Chunked so a library with hundreds of
        # near-identically-titled items cannot trip sqlite's variadic-parameter ceiling.
        scored: list[tuple[float, float, dict]] = []
        for start in range(0, len(gated), 400):
            batch = gated[start : start + 400]
            placeholders = ",".join("?" * len(batch))
            for row in self.db.execute(
                f"SELECT {_COLS} FROM items WHERE id IN ({placeholders})", batch
            ).fetchall():
                cand = dict(row)
                cand["embedding"] = bytes_to_floats(cand.get("embedding") or b"")
                try:
                    verdict = resolve_duplicate(cand, anchor)
                except Exception:
                    logger.debug("dup scoring failed for %s", cand.get("id"), exc_info=True)
                    continue
                # 🔴 `verdict.is_dup`, read DIRECTLY. This was
                # `getattr(verdict, "is_duplicate", False)` — a field `DupVerdict` does not have,
                # so the default won on every comparison and `find_duplicates` returned an empty
                # list for EVERY input, however identical the two items were. Found by driving it:
                # a pair with `filename_sim=1.0` and `cosine=0.9949` scored `is_dup=True` and
                # still surfaced nothing. The three tests that existed were all negative or vacuous
                # (no-embedding, unknown-item, and a never-return-the-embedding loop that iterated
                # ZERO rows), so the inert half read as covered. The other consumer of this verdict
                # (`pipeline/runner.py`) had it right all along.
                #
                # The `getattr` indirection is what made a typo silent, so it is gone rather than
                # spelled correctly: an attribute access on a dataclass RAISES when the name is
                # wrong, which is the behaviour a scorer's verdict field deserves.
                if not verdict.is_dup:
                    continue
                scored.append(
                    (
                        verdict.cosine,
                        verdict.filename_sim,
                        {
                            "id": cand["id"],
                            "title": cand.get("title") or "",
                            "item_type": cand.get("item_type") or "",
                            "created_at": cand.get("created_at") or "",
                            "word_count": cand.get("word_count") or 0,
                            # 🪤 THE REASON IS PER-CANDIDATE, NOT THE RULE'S NAME. It used to be
                            # `verdict.reason`, which on the positive branch is the one constant
                            # string "fuzzy dup (filename+cosine+date-gate)" for every row in
                            # existence — so the UI's stated purpose for showing it ("the scorer's
                            # own account of the match … so a destructive merge is reviewable")
                            # was carried by text that reviews nothing and is identical whether
                            # the match scored 0.90 or 1.00. The two numbers the verdict already
                            # measured were dropped on the floor here. They now travel, both as
                            # this sentence and as fields, and they are what the list is ordered
                            # by — so "strongest first" is a claim the payload can be checked
                            # against instead of a docstring's word.
                            "reason": _dup_reason(verdict.filename_sim, verdict.cosine),
                            "similarity": round(verdict.cosine, 4),
                            "title_similarity": round(verdict.filename_sim, 4),
                        },
                    )
                )
        # Strongest first: cosine leads (the semantic leg is the one that separates a re-download
        # from a rewrite), filename similarity breaks ties, then id for a stable total order.
        scored.sort(key=lambda t: (-t[0], -t[1], t[2]["id"]))
        return [d for _, _, d in scored[: max(1, int(limit))]]

    def merge_items(self, keep_id: str, merge_id: str) -> dict:
        """Fold *merge_id* into *keep_id*, then delete it. Returns what moved.

        The survivor inherits **both** items' collection memberships, tags and entity
        mentions — that is the whole point: a merge must not quietly lose the curation a user
        did on the copy that happens to lose. Mirrors `merge_entities`' shape (delete the rows
        that would violate the composite PK, then redirect the rest) because sqlite has no
        `INSERT OR IGNORE … SELECT` that also updates.

        Refuses to merge an item into itself — a self-merge would run the cascade delete on
        the survivor and destroy the very item it was asked to keep.
        """
        if not keep_id or not merge_id or keep_id == merge_id:
            raise ValueError("merge_items needs two distinct item ids")
        for iid in (keep_id, merge_id):
            if self.db.execute("SELECT 1 FROM items WHERE id = ?", (iid,)).fetchone() is None:
                raise ValueError(f"no such item {iid!r}")

        moved = {"collections": 0, "tags": 0, "mentions": 0, "annotations": 0}
        self.db.execute("BEGIN")
        try:
            # Collections: drop the pairs the survivor already has, then redirect the rest.
            self.db.execute(
                "DELETE FROM collection_items WHERE item_id = ? AND collection_id IN "
                "(SELECT collection_id FROM collection_items WHERE item_id = ?)",
                (merge_id, keep_id),
            )
            cur = self.db.execute(
                "UPDATE collection_items SET item_id = ? WHERE item_id = ?", (keep_id, merge_id)
            )
            moved["collections"] = cur.rowcount or 0

            self.db.execute(
                "DELETE FROM item_tags WHERE item_id = ? AND tag_id IN "
                "(SELECT tag_id FROM item_tags WHERE item_id = ?)",
                (merge_id, keep_id),
            )
            cur = self.db.execute(
                "UPDATE item_tags SET item_id = ? WHERE item_id = ?", (keep_id, merge_id)
            )
            moved["tags"] = cur.rowcount or 0

            self.db.execute(
                "DELETE FROM mentions WHERE item_id = ? AND entity_id IN "
                "(SELECT entity_id FROM mentions WHERE item_id = ?)",
                (merge_id, keep_id),
            )
            cur = self.db.execute(
                "UPDATE mentions SET item_id = ? WHERE item_id = ?", (keep_id, merge_id)
            )
            moved["mentions"] = cur.rowcount or 0

            # Reading highlights follow the same rule as the rest of the curation: a merge
            # must not silently lose passages the user marked on the copy that loses. No
            # de-dup pass is needed — `annotations` is surrogate-keyed, so there is no
            # composite PK for a redirect to violate. The anchor may no longer resolve
            # against the survivor's body; the row still lists, which is why anchoring
            # failure is designed to degrade to "listed but not marked".
            cur = self.db.execute(
                "UPDATE annotations SET item_id = ? WHERE item_id = ?", (keep_id, merge_id)
            )
            moved["annotations"] = cur.rowcount or 0

            # Relations discovered FROM the merged item now belong to the survivor, so the
            # graph edge keeps a live provenance link instead of dangling.
            self.db.execute(
                "UPDATE entity_relations SET source_item_id = ? WHERE source_item_id = ?",
                (keep_id, merge_id),
            )

            # The survivor keeps the STRONGER curation signal from either copy: a merge should
            # never demote something the user had favorited or already read.
            keep_row = self.db.execute(
                "SELECT read_state, favorited FROM items WHERE id = ?", (keep_id,)
            ).fetchone()
            merge_row = self.db.execute(
                "SELECT read_state, favorited FROM items WHERE id = ?", (merge_id,)
            ).fetchone()
            rank = {"unread": 0, "reading": 1, "read": 2}
            best_state = max(
                (keep_row["read_state"] or "unread", merge_row["read_state"] or "unread"),
                key=lambda s: rank.get(s, 0),
            )
            self.db.execute(
                "UPDATE items SET read_state = ?, favorited = ? WHERE id = ?",
                (
                    best_state,
                    1 if (keep_row["favorited"] or merge_row["favorited"]) else 0,
                    keep_id,
                ),
            )

            # The cascade owns the FTS 'delete' (it must carry exactly the indexed values,
            # read BEFORE item_tags rows go away) — reusing it is why this merge can't rot
            # the search index.
            self._delete_item_cascade(merge_id)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self._load_graph()
        # KL-14 — a merge is a delete plus a re-point: one item left the library and another
        # one's mentions/tags/chunks changed underneath it.
        from personalclaw.knowledge import maintenance

        maintenance.mark_dirty(reason="merge items")
        logger.info("merged knowledge item %s into %s: %s", merge_id, keep_id, moved)
        return moved

    # ── Extracted-content pool (node-graph engine, #30) ──

    def add_extracted_content(
        self,
        item_id: str,
        node_type: str,
        *,
        backend: str = "",
        text: str = "",
        metadata: dict | None = None,
    ) -> str:
        """Append one node's output to an item's extracted-content pool. Returns its id."""
        ec_id = uuid4().hex
        now = datetime.now().isoformat()
        self.db.execute(
            "INSERT INTO extracted_contents (id, item_id, node_type, backend, text, metadata, created_at) "  # noqa: E501
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ec_id, item_id, node_type, backend, text or "", json.dumps(metadata or {}), now),
        )
        self.db.commit()
        return ec_id

    def get_extracted_contents(self, item_id: str) -> list[dict]:
        """All pooled node outputs for an item (oldest first), metadata parsed."""
        rows = self.db.execute(
            "SELECT * FROM extracted_contents WHERE item_id = ? ORDER BY created_at, rowid",
            (item_id,),
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            try:
                d["metadata"] = json.loads(d.get("metadata") or "{}")
            except (json.JSONDecodeError, ValueError):
                d["metadata"] = {}
            out.append(d)
        return out

    def clear_extracted_contents(self, item_id: str) -> None:
        """Drop an item's pool (e.g. before a re-ingest)."""
        self.db.execute("DELETE FROM extracted_contents WHERE item_id = ?", (item_id,))
        self.db.commit()

    # -- Chunk index (KL-9): additive per-item slices with their own embeddings --------

    def replace_chunks(self, item_id: str, chunks: list) -> int:
        """Replace an item's chunk rows with *chunks* (``knowledge.chunking.Chunk``),
        embedding each with *embedder* handled by the caller.

        The item's OWN whole-item embedding is untouched — chunks are additive. Delete-
        then-insert (not upsert) keeps the row set exactly in step with a re-chunk, so a
        shorter document after an edit never leaves stale tail chunks behind. ``embedding``
        is written pre-serialized on the Chunk (``.embedding`` bytes) or NULL. Caller owns
        no commit — this commits its own single statement batch. Returns rows written.
        """
        # Drop the OLD chunk ids from the ANN index while they are still readable: a re-chunk
        # mints fresh uuids, so deleting only the new ids would leave every previous
        # generation's vectors behind as orphan candidates.
        self.vec_index.drop_item(item_id)
        self.db.execute("DELETE FROM chunks WHERE item_id = ?", (item_id,))
        rows = [
            (
                uuid4().hex,
                item_id,
                c.chunk_index,
                c.text,
                c.embedding,
                c.section,
                c.line_start,
                c.line_end,
            )
            for c in chunks
        ]
        if rows:
            self.db.executemany(
                "INSERT INTO chunks "
                "(id, item_id, chunk_index, text, embedding, section, line_start, line_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        # Write through to the ANN index on the same connection, immediately after the rows it
        # indexes. A failure here is swallowed by the index (an index write must never fail a
        # chunk write) and repaired by the next process's reconciliation.
        self.vec_index.sync_item(item_id, [(r[0], r[4]) for r in rows])
        self.db.commit()
        return len(rows)

    def get_chunks(self, item_id: str, *, with_embedding: bool = False) -> list[dict]:
        """An item's chunks in order. The raw embedding BLOB is an internal detail, so it
        is decoded to a float list only when *with_embedding* is set (the retrieval path);
        otherwise a lightweight ``has_embedding`` flag is returned instead."""
        cols = "id, item_id, chunk_index, text, section, line_start, line_end, embedding"
        rows = self.db.execute(
            f"SELECT {cols} FROM chunks WHERE item_id = ? ORDER BY chunk_index",
            (item_id,),
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            raw = d.pop("embedding", None)
            if with_embedding:
                from personalclaw.knowledge.embedder import bytes_to_floats

                d["embedding"] = bytes_to_floats(raw) if raw else []
            else:
                d["has_embedding"] = bool(raw)
            out.append(d)
        return out

    def clear_chunks(self, item_id: str) -> None:
        """Drop an item's chunk rows (e.g. before a re-ingest)."""
        self.vec_index.drop_item(item_id)  # before the ids go away
        self.db.execute("DELETE FROM chunks WHERE item_id = ?", (item_id,))
        self.db.commit()

    # -- Per-marker citations (WF2KNO-11) ----------------------------------------

    def set_item_citations(self, item_id: str, citations: Sequence[Any]) -> int:
        """REPLACE the citing item's whole citation set, in one transaction.

        Replace, not append. A synthesized item is re-synthesized (retry, refreshed sources,
        a template change) and the new prose numbers its sources afresh: appending would leave
        the previous generation's markers behind, so ``[2]`` would resolve to two different
        sources and the older, wrong one would read as equally attributed. The delete and the
        inserts share a transaction so a failure mid-write cannot leave an item with NO
        attribution after it had some.

        Accepts :class:`~personalclaw.knowledge.citations.Citation` objects or plain dicts --
        the boundary is dicts on purpose, so this schema and the marker-parsing module stay
        mutually unaware. Note the field flip: a ``Citation``'s ``item_id`` is the SOURCE
        being cited, while *item_id* here is the item DOING the citing; a dict may name the
        source either way.

        Duplicate markers in the input collapse (last wins) rather than raising on the primary
        key, because the caller's list is derived from prose and a model can restate a marker.
        """
        rows: dict[int, tuple[str, int, str, int, str]] = {}
        for citation in citations:
            if isinstance(citation, dict):
                marker = int(citation.get("marker", 0) or 0)
                source_id = citation.get("source_item_id") or citation.get("item_id") or ""
                raw_chunk = citation.get("chunk_index", -1)
                excerpt = citation.get("excerpt", "") or ""
            else:
                marker = int(getattr(citation, "marker", 0) or 0)
                source_id = getattr(citation, "item_id", "") or ""
                raw_chunk = getattr(citation, "chunk_index", -1)
                excerpt = getattr(citation, "excerpt", "") or ""
            # Chunk 0 is a real chunk and falsy, so this is an explicit None check rather
            # than `raw_chunk or -1`, which would relabel every first chunk "whole item".
            chunk_index = -1 if raw_chunk is None else int(raw_chunk)
            rows[marker] = (item_id, marker, str(source_id), chunk_index, str(excerpt))

        self.db.execute("BEGIN")
        try:
            self.db.execute("DELETE FROM item_citations WHERE item_id = ?", (item_id,))
            if rows:
                self.db.executemany(
                    "INSERT INTO item_citations "
                    "(item_id, marker, source_item_id, chunk_index, excerpt) "
                    "VALUES (?, ?, ?, ?, ?)",
                    list(rows.values()),
                )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return len(rows)

    def item_citations(self, item_id: str) -> list[dict]:
        """A citing item's attributions, ascending by marker.

        Marker order, not insertion order: the number is what the reader sees in the prose, so
        a list that does not ascend by it forces the caller to re-sort to render anything.
        """
        rows = self.db.execute(
            "SELECT marker, source_item_id, chunk_index, excerpt FROM item_citations "
            "WHERE item_id = ? ORDER BY marker",
            (item_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # -- Intent outcomes (Tier-3, stored by value with a soft back-ref) -----------

    def record_intent_outcome(
        self,
        intent_id: str,
        *,
        intent_name: str = "",
        item_id: str | None = None,
        item_title: str = "",
        takeaway: str = "",
        fields: list | None = None,
    ) -> str:
        """Persist one intent match BY VALUE. ``item_id`` is a soft back-ref only.

        Replaces any prior outcome for the same (intent_id, item_id) pair so a
        re-run doesn't duplicate. Returns the outcome id.
        """
        if item_id is not None:
            self.db.execute(
                "DELETE FROM intent_outcomes WHERE intent_id = ? AND item_id = ?",
                (intent_id, item_id),
            )
        oid = uuid4().hex
        now = datetime.now().isoformat()
        self.db.execute(
            "INSERT INTO intent_outcomes "
            "(id, intent_id, intent_name, item_id, item_title, takeaway, fields, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                oid,
                intent_id,
                intent_name,
                item_id,
                item_title,
                takeaway,
                json.dumps(fields or []),
                now,
            ),
        )
        self.db.commit()
        return oid

    @staticmethod
    def _serialize_outcome(row) -> dict:
        d = dict(row)
        try:
            d["fields"] = json.loads(d.get("fields") or "[]")
        except (json.JSONDecodeError, ValueError):
            d["fields"] = []
        return d

    def outcomes_for_intent(self, intent_id: str) -> list[dict]:
        """All recorded outcomes for an intent (newest first), fields parsed."""
        rows = self.db.execute(
            "SELECT * FROM intent_outcomes WHERE intent_id = ? ORDER BY created_at DESC, rowid DESC",  # noqa: E501
            (intent_id,),
        ).fetchall()
        return [self._serialize_outcome(r) for r in rows]

    def outcomes_for_item(self, item_id: str) -> list[dict]:
        """All intent outcomes that name this item as their source (newest first)."""
        rows = self.db.execute(
            "SELECT * FROM intent_outcomes WHERE item_id = ? ORDER BY created_at DESC, rowid DESC",
            (item_id,),
        ).fetchall()
        return [self._serialize_outcome(r) for r in rows]

    def intent_outcome_counts(self) -> dict[str, int]:
        """Map of intent_id → number of recorded outcomes (for list badges)."""
        rows = self.db.execute(
            "SELECT intent_id, COUNT(*) AS n FROM intent_outcomes GROUP BY intent_id"
        ).fetchall()
        return {r["intent_id"]: r["n"] for r in rows}

    def delete_intent_outcomes(self, intent_id: str) -> int:
        """Drop all outcomes for an intent (when the intent itself is deleted)."""
        cur = self.db.execute("DELETE FROM intent_outcomes WHERE intent_id = ?", (intent_id,))
        self.db.commit()
        return cur.rowcount

    def clear_item_intent_outcomes(self, item_id: str) -> int:
        """Drop outcomes still sourced from this item (before a re-ingest re-records
        the current matches). Only outcomes whose back-ref is THIS item are removed —
        by-value outcomes orphaned by a deleted item (item_id NULL) are never touched.
        """
        cur = self.db.execute("DELETE FROM intent_outcomes WHERE item_id = ?", (item_id,))
        self.db.commit()
        return cur.rowcount

    def _serialize_item(self, row, *, tags: list[str] | None = None) -> dict:
        """Storage row → the typed-item API shape.

        Tags come from `item_tags` now, so this is an instance method rather than a
        staticmethod. Pass ``tags`` when the caller already batched them (list paths do,
        via :meth:`_tags_for_items`) — otherwise it issues one lookup for this item.
        Either way the emitted shape is unchanged: a plain ``list[str]``, which is what
        every consumer, every tool schema and the whole frontend already expect.
        """
        d = dict(row)
        raw = d.pop("embedding", None)
        # The raw 384-float vector is an embedding-pipeline detail no API consumer
        # reads — shipping it would bloat every list/detail response (~40% of payload).
        # Responses carry only a `has_embedding` flag; the vector never leaves the DB.
        d["has_embedding"] = bool(raw)
        # Typed-item API shape: expose `type` (alias of the item_type storage
        # column), file_metadata/insights as parsed JSON, booleans as bool.
        d["type"] = d.get("item_type", "")
        for key in ("file_metadata", "insights"):
            val = d.get(key)
            if isinstance(val, str):
                try:
                    d[key] = json.loads(val) if val else {}
                except (json.JSONDecodeError, ValueError):
                    d[key] = {}
            elif val is None:
                d[key] = {}
        item_id = d.get("id") or ""
        d["tags"] = tags if tags is not None else self._tags_for_item(item_id)
        for key in ("is_pinned", "is_archived", "favorited"):
            d[key] = bool(d.get(key))
        # An item written before the curation columns landed reads NULL; the API
        # contract is the three-value enum, so normalize rather than leaking None.
        d["read_state"] = d.get("read_state") or "unread"
        d.setdefault("provider", "native")
        return d

    def _serialize_items(self, rows) -> list[dict]:
        """Serialize many rows with ONE tag query instead of one per row.

        The list surfaces render dozens of items at a time; a per-row lookup would make
        tag normalization an N+1 regression on the busiest read path in the library.
        """
        rows = list(rows)
        by_item = self._tags_for_items([r["id"] for r in rows if "id" in r.keys()])
        return [self._serialize_item(r, tags=by_item.get(r["id"], [])) for r in rows]

    def tags_are_all_ai_authored(self, item_id: str) -> bool:
        """True when the item has no tags, or every tag it has was written by a previous
        enrichment — i.e. when the AI may safely refresh them.

        This replaces the old inference in `pipeline/runner.py` (an ordered-list equality
        against the previous run's `insights.topics`). Recorded provenance is both
        order-independent and honest: a single user-added tag makes the whole set
        off-limits, which is the conservative direction — failing to refresh costs a
        stale tag, whereas overwriting costs the user's own work.
        """
        rows = self.db.execute(
            "SELECT source FROM item_tags WHERE item_id = ?", (item_id,)
        ).fetchall()
        return all((r["source"] or "user") == "ai" for r in rows)

    def _tags_for_item(self, item_id: str) -> list[str]:
        if not item_id:
            return []
        return [
            r["name"]
            for r in self.db.execute(
                "SELECT t.name FROM item_tags it JOIN tags t ON t.id = it.tag_id "
                "WHERE it.item_id = ? ORDER BY t.name",
                (item_id,),
            ).fetchall()
        ]

    def _tags_for_items(self, item_ids: list[str]) -> dict[str, list[str]]:
        """``{item_id: [tag names]}`` for many items in one query."""
        if not item_ids:
            return {}
        out: dict[str, list[str]] = {}
        # Chunked to stay under SQLite's variable limit (999 by default) on a big list.
        for start in range(0, len(item_ids), 500):
            chunk = item_ids[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            for row in self.db.execute(
                f"SELECT it.item_id, t.name FROM item_tags it "  # noqa: S608
                f"JOIN tags t ON t.id = it.tag_id "
                f"WHERE it.item_id IN ({placeholders}) ORDER BY t.name",
                chunk,
            ).fetchall():
                out.setdefault(row["item_id"], []).append(row["name"])
        return out

    def _write_item_tags(
        self, item_id: str, names: list[str], *, source: str = "user", now: str = ""
    ) -> None:
        """REPLACE an item's tags with *names*. Caller owns the transaction.

        Replace, not merge — `update_item(tags=[...])` has always been a replace, and a
        test pins it. Tag rows themselves are never deleted here: a tag surviving with no
        members is what makes the hierarchy stable (deleting the last tagged item would
        otherwise silently destroy a branch of the user's taxonomy).
        """
        now = now or datetime.now().isoformat()
        self.db.execute("DELETE FROM item_tags WHERE item_id = ?", (item_id,))
        for name in names:
            self.db.execute(
                "INSERT OR IGNORE INTO tags (name, created_at) VALUES (?, ?)", (name, now)
            )
            row = self.db.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
            self.db.execute(
                "INSERT OR IGNORE INTO item_tags (item_id, tag_id, source, added_at) "
                "VALUES (?, ?, ?, ?)",
                (item_id, row["id"], source, now),
            )

    _ITEM_COLUMNS = {
        "title",
        "content",
        "item_type",
        "summary",
        "embedding",
        "status",
        "updated_at",
        # typed-item fields (P6b)
        "gist_language",
        "url",
        "url_title",
        "url_description",
        "mime_type",
        "file_size",
        "thumbnail_path",
        "file_path",
        "file_metadata",
        "word_count",
        "is_pinned",
        "is_archived",
        "insights",
        "ai_title",
        "provider",
        # ingestion node-graph lifecycle (#30)
        "processing_status",
        "processing_error",
        # library curation (KNOWLEDGE-LIBRARY S1)
        "read_state",
        "favorited",
    }

    #: KL-14 — the fields whose change actually invalidates the derived index, so an
    #: `update_item` that touches one of them moves the graph-maintenance watermark and one
    #: that does not leaves the index clean.
    #:
    #: This is an ALLOWLIST rather than "everything except curation", and the reason is the
    #: DENY side, not the allow side: `embedding`, `insights`, `ai_title`,
    #: `processing_status` and `processing_error` are what the maintenance passes THEMSELVES
    #: write. Marking dirty on those would make every pass re-dirty the index it just
    #: cleaned, so `clear_up_to(snapshot)` would find `dirty_ts` past its snapshot every
    #: single time and the watermark could never go clean — a maintenance loop that runs
    #: forever at full cost and reports success. An allowlist cannot acquire that bug by a
    #: later column being added; a denylist acquires it by default.
    #:
    #: The rest of the deny side is derived or presentational bookkeeping: `updated_at` and
    #: `word_count` are computed FROM a change that is itself on this list, and
    #: `read_state`/`favorited`/`is_pinned` plus the file/url metadata columns change nothing
    #: any pass reads. `status`/`is_archived` ARE here because consolidation's candidate set
    #: is scoped to active items, so archiving one changes that set.
    _INDEX_AFFECTING_FIELDS = {
        "title",
        "content",
        "summary",
        "tags",
        "url",
        "item_type",
        "status",
        "is_archived",
    }

    def update_item(self, item_id, *, touch: bool = True, **fields):
        if not fields:
            return
        # `updated_at` tracks USER activity, so it powers an honest "Last updated" and
        # recency tie-break. Background enrichment writes (status transitions, insights,
        # tags, embedding) pass touch=False so machine processing doesn't masquerade as
        # the user having just edited the item.
        if touch:
            fields["updated_at"] = datetime.now().isoformat()
        # Recompute word_count whenever content changes (file uploads backfill content
        # after create; edits change it) so it never goes stale — unless the caller set
        # it explicitly.
        if "content" in fields and "word_count" not in fields:
            fields["word_count"] = len((fields.get("content") or "").split())
        # `tags` is no longer an items column — pull it out and write rows instead. The
        # public signature is unchanged: callers still pass `tags=[...]`.
        tags_update = _clean_tag_names(fields["tags"]) if "tags" in fields else None
        tag_source = str(fields.pop("tag_source", "") or "user")
        safe = {k: v for k, v in fields.items() if k in self._ITEM_COLUMNS}
        if not safe and tags_update is None:
            return
        # KL-14 — decided from what will ACTUALLY be written, and only acted on after the
        # commit below succeeds. `tags` is counted even though it is not a column, for the
        # same reason `fts_fields` counts it: the indexed value derives from the tag rows.
        # Both early returns above are deliberately upstream of this — a no-op PATCH must
        # leave the index clean, or the watermark says "there is graph work to do" for a call
        # that changed nothing and the pass runs over the whole library for nothing.
        index_changes = self._INDEX_AFFECTING_FIELDS & (
            set(safe) | ({"tags"} if tags_update is not None else set())
        )
        # Read old FTS values BEFORE the update. `tags` counts as an FTS field even
        # though it isn't a column, because the indexed value derives from the tag rows.
        fts_fields = {"title", "content"} & set(fields)
        if tags_update is not None:
            fts_fields.add("tags")
        old_row = None
        old_fts_tags = ""
        if fts_fields:
            old_row = self.db.execute(
                "SELECT rowid, title, content FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            # The delete-side value MUST be exactly what was indexed, or FTS desyncs
            # silently: a mismatched 'delete' corrupts the posting list without raising,
            # and integrity-check does not detect it afterwards.
            old_fts_tags = _fts_tags(self._tags_for_item(item_id))
        cols = ", ".join(f"{k} = ?" for k in safe)
        vals = [json.dumps(v) if isinstance(v, (list, dict)) else v for v in safe.values()]
        self.db.execute("BEGIN")
        try:
            if safe:
                self.db.execute(
                    f"UPDATE items SET {cols} WHERE id = ?",  # noqa: S608
                    (*vals, item_id),
                )
            if tags_update is not None:
                self._write_item_tags(item_id, tags_update, source=tag_source)
            # Sync FTS: delete with OLD values, insert with NEW values
            if old_row:
                self.db.execute(
                    "INSERT INTO items_fts (items_fts, rowid, title, content, tags) VALUES ('delete', ?, ?, ?, ?)",  # noqa: E501
                    (old_row["rowid"], old_row["title"], old_row["content"], old_fts_tags),
                )
                new_row = self.db.execute(
                    "SELECT title, content FROM items WHERE id = ?", (item_id,)
                ).fetchone()
                self.db.execute(
                    "INSERT INTO items_fts (rowid, title, content, tags) VALUES (?, ?, ?, ?)",
                    (
                        old_row["rowid"],
                        new_row["title"],
                        new_row["content"],
                        _fts_tags(self._tags_for_item(item_id)),
                    ),
                )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        if index_changes:
            from personalclaw.knowledge import maintenance

            maintenance.mark_dirty(reason="update " + ",".join(sorted(index_changes)))

    def _delete_item_cascade(self, item_id):
        """Delete item and its dependents without commit/graph reload (for batch use)."""
        row = self.db.execute(
            "SELECT rowid, title, content FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if row:
            # Same rule as the update path: the 'delete' side must carry exactly the
            # indexed value, read BEFORE item_tags rows go away.
            self.db.execute(
                "INSERT INTO items_fts (items_fts, rowid, title, content, tags) VALUES ('delete', ?, ?, ?, ?)",  # noqa: E501
                (
                    row["rowid"],
                    row["title"],
                    row["content"],
                    _fts_tags(self._tags_for_item(item_id)),
                ),
            )
        self.db.execute("DELETE FROM item_tags WHERE item_id = ?", (item_id,))
        self.db.execute("DELETE FROM mentions WHERE item_id = ?", (item_id,))
        self.db.execute("DELETE FROM entity_relations WHERE source_item_id = ?", (item_id,))
        self.db.execute("DELETE FROM extracted_contents WHERE item_id = ?", (item_id,))
        self.vec_index.drop_item(item_id)  # before the chunk ids go away
        self.db.execute("DELETE FROM chunks WHERE item_id = ?", (item_id,))
        # Intent outcomes are kept BY VALUE — only the soft back-ref is severed, so the
        # gathered insight survives the item's deletion.
        self.db.execute("UPDATE intent_outcomes SET item_id = NULL WHERE item_id = ?", (item_id,))
        self.db.execute("DELETE FROM items WHERE id = ?", (item_id,))

    def delete_item(self, item_id):
        self.db.execute("BEGIN")
        try:
            self._delete_item_cascade(item_id)
            # Remove orphan entities (no mentions and no relations)
            self.db.execute("""
                DELETE FROM entities WHERE id NOT IN (SELECT entity_id FROM mentions)
                AND id NOT IN (SELECT source_id FROM entity_relations)
                AND id NOT IN (SELECT target_id FROM entity_relations)
            """)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self._load_graph()
        # KL-14 — a delete is index-affecting in both directions: the item leaves the
        # consolidation candidate set, and the orphan-entity sweep above just changed the
        # entity graph the other passes read.
        from personalclaw.knowledge import maintenance

        maintenance.mark_dirty(reason="delete item")

    def clear_item_entities(self, item_id):
        """Drop this item's mention/relation rows + any now-orphan entities, WITHOUT
        deleting the item. The node-graph's entity stage calls this before re-writing
        so a re-ingest doesn't duplicate. Caller owns the commit (no BEGIN here)."""
        self.db.execute("DELETE FROM mentions WHERE item_id = ?", (item_id,))
        self.db.execute("DELETE FROM entity_relations WHERE source_item_id = ?", (item_id,))
        self.db.execute("""
            DELETE FROM entities WHERE id NOT IN (SELECT entity_id FROM mentions)
            AND id NOT IN (SELECT source_id FROM entity_relations)
            AND id NOT IN (SELECT target_id FROM entity_relations)
        """)
        self._load_graph()

    def clear_embeddings(self) -> int:
        """Null every item embedding. Used on an embedding-model switch — vectors
        from different models are incompatible. Item text/title/summary is
        preserved so they can be re-embedded. Returns the count cleared."""
        cur = self.db.execute("UPDATE items SET embedding = NULL WHERE embedding IS NOT NULL")
        self.db.commit()
        return cur.rowcount

    def count_items_to_reembed(self) -> int:
        """How many active items carry embeddable text (title or content)."""
        row = self.db.execute("SELECT COUNT(*) AS n FROM items WHERE status = 'active'").fetchone()
        return int(row["n"]) if row else 0

    def count_items_missing_embedding(self) -> int:
        """Active items that carry embeddable text but have NO embedding — the
        signature of an INTERRUPTED re-index (``clear_embeddings`` ran, then
        ``reembed_all`` died before finishing). Used to auto-resume on boot so the
        store never sits silently unsearchable. Ignores text-less items (nothing to
        embed) so a genuinely-empty item never triggers a phantom re-index."""
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM items WHERE status = 'active' "
            "AND embedding IS NULL "
            "AND (COALESCE(title,'') != '' OR COALESCE(content,'') != '')"
        ).fetchone()
        return int(row["n"]) if row else 0

    def count_items_needing_reembed(self, active_dim: int | None) -> int:
        """Active text-bearing items whose vector is MISSING **or STALE** (present but a
        different dimension than the active model's). Broader than
        ``count_items_missing_embedding``: it also catches the case where the gateway died
        AFTER an embedding-model SWAP but before/mid re-embed — those items keep an old
        wrong-dim vector (so ``missing`` is 0), yet are vector-dead against the new model's
        query dim. Boot auto-resume uses this so a mid-swap crash self-heals too. When
        ``active_dim`` is unknown (embedder not ready), falls back to missing-only."""
        if not active_dim:
            return self.count_items_missing_embedding()
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM items WHERE status = 'active' "
            "AND (COALESCE(title,'') != '' OR COALESCE(content,'') != '') "
            "AND (embedding IS NULL OR LENGTH(embedding) != ?)",
            (active_dim * 4,),
        ).fetchone()
        return int(row["n"]) if row else 0

    #: The whitespace set ``chunking.chunk_text`` strips before deciding a document is
    #: empty. SQLite's ``TRIM`` strips ONLY spaces unless the set is spelled out, so a
    #: bare ``TRIM(content)`` would keep selecting a tab/newline-only item that the
    #: chunker then declines — a backlog entry that can never drain, which makes a
    #: "completed" backfill re-run forever instead of being a no-op.
    _CHUNKABLE_WHITESPACE = " \t\n\r\v\f"

    #: The backlog predicate, in ONE place: an item needs chunking when it is active,
    #: not archived, carries non-whitespace content, and owns no chunk rows. Both the
    #: COUNT and the batch selector share it so the count can never disagree with the
    #: work — the backfill's resume state IS this query, never a persisted cursor, so no
    #: crash window can leave a cursor claiming work that the rows say is done.
    _CHUNK_BACKLOG_WHERE = (
        "FROM items WHERE status = 'active' "
        "AND COALESCE(is_archived, 0) = 0 "
        "AND LENGTH(TRIM(COALESCE(content, ''), ?)) > 0 "
        "AND NOT EXISTS (SELECT 1 FROM chunks WHERE chunks.item_id = items.id) "
    )

    def count_items_missing_chunks(self) -> int:
        """How many items still need chunking (KL-12/H1.5).

        Zero means the library is fully chunked, so a boot hook costs one COUNT. An item
        leaves this backlog the instant its chunk rows commit, which is what makes an
        interrupted backfill resumable without remembering anything.
        """
        row = self.db.execute(
            f"SELECT COUNT(*) AS n {self._CHUNK_BACKLOG_WHERE}",  # noqa: S608 — fixed literal
            (self._CHUNKABLE_WHITESPACE,),
        ).fetchone()
        return int(row["n"]) if row else 0

    def items_missing_chunks(self, limit: int, after_id: str | None = None) -> list[dict]:
        """One bounded batch of the chunk backlog, as ``{id, content}`` in id order.

        ``after_id`` is an exclusive keyset cursor rather than an OFFSET: it bounds peak
        memory (item content is unbounded) AND guarantees forward progress past an item
        the chunker declines, which re-fetching the backlog head would loop on forever.
        """
        params: list[object] = [self._CHUNKABLE_WHITESPACE]
        cursor = ""
        if after_id:
            cursor = "AND items.id > ? "
            params.append(after_id)
        params.append(int(limit))
        rows = self.db.execute(
            f"SELECT id, content {self._CHUNK_BACKLOG_WHERE}{cursor}"  # noqa: S608
            "ORDER BY items.id ASC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [{"id": r["id"], "content": r["content"] or ""} for r in rows]

    @staticmethod
    def _item_embed_one(embedder):
        """A single-text embed fn for *embedder*, used as ``embed_texts``' per-text fallback.

        Prefers ``.embed`` — literally the call ``embed_for_item`` makes once it has composed
        its text, so the vector is the same one. An embedder that exposes only
        ``embed_for_item`` (the shape this class's own docstring promises, and what the
        re-index tests pass) is adapted by handing it the ALREADY-composed text as the title:
        ``compose_item_text(composed, None)`` is ``composed`` for text that is already
        composed and stripped, so that vector is identical too. Returns None when the
        embedder offers neither, which ``embed_texts`` reports as "everything stays
        vector-less" — the same outcome the old per-item ``except Exception`` produced.
        """
        embed = getattr(embedder, "embed", None)
        if callable(embed):
            return embed
        for_item = getattr(embedder, "embed_for_item", None)
        if callable(for_item):
            return lambda text: for_item(text, None)
        return None

    def reembed_all(self, embedder, on_progress=None) -> dict:
        """Re-embed every active knowledge item with ``embedder`` (which exposes
        ``embed_for_item(title, summary)``, matching the ingestion pipeline).

        Embeds in GROUPS through ``knowledge.embed_batch.embed_texts`` (KL-15) — one provider
        call per group instead of one per item, with bounded retry and adaptive bisection. On
        a whole-library re-index that is the difference between a rate-limit blip costing a
        retry and it costing an item its vector for good. The item text is composed here with
        the pipeline's own ``compose_item_text``, which is exactly what ``embed_for_item``
        does internally, so the vectors are identical to the per-item path this replaces.

        ``on_progress(done, total)`` still fires once per item, in order, for job-progress
        streaming — grouping makes it coarser in TIME, not in call count. Items whose
        embedding fails are left vector-less and fall back to keyword/FTS retrieval; they are
        never corrupted and never deleted. Returns counts.
        """
        rows = self.db.execute(
            "SELECT id, title, summary, content FROM items WHERE status = 'active'"
        ).fetchall()
        total = len(rows)
        done = reembedded = failed = 0
        from personalclaw.knowledge.embed_batch import batch_size_from_config, embed_texts
        from personalclaw.knowledge.embedder import compose_item_text, floats_to_bytes
        from personalclaw.knowledge.pipeline.runner import active_batch_embed_fn

        embed_many = active_batch_embed_fn(embedder)
        embed_one = self._item_embed_one(embedder)
        size = batch_size_from_config()

        for start in range(0, total, size):
            group = rows[start : start + size]
            texts = []
            for r in group:
                title = r["title"] or ""
                summary = r["summary"] if "summary" in r.keys() else None
                content = r["content"] if "content" in r.keys() else None
                # Fall back to a content prefix when there's no title (chunk items).
                text_title = title or (content or "")[:200]
                texts.append(compose_item_text(text_title, summary, content))
            # A blank composed text never reaches a provider: ``UnifiedEmbedder.embed``
            # refuses one today, and a batch call cannot refuse a single member without
            # refusing its whole group. Such an item counts as failed, exactly as before.
            embeddable = [i for i, t in enumerate(texts) if t.strip()]
            vectors: list[list[float] | None] = [None] * len(texts)
            if embeddable:
                # `size` matches the slice, so this is one group per call — the outer loop
                # owns the grouping precisely so progress streams while it runs.
                got = embed_texts(
                    [texts[i] for i in embeddable],
                    embed_many=embed_many,
                    embed_one=embed_one,
                    batch_size=size,
                )
                for i, vec in zip(embeddable, got):
                    vectors[i] = vec
            for r, vec in zip(group, vectors):
                if vec:
                    self.db.execute(
                        "UPDATE items SET embedding = ? WHERE id = ?",
                        (floats_to_bytes(vec), r["id"]),
                    )
                    reembedded += 1
                else:
                    failed += 1
                done += 1
                if on_progress is not None:
                    on_progress(done, total)
        self.db.commit()
        return {"reembedded": reembedded, "failed": failed, "total": total}

    def search_items_fts(self, query, limit=10, offset=0) -> list:
        safe = self._sanitize_fts5(query)
        if not safe:
            return []
        try:
            rows = self.db.execute(
                "SELECT i.*, fts.rank FROM items_fts fts "
                "JOIN items i ON i.rowid = fts.rowid "
                "WHERE items_fts MATCH ? ORDER BY fts.rank LIMIT ? OFFSET ?",
                (safe, limit, offset),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return self._serialize_items(rows)

    def search_items_fts_count(self, query) -> int:
        safe = self._sanitize_fts5(query)
        if not safe:
            return 0
        try:
            row = self.db.execute(
                "SELECT COUNT(*) FROM items_fts WHERE items_fts MATCH ?", (safe,)
            ).fetchone()
            return row[0] if row else 0
        except sqlite3.OperationalError:
            return 0

    @staticmethod
    def _sanitize_fts5(query: str) -> str:
        tokens = query.split()
        return " ".join('"' + t.replace('"', '""') + '"' for t in tokens if t)

    def add_entity(self, name, entity_type, description=None, aliases=None) -> str:
        eid = str(uuid4())
        now = datetime.now().isoformat()
        self.db.execute(
            "INSERT INTO entities (id, name, entity_type, description, aliases, created_at, updated_at) "  # noqa: E501
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (eid, name, entity_type, description, json.dumps(aliases or []), now, now),
        )
        self.graph.add_node(eid, name=name, entity_type=entity_type)
        self.db.commit()
        return eid

    def backfill_entity_description(self, entity_id: str, description: str | None) -> bool:
        """Set an entity's description only when it currently has none — so a later,
        richer mention can fill in an entity first extracted without one, without
        clobbering an existing description. Returns True if it wrote."""
        desc = (description or "").strip()
        if not desc:
            return False
        row = self.db.execute(
            "SELECT description FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        if row is None or (row["description"] or "").strip():
            return False
        self.db.execute(
            "UPDATE entities SET description = ?, updated_at = ? WHERE id = ?",
            (desc, datetime.now().isoformat(), entity_id),
        )
        self.db.commit()
        return True

    def find_entity(self, name):
        row = self.db.execute("SELECT * FROM entities WHERE name = ?", (name,)).fetchone()
        if row:
            return dict(row)
        row = self.db.execute(
            "SELECT * FROM entities WHERE LOWER(name) = LOWER(?)", (name,)
        ).fetchone()
        if row:
            return dict(row)
        for row in self.db.execute("SELECT * FROM entities"):
            aliases = json.loads(row["aliases"]) if row["aliases"] else []
            if any(a.lower() == name.lower() for a in aliases):
                return dict(row)
        return None

    def merge_entities(self, keep_id, merge_id):
        self.db.execute(
            "UPDATE entity_relations SET source_id = ? WHERE source_id = ?", (keep_id, merge_id)
        )
        self.db.execute(
            "UPDATE entity_relations SET target_id = ? WHERE target_id = ?", (keep_id, merge_id)
        )
        # Remove self-loops created by the merge
        self.db.execute(
            "DELETE FROM entity_relations WHERE source_id = ? AND target_id = ?", (keep_id, keep_id)
        )
        # Delete mentions that would conflict, then update the rest
        self.db.execute(
            "DELETE FROM mentions WHERE entity_id = ? AND item_id IN (SELECT item_id FROM mentions WHERE entity_id = ?)",  # noqa: E501
            (merge_id, keep_id),
        )
        self.db.execute(
            "UPDATE mentions SET entity_id = ? WHERE entity_id = ?", (keep_id, merge_id)
        )
        self.db.execute("DELETE FROM entities WHERE id = ?", (merge_id,))
        self.db.commit()
        self._load_graph()

    def add_entity_relation(
        self, source_id, target_id, relation_type, description=None, weight=1.0, source_item_id=None
    ) -> str:
        # Idempotent on (source, target, type): the LLM often states the same relation
        # more than once in a single document, and a re-ingest re-extracts it — without
        # this guard each pass appended a duplicate edge, bloating the entity graph.
        existing = self.db.execute(
            "SELECT id FROM entity_relations WHERE source_id = ? AND target_id = ? AND relation_type = ? LIMIT 1",  # noqa: E501
            (source_id, target_id, relation_type),
        ).fetchone()
        if existing:
            self.graph.add_edge(
                source_id, target_id, id=existing["id"], relation_type=relation_type, weight=weight
            )
            return existing["id"]
        rid = str(uuid4())
        now = datetime.now().isoformat()
        self.db.execute(
            "INSERT INTO entity_relations (id, source_id, target_id, relation_type, description, weight, source_item_id, created_at) "  # noqa: E501
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, source_id, target_id, relation_type, description, weight, source_item_id, now),
        )
        self.graph.add_edge(
            source_id, target_id, id=rid, relation_type=relation_type, weight=weight
        )
        self.db.commit()
        return rid

    def add_mention(self, item_id, entity_id, context=None):
        now = datetime.now().isoformat()
        self.db.execute(
            "INSERT OR IGNORE INTO mentions (item_id, entity_id, context, created_at) VALUES (?, ?, ?, ?)",  # noqa: E501
            (item_id, entity_id, context, now),
        )
        self.db.commit()

    def get_neighbors(self, entity_id, depth=1) -> list:
        visited = set()
        frontier = {entity_id}
        for _ in range(depth):
            next_frontier = set()
            for nid in frontier:
                for neighbor in self.graph.successors(nid):
                    if neighbor not in visited and neighbor != entity_id:
                        next_frontier.add(neighbor)
                for neighbor in self.graph.predecessors(nid):
                    if neighbor not in visited and neighbor != entity_id:
                        next_frontier.add(neighbor)
            visited |= frontier
            frontier = next_frontier
        visited |= frontier
        visited.discard(entity_id)
        result = []
        for nid in visited:
            data = self.graph.nodes.get(nid, {})
            result.append(
                {"id": nid, "name": data.get("name"), "entity_type": data.get("entity_type")}
            )
        return result

    def get_entity_subgraph(self, entity_id, depth=2) -> dict:
        visited = set()
        frontier = {entity_id}
        for _ in range(depth):
            next_frontier = set()
            for nid in frontier:
                for neighbor in self.graph.successors(nid):
                    next_frontier.add(neighbor)
                for neighbor in self.graph.predecessors(nid):
                    next_frontier.add(neighbor)
            visited |= frontier
            frontier = next_frontier - visited
        visited |= frontier
        nodes = []
        for nid in visited:
            data = self.graph.nodes.get(nid, {})
            nodes.append({"id": nid, "name": data.get("name"), "type": data.get("entity_type")})
        edges = []
        for u, v, data in self.graph.edges(data=True):
            if u in visited and v in visited:
                edges.append(
                    {
                        "source": u,
                        "target": v,
                        "type": data.get("relation_type"),
                        "weight": data.get("weight"),
                    }
                )
        return {"nodes": nodes, "edges": edges}

    def get_stats(self) -> dict:
        """Library rollups: how much the user HAS. Mirrors are excluded from ``items``.

        Every consumer of this count means the user's own library — the Knowledge header's
        "N items" chip beside the list, Discover's "has this person engaged with Knowledge?"
        signal, and the status readout. A mirrored artifact (PEP-7) is none of those: it is
        indexed for search and deliberately never listed, so counting it made the header read
        "3 items" above an empty list on a home whose only content was three artifacts
        (measured on a running gateway) and would have marked Knowledge "engaged" for someone
        who never opened it.
        """
        from personalclaw.knowledge.artifact_ingest import ARTIFACT_ITEM_TYPE

        return {
            "items": self.db.execute(
                "SELECT COUNT(*) FROM items WHERE item_type != ?", (ARTIFACT_ITEM_TYPE,)
            ).fetchone()[0],
            "entities": self.db.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
            "relations": self.db.execute("SELECT COUNT(*) FROM entity_relations").fetchone()[0],
        }

    def corpus_overview(self, *, top_tags: int = 15) -> dict:
        """Corpus shape for gap-detection: total non-archived items, a by-type
        breakdown, and the most-common tags. Archived items are excluded so the view
        reflects the active library."""
        # Active + non-archived only — the same scope all_tags() and retrieval use, so
        # the agent's gap-detection view matches what the rest of the system considers
        # the live library (never counts inactive rows).
        active = "status='active' AND COALESCE(is_archived,0)=0"
        total = self.db.execute(
            f"SELECT COUNT(*) FROM items WHERE {active}",  # noqa: S608
        ).fetchone()[0]
        by_type = {
            r["item_type"]: r["c"]
            for r in self.db.execute(
                f"SELECT item_type, COUNT(*) c FROM items WHERE {active} "  # noqa: S608
                "GROUP BY item_type ORDER BY c DESC",
            )
        }
        # Counted from the join, not a denormalized tags.usage_count: the count must
        # respect the SAME active + non-archived scope as everything else here, and a
        # stored counter cannot express that (archiving an item would have to decrement
        # every one of its tags). Tests pin the archived-exclusion behavior.
        tag_rows = self.db.execute(
            "SELECT t.name AS tag, COUNT(*) c FROM item_tags it "
            "JOIN tags t ON t.id = it.tag_id JOIN items i ON i.id = it.item_id "
            f"WHERE i.{active} "  # noqa: S608
            "GROUP BY t.name ORDER BY c DESC, tag LIMIT ?",
            (top_tags,),
        ).fetchall()
        return {
            "total": total,
            "by_type": by_type,
            "top_tags": [{"tag": r["tag"], "count": r["c"]} for r in tag_rows],
            "entities": self.db.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
        }

    def all_tags(self) -> list[str]:
        """Distinct tags across non-archived items, ordered by frequency then name.
        Powers tag autocomplete in the create/edit forms (consistent tag reuse)."""
        rows = self.db.execute(
            "SELECT t.name AS tag FROM item_tags it "
            "JOIN tags t ON t.id = it.tag_id JOIN items i ON i.id = it.item_id "
            "WHERE COALESCE(i.is_archived,0)=0 AND i.status='active' "
            "GROUP BY t.name ORDER BY COUNT(*) DESC, t.name"
        ).fetchall()
        return [r["tag"] for r in rows if r["tag"]]

    # ── Tag taxonomy (KNOWLEDGE-LIBRARY S2, T2.2) ────────────────────────────

    def list_tags(self) -> list[dict]:
        """Every tag with its parent and live usage count, name-ordered.

        The count is computed from the join and scoped to the live library (active,
        non-archived) so it agrees with `all_tags` and `corpus_overview`. A tag with zero
        members is still listed: an empty tag is a real part of the taxonomy the user
        built, and hiding it would make a parent vanish the moment its last item was
        archived.
        """
        rows = self.db.execute(
            "SELECT t.id, t.name, t.parent_id, p.name AS parent_name, "
            "  (SELECT COUNT(*) FROM item_tags it JOIN items i ON i.id = it.item_id "
            "     WHERE it.tag_id = t.id AND i.status = 'active' "
            "       AND COALESCE(i.is_archived, 0) = 0) AS usage_count "
            "FROM tags t LEFT JOIN tags p ON p.id = t.parent_id "
            "ORDER BY t.name"
        ).fetchall()
        return [dict(r) for r in rows]

    def rename_tag(self, tag_id: int, name: str) -> bool:
        """Rename a tag in place. One row — which is exactly why `tags` has a surrogate
        id rather than using `name` as the primary key.

        Refuses a collision instead of silently merging: two tags becoming one is a
        distinct, lossier operation with its own method (:meth:`merge_tags`), and a
        rename that quietly merged would be impossible to undo.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("tag name is required")
        row = self.db.execute("SELECT id FROM tags WHERE id = ?", (tag_id,)).fetchone()
        if not row:
            return False
        clash = self.db.execute(
            "SELECT id FROM tags WHERE name = ? AND id <> ?", (name, tag_id)
        ).fetchone()
        if clash:
            raise ValueError(f"tag_name_taken:{name}")
        # Snapshot the indexed values BEFORE the rename — afterwards the old tag string
        # is unrecoverable, and FTS5 needs it verbatim to remove the stale terms.
        snapshot = self._fts_snapshot(self._items_with_tag(tag_id))
        self.db.execute("UPDATE tags SET name = ? WHERE id = ?", (name, tag_id))
        self.db.commit()
        self._resync_fts(snapshot)
        return True

    def set_tag_parent(self, tag_id: int, parent_id: int | None) -> bool:
        """Re-parent a tag. ``None`` makes it a root.

        Rejects cycles — including a tag becoming its own parent. The chat-folders
        hierarchy this mirrors has no such guard, so A→B→A is constructible there; a
        cycle here would make any recursive walk of the taxonomy hang.
        """
        if not self.db.execute("SELECT id FROM tags WHERE id = ?", (tag_id,)).fetchone():
            return False
        if parent_id is not None:
            if parent_id == tag_id:
                raise ValueError("tag_cycle")
            if not self.db.execute("SELECT id FROM tags WHERE id = ?", (parent_id,)).fetchone():
                raise ValueError(f"no such parent tag: {parent_id}")
            # Walk up from the proposed parent: if we reach tag_id, this edge closes a
            # loop. The visited set also stops a pre-existing cycle from hanging us.
            seen: set[int] = set()
            cursor: int | None = parent_id
            while cursor is not None and cursor not in seen:
                if cursor == tag_id:
                    raise ValueError("tag_cycle")
                seen.add(cursor)
                row = self.db.execute(
                    "SELECT parent_id FROM tags WHERE id = ?", (cursor,)
                ).fetchone()
                cursor = row["parent_id"] if row else None
        self.db.execute("UPDATE tags SET parent_id = ? WHERE id = ?", (parent_id, tag_id))
        self.db.commit()
        return True

    def merge_tags(self, source_id: int, target_id: int) -> dict:
        """Fold ``source_id`` into ``target_id``: every item tagged source becomes tagged
        target, then the source tag is deleted.

        Returns ``{"moved": n, "already": n}`` — items that gained the target tag versus
        items that already carried it. Provenance follows the more human of the two: if
        either membership was user-authored the merged one is, so a merge can never
        downgrade a user's tag into something enrichment may overwrite.
        """
        if source_id == target_id:
            raise ValueError("cannot merge a tag into itself")
        src = self.db.execute("SELECT id FROM tags WHERE id = ?", (source_id,)).fetchone()
        tgt = self.db.execute("SELECT id FROM tags WHERE id = ?", (target_id,)).fetchone()
        if not src or not tgt:
            return {"moved": 0, "already": 0}
        rows = self.db.execute(
            "SELECT item_id, source FROM item_tags WHERE tag_id = ?", (source_id,)
        ).fetchall()
        # Before anything moves: both sides' items change indexed tag text.
        snapshot = self._fts_snapshot(
            [r["item_id"] for r in rows] + self._items_with_tag(target_id)
        )
        moved = already = 0
        now = datetime.now().isoformat()
        self.db.execute("BEGIN")
        try:
            for row in rows:
                existing = self.db.execute(
                    "SELECT source FROM item_tags WHERE item_id = ? AND tag_id = ?",
                    (row["item_id"], target_id),
                ).fetchone()
                if existing:
                    already += 1
                    if "user" in (existing["source"], row["source"]):
                        self.db.execute(
                            "UPDATE item_tags SET source = 'user' "
                            "WHERE item_id = ? AND tag_id = ?",
                            (row["item_id"], target_id),
                        )
                else:
                    self.db.execute(
                        "INSERT INTO item_tags (item_id, tag_id, source, added_at) "
                        "VALUES (?, ?, ?, ?)",
                        (row["item_id"], target_id, row["source"] or "user", now),
                    )
                    moved += 1
            # Children of the source follow it to the target rather than being orphaned.
            self.db.execute(
                "UPDATE tags SET parent_id = ? WHERE parent_id = ?", (target_id, source_id)
            )
            self.db.execute("DELETE FROM item_tags WHERE tag_id = ?", (source_id,))
            self.db.execute("DELETE FROM tags WHERE id = ?", (source_id,))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self._resync_fts(snapshot)
        return {"moved": moved, "already": already}

    def delete_tag(self, tag_id: int) -> bool:
        """Remove a tag from the taxonomy and from every item carrying it.

        Children are re-parented to root rather than deleted (the ``ON DELETE SET NULL``
        on the self-FK). Deleting a parent should not silently destroy the branch beneath
        it — that is the one behavior of the chat-folders precedent worth keeping.
        """
        if not self.db.execute("SELECT id FROM tags WHERE id = ?", (tag_id,)).fetchone():
            return False
        snapshot = self._fts_snapshot(self._items_with_tag(tag_id))
        self.db.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        self.db.commit()
        self._resync_fts(snapshot)
        return True

    def _items_with_tag(self, tag_id: int) -> list[str]:
        """Item ids currently carrying *tag_id*."""
        return [
            r["item_id"]
            for r in self.db.execute(
                "SELECT item_id FROM item_tags WHERE tag_id = ?", (tag_id,)
            ).fetchall()
        ]

    def _fts_snapshot(self, item_ids: list[str]) -> list[tuple]:
        """Capture ``(rowid, title, content, tags)`` as currently INDEXED, for later
        removal from the FTS index.

        Must be called BEFORE the tag rows change. An external-content FTS5 index cannot
        be asked "what did you store for this row?" — the ``'delete'`` command requires
        the caller to hand back the exact column values that were inserted, so a
        rename/merge/delete has to snapshot them first or it can never remove them.
        """
        out: list[tuple] = []
        for item_id in dict.fromkeys(item_ids):
            row = self.db.execute(
                "SELECT i.rowid AS rowid, s.title AS title, s.content AS content, "
                "       s.tags AS tags "
                "FROM items i JOIN items_fts_src s ON s.rowid = i.rowid WHERE i.id = ?",
                (item_id,),
            ).fetchone()
            if row is not None:
                out.append((row["rowid"], row["title"], row["content"], row["tags"]))
        return out

    def _resync_fts(self, snapshot: list[tuple]) -> None:
        """Re-index the snapshotted rows so the indexed tag text matches the tag rows.

        This is the FIFTH FTS write path, and it did not exist before tags became rows: a
        rename/merge/delete changes an item's searchable tag text without touching the
        item itself. Miss it and tag search rots silently as the taxonomy is curated —
        writes keep succeeding while results drift.

        The removal MUST use FTS5's ``'delete'`` command with the pre-change values from
        :meth:`_fts_snapshot`. A plain ``DELETE FROM items_fts WHERE rowid = ?`` is a
        SILENT NO-OP on an external-content table (measured: the old term keeps matching
        after the delete+insert, so a renamed tag stays findable under BOTH names) — and
        passing the wrong values to ``'delete'`` corrupts the posting list without
        raising, which ``integrity-check`` does not detect.
        """
        for rowid, title, content, tags in snapshot:
            self.db.execute(
                "INSERT INTO items_fts (items_fts, rowid, title, content, tags) "
                "VALUES ('delete', ?, ?, ?, ?)",
                (rowid, title, content, tags),
            )
            fresh = self.db.execute(
                "SELECT title, content, tags FROM items_fts_src WHERE rowid = ?", (rowid,)
            ).fetchone()
            if fresh is None:
                continue  # the item itself is gone; the removal above is the whole job
            self.db.execute(
                "INSERT INTO items_fts (rowid, title, content, tags) VALUES (?, ?, ?, ?)",
                (rowid, fresh["title"], fresh["content"], fresh["tags"]),
            )
        self.db.commit()

    # ── Collections (KNOWLEDGE-LIBRARY S1, contract C2) ──────────────────────

    VALID_READ_STATES = ("unread", "reading", "read")
    VALID_COLLECTION_KINDS = ("manual", "smart")

    def create_collection(
        self, *, name: str, kind: str = "manual", query: str = "", icon: str = ""
    ) -> str:
        """Create a shelf. Returns its id.

        A MANUAL collection holds an explicit membership list; a SMART one stores a
        query re-run on every read, so it stays current as items arrive without any
        backfill. A smart collection with no query would silently match nothing, so
        that is rejected rather than created as a shelf that looks broken.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("collection name is required")
        if kind not in self.VALID_COLLECTION_KINDS:
            raise ValueError(
                f"unknown collection kind {kind!r}; expected one of "
                f"{list(self.VALID_COLLECTION_KINDS)}"
            )
        if kind == "smart" and not (query or "").strip():
            raise ValueError("a smart collection requires a query")
        cid = str(uuid4())
        now = datetime.now().isoformat()
        # New shelves land at the end of the rail rather than the top: the user's
        # existing order is theirs, and silently reshuffling it on every create is
        # the kind of "helpful" the ordering column exists to prevent.
        nxt = self.db.execute("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM collections")
        position = int(nxt.fetchone()["p"])
        self.db.execute(
            "INSERT INTO collections (id, name, kind, query, icon, position, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, name, kind, (query or "").strip(), icon or "", position, now, now),
        )
        self.db.commit()
        return cid

    def list_collections(self) -> list[dict]:
        """Every shelf in rail order, each with its item count.

        A smart collection's count is deliberately NOT computed here: it would mean
        running one search per shelf on every list call. The rail shows manual counts
        and resolves a smart shelf when the user opens it.
        """
        rows = self.db.execute(
            "SELECT c.*, ("
            "  SELECT COUNT(*) FROM collection_items ci WHERE ci.collection_id = c.id"
            ") AS item_count "
            "FROM collections c ORDER BY c.position, c.created_at"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("kind") == "smart":
                d["item_count"] = None  # unknown until resolved; see the docstring
            out.append(d)
        return out

    def get_collection(self, collection_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
        return dict(row) if row else None

    def update_collection(self, collection_id: str, **fields) -> bool:
        """Rename / re-icon / re-query / reorder a shelf. Returns False if absent."""
        allowed = {"name", "kind", "query", "icon", "position"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return False
        if "kind" in sets and sets["kind"] not in self.VALID_COLLECTION_KINDS:
            raise ValueError(f"unknown collection kind {sets['kind']!r}")
        # Guard the combination, not just each field: switching an existing shelf to
        # smart without a query (stored or supplied) would leave it permanently empty.
        if sets.get("kind") == "smart" and not (sets.get("query") or "").strip():
            existing = self.get_collection(collection_id) or {}
            if not (existing.get("query") or "").strip():
                raise ValueError("a smart collection requires a query")
        sets["updated_at"] = datetime.now().isoformat()
        cols = ", ".join(f"{k} = ?" for k in sets)
        cur = self.db.execute(
            f"UPDATE collections SET {cols} WHERE id = ?",  # noqa: S608 — keys allowlisted
            (*sets.values(), collection_id),
        )
        self.db.commit()
        return cur.rowcount > 0

    def delete_collection(self, collection_id: str) -> bool:
        """Remove a shelf. Membership rows go with it; the ITEMS are untouched —
        a shelf is a view onto the library, not a container that owns its contents."""
        self.db.execute("DELETE FROM collection_items WHERE collection_id = ?", (collection_id,))
        cur = self.db.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
        self.db.commit()
        return cur.rowcount > 0

    def add_to_collection(self, collection_id: str, item_id: str) -> bool:
        """Shelve an item. Idempotent (the composite PK absorbs a repeat).

        Returns False when the shelf or item doesn't exist — a membership row pointing
        at a missing item would surface as a phantom entry in the shelf view.
        """
        if not self.get_collection(collection_id):
            return False
        if not self.db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone():
            return False
        self.db.execute(
            "INSERT OR IGNORE INTO collection_items (collection_id, item_id, added_at) "
            "VALUES (?, ?, ?)",
            (collection_id, item_id, datetime.now().isoformat()),
        )
        self.db.commit()
        return True

    def remove_from_collection(self, collection_id: str, item_id: str) -> bool:
        cur = self.db.execute(
            "DELETE FROM collection_items WHERE collection_id = ? AND item_id = ?",
            (collection_id, item_id),
        )
        self.db.commit()
        return cur.rowcount > 0

    def collections_for_item(self, item_id: str) -> list[dict]:
        """Every MANUAL shelf holding this item. One item can sit on many shelves."""
        rows = self.db.execute(
            "SELECT c.* FROM collections c JOIN collection_items ci ON ci.collection_id = c.id "
            "WHERE ci.item_id = ? ORDER BY c.position, c.created_at",
            (item_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def resolve_collection(self, collection_id: str, limit: int = 50) -> list[dict]:
        """The items on a shelf.

        Manual: the membership join, newest-shelved first. Smart: the stored query run
        through hybrid retrieval, so the shelf reflects the library as it is now — that
        live-ness is the whole point of a smart collection, and it is also why this is a
        read-time resolve rather than a materialized list.

        Archived items are excluded from both kinds: an archive is the user saying "not
        in my active library", and a shelf is an active-library view.
        """
        coll = self.get_collection(collection_id)
        if not coll:
            return []
        if coll.get("kind") == "smart":
            query = (coll.get("query") or "").strip()
            if not query:
                return []
            from personalclaw.knowledge.retrieval import HybridRetriever

            hits = HybridRetriever(self).search(query, limit=limit)
            # Re-read each hit as a full item so a smart shelf and a manual one hand
            # the UI the SAME shape; a retrieval hit is a search projection, not an item.
            out = []
            for h in hits:
                item = self.get_item(h.get("id"))
                if item and not item.get("is_archived"):
                    out.append(item)
            return out
        rows = self.db.execute(
            "SELECT i.* FROM items i JOIN collection_items ci ON ci.item_id = i.id "
            "WHERE ci.collection_id = ? AND COALESCE(i.is_archived, 0) = 0 "
            "ORDER BY ci.added_at DESC LIMIT ?",
            (collection_id, limit),
        ).fetchall()
        return self._serialize_items(rows)

    # ── Item curation ────────────────────────────────────────────────────────

    def set_read_state(self, item_id: str, state: str) -> bool:
        """Set an item's read state. Enrichment-style write: does NOT touch
        `updated_at`, because marking something read is not editing it and would
        otherwise reorder a recency-sorted library out from under the user."""
        if state not in self.VALID_READ_STATES:
            raise ValueError(
                f"unknown read state {state!r}; expected one of {list(self.VALID_READ_STATES)}"
            )
        if not self.db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone():
            return False
        self.update_item(item_id, touch=False, read_state=state)
        return True

    def set_favorited(self, item_id: str, value: bool) -> bool:
        """Star / unstar. Also a non-touching write, for the same reason."""
        if not self.db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone():
            return False
        self.update_item(item_id, touch=False, favorited=1 if value else 0)
        return True

    # ── Reading annotations ──────────────────────────────────────────────────

    #: A highlight longer than this is a mis-drag (or a select-all), not a passage worth
    #: keeping, and storing the whole article as its own annotation is worse than
    #: refusing. Roughly two long paragraphs.
    MAX_ANNOTATION_QUOTE = 2000

    def add_annotation(
        self, item_id: str, quote: str, *, occurrence: int = 0, note: str = ""
    ) -> dict | None:
        """Persist one reading highlight against an item. Returns the row, or None if
        the item does not exist.

        Like read-state and favorites this is a NON-TOUCHING write: highlighting a
        passage is reading, not editing, so it must not reorder a recency-sorted
        library. Nothing here writes `items.updated_at`.
        """
        quote = (quote or "").strip()
        if not quote:
            raise ValueError("a highlight needs a quote")
        if len(quote) > self.MAX_ANNOTATION_QUOTE:
            raise ValueError(
                f"quote is {len(quote)} chars; the limit is {self.MAX_ANNOTATION_QUOTE}"
            )
        if occurrence < 0:
            raise ValueError("occurrence must be >= 0")
        if not self.db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone():
            return None
        row_id = str(uuid4())
        now = datetime.now().isoformat()
        self.db.execute(
            "INSERT INTO annotations (id, item_id, quote, occurrence, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (row_id, item_id, quote, int(occurrence), (note or "").strip(), now),
        )
        self.db.commit()
        return {
            "id": row_id,
            "item_id": item_id,
            "quote": quote,
            "occurrence": int(occurrence),
            "note": (note or "").strip(),
            "created_at": now,
        }

    def list_annotations(self, item_id: str) -> list[dict]:
        """This item's highlights, oldest first — reading order, which for a document
        read top-to-bottom is also roughly document order, and is stable under
        re-render in a way a similarity or recency sort is not."""
        rows = self.db.execute(
            "SELECT id, item_id, quote, occurrence, note, created_at FROM annotations "
            "WHERE item_id = ? ORDER BY created_at ASC, id ASC",
            (item_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_annotation(self, annotation_id: str) -> bool:
        """Remove one highlight. False when it was already gone."""
        cur = self.db.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))
        self.db.commit()
        return bool(cur.rowcount)

    # ── Bulk curation ────────────────────────────────────────────────────────

    #: Ops :meth:`bulk_apply` accepts. ``delete`` is deliberately absent — see the
    #: method docstring.
    BULK_OPS = (
        "collect",
        "uncollect",
        "read_state",
        "favorite",
        "archive",
        "restore",
        "pin",
    )

    def bulk_apply(self, op: str, item_ids: list[str], **args) -> dict:
        """Apply one curation op to many items, reporting per-item outcomes.

        Returns ``{"changed": [...], "unchanged": [...], "missing": [...]}``. Every op
        is per-item best-effort: a selection can go stale between the click and the
        request, and 38-of-40 is a useful answer where a wholesale failure is not.
        ``unchanged`` means the item already had that state — worth distinguishing from
        a failure so the UI can say "8 were already read".

        ``delete`` is NOT an op here, mirroring the deliberate exclusion in the chat
        bulk endpoint (``dashboard/session_bulk.py``): every op above is reversible,
        and putting an irreversible one beside them is a mis-click away from data loss.
        Deleting stays the single-item path with its own confirmation.

        Ops and their args:
          * ``collect`` / ``uncollect`` — ``collection_id``
          * ``read_state`` — ``state`` (one of :attr:`VALID_READ_STATES`)
          * ``favorite`` / ``pin`` / ``archive`` / ``restore`` — ``value`` (bool;
            ``archive``/``restore`` ignore it, the op name carries the direction)

        Raises ``ValueError`` for an unknown op or a missing/invalid arg, so a caller
        that forgot an argument gets a typed refusal rather than a silent no-op over
        every selected item.
        """
        if op not in self.BULK_OPS:
            raise ValueError(f"unknown bulk op {op!r}; expected one of {list(self.BULK_OPS)}")

        collection_id = str(args.get("collection_id") or "")
        if op in ("collect", "uncollect"):
            if not collection_id:
                raise ValueError(f"{op} requires collection_id")
            coll = self.get_collection(collection_id)
            if coll is None:
                raise ValueError(f"no such collection: {collection_id}")
            # A smart shelf resolves its membership from a query at read time, so a
            # stored row would be ignored by its own reads. Refuse loudly rather than
            # writing rows that do nothing (the same rule the single-item route uses).
            if coll.get("kind") == "smart":
                raise ValueError("smart_collection_immutable")

        state = str(args.get("state") or "")
        if op == "read_state" and state not in self.VALID_READ_STATES:
            raise ValueError(
                f"read_state requires state in {list(self.VALID_READ_STATES)}; got {state!r}"
            )
        value = bool(args.get("value", True))

        changed: list[str] = []
        unchanged: list[str] = []
        missing: list[str] = []

        for item_id in item_ids:
            row = self.db.execute(
                "SELECT read_state, favorited, is_archived, is_pinned FROM items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                missing.append(item_id)
                continue

            did = False
            if op == "collect":
                # add_to_collection is idempotent (INSERT OR IGNORE), so ask first
                # whether this is genuinely a change — otherwise re-shelving 40 items
                # would report 40 changes and no-ops alike.
                already = self.db.execute(
                    "SELECT 1 FROM collection_items WHERE collection_id = ? AND item_id = ?",
                    (collection_id, item_id),
                ).fetchone()
                did = not already and self.add_to_collection(collection_id, item_id)
            elif op == "uncollect":
                did = self.remove_from_collection(collection_id, item_id)
            elif op == "read_state":
                did = (row["read_state"] or "unread") != state and self.set_read_state(
                    item_id, state
                )
            elif op == "favorite":
                did = bool(row["favorited"]) != value and self.set_favorited(item_id, value)
            elif op in ("archive", "restore"):
                want = op == "archive"
                if bool(row["is_archived"]) != want:
                    # A touching write, unlike read-state/favorite: archiving IS a
                    # change to the item's standing in the library, not a reading note.
                    self.update_item(item_id, is_archived=1 if want else 0)
                    did = True
            elif op == "pin":
                if bool(row["is_pinned"]) != value:
                    self.update_item(item_id, is_pinned=1 if value else 0)
                    did = True

            (changed if did else unchanged).append(item_id)

        return {"changed": changed, "unchanged": unchanged, "missing": missing}

    def close(self):
        self.db.close()
