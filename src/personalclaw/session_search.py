"""Cross-session full-text search (SESSION-MANAGEMENT §C1).

Search today is a linear scan: `ConversationLog.search_sessions` reads up to 500
session files per query and counts substrings. It works, and it silently stops
finding things once history outgrows that window. This module is the FTS5 index that
replaces it — one query against a real inverted index, with a highlighted snippet
showing *why* each session matched.

Two rules are load-bearing:

* **Restricted sessions are never indexed.** A temporary or incognito session that
  became searchable would defeat the entire point of the mode. Exclusion happens at
  the index boundary AND is re-checked at read time, because a session can be
  reclassified after its rows were written.
* **The index is disposable.** It holds no truth of its own — every row is derived
  from the JSONL transcripts, so a corrupt or missing database is repaired by
  rebuilding rather than restored. Any failure degrades to the linear scan, which
  is why nothing here raises into a caller.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_FILE = "session_search.db"

# Modes whose transcripts must never enter the index.
_RESTRICTED_MODES = frozenset({"temporary", "incognito"})

# Below this a query matches nearly everything, so it isn't a search.
MIN_QUERY_CHARS = 2

# Per-session indexed-text ceiling. A session is searchable by its content, not
# archivable through the index — the transcript is still the record.
_MAX_SESSION_CHARS = 200_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS indexed (
    session_key TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    mtime REAL NOT NULL DEFAULT 0,
    chars INTEGER NOT NULL DEFAULT 0,
    indexed_at REAL NOT NULL DEFAULT 0
);

CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
    session_key UNINDEXED,
    title,
    body,
    tokenize='porter unicode61'
);
"""

_db: sqlite3.Connection | None = None
_db_path_cache: str = ""


def db_path() -> Path:
    from personalclaw.config.loader import config_dir

    home = Path(os.environ.get("PERSONALCLAW_HOME", config_dir()))
    return home / _DB_FILE


def _connect() -> "sqlite3.Connection | None":
    """The process-wide connection, or None when FTS5 is unavailable.

    A standalone (contentful) FTS5 table rather than the external-content form the
    knowledge store uses, because `snippet()` needs the text in the index and the
    transcripts aren't rows in a SQL table to delegate to.
    """
    global _db, _db_path_cache
    path = str(db_path())
    if _db is not None and _db_path_cache == path:
        return _db
    if _db is not None:
        try:
            _db.close()
        except sqlite3.Error:
            pass
        _db = None
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
        except sqlite3.DatabaseError:
            logger.debug("session_search: pragma setup skipped", exc_info=True)
        conn.executescript(_SCHEMA)
    except sqlite3.OperationalError:
        # No FTS5 in this SQLite build — the caller falls back to the linear scan.
        logger.debug("session_search: FTS5 unavailable", exc_info=True)
        return None
    except Exception:  # noqa: BLE001
        logger.debug("session_search: cannot open index", exc_info=True)
        return None
    _db = conn
    _db_path_cache = path
    return _db


def reset_for_tests() -> None:
    """Drop the cached connection so a test's temp home is honored."""
    global _db, _db_path_cache
    if _db is not None:
        try:
            _db.close()
        except sqlite3.Error:
            pass
    _db = None
    _db_path_cache = ""


# ── restriction gate ───────────────────────────────────────────────────────────


def is_restricted(session_key: str, *, memory_mode: str = "") -> bool:
    """Whether this session must stay out of the index.

    Checks the persisted `memory_mode` when the caller has it, then the live
    restriction registry. Both, because the registry only knows about sessions this
    process has seen, while the metadata survives a restart.
    """
    if memory_mode and memory_mode.strip().lower() in _RESTRICTED_MODES:
        return True
    try:
        from personalclaw import session_restrictions

        return bool(session_restrictions.is_restricted(session_key))
    except Exception:  # noqa: BLE001 — an unavailable registry must not open the gate
        return False


# ── writing ────────────────────────────────────────────────────────────────────


