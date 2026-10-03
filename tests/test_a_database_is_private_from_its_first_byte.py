"""A database PersonalClaw keeps in its home is private from its first byte, like every file there.

The home rule (`atomic_write`) writes every file under the home 0600 in a 0700 folder, and it left
databases out: "the 0700 home it sits in is what shields it". SQLite makes a database's file, and
the write-ahead log and shared-memory files it keeps beside it, with the umask's mode, 0644. So a
memory partition held `learning.db`, its `-wal` and its `-shm` readable by every other account on
the machine, in folders made 0755, beside the owner's 0600 settings.

SQLite makes a database's journal, write-ahead log and shared-memory files with the mode of the
database file itself. So a store that makes its database file 0600 before SQLite first opens it,
in a folder made 0700, keeps every one of them 0600 from the first byte: every store opens its
database through ``sqlite_compat.connect`` or ``connect_shared``, which do that, and a database
left readable by an earlier version is tightened, with the files beside it, the next time it
opens. Outside the home a database is the owner's to share, and SQLite's own mode stands.
"""

from __future__ import annotations

import ast
import os
import stat
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from personalclaw.sqlite_compat import sqlite3

USUAL_UMASK = 0o022
SIDECARS = ("-wal", "-shm", "-journal")


@pytest.fixture(autouse=True)
def _the_usual_umask() -> Iterator[None]:
    """The umask most accounts run with, which leaves a new file readable by everyone."""
    previous = os.umask(USUAL_UMASK)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = (tmp_path / "home").resolve()
    home.mkdir(mode=0o700)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _files_of(db: Path) -> list[Path]:
    """The database and each file SQLite keeps beside it that is there now."""
    return [p for p in (db, *(db.with_name(db.name + s) for s in SIDECARS)) if p.exists()]


def _loose(paths) -> dict[str, str]:
    """Each path that is not private: a folder not 0700, a file not 0600."""
    return {str(p): oct(_mode(p)) for p in paths if _mode(p) != (0o700 if p.is_dir() else 0o600)}


# ── the stores ─────────────────────────────────────────────────────────────────────────────────


def test_the_learning_database_and_the_files_beside_it_are_private(home):
    """🔴 Red on integration: `learning.db`, its `-wal` and its `-shm` were 0644."""
    from personalclaw.learning.staging import StagingStore

    store = StagingStore(home)
    try:
        assert store.stage(cadence="turn", kind="preference", content="Prefers short answers")
        files = _files_of(store.path)
        assert [p.name for p in files] == ["learning.db", "learning.db-wal", "learning.db-shm"]
        assert _loose(files) == {}
    finally:
        store.close()


def test_a_memory_partitions_files_and_folders_are_private(home):
    """What a session keeps in a working folder's partition — its memory files, its memory
    database and the evidence for its lessons, with the files SQLite keeps beside them while they
    are open — is 0600, and every folder down to it 0700.

    🔴 Red on integration: the partition folder and `memory/history` were 0755, `learning.db*` and
    `memory_index.db-wal`/`-shm` 0644."""
    from personalclaw.config.loader import memory_dir_for_cwd
    from personalclaw.context import ContextBuilder
    from personalclaw.memory_service import service_for
    from personalclaw.vector_memory import VectorMemoryStore

    folder = "/srv/example/newsletter"
    partition = memory_dir_for_cwd(folder)
    memory = ContextBuilder.get_memory_for(folder)
    vs = VectorMemoryStore(db_path=partition / "memory_index.db")
    vs.init()
    memory.vector_store = vs
    try:
        assert service_for(memory).write_lesson(
            "Keep the changelog current", source="user_explicit"
        )
        for db in ("learning.db", "memory_index.db"):
            assert (partition / f"{db}-wal").is_file(), f"{db} is not open in WAL mode"

        folders = [partition.parent.parent, partition.parent, partition]
        assert _loose([*folders, *partition.rglob("*")]) == {}
    finally:
        vs.close()


def test_a_database_left_readable_is_made_private_when_its_store_opens_it(home):
    """A home an earlier version wrote holds its databases 0644, the files beside them too.

    🔴 Red on integration: they stayed 0644."""
    from personalclaw.learning.staging import StagingStore

    db = home / "learning.db"
    earlier = sqlite3.connect(str(db))
    try:
        earlier.execute("PRAGMA journal_mode=WAL")
        earlier.execute("CREATE TABLE IF NOT EXISTS earlier (x)")
        earlier.commit()
        for path in _files_of(db):
            os.chmod(path, 0o644)
        assert len(_files_of(db)) == 3

        store = StagingStore(home)
        try:
            assert store.pending_count() == 0
            assert _loose(_files_of(db)) == {}
        finally:
            store.close()
    finally:
        earlier.close()


