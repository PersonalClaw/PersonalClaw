"""The knowledge ingest queue reads what a person adds herself before background work.

One first-in-first-out line held a person's own voice memo for about three hours behind a
watched folder's notes, each enriched on a local model for minutes. These tests pin the
two lanes (hers first, one worker), that an item she asks for moves up, that a restart puts
each item back in the lane it belongs to, and that ``standing`` reports what the page says:
how many are ahead, whether one is being read now, and how long recent items took.

The pipeline itself is replaced by a recorder (``_process``): the order items are read in is
the claim, not what reading does.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.knowledge.ingest_queue import (
    LANE_BACKGROUND,
    LANE_YOURS,
    KnowledgeIngestQueue,
)
from personalclaw.knowledge.store import KnowledgeStore


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


class _Recorder:
    """Stands in for the pipeline: records the order, and can hold one item open so a test
    reads the queue's standing while it is 'being read'."""

    def __init__(self):
        self.order: list[str] = []
        self.hold: dict[str, asyncio.Event] = {}

    async def __call__(self, item_id: str) -> None:
        self.order.append(item_id)
        gate = self.hold.get(item_id)
        if gate is not None:
            await gate.wait()


async def _until(pred, *, tries=200):
    for _ in range(tries):
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


def _queue(store, recorder):
    q = KnowledgeIngestQueue(store)
    q._process = recorder  # type: ignore[method-assign]
    return q


def test_her_own_item_is_read_before_the_background_work_queued_ahead_of_it(store):
    async def go():
        rec = _Recorder()
        q = _queue(store, rec)
        for i in range(5):
            q.enqueue_background(f"feed-{i}")
        q.enqueue("her-memo")
        q.start()
        await _until(lambda: len(rec.order) == 6)
        q.stop()
        return rec.order

    order = asyncio.run(go())
    assert order[0] == "her-memo"
    assert order[1:] == [f"feed-{i}" for i in range(5)], "background work keeps its own order"


def test_she_waits_only_for_the_item_already_being_read(store):
    """One worker: an item mid-read is not interrupted (two items' writes interleaved on the
    store's one connection can drop each other's), so hers is NEXT — not after the backlog."""

    async def go():
        rec = _Recorder()
        rec.hold["feed-0"] = asyncio.Event()
        q = _queue(store, rec)
        for i in range(4):
            q.enqueue_background(f"feed-{i}")
        q.start()
        await _until(lambda: rec.order == ["feed-0"])
        q.enqueue("her-memo")
        standing = q.standing("her-memo")
        rec.hold["feed-0"].set()
        await _until(lambda: len(rec.order) == 5)
        q.stop()
        return rec.order, standing

    order, standing = asyncio.run(go())
    assert order[:2] == ["feed-0", "her-memo"]
    assert standing["state"] == "waiting"
    assert standing["lane"] == LANE_YOURS
    assert standing["ahead"] == 0
    assert standing["running_since"] is not None


def test_a_background_item_she_asks_for_moves_up_to_her_lane(store):
    async def go():
        rec = _Recorder()
        q = _queue(store, rec)
        for i in range(3):
            q.enqueue_background(f"feed-{i}")
        q.enqueue("feed-2")  # she pressed Regenerate on it
        assert q.qsize() == 3, "moved, not duplicated"
        q.start()
        await _until(lambda: len(rec.order) == 3)
        q.stop()
        return rec.order

    assert asyncio.run(go()) == ["feed-2", "feed-0", "feed-1"]


def test_background_work_never_jumps_ahead_of_hers(store):
    async def go():
        q = _queue(store, _Recorder())
        q.enqueue("mine-1")
        q.enqueue_background("mine-1")  # already waiting in her lane: stays there
        q.enqueue_background("feed-0")
        q.enqueue("mine-2")
        return q.standing("mine-1"), q.standing("mine-2"), q.standing("feed-0")

    mine_1, mine_2, feed = asyncio.run(go())
    assert (mine_1["lane"], mine_1["ahead"]) == (LANE_YOURS, 0)
    assert (mine_2["lane"], mine_2["ahead"]) == (LANE_YOURS, 1)
    assert (feed["lane"], feed["ahead"]) == (LANE_BACKGROUND, 2)


