"""An Incognito chat writes nothing to long-term memory by any path, and nothing of it is embedded.

The defect this pins: a chat started Incognito held a weekly physio log (pain scores, the
physiotherapist's rule). When its session idled out, the session sweep's end-of-session callback
consolidated it like any other: a working-memory row, three episodic records filed for every chat,
two persona notes, each embedded with the configured embedding model, and two of them were then
recalled into later, ordinary chats. Nothing on the consolidation path asked what mode the session
was in.

The behaviour now, every half of it driven against the real stores:

* every way a session's transcript reaches consolidation (the idle sweep's expiry, a channel's
  "end session", a request to consolidate now, the per-turn and idle passes, the command line)
  keeps nothing from an Incognito chat: no row in the memory database, no line in the markdown
  memory, no model call, no embedding call;
* the stores themselves refuse: inside work that derives from such a chat, the memory, knowledge
  and vocabulary databases refuse every change, the memory files are not written, and the embedding
  functions embed nothing; the API answers a session's refused write 403;
* a session whose mode cannot be read keeps nothing, while a normal chat consolidates exactly as
  before, and each record it writes names the chat it came from;
* what an earlier version kept from an Incognito chat is removed when the gateway starts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from personalclaw import memory_writes, session_restrictions
from personalclaw.config import AppConfig
from personalclaw.history import ConversationLog, HistoryConsolidator, session_path
from personalclaw.memory import MemoryStore
from personalclaw.session import SessionManager
from personalclaw.vector_memory import VectorMemoryStore

INCOGNITO = "dashboard:chat-7-1700000000"
NORMAL = "dashboard:chat-8-1700000100"

#: What the consolidation model answers for a chat about a weekly exercise log. Invented content.
_EXTRACTED = {
    "history_entry": "The user logged a week of exercise sessions and adjusted the last one.",
    "episodic": [
        {"text": "Weekly log: Monday easy, Wednesday skipped, Friday moderate.", "tags": ["log"]},
        {"text": "Applied the coach's rule: add one set when the effort was low.", "tags": []},
    ],
    "semantic": [{"key": "user.pref.log_format", "value": "weekday list", "confidence": 0.9}],
    "self_persona": ["Applied a conditional rule to a training log accurately."],
    "lessons": [{"rule": "Keep the weekly log as a weekday list.", "category": "preference"}],
}


class _Counter:
    """An embedding function that counts its calls."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, text: str) -> list[float]:
        self.calls += 1
        # A vector per text, so two different texts are not taken for one (the store dedups).
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [b - 127.5 for b in digest[:16]]


@pytest.fixture
def home(tmp_path: Path):
    """The test's home: a conversation log, markdown memory and a memory database, the same
    pieces the gateway wires, with an embedding function that counts and a model that answers
    :data:`_EXTRACTED`."""
    log = ConversationLog()
    log.init()
    markdown = MemoryStore(workspace=tmp_path / "workspace")
    markdown.init()
    store = VectorMemoryStore(db_path=tmp_path / "memory.db", confidence_threshold=0.0)
    embed = _Counter()
    store.embed_fn = embed
    store.init()
    markdown.vector_store = store
    consolidator = HistoryConsolidator(
        log=log, memory=markdown, vector_store=store, migrated=True, history_idle_secs=0
    )
    model = AsyncMock(return_value=json.loads(json.dumps(_EXTRACTED)))
    consolidator._call_llm = model  # type: ignore[method-assign]
    yield {
        "log": log,
        "markdown": markdown,
        "store": store,
        "embed": embed,
        "model": model,
        "consolidator": consolidator,
        "workspace": tmp_path / "workspace",
    }
    session_restrictions.clear(INCOGNITO)
    session_restrictions.clear(NORMAL)
    store.close()


def _chat(log: ConversationLog, key: str, mode: str | None) -> None:
    """A chat's transcript as the dashboard saves it: messages, and the mode in its metadata."""
    log.append(key, "user", "Here is this week's log: Monday easy, Wednesday skipped.")
    log.append(key, "assistant", "Noted. Friday can take one more set by your coach's rule.")
    log.append(key, "user", "Good, apply it.")
    if mode is not None:
        log.update_metadata(key, {"memory_mode": mode})


