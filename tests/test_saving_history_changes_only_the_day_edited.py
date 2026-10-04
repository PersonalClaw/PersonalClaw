"""Saving History on the Memory page changes the day the owner edited, in that day's file, and
nothing else.

Measured before this was written: the Memory page's History document was the recent days read as
one text (``MemoryStore.read_recent_history``: the last two weeks whole, older days cut to their
first entry), and its save wrote that whole text into TODAY's file. One save of a page showing two
days stored yesterday's entry twice, once in its own day and once in today's, and every later save
copied the other days in again. The save wrote the file directly, too: the keyword index never saw
the edit, and work that may change none of your memory (a Temporary or Incognito chat's) was not
refused.

Now the daily history is listed by day (``GET /api/memory/history``), and each day is read and
saved as the one file it is kept in (``/api/memory/history/{day}``), through the write every memory
file takes (the refusal, then the file, then its index), under the lock every writer of the memory
documents holds (``memory.hold_documents``), which every writer of the daily history takes too.
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.stale_write import revision_of

pytestmark = pytest.mark.asyncio

#: What each day's entry says, by how many days before today it was written. Twenty days back is
#: a day the recent-days view cut to its first entry.
ENTRIES = {
    0: "Ordered the kitchen tiles.",
    1: "Planned the garden shed with the neighbour.",
    3: "Booked the bike in for a new chain.",
    20: "Picked a paint colour for the hallway.",
}
#: A second entry of yesterday's, so a day holds more than one.
YESTERDAY_LATER = "Bought the shed's roofing felt."


def _day(days_ago: int) -> str:
    return (datetime.now().date() - timedelta(days=days_ago)).isoformat()


def _entry(stamp: str, text: str) -> str:
    """One entry, as the consolidation writes it (``MemoryStore.append_history``)."""
    return f"\n#### {stamp}\n{text}\n"


def _day_text(day: str, *entries: str) -> str:
    return f"# {day}\n" + "".join(_entry(f"{9 + i:02d}:30 UTC", e) for i, e in enumerate(entries))


@pytest.fixture
def memory(tmp_path):
    """A memory whose daily history holds four days, its keyword index built from the files as
    the gateway builds it when it starts."""
    from personalclaw.memory import MemoryStore

    mem = MemoryStore(workspace=tmp_path / "workspace")
    mem.init()
    for days_ago, text in ENTRIES.items():
        entries = (text, YESTERDAY_LATER) if days_ago == 1 else (text,)
        _file(mem, _day(days_ago)).write_text(_day_text(_day(days_ago), *entries), encoding="utf-8")
    mem.rebuild_index()
    return mem


def _file(mem, day: str) -> Path:
    return mem._workspace / "memory" / "history" / f"{day}.md"


def _history(mem) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(_file(mem, "x").parent.glob("*.md"))}


def _stamps(mem) -> dict[str, int]:
    return {p.name: p.stat().st_mtime_ns for p in sorted(_file(mem, "x").parent.glob("*.md"))}


def _app(mem, *, middlewares: tuple = ()) -> web.Application:
    """The gateway's own route table, over *mem*."""
    from personalclaw.dashboard.routes import register_dashboard_routes

    app = web.Application(middlewares=list(middlewares))
    app["state"] = SimpleNamespace(context_builder=SimpleNamespace(memory=mem))
    register_dashboard_routes(app)
    return app


def _based_on(revision: str) -> dict[str, str]:
    return {"If-Match": f'"{revision}"'}


async def _edit_a_day(c: TestClient, day: str, change) -> tuple[int, dict]:
    """The owner opens *day* in the History editor, changes its text with *change*, and saves."""
    read = await (await c.get(f"/api/memory/history/{day}")).json()
    saved = await c.put(
        f"/api/memory/history/{day}",
        json={"content": change(read["content"])},
        headers=_based_on(read["revision"]),
    )
    return saved.status, await saved.json()


# ── each day is its own file ────────────────────────────────────────────────────────────────


async def test_the_days_are_listed_newest_first_with_their_entries(memory) -> None:
    async with TestClient(TestServer(_app(memory))) as c:
        listing = await (await c.get("/api/memory/history")).json()
    assert listing["today"] == _day(0)
    assert listing["days"] == [
        {"date": _day(0), "entries": 1},
        {"date": _day(1), "entries": 2},
        {"date": _day(3), "entries": 1},
        {"date": _day(20), "entries": 1},
    ]


