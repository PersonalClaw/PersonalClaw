"""A lesson is listed once, as a lesson, on the reads the Memory studio is built from.

A lesson is stored as a semantic row keyed ``lesson.<hash>``. The studio lists its lessons from
``GET /api/lessons`` and draws its graph from ``GET /api/memory/graph``; both must hold every
lesson exactly once:

* the lessons list is the whole inventory. It used to keep 50 rows of a newest-first read from
  the END, so past 50 lessons the NEWEST ones were the ones missing — and a lesson the list
  leaves out is one the studio cannot show, open from a citation, or delete;
* the graph draws a lesson as its lesson node only. It also drew the same row again as a raw
  ``lesson.<hash>`` fact node.

All state is under ``tmp_path``; the real home is never touched.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.handlers import api_memory_graph
from personalclaw.dashboard.handlers.schedule import api_lessons
from personalclaw.dashboard.state import DashboardState
from personalclaw.memory import MemoryStore
from personalclaw.vector_memory import VectorMemoryStore


def _state(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    mem = MemoryStore(workspace=ws)
    mem.init()
    vs = VectorMemoryStore(db_path=tmp_path / "memory.db", embedding_dim=3)
    vs.init()
    mem.vector_store = vs
    cb = MagicMock()
    cb.memory = mem
    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0, context_builder=cb)
    return state, vs


def _req(state):
    req = MagicMock()
    req.app = {"state": state}
    req.headers = {"X-Session-Key": "dashboard:ui"}
    req.query = {}
    return req


def _lesson(vs: VectorMemoryStore, n: int, updated_at: str) -> str:
    """A lesson with a distinct rule (no shared words, so none dedups another), last updated
    at *updated_at*."""
    rule = f"Rule{n:03d}: prefer option{n:03d}"
    key = f"lesson.rule{n:03d}"
    vs.db.execute(
        "INSERT INTO semantic_memory (key, value_json, confidence, source, created_at, updated_at) "
        "VALUES (?, ?, 0.9, 'user_explicit', ?, ?)",
        (key, json.dumps(rule), updated_at, updated_at),
    )
    vs.db.commit()
    return rule


@pytest.mark.asyncio
async def test_the_lessons_list_holds_every_lesson_the_newest_included(tmp_path, monkeypatch):
    state, vs = _state(tmp_path, monkeypatch)
    rules = [_lesson(vs, n, f"2026-09-{1 + n // 24:02d}T{n % 24:02d}:00:00Z") for n in range(60)]
    newest = rules[-1]

    resp = await api_lessons(_req(state))
    listed = [le["rule"] for le in json.loads(resp.body)["lessons"]]

    assert len(listed) == 60, "every lesson is listed"
    assert sorted(listed) == sorted(rules)
    assert newest in listed, "the lesson taught last is the one a cap dropped"


@pytest.mark.asyncio
async def test_the_graph_draws_a_lesson_once_as_its_lesson(tmp_path, monkeypatch):
    state, vs = _state(tmp_path, monkeypatch)
    rule = _lesson(vs, 1, "2026-09-30T12:00:00Z")
    vs.db.execute(
        "INSERT INTO semantic_memory (key, value_json, confidence, source, created_at, updated_at) "
        "VALUES ('user.favorite_language', '\"Python\"', 1.0, 'user_explicit', "
        "'2026-09-30T12:00:00Z', '2026-09-30T12:00:00Z')"
    )
    vs.db.commit()

    resp = await api_memory_graph(_req(state))
    nodes = json.loads(resp.body)["nodes"]

    lesson_nodes = [n for n in nodes if n["group"] == "lesson"]
    assert [n["ref"] for n in lesson_nodes] == [f"lesson:{rule}"]
    raw = [n for n in nodes if n["ref"].startswith("sem:lesson.")]
    assert raw == [], "no raw lesson.<hash> fact node"
    # An ordinary fact still is a fact node.
    facts = [n["ref"] for n in nodes if n["group"] == "semantic"]
    assert facts == ["sem:user.favorite_language"]
