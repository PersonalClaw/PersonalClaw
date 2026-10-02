"""A store's one connection answers every thread that reads it at the same time.

Memory Studio reloads after a save by asking for its entities, their graph, the slots and the
proposals together. Each request reads ``memory.db`` on an executor thread, and the entities route
reads two things at once by itself. They all go through the store's ONE connection, opened with
``check_same_thread=False`` — which only turns the driver's ownership check off. The driver is not
safe for two threads inside it on one connection. The reload answered 500 with
``IndexError: tuple index out of range`` (a row whose columns were not there), a ``COUNT(*)``
returned no row at all, ``InterfaceError: bad parameter or other API misuse`` came back, and —
worse than any of them — a 200 listed 17 or 60 entities for a store that holds 40.

The knowledge, lexicon and code-index stores keep one shared connection the same way. Each is now a
``SharedConnection``: every call into the driver on it takes the connection's lock.
"""

from __future__ import annotations

import ast
import asyncio
import collections
import threading
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import memory as mem_handlers
from personalclaw.memory_service import MemoryService
from personalclaw.sqlite_compat import SharedConnection, connect_shared, sqlite3
from personalclaw.vector_memory import VectorMemoryStore

_SRC = Path(__file__).resolve().parents[1] / "src"

_ENTITIES = 40


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    s = VectorMemoryStore(db_path=tmp_path / "memory.db")
    s.init()
    # Pinned rather than read from config: the graph must be on whatever the ambient home says.
    s.graph_enabled = True
    for i in range(_ENTITIES):
        s.graph.upsert_entity(f"Person {i:02d}", "person", aliases=[f"P{i}"], source="user")
    return s


# ── the reload the Studio makes ────────────────────────────────────────────────────────────────


