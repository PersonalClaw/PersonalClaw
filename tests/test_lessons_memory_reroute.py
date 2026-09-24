"""WF2LEA-3 regression: /api/lessons rides memory.db lesson.* (no JSONL store).

Success criterion 6 ("the /api/lessons consumers — MCP tools, dashboard, no-embedder
path — work identically after the consumer reroute onto memory.db"). The legacy JSONL
``LessonStore`` is deleted; every lesson read/write now goes through the memory service
onto memory.db ``lesson.*`` records. These tests pin:

* the three ``/api/lessons`` consumers (dashboard create / list / delete) round-trip a
  lesson through memory.db;
* an EMBEDDER-LESS write still persists and is retrievable by key/namespace (the subtle
  correctness point — the context.py fallback path used to write JSONL);
* the residual-JSONL backfill (``VectorMemoryStore.migrate_from_markdown``) is idempotent.

All state is under ``tmp_path`` / a monkeypatched config dir; the real ``~/.personalclaw``
is never touched.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.handlers.schedule import (
    api_lessons,
    api_lessons_create,
    api_lessons_delete,
)
from personalclaw.dashboard.state import DashboardState
from personalclaw.memory import MemoryStore
from personalclaw.vector_memory import VectorMemoryStore

# ── helpers ──────────────────────────────────────────────────────────────────


def _state_with_record_store(tmp_path, *, with_embedder: bool):
    """A DashboardState whose context_builder.memory carries a memory.db record
    store — the sole lesson backing. ``with_embedder=False`` exercises the
    embedder-less write path (vector optional; row still persists)."""
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    mem = MemoryStore(workspace=ws)
    mem.init()
    vs = VectorMemoryStore(db_path=tmp_path / "memory.db", embedding_dim=3)
    vs.init()
    if with_embedder:
        vs.embed_fn = lambda t: [1.0, 0.0, 0.0]
    mem.vector_store = vs

    cb = MagicMock()
    cb.memory = mem
    state = DashboardState(
        sessions=MagicMock(count=0),
        start_time=0.0,
        context_builder=cb,
    )
    return state, vs


def _req(state, *, body=None, session_key="dashboard:ui"):
    req = MagicMock()
    req.app = {"state": state}
    req.headers = {"X-Session-Key": session_key}
    req.query = {}

    async def _json():
        return body if body is not None else {}

    req.json = _json
    return req


async def _read_body(resp):
    return json.loads(resp.body)


# ── criterion 6: the three consumers round-trip through memory.db ──────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("with_embedder", [True, False])
async def test_dashboard_create_list_delete_roundtrip(tmp_path, with_embedder):
    """POST → GET → DELETE against /api/lessons all ride memory.db lesson.*,
    identically with and without an embedder configured."""
    state, vs = _state_with_record_store(tmp_path, with_embedder=with_embedder)

    # create
    resp = await api_lessons_create(
        _req(state, body={"rule": "always run make lint", "category": "process"})
    )
    assert (await _read_body(resp)) == {"ok": True}
    # the write landed in memory.db as a lesson.* record
    assert any(json.loads(e["value_json"]) == "always run make lint" for e in vs.get_lessons())

    # list (the MCP memory_list / dashboard read path)
    resp = await api_lessons(_req(state))
    data = (await _read_body(resp))["lessons"]
    assert [le["rule"] for le in data] == ["always run make lint"]

    # delete (memory_forget)
    resp = await api_lessons_delete(_req(state, body={"rule": "make lint"}))
    assert (await _read_body(resp))["ok"] is True
    assert vs.get_lessons() == []


# ── the subtle correctness point: embedder-less write persists + retrievable ───


def test_embedderless_write_persists_and_is_retrievable(tmp_path):
    """A lesson written with NO embedder configured persists to memory.db without a
    vector and is retrievable by its lesson.* key/namespace (the context.py no-embedder
    fallback that used to write JSONL)."""
    vs = VectorMemoryStore(db_path=tmp_path / "memory.db")  # embed_fn is None
    vs.init()
    assert vs.embed_fn is None

    assert vs.write_lesson("prefer uv over pip", "tool") is True

    rows = vs.get_lessons()
    assert len(rows) == 1
    row = rows[0]
    assert row["key"].startswith("lesson.")  # namespaced key
    assert json.loads(row["value_json"]) == "prefer uv over pip"
    # No vector was stored (embedder-less), yet the record is fully present.
    assert not row.get("embedding")
    # Retrievable by key through the semantic read path.
    assert vs.get_semantic(row["key"]) is not None
    vs.close()


# ── residual JSONL import is an idempotent backfill ────────────────────────────


def test_residual_jsonl_backfill_is_idempotent(tmp_path, monkeypatch):
    """Seeding a residual lessons.jsonl then running the backfill twice yields exactly
    two lesson.* records (no duplicates), and they read back through the reroute."""
    home = tmp_path / "home"
    home.mkdir()
    # migrate_from_markdown resolves the JSONL under the resolved config home.
    monkeypatch.setattr("personalclaw.vector_memory._path_home_pclaw", lambda: home)
    (home / "lessons.jsonl").write_text(
        json.dumps({"ts": "seed", "rule": "use snake_case", "category": "tool"})
        + "\n"
        + json.dumps({"ts": "seed", "rule": "write tests first", "category": "knowledge"})
        + "\n"
    )

    vs = VectorMemoryStore(db_path=tmp_path / "memory.db")
    vs.init()
    c1 = vs.migrate_from_markdown()
    c2 = vs.migrate_from_markdown()  # rerun must not duplicate
    assert c1["semantic"] == 2
    assert c2["semantic"] == 0  # every row already present → skipped

    rules = {json.loads(e["value_json"]) for e in vs.get_lessons()}
    assert rules == {"use snake_case", "write tests first"}
    assert len(vs.get_lessons()) == 2
    vs.close()
