"""An intent checks your saved items in the background, at its own address: a start answers 202
with how many items it will check, its progress counts up, a stop ends it keeping what it found,
one job runs per intent, and a paused intent starts none. The old forms answer as before: ``/run``
waits and answers its counts and findings, and the findings without ``limit`` or ``before`` are
every one."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from library_shared import client, isolate, note


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


GOAL = "Anything that could improve my homelab"


class Matcher:
    """The intent matcher, held: each item waits for :meth:`let` before it is answered. An item
    whose text names ZFS matches."""

    def __init__(self) -> None:
        self.gate = asyncio.Semaphore(0)
        self.seen = 0

    async def __call__(self, intent, text, *, pool=None, raise_on_error=False):
        await self.gate.acquire()
        self.seen += 1
        if "ZFS" in text:
            return SimpleNamespace(takeaway="Snapshots help", fields=[])
        return None

    def let(self, n: int) -> None:
        for _ in range(n):
            self.gate.release()


async def _said(resp) -> tuple[int, str]:
    """A refusal's status and code, read while the client is open."""
    body = await resp.json()
    return resp.status, str(body.get("error", {}).get("code", ""))


async def _current(c) -> dict:
    out = await c.get("/api/knowledge/intents/homelab/runs/current")
    assert out.status == 200, await out.text()
    return await out.json()


async def _until(c, check, *, tries: int = 200) -> dict:
    for _ in range(tries):
        now = await _current(c)
        if check(now):
            return now
        await asyncio.sleep(0.01)
    raise AssertionError(f"never reached: {now}")


@pytest.mark.asyncio
async def test_a_run_counts_up_stops_keeping_what_it_found_and_runs_once(home, monkeypatch):
    from library_shared import intent

    import personalclaw.knowledge.intents as intents

    matcher = Matcher()
    monkeypatch.setattr(intents, "match_intent", matcher)
    intent("homelab", GOAL)
    note("ZFS snapshots", "ZFS takes hourly snapshots")
    for i in range(5):
        note(f"Recipe {i}", "Flour and water")
    async with client(home) as c:
        before = await c.get("/api/knowledge/intents/homelab/runs/current")
        count = await (await c.get("/api/knowledge/intents/candidates?enabled_for=")).json()
        started = await c.post("/api/knowledge/intents/homelab/runs")
        assert started.status == 202, await started.text()
        total = (await started.json())["total"]
        twice = await _said(await c.post("/api/knowledge/intents/homelab/runs"))
        clear = await _said(await c.delete("/api/knowledge/intents/homelab/outcomes"))
        matcher.let(2)
        partway = await _until(c, lambda s: s["done"] >= 2)
        stopped = await c.delete("/api/knowledge/intents/homelab/runs/current")
        assert stopped.status == 200, await stopped.text()
        ended = await _current(c)
        found = (await (await c.get("/api/knowledge/intents/homelab/outcomes")).json())["outcomes"]
    assert before.status == 404, "no run yet"
    assert total == 6 and count == {"total": total}, "the count is the run's own"
    assert twice == (409, "already_running")
    assert clear == (409, "already_running"), "clearing waits for the run"
    assert partway["status"] == "running" and partway["total"] == total
    assert ended["status"] == "stopped" and 2 <= ended["done"] < total
    assert ended["kept"] == len(found)


@pytest.mark.asyncio
async def test_a_run_finishes_and_a_paused_intent_starts_none(home, monkeypatch):
    from library_shared import knowledge_store

    import personalclaw.knowledge.intents as intents
    from personalclaw.knowledge.intents import Intent, IntentStore

    matcher = Matcher()
    monkeypatch.setattr(intents, "match_intent", matcher)
    where = IntentStore(Path(knowledge_store().db_path).parent / "intents.json")
    where.upsert(Intent(id="homelab", goal=GOAL))
    where.upsert(Intent(id="garden", goal="Anything about the garden", enabled=False))
    note("ZFS snapshots", "ZFS takes hourly snapshots")
    note("Bread", "Flour and water")
    async with client(home) as c:
        paused = await _said(await c.post("/api/knowledge/intents/garden/runs"))
        none = await c.get("/api/knowledge/intents/garden/runs/current")
        assert (await c.post("/api/knowledge/intents/homelab/runs")).status == 202
        matcher.let(2)
        done = await _until(c, lambda s: s["status"] != "running")
        again = await c.post("/api/knowledge/intents/homelab/runs")
        matcher.let(2)
        await _until(c, lambda s: s["status"] != "running")
        matcher.let(2)
        old = await c.post("/api/knowledge/intents/homelab/run")
        answer = await old.json()
    assert paused == (409, "paused")
    assert none.status == 404, "a paused intent starts no job"
    assert done == {"status": "done", "done": 2, "total": 2, "kept": 1}
    assert again.status == 202, "a finished run makes way for the next"
    assert old.status == 200
    assert set(answer) >= {"recorded", "matched", "new", "errors", "evaluated", "outcomes"}
    assert answer["matched"] == 1 and answer["evaluated"] == 2
