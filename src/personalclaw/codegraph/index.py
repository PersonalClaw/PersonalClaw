"""The SQLite symbol index (CONTEXT-ECONOMY §5.5).

One database per workspace at ``<home>/codegraph/<workspace-key>.db``, holding
definitions, references and import edges. Files are re-parsed only when their mtime
or size changes, so a second index pass over an unchanged tree costs a stat per file.

Two budgets keep a pathological repo from becoming a hang: a wall-clock cap on the
whole pass and a file-count cap. Exceeding either stops indexing and marks the
result partial — a partial index still answers most queries, and the alternative
(blocking a code session for a minute) is worse than an incomplete accelerator.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from personalclaw.codegraph.parse import language_for, parse_source

logger = logging.getLogger(__name__)

# The plan's ~30s budget for a full rebuild. Fail-soft: whatever is indexed stays.
DEFAULT_BUDGET_SECS = 30.0
DEFAULT_MAX_FILES = 20_000

_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "target",
        ".next",
        ".nuxt",
        "coverage",
        ".tox",
        "site-packages",
        ".idea",
        ".vscode",
        ".cache",
        "vendor",
    }
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,          -- workspace-relative, POSIX separators
    language TEXT NOT NULL,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    signature TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_def_name ON definitions(name);
CREATE INDEX IF NOT EXISTS idx_def_path ON definitions(path);

CREATE TABLE IF NOT EXISTS refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    line INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ref_name ON refs(name);
CREATE INDEX IF NOT EXISTS idx_ref_path ON refs(path);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    statement TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_import_path ON imports(path);
"""


@dataclass
class IndexStats:
    """What one indexing pass did — the honest report, including what it skipped."""

    files_indexed: int = 0
    files_skipped_unchanged: int = 0
    files_removed: int = 0
    definitions: int = 0
    references: int = 0
    duration_secs: float = 0.0
    partial: bool = False
    reason: str = ""
    languages: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "files_indexed": self.files_indexed,
            "files_skipped_unchanged": self.files_skipped_unchanged,
            "files_removed": self.files_removed,
            "definitions": self.definitions,
            "references": self.references,
            "duration_secs": round(self.duration_secs, 3),
            "partial": self.partial,
            "reason": self.reason,
            "languages": dict(self.languages),
        }


def workspace_key(workspace: str) -> str:
    """A stable, fixed-length key for a workspace path.

    Follows `loop/worktree.py`'s precedent (sha1 of the absolute path, 12 hex chars)
    rather than the readable-slug style, because this is a filename that only the
    code resolves — nobody needs to read it, and a hash can't collide with a path
    separator or blow the name-length limit.
    """
    real = os.path.abspath(os.path.expanduser(str(workspace or "")))
    return hashlib.sha1(real.encode("utf-8")).hexdigest()[:12]


def default_db_path(workspace: str) -> Path:
    from personalclaw.config.loader import config_dir

    root = Path(os.environ.get("PERSONALCLAW_HOME", config_dir())) / "codegraph"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{workspace_key(workspace)}.db"