def _rows(store: VectorMemoryStore) -> dict[str, int]:
    tables = ("semantic_memory", "episodic_memories", "memory_events", "mem_links")
    return {t: store.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def _nothing_kept(h: dict) -> None:
    assert _rows(h["store"]) == {
        "semantic_memory": 0,
        "episodic_memories": 0,
        "memory_events": 0,
        "mem_links": 0,
    }
    assert h["embed"].calls == 0, "text from the Incognito chat was sent to the embedding model"
    assert h["model"].await_count == 0, "the Incognito transcript was handed to a model"
    history = h["workspace"] / "memory" / "history"
    assert not list(history.glob("*.md")), "the daily history recorded the Incognito chat"


# ── every way a transcript reaches consolidation ────────────────────────────────────────────


def test_an_incognito_chat_that_idles_out_leaves_nothing_in_memory(home) -> None:
    """The reported path: the session sweep expires the idle chat and calls the end-of-session
    callback the gateway wires to ``consolidate_session``."""
    _chat(home["log"], INCOGNITO, "incognito")
    cfg = AppConfig()

    async def run() -> None:
        def factory(*_args, **_kw):
            provider = AsyncMock()
            provider.context_usage_pct = lambda: 0.0
            provider.compacts_automatically = False
            return provider

        manager = SessionManager(cfg, provider_factory=factory)
        manager.set_session_expire_callback(home["consolidator"].consolidate_session)
        await manager.get_or_create(INCOGNITO)
        manager.release(INCOGNITO)
        manager._sessions[INCOGNITO].last_used = time.monotonic() - 99_999
        await manager._expire_idle(1)
        assert INCOGNITO not in manager._sessions, "the sweep did not expire the idle chat"
        await manager.close_all()

    asyncio.run(run())
    _nothing_kept(home)


@pytest.mark.parametrize(
    "seam",
    [
        "a channel ending the session",
        "consolidate now",
        "the consolidate request",
        "the per-turn pass",
        "the idle pass",
    ],
)
def test_no_trigger_consolidates_an_incognito_chat(home, seam: str) -> None:
    _chat(home["log"], INCOGNITO, "incognito")
    consolidator: HistoryConsolidator = home["consolidator"]

    async def run() -> None:
        if seam == "a channel ending the session":
            assert await consolidator.consolidate_session(INCOGNITO) is False
        elif seam == "consolidate now":
            assert await consolidator.consolidate_now(INCOGNITO) is False
        elif seam == "the consolidate request":
            # What POST /api/memory/consolidate and the eval runner start.
            consolidator._running.add(INCOGNITO)
            await consolidator._consolidate(INCOGNITO, include_history=True)
        elif seam == "the per-turn pass":
            for _ in range(40):
                home["log"].append(INCOGNITO, "user", "one more line")
            consolidator.maybe_consolidate(INCOGNITO)
            await asyncio.gather(*consolidator._tasks, return_exceptions=True)
        else:
            consolidator._last_activity[INCOGNITO] = 0.0
            consolidator.check_idle_sessions()
            await asyncio.gather(*consolidator._tasks, return_exceptions=True)

    asyncio.run(run())
    _nothing_kept(home)
    assert INCOGNITO not in consolidator._running, "a skipped pass left its key marked running"


def test_a_temporary_chat_keeps_nothing_either(home) -> None:
    _chat(home["log"], INCOGNITO, "temporary")
    asyncio.run(home["consolidator"].consolidate_session(INCOGNITO))
    _nothing_kept(home)


def test_a_channel_thread_marked_incognito_keeps_nothing_after_a_restart(home) -> None:
    """A channel marks its Incognito thread in the in-process registry. The transcript records the
    mark, so a gateway that restarted (an empty registry) still reads the thread as Incognito."""
    key = "slack:C0TEST:1700000000.000100"
    session_restrictions.mark_incognito(key)
    _chat(home["log"], key, None)
    session_restrictions.clear(key)  # the restart
    assert home["log"].recorded_memory_mode(key) == "incognito"
    asyncio.run(home["consolidator"].consolidate_session(key))
    _nothing_kept(home)


def test_the_consolidate_command_says_why_it_skips_an_incognito_chat(home, capsys) -> None:
    import argparse

    from personalclaw import cli_server

    _chat(home["log"], INCOGNITO, "incognito")
    consolidator = home["consolidator"]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cli_server, "_build_consolidator", lambda: (None, consolidator, home["log"]))
        asyncio.run(cli_server._consolidate_cmd(argparse.Namespace(all=False, key=INCOGNITO)))
        asyncio.run(cli_server._consolidate_cmd(argparse.Namespace(all=True, key="")))
    out = capsys.readouterr().out
    assert "already in flight" not in out.lower()
    assert "it is Incognito or Temporary" in out
    _nothing_kept(home)