def index_session(
    session_key: str,
    title: str,
    body: str,
    *,
    memory_mode: str = "",
    mtime: float = 0.0,
) -> bool:
    """Replace one session's index entry. Returns whether it was indexed.

    Whole-session replacement rather than per-message append: the transcript is
    rewritten wholesale on every turn (`_save_session_to_history` rewrites the file),
    so incremental rows would drift out of sync with it. One row per session also
    makes `snippet()` return the best-matching passage from the entire conversation.

    ``mtime`` records the SOURCE FILE's timestamp, not the wall clock. Storing
    `time.time()` here made the incremental check compare an index time against a file
    mtime — always newer, so a transcript appended within the same coarse mtime tick was
    skipped forever. Caught by CI, where the filesystem has second-granularity mtimes;
    macOS's finer timestamps hid it.
    """
    key = (session_key or "").strip()
    if not key:
        return False
    if is_restricted(key, memory_mode=memory_mode):
        # Also purge anything indexed before the mode was known/changed.
        forget_session(key)
        return False
    conn = _connect()
    if conn is None:
        return False
    text = (body or "")[:_MAX_SESSION_CHARS]
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM sessions_fts WHERE session_key = ?", (key,))
        conn.execute(
            "INSERT INTO sessions_fts (session_key, title, body) VALUES (?, ?, ?)",
            (key, title or "", text),
        )
        conn.execute(
            "INSERT INTO indexed (session_key, title, mtime, chars, indexed_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(session_key) DO UPDATE SET "
            "title=?, mtime=?, chars=?, indexed_at=?",
            (
                key,
                title or "",
                float(mtime or 0.0),
                len(text),
                time.time(),
                title or "",
                float(mtime or 0.0),
                len(text),
                time.time(),
            ),
        )
        conn.execute("COMMIT")
        return True
    except Exception:  # noqa: BLE001
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        logger.debug("session_search: index write failed for %s", key, exc_info=True)
        return False


def index_turn(session_key: str, role: str, text: str, *, memory_mode: str = "") -> None:
    """Re-index a session after a turn lands (the §C1 hook).

    Signature kept as the plan specifies. The turn's own text is not what's stored —
    the whole transcript is re-read, so the index matches the file rather than an
    accumulation that could diverge from it. Best-effort and never raises: a search
    index must not be able to break a chat.
    """
    key = (session_key or "").strip()
    if not key:
        return
    if is_restricted(key, memory_mode=memory_mode):
        forget_session(key)
        return
    try:
        reindex_session(key)
    except Exception:  # noqa: BLE001
        logger.debug("session_search: index_turn failed for %s", key, exc_info=True)


def forget_session(session_key: str) -> None:
    """Remove a session from the index (deleted, or newly restricted)."""
    key = (session_key or "").strip()
    if not key:
        return
    conn = _connect()
    if conn is None:
        return
    try:
        conn.execute("DELETE FROM sessions_fts WHERE session_key = ?", (key,))
        conn.execute("DELETE FROM indexed WHERE session_key = ?", (key,))
    except Exception:  # noqa: BLE001
        logger.debug("session_search: forget failed for %s", key, exc_info=True)


def _conversation_log():
    from personalclaw.history import ConversationLog

    return ConversationLog()


def reindex_session(session_key: str, log=None) -> bool:
    """Read one session's transcript and refresh its index entry."""
    log = log or _conversation_log()
    key = (session_key or "").strip()
    if not key:
        return False
    try:
        meta = log.get_metadata(key) or {}
    except Exception:  # noqa: BLE001
        meta = {}
    mode = str(meta.get("memory_mode", "") or "")
    if is_restricted(key, memory_mode=mode):
        forget_session(key)
        return False
    try:
        messages = log.read_messages(key) or []
    except Exception:  # noqa: BLE001
        logger.debug("session_search: cannot read %s", key, exc_info=True)
        return False
    body = "\n".join(str(m.get("content", "") or "") for m in messages if m.get("role") != "system")
    title = str(meta.get("title", "") or "")
    return index_session(key, title, body, memory_mode=mode, mtime=_source_mtime(log, key))


def _still_same_size(log, key: str, indexed_chars: int) -> bool:
    """Whether the transcript's indexed body length still matches what's on disk.

    Compares the INDEXED BODY length against a freshly-derived one. That is stricter
    than a file-size check (the file carries metadata and JSON framing the body does
    not), and it is what makes a same-tick append visible. Unknown ⇒ False, so an
    unreadable session is re-indexed rather than silently skipped.
    """
    try:
        messages = log.read_messages(key) or []
    except Exception:  # noqa: BLE001
        return False
    body = "\n".join(str(m.get("content", "") or "") for m in messages if m.get("role") != "system")
    return len(body[:_MAX_SESSION_CHARS]) == int(indexed_chars or 0)


def _source_mtime(log, key: str) -> float:
    """The transcript file's mtime, or 0.0 when it can't be read.

    0.0 is the safe direction: it reads as "older than anything", so the next pass
    re-indexes rather than skipping a session whose freshness is unknown.
    """
    try:
        return float(log._path(key).stat().st_mtime)
    except Exception:  # noqa: BLE001
        return 0.0


