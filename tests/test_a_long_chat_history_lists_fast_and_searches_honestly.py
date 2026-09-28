"""A power user's chat history: the chat list, and search while the index is being built.

Measured on a synthetic home of 12,005 chats (built through the product's own writers, never a
real history), before this change:

* **The chat list**. Listing them opened every transcript twice — 24,110 opens,
  2.1 s — and every new ``ConversationLog`` paid that again, the search index's pass included,
  every five minutes. ``get_metadata`` read each transcript whole to take its first line (352 MB
  for all of them). ``GET /api/chat/sessions`` did all of it on the event loop: 0.9 s, the whole
  gateway waiting on it, on every refetch.
* **Search while the index is built**. The index took 200 chats every five minutes
  from the heartbeat: 5 hours for 12,005. Meanwhile a search for a word 240 chats say answered 4,
  as though that were all of them.

These rails count work rather than time wherever a count can say it: transcript opens, whole-file
reads, chats indexed. Each builds its history in ``tmp_path``, the home and every importer
source pointing there.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import os
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

#: The CI-sized history: most chats record their count (the dashboard's save and the import do),
#: a few were appended to by a channel app and record none.
CHATS = 300
APPENDED = 12
WORD = "zebracrossing"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from personalclaw import history, session_search
    from personalclaw.config.loader import config_dir

    pc = tmp_path / "pclaw-home"
    pc.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pc))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    assert config_dir() == pc, "PERSONALCLAW_HOME did not bind — the real home is at risk"
    history._HEADS.clear()
    session_search.reset_for_tests()
    session_search.INDEXER.reset()
    yield pc
    session_search.INDEXER.reset()
    session_search.reset_for_tests()
    history._HEADS.clear()


def _history(*, marked: int = 0) -> list[str]:
    """``CHATS`` chats, newest last, the first *marked* of them (oldest) saying ``WORD``."""
    from personalclaw.history import ConversationLog, import_conversation

    log = ConversationLog()
    log.init()
    keys = []
    now = time.time()
    for n in range(CHATS):
        says = f" and the {WORD} fix" if n < marked else ""
        key = f"dashboard_chat-{n:04d}"
        if n % (CHATS // APPENDED) == 0:
            log.append(key, "user", f"channel note {n}{says}")
            log.append(key, "assistant", "noted")
        else:
            messages = [
                {"role": "user", "content": f"Why is build {n} slow{says}?", "ts": ""},
                {"role": "assistant", "content": "A timer it never cancels. " * 40, "ts": ""},
            ]
            import_conversation(
                key, metadata={"title": f"Build {n}"}, messages=messages, modified=now - CHATS + n
            )
        os.utime(log._path(key), (now - CHATS + n, now - CHATS + n))
        keys.append(key)
    return keys


class _Opens:
    """Counts transcripts opened, and transcripts read whole."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.opened = 0
        real_open = builtins.open

        def counting_open(file, *args, **kwargs):
            if str(file).endswith(".jsonl"):
                self.opened += 1
            return real_open(file, *args, **kwargs)

        def refusing_read_text(path, *args, **kwargs):
            raise AssertionError(f"read whole: {path}")

        monkeypatch.setattr(builtins, "open", counting_open)
        self._refuse = refusing_read_text
        self._monkeypatch = monkeypatch

    def refuse_whole_reads(self) -> None:
        real = Path.read_text

        def read_text(path, *args, **kwargs):
            if str(path).endswith(".jsonl"):
                self._refuse(path)
            return real(path, *args, **kwargs)

        self._monkeypatch.setattr(Path, "read_text", read_text)


def _new_process() -> None:
    """Forget what this process read of the transcripts, as a restarted gateway has."""
    from personalclaw import history

    history._HEADS.clear()
    history._LISTING_LOADED.clear()


# ── the chat list ─────────────────────────────────────────────────────────────


def test_a_listing_opens_each_transcript_once_and_a_second_log_opens_none(home, monkeypatch):
    """Two opens a transcript before (the count, then the metadata), and every new log again."""
    from personalclaw import history
    from personalclaw.history import ConversationLog

    _history()
    (home / history.LISTING_FILE).unlink(missing_ok=True)
    _new_process()
    opens = _Opens(monkeypatch)
    listed = ConversationLog().list_sessions()
    assert len(listed) == CHATS
    assert opens.opened == CHATS, f"{opens.opened} opens for {CHATS} transcripts"
    opens.opened = 0
    assert ConversationLog().list_sessions() == listed
    assert opens.opened == 0, "a second log opened the transcripts again"