def test_standing_says_what_is_running_and_only_reports_a_typical_time_once_measured(store):
    async def go():
        rec = _Recorder()
        q = _queue(store, rec)
        q.start()
        before = []
        for i in range(3):
            q.enqueue(f"done-{i}")
            await _until(lambda i=i: len(rec.order) == i + 1 and q._running is None)
            before.append(q.standing("next"))
        rec.hold["slow"] = asyncio.Event()
        q.enqueue("slow")
        await _until(lambda: rec.order[-1:] == ["slow"])
        running = q.standing("slow")
        q.enqueue("after")
        waiting = q.standing("after")
        rec.hold["slow"].set()
        await _until(lambda: rec.order[-1:] == ["after"])
        q.stop()
        return before, running, waiting, q.standing("after")

    before, running, waiting, gone = asyncio.run(go())
    assert before == [None, None, None], "an item the queue does not hold has no standing"
    assert running["state"] == "running"
    assert running["typical_secs"] is not None, "three finished items make a typical time"
    assert waiting["state"] == "waiting" and waiting["ahead"] == 0
    assert gone is None


def test_a_typical_time_needs_three_finished_items(store):
    async def go():
        rec = _Recorder()
        q = _queue(store, rec)
        q.start()
        q.enqueue("one")
        await _until(lambda: rec.order == ["one"] and q._running is None)
        q.enqueue_background("waiting")
        standing = q.standing("waiting")
        q.stop()
        return standing

    assert asyncio.run(go())["typical_secs"] is None


def test_a_restart_puts_each_item_back_in_the_lane_it_belongs_to(store):
    """The lane was only in memory; recovery reads it off the item, so an upload a restart
    interrupted does not come back behind a whole watched folder."""
    sid = store.create_source(name="notes", provider="watched-dir", kind="dir", spec={})
    from_source = store.create_typed_item(
        item_type="note",
        title="n.md",
        content="n",
        provider="watched-dir",
        source_id=sid,
        guid="n.md",
        extra={"processing_status": "queued"},
    )
    upload = store.create_typed_item(
        item_type="note", title="memo", content="m", extra={"processing_status": "processing"}
    )

    async def go():
        q = KnowledgeIngestQueue(store)
        n = q.recover_pending()
        return n, q.standing(from_source), q.standing(upload)

    n, src, mine = asyncio.run(go())
    assert n == 2
    assert src["lane"] == LANE_BACKGROUND
    assert (mine["lane"], mine["ahead"]) == (LANE_YOURS, 0)


def test_an_enqueue_from_a_worker_thread_reaches_the_drain(store):
    """The memory vault's sync enqueues from ``asyncio.to_thread``."""

    async def go():
        rec = _Recorder()
        q = _queue(store, rec)
        q.start()
        await asyncio.to_thread(q.enqueue_background, "from-thread")
        await _until(lambda: rec.order == ["from-thread"], tries=400)
        q.stop()
        return rec.order

    assert asyncio.run(go()) == ["from-thread"]


def test_the_item_and_list_reads_say_where_a_queued_item_stands(store):
    """The page said "Enriching" with every step pending and nothing about the wait. The
    item and the library row now carry the queue's standing; a finished item carries none,
    and reading it never constructs a queue."""
    import json
    from types import SimpleNamespace

    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import knowledge as H

    feed = store.create_typed_item(
        item_type="note", title="feed", content="f", extra={"processing_status": "queued"}
    )
    memo = store.create_typed_item(
        item_type="note", title="memo", content="m", extra={"processing_status": "queued"}
    )
    done = store.create_typed_item(
        item_type="note", title="done", content="d", extra={"processing_status": "done"}
    )
    q = KnowledgeIngestQueue(store)
    q.enqueue_background(feed)
    q.enqueue(memo)

    def _app(queue):
        app = web.Application()
        app["state"] = SimpleNamespace(knowledge_store=store, _knowledge_ingest_queue=queue)
        return app

    def _get(item_id, queue=q):
        req = make_mocked_request(
            "GET", f"/api/knowledge/items/{item_id}", app=_app(queue), match_info={"id": item_id}
        )
        return json.loads(asyncio.run(H.get_item(req)).body)["queue"]

    assert _get(memo) == {
        "state": "waiting",
        "lane": LANE_YOURS,
        "ahead": 0,
        "running_since": None,
        "typical_secs": None,
    }
    assert _get(feed)["lane"] == LANE_BACKGROUND and _get(feed)["ahead"] == 1
    assert _get(done) is None
    assert _get(memo, queue=None) is None, "no live queue: queued, with no standing to state"

    req = make_mocked_request("GET", "/api/knowledge/items", app=_app(q))
    rows = {r["id"]: r for r in json.loads(asyncio.run(H.list_items(req)).body)["items"]}
    assert rows[memo]["queue"]["ahead"] == 0
    assert rows[done]["queue"] is None
