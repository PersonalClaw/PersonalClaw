"""A memory recall answers within the agent's tool budget, and never stops the gateway meanwhile.

Measured on a running install with a cloud embedding model bound: four ``memory_recall`` calls in a
row came back "Error: timed out", and nothing in the gateway log said why. Two calls to the same
route took 19.6 s and 25.9 s, and a health check sent while each ran waited exactly as long. On a
copy of that store, at half a second per embedding, one recall made 39 embedding calls: the
question three times, once per arm, and every saved fact and lesson once each, because the fact
and lesson ranking embedded each stored row again at every question. All of it ran inside the
route on the event loop, so every other request waited, and the tool gave up after 10 s.

These drive the real route with a scripted embedding model whose every call is counted and whose
latency is set per test. Nothing in the recall itself is mocked.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import memory_service
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore

LESSON = "Noor is left-handed, so the dishwasher goes to the left of the sink."
FACTS = {
    "pref.kitchen_dishwasher_placement": "left of the sink",
    "pref.editor": "vim",
    "pref.test_framework": "pytest",
    "user.location": "Toronto",
    "project.talk.opening_minutes": "ten",
    "pref.commit_convention": "conventional commits",
}
EPISODE = "She asked where the dishwasher should go in the kitchen plan."
OTHER_EPISODE = "The deploy runbook was reviewed on Tuesday morning."


def _bag(text: str) -> list[float]:
    """A deterministic 32-wide bag-of-words vector: texts are close only through shared words."""
    vec = [0.0] * 32
    for word in re.findall(r"\w+", text.lower()):
        bits = int(hashlib.sha256(word.encode()).hexdigest(), 16)
        for i in range(32):
            vec[i] += 1.0 if (bits >> i) & 1 else -1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class _Model:
    """A scripted embedding model: it answers after ``latency`` seconds and counts every call."""

    def __init__(self) -> None:
        self.latency = 0.0
        self.calls: list[str] = []
        self.ref = "test:bag"

    def __call__(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.latency:
            time.sleep(self.latency)
        return _bag(text)


@pytest.fixture
def model(monkeypatch) -> _Model:
    m = _Model()
    monkeypatch.setattr(VectorMemoryStore, "_embedder", lambda self: (m, m.ref))
    monkeypatch.setattr(VectorMemoryStore, "_embedding_ref", lambda self: m.ref)
    return m


@pytest.fixture
def store(tmp_path, model):
    s = VectorMemoryStore(db_path=tmp_path / "memory.db")
    s.init()
    s._graph_enabled = False
    assert s.write_lesson(LESSON, source="user_explicit")
    for key, value in FACTS.items():
        assert s.set_semantic(key, value, 1.0, "user_explicit") is None
    for text in (EPISODE, OTHER_EPISODE):
        assert s.write_episodic(text, conversation_id="chat-1", source="user_explicit")
    yield s
    s.close()


def _app() -> web.Application:
    from personalclaw.dashboard.handlers.memory import api_memory_recall
    from personalclaw.dashboard.handlers_system import api_healthz

    app = web.Application()
    app["state"] = MagicMock(_sessions={})
    app.router.add_get("/api/healthz", api_healthz)
    app.router.add_get("/api/memory/recall", api_memory_recall)
    return app


def _serving(svc: MemoryService):
    """The recall route served over ``svc``, as the gateway serves it for an owner's chat."""
    return (
        patch("personalclaw.dashboard.handlers.memory._get_service", return_value=svc),
        patch("personalclaw.dashboard.handlers.memory._blocks_reads_session", return_value=False),
    )


async def _recall(svc: MemoryService, query: str) -> tuple[int, dict[str, Any], float]:
    service, blocks = _serving(svc)
    with service, blocks:
        async with TestClient(TestServer(_app())) as client:
            start = time.monotonic()
            resp = await client.get("/api/memory/recall", params={"q": query})
            took = time.monotonic() - start
            return resp.status, await resp.json(), took


def _recall_warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "memory recall" in r.getMessage().lower()
    ]


@pytest.mark.asyncio
async def test_the_gateway_answers_while_a_recall_waits_on_the_embedding_model(store, model):
    """🔴 Red before: the recall ran on the event loop, so the health check waited for all of it."""
    model.latency = 0.6
    svc = MemoryService.over_vector_store(store)
    service, blocks = _serving(svc)
    with service, blocks:
        async with TestClient(TestServer(_app())) as client:
            recall = asyncio.ensure_future(
                client.get("/api/memory/recall", params={"q": "dishwasher"})
            )
            # Timed from here: on a blocked loop the pause itself waits for the recall.
            start = time.monotonic()
            await asyncio.sleep(0.1)
            health = await client.get("/api/healthz")
            took = time.monotonic() - start - 0.1
            still_recalling = not recall.done()
            resp = await recall
            body = await resp.json()

    assert health.status == 200
    assert took < 0.5, f"/api/healthz took {took:.2f}s while a recall waited on the model"
    assert still_recalling, "premise: the recall was still waiting on the model"
    assert resp.status == 200 and LESSON in body["result"]