def reindex_all(log=None, *, limit: int | None = None, force: bool = False) -> int:
    """Index every session that needs it. Returns how many were (re)indexed.

    Incremental by mtime so the heartbeat can call this repeatedly for pennies, and
    it prunes sessions that have been deleted or turned restricted since last time.
    """
    log = log or _conversation_log()
    conn = _connect()
    if conn is None:
        return 0
    try:
        sessions = log.list_sessions() or []
    except Exception:  # noqa: BLE001
        logger.debug("session_search: cannot list sessions", exc_info=True)
        return 0

    try:
        known = {
            row["session_key"]: (row["mtime"], row["chars"])
            for row in conn.execute("SELECT session_key, mtime, chars FROM indexed").fetchall()
        }
    except Exception:  # noqa: BLE001
        known = {}

    indexed = 0
    live: set[str] = set()
    for entry in sessions:
        key = str(entry.get("key", "") or "")
        if not key:
            continue
        mode = str(entry.get("memory_mode", "") or "")
        if is_restricted(key, memory_mode=mode):
            forget_session(key)
            continue
        live.add(key)
        modified = float(entry.get("modified", 0) or 0)
        # Skip only when the file has NOT advanced past what we indexed. mtime alone
        # is not enough: on a coarse-granularity filesystem an append can land inside
        # the same tick, leaving the timestamps equal while the content grew — which is
        # why the file's byte size is part of the comparison.
        if not force and key in known:
            indexed_mtime, indexed_chars = known[key]
            if float(indexed_mtime or 0) >= modified and _still_same_size(log, key, indexed_chars):
                continue
        if reindex_session(key, log=log):
            indexed += 1
        if limit is not None and indexed >= limit:
            break

    # Prune entries whose session no longer exists — but only on a complete pass,
    # since a limited one hasn't seen every session and would delete live rows.
    if limit is None:
        for key in set(known) - live:
            forget_session(key)
    return indexed


# ── reading ────────────────────────────────────────────────────────────────────

_FTS_TOKEN = re.compile(r"[0-9A-Za-z_]+")


def _fts_query(raw: str) -> str:
    """Turn user text into a safe FTS5 MATCH expression.

    Every token is quoted (so `AND`, `*`, `"` and friends can't be read as syntax)
    and the last one gets a prefix wildcard, which is what makes search feel live
    while the user is still typing.
    """
    tokens = _FTS_TOKEN.findall(raw or "")
    if not tokens:
        return ""
    quoted = [f'"{t}"' for t in tokens[:-1]]
    quoted.append(f'"{tokens[-1]}"*')
    return " ".join(quoted)


def search_sessions(query: str, *, limit: int = 30, folder: str | None = None) -> list[dict]:
    """Ranked sessions matching ``query``, each with a highlighted snippet.

    Returns ``[]`` — never raises — when the index is unavailable or the query is
    too short, so a caller can treat an empty result as "fall back to the scan".
    """
    text = (query or "").strip()
    if len(text) < MIN_QUERY_CHARS:
        return []
    conn = _connect()
    if conn is None:
        return []
    match = _fts_query(text)
    if not match:
        return []
    try:
        rows = conn.execute(
            "SELECT session_key, title, "
            "       snippet(sessions_fts, 2, '<<', '>>', '…', 24) AS snippet, "
            "       rank "
            "FROM sessions_fts WHERE sessions_fts MATCH ? ORDER BY rank LIMIT ?",
            (match, max(1, min(int(limit or 30), 200))),
        ).fetchall()
    except sqlite3.OperationalError:
        # A malformed MATCH or a missing index: the caller falls back.
        logger.debug("session_search: query failed for %r", text, exc_info=True)
        return []
    except Exception:  # noqa: BLE001
        logger.debug("session_search: query error", exc_info=True)
        return []

    out: list[dict] = []
    for row in rows:
        key = row["session_key"]
        # Re-check at READ time: a session may have been reclassified since its rows
        # were written, and search must honor the mode as it is now.
        if is_restricted(key):
            continue
        out.append(
            {
                "session_key": key,
                "key": key,  # the existing endpoint contract uses `key`
                "title": row["title"] or key,
                "snippet": row["snippet"] or "",
                "rank": float(row["rank"] or 0.0),
            }
        )
    return out


def stats() -> dict:
    """Index size + availability, for diagnostics."""
    conn = _connect()
    if conn is None:
        return {"available": False, "sessions": 0}
    try:
        count = int(conn.execute("SELECT COUNT(*) FROM indexed").fetchone()[0])
        chars = int(conn.execute("SELECT COALESCE(SUM(chars), 0) FROM indexed").fetchone()[0])
    except Exception:  # noqa: BLE001
        return {"available": False, "sessions": 0}
    return {
        "available": True,
        "sessions": count,
        "indexed_chars": chars,
        "db_path": str(db_path()),
    }