def test_a_restarted_gateway_lists_without_opening_an_unchanged_transcript(home, monkeypatch):
    """The listing file: what the last listing read, used while each transcript is unchanged."""
    from personalclaw import history
    from personalclaw.history import ConversationLog

    _history()
    first = ConversationLog().list_sessions()
    assert (home / history.LISTING_FILE).is_file()
    _new_process()
    opens = _Opens(monkeypatch)
    assert ConversationLog().list_sessions() == first
    assert opens.opened == 0, f"{opens.opened} transcripts opened by a restarted listing"

    # One chat changed since: that one is opened again, and only it.
    ConversationLog().append(first[0]["key"], "user", "one more thing")
    _new_process()
    opens.opened = 0
    again = {entry["key"]: entry for entry in ConversationLog().list_sessions()}
    assert opens.opened == 1
    assert again[first[0]["key"]]["messages"] == first[0]["messages"] + 1


def test_the_listing_file_holds_nothing_of_a_restricted_chat(home):
    from personalclaw import history
    from personalclaw.history import ConversationLog

    _history()
    log = ConversationLog()
    log.append("dashboard_secret", "user", "the password reset for the vault")
    log.update_metadata("dashboard_secret", {"memory_mode": "incognito", "title": "Vault reset"})
    _new_process()
    (home / history.LISTING_FILE).unlink(missing_ok=True)
    log.list_sessions()
    written = (home / history.LISTING_FILE).read_text(encoding="utf-8")
    assert "Vault reset" not in written and "dashboard_secret" not in written


def test_a_damaged_listing_file_is_ignored_and_written_again(home):
    from personalclaw import history
    from personalclaw.history import ConversationLog

    _history()
    (home / history.LISTING_FILE).write_text("{not json", encoding="utf-8")
    _new_process()
    assert len(ConversationLog().list_sessions()) == CHATS
    assert json.loads((home / history.LISTING_FILE).read_text(encoding="utf-8"))["version"] == 1


def test_metadata_is_read_from_the_first_line_never_the_whole_transcript(home, monkeypatch):
    from personalclaw.history import ConversationLog

    keys = _history()
    _new_process()
    opens = _Opens(monkeypatch)
    opens.refuse_whole_reads()
    log = ConversationLog()
    assert log.get_metadata(keys[1])["title"] == "Build 1"
    assert opens.opened == 1


def test_a_rewrite_of_the_same_size_in_the_same_tick_is_seen(home):
    """Every write replaces a transcript (a new inode) or grows it: a listing keyed by the file's
    mtime alone kept the title it had read before such a rewrite."""
    from personalclaw.atomic_write import atomic_write
    from personalclaw.history import ConversationLog

    keys = _history()
    log = ConversationLog()
    path = log._path(keys[1])
    assert {e["key"]: e for e in log.list_sessions()}[keys[1]]["title"] == "Build 1"
    stat = path.stat()
    text = path.read_text(encoding="utf-8").replace('"title": "Build 1"', '"title": "Build 9"')
    atomic_write(path, text)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_size == stat.st_size and path.stat().st_mtime_ns == stat.st_mtime_ns
    assert {e["key"]: e for e in log.list_sessions()}[keys[1]]["title"] == "Build 9"


def _chat_app(log) -> web.Application:
    from unittest.mock import MagicMock

    from personalclaw.dashboard.chat import api_chat_sessions
    from personalclaw.dashboard.state import DashboardState

    sessions = MagicMock(count=0)
    sessions.get_channel_link = lambda key: (None, None)
    state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=log)
    app = web.Application()
    app["state"] = state

    async def ping(_request):
        return web.json_response({"ok": True})

    app.router.add_get("/api/chat/sessions", api_chat_sessions)
    app.router.add_get("/ping", ping)
    return app


