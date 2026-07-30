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


# ── tag taxonomy routes (S2, T2.2) ────────────────────────────────────────────


def _tag_req(store, method, path_id=None, body=None):
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    req = make_mocked_request(
        method, "/api/knowledge/tags", app=app, match_info={"id": str(path_id)} if path_id else {}
    )
    if body is not None:

        async def _json():
            return body

        req.json = _json
    return req


def _tag_ids(store) -> dict:
    return {t["name"]: t["id"] for t in store.list_tags()}


def test_tag_tree_carries_ids_parents_and_counts(store):
    """Distinct from GET /tags, which stays a flat list[str] for autocomplete."""
    store.create_typed_item(item_type="note", title="N", content="c", tags=["a", "b"])
    from personalclaw.dashboard.handlers import knowledge as H

    resp = _run(H.list_tag_taxonomy(_tag_req(store, "GET")))
    body = json.loads(resp.body)

    assert resp.status == 200
    assert {t["name"] for t in body["tags"]} == {"a", "b"}
    assert all({"id", "parent_id", "parent_name", "usage_count"} <= set(t) for t in body["tags"])


def test_rename_route_round_trips_and_returns_the_whole_tree(store):
    iid = store.create_typed_item(item_type="note", title="N", content="c", tags=["old"])
    from personalclaw.dashboard.handlers import knowledge as H

    tid = _tag_ids(store)["old"]
    resp = _run(H.rename_tag(_tag_req(store, "PATCH", tid, {"name": "new"})))
    body = json.loads(resp.body)

    assert resp.status == 200 and body["ok"] is True
    assert {t["name"] for t in body["tags"]} == {"new"}
    assert store.get_item(iid)["tags"] == ["new"]


def test_reparent_via_the_rename_route(store):
    store.create_typed_item(item_type="note", title="N", content="c", tags=["parent", "child"])
    from personalclaw.dashboard.handlers import knowledge as H

    ids = _tag_ids(store)
    resp = _run(H.rename_tag(_tag_req(store, "PATCH", ids["child"], {"parent_id": ids["parent"]})))
    body = json.loads(resp.body)

    assert resp.status == 200
    by_name = {t["name"]: t for t in body["tags"]}
    assert by_name["child"]["parent_name"] == "parent"

    # null means "make it a root again".
    resp2 = _run(H.rename_tag(_tag_req(store, "PATCH", ids["child"], {"parent_id": None})))
    by_name2 = {t["name"]: t for t in json.loads(resp2.body)["tags"]}
    assert by_name2["child"]["parent_id"] is None


def test_a_cycle_is_a_typed_400(store):
    store.create_typed_item(item_type="note", title="N", content="c", tags=["a", "b"])
    from personalclaw.dashboard.handlers import knowledge as H

    ids = _tag_ids(store)
    _run(H.rename_tag(_tag_req(store, "PATCH", ids["b"], {"parent_id": ids["a"]})))

    resp = _run(H.rename_tag(_tag_req(store, "PATCH", ids["a"], {"parent_id": ids["b"]})))
    body = json.loads(resp.body)

    assert resp.status == 400
    assert body["error"]["code"] == "tag_cycle"


def test_a_name_collision_is_a_typed_400(store):
    store.create_typed_item(item_type="note", title="N", content="c", tags=["a", "b"])
    from personalclaw.dashboard.handlers import knowledge as H

    resp = _run(H.rename_tag(_tag_req(store, "PATCH", _tag_ids(store)["a"], {"name": "b"})))
    body = json.loads(resp.body)

    assert resp.status == 400
    assert body["error"]["code"] == "tag_name_taken"


def test_an_empty_rename_body_is_refused(store):
    store.create_typed_item(item_type="note", title="N", content="c", tags=["a"])
    from personalclaw.dashboard.handlers import knowledge as H

    resp = _run(H.rename_tag(_tag_req(store, "PATCH", _tag_ids(store)["a"], {})))
    assert resp.status == 400
    assert json.loads(resp.body)["error"]["code"] == "nothing_to_update"


def test_a_non_numeric_tag_id_is_a_404_not_a_500(store):
    """Tag ids are integers (a surrogate key). A junk path segment must not raise."""
    from personalclaw.dashboard.handlers import knowledge as H

    resp = _run(H.rename_tag(_tag_req(store, "PATCH", "not-a-number", {"name": "x"})))
    assert resp.status == 404


def test_merge_route_reports_moved_and_already(store):
    a = store.create_typed_item(item_type="note", title="A", content="c", tags=["src"])
    store.create_typed_item(item_type="note", title="B", content="c", tags=["src", "dst"])
    from personalclaw.dashboard.handlers import knowledge as H

    ids = _tag_ids(store)
    resp = _run(H.merge_tag(_tag_req(store, "POST", ids["src"], {"into": ids["dst"]})))
    body = json.loads(resp.body)

    assert resp.status == 200
    assert body["moved"] == 1 and body["already"] == 1
    assert {t["name"] for t in body["tags"]} == {"dst"}
    assert store.get_item(a)["tags"] == ["dst"]


def test_merge_without_a_target_is_refused(store):
    store.create_typed_item(item_type="note", title="N", content="c", tags=["a"])
    from personalclaw.dashboard.handlers import knowledge as H

    resp = _run(H.merge_tag(_tag_req(store, "POST", _tag_ids(store)["a"], {})))
    assert resp.status == 400
    assert json.loads(resp.body)["error"]["code"] == "into_required"


def test_delete_route_removes_the_tag_and_returns_the_tree(store):
    iid = store.create_typed_item(item_type="note", title="N", content="c", tags=["doomed", "kept"])
    from personalclaw.dashboard.handlers import knowledge as H

    resp = _run(H.delete_tag(_tag_req(store, "DELETE", _tag_ids(store)["doomed"])))
    body = json.loads(resp.body)

    assert resp.status == 200
    assert {t["name"] for t in body["tags"]} == {"kept"}
    assert store.get_item(iid)["tags"] == ["kept"]


def test_the_tag_routes_are_registered(store):
    from personalclaw.dashboard.handlers.knowledge import setup_knowledge_routes

    app = web.Application()
    setup_knowledge_routes(app)
    paths = {r.resource.canonical for r in app.router.routes() if r.resource is not None}
    assert "/api/knowledge/tag-tree" in paths
    assert "/api/knowledge/tags/{id}" in paths
    assert "/api/knowledge/tags/{id}/merge" in paths
    # The flat autocomplete contract must survive untouched.
    assert "/api/knowledge/tags" in paths