async def test_today_is_listed_before_anything_is_recorded_for_it(tmp_path) -> None:
    """So the owner can write today's note on a day nothing has been recorded yet."""
    from personalclaw.memory import MemoryStore

    mem = MemoryStore(workspace=tmp_path / "workspace")
    mem.init()
    async with TestClient(TestServer(_app(mem))) as c:
        listing = await (await c.get("/api/memory/history")).json()
        status, saved = await _edit_a_day(c, _day(0), lambda text: text + "A note of my own.\n")
    assert listing["days"] == [{"date": _day(0), "entries": 0}]
    assert status == 200, saved
    assert _file(mem, _day(0)).read_text(encoding="utf-8") == "A note of my own.\n"


async def test_one_save_with_several_days_listed_stores_each_entry_once_in_its_own_day(
    memory,
) -> None:
    async with TestClient(TestServer(_app(memory))) as c:
        status, saved = await _edit_a_day(
            c, _day(1), lambda text: text.replace("with the neighbour", "with Sam next door")
        )
    assert status == 200, saved
    every = b"".join(_history(memory).values()).decode("utf-8")
    kept = [*ENTRIES.values(), YESTERDAY_LATER]
    kept[1] = "Planned the garden shed with Sam next door."
    for text in kept:
        assert every.count(text) == 1, f"{text!r} is stored {every.count(text)} times"
    for days_ago, text in ENTRIES.items():
        own = _file(memory, _day(days_ago)).read_text(encoding="utf-8")
        assert (text if days_ago != 1 else kept[1]) in own
    assert "with the neighbour" not in every


async def test_an_edit_to_one_day_changes_only_that_day(memory) -> None:
    before, stamps = _history(memory), _stamps(memory)
    async with TestClient(TestServer(_app(memory))) as c:
        status, saved = await _edit_a_day(
            c, _day(3), lambda text: text.replace("a new chain", "a new chain and brake pads")
        )
    assert status == 200, saved
    after = _history(memory)
    edited = f"{_day(3)}.md"
    assert after[edited] == before[edited].replace(b"a new chain", b"a new chain and brake pads")
    assert saved["content"] == after[edited].decode("utf-8")
    assert saved["revision"] == revision_of(saved["content"])
    untouched = {name for name in before if name != edited}
    assert {n: after[n] for n in untouched} == {n: before[n] for n in untouched}
    assert {n: _stamps(memory)[n] for n in untouched} == {n: stamps[n] for n in untouched}


async def test_saving_a_day_unchanged_writes_the_same_bytes(memory) -> None:
    """The positive control: a save that changes nothing leaves every day as it was."""
    before = _history(memory)
    async with TestClient(TestServer(_app(memory))) as c:
        listed = await (await c.get("/api/memory/history")).json()
        status, saved = await _edit_a_day(c, _day(1), lambda text: text)
        listed_after = await (await c.get("/api/memory/history")).json()
    assert status == 200, saved
    assert saved["revision"] == revision_of(before[f"{_day(1)}.md"].decode("utf-8"))
    assert _history(memory) == before
    assert listed_after == listed


async def test_the_keyword_index_finds_the_edited_words(memory) -> None:
    async with TestClient(TestServer(_app(memory))) as c:
        status, saved = await _edit_a_day(
            c, _day(1), lambda text: text.replace("garden shed", "greenhouse")
        )
    assert status == 200, saved
    assert [hit["path"] for hit in memory.search("greenhouse")] == [str(_file(memory, _day(1)))]
    assert memory.search('"garden shed"') == []
    assert memory.fts_desync_count() == 0


async def test_the_recent_days_are_never_saved_into_todays_file(memory) -> None:
    """The view of several days the page used to save has no write: whatever is sent there, no
    day's file changes."""
    before = _history(memory)
    async with TestClient(TestServer(_app(memory))) as c:
        read = await (await c.get("/api/memory/history")).json()
        resp = await c.put(
            "/api/memory/history",
            json={"content": str(read.get("content", ""))},
            headers=_based_on(str(read.get("revision", ""))),
        )
        answer = await resp.text()
    assert resp.status == 405, answer
    assert _history(memory) == before


@pytest.mark.parametrize("name", ["yesterday", "2026-13-01", "2026-1-3", "20261003"])
async def test_a_day_is_named_by_its_date(memory, name: str) -> None:
    before = _history(memory)
    async with TestClient(TestServer(_app(memory))) as c:
        read = await c.get(f"/api/memory/history/{name}")
        saved = await c.put(
            f"/api/memory/history/{name}",
            json={"content": "A note."},
            headers=_based_on(revision_of("")),
        )
        bodies = [await read.json(), await saved.json()]
    assert [read.status, saved.status] == [400, 400]
    assert [b["error"]["code"] for b in bodies] == ["history_day_invalid"] * 2
    assert _history(memory) == before


