"""Embedding an item leaves the gateway answering.

An ingest's last steps write the item's vectors: one from the bound embedding model for the item
and one for each of its passages, and then a comparison with the items like it. Before an item is
read, the ingest queue builds the embedder for it, and building one asks the bound model for a
vector to learn its width. A bound model's answer is waited for in the calling thread
(``run_embed_sync``), so on the event loop each answer stopped every request the gateway had for as
long as the model took to give it. All three run in a worker thread, as the embedding re-index runs
the same code.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from personalclaw.embedding_providers.base import run_embed_sync
from personalclaw.knowledge.ingest_queue import KnowledgeIngestQueue
from personalclaw.knowledge.pipeline import ensure_nodes_registered
from personalclaw.knowledge.pipeline.runner import ingest_item
from personalclaw.knowledge.store import KnowledgeStore

#: How long the model takes to answer each request.
_ANSWER_SECONDS = 1.5
#: The longest the event loop may go without running while the model answers: half of one answer.
_LOOP_BOUND = 0.75


class _SlowModel:
    """An embedding model that takes a while to answer, waited for as a bound one is."""

    def __init__(self) -> None:
        self.asked = 0

    def _answer(self) -> list[float]:
        self.asked += 1

        async def answer() -> list[float]:
            await asyncio.sleep(_ANSWER_SECONDS)
            return [0.1, 0.2, 0.3, 0.4]

        return run_embed_sync(answer, timeout=30)

    def embed_for_item(self, title, summary, content):
        return self._answer()

    def embed(self, text):
        return self._answer()

    def is_available(self) -> bool:
        return True


async def _worst_gap_while(work):
    """Run *work* while a 10 ms tick runs beside it; return its result and the tick's worst gap."""
    worst = 0.0
    done = asyncio.Event()

    async def tick() -> None:
        nonlocal worst
        last = time.monotonic()
        while not done.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            worst = max(worst, now - last)
            last = now

    ticker = asyncio.create_task(tick())
    try:
        result = await work
    finally:
        done.set()
        await ticker
    # Read after the tick's last turn: a stop that ended as the work did is measured by that turn.
    return result, worst


@pytest.mark.asyncio
async def test_the_gateway_keeps_answering_while_an_item_is_embedded(tmp_path):
    ensure_nodes_registered()
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    item_id = store.create_typed_item(
        item_type="note",
        title="Release checklist",
        content="Tag the build, write the notes, and tell the people who asked for the fix.",
    )
    store.db.commit()
    model = _SlowModel()

    _status, worst = await _worst_gap_while(ingest_item(store, item_id, embedder=model))

    item = store.get_item(item_id)
    assert item["has_embedding"], item.get("file_metadata")
    assert model.asked >= 2, "the item's own vector and its passage's"
    assert store.db.execute(
        "SELECT COUNT(*) FROM chunks WHERE item_id = ? AND embedding IS NOT NULL", (item_id,)
    ).fetchone()[0]
    assert worst < _LOOP_BOUND, f"the event loop stopped for {worst:.2f}s"


@pytest.mark.asyncio
async def test_the_gateway_keeps_answering_while_the_queue_finds_the_embedding_model(tmp_path):
    ensure_nodes_registered()
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    item_id = store.create_typed_item(
        item_type="note", title="Standup", content="The release waits on one review."
    )
    store.db.commit()
    model = _SlowModel()

    def embedder_for_this_item():
        # What building the embedder does for a bound model: one vector, to learn its width.
        model.embed("dimension probe")
        return None

    queue = KnowledgeIngestQueue(store, embedder_factory=embedder_for_this_item)

    async def read_through_the_queue() -> None:
        queue.start()
        queue.enqueue(item_id)
        try:
            for _ in range(1500):
                if model.asked and queue.standing(item_id) is None:
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("the queue never finished the item")
        finally:
            queue.stop()

    _none, worst = await _worst_gap_while(read_through_the_queue())

    assert model.asked == 1
    assert store.get_item(item_id)["processing_status"] not in ("queued", "processing")
    assert worst < _LOOP_BOUND, f"the event loop stopped for {worst:.2f}s"
