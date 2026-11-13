"""A lesson has no category, and nothing that reads or writes one says it has.

A lesson is stored as its rule alone (``vector_memory._lesson_record``); no store keeps a
category. Yet ``GET /api/lessons`` answered ``"category": "knowledge"`` for every lesson,
``POST /api/lessons`` and ``memory_remember`` took one and answered "ok", and ``memory_list``
tagged every lesson ``[knowledge]``. So an app or an agent that saved a lesson as a tool or a
preference was told it had, and read every lesson back as knowledge.

Now, as ``personalclaw learn add`` refuses ``--category``:

* ``POST /api/lessons`` refuses a category with a 400 that says so, and saves nothing;
* ``memory_remember`` publishes no category, and its validator refuses one, naming what it takes;
* ``GET /api/lessons`` lists no category, and ``memory_list`` tags no lesson with one;
* the bundled skill that teaches a ``memory_remember`` call teaches one the tool takes.

Asserted against the real route over a memory database under ``tmp_path``. What the bundled
skills teach is held by ``tests/rails/test_a_lesson_has_no_category_tree.py``.
"""

from __future__ import annotations

import json

import pytest
import test_lessons_memory_reroute as reroute

from personalclaw import mcp_memory
from personalclaw.dashboard.handlers.schedule import api_lessons, api_lessons_create

#: What the route answers a lesson sent with a category.
NO_CATEGORY = (
    "A lesson takes no category, since no lesson stores one, so nothing was saved: send the rule "
    "without it."
)
RULE = "Answer billing questions with the invoice number first."

_state = reroute._state_with_record_store
_req = reroute._req


def _remember_schema() -> dict:
    [tool] = [t for t in mcp_memory._list_tools() if t["name"] == "memory_remember"]
    return tool["inputSchema"]


# ── the lessons route ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_lesson_sent_with_a_category_is_refused_and_nothing_is_saved(tmp_path):
    state, vs = _state(tmp_path, with_embedder=False)
    resp = await api_lessons_create(_req(state, body={"rule": RULE, "category": "tool"}))
    assert (resp.status, json.loads(resp.body)) == (
        400,
        {"error": {"code": "bad_request", "message": NO_CATEGORY}},
    )
    assert vs.get_lessons() == []


@pytest.mark.asyncio
async def test_a_lesson_is_listed_without_a_category(tmp_path):
    state, _vs = _state(tmp_path, with_embedder=False)
    saved = await api_lessons_create(_req(state, body={"rule": RULE}))
    assert json.loads(saved.body) == {"ok": True}
    [row] = json.loads((await api_lessons(_req(state))).body)["lessons"]
    assert row["rule"] == RULE
    assert "category" not in row


# ── the memory tools ─────────────────────────────────────────────────────────


def test_memory_remember_publishes_no_category():
    schema = _remember_schema()
    assert "category" not in schema["properties"]
    assert schema["required"] == ["rule"]


def test_memory_remember_refuses_a_category_and_sends_nothing(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(mcp_memory, "_post", lambda _path, payload: sent.append(payload) or {})
    out = mcp_memory._call_tool("memory_remember", {"rule": RULE, "category": "preference"})
    assert "category" in out and "unknown field" in out, out
    assert sent == []

    saved = mcp_memory._call_tool("memory_remember", {"rule": RULE})
    assert saved == f"Saved lesson (global): {RULE}"
    assert sent == [{"rule": RULE, "scope": "global"}]


@pytest.mark.asyncio
async def test_memory_list_tags_no_lesson_with_a_category(tmp_path, monkeypatch):
    """``memory_list`` reads the route's own answer."""
    state, _vs = _state(tmp_path, with_embedder=False)
    await api_lessons_create(_req(state, body={"rule": RULE}))
    listed = json.loads((await api_lessons(_req(state))).body)
    monkeypatch.setattr(mcp_memory, "_get", lambda _url: listed)
    assert mcp_memory._call_tool_inner("memory_list", {}) == RULE
