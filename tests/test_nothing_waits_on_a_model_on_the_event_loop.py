"""Nothing on the event loop waits on a model for memory or embedding work.

A memory read or write can wait on a model: a recall embeds the question, a fact, a lesson or an
episode is embedded when it is written, and a lesson may be judged against the ones it might
contradict. So can the embedding a route or a turn does for itself: a send's routing suggestion
embeds the message, putting a turn's message together embeds it for its memory and its skill
match (a chat's, a webhook's, a heartbeat's, a subagent's), and starting a re-index embeds a probe
with the model. Inside an ``async def`` that wait stops the event loop, so every request the
gateway is serving waits with it: a health check sent during one recall took exactly as long as
the recall did (19.5 s). The knowledge search routes already hand their work to a worker thread;
these rails hold everything else to the same rule.

Derived from the source, not from a list of routes: every ``async def`` in the package is read,
and a call to one of the model-bound operations below must sit inside the arguments of
``asyncio.to_thread`` / ``run_in_executor``, or inside a function handed to one of them.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import math
import re
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore

_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: Operations whose work can include a round trip to a model: memory operations that embed a
#: question or a memory, or judge a lesson; putting a turn's message together; and the embedding
#: of a send's routing suggestion, a skill match and a re-index's readiness probe. Named by
#: method, whichever object it is called on, so a new caller is read without anyone adding it.
MODEL_BOUND = frozenset(
    {
        "semantic_context",
        "get_semantic_context",
        "recall_lessons",
        "recall_facts",
        "recall_with_provenance",
        "rank_episodic",
        "search_episodic",
        "get_episodic_context",
        "episodic_context",
        "active_recall",
        "rank_semantic",
        "rank_lessons",
        "get_context_preview",
        "embed_query",
        "set_semantic",
        "write_episodic",
        "write_lesson",
        "import_memory",
        "migrate_from_markdown",
        "reembed_stale",
        "count_to_reembed",
        "promote_episodic_patterns",
        "suggest_for_send",
        "surface_skills",
        "search_skills",
        "_resolve_embed",
        "assemble_context",
        "build_message",
    }
)
_HAND_OFF = frozenset({"to_thread", "run_in_executor"})


def _name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _sites() -> tuple[list[str], int]:
    """``(calls on the event loop, calls handed off)`` across every ``async def`` in the package."""
    on_loop: list[str] = []
    handed_off = 0
    for path in sorted(_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Each call is read once, from the outermost ``async def`` around it: ``ast.walk`` reaches
        # an ``async def`` before the ones nested in it, and a nested one's calls are its too.
        read: set[int] = set()
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            off: set[int] = set()
            handed: set[str] = set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and _name(node.func) in _HAND_OFF:
                    off.update(id(n) for n in ast.walk(node))
                    handed.update(_name(a) for a in node.args if _name(a))
                    if id(node) not in read:
                        # `asyncio.to_thread(svc.set_semantic, ...)`: the operation, handed off.
                        handed_off += sum(1 for a in node.args if _name(a) in MODEL_BOUND)
            for node in ast.walk(fn):
                if isinstance(node, ast.FunctionDef) and node.name in handed:
                    off.update(id(n) for n in ast.walk(node))
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call) or id(node) in read:
                    continue
                read.add(id(node))
                if _name(node.func) not in MODEL_BOUND:
                    continue
                if id(node) in off:
                    handed_off += 1
                else:
                    rel = path.relative_to(_PACKAGE).as_posix()
                    on_loop.append(f"{rel}:{node.lineno} {fn.name} .{_name(node.func)}()")
    return on_loop, handed_off


def test_nothing_on_the_event_loop_waits_on_a_model():
    """🔴 Red before: twenty calls in sixteen functions: the recall's three, a send's routing
    suggestion, the re-index's probe, and putting together the message of a chat turn, a webhook's
    turn, a heartbeat, a subagent and the two turns that read a subagent's result."""
    on_loop, handed_off = _sites()
    # Vacuity floor: a matcher that stopped matching would find nothing anywhere and pass.
    seen = len(on_loop) + handed_off
    assert seen >= 20, f"the census found only {seen} model-bound calls: is it reading?"
    listed = "\n".join(on_loop)
    assert not on_loop, f"work that can wait on a model runs on the event loop:\n{listed}"