class CodeGraphIndex:
    """Read/write access to one workspace's symbol index.

    Connection conventions follow the knowledge/lexicon stores (WAL, busy_timeout,
    Row factory, cross-thread) so this behaves like every other store here.
    """

    def __init__(self, workspace: str, db_path: "Path | None" = None) -> None:
        self.workspace = os.path.abspath(os.path.expanduser(str(workspace or "")))
        self._db_path = Path(db_path) if db_path else default_db_path(self.workspace)
        self._db: sqlite3.Connection | None = None

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self._db_path), timeout=30, isolation_level=None, check_same_thread=False
            )
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=10000")
            except sqlite3.DatabaseError:
                logger.debug("codegraph: pragma setup skipped", exc_info=True)
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('workspace', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = ?",
                (self.workspace, self.workspace),
            )
            self._db = conn
        return self._db

    def close(self) -> None:
        if self._db is not None:
            try:
                self._db.close()
            finally:
                self._db = None

    # ── indexing ──

    def _candidate_files(self) -> "list[tuple[str, str, float, int]]":
        """Every indexable file as ``(relpath, language, mtime, size)``."""
        out: list[tuple[str, str, float, int]] = []
        root = Path(self.workspace)
        if not root.is_dir():
            return out
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune in place so os.walk never descends into the noise.
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for filename in filenames:
                language = language_for(filename)
                if not language:
                    continue
                full = Path(dirpath) / filename
                try:
                    stat = full.stat()
                except OSError:
                    continue
                try:
                    rel = full.relative_to(root).as_posix()
                except ValueError:
                    continue
                out.append((rel, language, stat.st_mtime, stat.st_size))
        out.sort()
        return out

    def index(
        self,
        *,
        budget_secs: float = DEFAULT_BUDGET_SECS,
        max_files: int = DEFAULT_MAX_FILES,
        full: bool = False,
    ) -> IndexStats:
        """Index the workspace incrementally. Never raises.

        ``full=True`` re-parses everything; otherwise a file whose mtime and size
        both match the stored row is skipped without being read.
        """
        started = time.monotonic()
        stats = IndexStats()
        try:
            candidates = self._candidate_files()
        except Exception:  # noqa: BLE001
            logger.debug("codegraph: walk failed", exc_info=True)
            stats.reason = "workspace could not be scanned"
            stats.duration_secs = time.monotonic() - started
            return stats

        if len(candidates) > max_files:
            stats.partial = True
            stats.reason = f"file cap reached ({max_files} of {len(candidates)} files)"
            candidates = candidates[:max_files]

        known = {
            row["path"]: (row["mtime"], row["size"])
            for row in self.db.execute("SELECT path, mtime, size FROM files").fetchall()
        }
        seen: set[str] = set()

        for rel, language, mtime, size in candidates:
            seen.add(rel)
            if time.monotonic() - started > budget_secs:
                stats.partial = True
                stats.reason = f"time budget reached after {stats.files_indexed} files"
                break
            previous = known.get(rel)
            if not full and previous is not None and previous == (mtime, size):
                stats.files_skipped_unchanged += 1
                continue
            try:
                source = (Path(self.workspace) / rel).read_bytes()
            except OSError:
                continue
            result = parse_source(rel, source)
            self._replace_file(rel, language, mtime, size, result)
            stats.files_indexed += 1
            stats.definitions += len(result.definitions)
            stats.references += len(result.references)
            stats.languages[language] = stats.languages.get(language, 0) + 1

        # Drop files that no longer exist — but only on a complete pass, since a
        # truncated one hasn't seen the whole tree and would delete live rows.
        if not stats.partial:
            gone = [path for path in known if path not in seen]
            for path in gone:
                self._forget_file(path)
            stats.files_removed = len(gone)

        self.db.execute(
            "INSERT INTO meta (key, value) VALUES ('indexed_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = ?",
            (_now(), _now()),
        )
        stats.duration_secs = time.monotonic() - started
        logger.info(
            "codegraph: indexed %d file(s) (%d unchanged) in %.2fs%s",
            stats.files_indexed,
            stats.files_skipped_unchanged,
            stats.duration_secs,
            f" — PARTIAL: {stats.reason}" if stats.partial else "",
        )
        return stats

    def _replace_file(self, rel: str, language: str, mtime: float, size: int, result) -> None:
        """Swap one file's rows atomically — delete-then-insert in one transaction."""
        db = self.db
        try:
            db.execute("BEGIN")
            self._delete_rows(rel)
            db.execute(
                "INSERT INTO files (path, language, mtime, size, indexed_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET "
                "language=?, mtime=?, size=?, indexed_at=?",
                (rel, language, mtime, size, _now(), language, mtime, size, _now()),
            )
            if result.definitions:
                db.executemany(
                    "INSERT INTO definitions (path, name, kind, owner, line, end_line, "
                    "signature) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (rel, d.name, d.kind, d.owner, d.line, d.end_line, d.signature)
                        for d in result.definitions
                    ],
                )
            if result.references:
                db.executemany(
                    "INSERT INTO refs (path, name, line) VALUES (?, ?, ?)",
                    [(rel, r.name, r.line) for r in result.references],
                )
            if result.imports:
                db.executemany(
                    "INSERT INTO imports (path, statement) VALUES (?, ?)",
                    [(rel, statement) for statement in result.imports],
                )
            db.execute("COMMIT")
        except Exception:  # noqa: BLE001
            try:
                db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            logger.debug("codegraph: could not store %s", rel, exc_info=True)

    def _delete_rows(self, rel: str) -> None:
        for table in ("definitions", "refs", "imports"):
            self.db.execute(f"DELETE FROM {table} WHERE path = ?", (rel,))  # noqa: S608

    def _forget_file(self, rel: str) -> None:
        self._delete_rows(rel)
        self.db.execute("DELETE FROM files WHERE path = ?", (rel,))

    # ── queries ──

    def is_empty(self) -> bool:
        return not self.db.execute("SELECT 1 FROM files LIMIT 1").fetchone()

    def definitions_of(self, name: str, *, limit: int = 25) -> list[dict]:
        """Definitions matching ``name`` — exact first, then suffix/prefix matches.

        Exact-before-fuzzy matters: asking for `parse` should lead with the function
        actually called `parse`, not `parse_source_recursively`.
        """
        exact = self.db.execute(
            "SELECT * FROM definitions WHERE name = ? ORDER BY path, line LIMIT ?",
            (name, limit),
        ).fetchall()
        rows = [dict(r) for r in exact]
        if len(rows) < limit:
            fuzzy = self.db.execute(
                "SELECT * FROM definitions WHERE name LIKE ? AND name != ? "
                "ORDER BY LENGTH(name), path, line LIMIT ?",
                (f"%{name}%", name, limit - len(rows)),
            ).fetchall()
            rows.extend(dict(r) for r in fuzzy)
        return rows

    def references_to(self, name: str, *, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT path, name, line FROM refs WHERE name = ? ORDER BY path, line LIMIT ?",
            (name, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def file_outline(self, rel: str) -> dict:
        """Everything known about one file: imports + definitions in line order."""
        row = self.db.execute("SELECT * FROM files WHERE path = ?", (rel,)).fetchone()
        if row is None:
            # Accept a suffix match so a caller can pass an absolute or partial path.
            row = self.db.execute(
                "SELECT * FROM files WHERE path LIKE ? ORDER BY LENGTH(path) LIMIT 1",
                (f"%{rel}",),
            ).fetchone()
        if row is None:
            return {}
        path = row["path"]
        return {
            "path": path,
            "language": row["language"],
            "imports": [
                r["statement"]
                for r in self.db.execute(
                    "SELECT statement FROM imports WHERE path = ? ORDER BY id", (path,)
                ).fetchall()
            ],
            "definitions": [
                dict(r)
                for r in self.db.execute(
                    "SELECT * FROM definitions WHERE path = ? ORDER BY line", (path,)
                ).fetchall()
            ],
        }

    def centrality(self, *, limit: int = 200) -> dict:
        """Per-file inbound-reference counts — how much of the tree points here.

        Used to rank the `@`-mention picker and the planning summary: a file whose
        symbols everything else calls is more likely what the user meant than an
        equally-named leaf.

        Two corrections make this measure something rather than nothing, both found
        by running it on a real repo:

        * **Count referring FILES, not raw mentions.** Raw counts multiply a name's
          ambiguity by its popularity, so a file defining a generic `name` method
          scored higher than the actual hubs.
        * **Weight each name by how uniquely it identifies a file.** A name defined
          in forty files says almost nothing about any one of them; a name defined
          once is strong evidence. Dividing by the definition count turns
          "everything defines `name`" from the loudest signal into a whisper.
        """
        rows = self.db.execute(
            "WITH def_spread AS ("
            "  SELECT name, COUNT(DISTINCT path) AS files FROM definitions GROUP BY name"
            ") "
            "SELECT d.path AS path, "
            "       SUM(1.0 / ds.files) AS score, "
            "       COUNT(DISTINCT r.path) AS referrers "
            "FROM definitions d "
            "JOIN def_spread ds ON ds.name = d.name "
            "JOIN refs r ON r.name = d.name AND r.path != d.path "
            "GROUP BY d.path ORDER BY score DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return {r["path"]: int(r["referrers"]) for r in rows}

    def module_summary(self, *, max_files: int = 40, max_defs_per_file: int = 8) -> str:
        """A bounded package-layout + public-API sketch for planning context.

        Ranked by centrality so the budget is spent on the files that matter, and
        underscore-prefixed names are dropped — the point is the surface a newcomer
        would need, not an inventory.
        """
        if self.is_empty():
            return ""
        ranked = self.centrality(limit=max_files)
        paths = list(ranked)
        if len(paths) < max_files:
            extra = self.db.execute(
                "SELECT path, COUNT(*) AS n FROM definitions GROUP BY path "
                "ORDER BY n DESC LIMIT ?",
                (max_files,),
            ).fetchall()
            for row in extra:
                if row["path"] not in ranked and len(paths) < max_files:
                    paths.append(row["path"])
        if not paths:
            return ""
        lines = ["[code map: most-referenced modules and their public surface]"]
        for path in paths:
            defs = self.db.execute(
                "SELECT name, kind, owner, line FROM definitions WHERE path = ? "
                "AND name NOT LIKE '\\_%' ESCAPE '\\' ORDER BY line LIMIT ?",
                (path, max_defs_per_file),
            ).fetchall()
            if not defs:
                continue
            hits = ranked.get(path)
            suffix = f"  ({hits} inbound refs)" if hits else ""
            lines.append(f"\n{path}{suffix}")
            for d in defs:
                owner = f"{d['owner']}." if d["owner"] else ""
                lines.append(f"  {d['kind']} {owner}{d['name']}  # line {d['line']}")
        return "\n".join(lines)

    def stats(self) -> dict:
        one = lambda sql: int(self.db.execute(sql).fetchone()[0])  # noqa: E731
        indexed_at = self.db.execute("SELECT value FROM meta WHERE key = 'indexed_at'").fetchone()
        languages = {
            r["language"]: int(r["n"])
            for r in self.db.execute(
                "SELECT language, COUNT(*) AS n FROM files GROUP BY language"
            ).fetchall()
        }
        return {
            "workspace": self.workspace,
            "db_path": str(self._db_path),
            "files": one("SELECT COUNT(*) FROM files"),
            "definitions": one("SELECT COUNT(*) FROM definitions"),
            "references": one("SELECT COUNT(*) FROM refs"),
            "languages": languages,
            "indexed_at": indexed_at["value"] if indexed_at else "",
        }


def index_workspace(
    workspace: str,
    *,
    budget_secs: float = DEFAULT_BUDGET_SECS,
    full: bool = False,
) -> "tuple[CodeGraphIndex | None, IndexStats]":
    """Open (creating if needed) and refresh a workspace's index. Never raises.

    Returns ``(None, stats)`` when no index could be built at all — the caller then
    falls back to grep/read, which is the designed behavior, not an error path.
    """
    try:
        index = CodeGraphIndex(workspace)
        stats = index.index(budget_secs=budget_secs, full=full)
        return index, stats
    except Exception:  # noqa: BLE001
        logger.debug("codegraph: indexing unavailable for %s", workspace, exc_info=True)
        return None, IndexStats(reason="index unavailable")


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).isoformat()
