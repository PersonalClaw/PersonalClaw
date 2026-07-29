"""HTTP surface for bulk curation (KNOWLEDGE-LIBRARY S2, T2.3).

The store-level behavior lives in `test_knowledge_collections.py`; these cover the
endpoint's own job — argument validation, the typed error envelopes, the selection cap,
and passing per-item outcomes through unchanged.

Worth stating why the endpoint validates at all rather than just forwarding: a caller
that forgot `collection_id` would otherwise get a cheerful `{"ok": true}` with an empty
`changed` list over a 40-item selection, which reads as "nothing matched" instead of
"you left out an argument".
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.knowledge.store import KnowledgeStore


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(tmp_path / "k.db")


def _item(store, title: str) -> str:
    return store.create_typed_item(item_type="note", title=title, content="body")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _post(store, body):
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    req = make_mocked_request("POST", "/api/knowledge/bulk", app=app)

    async def _json():
        return body

    req.json = _json
    from personalclaw.dashboard.handlers import knowledge as H

    resp = _run(H.bulk_items(req))
    return resp, json.loads(resp.body)


def test_a_successful_bulk_returns_per_item_outcomes(store):
    a, b = _item(store, "A"), _item(store, "B")
    store.set_read_state(b, "read")

    resp, body = _post(store, {"op": "read_state", "item_ids": [a, b, "ghost"], "state": "read"})

    assert resp.status == 200
    assert body["ok"] is True and body["op"] == "read_state"
    assert body["changed"] == [a]
    assert body["unchanged"] == [b]
    assert body["missing"] == ["ghost"]


def test_unknown_op_is_a_typed_400_that_names_the_valid_ops(store):
    resp, body = _post(store, {"op": "obliterate", "item_ids": [_item(store, "A")]})

    assert resp.status == 400
    assert body["error"]["code"] == "unknown_op"
    assert body["error"]["received"] == "obliterate"
    assert "collect" in body["error"]["message"]


def test_missing_item_ids_is_a_typed_400(store):
    for payload in ({"op": "archive"}, {"op": "archive", "item_ids": []}):
        resp, body = _post(store, payload)
        assert resp.status == 400
        assert body["error"]["code"] == "item_ids_required"


def test_an_oversized_selection_is_refused_rather_than_served(store):
    """A runaway bulk write over an entire library is a client bug, not an intent."""
    resp, body = _post(store, {"op": "archive", "item_ids": [f"i{n}" for n in range(501)]})

    assert resp.status == 400
    assert body["error"]["code"] == "too_many_items"
    assert body["error"]["received"] == 501


def test_a_missing_op_argument_is_a_typed_400_not_a_silent_no_op(store):
    a = _item(store, "A")
    resp, body = _post(store, {"op": "collect", "item_ids": [a]})

    assert resp.status == 400
    assert body["error"]["code"] == "invalid_bulk_args"
    assert "collection_id" in body["error"]["message"]


def test_a_smart_shelf_refusal_keeps_its_own_error_code(store):
    """The frontend keys on this code to explain that a smart shelf fills itself, so it
    must not be flattened into the generic argument-error code."""
    a = _item(store, "A")
    smart = store.create_collection(name="All notes", kind="smart", query="note")

    resp, body = _post(store, {"op": "collect", "item_ids": [a], "collection_id": smart})

    assert resp.status == 400
    assert body["error"]["code"] == "smart_collection_immutable"


def test_invalid_json_is_rejected(store):
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    req = make_mocked_request("POST", "/api/knowledge/bulk", app=app)

    async def _boom():
        raise ValueError("not json")

    req.json = _boom
    from personalclaw.dashboard.handlers import knowledge as H

    resp = _run(H.bulk_items(req))
    assert resp.status == 400


def test_a_non_object_body_is_rejected(store):
    resp, _ = _post(store, ["not", "an", "object"])
    assert resp.status == 400


def test_collect_over_many_items_shelves_them_all(store):
    a, b = _item(store, "A"), _item(store, "B")
    shelf = store.create_collection(name="Reading")

    resp, body = _post(store, {"op": "collect", "item_ids": [a, b], "collection_id": shelf})

    assert resp.status == 200
    assert sorted(body["changed"]) == sorted([a, b])
    assert {i["id"] for i in store.resolve_collection(shelf)} == {a, b}


def test_the_route_is_registered(store):
    """A handler nobody can reach is not a feature."""
    from personalclaw.dashboard.handlers.knowledge import setup_knowledge_routes

    app = web.Application()
    setup_knowledge_routes(app)
    paths = {r.resource.canonical for r in app.router.routes() if r.resource is not None}
    assert "/api/knowledge/bulk" in paths
