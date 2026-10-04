"""Persistent memory — structured files, daily history, and FTS5 search.

Structure:
    ~/.personalclaw/workspace/memory/
    ├── preferences.md      # Learned user preferences
    ├── projects.md         # Active project context
    └── history/
        └── 2026-02-16.md   # Daily conversation summaries

    ~/.personalclaw/memory_index.db  # FTS5 full-text search index

A store given a working folder's partition keeps its index in that folder's ``memory_index.db``,
where the partition's vector store keeps the folder's memories too. Nothing here ever deletes that
file (see "The keyword index" below).

**The documents are rewritten from the file as it is then.** preferences.md and projects.md have
several writers: the owner in Settings → Memory or the Files editor, the agent's file tools, and a
consolidation, which reads both, waits for its model (seconds, for a real one) and rewrites them.
So a consolidation's rewrite is applied to each file as it is when the model has answered, never
written over it from the copy it read (:meth:`MemoryStore.rewrite`, :func:`apply_rewrite`),
and every writer of the documents and of the daily history reads and writes them under one lock
(:func:`hold_documents`), so none lands between another's read and its write.

**Every change to a document is the store's own write** (:meth:`MemoryStore._persist`): indexed,
and refused inside work that may change none of your memory, an Incognito or Temporary chat's or
an app's not given your memory (``memory_writes``). The writers that reach any file, the agent's
file tools and the Files editor's save, write a document through it too (:func:`write_document`),
and the agent's tools change nothing else in the memory folders for such work either
(:func:`memory_folders`: ``file_scope`` refuses the call, the sandbox keeps them read-only).

**Each day of the daily history is its own file.** A consolidation appends its entry to today's;
the owner reads and saves one day at a time (:meth:`MemoryStore.read_history_day`,
:meth:`MemoryStore.write_history_day`), so a save of one day changes no other. The recent days read
together (:meth:`MemoryStore.read_recent_history`) are what the agent's context carries, older days
cut short, and are never written back.
"""

import fcntl
import logging
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, TypeVar

from personalclaw import memory_writes, notification_kinds
from personalclaw.atomic_write import SQLITE_SIDECARS, atomic_write, make_private_dirs
from personalclaw.config import loader as config_loader
from personalclaw.home_paths import from_home
from personalclaw.sqlite_compat import FTS5_REMEDY, connect, probe, sqlite3