@pytest.mark.asyncio
async def test_the_chat_list_leaves_the_gateway_answering_while_it_lists(home, monkeypatch):
    """Listing 12,005 chats held the event loop for 0.9 s: every request answered meanwhile waited
    on it. Here the listing is made to take half a second; a request sent meanwhile answers."""
    from personalclaw.history import ConversationLog

    _history()
    log = ConversationLog()
    real_with, real_plain = (
        getattr(ConversationLog, "list_sessions_with_metadata", None),
        ConversationLog.list_sessions,
    )

    def slow_with(self):
        time.sleep(0.5)
        return real_with(self)

    def slow_plain(self):
        time.sleep(0.5)
        return real_plain(self)

    monkeypatch.setattr(ConversationLog, "list_sessions_with_metadata", slow_with, raising=False)
    monkeypatch.setattr(ConversationLog, "list_sessions", slow_plain)
    async with TestClient(TestServer(_chat_app(log))) as client:
        # Client and server share this loop, so the clock starts before the loop is handed over.
        started = time.monotonic()
        listing = asyncio.ensure_future(client.get("/api/chat/sessions"))
        await asyncio.sleep(0.1)
        assert (await client.get("/ping")).status == 200
        answered = time.monotonic() - started
        rows = await (await listing).json()
    assert len(rows) == CHATS
    assert answered < 0.4, f"a request sent while the chat list was listed took {answered:.2f} s"


# ── search while the index is being built ─────────────────────────────────────


def _search_app(log, *, app: str = "") -> web.Application:
    from types import SimpleNamespace

    from personalclaw.dashboard.handlers.sessions import api_sessions_search

    state = SimpleNamespace(
        conversation_log=log,
        session_creating_app=lambda key: "probe" if key.endswith(("0", "5")) else "",
    )

    @web.middleware
    async def as_app(request, handler):
        if app:
            request["app"] = app
        return await handler(request)

    application = web.Application(middlewares=[as_app])
    application["state"] = state
    application.router.add_get("/api/sessions/search", api_sessions_search)
    return application


def _index(keys: list[str]) -> None:
    from personalclaw import session_search
    from personalclaw.history import ConversationLog

    log = ConversationLog()
    for key in keys:
        assert session_search.reindex_session(key, log=log)


async def _search(log, query: str = WORD, **params) -> dict:
    params.setdefault("limit", 200)
    path = f"/api/sessions/search?q={query}" + "".join(
        f"&{k}={v}" for k, v in params.items() if k != "app"
    )
    async with TestClient(TestServer(_search_app(log, app=params.get("app", "")))) as client:
        resp = await client.get(path)
        assert resp.status == 200
        return await resp.json()


@pytest.mark.asyncio
async def test_a_search_while_the_index_is_built_says_how_much_it_covered(home, monkeypatch):
    """The index holds the 100 newest of 300 chats; 60 of the 300 say the word, all among the
    200 older ones, and the direct read of the newest (here 50) finds none of them. The answer
    before: nothing found, and nothing said about the rest."""
    from personalclaw import session_search
    from personalclaw.history import ConversationLog

    monkeypatch.setattr(session_search, "SCAN_WINDOW", 50)
    keys = _history(marked=60)
    _index(keys[-100:])
    body = await _search(ConversationLog())
    assert body["complete"] is False
    assert body["searched"] == {"chats": 100, "of": CHATS}
    assert body["index"] == {"indexed": 100, "of": CHATS, "building": True, "long": 0}
    assert body["sessions"] == []


@pytest.mark.asyncio
async def test_the_rest_read_directly_makes_the_answer_complete(home, monkeypatch):
    from personalclaw import session_search
    from personalclaw.history import ConversationLog

    monkeypatch.setattr(session_search, "SCAN_WINDOW", 50)
    keys = _history(marked=60)
    _index(keys[-100:])
    body = await _search(ConversationLog(), rest=1)
    assert body["complete"] is True
    assert body["searched"] == {"chats": CHATS, "of": CHATS}
    assert body["source"] == "index+scan"
    assert {s["key"] for s in body["sessions"]} == set(keys[:60])


@pytest.mark.asyncio
async def test_a_caught_up_index_answers_complete_on_its_own(home):
    from personalclaw.history import ConversationLog

    keys = _history(marked=60)
    _index(keys)
    body = await _search(ConversationLog())
    assert body["complete"] is True and body["source"] == "index"
    assert body["index"] == {"indexed": CHATS, "of": CHATS, "building": False, "long": 0}
    assert {s["key"] for s in body["sessions"]} == set(keys[:60])


