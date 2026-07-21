"""KnowledgeStore -- SQLite backed knowledge graph with lightweight in-memory graph."""

import json
import logging
from collections import defaultdict
from datetime import datetime
from uuid import uuid4

logger = logging.getLogger(__name__)

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

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


class KnowledgeStore:
    def __init__(self, db_path: str):
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
                tags TEXT DEFAULT '[]',
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
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);

            CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
                title, content, tags, content=items, content_rowid=rowid
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
        ("word_count", "INTEGER DEFAULT 0"),
        ("is_pinned", "INTEGER DEFAULT 0"),
        ("is_archived", "INTEGER DEFAULT 0"),
        ("insights", "TEXT DEFAULT '{}'"),
        ("ai_title", "TEXT"),
        ("provider", "TEXT DEFAULT 'native'"),
        # Ingestion node-graph lifecycle (#30): queued|processing|done|failed|partial.
        ("processing_status", "TEXT DEFAULT ''"),
        ("processing_error", "TEXT"),
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
        # Clean break: drop the legacy chunk/source model from existing DBs. A
        # previously-chunked doc collapses to its first row (chunk_index 0); the
        # extra chunk rows + their mentions/relations are removed. Legacy tables are
        # dropped first so deleting chunk-item rows can't trip a stale FK. FK
        # enforcement is suspended for the structural rewrite (toggle outside any txn).
        if "chunk_index" in cols or "source_id" in cols:
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
        extra: dict | None = None,
    ) -> str:
        """Create one logical-document typed item (note/gist/bookmark/…) directly.

        This is the one logical document the typed UI + agents work with: it carries
        the first-class fields (type, url, word_count) and is NOT chunked (chunking is
        an embedding-pipeline detail). ``extra`` may set any other first-class column
        (mime_type, file_path, …)."""
        item_id = str(uuid4())
        now = datetime.now().isoformat()
        tags_json = json.dumps(tags or [])
        word_count = len((content or "").split())
        extra = extra or {}
        # Canonicalize a bookmark's URL at the storage boundary so dedup is consistent
        # regardless of caller (HTTP handler, agent tool, provider) and tracking-param /
        # trailing-slash variants of the same page collapse to one item.
        if item_type == "bookmark" and url:
            url = normalize_url(url)
        self.db.execute("BEGIN")
        try:
            self.db.execute(
                "INSERT INTO items (id, title, content, item_type, "
                "summary, tags, status, url, word_count, provider, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
                (
                    item_id,
                    title,
                    content,
                    item_type,
                    summary,
                    tags_json,
                    url,
                    word_count,
                    provider,
                    now,
                    now,
                ),
            )
            rowid = self.db.execute("SELECT rowid FROM items WHERE id = ?", (item_id,)).fetchone()[
                0
            ]
            self.db.execute(
                "INSERT INTO items_fts (rowid, title, content, tags) VALUES (?, ?, ?, ?)",
                (rowid, title, content, tags_json),
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        if extra:
            self.update_item(item_id, **extra)
            self.db.commit()
        return item_id

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

    @staticmethod
    def _serialize_item(row) -> dict:
        d = dict(row)
        raw = d.pop("embedding", None)
        # The raw 384-float vector is an embedding-pipeline detail no API consumer
        # reads — shipping it would bloat every list/detail response (~40% of payload).
        # Responses carry only a `has_embedding` flag; the vector never leaves the DB.
        d["has_embedding"] = bool(raw)
        # Typed-item API shape: expose `type` (alias of the item_type storage
        # column), tags/file_metadata/insights as parsed JSON, booleans as bool.
        d["type"] = d.get("item_type", "")
        for key in ("tags", "file_metadata", "insights"):
            val = d.get(key)
            if isinstance(val, str):
                try:
                    d[key] = json.loads(val) if val else ([] if key == "tags" else {})
                except (json.JSONDecodeError, ValueError):
                    d[key] = [] if key == "tags" else {}
            elif val is None:
                d[key] = [] if key == "tags" else {}
        for key in ("is_pinned", "is_archived"):
            d[key] = bool(d.get(key))
        d.setdefault("provider", "native")
        return d

    _ITEM_COLUMNS = {
        "title",
        "content",
        "item_type",
        "summary",
        "tags",
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
        safe = {k: v for k, v in fields.items() if k in self._ITEM_COLUMNS}
        if not safe:
            return
        # Read old FTS values BEFORE the update
        fts_fields = {"title", "content", "tags"} & set(fields)
        old_row = None
        if fts_fields:
            old_row = self.db.execute(
                "SELECT rowid, title, content, tags FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        cols = ", ".join(f"{k} = ?" for k in safe)
        vals = [json.dumps(v) if isinstance(v, (list, dict)) else v for v in safe.values()]
        self.db.execute("BEGIN")
        try:
            self.db.execute(f"UPDATE items SET {cols} WHERE id = ?", (*vals, item_id))  # noqa: S608
            # Sync FTS: delete with OLD values, insert with NEW values
            if old_row:
                self.db.execute(
                    "INSERT INTO items_fts (items_fts, rowid, title, content, tags) VALUES ('delete', ?, ?, ?, ?)",  # noqa: E501
                    (old_row["rowid"], old_row["title"], old_row["content"], old_row["tags"]),
                )
                new_row = self.db.execute(
                    "SELECT title, content, tags FROM items WHERE id = ?", (item_id,)
                ).fetchone()
                self.db.execute(
                    "INSERT INTO items_fts (rowid, title, content, tags) VALUES (?, ?, ?, ?)",
                    (old_row["rowid"], new_row["title"], new_row["content"], new_row["tags"]),
                )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _delete_item_cascade(self, item_id):
        """Delete item and its dependents without commit/graph reload (for batch use)."""
        row = self.db.execute(
            "SELECT rowid, title, content, tags FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if row:
            self.db.execute(
                "INSERT INTO items_fts (items_fts, rowid, title, content, tags) VALUES ('delete', ?, ?, ?, ?)",  # noqa: E501
                (row["rowid"], row["title"], row["content"], row["tags"]),
            )
        self.db.execute("DELETE FROM mentions WHERE item_id = ?", (item_id,))
        self.db.execute("DELETE FROM entity_relations WHERE source_item_id = ?", (item_id,))
        self.db.execute("DELETE FROM extracted_contents WHERE item_id = ?", (item_id,))
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

    def reembed_all(self, embedder, on_progress=None) -> dict:
        """Re-embed every active knowledge item with ``embedder`` (which exposes
        ``embed_for_item(title, summary)``, matching the ingestion pipeline).

        ``on_progress(done, total)`` fires after each item for job-progress
        streaming. Items whose embedding fails (model returns None) are left
        vector-less and fall back to keyword/FTS retrieval. Returns counts.
        """
        rows = self.db.execute(
            "SELECT id, title, summary, content FROM items WHERE status = 'active'"
        ).fetchall()
        total = len(rows)
        done = reembedded = failed = 0
        from personalclaw.knowledge.embedder import floats_to_bytes

        for r in rows:
            title = r["title"] or ""
            summary = r["summary"] if "summary" in r.keys() else None
            content = r["content"] if "content" in r.keys() else None
            # Fall back to a content prefix when there's no title (chunk items).
            text_title = title or (content or "")[:200]
            vec = None
            try:
                vec = embedder.embed_for_item(text_title, summary, content)
            except Exception:
                vec = None
            if vec:
                self.db.execute(
                    "UPDATE items SET embedding = ? WHERE id = ?", (floats_to_bytes(vec), r["id"])
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
        return [self._serialize_item(r) for r in rows]

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
        return {
            "items": self.db.execute("SELECT COUNT(*) FROM items").fetchone()[0],
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
        tag_rows = self.db.execute(
            "SELECT je.value AS tag, COUNT(*) c FROM items, json_each(items.tags) je "
            f"WHERE items.{active} "  # noqa: S608
            "GROUP BY je.value ORDER BY c DESC, tag LIMIT ?",
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
            "SELECT je.value AS tag FROM items, json_each(items.tags) je "
            "WHERE COALESCE(items.is_archived,0)=0 AND items.status='active' "
            "GROUP BY je.value ORDER BY COUNT(*) DESC, je.value"
        ).fetchall()
        return [r["tag"] for r in rows if r["tag"]]

    def close(self):
        self.db.close()