def config_dir() -> Path:
    """The active home, re-resolved per call and never made here: each path in it is worked out
    from this, and :meth:`MemoryStore.init` makes the folders it writes into (the home first,
    ``atomic_write.ensure_home_for``). See :func:`personalclaw.config.loader.resolve_config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.resolve_config_dir()


if TYPE_CHECKING:
    from personalclaw.vector_memory import VectorMemoryStore

logger = logging.getLogger(__name__)

# ── Paths ──

WORKSPACE_DIR_NAME = "workspace"
MEMORY_DIR_NAME = "memory"
HISTORY_DIR_NAME = "history"
PREFERENCES_FILE = "preferences.md"
PROJECTS_FILE = "projects.md"

_DEFAULT_PREFERENCES = "# User Preferences\n\n<!-- Learned from conversations -->\n"
_DEFAULT_PROJECTS = "# Active Projects\n\n<!-- Current work context -->\n"


def workspace_dir() -> Path:
    return config_dir() / WORKSPACE_DIR_NAME


def memory_dir() -> Path:
    return workspace_dir() / MEMORY_DIR_NAME


# ── The documents' writers ──

#: The memory documents' lock (``concurrency.lock_path``): one for every memory's preferences.md,
#: projects.md and daily history, in the home's ``locks/`` rather than beside them, where it would
#: be listed and copied with the memory.
_DOCUMENTS_LOCK = "memory-documents"


@contextmanager
def hold_documents() -> Iterator[None]:
    """Hold the memory documents' lock, waiting for it, while a document is read and written.

    Every writer of preferences.md, projects.md and the days of the daily history holds it across
    its read and its write: a consolidation applying its rewrite (:meth:`MemoryStore.rewrite`) and
    appending its entry (:meth:`MemoryStore.append_history`), the Memory page's save of a document
    or of a day, :meth:`MemoryStore.add_preference`, the removal at start of what an Incognito or
    Temporary chat left in the history (:meth:`MemoryStore.forget_history_entries`), the boot that
    creates the files, a partition's documents moving into another, and the Files editor's save and
    the agent's ``write_file`` and ``edit_file`` (``write_locks``). ``flock`` on a file opened for
    this hold, so it excludes another thread as it does another process (the ``personalclaw
    consolidate`` command), and the OS frees it if the holder dies. Not re-entrant: nothing done
    while it is held may take it again. A command the agent's shell runs, or another program, takes
    no lock.
    """
    from personalclaw.concurrency import lock_path
    from personalclaw.durability.home_paths import open_lock

    with open_lock(lock_path(_DOCUMENTS_LOCK)) as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def is_document(path: Path | str) -> bool:
    """Whether *path* is a memory document: a preferences.md or projects.md in a memory folder, or a
    day of its daily history (a ``.md`` file in the memory folder's ``history``), named directly or
    through a link. Known by where every store keeps them, so a file of that name in a folder of
    that name elsewhere counts too, which only makes its write wait for a memory document's."""
    real = Path(os.path.realpath(path))
    if real.parent.name == MEMORY_DIR_NAME:
        return real.name in (PREFERENCES_FILE, PROJECTS_FILE)
    return (
        real.suffix == ".md"
        and real.parent.name == HISTORY_DIR_NAME
        and real.parent.parent.name == MEMORY_DIR_NAME
    )


def memory_folders() -> tuple[Path, Path]:
    """The folders long-term memory keeps its files in, in the home's ``workspace``
    (``config.loader.memory_root``): the ``memory`` folder of the home's own preferences.md,
    projects.md and daily history, and the folder every working folder's memory is kept in, its
    documents and its database (``config.loader.memory_dir_for_cwd``). Worked out, never made."""
    return (
        config_loader.memory_root() / MEMORY_DIR_NAME,
        config_loader.memory_dir_for_cwd(None).parent,
    )


def in_memory_folders(path: "str | os.PathLike[str]") -> bool:
    """Whether *path*, once links and ``..`` are resolved, is one of the memory folders
    (:func:`memory_folders`) or lies inside one, so that a change to it changes long-term memory."""
    real = os.path.realpath(path)
    tops = (os.path.realpath(folder) for folder in memory_folders())
    return any(real == top or real.startswith(top + os.sep) for top in tops)


def write_document(path: Path | str, content: str) -> bool:
    """Write *content* as the memory document *path* names through its store's one write of a
    memory file (:meth:`MemoryStore._persist`), which refuses it inside work that may change none
    of your memory and indexes it. False, writing nothing, for a path no store keeps a document at:
    its writer writes it as any other file.

    For the writers that reach any file, the agent's ``write_file`` and ``edit_file`` and the Files
    editor's save, which hold the documents' lock across their read and this write
    (``write_locks``). A document is a preferences.md, a projects.md or a ``.md`` file in the daily
    history of the home's memory or a working folder's (:func:`memory_folders`), named directly or
    through a link, and it is written at the path its store names it by, the one its index uses.
    """
    real = Path(os.path.realpath(path))
    home_memory, partitions = memory_folders()
    if (name := _document_in(real, home_memory)) is not None:
        store = MemoryStore()
    else:
        try:
            partition = partitions / real.relative_to(os.path.realpath(partitions)).parts[0]
        except (ValueError, IndexError):
            return False
        if (name := _document_in(real, partition / MEMORY_DIR_NAME)) is None:
            return False
        store = MemoryStore(workspace=partition)
    store._persist(store._memory_dir / name, content)
    return True


def _document_in(real: Path, memory: Path) -> Path | None:
    """Where the resolved path *real* is in the memory folder *memory*, when it is one of that
    folder's documents: its preferences.md or projects.md, or a ``.md`` file of its history."""
    folder = Path(os.path.realpath(memory))
    if real.parent == folder and real.name in (PREFERENCES_FILE, PROJECTS_FILE):
        return Path(real.name)
    if real.parent == folder / HISTORY_DIR_NAME and real.suffix == ".md":
        return Path(HISTORY_DIR_NAME, real.name)
    return None


#: How the daily history names a day: its file is ``history/<YYYY-MM-DD>.md``.
_DAY = "%Y-%m-%d"


def is_history_day(name: str) -> bool:
    """Whether *name* names a day as the daily history does: a date of the calendar, ``YYYY-MM-DD``
    exactly."""
    try:
        return datetime.strptime(name, _DAY).strftime(_DAY) == name
    except ValueError:
        return False


def history_today() -> str:
    """Today, as the daily history names it: the date where the gateway runs."""
    return datetime.now().strftime(_DAY)


def _entry_count(text: str) -> int:
    """How many entries a day of the daily history holds: one under each ``#### <time>`` heading,
    as :meth:`MemoryStore.append_history` writes them."""
    return sum(1 for line in text.splitlines() if line.startswith("#### "))


def apply_rewrite(read: str, now: str, rewrite: str) -> str | None:
    """*rewrite*, which its writer made of the text *read*, applied to the text as it is *now*;
    None when that would undo a change made since.

    The three are compared line by line. Each part of the file the rewrite changes (a line
    rewritten, removed or added) is changed when *now* still holds that part as it was read, and at
    least one line neither changed lies between it and each change made since. A part both changed
    the same way is kept once. A part both changed differently, or changed right next to each
    other, gives None: lines side by side often belong together (a project and its notes beside
    it), and of two additions in one place neither can be put first, so the file is left whole as
    it is. The result ends each line with a newline. The text *read* is what *rewrite* is applied
    against, so a file still as it was read is *rewrite* exactly.
    """
    if now == read:
        return rewrite
    if rewrite == read:
        return now
    merged = _merged_lines(read.splitlines(), now.splitlines(), rewrite.splitlines())
    return None if merged is None else "".join(line + "\n" for line in merged)


def _merged_lines(read: list[str], now: list[str], rewrite: list[str]) -> list[str] | None:
    """*now* with the changes *rewrite* made to *read*, or None (:func:`apply_rewrite`)."""
    out: list[str] = []
    r0 = n0 = w0 = 0
    for r1, r2, n1, n2, w1, w2 in _kept_by_both(read, now, rewrite):
        was, theirs, generated = read[r0:r1], now[n0:n1], rewrite[w0:w1]
        if theirs == was:
            out += generated
        elif generated in (was, theirs):
            out += theirs
        else:
            return None
        out += now[n1:n2]
        r0, n0, w0 = r2, n2, w2
    return out


def _kept_by_both(
    read: list[str], now: list[str], rewrite: list[str]
) -> Iterator[tuple[int, int, int, int, int, int]]:
    """Each run of *read*'s lines that *now* and *rewrite* both still hold, in order, as its start
    and end in each of the three, and last an empty run at their ends."""
    in_now = SequenceMatcher(None, read, now, autojunk=False).get_matching_blocks()
    in_rewrite = SequenceMatcher(None, read, rewrite, autojunk=False).get_matching_blocks()
    i = j = 0
    while i < len(in_now) and j < len(in_rewrite):
        a, n, size_n = in_now[i]
        b, w, size_w = in_rewrite[j]
        start, end = max(a, b), min(a + size_n, b + size_w)
        if start < end:
            yield start, end, n + start - a, n + end - a, w + start - b, w + end - b
        if a + size_n < b + size_w:
            i += 1
        else:
            j += 1
    yield len(read), len(read), len(now), len(now), len(rewrite), len(rewrite)


# ── The keyword index ──
#
# The keyword (full-text) index is DERIVED: each row is one memory file's text
# (`MemoryStore._indexable_files`), so it can always be rebuilt from the files, and nothing in it is
# kept nowhere else. The FILE it lives in is not always the index's alone: a working folder's
# partition keeps its vector store's memories in the same database (`context._attach_vector_store`),
# its facts and episodes, held nowhere else. So nothing here deletes that file, whatever fails. An
# index that cannot be used is rebuilt in place from the files; a database SQLite reports damaged is
# moved aside whole, never deleted, and a new one started; and what cannot be repaired is left as it
# is and recorded as degraded keyword search with its reason, which the Doctor's memory check (and
# the memory page, which shows that check) says.

INDEX_FILE = "memory_index.db"
#: What a damaged database is renamed to, beside where it was, the files SQLite kept beside it with
#: it: ``<name>.broken-<UTC instant>``. Never deleted, never written over, never opened again.
SET_ASIDE_MARK = ".broken-"

_FTS_TABLE = "memory_fts"
_CREATE_FTS = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} USING fts5("
    "path, content, tokenize='porter unicode61')"
)