@pytest.mark.asyncio
async def test_a_chat_longer_than_the_index_keeps_is_not_counted_as_searched_whole(
    home, monkeypatch
):
    """The index keeps a chat's first 200,000 characters. In the synthetic history 3 of the 240
    chats that say the word say it after that, so a caught-up index found 237 and the answer
    said it had searched every chat. Here the index keeps 5,000, and one chat says the word
    after 8,400."""
    from personalclaw import session_search
    from personalclaw.history import ConversationLog
    from personalclaw.inbound import tools

    monkeypatch.setattr(session_search, "_MAX_SESSION_CHARS", 5000)
    # Past what a search reads of such chats directly (within it, the chat is read whole:
    # test_a_search_reads_a_long_chat_whole).
    monkeypatch.setattr(session_search, "LONG_READ_BYTES", 0)
    keys = _history(marked=60)
    log = ConversationLog()
    for _ in range(10):
        log.append(keys[100], "user", "Nothing to see here. " * 40)
    log.append(keys[100], "user", f"and the {WORD} at last")
    session_search.reindex_all()
    body = await _search(ConversationLog())
    assert body["complete"] is False
    assert body["searched"] == {"chats": CHATS - 1, "of": CHATS}
    assert body["index"] == {"indexed": CHATS, "of": CHATS, "building": False, "long": 1}
    assert keys[100] not in {s["key"] for s in body["sessions"]}
    body = await _search(ConversationLog(), rest=1)
    assert body["complete"] is True and body["matched"] == 61
    assert keys[100] in {s["key"] for s in body["sessions"]}
    text = await tools._sessions_search({"query": WORD}, None)
    assert f"Only {CHATS - 1} of {CHATS} conversations were searched whole" in text


@pytest.mark.asyncio
async def test_an_answer_counts_every_chat_that_matched_not_only_those_it_lists(home):
    """A list capped at its limit is a partial answer too: it says how many matched."""
    from personalclaw.history import ConversationLog

    keys = _history(marked=60)
    _index(keys)
    body = await _search(ConversationLog(), limit=10)
    assert len(body["sessions"]) == 10
    assert body["matched"] == 60 and body["complete"] is True


@pytest.mark.asyncio
async def test_with_no_index_the_answer_says_it_read_only_the_newest(home, monkeypatch):
    """No FTS5: the direct read covers the newest chats, and says so; the rest reads them all."""
    from personalclaw import session_search
    from personalclaw.history import ConversationLog

    keys = _history(marked=60)
    monkeypatch.setattr(session_search, "_connect", lambda: None)
    monkeypatch.setattr(session_search, "SCAN_WINDOW", 100)
    body = await _search(ConversationLog())
    assert body["complete"] is False and body["index"] is None
    assert body["searched"] == {"chats": 100, "of": CHATS}
    body = await _search(ConversationLog(), rest=1)
    assert body["complete"] is True
    assert {s["key"] for s in body["sessions"]} == set(keys[:60])


@pytest.mark.asyncio
async def test_an_app_s_answer_counts_only_the_chats_it_started(home):
    """An app is never told how many chats you have: its counts are of its own."""
    from personalclaw.history import ConversationLog

    keys = _history(marked=60)
    _index(keys[-100:])
    body = await _search(ConversationLog(), app="probe")
    own = [key for key in keys if key.endswith(("0", "5"))]
    assert body["searched"]["of"] == len(own)
    assert body["index"]["of"] == len(own)
    assert all(s["key"] in own for s in body["sessions"])


@pytest.mark.asyncio
async def test_the_agent_search_tool_says_when_it_searched_only_part(home):
    from personalclaw.inbound import tools

    keys = _history(marked=60)
    _index(keys[-100:])
    text = await tools._sessions_search({"query": "build"}, None)
    assert "Only 100 of 300 conversations were searched" in text
    assert text.startswith("The best 5 of ")