async def _studio(monkeypatch, store) -> TestClient:
    svc = MemoryService.over_vector_store(store)
    monkeypatch.setattr(mem_handlers, "_get_service", lambda _state: svc)
    app = web.Application()
    app["state"] = object()
    app.router.add_get("/api/memory/entities", mem_handlers.api_memory_entities)
    app.router.add_get("/api/memory/graph/entities", mem_handlers.api_memory_entity_graph)
    app.router.add_get("/api/memory/slots", mem_handlers.api_memory_slots)
    app.router.add_get(
        "/api/memory/entities/proposals", mem_handlers.api_memory_entity_proposals_list
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


_RELOAD = (
    "/api/memory/entities",
    "/api/memory/graph/entities",
    "/api/memory/slots",
    "/api/memory/entities/proposals",
)


@pytest.mark.asyncio
async def test_a_studio_reload_answers_every_read(monkeypatch, store):
    """The Studio's reload, many times over: every read answers, and answers with every entity."""
    client = await _studio(monkeypatch, store)
    failures: list[str] = []
    rounds = 0
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            rounds += 1
            responses = await asyncio.gather(*(client.get(path) for path in _RELOAD))
            for path, response in zip(_RELOAD, responses):
                if response.status != 200:
                    failures.append(f"{path} → {response.status}")
                    continue
                body = await response.json()
                if path == "/api/memory/entities":
                    if len(body["entities"]) != _ENTITIES:
                        failures.append(f"{path} listed {len(body['entities'])} entities")
                    if body["summary"].get("entities") != _ENTITIES:
                        failures.append(f"{path} summary counted {body['summary']}")
    finally:
        await client.close()
    assert rounds >= 20, f"the reload ran only {rounds} times — nothing was measured"
    assert failures == [], f"{len(failures)} failed reads in {rounds} reloads: {failures[:5]}"


def test_four_threads_on_one_store_read_whole_rows(store):
    """The store directly: four kinds of read, two threads each, for a second."""
    graph = store.graph
    errors: collections.Counter[str] = collections.Counter()
    wrong: list[str] = []
    done: collections.Counter[str] = collections.Counter()
    stop = threading.Event()

    def entities() -> None:
        n = len(graph.entities())
        if n != _ENTITIES:
            wrong.append(f"entities() listed {n}")

    def summary() -> None:
        counted = graph.summary()["entities"]
        if counted != _ENTITIES:
            wrong.append(f"summary() counted {counted}")

    def topology() -> None:
        nodes = len(graph.entity_graph()["nodes"])
        if nodes != _ENTITIES:
            wrong.append(f"entity_graph() drew {nodes} nodes")

    def stats() -> None:
        for entity in graph.entities()[:5]:
            graph.stats(entity.id)

    def run(fn) -> None:
        while not stop.is_set():
            try:
                fn()
                done[fn.__name__] += 1
            except Exception as exc:  # noqa: BLE001 — every failure is the finding
                errors[f"{fn.__name__}: {type(exc).__name__}: {exc}"] += 1

    threads = [
        threading.Thread(target=run, args=(fn,), daemon=True)
        for fn in (entities, summary, topology, stats)
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    time.sleep(1.0)
    stop.set()
    for t in threads:
        t.join(10)

    assert all(
        done[name] >= 20 for name in ("entities", "summary", "topology", "stats")
    ), f"a reader barely ran, so nothing was measured: {dict(done)}"
    assert not errors, f"reads failed: {errors.most_common(5)}"
    assert not wrong, f"reads answered wrong: {wrong[:5]}"


# ── what every shared store opens ──────────────────────────────────────────────────────────────


def test_every_store_that_shares_one_connection_opens_a_shared_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.codegraph.index import CodeGraphIndex
    from personalclaw.knowledge.store import KnowledgeStore
    from personalclaw.lexicon.store import LexiconStore

    memory = VectorMemoryStore(db_path=tmp_path / "memory.db")
    memory.init()
    knowledge = KnowledgeStore(str(tmp_path / "knowledge.db"))
    lexicon = LexiconStore(str(tmp_path / "lexicon.db"))
    code = CodeGraphIndex(str(tmp_path), db_path=tmp_path / "code.db")
    for name, conn in (
        ("memory", memory.db),
        ("knowledge", knowledge.db),
        ("lexicon", lexicon.db),
        ("code index", code.db),
    ):
        assert isinstance(conn, SharedConnection), f"the {name} store's connection is not shared"


#: Modules that open a connection for several threads and serialize EVERY use of it with a lock of
#: their own, around whole transactions. Nothing else may open one without ``connect_shared``.
_OWN_LOCK = {
    "personalclaw/session_search.py",
    "personalclaw/learning/staging.py",
}


def _raw_shared_opens(source: str) -> int:
    """How many ``connect(..., check_same_thread=False)`` calls ``source`` makes."""
    count = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "connect":
            continue
        for kw in node.keywords:
            if (
                kw.arg == "check_same_thread"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is False
            ):
                count += 1
    return count


def test_a_connection_shared_between_threads_is_opened_shared():
    """The rail: a store that keeps one connection for several threads opens it with
    ``connect_shared`` (or serializes every use itself, and is listed above)."""
    # Positive control on the predicate: it must still see the raw form it exists to refuse.
    assert _raw_shared_opens("sqlite3.connect(p, check_same_thread=False)") == 1
    assert _raw_shared_opens("connect_shared(p, isolation_level=None)") == 0

    found: dict[str, int] = {}
    for path in (_SRC / "personalclaw").rglob("*.py"):
        rel = path.relative_to(_SRC).as_posix()
        if rel == "personalclaw/sqlite_compat.py":
            continue  # where the shared connection is made
        n = _raw_shared_opens(path.read_text(encoding="utf-8"))
        if n:
            found[rel] = n
    # And on the population: the two lock-holding modules are found, so the scan is not vacuous.
    assert _OWN_LOCK <= set(found), f"the scan no longer finds the lock-holding modules: {found}"
    stray = sorted(set(found) - _OWN_LOCK)
    assert (
        stray == []
    ), f"these open a connection for several threads without connect_shared: {stray}"


# ── the connection itself behaves like any other ───────────────────────────────────────────────


def test_a_shared_connection_behaves_like_a_plain_one(tmp_path):
    conn = connect_shared(str(tmp_path / "s.db"), isolation_level=None)
    conn.row_factory = sqlite3.Row
    assert isinstance(conn, sqlite3.Connection)
    conn.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT);")
    conn.isolation_level = ""
    with conn:
        conn.executemany("INSERT INTO t (name) VALUES (?)", [("a",), ("b",), ("c",)])
    cur = conn.execute("INSERT INTO t (name) VALUES (?)", ("d",))
    assert cur.lastrowid == 4
    conn.commit()
    assert conn.execute("SELECT name FROM t WHERE id = 1").fetchone()["name"] == "a"
    assert [r["name"] for r in conn.execute("SELECT name FROM t ORDER BY id")] == [
        "a",
        "b",
        "c",
        "d",
    ]
    cur = conn.cursor()
    cur.arraysize = 2
    cur.execute("SELECT name FROM t ORDER BY id")
    assert [r["name"] for r in cur.fetchmany()] == ["a", "b"]
    assert [r["name"] for r in cur.fetchall()] == ["c", "d"]
    conn.close()