async def test_a_day_changed_since_it_was_read_is_not_saved_over(memory) -> None:
    """The consolidation appends to today's file while the page holds its copy: the save is
    refused as stale, and the new entry stays."""
    async with TestClient(TestServer(_app(memory))) as c:
        read = await (await c.get(f"/api/memory/history/{_day(0)}")).json()
        memory.append_history("Shipped the new shelves.")
        resp = await c.put(
            f"/api/memory/history/{_day(0)}",
            json={"content": read["content"] + "My own note.\n"},
            headers=_based_on(read["revision"]),
        )
        answer = await resp.text()
    assert resp.status == 409, answer
    today = _file(memory, _day(0)).read_text(encoding="utf-8")
    assert "Shipped the new shelves." in today and "My own note." not in today


# ── the save is a memory write like any other ──────────────────────────────────────────────


async def test_work_that_may_change_no_memory_cannot_save_a_day(memory) -> None:
    """A Temporary chat's work is refused in the refusal's own words, the owner's page is not."""
    from personalclaw import memory_writes, session_restrictions
    from personalclaw.dashboard.memory_write_gate import memory_write_middleware

    key = "dashboard:chat-41-1700000000"
    session_restrictions.mark_temporary(key)
    day = _day(1)
    try:
        app = _app(memory, middlewares=(memory_write_middleware(),))
        async with TestClient(TestServer(app)) as c:
            read = await (await c.get(f"/api/memory/history/{day}")).json()
            refused = await c.put(
                f"/api/memory/history/{day}",
                json={"content": read["content"] + "Kept by a Temporary chat.\n"},
                headers={"X-Session-Key": key, **_based_on(read["revision"])},
            )
            refusal = await refused.json()
            owner = await c.put(
                f"/api/memory/history/{day}",
                json={"content": read["content"] + "Kept by the owner.\n"},
                headers={"X-Session-Key": "dashboard:ui", **_based_on(read["revision"])},
            )
            owners = await owner.text()
    finally:
        session_restrictions.clear(key)
    assert refused.status == 403, refusal
    assert refusal == {"error": memory_writes.REFUSAL}
    assert owner.status == 200, owners
    text = _file(memory, day).read_text(encoding="utf-8")
    assert "Kept by the owner." in text and "Temporary chat" not in text


# ── every writer of the daily history holds the documents' lock ────────────────────────────

#: What another writer adds to today's file while it holds the lock.
ANOTHER = "Painted the shed door."


def _holding_the_documents_lock(
    path: Path, *, hold: float
) -> tuple[threading.Thread, threading.Event]:
    """Another writer: under the memory documents' lock it reads *path*, takes *hold* seconds, and
    writes back what it read with an entry added. Returns its thread, and the event it sets once it
    holds the lock and has read."""
    from personalclaw import memory as memory_module

    holding = threading.Event()

    def write() -> None:
        with memory_module.hold_documents():
            text = path.read_text(encoding="utf-8")
            holding.set()
            time.sleep(hold)
            path.write_text(text + _entry("11:00 UTC", ANOTHER), encoding="utf-8")

    thread = threading.Thread(target=write)
    thread.start()
    return thread, holding


async def test_a_history_save_that_meets_another_writer_waits_and_is_not_lost(memory) -> None:
    today = _file(memory, _day(0))
    held = today.read_text(encoding="utf-8")
    async with TestClient(TestServer(_app(memory))) as c:
        read = await (await c.get(f"/api/memory/history/{_day(0)}")).json()
        thread, holding = _holding_the_documents_lock(today, hold=0.5)
        assert holding.wait(10)
        saved = await c.put(
            f"/api/memory/history/{_day(0)}",
            json={"content": read["content"] + "My own note.\n"},
            headers=_based_on(read["revision"]),
        )
        answer = await saved.text()
        thread.join(10)
    assert saved.status == 409, answer
    assert today.read_text(encoding="utf-8") == held + _entry("11:00 UTC", ANOTHER)


def _write_forgotten_entry(memory) -> str:
    """Today's file with an entry a Temporary chat left, which the start of the gateway removes."""
    left = "Something a Temporary chat said."
    path = _file(memory, _day(0))
    path.write_text(path.read_text(encoding="utf-8") + _entry("10:00 UTC", left), encoding="utf-8")
    return left