@pytest.mark.asyncio
async def test_a_recall_embeds_the_question_once_and_no_stored_memory(store, model):
    """🔴 Red before: the question three times, then every fact and lesson again, each its own
    round trip to the model."""
    model.calls.clear()
    status, body, _took = await _recall(MemoryService.over_vector_store(store), "dishwasher")

    assert status == 200
    assert model.calls == ["dishwasher"], model.calls
    result = body["result"]
    assert f"- {LESSON}" in result, "what she taught is still recalled"
    assert "pref.kitchen_dishwasher_placement" in result, "and the fact that names it"
    assert EPISODE in result, "and the conversation about it"
    assert OTHER_EPISODE not in result


@pytest.mark.asyncio
async def test_a_question_the_model_does_not_embed_in_time_is_answered_by_keyword_and_says_so(
    store, model, monkeypatch, caplog
):
    """🔴 Red before: the recall waited for the model however long it took, said nothing, and
    logged nothing."""
    monkeypatch.setattr(memory_service, "QUERY_EMBED_BUDGET_SECS", 0.2, raising=False)
    model.latency = 1.5
    caplog.set_level(logging.WARNING)

    status, body, took = await _recall(MemoryService.over_vector_store(store), "dishwasher")

    assert status == 200
    assert took < 1.0, f"the recall waited {took:.2f}s for a model past its 0.2s budget"
    result = body["result"]
    assert f"- {LESSON}" in result, "the lesson that holds the word is found by keyword"
    assert (
        "Searched by keyword only: the embedding model did not answer within 0.2 s, so a memory "
        "that matches the question only in meaning may be missing." in result
    ), result
    warnings = _recall_warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "embedding the question" in warnings[0] and "0.2 s" in warnings[0], warnings[0]
    assert "dishwasher" not in warnings[0], "the log names the stage, never what was asked"


@pytest.mark.asyncio
async def test_a_recall_that_runs_out_of_time_says_where_it_was_and_answers_in_time(
    store, model, monkeypatch, caplog
):
    """🔴 Red before: nothing bounded the recall, so the agent's tool gave up first and read
    "timed out" with no stage, and the route answered later into a closed connection."""
    monkeypatch.setattr(memory_service, "RECALL_BUDGET_SECS", 0.3, raising=False)
    real = MemoryService.recall_with_provenance

    def _slow(self, *args, **kwargs):
        time.sleep(1.0)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(MemoryService, "recall_with_provenance", _slow)
    caplog.set_level(logging.WARNING)

    status, body, took = await _recall(MemoryService.over_vector_store(store), "dishwasher")

    assert took < 0.9, f"the route answered after {took:.2f}s, past its 0.3s budget"
    assert status == 504
    assert body["error"]["code"] == "memory_recall_timeout"
    assert body["error"]["message"] == (
        "Memory search did not finish within 0.3 s (it was still searching past conversations), "
        "so nothing was recalled this time. The memories are intact; ask again in a moment."
    )
    warnings = _recall_warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "searching past conversations" in warnings[0] and "0.3 s" in warnings[0]


def _stops_waiting_then_probes(base: str, *, after: float, probe_for: float) -> tuple[bool, list]:
    """The agent's tool and a health monitor, as the gateway's callers are: each in a thread of its
    own with its own timeout. The tool asks for a recall and stops waiting after ``after`` seconds,
    as ``mcp_core`` does after ten; then a health check is sent every 50 ms for ``probe_for``
    seconds. Returns whether the tool stopped waiting, and how long each health check took."""
    stopped = False
    try:
        urllib.request.urlopen(f"{base}/api/memory/recall?q=dishwasher", timeout=after).read()
    except TimeoutError:
        stopped = True
    except urllib.error.URLError as exc:
        stopped = isinstance(exc.reason, TimeoutError)
    took: list[float] = []
    until = time.monotonic() + probe_for
    while time.monotonic() < until:
        start = time.monotonic()
        with urllib.request.urlopen(f"{base}/api/healthz", timeout=30) as resp:
            resp.read()
        took.append(time.monotonic() - start)
        time.sleep(0.05)
    return stopped, took


@pytest.mark.asyncio
async def test_the_gateway_is_free_the_moment_the_agent_stops_waiting_for_a_recall(store, model):
    """🔴 Red before: when the agent's tool stopped waiting, the route went on embedding on the
    event loop, so the gateway answered nothing else until the recall nobody read was done (a
    health check waited 163 s once, on a running install)."""
    model.latency = 0.4
    service, blocks = _serving(MemoryService.over_vector_store(store))
    with service, blocks:
        async with TestClient(TestServer(_app())) as client:
            base = str(client.make_url("")).rstrip("/")
            stopped, took = await asyncio.to_thread(
                _stops_waiting_then_probes, base, after=0.2, probe_for=1.5
            )

    assert stopped, "premise: the tool stopped waiting before the recall answered"
    assert took, "no health check was answered at all"
    assert max(took) < 0.3, f"a health check took {max(took):.2f}s after the tool stopped waiting"


