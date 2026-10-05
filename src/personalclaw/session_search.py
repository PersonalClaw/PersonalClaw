"""Cross-session full-text search.

Search today is a linear scan: `ConversationLog.search_sessions` reads up to 500
session files per query and counts substrings. It works, and it silently stops
finding things once history outgrows that window. This module is the FTS5 index that
replaces it — one query against a real inverted index, with a highlighted snippet
showing *why* each session matched.

Three rules are load-bearing:

* **Restricted sessions are never indexed.** A temporary or incognito session that
  became searchable would defeat the entire point of the mode. Exclusion happens at
  the index boundary AND is re-checked at read time, because a session can be
  reclassified after its rows were written.
* **The index is disposable.** It holds no truth of its own — every row is derived
  from the JSONL transcripts, so a corrupt or missing database is repaired by
  rebuilding rather than restored. Any failure degrades to the linear scan, which
  is why nothing here raises into a caller.
* **A chat is one entry, under its transcript's file name** (:func:`_file_key`, the key the
  chat list uses), whichever spelling of its key a caller hands in: ``dashboard:chat-7`` and
  ``dashboard_chat-7`` are one chat. A chat's save and the indexer each kept their own
  spelling, so one chat answered a search twice, a forget under one spelling left the other
  findable, and every check read the chat again (:func:`_key_by_file`).

And one rule about what a search SAYS: **a partial answer never reads as a complete one.**
The index is kept caught up in the background (:class:`SessionIndexer`), and until it has
caught up with every chat — after a rebuild, or a history arriving in bulk — some chats are
not in it; and it holds only the beginning of a chat longer than it keeps, which a search
reads directly within a bound (:data:`LONG_READ_BYTES`). :func:`search` therefore answers
with how many chats it looked in whole, of how many, and reads the rest directly when asked to.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personalclaw.sqlite_compat import FTS5_REMEDY, connect, probe, sqlite3

logger = logging.getLogger(__name__)

_DB_FILE = "session_search.db"

# Below this a query matches nearly everything, so it isn't a search.
MIN_QUERY_CHARS = 2

# Per-session indexed-text ceiling. A session is searchable by its content, not
# archivable through the index — the transcript is still the record. A longer session is
# indexed as far as this; a search reads it directly within `LONG_READ_BYTES`, and past that
# says it looked in only its beginning (`search`).
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
#: Every use of the one connection, from any thread. The background indexer writes while a
#: search reads and a chat's save indexes its turn; a transaction on a shared connection must
#: not have another thread's statements land inside it.
_LOCK = threading.RLock()


def db_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _DB_FILE


def _connect() -> "sqlite3.Connection | None":
    """The process-wide connection, or None when FTS5 is unavailable.

    A standalone (contentful) FTS5 table rather than the external-content form the
    knowledge store uses, because `snippet()` needs the text in the index and the
    transcripts aren't rows in a SQL table to delegate to.
    """
    with _LOCK:
        return _open()


def _open() -> "sqlite3.Connection | None":
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
        conn = connect(path, timeout=15, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
        except sqlite3.DatabaseError:
            logger.debug("session_search: pragma setup skipped", exc_info=True)
        if int(conn.execute("PRAGMA user_version").fetchone()[0]) < _SCHEMA_VERSION:
            # Rebuilt by the indexer, which finds every session unindexed (`SessionIndexer`).
            conn.executescript("DROP TABLE IF EXISTS sessions_fts; DROP TABLE IF EXISTS indexed;")
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        conn.executescript(_SCHEMA)
        _key_by_file(conn)
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


def _key_by_file(conn) -> int:
    """Backfill: every entry under its transcript's file name (:func:`_file_key`).

    Before, a chat's save indexed it under the key it was handed (``dashboard:chat-7``) and the
    indexer under the file's name (``dashboard_chat-7``). An entry under another spelling moves
    to the file's name, keeping its text and stamp, so the indexer has nothing to read again;
    where the file's name already has an entry, or the entry's text row is missing, it is
    dropped, and the indexer reads the chat in again if the name has none. A text row with no
    entry, under another spelling, is drift from an older forget and is dropped too. Keyed on
    the data, so it runs whenever the index is opened and changes nothing the second time.
    Returns how many rows it moved or dropped.
    """
    changed = 0
    try:
        entries = conn.execute("SELECT rowid, session_key FROM indexed").fetchall()
        for rowid, key in entries:
            name = _file_key(key)
            if name == key:
                continue
            conn.execute("BEGIN")
            try:
                taken = conn.execute(
                    "SELECT 1 FROM indexed WHERE session_key = ?", (name,)
                ).fetchone()
                paired = conn.execute(
                    "SELECT 1 FROM sessions_fts WHERE rowid = ?", (rowid,)
                ).fetchone()
                if taken is None and paired is not None:
                    conn.execute(
                        "UPDATE indexed SET session_key = ? WHERE rowid = ?", (name, rowid)
                    )
                    conn.execute(
                        "UPDATE sessions_fts SET session_key = ? WHERE rowid = ?", (name, rowid)
                    )
                else:
                    conn.execute("DELETE FROM sessions_fts WHERE rowid = ?", (rowid,))
                    conn.execute("DELETE FROM indexed WHERE rowid = ?", (rowid,))
                conn.execute("COMMIT")
                changed += 1
            except sqlite3.Error:
                conn.execute("ROLLBACK")
                raise
        texts = conn.execute("SELECT rowid, session_key FROM sessions_fts").fetchall()
        drift = [rowid for rowid, key in texts if _file_key(key) != key]
        if drift:
            conn.execute("BEGIN")
            try:
                for rowid in drift:
                    conn.execute("DELETE FROM sessions_fts WHERE rowid = ?", (rowid,))
                    conn.execute("DELETE FROM indexed WHERE rowid = ?", (rowid,))
                conn.execute("COMMIT")
            except sqlite3.Error:
                conn.execute("ROLLBACK")
                raise
            changed += len(drift)
    except sqlite3.Error:
        # What it did not finish, the next open does: the index works either way, and the
        # indexer reads again a chat whose entry it cannot find.
        logger.debug("session_search: keying entries by file name stopped", exc_info=True)
    if changed:
        logger.info("session_search: %d index row(s) moved to their transcript's name", changed)
    return changed


def reset_for_tests() -> None:
    """Drop the cached connection so a test's temp home is honored."""
    global _db, _db_path_cache, _fts_unavailable_logged
    with _LOCK:
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

    Checks the `memory_mode` the caller read from the session's transcript, then the live
    restriction registry. Both, because the registry only knows about sessions this process has
    seen, while the transcript survives a restart. A recorded mode keeps the session out unless it
    is `persistent`, as the one reader of a session's mode reads such a record
    (`memory_writes.session_mode`): a value this build does not know, and a metadata line that
    cannot be read, keep it out too. The transcript is not read again here: the census and a
    search ask this of every chat at once, from the heads the chat list already holds.
    """
    mode = (memory_mode or "").strip()
    if mode and mode != "persistent":
        return True
    try:
        from personalclaw import session_restrictions

        return bool(session_restrictions.is_restricted(session_key))
    except Exception:  # noqa: BLE001 — an unavailable registry keeps the session out
        return True


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

    The entry is under the transcript's file name, whichever spelling of the key is handed in;
    the restriction is checked on the key as handed in, the one the registry marks.
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
    name = _file_key(key)
    text = (body or "")[:_MAX_SESSION_CHARS]
    with _LOCK:
        written = _write_entry(conn, name, title, text, mtime=mtime, size=size)
    if written:
        INDEXER.indexed(name, (float(mtime or 0.0), int(size)), len(text))
    return written


def _write_entry(conn, key: str, title: str, text: str, *, mtime: float, size: int) -> bool:
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
    """Re-index a session after a turn lands.

    The turn's own text is not what's stored —
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
    """Remove a session from the index (deleted, or newly restricted), by any spelling of its
    key: the entry is its transcript's file name's.

    Both tables are dropped in one transaction so a failure cannot leave a
    searchable FTS row whose bookkeeping row is gone (or vice versa). A session with a
    bookkeeping row loses its FTS row by that row's rowid; one without — an FTS row
    left by drift, or a key never indexed — is looked for by key, which reads the table.
    """
    key = (session_key or "").strip()
    if key:
        _forget_entry(_file_key(key))