# ── a mode that cannot be read; a normal chat ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "first_line", ["not a metadata line {", '{"_type": "metadata", "memory_mode": "private"}']
)
def test_a_session_whose_mode_cannot_be_read_keeps_nothing(home, first_line: str) -> None:
    _chat(home["log"], INCOGNITO, None)
    path = session_path(INCOGNITO)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text(first_line + "\n" + "".join(lines[1:]), encoding="utf-8")
    home["log"]._invalidate_cache(INCOGNITO)

    asyncio.run(home["consolidator"].consolidate_session(INCOGNITO))
    _nothing_kept(home)


def test_a_normal_chat_still_consolidates_and_its_records_name_it(home) -> None:
    _chat(home["log"], NORMAL, "persistent")
    assert asyncio.run(home["consolidator"].consolidate_session(NORMAL)) is True

    store: VectorMemoryStore = home["store"]
    rows = _rows(store)
    assert rows["episodic_memories"] >= 2 and rows["semantic_memory"] >= 2
    assert home["embed"].calls > 0 and home["model"].await_count >= 1
    assert list((home["workspace"] / "memory" / "history").glob("*.md"))
    sources = {
        r[0]
        for table in ("episodic_memories", "semantic_memory")
        for r in store.db.execute(f"SELECT source_session FROM {table}").fetchall()
    }
    assert sources == {NORMAL}, sources


def test_a_loop_an_incognito_turn_happened_to_start_still_keeps_a_normal_chat(home) -> None:
    """The session sweep's loop is started by whichever turn first opens a session, and a task
    keeps the context it was started in. A normal chat it ends later is still that chat's work."""
    _chat(home["log"], NORMAL, "persistent")

    async def run() -> bool:
        with memory_writes.derived_from(INCOGNITO, memory_mode="incognito"):
            loop = asyncio.create_task(home["consolidator"].consolidate_session(NORMAL))
        return await loop

    assert asyncio.run(run()) is True
    assert _rows(home["store"])["episodic_memories"] >= 2


# ── the stores refuse, whoever asks ─────────────────────────────────────────────────────────


def test_the_memory_store_refuses_every_write_in_an_incognito_chats_work(home) -> None:
    _chat(home["log"], INCOGNITO, "incognito")
    store: VectorMemoryStore = home["store"]
    with memory_writes.derived_from(INCOGNITO):
        with pytest.raises(memory_writes.MemoryWriteRefused):
            store.write_episodic("A fact the agent tried to keep from the Incognito chat.")
        with pytest.raises(memory_writes.MemoryWriteRefused):
            store.set_semantic("user.pref.colour", "green", 0.9, "agent")
        with pytest.raises(memory_writes.MemoryWriteRefused):
            home["markdown"].append_history("A note about the Incognito chat.")
        # Reading still works, and leaves no mark on what it read.
        assert store.search_episodic(query_text="fact", limit=5) == []
        store.record_recall(["user.pref.colour"])
    _nothing_kept(home)