#: Each keyword index this process could not build or keep up to date with the memory files → why,
#: in one clause. Cleared when the index is next rebuilt from the files. Keyed by the index's file,
#: so every store on one index (the gateway's, a maintenance pass's) shares it; process-local like
#: `config.loader.config_discard`, because it is what this process saw.
_DEGRADED: dict[str, str] = {}
_DEGRADED_LOCK = threading.Lock()
#: One repair at a time: two stores mending one file must not each move it aside.
_REPAIR_LOCK = threading.Lock()

_T = TypeVar("_T")


class KeywordIndexUnavailable(RuntimeError):
    """The keyword index cannot be used now; :func:`degraded_keyword_indexes` says why."""


def degraded_keyword_indexes() -> dict[str, str]:
    """Each keyword index this process cannot use now → why, in one clause."""
    with _DEGRADED_LOCK:
        return dict(_DEGRADED)


def set_aside_databases(home: Path) -> list[Path]:
    """Each damaged memory database moved aside under *home*, oldest first: the home's own keyword
    index, and each working folder partition's database. The files SQLite kept beside one moved
    with it and are not listed."""
    found = [
        *home.glob(f"{INDEX_FILE}{SET_ASIDE_MARK}*"),
        *(config_loader.memory_root(home) / "_ext").glob(f"*/{INDEX_FILE}{SET_ASIDE_MARK}*"),
    ]
    copies = [p for p in found if p.is_file() and not p.name.endswith(SQLITE_SIDECARS)]
    return sorted(copies, key=lambda p: (p.name.split(SET_ASIDE_MARK, 1)[1], str(p)))


def retry_degraded_keyword_indexes(home: Path) -> dict[Path, str]:
    """Rebuild each keyword index of *home* this process recorded degraded, from its memory
    files, and return those that still cannot be used → why.

    So a report of degraded search says what is true now rather than what was true when the
    index was last used: a partition whose vector store has since opened may not use its keyword
    index again for a long time. The index is derived from the files, so rebuilding it changes
    no memory, and it goes through the store's own repair (:meth:`MemoryStore._repair`), which
    deletes nothing.
    """
    root = home.resolve()
    left: dict[Path, str] = {}
    for key, recorded in degraded_keyword_indexes().items():
        index = Path(key)
        where = index.resolve()
        if not where.is_relative_to(root):
            continue
        # The home's own store keeps its index at the top of the home, and a folder's store in the
        # folder; either is built on the path it was recorded under, so it reads its own record.
        if where.parent != root:
            store = MemoryStore(workspace=index.parent)
        elif config_dir().resolve() == root:
            store = MemoryStore()
        else:  # another home's own index: this process cannot open its store
            left[where] = recorded
            continue
        store.rebuild_index()
        if why := store.search_degraded():
            left[where] = why
    return left


#: SQLite's names for a failure no rebuild of the index can help with — the database is locked,
#: read-only, missing or out of room, or the disk failed — each in the words the Doctor says it in.
_ENVIRONMENT: dict[str, str] = {
    "SQLITE_BUSY": "another connection held the database locked",
    "SQLITE_LOCKED": "another connection held the database locked",
    "SQLITE_READONLY": "the database file is read-only",
    "SQLITE_FULL": "the disk is full",
    "SQLITE_CANTOPEN": "the database file could not be opened",
    "SQLITE_IOERR": "the disk failed to read or write the database",
    "SQLITE_PERM": "permission to the database file was denied",
}
#: The message SQLite gives each of those, for a driver whose errors carry no name (``pysqlite3``).
_ENVIRONMENT_MESSAGES: dict[str, str] = {
    "database is locked": "SQLITE_BUSY",
    "database table is locked": "SQLITE_LOCKED",
    "attempt to write a readonly database": "SQLITE_READONLY",
    "database or disk is full": "SQLITE_FULL",
    "unable to open database file": "SQLITE_CANTOPEN",
    "disk I/O error": "SQLITE_IOERR",
    "access permission denied": "SQLITE_PERM",
}


def _sqlite_name(exc: BaseException) -> str:
    """SQLite's name for what *exc* is (``SQLITE_READONLY``…), or "" when it gives none."""
    name = str(getattr(exc, "sqlite_errorname", "") or "")
    if name or not isinstance(exc, sqlite3.Error):
        return name
    text = str(exc)
    return next((n for message, n in _ENVIRONMENT_MESSAGES.items() if message in text), "")


def _environment(exc: BaseException) -> str:
    """Why the index cannot be written when no rebuild can help — a lock, the file's mode, the
    disk — in the Doctor's words, or "" when *exc* is something else."""
    if isinstance(exc, OSError):
        return f"a memory file or its folder could not be used ({exc.strerror or exc})"
    name = _sqlite_name(exc)
    return next(
        (why for n, why in _ENVIRONMENT.items() if name == n or name.startswith(n + "_")), ""
    )


def _is_damage(exc: BaseException) -> bool:
    """Whether *exc* is SQLite saying a database, or the index in it, is damaged — or that the file
    is not a database at all."""
    name = _sqlite_name(exc)
    if name:
        return name.startswith("SQLITE_CORRUPT") or name == "SQLITE_NOTADB"
    # A driver without the names raises exactly DatabaseError for these, and a subclass of it
    # (OperationalError, IntegrityError…) for every other failure.
    return type(exc) is sqlite3.DatabaseError


def _why(exc: BaseException) -> str:
    """Why keyword search cannot use its index, in one clause."""
    return _environment(exc) or f"SQLite could not build the index ({exc})"