def _forget_entry(key: str) -> None:
    """Remove the entry stored under exactly *key*: what a sweep of the stored keys forgets."""
    INDEXER.forgotten(key)
    conn = _connect()
    if conn is None:
        return
    with _LOCK:
        _drop_entry(conn, key)


def _drop_entry(conn, key: str) -> None:
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
    """Backfill: drop index rows whose transcript no longer exists.

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
        with _LOCK:
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
            _forget_entry(key)
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
    records the new file's stamp over the old text. The transcript is read line by line and
    only as far as the index keeps (:data:`_MAX_SESSION_CHARS`), and none of it is cached: the
    indexer reads a whole history this way, one chat at a time.
    """
    log = log or _conversation_log()
    key = (session_key or "").strip()
    if not key:
        return False
    path = log._path(key)
    try:
        stat = os.stat(path)
    except OSError:
        forget_session(key)  # gone: nothing of it may stay findable
        return False
    try:
        meta = log.get_metadata(key) or {}
    except Exception:  # noqa: BLE001
        meta = {}
    from personalclaw.history import read_memory_mode

    # The mode as the transcript records it, read from the file: a metadata line that cannot be
    # read says so, where the head the chat list holds reads as one that records nothing.
    mode = read_memory_mode(path) or ""
    if is_restricted(key, memory_mode=mode):
        forget_session(key)
        return False
    body = _transcript_text(path)
    if body is None:
        logger.debug("session_search: cannot read %s", key)
        return False
    title = str(meta.get("title", "") or "")
    return index_session(
        key, title, body, memory_mode=mode, mtime=float(stat.st_mtime), size=int(stat.st_size)
    )


