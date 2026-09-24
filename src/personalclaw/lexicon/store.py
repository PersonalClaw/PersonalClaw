"""Lexicon SQLite store (core LEX.2) — the personal vocabulary + learned corrections.

Its own ``lexicon.db`` (separate from knowledge.db) so it is trivially rebuildable from
the graph and keeps concerns apart (design open-question #2, leaning-that-way resolved to
its own file). Two tables:

* ``terms``       — canonical vocabulary (from graph entities / manual / learned), each
  with its Double Metaphone keys, an ``entity_type``, a ``weight`` (recency × frequency ×
  correction-count, drives bias-term ranking), ``source`` and ``enabled``.
* ``corrections`` — the learned ``heard → meant`` loop; ``count`` bumps each time the user
  fixes the same mishearing, and ``auto_apply`` flips once past threshold / "always fix".

Access mirrors KnowledgeStore (WAL, busy_timeout, Row factory, check_same_thread=False).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass


def lexicon_db_path() -> str:
    from personalclaw.config.loader import config_dir

    db_dir = os.path.join(str(config_dir()), "workspace", "lexicon")
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "lexicon.db")


@dataclass
class LexiconTerm:
    id: str
    canonical: str
    aliases: list[str]
    phonetic_keys: list[str]
    entity_type: str
    weight: float
    source: str  # graph | manual | learned
    enabled: bool


@dataclass
class Correction:
    id: str
    heard: str
    meant: str
    phonetic_key: str
    count: int
    auto_apply: bool
    last_seen: str


class LexiconStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or lexicon_db_path()
        self.db = sqlite3.connect(
            self.db_path, timeout=30, isolation_level=None, check_same_thread=False
        )
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS terms (
                id TEXT PRIMARY KEY,
                canonical TEXT NOT NULL,
                aliases_json TEXT DEFAULT '[]',
                phonetic_keys_json TEXT DEFAULT '[]',
                entity_type TEXT DEFAULT '',
                weight REAL DEFAULT 1.0,
                source TEXT DEFAULT 'graph',
                enabled INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_terms_canonical ON terms(canonical);
            CREATE INDEX IF NOT EXISTS idx_terms_weight ON terms(weight DESC);

            CREATE TABLE IF NOT EXISTS corrections (
                id TEXT PRIMARY KEY,
                heard TEXT NOT NULL,
                meant TEXT NOT NULL,
                phonetic_key TEXT DEFAULT '',
                count INTEGER DEFAULT 1,
                auto_apply INTEGER DEFAULT 0,
                last_seen TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_corr_pair ON corrections(heard, meant);

            -- phonetic_key → term ids, for O(1) same-sound lookup during correction.
            CREATE TABLE IF NOT EXISTS phonetic_index (
                phonetic_key TEXT NOT NULL,
                term_id TEXT NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
                PRIMARY KEY (phonetic_key, term_id)
            );
            CREATE INDEX IF NOT EXISTS idx_phon_key ON phonetic_index(phonetic_key);
            """)

    # ── terms ────────────────────────────────────────────────────────────────
    def upsert_term(
        self,
        term_id: str,
        canonical: str,
        *,
        aliases: list[str] | None = None,
        phonetic_keys: list[str] | None = None,
        entity_type: str = "",
        weight: float = 1.0,
        source: str = "graph",
        enabled: bool = True,
    ) -> None:
        now = _now()
        aliases_j = json.dumps(aliases or [])
        keys = phonetic_keys or []
        keys_j = json.dumps(keys)
        # Preserve created_at + never downgrade a manual/learned source back to graph.
        existing = self.db.execute(
            "SELECT source, created_at, enabled FROM terms WHERE id = ?", (term_id,)
        ).fetchone()
        created = existing["created_at"] if existing else now
        eff_source = source
        if existing and existing["source"] in ("manual", "learned") and source == "graph":
            eff_source = existing["source"]
        # A graph re-sync must not undo the user's prune: keep the stored enabled flag.
        eff_enabled = enabled
        if existing and source == "graph":
            eff_enabled = bool(existing["enabled"])
        self.db.execute(
            """INSERT INTO terms (id, canonical, aliases_json, phonetic_keys_json, entity_type,
                                  weight, source, enabled, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 canonical=excluded.canonical, aliases_json=excluded.aliases_json,
                 phonetic_keys_json=excluded.phonetic_keys_json, entity_type=excluded.entity_type,
                 weight=excluded.weight, source=excluded.source, enabled=excluded.enabled,
                 updated_at=excluded.updated_at""",
            (
                term_id,
                canonical,
                aliases_j,
                keys_j,
                entity_type,
                weight,
                eff_source,
                1 if eff_enabled else 0,
                created,
                now,
            ),
        )
        # Rebuild this term's phonetic index rows.
        self.db.execute("DELETE FROM phonetic_index WHERE term_id = ?", (term_id,))
        for k in set(keys):
            self.db.execute(
                "INSERT OR IGNORE INTO phonetic_index (phonetic_key, term_id) VALUES (?,?)",
                (k, term_id),
            )

    def bump_weight(self, canonical: str, delta: float = 1.0) -> None:
        self.db.execute(
            "UPDATE terms SET weight = weight + ?, updated_at = ? WHERE canonical = ?",
            (delta, _now(), canonical),
        )

    def list_terms(
        self, *, source: str = "", search: str = "", limit: int = 500
    ) -> list[LexiconTerm]:
        q = "SELECT * FROM terms WHERE 1=1"
        args: list = []
        if source:
            q += " AND source = ?"
            args.append(source)
        if search:
            q += " AND canonical LIKE ?"
            args.append(f"%{search}%")
        q += " ORDER BY weight DESC, canonical LIMIT ?"
        args.append(limit)
        return [_row_to_term(r) for r in self.db.execute(q, args)]

    def top_terms(self, limit: int) -> list[LexiconTerm]:
        rows = self.db.execute(
            "SELECT * FROM terms WHERE enabled = 1 ORDER BY weight DESC, canonical LIMIT ?",
            (limit,),
        )
        return [_row_to_term(r) for r in rows]

    def terms_for_phonetic_key(self, key: str) -> list[LexiconTerm]:
        rows = self.db.execute(
            """SELECT t.* FROM terms t JOIN phonetic_index p ON p.term_id = t.id
               WHERE p.phonetic_key = ? AND t.enabled = 1""",
            (key,),
        )
        return [_row_to_term(r) for r in rows]

    def terms_for_phonetic_prefix(self, prefix: str) -> list[LexiconTerm]:
        """Terms whose phonetic key SHARES a prefix with *prefix* (either direction) —
        catches severe mishearings that truncate a word to a shorter metaphone key (e.g.
        'Cubeer'=KPR vs 'Kubernetes'=KPRN). Uses a LIKE prefix scan on the indexed key."""
        if len(prefix) < 3:
            return []
        rows = self.db.execute(
            """SELECT DISTINCT t.* FROM terms t JOIN phonetic_index p ON p.term_id = t.id
               WHERE t.enabled = 1 AND (p.phonetic_key LIKE ? OR ? LIKE p.phonetic_key || '%')""",
            (prefix + "%", prefix),
        )
        return [_row_to_term(r) for r in rows]

    def get_term_by_canonical(self, canonical: str) -> LexiconTerm | None:
        """Exact-canonical lookup (ASCII case-insensitive) — NOT a substring search."""
        row = self.db.execute(
            "SELECT * FROM terms WHERE canonical = ? COLLATE NOCASE", (canonical,)
        ).fetchone()
        return _row_to_term(row) if row else None

    def prune_graph_terms(self, keep: set[str]) -> int:
        """Delete graph-sourced terms whose id is not in *keep* — their entity left the
        knowledge graph, so a resync must drop them. Manual/learned terms are never
        touched. (phonetic_index rows are deleted explicitly: the FK cascade is inert
        because sqlite's foreign_keys pragma is off, same as delete_term.)"""
        stale = [
            r["id"]
            for r in self.db.execute("SELECT id FROM terms WHERE source = 'graph'")
            if r["id"] not in keep
        ]
        for tid in stale:
            self.db.execute("DELETE FROM phonetic_index WHERE term_id = ?", (tid,))
            self.db.execute("DELETE FROM terms WHERE id = ?", (tid,))
        return len(stale)

    def set_enabled(self, term_id: str, enabled: bool) -> bool:
        cur = self.db.execute(
            "UPDATE terms SET enabled = ?, updated_at = ? WHERE id = ?",
            (1 if enabled else 0, _now(), term_id),
        )
        return cur.rowcount > 0

    def delete_term(self, term_id: str) -> bool:
        self.db.execute("DELETE FROM phonetic_index WHERE term_id = ?", (term_id,))
        cur = self.db.execute("DELETE FROM terms WHERE id = ?", (term_id,))
        return cur.rowcount > 0

    def count_terms(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM terms").fetchone()[0])

    # ── corrections ────────────────────────────────────────────────────────────
    def upsert_correction(
        self,
        heard: str,
        meant: str,
        *,
        phonetic_key: str = "",
        auto_apply: bool | None = None,
        threshold: int = 2,
    ) -> Correction:
        now = _now()
        row = self.db.execute(
            "SELECT * FROM corrections WHERE heard = ? AND meant = ?", (heard, meant)
        ).fetchone()
        if row is None:
            cid = f"corr_{abs(hash((heard, meant))) & 0xFFFFFFFF:x}"
            aa = 1 if auto_apply else 0
            self.db.execute(
                """INSERT INTO corrections (id, heard, meant, phonetic_key, count, auto_apply,
                                            last_seen, created_at) VALUES (?,?,?,?,?,?,?,?)""",
                (cid, heard, meant, phonetic_key, 1, aa, now, now),
            )
            return Correction(cid, heard, meant, phonetic_key, 1, bool(aa), now)
        new_count = int(row["count"]) + 1
        # auto_apply becomes true once past threshold, or when explicitly forced.
        new_aa = 1 if (auto_apply or bool(row["auto_apply"]) or new_count >= threshold) else 0
        self.db.execute(
            "UPDATE corrections SET count = ?, auto_apply = ?, last_seen = ? WHERE id = ?",
            (new_count, new_aa, now, row["id"]),
        )
        return Correction(
            row["id"], heard, meant, row["phonetic_key"], new_count, bool(new_aa), now
        )

    def auto_corrections(self) -> dict[str, str]:
        """heard → meant map for corrections flagged auto_apply (case-insensitive heard)."""
        out: dict[str, str] = {}
        for r in self.db.execute("SELECT heard, meant FROM corrections WHERE auto_apply = 1"):
            out[r["heard"].lower()] = r["meant"]
        return out

    def list_corrections(self, limit: int = 500) -> list[Correction]:
        rows = self.db.execute(
            "SELECT * FROM corrections ORDER BY count DESC, last_seen DESC LIMIT ?", (limit,)
        )
        return [
            Correction(
                r["id"],
                r["heard"],
                r["meant"],
                r["phonetic_key"],
                int(r["count"]),
                bool(r["auto_apply"]),
                r["last_seen"],
            )
            for r in rows
        ]

    def set_correction_auto_apply(self, corr_id: str, auto_apply: bool) -> bool:
        cur = self.db.execute(
            "UPDATE corrections SET auto_apply = ? WHERE id = ?",
            (1 if auto_apply else 0, corr_id),
        )
        return cur.rowcount > 0

    def reset(self) -> None:
        """Drop all learned/graph state (the user-facing 'reset' — rebuild repopulates)."""
        self.db.executescript(
            "DELETE FROM phonetic_index; DELETE FROM terms; DELETE FROM corrections;"
        )


def _now() -> str:
    # Wall-clock ISO stamp; time.time is fine here (not in a workflow script sandbox).
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def _row_to_term(r: sqlite3.Row) -> LexiconTerm:
    return LexiconTerm(
        id=r["id"],
        canonical=r["canonical"],
        aliases=json.loads(r["aliases_json"] or "[]"),
        phonetic_keys=json.loads(r["phonetic_keys_json"] or "[]"),
        entity_type=r["entity_type"] or "",
        weight=float(r["weight"] or 0.0),
        source=r["source"] or "graph",
        enabled=bool(r["enabled"]),
    )