def test_an_incognito_chat_still_reads_memory_and_leaves_no_mark_on_it(home) -> None:
    """Incognito reads memory for context, as before; the reads are lexical (nothing of the chat
    is embedded to search with) and change nothing: no recall counts, no access stamps, no
    volunteer log, no history events."""
    from personalclaw.memory_service import MemoryService

    store: VectorMemoryStore = home["store"]
    store.write_episodic("The user swims on Tuesdays at the community pool.", tags=["swim"])
    store.set_semantic("user.pref.swim_day", "Tuesday", 0.9, "user_explicit")
    store.write_lesson("Answer swim questions with the pool schedule.", category="preference")
    before = _rows(store)
    snapshot = store.db.execute(
        "SELECT key, recall_count FROM semantic_memory ORDER BY key"
    ).fetchall()
    embedded = home["embed"].calls
    _chat(home["log"], INCOGNITO, "incognito")
    svc = MemoryService.over_vector_store(store)
    with memory_writes.derived_from(INCOGNITO):
        found = store.search_episodic(query_text="swims Tuesdays pool", limit=5)
        assert [r["text"] for r in found] == ["The user swims on Tuesdays at the community pool."]
        assert "Tuesday" in svc.semantic_context("when does the user swim")
        svc.episodic_context("swims pool")
        svc.lessons_context("swim")
        svc.active_recall("swim pool Tuesday")
        svc.push_context(["The pool on Tuesday"], session_key=INCOGNITO)
        svc.recall_with_provenance(query_text="pool")
        store.get_l1_manifest()
    assert home["embed"].calls == embedded, "the Incognito chat's words were embedded to search"
    assert _rows(store) == before
    assert (
        store.db.execute("SELECT key, recall_count FROM semantic_memory ORDER BY key").fetchall()
        == snapshot
    )
    assert (
        store.db.execute(
            "SELECT COUNT(*) FROM episodic_memories WHERE last_accessed_at IS NOT NULL"
        ).fetchone()[0]
        == 0
    )
    assert store.db.execute("SELECT COUNT(*) FROM mem_volunteer_events").fetchone()[0] == 0


def test_a_workspace_or_knowledge_write_is_refused_and_ordinary_work_is_not(tmp_path) -> None:
    from personalclaw.knowledge.store import KnowledgeStore
    from personalclaw.lexicon.store import LexiconStore

    log = ConversationLog()
    log.init()
    _chat(log, INCOGNITO, "incognito")
    knowledge = KnowledgeStore(str(tmp_path / "knowledge.db"))
    lexicon = LexiconStore(str(tmp_path / "lexicon.db"))
    with memory_writes.derived_from(INCOGNITO):
        with pytest.raises(memory_writes.MemoryWriteRefused):
            knowledge.create_typed_item(item_type="note", title="Exercise log", content="Monday")
        with pytest.raises(memory_writes.MemoryWriteRefused):
            lexicon.bump_weight("coach")
        assert knowledge.search_items_fts("Exercise") == []
    # The owner's own work, outside any chat, is untouched.
    assert knowledge.create_typed_item(item_type="note", title="Exercise log", content="Monday")
    with memory_writes.derived_from(NORMAL, memory_mode="persistent"):
        assert knowledge.create_typed_item(item_type="note", title="Other note", content="Tuesday")


def test_an_incognito_chats_text_is_never_embedded() -> None:
    from personalclaw.embedding_providers import registry
    from personalclaw.embedding_providers.base import EmbeddingProvider

    calls: list[object] = []

    class _Fake(EmbeddingProvider):
        @property
        def name(self) -> str:
            return "fake-embed"

        @property
        def display_name(self) -> str:
            return "Fake embeddings"

        async def is_available(self) -> bool:
            return True

        async def embed(self, text: str, model: str = "") -> list[float] | None:
            calls.append(text)
            return [0.5, 0.5]

        async def embed_batch(self, texts: list[str], model: str = "") -> list[list[float] | None]:
            calls.append(texts)
            return [[0.5, 0.5] for _ in texts]

    registry.register_provider(_Fake())
    try:
        one = registry.embed_fn_for("fake-embed", "m")
        many = registry.embed_many_fn_for("fake-embed", "m")
        assert one is not None and many is not None
        with memory_writes.derived_from(INCOGNITO, memory_mode="incognito"):
            assert one("the log") is None
            assert many(["the log", "the rule"]) == [None, None]
        assert calls == [], "the embedding provider was called for an Incognito chat"
        with memory_writes.derived_from(NORMAL, memory_mode="persistent"):
            assert one("an ordinary note") == [0.5, 0.5]
        assert calls == ["an ordinary note"]
    finally:
        registry.unregister_provider("fake-embed")