def _transcript_text(path: Path) -> str | None:
    """What a session is found by: each message's text but a system note's, in order — read
    only as far as the index keeps. ``None`` when the transcript cannot be read."""
    parts: list[str] = []
    size = 0
    try:
        with open(path, "rb") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                try:
                    data = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(data, dict) or data.get("_type") == "metadata":
                    continue
                if data.get("role") == "system":
                    continue
                text = str(data.get("content", "") or "")
                parts.append(text)
                size += len(text) + 1
                if size >= _MAX_SESSION_CHARS:
                    break
    except OSError:
        return None
    return "\n".join(parts)


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


def _indexed_rows(conn) -> dict[str, tuple[float, int, int]]:
    """Every entry's ``(mtime, size, chars)``: the transcript it was read from, and how many
    characters of its text the index holds."""
    with _LOCK:
        rows = conn.execute("SELECT session_key, mtime, size, chars FROM indexed").fetchall()
    return {
        row["session_key"]: (float(row["mtime"] or 0), int(row["size"]), int(row["chars"] or 0))
        for row in rows
    }


def _is_current(indexed: tuple[float, int] | None, file: tuple[float, int]) -> bool:
    """Whether the index holds a transcript as it is now: its entry's ``(mtime, size)`` against
    the file's. mtime alone is not enough: on a coarse-granularity filesystem an append can land
    inside the same tick, leaving the timestamps equal while the file grew — which is why its
    size is part of the comparison. The size is the file's, from one stat: comparing the indexed
    text's length instead re-read every transcript on every pass (7.4 s and 883 MB for 12,005
    chats, measured)."""
    return indexed is not None and indexed[0] >= file[0] and indexed[1] == file[1]


@dataclass(frozen=True)
class _Census:
    """What a check counted: every indexable chat, newest first; those the index is missing or
    holds an older copy of; and those it holds only the beginning of, because the transcript is
    longer than it keeps (:data:`_MAX_SESSION_CHARS`)."""

    chats: list[str]
    stale: list[str]
    long: list[str]


