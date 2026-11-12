"""An unchanged library answers 304, and reads nothing to say so.

The read's revision is taken without reading a row: the knowledge store's own counter, a stat of
each artifact's ``meta.json``, the record files' times, the projects and loops, and the minute. So a
repeat read within the minute answers 304 to its ``ETag``, opening no ``meta.json`` and running no
item ``SELECT``. A cold read runs a fixed set of queries whatever the library's size, never one per
row. Any write a row is read from moves the revision, a read-state change among them, and so does
the minute turning. The read sets ``Cache-Control: no-cache`` itself, since the gateway's default
for an API answer, ``no-store``, keeps no copy for the browser to ask about.
"""

from __future__ import annotations

import pytest
from library_shared import artifacts, client, isolate, knowledge_store, note

from personalclaw.artifacts.native import NativeArtifactProvider


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1_794_000_000.0}
    monkeypatch.setattr("personalclaw.dashboard.handlers.library.time.time", lambda: now["t"])
    return now


@pytest.fixture
def reads(monkeypatch):
    """How many ``meta.json`` the artifact store reads, and which statements the knowledge store
    runs, while counted."""
    seen = {"meta": 0, "sql": []}
    real = NativeArtifactProvider._read_meta

    def counted(self, slug):
        seen["meta"] += 1
        return real(self, slug)

    monkeypatch.setattr(NativeArtifactProvider, "_read_meta", counted)
    return seen


def _trace(store, seen):
    store.db.set_trace_callback(lambda sql: seen["sql"].append(sql))


def _item_selects(sql: list[str]) -> list[str]:
    return [s for s in sql if s.lstrip().upper().startswith("SELECT") and " items" in s]


@pytest.mark.asyncio
async def test_an_unchanged_library_answers_304_reading_no_row(home, clock, reads):
    for i in range(3):
        note(f"Note {i}")
        artifacts().create(name=f"Page {i}", content="# P", kind="markdown")
    store = knowledge_store()
    async with client(home, knowledge=store) as c:
        first = await c.get("/api/library")
        etag = first.headers["ETag"]
        assert first.status == 200 and first.headers["Cache-Control"] == "no-cache"
        reads["meta"], reads["sql"] = 0, []
        _trace(store, reads)
        again = await c.get("/api/library", headers={"If-None-Match": etag})
        store.db.set_trace_callback(None)
    assert again.status == 304, again.status
    assert again.headers["ETag"] == etag and again.headers["Cache-Control"] == "no-cache"
    assert reads["meta"] == 0, "a 304 opens no meta.json"
    assert _item_selects(reads["sql"]) == [], _item_selects(reads["sql"])


async def _cold_queries(home, n: int) -> int:
    store = knowledge_store()
    for i in range(n):
        note(f"Note {i}")
    seen = {"sql": []}
    async with client(home, knowledge=store) as c:
        _trace(store, seen)
        assert (await c.get("/api/library")).status == 200
        store.db.set_trace_callback(None)
    return len([s for s in seen["sql"] if s.lstrip().upper().startswith("SELECT")])


@pytest.mark.asyncio
async def test_a_cold_read_runs_the_same_queries_for_3_things_or_60(tmp_path, monkeypatch, clock):
    few = await _cold_queries(isolate(tmp_path / "a", monkeypatch), 3)
    many = await _cold_queries(isolate(tmp_path / "b", monkeypatch), 60)
    assert few == many, (few, many)


@pytest.mark.asyncio
async def test_a_write_or_the_minute_moves_the_etag(home, clock):
    kept = note("A note")
    page = artifacts().create(name="A page", content="# P", kind="markdown")
    store = knowledge_store()
    async with client(home, knowledge=store) as c:

        async def etag() -> str:
            return (await c.get("/api/library")).headers["ETag"]

        tags = [await etag()]
        store.set_read_state(kept, "read")  # leaves updated_at, so only the counter sees it
        tags.append(await etag())
        artifacts().update(page.slug, content="# P, edited")
        tags.append(await etag())
        note("Another note")
        tags.append(await etag())
        clock["t"] += 60
        tags.append(await etag())
        assert await etag() == tags[-1], "nothing changed since"
    assert len(set(tags)) == len(tags), tags