def _damage(db: Path) -> str:
    """What SQLite finds wrong with the database *db* beyond the keyword index, or "" when it finds
    the rest sound or cannot tell (a lock).

    Read-only. Damage confined to the index is the index's own and is mended by rebuilding it, so a
    problem SQLite names the index's tables in is not counted (FTS5's own check reports a corrupt
    index that way). A database is only ever moved aside on SQLite's word that it is damaged.

    The whole database is checked at once, and that check opens the index. An FTS5 that reads the
    index's structure as it opens it (SQLite 3.45's does) cannot open an index whose structure is
    damaged: the check stops there, naming the index, where a later SQLite opens it and reports the
    damage as one more finding. Each other table is then checked on its own, which never opens the
    index, so every build says whether the rest is sound.
    """
    if not db.is_file():
        return ""
    try:
        # as_uri() percent-encodes the path (see `context._holds_memory`).
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=2.0)
        try:
            try:
                rows = conn.execute("PRAGMA quick_check").fetchall()
            except sqlite3.Error as exc:
                if _FTS_TABLE not in str(exc):
                    raise
                rows = [row for table in _tables(conn) for row in _quick_check(conn, table)]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return str(exc) if _is_damage(exc) and _FTS_TABLE not in str(exc) else ""
    found = [str(r[0]) for r in rows if str(r[0]) != "ok" and _FTS_TABLE not in str(r[0])]
    return "; ".join(found[:3])


def _tables(conn: sqlite3.Connection) -> list[str]:
    """Each table of *conn*'s database but the virtual ones, the keyword index among them. The
    tables FTS5 keeps the index's rows in are ordinary ones, and are listed."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND sql NOT LIKE 'CREATE VIRTUAL TABLE%'"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _quick_check(conn: sqlite3.Connection, table: str) -> list[tuple]:
    """SQLite's quick check of *table* alone: its pages and its indexes."""
    quoted = '"' + table.replace('"', '""') + '"'
    return conn.execute(f"PRAGMA quick_check({quoted})").fetchall()


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _beside(path: Path, suffix: str) -> Path:
    """*path* with *suffix* on the end of its name: where SQLite keeps a database's journal, log and
    shared memory (``-journal``, ``-wal``, ``-shm``), and where a damaged one is set aside."""
    return path.with_name(path.name + suffix)


def _move_aside(db: Path) -> Path:
    """Rename *db*, and each file SQLite keeps beside it, to ``<name>.broken-<UTC instant>`` beside
    where it was, and return the new path of *db*.

    A name already taken gets a counter rather than being reused: a rename over a file replaces it,
    and that file is a copy set aside before. The files beside the database move first, so a new
    database made at its name can never find the old one's log and replay it.
    """
    stamp = SET_ASIDE_MARK + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = _beside(db, stamp)
    counter = 2
    while any(_beside(target, suffix).exists() for suffix in ("", *SQLITE_SIDECARS)):
        target = _beside(db, f"{stamp}-{counter}")
        counter += 1
    for suffix in SQLITE_SIDECARS:
        if _beside(db, suffix).exists():
            _beside(db, suffix).rename(_beside(target, suffix))
    db.rename(target)
    return target


def _announce_set_aside(
    db: Path, moved: Path, damage: str, *, held_memories: bool, rebuilt: bool
) -> None:
    """Say that the damaged database *db* was moved aside to *moved*: one WARNING, and a notice to
    the owner when a gateway is running. A folder's memories it held are in the moved file; the
    home's own index held only what the memory files hold, *rebuilt* from them or not yet."""
    if held_memories:
        after = (
            "The memories this folder kept in it are in that file: restore them from a snapshot "
            "under Settings → Durability, or recover them from the file."
        )
    elif rebuilt:
        after = "Keyword search was rebuilt from the memory files, so nothing is missing."
    else:
        after = (
            "Nothing is missing: keyword search is rebuilt from the memory files once it can be, "
            "and the Doctor says why it cannot yet."
        )
    logger.warning(
        "The memory database %s is damaged (%s). It was moved aside to %s, not deleted, and a "
        "new one started. %s",
        from_home(db),
        damage,
        moved.name,
        after,
    )
    try:
        from personalclaw.inbox_providers.native_source import get_dashboard_state

        state = get_dashboard_state()
        if state is not None:
            state.notify(
                notification_kinds.WARNING,
                "A damaged memory database was moved aside",
                f"{from_home(db)} could not be read ({damage}). It was moved aside to "
                f"{moved.name} beside it, not deleted, and a new one started. {after}",
            )
    except Exception:  # noqa: BLE001 — the notice is best-effort: the move is done and logged
        logger.debug("memory: the notice for %s was not posted", moved, exc_info=True)


# ── MemoryStore ──