def _check(log, conn, *, force: bool = False) -> _Census:
    """Every chat against the index. What it holds of a chat that is gone, or turned
    restricted, it forgets."""
    try:
        sessions = log.list_sessions() or []
    except Exception:  # noqa: BLE001
        logger.debug("session_search: cannot list sessions", exc_info=True)
        return _Census([], [], [])
    try:
        known = _indexed_rows(conn)
    except Exception:  # noqa: BLE001
        known = {}
    census = _Census([], [], [])
    for entry in sessions:
        key = str(entry.get("key", "") or "")
        if not key:
            continue
        if is_restricted(key, memory_mode=str(entry.get("memory_mode", "") or "")):
            if key in known:
                forget_session(key)
            continue
        census.chats.append(key)
        row = known.get(key)
        if force or row is None or not _is_current(row[:2], _source_stamp(log, key)):
            census.stale.append(key)
        elif row[2] >= _MAX_SESSION_CHARS:
            census.long.append(key)
    # purge_orphans reads the union of both tables, so an FTS-only drift row (invisible to
    # `known`, which reads only `indexed`) is swept too.
    for key in set(known) - set(census.chats):
        _forget_entry(key)
    purge_orphans(log)
    return census


def reindex_all(log=None, *, force: bool = False) -> int:
    """Index every session that needs it, now, in the caller's thread. Returns how many were
    (re)indexed. The gateway does not call this — its :data:`INDEXER` keeps the index caught
    up in the background — but a script or a test that wants the index current does."""
    log = log or _conversation_log()
    conn = _connect()
    if conn is None:
        return 0
    stale = _check(log, conn, force=force).stale
    return sum(1 for key in stale if reindex_session(key, log=log))


# ── keeping the index caught up ────────────────────────────────────────────────

#: How often the indexer checks every transcript against the index besides at start: for what
#: another process wrote (a restore, a sync, a copied folder).
RECHECK_SECONDS = 300.0
#: The most of the time the indexer spends reading. It rests after each chat in proportion to
#: the work, so a whole history's rebuild never takes more than this share of the interpreter.
DUTY = 0.25
#: The least it rests between two chats, so the interpreter is always handed back.
_MIN_REST_SECONDS = 0.002