def test_a_turn_and_everything_it_starts_derive_from_its_chat() -> None:
    """The turn engine runs each turn inside its chat's scope; a task the turn starts and a worker
    thread it hands work to (through the gateway's executor) carry it too."""
    from personalclaw.dashboard import chat_runner
    from personalclaw.memory_writes import runs_as_its_session

    assert chat_runner.run_chat.__wrapped__.__name__ == "run_chat"  # type: ignore[attr-defined]
    assert chat_runner.run_chat.__code__ is runs_as_its_session(_noop_turn).__code__

    seen: dict[str, bool] = {}

    @runs_as_its_session
    async def turn(state, session, message, **_kw) -> None:
        seen["turn"] = memory_writes.writes_refused()
        seen["task"] = await asyncio.create_task(asyncio.sleep(0, memory_writes.writes_refused()))
        loop = asyncio.get_running_loop()
        seen["thread"] = await loop.run_in_executor(None, memory_writes.writes_refused)

    class _Session:
        key = "chat-7-1700000000"
        memory_mode = "incognito"

    class _State:
        conversation_log = None

    async def drive(mode: str) -> dict[str, bool]:
        memory_writes.carry_scope_into_worker_threads(asyncio.get_running_loop())
        _Session.memory_mode = mode
        seen.clear()
        await turn(_State(), _Session(), "hello")
        return dict(seen)

    assert asyncio.run(drive("incognito")) == {"turn": True, "task": True, "thread": True}
    assert asyncio.run(drive("persistent")) == {"turn": False, "task": False, "thread": False}


async def _noop_turn(state, session, message, **_kw) -> None:
    return None