class MemoryStore:
    """The markdown projection layer: preferences.md, projects.md, daily history,
    FTS5 search over them.

    Post-M2 this is NOT a ``MemoryProvider`` — the provider seam is the record/
    vector store (``VectorMemoryStore``). This class is the human-readable
    *projection* the ``MemoryService`` composes for prompt context + the
    Obsidian-style FS mirror. Its files are a view, not a parallel store.
    """

    @property
    def name(self) -> str:
        return "native"

    def __init__(self, workspace: Path | None = None):
        self._workspace = workspace or workspace_dir()
        self._memory_dir = self._workspace / MEMORY_DIR_NAME
        self._history_dir = self._memory_dir / HISTORY_DIR_NAME
        self._preferences_file = self._memory_dir / PREFERENCES_FILE
        self._projects_file = self._memory_dir / PROJECTS_FILE
        self._index_db = (workspace or config_dir()) / INDEX_FILE
        # The home's own index is a file of its own; a store given a folder keeps its index in that
        # folder's database, where a vector store keeps the folder's memories too.
        self._index_alone = workspace is None
        self._vector_store: "VectorMemoryStore | None" = None
        # DEGRADE (not raise): the markdown projection (preferences/projects/history) is
        # this class's real job and works without a search index — FTS5 only powers the
        # optional `search()`. Decide ONCE here whether the index is available; on a build
        # without FTS5 we log the remedy once and every FTS method becomes a clean no-op
        # instead of throwing (or retrying to build) on each write/search.
        self._fts_available = probe().fts5
        if not self._fts_available:
            logger.warning("Memory full-text search disabled. %s", FTS5_REMEDY)

    @property
    def vector_store(self) -> "VectorMemoryStore | None":
        return self._vector_store

    @vector_store.setter
    def vector_store(self, store: "VectorMemoryStore | None") -> None:
        self._vector_store = store

    def init(self) -> None:
        """Create directory structure and default files. Each folder is 0700, as every folder a
        home file is written into is (``atomic_write.make_private_dirs``): the store's own folder
        (a working folder's partition), its memory folder and the daily history. Under the
        documents' lock (:func:`hold_documents`), so a document another writer makes at that moment
        is not written over."""
        for folder in (self._workspace, self._memory_dir, self._history_dir):
            make_private_dirs(folder)
        with hold_documents():
            if not self._preferences_file.exists():
                atomic_write(self._preferences_file, _DEFAULT_PREFERENCES)
            if not self._projects_file.exists():
                atomic_write(self._projects_file, _DEFAULT_PROJECTS)

    # ── Preferences ──

    def read_preferences(self) -> str:
        """Read user preferences markdown file."""
        if self._preferences_file.exists():
            return self._preferences_file.read_text(encoding="utf-8")
        return ""

    def write_preferences(self, content: str) -> None:
        """Write user preferences and update FTS index. A writer that read the file first writes
        under the documents' lock (:func:`hold_documents`), held across both."""
        self._persist(self._preferences_file, content)

    def add_preference(self, preference: str) -> None:
        """Append a preference line, avoiding duplicates, to the file as it is: read and written
        under the documents' lock."""
        with hold_documents():
            content = self.read_preferences()
            if preference not in content:
                content += f"- {preference}\n"
                self.write_preferences(content)

    # ── Projects ──

    def read_projects(self) -> str:
        """Read active projects markdown file."""
        if self._projects_file.exists():
            return self._projects_file.read_text(encoding="utf-8")
        return ""

    def write_projects(self, content: str) -> None:
        """Write active projects, adding header if missing, and update FTS index. A writer that
        read the file first writes under the documents' lock (:func:`hold_documents`)."""
        date = datetime.now().strftime("%Y-%m-%d")
        # Don't double-wrap if content already has the header
        if content.strip().startswith("# Active Projects"):
            full = content.strip() + "\n"
        else:
            full = f"# Active Projects\n\n_Updated: {date}_\n\n{content}\n"
        self._persist(self._projects_file, full)

    def rewrite(self, which: str, read: str, rewritten: str, *, by: str) -> None:
        """Apply *rewritten*, the text *by* made of the document *which* (``"preferences"`` or
        ``"projects"``) from its text *read* before it waited (a consolidation's model call), to the
        document as it is now.

        Read again and written under the documents' lock (:func:`hold_documents`), with nothing
        awaited, so no writer lands in between. A document still as it was read is written as
        *rewritten*. One changed since (the owner saved it in Settings → Memory or the Files editor,
        the agent wrote it) has the rewrite applied around those changes (:func:`apply_rewrite`),
        and one changed where the rewrite changes it, or right beside it, is left as it is: what
        was written since is the later word. The log says which of the two it was.
        """
        path, write = {
            "preferences": (self._preferences_file, self.write_preferences),
            "projects": (self._projects_file, self.write_projects),
        }[which]
        with hold_documents():
            now = path.read_text(encoding="utf-8") if path.exists() else ""
            merged = apply_rewrite(read, now, rewritten)
            if merged is not None and merged.splitlines() != now.splitlines():
                write(merged)
        if merged is None:
            logger.warning(
                "%s: %s was changed after it was read, where the rewrite changes it or right "
                "beside it, so it is left as it is and the rewrite of it is not applied",
                by,
                path.name,
            )
        elif now != read:
            logger.info(
                "%s: %s was changed after it was read, and the rewrite was applied around that",
                by,
                path.name,
            )

    # ── Combined read/write (used by consolidator) ──

    def read(self) -> str:
        """Read preferences + projects as combined memory."""
        parts: list[str] = []
        prefs = self.read_preferences()
        if prefs.strip() and prefs.strip() != _DEFAULT_PREFERENCES.strip():
            parts.append(prefs)
        projects = self.read_projects()
        if projects.strip() and projects.strip() != _DEFAULT_PROJECTS.strip():
            parts.append(projects)
        return "\n\n".join(parts)

    def write(self, content: str) -> None:
        """Write combined memory — splits into preferences + projects sections."""
        if "# Active Projects" in content:
            idx = content.index("# Active Projects")
            self.write_preferences(content[:idx].strip() + "\n")
            # Write directly + index (not write_projects, which adds a header)
            projects_content = content[idx:].strip() + "\n"
            self._persist(self._projects_file, projects_content)
        else:
            self.write_preferences(content)

    # ── Daily History ──

    def _history_file(self, day: str) -> Path:
        """Where the daily history keeps *day* (``YYYY-MM-DD``). Any other name is refused rather
        than made into a path: it could name a file outside the history."""
        if not is_history_day(day):
            raise ValueError(f"not a day of the daily history: {day!r}")
        return self._history_dir / f"{day}.md"

    def _today_history_file(self) -> Path:
        return self._history_file(history_today())

    def history_days(self) -> list[tuple[str, int]]:
        """Each day the daily history keeps a file for, newest first, with how many entries the
        file holds."""
        if not self._history_dir.is_dir():
            return []
        days = [
            (path.stem, _entry_count(path.read_text(encoding="utf-8", errors="replace")))
            for path in self._history_dir.glob("*.md")
            if is_history_day(path.stem)
        ]
        return sorted(days, reverse=True)

    def read_history_day(self, day: str) -> str:
        """The daily history file of *day* (``YYYY-MM-DD``) as it is kept, or "" when there is
        none."""
        path = self._history_file(day)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def write_history_day(self, day: str, content: str) -> None:
        """Replace the daily history file of *day* (``YYYY-MM-DD``) with *content*, and index it.
        No other day's file is touched. A writer that read the file first writes under the
        documents' lock (:func:`hold_documents`), held across both."""
        path = self._history_file(day)
        make_private_dirs(self._history_dir)
        self._persist(path, content)

    def append_history(self, entry: str) -> None:
        """Append a timestamped entry to today's daily history file, read and written under the
        documents' lock (:func:`hold_documents`)."""
        make_private_dirs(self._history_dir)
        timestamp = datetime.now().astimezone().strftime("%H:%M %Z")
        with hold_documents():
            day = history_today()
            content = self.read_history_day(day) or f"# {day}\n"
            content += f"\n#### {timestamp}\n{entry.strip()}\n"
            self._persist(self._history_file(day), content)

    def forget_history_entries(self, entries: "set[str]") -> int:
        """Remove from the daily history every entry whose text is one of ``entries``, verbatim.

        Returns how many entries went. What the consolidator appended for a session that keeps
        nothing is removed this way at start (see ``memory_writes``); an entry that is not word for
        word one of ``entries`` is left as it is. Each day is read and written under the documents'
        lock (:func:`hold_documents`).
        """
        wanted = {e.strip() for e in entries if e and e.strip()}
        if not wanted or not self._history_dir.is_dir():
            return 0
        removed = 0
        with hold_documents():
            for path in sorted(self._history_dir.glob("*.md")):
                content = path.read_text(encoding="utf-8")
                head, *blocks = content.split("\n#### ")
                kept = [b for b in blocks if b.partition("\n")[2].strip() not in wanted]
                if len(kept) != len(blocks):
                    removed += len(blocks) - len(kept)
                    self._persist(path, "\n#### ".join([head, *kept]))
        return removed

    def take_in_documents(self, other: "MemoryStore") -> None:
        """Add to these documents what *other*'s hold that these do not, for a partition whose
        memory now belongs to this one (``memory_locality.move_what_context_folders_kept``): its
        preference lines, its projects, and each day's history entries. Text these already hold is
        not added again, so taking the same documents twice changes nothing."""
        if other is self:
            return
        # Each document and each day read and written under the documents' lock (`hold_documents`).
        with hold_documents():
            mine = self.read_preferences()
            held = set(mine.splitlines())
            new = [ln for ln in other.read_preferences().splitlines() if ln.startswith("- ")]
            new = [ln for ln in dict.fromkeys(new) if ln not in held]
            if new:
                self.write_preferences(mine.rstrip("\n") + "\n" + "\n".join(new) + "\n")
            theirs = other.read_projects().strip()
            if theirs and theirs != _DEFAULT_PROJECTS.strip():
                body = theirs.removeprefix("# Active Projects").strip()
                current = self.read_projects()
                if body and body not in current:
                    self._persist(self._projects_file, current.rstrip("\n") + "\n\n" + body + "\n")
            if not other._history_dir.is_dir():
                return
            for path in sorted(other._history_dir.glob("*.md")):
                entries = path.read_text(encoding="utf-8").split("\n#### ")[1:]
                target = self._history_dir / path.name
                current = (
                    target.read_text(encoding="utf-8") if target.exists() else f"# {path.stem}\n"
                )
                kept = set(current.split("\n#### ")[1:])
                add = [entry for entry in entries if entry not in kept]
                if add:
                    make_private_dirs(self._history_dir)
                    self._persist(target, current + "".join("\n#### " + entry for entry in add))

    def _history_files_over_retention(self, keep_days: int) -> list[Path]:
        """Daily history files older than *keep_days*. Shared by the prune and its
        dry-run count so the measured deficit is exactly what the prune would delete."""
        if not self._history_dir.exists():
            return []
        cutoff = datetime.now().date() - timedelta(days=keep_days)
        out: list[Path] = []
        for f in self._history_dir.glob("*.md"):
            try:
                if datetime.strptime(f.stem, "%Y-%m-%d").date() < cutoff:
                    out.append(f)
            except ValueError:
                continue
        return out

    def count_history_over_retention(self, keep_days: int = 365) -> int:
        """Read-only count of daily history files past *keep_days* (the remediation
        engine's measured deficit for the history prune — never a guess: it is the same
        listing ``prune_history`` deletes)."""
        return len(self._history_files_over_retention(keep_days))

    def prune_history(self, keep_days: int = 365) -> int:
        """Delete daily history files older than *keep_days*. Returns count deleted.

        Drops each deleted file's FTS row too: otherwise ``search()`` keeps returning
        snippets for files that no longer exist until the next full rebuild.
        """
        deleted = 0
        for f in self._history_files_over_retention(keep_days):
            try:
                f.unlink()
                deleted += 1
            except OSError:
                logger.debug("history prune: could not unlink %s", f, exc_info=True)
                continue
            self._unindex_file(f)
        if deleted:
            logger.info("Pruned %d history files older than %d days", deleted, keep_days)
        return deleted

    def read_recent_history(self, days: int = 14) -> str:
        """Load daily history with natural decay: recent=full, older=summary."""
        if days <= 0:
            return ""
        parts: list[str] = []
        today = datetime.now().date()
        for i in range(181):
            date = today - timedelta(days=i)
            path = self._history_dir / f"{date.strftime('%Y-%m-%d')}.md"
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                continue

            if i < days:
                parts.append(content)
            elif i < 61:
                parts.append(self._summarize_day(content))
            else:
                n = content.count("####")
                parts.append(f"# {date.strftime('%Y-%m-%d')}\n_{n} conversation(s)_")
        return "\n\n".join(parts)

    @staticmethod
    def _summarize_day(content: str) -> str:
        """Extract header + first entry from a daily history file."""
        sections = content.split("####")
        header = sections[0].strip()
        first = sections[1].strip() if len(sections) > 1 else ""
        result = header + ("\n#### " + first if first else "")
        n_more = len(sections) - 2
        if n_more > 0:
            result += f"\n_…{n_more} more entries_"
        return result

    def read_history(self, days: int = 14) -> str:
        return self.read_recent_history(days=days)

    # ── Context Injection ──

    def render_markdown_context(
        self,
        prefs_cap: int = 4_000,
        projects_cap: int = 6_000,
        history_cap: int = 25_000,
    ) -> list[str]:
        """The markdown projection's context blocks (prefs / projects / history)
        with source citations — the half of memory context THIS layer owns.

        Returns a list of block strings (empty list when nothing to show). The
        Memory Service composes these with the vector layer's L1/semantic/
        episodic blocks and wraps the whole thing — composition is L3's job, not
        this projection's (post-M2 this class no longer reaches the vector store).
        """

        def _cap(text: str, limit: int) -> str:
            """``text`` within ``limit``, cut after the last whole line that fits.

            A line cut in half reads as a different line — half a preference is another
            preference — so the cut falls between lines and says how many were left out; the
            source the block names holds them whole.
            """
            if len(text) <= limit:
                return text
            kept = text[:limit]
            kept = kept[: kept.rfind("\n") + 1]
            left = sum(1 for line in text[len(kept) :].splitlines() if line.strip())
            more = "1 more line" if left == 1 else f"{left} more lines"
            return f"{kept}…[{more} not shown: they are in the source named above]"

        parts: list[str] = []
        prefs = self.read_preferences()
        if prefs.strip() and prefs.strip() != _DEFAULT_PREFERENCES.strip():
            parts.append(
                f"## User Preferences\n"
                f"_[source: {from_home(self._preferences_file)}]_\n"
                f"{_cap(prefs, prefs_cap)}"
            )
        projects = self.read_projects()
        if projects.strip() and projects.strip() != _DEFAULT_PROJECTS.strip():
            parts.append(
                f"## Active Projects\n"
                f"_[source: {from_home(self._projects_file)}]_\n"
                f"{_cap(projects, projects_cap)}"
            )
        history = self.read_recent_history(days=14)
        if history.strip():
            parts.append(
                f"## Recent History\n"
                f"_[source: {from_home(self._history_dir)}, last 180 days decaying]_\n"
                f"{_cap(history, history_cap)}"
            )
        return parts

    def _persist(self, path: Path, content: str) -> None:
        """Write one memory file and index it: every change to a memory file comes through here.

        Refused inside work that derives from an Incognito or Temporary session, or that is an
        app's not given your memory (:mod:`personalclaw.memory_writes`): such work writes nothing
        to memory.
        """
        memory_writes.refuse_memory_write("a memory file")
        atomic_write(path, content)
        self._index_file(path, content)

    # ── FTS5 Full-Text Search ──
    #
    # See "The keyword index" above: every failure goes through `_repair`, which deletes nothing.

    def search_degraded(self) -> str:
        """Why keyword search cannot use this store's index now, or "" when it can."""
        with _DEGRADED_LOCK:
            return _DEGRADED.get(str(self._index_db), "")

    def _get_db(self) -> sqlite3.Connection:
        """The keyword index, open, with its table: caught up with the memory files first when it
        is recorded degraded, and repaired when it cannot be opened (:meth:`_repair`). Raises
        :class:`KeywordIndexUnavailable` when it cannot be used, having recorded why."""
        try:
            conn = self._try_create_db()
            if self.search_degraded():
                try:
                    self._fill(conn)
                except BaseException:
                    conn.close()
                    raise
                self._recovered()
            return conn
        except (sqlite3.Error, OSError) as exc:
            return self._repair(exc)

    def _try_create_db(self) -> sqlite3.Connection:
        """The index, open, its table made when it is missing. The connection is closed again when
        the table cannot be made, so a failure leaves nothing holding the file."""
        conn = connect(str(self._index_db))
        try:
            conn.execute(_CREATE_FTS)
        except BaseException:
            conn.close()
            raise
        return conn

    def _fill(self, conn: sqlite3.Connection) -> int:
        """Make the index hold exactly the memory files. Returns how many."""
        files = self._indexable_files()
        conn.execute(f"DELETE FROM {_FTS_TABLE}")
        conn.executemany(f"INSERT INTO {_FTS_TABLE} (path, content) VALUES (?, ?)", files)
        conn.commit()
        return len(files)

    def _with_index(self, op: Callable[[sqlite3.Connection], _T], *, query: bool = False) -> _T:
        """Run *op* on the keyword index and return what it returns.

        When *op* fails the index is repaired from the memory files (:meth:`_repair`; a file is
        written before it is indexed, so the change *op* was making is in them) and *op* runs once
        more on the repaired index. A *query*'s failure is mended only when SQLite says the index is
        damaged: any other is the query's own (a search the index cannot parse), raised as it came.
        Raises :class:`KeywordIndexUnavailable` when the index cannot be used.
        """
        conn = self._get_db()
        try:
            try:
                return op(conn)
            except (sqlite3.Error, OSError) as exc:
                if query and not _is_damage(exc):
                    raise
                conn.close()
                conn = self._repair(exc)
            try:
                return op(conn)
            except (sqlite3.Error, OSError) as exc:
                if query and not _is_damage(exc):
                    raise
                self._degrade(exc)
        finally:
            conn.close()

    def _repair(self, failure: BaseException) -> sqlite3.Connection:
        """Mend the keyword index after *failure*, and return it open, holding every memory file;
        or record why it cannot be used and raise :class:`KeywordIndexUnavailable`. Nothing is
        deleted, whatever failed:

        * a lock, the file's mode or the disk (:func:`_environment`): left as it is, and recorded;
        * a database SQLite reports damaged beyond the index (:func:`_damage`): moved aside whole,
          and a new one started (:meth:`_start_again`);
        * anything else: the index rebuilt in place, its table dropped and made again inside the
          same database from the memory files. Whatever else that database holds is not touched.
        """
        if _environment(failure):
            self._degrade(failure)
        with _REPAIR_LOCK:
            try:
                damage = _damage(self._index_db) if _is_damage(failure) else ""
                if damage:
                    conn = self._start_again(damage)
                else:
                    try:
                        conn = self._rebuild_in_place()
                    except (sqlite3.Error, OSError) as again:
                        damage = _damage(self._index_db) if _is_damage(again) else ""
                        if not damage:
                            raise
                        conn = self._start_again(damage)
            except (sqlite3.Error, OSError) as exc:
                self._degrade(exc)
        self._recovered()
        return conn

    def _rebuild_in_place(self) -> sqlite3.Connection:
        """Drop the index's table and make it again in the same database, holding every memory
        file. FTS5 drops the tables it keeps for the index with it, and nothing else."""
        conn = connect(str(self._index_db))
        try:
            conn.execute(f"DROP TABLE IF EXISTS {_FTS_TABLE}")
            conn.execute(_CREATE_FTS)
            self._fill(conn)
        except BaseException:
            conn.close()
            raise
        return conn

    def _start_again(self, damage: str) -> sqlite3.Connection:
        """Move the damaged database aside and start a new one, its index holding every memory
        file.

        In a working folder's partition the database is also where a vector store keeps the
        folder's memories, so every one this process holds on it is closed and let go first — this
        store's own, and the one kept for the folder (``context.forget_memory_store``): a
        connection left open on a moved database goes on writing into it, and into the log beside
        it, after the store has a new one. The next use opens a vector store on the new database
        (``ContextBuilder.get_memory_for``).
        """
        vectors = self._vector_store
        if vectors is not None and _same_file(vectors.db_path, self._index_db):
            self._vector_store = None
            try:
                vectors.close()
            except Exception:  # noqa: BLE001 — a store that will not close must not keep the file
                logger.debug("memory: the vector store on %s did not close", self._index_db)
        if not self._index_alone:
            from personalclaw import context

            context.forget_memory_store(self._workspace)
        moved = _move_aside(self._index_db)
        held = not self._index_alone
        try:
            conn = self._try_create_db()
            try:
                self._fill(conn)
            except BaseException:
                conn.close()
                raise
        except BaseException:
            _announce_set_aside(self._index_db, moved, damage, held_memories=held, rebuilt=False)
            raise
        _announce_set_aside(self._index_db, moved, damage, held_memories=held, rebuilt=True)
        return conn

    def _degrade(self, failure: BaseException) -> NoReturn:
        """Record that keyword search cannot use this store's index, and why; say so once (a
        WARNING when the reason is new); and raise :class:`KeywordIndexUnavailable`."""
        why = _why(failure)
        key = str(self._index_db)
        with _DEGRADED_LOCK:
            new = _DEGRADED.get(key) != why
            _DEGRADED[key] = why
        if new:
            logger.warning(
                "Memory keyword search is degraded for %s: %s. Nothing was deleted, and every "
                "memory is where it was; the index is rebuilt from the memory files once that is "
                "fixed.",
                from_home(self._index_db),
                why,
            )
        raise KeywordIndexUnavailable(why) from failure

    def _recovered(self) -> None:
        """The index was rebuilt from the memory files: keyword search is no longer degraded."""
        with _DEGRADED_LOCK:
            was = _DEGRADED.pop(str(self._index_db), None)
        if was is not None:
            logger.info(
                "Memory keyword search is back for %s: its index was rebuilt from the memory files",
                from_home(self._index_db),
            )

    def _index_file(self, path: Path, content: str) -> None:
        """Index a single file (incremental update). No-op without FTS5."""
        if not self._fts_available:
            return

        def _upsert(conn: sqlite3.Connection) -> None:
            path_str = str(path)
            conn.execute(f"DELETE FROM {_FTS_TABLE} WHERE path = ?", (path_str,))
            conn.execute(
                f"INSERT INTO {_FTS_TABLE} (path, content) VALUES (?, ?)", (path_str, content)
            )
            conn.commit()

        try:
            self._with_index(_upsert)
        except Exception:
            logger.debug("FTS index update failed", exc_info=True)

    def _unindex_file(self, path: Path) -> None:
        """Drop a file's FTS row (used when the file itself is deleted). No-op without FTS5."""
        if not self._fts_available:
            return

        def _drop(conn: sqlite3.Connection) -> None:
            conn.execute(f"DELETE FROM {_FTS_TABLE} WHERE path = ?", (str(path),))
            conn.commit()

        try:
            self._with_index(_drop)
        except Exception:
            logger.debug("FTS index delete failed", exc_info=True)

    def _indexable_files(self) -> list[tuple[str, str]]:
        """``(path, content)`` for every file that BELONGS in the FTS index. One listing
        shared by the rebuild and the desync measurement, so the measured deficit can
        never disagree with what a rebuild would produce."""
        files: list[tuple[str, str]] = []
        for path in (self._preferences_file, self._projects_file):
            if path.exists():
                files.append((str(path), path.read_text(encoding="utf-8")))
        if self._history_dir.exists():
            for path in self._history_dir.glob("*.md"):
                files.append((str(path), path.read_text(encoding="utf-8")))
        return files

    def fts_desync_count(self) -> int:
        """Number of files whose FTS row disagrees with disk — an EXACT measured count
        (never a size/mtime proxy), the remediation engine's deficit for the rebuild.

        Writes through this class index incrementally, so a non-zero count means content
        arrived or vanished out-of-band: a hand-edited memory file, a history file written
        by another path, or a row left behind by ``prune_history`` (which deletes files
        without touching the index). Those are exactly what a full rebuild reconciles.
        Returns 0 without FTS5 — there is no index to be out of sync — and for an index that
        cannot be used, which the Doctor's memory check reports with its reason instead.
        """
        if not self._fts_available:
            return 0
        on_disk = dict(self._indexable_files())

        def _rows(conn: sqlite3.Connection) -> dict[str, str]:
            return {
                str(row[0]): str(row[1])
                for row in conn.execute(f"SELECT path, content FROM {_FTS_TABLE}")
            }

        try:
            indexed = self._with_index(_rows)
        except Exception:
            logger.debug("FTS desync measure failed", exc_info=True)
            return 0  # unreadable index contributes no count (never a guess)
        divergent = sum(1 for p, c in on_disk.items() if indexed.get(p) != c)
        return divergent + sum(1 for p in indexed if p not in on_disk)

    def rebuild_index(self) -> int:
        """Rebuild the full FTS index from all memory files. Returns how many files it holds:
        0 when it could not be built (:meth:`search_degraded` says why).

        Without FTS5 the index doesn't exist; returns 0 rather than attempting a build.
        """
        if not self._fts_available:
            return 0
        try:
            return self._with_index(self._fill)
        except KeywordIndexUnavailable:
            return 0  # why is recorded, and was said once (`_degrade`)
        except Exception:
            logger.warning("FTS rebuild failed", exc_info=True)
            return 0

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Search memory using FTS5. Returns [{path, snippet, rank}], or [] without FTS5."""
        if not self._fts_available:
            return []

        def _match(conn: sqlite3.Connection) -> list[dict]:
            cursor = conn.execute(
                f"SELECT path, snippet({_FTS_TABLE}, 1, '>>>', '<<<', '...', 32), rank "
                f"FROM {_FTS_TABLE} WHERE {_FTS_TABLE} MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            )
            return [
                {"path": row[0], "snippet": row[1], "rank": row[2]} for row in cursor.fetchall()
            ]

        try:
            return self._with_index(_match, query=True)
        except Exception:
            logger.debug("FTS search failed", exc_info=True)
            return []