class SessionIndexer:
    """Keeps the index caught up with the home's transcripts, in the background.

    It indexed 200 chats every five minutes from the heartbeat: 5 hours for 12,005 after the
    index was rebuilt (measured), while a search answered from the few it had as though they
    were all. Now one thread checks every chat against the index when the gateway starts and
    every :data:`RECHECK_SECONDS` after, and reads each chat that is missing or stale into the
    index, one at a time, until none is: a chat just written first (:meth:`note_changed`), then
    the others newest first. :meth:`progress` is how far it has got, which every search answer
    carries.

    It gives way. A request that asks (:meth:`foreground` — a search, the chat list) holds it
    between two chats, and after each chat it rests in proportion to the work (:data:`DUTY`).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._checking = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._go = threading.Event()
        self._go.set()
        self._holders = 0
        self._thread: threading.Thread | None = None
        self._check_due = True
        self._checked = False
        #: Every indexable chat, as the last check counted it and writes since added.
        self._chats: set[str] = set()
        #: The chats the last check found missing from the index or older in it, newest first.
        self._stale: dict[str, None] = {}
        #: Chats written since, each by the number of its latest write: read before the others,
        #: and kept when a write lands while it is being read.
        self._fresh: dict[str, int] = {}
        self._writes = 0
        #: Chats the index holds only the beginning of (:data:`_MAX_SESSION_CHARS`).
        self._long: set[str] = set()
        self._log: Any = None
        #: The home whose chats these are: a count of another home's chats counts nothing here.
        self._home = ""

    # ── what it has got to ──

    def _rehome(self) -> None:
        """Forget a count made for another home (a test's, or a switch of the active home)."""
        home = _home_sessions()
        with self._lock:
            if self._home == home:
                return
            self._home = home
            self._checked = False
            self._check_due = True
            self._chats, self._stale, self._fresh, self._long = set(), {}, {}, set()
            self._log = None

    def progress(self) -> dict[str, Any] | None:
        """``{"indexed", "of", "building"}``: how many of the home's chats the index answers for,
        of how many there are. ``None`` until the first check has counted them."""
        self._rehome()
        with self._lock:
            if not self._checked:
                return None
            waiting = sum(1 for key in self._chats if self._waiting(key))
            of = len(self._chats)
            return {"indexed": of - waiting, "of": of, "building": bool(waiting)}

    def uncovered(self, keys: list[str]) -> list[str]:
        """Those of *keys* the index does not answer for yet — every one of them before the
        first check has counted the chats. In the order given."""
        self._rehome()
        with self._lock:
            if not self._checked:
                return list(keys)
            return [key for key in keys if self._waiting(key) or key not in self._chats]

    def partial(self, keys: list[str]) -> list[str]:
        """Those of *keys* the index holds as they are but only the beginning of, a transcript
        being longer than it keeps (:data:`_MAX_SESSION_CHARS`). In the order given."""
        self._rehome()
        with self._lock:
            if not self._checked:
                return []
            return [key for key in keys if key in self._long and not self._waiting(key)]

    def _waiting(self, key: str) -> bool:
        return key in self._fresh or key in self._stale

    def ensure_checked(self, *, wait: float) -> None:
        """Make sure the chats have been counted: wait up to *wait* seconds for the running
        indexer's first check, and count them here when no indexer is running."""
        self._rehome()
        deadline = time.monotonic() + wait
        while not self._checked:
            if self._thread is None or not self._thread.is_alive():
                self.check()
                return
            if time.monotonic() >= deadline:
                return
            time.sleep(0.05)

    # ── what it is told ──

    def note_changed(self, key: str) -> None:
        """A transcript in the home was written: read it into the index before the others.
        Every writer calls this after its write lands (``ConversationLog._invalidate_cache``)."""
        with self._lock:
            self._writes += 1
            self._chats.add(key)
            self._fresh[key] = self._writes
        self._wake.set()

    def indexed(self, name: str, stamp: tuple[float, int], chars: int) -> None:
        """The index now holds *chars* characters of the transcript named *name* as it was at
        *stamp* (``(mtime, size)``): it answers for the chat while the transcript is still that
        file. Checked under the lock :meth:`note_changed` takes, so a write that lands meanwhile
        keeps the chat waiting."""
        with self._lock:
            file = _file_stamp(name)
            if file is None or not _is_current(stamp, file):
                return
            self._chats.add(name)
            self._stale.pop(name, None)
            self._fresh.pop(name, None)
            if chars >= _MAX_SESSION_CHARS:
                self._long.add(name)
            else:
                self._long.discard(name)

    def forgotten(self, name: str) -> None:
        """The transcript named *name* left the index: deleted, or restricted."""
        with self._lock:
            self._chats.discard(name)
            self._stale.pop(name, None)
            self._fresh.pop(name, None)
            self._long.discard(name)

    @contextmanager
    def foreground(self) -> Iterator[None]:
        """Hold the indexer between two chats for as long as this block runs."""
        with self._lock:
            self._holders += 1
            self._go.clear()
        try:
            yield
        finally:
            with self._lock:
                self._holders -= 1
                if not self._holders:
                    self._go.set()

    # ── the thread ──

    def start(self) -> bool:
        """Run in a thread, from now until :meth:`stop`. Returns whether one was started."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop = threading.Event()
            self._check_due = True
            self._thread = threading.Thread(
                target=self._run, args=(self._stop,), name="session-search-indexer", daemon=True
            )
            self._thread.start()
            return True

    def stop(self, *, wait: bool = False) -> None:
        with self._lock:
            thread = self._thread
            self._stop.set()
        self._wake.set()
        if wait and thread is not None:
            thread.join(5.0)

    def _run(self, stop: threading.Event) -> None:
        next_check = 0.0
        while not stop.is_set():
            try:
                if self._check_due or time.monotonic() >= next_check:
                    self.check()
                    next_check = time.monotonic() + RECHECK_SECONDS
                self._catch_up(stop)
            except Exception:  # noqa: BLE001 — a failed pass leaves the index as it was
                logger.warning("session search: indexing pass failed", exc_info=True)
                next_check = time.monotonic() + RECHECK_SECONDS
            self._wake.wait(max(0.0, next_check - time.monotonic()))
            self._wake.clear()

    def check(self) -> None:
        """Count every chat against the index (:func:`_check`)."""
        self._rehome()
        with self._checking:
            self._check_due = False
            conn = _connect()
            if conn is None:
                return
            census = _check(self._home_log(), conn)
            with self._lock:
                # A chat written while this counted is waiting in `_fresh`, which a count
                # leaves as it is.
                self._chats = set(census.chats) | set(self._fresh)
                self._stale = dict.fromkeys(census.stale)
                self._long = set(census.long)
                self._checked = True

    def _catch_up(self, stop: threading.Event) -> None:
        log = self._home_log()
        conn = _connect()
        if conn is None:
            return
        while not stop.is_set():
            key: str | None
            write: int | None
            with self._lock:
                if self._fresh:
                    key, write = next(iter(self._fresh.items()))
                else:
                    key, write = next(iter(self._stale), None), None
            if key is None:
                return
            self._give_way(stop)
            if stop.is_set():
                return
            started = time.monotonic()
            try:
                if not _is_current(_indexed_stamp(conn, key), _source_stamp(log, key)):
                    reindex_session(key, log=log)
            except Exception:  # noqa: BLE001 — one unreadable chat must not stop the rest
                logger.debug("session search: could not index %s", key, exc_info=True)
            with self._lock:
                # Read, forgotten or unreadable, it has had its turn: unless it was written
                # again meanwhile, which keeps it first in line.
                self._stale.pop(key, None)
                if write is not None and self._fresh.get(key) == write:
                    del self._fresh[key]
            busy = time.monotonic() - started
            if stop.wait(max(_MIN_REST_SECONDS, busy * (1 - DUTY) / DUTY)):
                return

    def _give_way(self, stop: threading.Event) -> None:
        """Wait while a foreground request holds the indexer."""
        while not self._go.wait(0.1):
            if stop.is_set():
                return

    def _home_log(self) -> Any:
        if self._log is None:
            self._log = _conversation_log()
        return self._log

    def reset(self) -> None:
        """Stop, and forget everything counted — for a test's fresh home."""
        self.stop(wait=True)
        with self._lock:
            self._thread = None
            self._home = ""


def _home_sessions() -> str:
    from personalclaw.history import _sessions_dir

    return str(_sessions_dir())


def _file_key(key: str) -> str:
    """A session key as its transcript's file is named — the form the chat listing uses."""
    from personalclaw.history import _safe_key

    return _safe_key(key.strip())


def _file_stamp(name: str) -> tuple[float, int] | None:
    """The home transcript *name*'s ``(mtime, size)``, as :func:`_source_stamp` reads it;
    ``None`` when there is none."""
    from personalclaw.history import session_path

    try:
        stat = session_path(name).stat()
    except OSError:
        return None
    return float(stat.st_mtime), int(stat.st_size)


def _indexed_stamp(conn, key: str) -> tuple[float, int] | None:
    with _LOCK:
        row = conn.execute(
            "SELECT mtime, size FROM indexed WHERE session_key = ?", (key,)
        ).fetchone()
    return None if row is None else (float(row["mtime"] or 0), int(row["size"]))


#: The home's one indexer. The gateway starts it; every index write keeps its count honest.
INDEXER = SessionIndexer()


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
        with _LOCK:
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
        with _LOCK:
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


# ── what a search answers ──────────────────────────────────────────────────────

#: The newest chats the direct read covers when the index finds nothing, or is unavailable.
SCAN_WINDOW = 500

#: How much of those newest chats that read takes, by the size of their transcripts: the newest,
#: whole, for as long as the next one still fits. A search runs on every pause in typing, and the
#: window used to be read whole however large its chats were — 500 of them on every keystroke that
#: found nothing. At what a direct read costs (:data:`LONG_READ_BYTES`), this is about 50 ms a
#: search; asking for the rest reads every chat regardless.
SCAN_READ_BYTES = 8_000_000


@dataclass
class Answer:
    """What a search found, and how much of the history it looked at.

    ``searched`` of ``of`` chats: those looked in whole, by the index or read directly. Short of
    all of them the answer is not ``complete`` — the index is still being built, or it holds
    only the beginning of a chat longer than it keeps that is past what a search reads directly
    (:data:`LONG_READ_BYTES`), or it is unavailable and only the newest were read — and asking
    for the rest reads the others directly. ``index`` is the index's own
    count (``indexed`` of ``of``, and the ``long`` chats it holds only the beginning of), or
    ``None`` when it is unavailable.
    """

    hits: list[dict]
    source: str
    searched: int
    of: int
    index: dict[str, Any] | None = field(default=None)
    #: How many chats matched, of those searched — more than ``hits`` holds when there were
    #: more matches than the answer lists, which is said too.
    matched: int = 0

    @property
    def complete(self) -> bool:
        return self.searched >= self.of

    def shortfall(self, noun: str) -> str:
        """The sentence that says how much of the history an incomplete answer looked at, naming
        what it searched as *noun* ("conversations", "chats"); ``""`` for a complete one. What a
        tool hands a model beside the matches, so "no match" is never read as "not there"."""
        if self.complete:
            return ""
        if self.index is None:
            return (
                f"Only the {self.searched:,} most recent of {self.of:,} {noun} were searched: "
                "there is no search index."
            )
        if self.index["indexed"] < self.index["of"]:
            return (
                f"Only {self.searched:,} of {self.of:,} {noun} were searched: the search index is "
                "still being built, so matches in the others are not listed yet."
            )
        if self.of - self.searched == 1:
            return (
                f"Only {self.searched:,} of {self.of:,} {noun} were searched whole: the other one "
                "is longer than the search index keeps, so only its beginning was searched."
            )
        return (
            f"Only {self.searched:,} of {self.of:,} {noun} were searched whole: the others are "
            "longer than the search index keeps, so only their beginnings were searched."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessions": self.hits,
            "source": self.source,
            "searched": {"chats": self.searched, "of": self.of},
            "complete": self.complete,
            "index": self.index,
            "matched": self.matched,
        }


def search(
    query: str,
    *,
    log,
    limit: int = 50,
    rest: bool = False,
    visible: Callable[[str], bool] | None = None,
) -> Answer:
    """Search every chat *visible* (all, by default) for *query*, and say how much it covered.

    The index answers for the chats it holds as they are now, and for no more of a chat than
    its first :data:`_MAX_SESSION_CHARS` characters. The chats longer than that are read
    directly, whole, as far as :data:`LONG_READ_BYTES` goes (:func:`_read_whole`). The answer
    counts the chats it could not look in whole — those not in the index yet, and the long ones
    past that bound — and ``rest`` reads exactly those directly: the one way a search is
    complete while the index is still being built, or past the bound. When nothing is found,
    the newest :data:`SCAN_WINDOW` chats are read directly as well, as far as
    :data:`SCAN_READ_BYTES` goes (:func:`_newest_within`), which also matches inside words; with
    no index at all, that window is all a search reads unless ``rest`` asks for every chat. A
    chat a direct read found carries the passage where it was said, marked, as an index hit
    does (``history.match_snippet``).
    """
    text = (query or "").strip()
    listed = [
        entry
        for entry in log.list_sessions()
        if not is_restricted(entry["key"], memory_mode=str(entry.get("memory_mode", "") or ""))
        and (visible is None or visible(entry["key"]))
    ]
    chats = [entry["key"] for entry in listed]
    of = len(chats)
    # The index is the home's: a log rooted anywhere else is only ever read directly.
    home = len(text) >= MIN_QUERY_CHARS and log.is_home_log()
    conn = _connect() if home else None
    if conn is None:
        window = chats if rest else _newest_within(log, chats[:SCAN_WINDOW])
        read = log.search_sessions(text, _EVERY, keys=window) if text else []
        return Answer(read[:limit], "scan", len(window), of, matched=len(read))
    INDEXER.ensure_checked(wait=3.0)
    progress = INDEXER.progress()
    waiting = INDEXER.uncovered(chats)
    long = INDEXER.partial(chats)
    index = {
        "indexed": of - len(waiting),
        "of": of,
        "building": progress is None or bool(progress["building"]),
        "long": len(long),
    }
    # What the index cannot answer for whole, newest first: a direct read covers these.
    unread = {*waiting, *long}
    uncovered = [key for key in chats if key in unread]
    covered = of - len(uncovered)
    # Named as the chat list names them. The index keeps the title a transcript recorded when it
    # was read, and none for most chats, which the list names by their first prompt: a hit said
    # its chat's key instead, beside chats a direct read named the list's way.
    titles = {entry["key"]: entry.get("title") for entry in listed}
    hits = [
        {**hit, "title": titles.get(hit["key"]) or hit["title"]}
        for hit in search_sessions(text, limit=limit)
    ]
    if visible is not None:
        hits = [hit for hit in hits if visible(hit["key"])]
    found = _matching(conn, text) & set(chats)
    if rest and uncovered:
        read = log.search_sessions(text, _EVERY, keys=uncovered)
        found |= {meta["key"] for meta in read}
        return Answer(_merged(hits, read, limit), "index+scan", of, of, index, len(found))
    # What was said past the index's ceiling: the chats longer than it keeps, read whole as far
    # as the bound goes.
    whole = _read_whole(log, [key for key in chats if key in set(long)])
    read = log.search_sessions(text, _EVERY, keys=whole) if whole else []
    found |= {meta["key"] for meta in read}
    covered += len(whole)
    if hits or read:
        source = "index+scan" if hits and read else "scan" if read else "index"
        return Answer(_merged(hits, read, limit), source, covered, of, index, len(found))
    window = _newest_within(log, [key for key in chats[:SCAN_WINDOW] if key not in set(whole)])
    read = log.search_sessions(text, _EVERY, keys=window)
    found |= {meta["key"] for meta in read}
    covered += len(set(window) & unread)
    return Answer(read[:limit], "scan" if read else "index", covered, of, index, len(found))


#: How much of the chats longer than the index keeps a search reads directly, by the size of
#: their transcripts, so what was said past the index's ceiling is found while typing. A direct
#: read costs 5 to 7 ms a megabyte (measured), so this is about 50 ms a search. The index's
#: ceiling (:data:`_MAX_SESSION_CHARS`) stays: it bounds the index's size and what a save
#: re-reads, and these chats are few.
LONG_READ_BYTES = 8_000_000


def _newest_within(log, keys: list[str]) -> list[str]:
    """The newest of *keys* (they come newest first) that a search reads whole on its own: in
    order, while their transcripts fit in :data:`SCAN_READ_BYTES`, stopping at the first that
    does not. So they are the newest, which is what an answer that did not read the rest says it
    read. A chat with no transcript to read is passed over."""
    chosen: list[str] = []
    spent = 0
    for key in keys:
        size = _source_stamp(log, key)[1]
        if size < 0:
            continue
        if spent + size > SCAN_READ_BYTES:
            break
        chosen.append(key)
        spent += size
    return chosen


def _read_whole(log, keys: list[str]) -> list[str]:
    """Those of *keys* (newest first) a search reads whole: newest first, each that still fits in
    :data:`LONG_READ_BYTES`. One that does not is left to the index, which holds its beginning,
    and the answer counts it as not searched whole."""
    chosen: list[str] = []
    spent = 0
    for key in keys:
        size = _source_stamp(log, key)[1]
        if size < 0 or spent + size > LONG_READ_BYTES:
            continue
        chosen.append(key)
        spent += size
    return chosen


#: As many matches as a direct read finds: it ranks every chat it reads anyway.
_EVERY = 1_000_000_000


def _matching(conn, text: str) -> set[str]:
    """Every chat the index finds *text* in, by its transcript's name — to count, not to list."""
    match = _fts_query(text)
    if not match:
        return set()
    try:
        with _LOCK:
            rows = conn.execute(
                "SELECT DISTINCT session_key FROM sessions_fts WHERE sessions_fts MATCH ?", (match,)
            ).fetchall()
    except sqlite3.Error:
        return set()
    return {row["session_key"] for row in rows if not is_restricted(row["session_key"])}


def _merged(first: list[dict], then: list[dict], limit: int) -> list[dict]:
    """*first*'s hits, then those of *then* for chats *first* does not already name."""
    seen = {hit["key"] for hit in first}
    return [*first, *(hit for hit in then if hit["key"] not in seen)][: max(limit, len(first))]