def test_the_indexer_catches_a_whole_history_up_without_waiting_for_a_heartbeat(home):
    """200 chats every five minutes was the heartbeat's pace. The indexer reads until none is
    left, one chat at a time, resting in proportion to the work."""
    from personalclaw import session_search

    _history(marked=60)
    assert session_search.INDEXER.start()
    deadline = time.monotonic() + 60
    progress = None
    while time.monotonic() < deadline:
        progress = session_search.INDEXER.progress()
        if progress is not None and not progress["building"]:
            break
        time.sleep(0.05)
    assert progress == {"indexed": CHATS, "of": CHATS, "building": False}
    assert session_search.stats()["sessions"] == CHATS


def _caught_up(timeout: float = 60.0) -> dict:
    from personalclaw import session_search

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        progress = session_search.INDEXER.progress()
        if progress is not None and not progress["building"]:
            return progress
        time.sleep(0.02)
    raise AssertionError(f"the indexer did not catch up: {session_search.INDEXER.progress()}")


def test_a_chat_written_while_the_index_is_built_is_read_before_the_rest(home, monkeypatch):
    """The oldest chat is written to while the indexer reads the newest: it is read next, not
    after the other 298."""
    from personalclaw import session_search
    from personalclaw.history import ConversationLog

    keys = _history()
    order: list[str] = []
    real = session_search.reindex_session

    def recording(key, log=None):
        order.append(key)
        if len(order) == 1:
            ConversationLog().append(keys[0], "user", "one more thing")
        return real(key, log=log)

    monkeypatch.setattr(session_search, "reindex_session", recording)
    session_search.INDEXER.start()
    _caught_up()
    assert order[:2] == [keys[-1], keys[0]]
    assert len(order) == CHATS


def test_a_write_landing_while_its_chat_is_read_is_read_too(home, monkeypatch):
    """A chat is written to again while the indexer reads it. The index then held the chat as it
    was before that write, and answered for it: a search for the new words missed it and said
    it had searched every chat."""
    from personalclaw import session_search
    from personalclaw.history import ConversationLog

    keys = _history(marked=60)
    session_search.reindex_all()
    log = ConversationLog()
    log.append(keys[100], "user", "a follow-up")
    real = session_search._transcript_text

    def reading(path):
        text = real(path)
        if Path(path).stem == keys[100] and WORD not in path.read_text(encoding="utf-8"):
            log.append(keys[100], "user", f"and the {WORD} as well")
        return text

    monkeypatch.setattr(session_search, "_transcript_text", reading)
    session_search.INDEXER.start()
    _caught_up()
    answer = session_search.search(WORD, log=ConversationLog(), limit=200)
    assert answer.complete
    assert keys[100] in {hit["key"] for hit in answer.hits}
    assert answer.matched == 61


def test_the_indexer_rests_in_proportion_to_its_work(home, monkeypatch):
    """It never takes more than ``DUTY`` of the time: each chat's work is followed by a rest
    ``(1 - DUTY) / DUTY`` times as long. Here each chat takes 20 ms of work."""
    from personalclaw import session_search

    keys = _history()[:10]
    indexed: list[str] = []

    def working(key, log=None):
        time.sleep(0.02)
        indexed.append(key)
        return True

    monkeypatch.setattr(session_search, "reindex_session", working)
    monkeypatch.setattr(
        session_search,
        "_check",
        lambda log, conn, force=False: session_search._Census(list(keys), list(keys), []),
    )
    started = time.monotonic()
    session_search.INDEXER.start()
    while len(indexed) < len(keys) and time.monotonic() - started < 30:
        time.sleep(0.01)
    elapsed = time.monotonic() - started
    assert len(indexed) == len(keys)
    floor = len(keys) * 0.02 / session_search.DUTY * 0.8
    assert elapsed >= floor, f"{len(keys)} chats of 20 ms each in {elapsed:.2f} s"


@pytest.mark.asyncio
async def test_a_search_holds_the_indexer_while_it_is_answered(home, monkeypatch):
    """The indexer gives way to a search, as the reading pass gives way to an import scan."""
    from personalclaw import session_search
    from personalclaw.history import ConversationLog

    _history()
    held: list[bool] = []
    real = session_search.search

    def watching(*args, **kwargs):
        held.append(not session_search.INDEXER._go.is_set())
        return real(*args, **kwargs)

    monkeypatch.setattr(session_search, "search", watching)
    await _search(ConversationLog(), query="build")
    assert held == [True]
