"""Cross-session full-text search.

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
import re
import time
from pathlib import Path

from personalclaw.sqlite_compat import FTS5_REMEDY, probe, sqlite3

logger = logging.getLogger(__name__)

_DB_FILE = "session_search.db"

# Modes whose transcripts must never enter the index.
_RESTRICTED_MODES = frozenset({"temporary", "incognito"})

# Below this a query matches nearly everything, so it isn't a search.
MIN_QUERY_CHARS = 2

# Per-session indexed-text ceiling. A session is searchable by its content, not
# archivable through the index — the transcript is still the record.
_MAX_SESSION_CHARS = 200_000

#: 🔑 A session's FTS row has the ROWID of its ``indexed`` row. ``session_key`` is an UNINDEXED
#: column of an FTS5 table, so finding a session's row by its key reads EVERY row: replacing one
#: session's entry cost 1.6 ms at 1,000 indexed sessions and 17 ms at 6,000 (measured), and an
#: import of a months-long history — thousands of conversations, each indexed as it lands — spent
#: half its time there. By rowid it is one lookup, whatever the index holds.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS indexed (
    session_key TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    mtime REAL NOT NULL DEFAULT 0,
    size INTEGER NOT NULL DEFAULT -1,
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

#: ``PRAGMA user_version`` of an index whose FTS rows share their ``indexed`` rows' rowids, and
#: whose ``indexed`` rows record each transcript's size. An index built before (version 0) paired
#: them by key alone and kept no size, so its rowids say nothing: it is dropped and rebuilt from
#: the transcripts, which is how this disposable index is repaired.
_SCHEMA_VERSION = 1

_db: sqlite3.Connection | None = None
_db_path_cache: str = ""
_fts_unavailable_logged: bool = False


def db_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _DB_FILE


def _connect() -> "sqlite3.Connection | None":
    """The process-wide connection, or None when FTS5 is unavailable.

    A standalone (contentful) FTS5 table rather than the external-content form the
    knowledge store uses, because `snippet()` needs the text in the index and the
    transcripts aren't rows in a SQL table to delegate to.
    """
    global _db, _db_path_cache, _fts_unavailable_logged
    # DEGRADE (not raise): the whole module is a disposable index whose failure mode is
    # "fall back to the linear scan" (every reader treats None / [] that way). Decide the
    # FTS5 question ONCE via the probe, before opening a connection — so a build without
    # FTS5 skips the index cleanly and logs the remedy a single time, rather than each
    # _connect() re-attempting the CREATE VIRTUAL TABLE and swallowing the error per call.
    if not probe().fts5:
        if not _fts_unavailable_logged:
            logger.warning("Session full-text search disabled. %s", FTS5_REMEDY)
            _fts_unavailable_logged = True
        return None
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
        if int(conn.execute("PRAGMA user_version").fetchone()[0]) < _SCHEMA_VERSION:
            # Rebuilt by the next `reindex_all`, which finds every session unindexed.
            conn.executescript("DROP TABLE IF EXISTS sessions_fts; DROP TABLE IF EXISTS indexed;")
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        conn.executescript(_SCHEMA)
    except sqlite3.OperationalError:
        # FTS5 presence was already settled by the probe above; a failure here is an
        # unwritable/locked index. The caller falls back to the linear scan.
        logger.debug("session_search: cannot open index", exc_info=True)
        return None
    except Exception:  # noqa: BLE001
        logger.debug("session_search: cannot open index", exc_info=True)
        return None
    _db = conn
    _db_path_cache = path
    return _db


def reset_for_tests() -> None:
    """Drop the cached connection so a test's temp home is honored."""
    global _db, _db_path_cache, _fts_unavailable_logged
    if _db is not None:
        try:
            _db.close()
        except sqlite3.Error:
            pass
    _db = None
    _db_path_cache = ""
    _fts_unavailable_logged = False


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
    size: int = -1,
) -> bool:
    """Replace one session's index entry. Returns whether it was indexed.

    Whole-session replacement rather than per-message append: the transcript is
    rewritten wholesale on every turn (`save_session_to_history` rewrites the file),
    so incremental rows would drift out of sync with it. One row per session also
    makes `snippet()` return the best-matching passage from the entire conversation.

    ``mtime`` records the SOURCE FILE's timestamp, not the wall clock. Storing
    `time.time()` here made the incremental check compare an index time against a file
    mtime — always newer, so a transcript appended within the same coarse mtime tick was
    skipped forever. Caught by CI, where the filesystem has second-granularity mtimes;
    macOS's finer timestamps hid it. ``size`` is the source file's byte size, which a pass
    compares with the file's (``-1``, unknown, never matches one).
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
        # The bookkeeping row first: an upsert keeps an existing row's rowid, and a new row's is
        # the one its FTS row takes (see `_SCHEMA`).
        conn.execute(
            "INSERT INTO indexed (session_key, title, mtime, size, chars, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(session_key) DO UPDATE SET "
            "title=?, mtime=?, size=?, chars=?, indexed_at=?",
            (
                key,
                title or "",
                float(mtime or 0.0),
                int(size),
                len(text),
                time.time(),
                title or "",
                float(mtime or 0.0),
                int(size),
                len(text),
                time.time(),
            ),
        )
        rowid = conn.execute("SELECT rowid FROM indexed WHERE session_key = ?", (key,)).fetchone()[
            0
        ]
        conn.execute("DELETE FROM sessions_fts WHERE rowid = ?", (rowid,))
        conn.execute(
            "INSERT INTO sessions_fts (rowid, session_key, title, body) VALUES (?, ?, ?, ?)",
            (rowid, key, title or "", text),
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


def index_turn(session_key: str, role: str, text: str, *, memory_mode: str = "", log=None) -> None:
    """Re-index a session after a turn lands (the §C1 hook).

    Signature kept as the plan specifies. The turn's own text is not what's stored —
    the whole transcript is re-read, so the index matches the file rather than an
    accumulation that could diverge from it. Best-effort and never raises: a search
    index must not be able to break a chat.

    ``log`` is the store the transcript was just written to, and it is re-read from THAT
    store. A log rooted outside the home (an explicit ``base_dir``) is not indexed at all:
    this index is one home's, and the row would describe a transcript the home does not have.
    Re-reading such a session through the home's own store instead found nothing, so the row
    was written EMPTY — measured: three ``chars=0`` rows in a developer's real index, from
    scripts that kept their ``ConversationLog`` in a temp dir but never set
    ``PERSONALCLAW_HOME``, so the index alone escaped the isolation they had chosen.
    """
    key = (session_key or "").strip()
    if not key:
        return
    if log is not None and not log.is_home_log():
        return
    if is_restricted(key, memory_mode=memory_mode):
        forget_session(key)
        return
    try:
        reindex_session(key, log=log)
    except Exception:  # noqa: BLE001
        logger.debug("session_search: index_turn failed for %s", key, exc_info=True)


def forget_session(session_key: str) -> None:
    """Remove a session from the index (deleted, or newly restricted).

    Both tables are dropped in one transaction so a failure cannot leave a
    searchable FTS row whose bookkeeping row is gone (or vice versa). A session with a
    bookkeeping row loses its FTS row by that row's rowid; one without — an FTS row
    left by drift, or a key never indexed — is looked for by key, which reads the table.
    """
    key = (session_key or "").strip()
    if not key:
        return
    conn = _connect()
    if conn is None:
        return
    try:
        conn.execute("BEGIN")
        row = conn.execute("SELECT rowid FROM indexed WHERE session_key = ?", (key,)).fetchone()
        if row is not None:
            conn.execute("DELETE FROM sessions_fts WHERE rowid = ?", (row[0],))
        else:
            conn.execute("DELETE FROM sessions_fts WHERE session_key = ?", (key,))
        conn.execute("DELETE FROM indexed WHERE session_key = ?", (key,))
        conn.execute("COMMIT")
    except Exception:  # noqa: BLE001
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        logger.debug("session_search: forget failed for %s", key, exc_info=True)


def purge_orphans(log=None) -> int:
    """Backfill (SM-11): drop index rows whose transcript no longer exists.

    Enumerates distinct session keys from BOTH tables — the union matters:
    a row present only in ``sessions_fts`` (drift from a partial write under
    the old non-transactional forget) is invisible to any prune that reads
    ``indexed`` alone, yet it is exactly the row that keeps deleted text
    searchable. Returns how many orphaned sessions were purged, and logs the
    count so the sweep's effect is stated, not silent.
    """
    log = log or _conversation_log()
    conn = _connect()
    if conn is None:
        return 0
    try:
        keys = {
            row["session_key"]
            for row in conn.execute("SELECT DISTINCT session_key FROM sessions_fts").fetchall()
        } | {
            row["session_key"]
            for row in conn.execute("SELECT DISTINCT session_key FROM indexed").fetchall()
        }
    except Exception:  # noqa: BLE001
        logger.debug("session_search: purge_orphans enumeration failed", exc_info=True)
        return 0
    purged = 0
    for key in keys:
        try:
            exists = bool(log.has_log(key))
        except Exception:  # noqa: BLE001
            continue  # unknown ⇒ keep: never purge a row we cannot verify
        if not exists:
            forget_session(key)
            purged += 1
    if purged:
        logger.info("session_search: purged %d orphaned index row(s)", purged)
    return purged


def _conversation_log():
    from personalclaw.history import ConversationLog

    return ConversationLog()


def reindex_session(session_key: str, log=None) -> bool:
    """Read one session's transcript and refresh its index entry.

    The file's time and size are taken BEFORE it is read, so a write landing during the read
    leaves an entry older than its file, which the next pass reads again — never one that
    records the new file's stamp over the old text.
    """
    log = log or _conversation_log()
    key = (session_key or "").strip()
    if not key:
        return False
    mtime, size = _source_stamp(log, key)
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
    return index_session(key, title, body, memory_mode=mode, mtime=mtime, size=size)


def _source_stamp(log, key: str) -> tuple[float, int]:
    """The transcript file's ``(mtime, size)``, or ``(0.0, -1)`` when it can't be read.

    That is the safe direction: it reads as "older than anything, and of a size no file has",
    so the next pass re-indexes rather than skipping a session whose freshness is unknown.
    """
    try:
        stat = log._path(key).stat()
    except Exception:  # noqa: BLE001
        return 0.0, -1
    return float(stat.st_mtime), int(stat.st_size)


def reindex_all(log=None, *, limit: int | None = None, force: bool = False) -> int:
    """Index every session that needs it. Returns how many were (re)indexed.

    Incremental by each file's time and size, so the heartbeat can call this repeatedly for
    pennies — a pass reads only the transcripts that changed — and it prunes sessions that
    have been deleted or turned restricted since last time.
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
            row["session_key"]: (row["mtime"], row["size"])
            for row in conn.execute("SELECT session_key, mtime, size FROM indexed").fetchall()
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
        # Skip only when the file has NOT changed since it was indexed. mtime alone is not
        # enough: on a coarse-granularity filesystem an append can land inside the same tick,
        # leaving the timestamps equal while the file grew — which is why its size is part of
        # the comparison. The size is the file's, from one stat: comparing the indexed text's
        # length instead re-read every transcript on every pass, which for 12,005 imported
        # conversations was 7.4 s and 883 MB every five minutes (measured).
        if not force and key in known:
            indexed_mtime, indexed_size = known[key]
            if (
                float(indexed_mtime or 0) >= modified
                and int(indexed_size) == _source_stamp(log, key)[1]
            ):
                continue
        if reindex_session(key, log=log):
            indexed += 1
        if limit is not None and indexed >= limit:
            break

    # Prune entries whose session no longer exists — but only on a complete pass,
    # since a limited one hasn't seen every session and would delete live rows.
    # purge_orphans reads the union of both tables, so an FTS-only drift row
    # (invisible to `known`, which reads only `indexed`) is swept too.
    if limit is None:
        for key in set(known) - live:
            forget_session(key)
        purge_orphans(log)
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