def test_the_api_answers_a_refused_write_403_whichever_handler_makes_it(home) -> None:
    """A handler with no check of its own, writing from the loop and from a worker thread: the
    request names its session, and the store refuses for an Incognito one."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.memory_write_gate import memory_write_middleware

    _chat(home["log"], INCOGNITO, "incognito")
    _chat(home["log"], NORMAL, "persistent")
    store: VectorMemoryStore = home["store"]

    async def on_loop(request: web.Request) -> web.Response:
        store.write_episodic(f"Something the agent kept for {request.headers['X-Session-Key']}.")
        return web.json_response({"ok": True})

    async def in_thread(request: web.Request) -> web.Response:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, store.write_episodic, "Kept from a worker thread here.")
        return web.json_response({"ok": True})

    async def run() -> list[tuple[str, int]]:
        memory_writes.carry_scope_into_worker_threads(asyncio.get_running_loop())
        app = web.Application(middlewares=[memory_write_middleware()])
        app["state"] = None
        app.router.add_post("/loop", on_loop)
        app.router.add_post("/thread", in_thread)
        out = []
        async with TestClient(TestServer(app)) as client:
            for path in ("/loop", "/thread"):
                for key in (INCOGNITO, NORMAL):
                    resp = await client.post(path, headers={"X-Session-Key": key})
                    out.append((f"{path} {key}", resp.status))
                    if resp.status == 403:
                        assert (await resp.json())["error"] == memory_writes.REFUSAL
        return out

    statuses = asyncio.run(run())
    assert statuses == [
        (f"/loop {INCOGNITO}", 403),
        (f"/loop {NORMAL}", 200),
        (f"/thread {INCOGNITO}", 403),
        (f"/thread {NORMAL}", 200),
    ]
    texts = [r[0] for r in store.db.execute("SELECT text FROM episodic_memories").fetchall()]
    assert texts == [f"Something the agent kept for {NORMAL}.", "Kept from a worker thread here."]


@pytest.mark.parametrize(
    ("sql", "reads"),
    [
        ("SELECT key FROM semantic_memory", True),
        ("  -- note\n  select 1", True),
        ("WITH r AS (SELECT 1) SELECT * FROM r", True),
        ("PRAGMA table_info(semantic_memory)", True),
        ("COMMIT", True),
        ("INSERT INTO semantic_memory (key) VALUES (?)", False),
        ("UPDATE episodic_memories SET last_accessed_at = ?", False),
        ("DELETE FROM mem_links", False),
        ("REPLACE INTO semantic_memory (key) VALUES (?)", False),
        ("WITH r AS (SELECT 1) INSERT INTO t SELECT * FROM r", False),
        ("BEGIN", False),
        ("CREATE TABLE t (x)", False),
    ],
)
def test_what_the_databases_take_for_a_read(sql: str, reads: bool) -> None:
    assert memory_writes.reads_only(sql) is reads


# ── what an earlier version kept ────────────────────────────────────────────────────────────


def test_what_an_earlier_version_kept_from_an_incognito_chat_is_removed_at_start(home) -> None:
    """The rows an earlier version left, as they were found on an affected install: episodic
    records filed under the chat, a fact sourced from its consolidation, the session's working
    memory (sealed, so tombstoned), their history events and links. A normal chat's records and a
    persona note that names no chat stay."""
    _chat(home["log"], INCOGNITO, "incognito")
    _chat(home["log"], NORMAL, "persistent")
    store: VectorMemoryStore = home["store"]
    store.write_episodic("Weekly log: Monday easy, Wednesday skipped.", conversation_id=INCOGNITO)
    store.write_episodic(
        "A sealed summary of the exercise chat.", conversation_id=INCOGNITO, source="seal"
    )
    store.write_episodic("The user prefers short answers in the morning.", conversation_id=NORMAL)
    store.set_semantic("user.pref.log_format", "weekday list", 0.9, f"consolidation:{INCOGNITO}")
    store.set_semantic("user.pref.tone", "brief", 0.9, f"consolidation:{NORMAL}")
    store.set_semantic("user.persona.abc123", "Applied a rule accurately.", 0.9, "self_persona")
    from personalclaw.memory_service import MemoryService

    MemoryService.over_vector_store(store).write_working_memory(INCOGNITO, "Logged a week.")
    # The daily history the consolidator appended the same summary to, beside another chat's.
    markdown: MemoryStore = home["markdown"]
    markdown.append_history("Logged a week.")
    markdown.append_history("Planned the garden beds.")
    store.delete_semantic(MemoryService._working_key(INCOGNITO), "seal")
    gone = {
        r[0]
        for r in store.db.execute(
            "SELECT id FROM episodic_memories WHERE conversation_id = ?", (INCOGNITO,)
        ).fetchall()
    } | {"user.pref.log_format", MemoryService._working_key(INCOGNITO)}
    # The linker filed the chat's episodic records under the chat, as it did on that install.
    assert store.db.execute(
        "SELECT COUNT(*) FROM mem_links WHERE to_ref = ?", (INCOGNITO,)
    ).fetchone()[0]

    assert memory_writes.forget_what_restricted_sessions_left(store, markdown) == 5
    history = "".join(
        f.read_text(encoding="utf-8")
        for f in (home["workspace"] / "memory" / "history").glob("*.md")
    )
    assert "Logged a week." not in history and "Planned the garden beds." in history
    left_keys = {r[0] for r in store.db.execute("SELECT key FROM semantic_memory").fetchall()}
    assert left_keys == {"user.pref.tone", "user.persona.abc123"}
    left_texts = [r[0] for r in store.db.execute("SELECT text FROM episodic_memories").fetchall()]
    assert left_texts == ["The user prefers short answers in the morning."]
    marks = ",".join("?" * len(gone))
    assert (
        store.db.execute(
            f"SELECT COUNT(*) FROM memory_events WHERE memory_key IN ({marks})", sorted(gone)
        ).fetchone()[0]
        == 0
    ), "a history event still carries the Incognito chat's text"
    assert (
        store.db.execute(
            "SELECT COUNT(*) FROM mem_links WHERE to_ref = ?", (INCOGNITO,)
        ).fetchone()[0]
        == 0
    )
    assert memory_writes.forget_what_restricted_sessions_left(store, markdown) == 0