def test_a_database_outside_the_home_keeps_the_mode_sqlite_gives_it(home, tmp_path):
    """Outside the home a file is its owner's to share: the rule is the home's, not every
    folder's."""
    from personalclaw.learning.staging import StagingStore

    store = StagingStore(tmp_path / "elsewhere")
    try:
        assert store.pending_count() == 0
        assert _mode(store.path) == 0o666 & ~USUAL_UMASK
    finally:
        store.close()


def _loops(home: Path) -> tuple[Path, Callable[[], None]]:
    from personalclaw.loop import store

    store.list_all()
    return home / "loop" / "loops.db", lambda: None


def _workflow_runs(home: Path) -> tuple[Path, Callable[[], None]]:
    from personalclaw.workflows import store

    store.list_runs()
    return home / "workflows" / "runs.db", lambda: None


def _session_search(home: Path) -> tuple[Path, Callable[[], None]]:
    from personalclaw import session_search

    session_search.reset_for_tests()
    if session_search._connect() is None:
        pytest.skip("this SQLite has no full-text search")
    return session_search.db_path(), session_search.reset_for_tests


def _knowledge(home: Path) -> tuple[Path, Callable[[], None]]:
    from personalclaw.knowledge.store import KnowledgeStore

    folder = home / "workspace" / "knowledge"
    folder.mkdir(parents=True, mode=0o700)
    store = KnowledgeStore(str(folder / "knowledge.db"))
    return folder / "knowledge.db", store.close


def _lexicon(home: Path) -> tuple[Path, Callable[[], None]]:
    from personalclaw.lexicon.store import LexiconStore

    folder = home / "workspace" / "lexicon"
    folder.mkdir(parents=True, mode=0o700)
    store = LexiconStore(str(folder / "lexicon.db"))
    return folder / "lexicon.db", store.db.close


def _code_graph(home: Path) -> tuple[Path, Callable[[], None]]:
    from personalclaw.codegraph.index import CodeGraphIndex

    index = CodeGraphIndex("/srv/example/newsletter")
    index.db  # noqa: B018 — the first use opens it
    return index.db_path, index.close


@pytest.mark.parametrize(
    "open_store",
    [_loops, _workflow_runs, _session_search, _knowledge, _lexicon, _code_graph],
    ids=["loops", "workflow-runs", "session-search", "knowledge", "lexicon", "code-graph"],
)
def test_every_store_makes_its_database_private(home, open_store):
    """🔴 Red on integration: each of them was 0644."""
    db, close = open_store(home)
    try:
        assert db.is_file()
        assert _loose([db.parent, *_files_of(db)]) == {}
    finally:
        close()


# ── the rail ──────────────────────────────────────────────────────────────────────────────────

_SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: Where the choice of driver is made, and where the private opener lives.
_OPENER = "sqlite_compat.py"

#: The durability tooling. Each opens a database a store keeps — to checkpoint, vacuum, copy or
#: merge it — or writes the copy it takes into a private temporary folder, and none of them makes a
#: store's database.
_TOOLING = frozenset(
    {"snapshot.py", "portability.py", "durability/shards.py", "durability/footprint.py"}
)


def _driver_names(tree: ast.AST) -> set[str]:
    """The names a module binds the driver to (``from …sqlite_compat import sqlite3``)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "personalclaw.sqlite_compat":
            names |= {a.asname or a.name for a in node.names if a.name == "sqlite3"}
    return names


def _file_opens(path: Path) -> list[int]:
    """The lines that open a database FILE with the driver's own ``connect``: not ``:memory:``,
    and not a URI (``uri=True``, which every read-only open passes)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    driver = _driver_names(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in driver
        ):
            continue
        if any(kw.arg == "uri" for kw in node.keywords):
            continue
        first = node.args[0] if node.args else None
        if isinstance(first, ast.Constant) and first.value == ":memory:":
            continue
        lines.append(node.lineno)
    return sorted(lines)


def test_a_store_opens_its_database_through_the_private_opener():
    """🔴 Red on integration: the staging log, the memory index, session search, the loop store and
    the workflow store opened theirs with the driver's own ``connect``."""
    found = {}
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        if rel == _OPENER or rel in _TOOLING:
            continue
        lines = _file_opens(path)
        if lines:
            found[rel] = lines
    assert found == {}, (
        "open a store's database with personalclaw.sqlite_compat.connect (or connect_shared), "
        f"which makes it private before SQLite first opens it: {found}"
    )


def test_the_rail_sees_a_file_open():
    """The positive control: the tooling it exempts does open files with the driver's own
    ``connect``, so a scan that finds none of them is blind."""
    assert all(_file_opens(_SRC / rel) for rel in _TOOLING)