@pytest.mark.parametrize("writer", ["append", "forget"])
async def test_a_writer_of_the_daily_history_waits_for_another_and_both_changes_stay(
    memory, writer: str
) -> None:
    """The consolidation's entry, and the removal at start of what a Temporary chat left, each read
    today's file, change it and write it back. Started while another writer holds the lock, each
    waits for that writer, so neither change undoes the other."""
    today = _file(memory, _day(0))
    left = _write_forgotten_entry(memory)
    thread, holding = _holding_the_documents_lock(today, hold=0.5)
    assert holding.wait(10)
    if writer == "append":
        memory.append_history("Fitted the new bike chain.")
    else:
        memory.forget_history_entries({left})
    thread.join(10)

    text = today.read_text(encoding="utf-8")
    assert ANOTHER in text, "the other writer's entry was undone"
    assert ENTRIES[0] in text
    if writer == "append":
        assert text.count("Fitted the new bike chain.") == 1, text
    else:
        assert left not in text, text


def _the_lock_is_held() -> bool:
    """Whether a writer holds the memory documents' lock now: a hold of it that may not wait is
    refused."""
    from personalclaw import memory as memory_module
    from personalclaw.concurrency import single_flight

    with single_flight(memory_module._DOCUMENTS_LOCK) as acquired:
        return not acquired


@pytest.mark.parametrize("writer", ["append", "forget", "take_in", "page"])
async def test_each_write_of_a_day_of_history_is_made_under_the_lock(
    memory, tmp_path, writer: str
) -> None:
    from personalclaw import memory as memory_module
    from personalclaw.memory import MemoryStore

    left = _write_forgotten_entry(memory)
    other = MemoryStore(workspace=tmp_path / "folder")
    other.init()
    _file(other, _day(0)).write_text(_day_text(_day(0), "Moved the folder's notes."), "utf-8")
    history = _file(memory, _day(0)).parent
    real = memory_module.atomic_write
    writes: list[tuple[str, bool]] = []

    def recorded(path, content, **kw):
        if Path(path).parent == history:
            writes.append((Path(path).name, _the_lock_is_held()))
        return real(path, content, **kw)

    with patch.object(memory_module, "atomic_write", recorded):
        if writer == "append":
            memory.append_history("Fitted the new bike chain.")
        elif writer == "forget":
            memory.forget_history_entries({left})
        elif writer == "take_in":
            memory.take_in_documents(other)
        else:
            async with TestClient(TestServer(_app(memory))) as c:
                status, saved = await _edit_a_day(c, _day(0), lambda text: text + "My note.\n")
            assert status == 200, saved
    assert writes == [(f"{_day(0)}.md", True)]


def _file_app() -> web.Application:
    from personalclaw.apps.permissions import scoped_to_app
    from personalclaw.dashboard.handlers import api_file_read, api_file_write

    @web.middleware
    async def owner(request, handler):
        with scoped_to_app(""):
            return await handler(request)

    app = web.Application(middlewares=[owner])
    state = MagicMock()
    state._sessions = {}
    app["state"] = state
    app.router.add_get("/api/file-read", api_file_read)
    app.router.add_post("/api/file-write", api_file_write)
    return app


async def test_a_files_editor_save_of_a_day_of_history_waits_for_its_lock(memory) -> None:
    """The Files editor reaches the daily history too, and takes the same lock for it."""
    path = _file(memory, _day(0))
    held = path.read_text(encoding="utf-8")
    roots = [("Workspace", str(memory._workspace))]
    with (
        patch("personalclaw.dashboard.handlers.files._dashboard_roots", return_value=roots),
        patch("personalclaw.file_roots.dashboard_roots", return_value=roots),
        patch("personalclaw.dashboard.handlers.files._sel"),
    ):
        async with TestClient(TestServer(_file_app())) as c:
            read = await c.get("/api/file-read", params={"path": str(path)})
            assert read.status == 200, await read.text()
            thread, holding = _holding_the_documents_lock(path, hold=0.5)
            assert holding.wait(10)
            saved = await c.post(
                "/api/file-write",
                json={"path": str(path), "content": await read.text() + "My own note.\n"},
                headers={"If-Match": read.headers["ETag"]},
            )
            answer = await saved.text()
            thread.join(10)
    assert saved.status == 409, answer
    assert path.read_text(encoding="utf-8") == held + _entry("11:00 UTC", ANOTHER)


async def test_a_day_of_history_is_a_memory_document(tmp_path) -> None:
    from personalclaw.memory import is_document

    memory_dir = tmp_path / "workspace" / "memory"
    assert is_document(memory_dir / "history" / f"{date(2026, 10, 2).isoformat()}.md")
    assert is_document(memory_dir / "preferences.md")
    assert not is_document(memory_dir / "history" / "notes.txt")
    assert not is_document(tmp_path / "workspace" / "history" / "2026-10-02.md")