@pytest.mark.asyncio
async def test_a_recall_the_route_stopped_waiting_for_goes_no_further(store, model, monkeypatch):
    """🔴 Red before: nothing stopped a recall once nobody waited for it: it went on ranking,
    searching and bumping the recalled facts' counts for an answer nobody read."""
    monkeypatch.setattr(memory_service, "RECALL_BUDGET_SECS", 0.3, raising=False)
    model.latency = 0.6
    went_on: list[str] = []
    for name in ("semantic_context", "recall_lessons", "recall_with_provenance", "record_recall"):
        real = getattr(MemoryService, name)

        def _noting(self, *args, _real=real, _name=name, **kwargs):
            went_on.append(_name)
            return _real(self, *args, **kwargs)

        monkeypatch.setattr(MemoryService, name, _noting)

    status, body, _took = await _recall(MemoryService.over_vector_store(store), "dishwasher")
    assert status == 504
    assert "it was still embedding the question" in body["error"]["message"]
    # Past the moment the model answers the question the abandoned recall had asked it.
    await asyncio.sleep(1.0)
    assert went_on == [], f"the recall the route gave up on went on to {went_on}"


def test_the_recall_budget_fits_inside_the_agents_tool_budget():
    """The route answers, in words, before the agent's tool stops waiting for it."""
    from personalclaw import mcp_core

    assert memory_service.QUERY_EMBED_BUDGET_SECS < memory_service.RECALL_BUDGET_SECS
    assert memory_service.RECALL_BUDGET_SECS < mcp_core.GATEWAY_READ_TIMEOUT_SECS


# ── a re-index never makes a search wait for it ─────────────────────────────────────────────


KESTREL = "A kestrel hovered over the field by the barn."
OTHERS = (
    "The allotment committee moved the seed swap to the first Saturday in March.",
    "Invoices for the roofing job go to the accounts address, never the site lead.",
    "Sam prefers the window seat on long train journeys to Montreal.",
    "The library returns box by the station closes at nine on weekdays.",
    "Grandma's lentil soup needs cumin added only at the very end.",
)


def test_a_search_during_a_reindex_never_waits_for_the_index_to_be_rebuilt(tmp_path, model):
    """🔴 Red before: a search that found the index on another model's vectors, or behind the
    re-index, rebuilt it then and there, so every search during a re-index paid for a rebuild of
    the whole index, or waited for the one in progress. A search answers from what it has: the
    index it can read and, beside it, the memories re-embedded since, compared one by one."""
    import pytest as _pytest

    _pytest.importorskip("faiss")
    store = VectorMemoryStore(db_path=tmp_path / "memory.db")
    store.init()
    store._graph_enabled = False
    model.ref = "test:a"
    for text in (KESTREL, *OTHERS):
        assert store.write_episodic(text, conversation_id="chat-1", source="user_explicit")

    # A rebind: the new model embeds slowly, and building its index takes a while.
    model.ref = "test:b"
    model.latency = 0.3
    real_build = VectorMemoryStore._build_index_for
    building = threading.Event()

    def _slow_build(self, space):
        with self._index_lock:
            building.set()
            time.sleep(1.2)
            return real_build(self, space)

    store._build_index_for = _slow_build.__get__(store)  # type: ignore[method-assign]
    first_row = threading.Event()
    reindex = threading.Thread(
        target=store.reembed_stale, kwargs={"on_progress": lambda d, _t: first_row.set()}
    )
    reindex.start()
    try:
        # Whichever comes first: the re-index rebuilding the index, or its first memory.
        deadline = time.monotonic() + 5
        while not (building.is_set() or first_row.is_set()) and time.monotonic() < deadline:
            time.sleep(0.01)
        start = time.monotonic()
        during = store.search_episodic(query_embedding=_bag("kestrel"), query_text="kestrel")
        took = time.monotonic() - start
        assert took < 0.4, f"a search waited {took:.2f}s for the index to be rebuilt"
        assert KESTREL in {r["text"] for r in during}, "the memory is found meanwhile"

        assert first_row.wait(timeout=10)
        start = time.monotonic()
        after_one = store.search_episodic(query_embedding=_bag("kestrel"), query_text="kestrel")
        took = time.monotonic() - start
        assert took < 0.4, f"a search waited {took:.2f}s once the re-index had re-embedded one"
        assert KESTREL in {r["text"] for r in after_one}
    finally:
        reindex.join(timeout=30)
        store.close()