# ── one route, driven: teaching a lesson while the model is slow ─────────────────────────────


def _bag(text: str) -> list[float]:
    vec = [0.0] * 32
    for word in re.findall(r"\w+", text.lower()):
        bits = int(hashlib.sha256(word.encode()).hexdigest(), 16)
        for i in range(32):
            vec[i] += 1.0 if (bits >> i) & 1 else -1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


@pytest.fixture
def slow_store(tmp_path, monkeypatch):
    latency = {"secs": 0.0}

    def _embed(text: str) -> list[float]:
        time.sleep(latency["secs"])
        return _bag(text)

    monkeypatch.setattr(VectorMemoryStore, "_embedder", lambda self: (_embed, "test:bag"))
    monkeypatch.setattr(VectorMemoryStore, "_embedding_ref", lambda self: "test:bag")
    store = VectorMemoryStore(db_path=tmp_path / "memory.db")
    store.init()
    store._graph_enabled = False
    yield store, latency
    store.close()


def _app(store: VectorMemoryStore) -> web.Application:
    from personalclaw.dashboard.handlers.memory import api_memory_semantic_write
    from personalclaw.dashboard.handlers.schedule import api_lessons_create
    from personalclaw.dashboard.handlers_system import api_healthz

    app = web.Application()
    app["state"] = MagicMock(
        _sessions={},
        context_builder=types.SimpleNamespace(memory=types.SimpleNamespace(vector_store=store)),
    )
    app.router.add_get("/api/healthz", api_healthz)
    app.router.add_post("/api/lessons", api_lessons_create)
    app.router.add_put("/api/memory/semantic", api_memory_semantic_write)
    return app


async def _health_while(client: TestClient, method: str, path: str, body: dict) -> tuple:
    write = asyncio.ensure_future(
        client.request(method, path, json=body, headers={"X-Session-Key": "dashboard:ui"})
    )
    # Timed from here: on a blocked loop the pause itself waits for the write.
    start = time.monotonic()
    await asyncio.sleep(0.1)
    health = await client.get("/api/healthz")
    took = time.monotonic() - start - 0.1
    still_writing = not write.done()
    resp = await write
    return health.status, took, still_writing, resp.status


@pytest.mark.asyncio
async def test_the_gateway_answers_while_a_lesson_is_embedded(slow_store):
    """🔴 Red before: ``POST /api/lessons`` embedded the lesson on the event loop."""
    store, latency = slow_store
    latency["secs"] = 0.8
    with patch("personalclaw.dashboard.handlers.schedule._sel", return_value=MagicMock()):
        async with TestClient(TestServer(_app(store))) as client:
            health, took, still_writing, status = await _health_while(
                client, "POST", "/api/lessons", {"rule": "The dishwasher goes left of the sink."}
            )

    assert health == 200
    assert took < 0.5, f"/api/healthz took {took:.2f}s while a lesson was embedded"
    assert still_writing, "premise: the lesson was still being embedded"
    assert status == 200
    assert [r["key"] for r in store.get_lessons()], "and the lesson was saved"


@pytest.mark.asyncio
async def test_the_gateway_answers_while_a_fact_is_embedded(slow_store):
    """A fact is embedded when it is written, so its route hands the write off as well."""
    store, latency = slow_store
    latency["secs"] = 0.8
    svc = MemoryService.over_vector_store(store)
    with (
        patch("personalclaw.dashboard.handlers.memory._get_service", return_value=svc),
        patch("personalclaw.dashboard.handlers.memory._sel", return_value=MagicMock()),
    ):
        async with TestClient(TestServer(_app(store))) as client:
            health, took, still_writing, status = await _health_while(
                client, "PUT", "/api/memory/semantic", {"key": "pref.editor", "value": "vim"}
            )

    assert health == 200
    assert took < 0.5, f"/api/healthz took {took:.2f}s while a fact was embedded"
    assert still_writing, "premise: the fact was still being embedded"
    assert status == 200
    assert store.get_semantic("pref.editor") is not None
