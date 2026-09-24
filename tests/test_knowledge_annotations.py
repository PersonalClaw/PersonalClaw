"""Reading annotations — the persistence half of the reading view (KNOWLEDGE-LIBRARY T3.1).

The atom's load-bearing clause is that an in-reader highlight *persists* and *reappears on
the item*, so these tests are about the store round-trip and what must not be lost:

* **Survival.** A highlight written through the API is readable back from a FRESH store
  handle over the same file — component state would pass a same-process assertion and fail
  this one, which is the whole distinction the atom draws.
* **Non-touching.** Highlighting is reading, not editing. It must not move
  `items.updated_at`, or marking a passage would reorder a recency-sorted library out from
  under the reader — the same contract read-state and favorites already hold to.
* **The merge.** T3.2's merge moves collections, tags and mentions to the survivor;
  highlights are curation too, and a merge that dropped them would quietly delete the
  reader's own work.
* **The cascade.** Deleting the item takes its highlights with it rather than leaving rows
  pointing at nothing.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.knowledge.store import KnowledgeStore


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "k.db"


@pytest.fixture()
def store(db_path):
    return KnowledgeStore(db_path)


def _item(store, title="Long article", content="A paragraph worth marking up.") -> str:
    return store.create_typed_item(item_type="note", title=title, content=content)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _call(store, handler_name, method, path, *, match_info=None, body=None):
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    req = make_mocked_request(method, path, app=app, match_info=match_info or {})
    if body is not None:

        async def _json():
            return body

        req.json = _json
    from personalclaw.dashboard.handlers import knowledge as H

    resp = _run(getattr(H, handler_name)(req))
    return resp, json.loads(resp.body)


# ── Store round-trip ────────────────────────────────────────────────────


def test_a_highlight_round_trips(store):
    item = _item(store)
    row = store.add_annotation(item, "worth marking", occurrence=0, note="why it matters")

    assert row is not None and row["id"]
    listed = store.list_annotations(item)
    assert [(a["quote"], a["note"], a["occurrence"]) for a in listed] == [
        ("worth marking", "why it matters", 0)
    ]


def test_a_highlight_survives_a_fresh_store_handle(db_path):
    """The atom's real clause: a reload must find it. Component state passes every
    same-process assertion and fails exactly this one."""
    first = KnowledgeStore(db_path)
    item = _item(first)
    first.add_annotation(item, "persisted passage")

    reopened = KnowledgeStore(db_path)

    assert [a["quote"] for a in reopened.list_annotations(item)] == ["persisted passage"]


def test_the_note_is_optional(store):
    item = _item(store)
    row = store.add_annotation(item, "just the passage")
    assert row["note"] == ""


def test_occurrence_distinguishes_two_highlights_of_the_same_sentence(store):
    """A repeated sentence is one string but two passages; without `occurrence` the second
    highlight would be indistinguishable from the first and re-mark the wrong one."""
    item = _item(store)
    store.add_annotation(item, "same words", occurrence=0)
    store.add_annotation(item, "same words", occurrence=1)

    assert sorted(a["occurrence"] for a in store.list_annotations(item)) == [0, 1]


def test_highlights_list_in_reading_order(store):
    item = _item(store)
    store.add_annotation(item, "first")
    store.add_annotation(item, "second")
    store.add_annotation(item, "third")

    assert [a["quote"] for a in store.list_annotations(item)] == ["first", "second", "third"]


def test_only_this_items_highlights_come_back(store):
    a, b = _item(store, "A"), _item(store, "B")
    store.add_annotation(a, "mine")
    store.add_annotation(b, "theirs")

    assert [x["quote"] for x in store.list_annotations(a)] == ["mine"]


def test_deleting_a_highlight(store):
    item = _item(store)
    row = store.add_annotation(item, "temporary")

    assert store.delete_annotation(row["id"]) is True
    assert store.list_annotations(item) == []
    assert store.delete_annotation(row["id"]) is False


# ── Guard rails ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("quote", ["", "   ", "\n"])
def test_an_empty_quote_is_refused(store, quote):
    with pytest.raises(ValueError):
        store.add_annotation(_item(store), quote)


def test_a_runaway_quote_is_refused(store):
    """A select-all drag would otherwise store the whole article as its own annotation."""
    item = _item(store)
    with pytest.raises(ValueError):
        store.add_annotation(item, "x" * (KnowledgeStore.MAX_ANNOTATION_QUOTE + 1))


def test_a_negative_occurrence_is_refused(store):
    with pytest.raises(ValueError):
        store.add_annotation(_item(store), "passage", occurrence=-1)


def test_highlighting_an_unknown_item_returns_none(store):
    assert store.add_annotation("ghost", "passage") is None


# ── What must not be lost ───────────────────────────────────────────────


def test_highlighting_does_not_touch_updated_at(store):
    """Reading is not editing. Same contract as read-state and favorites."""
    item = _item(store)
    before = store.get_item(item)["updated_at"]

    store.add_annotation(item, "a passage")

    assert store.get_item(item)["updated_at"] == before


def test_a_merge_moves_highlights_to_the_survivor(store):
    keep, loser = _item(store, "Keep"), _item(store, "Loser")
    store.add_annotation(keep, "kept passage")
    store.add_annotation(loser, "passage from the copy that loses")

    moved = store.merge_items(keep, loser)

    assert moved["annotations"] == 1
    assert sorted(a["quote"] for a in store.list_annotations(keep)) == [
        "kept passage",
        "passage from the copy that loses",
    ]


def test_deleting_the_item_takes_its_highlights(store):
    item = _item(store)
    row = store.add_annotation(item, "doomed")

    store.delete_item(item)

    assert (
        store.db.execute("SELECT 1 FROM annotations WHERE id = ?", (row["id"],)).fetchone() is None
    )


# ── HTTP surface ────────────────────────────────────────────────────────


def test_post_then_get_over_the_endpoints(store):
    item = _item(store)
    resp, body = _call(
        store,
        "add_item_annotation",
        "POST",
        f"/api/knowledge/items/{item}/annotations",
        match_info={"id": item},
        body={"quote": "an endpoint passage", "occurrence": 2, "note": "n"},
    )
    assert resp.status == 200 and body["ok"] is True
    assert body["annotation"]["occurrence"] == 2

    resp, body = _call(
        store,
        "list_item_annotations",
        "GET",
        f"/api/knowledge/items/{item}/annotations",
        match_info={"id": item},
    )
    assert resp.status == 200
    assert [a["quote"] for a in body["annotations"]] == ["an endpoint passage"]


def test_listing_an_unknown_item_is_404(store):
    resp, _ = _call(
        store,
        "list_item_annotations",
        "GET",
        "/api/knowledge/items/ghost/annotations",
        match_info={"id": "ghost"},
    )
    assert resp.status == 404


def test_an_empty_quote_is_a_400(store):
    item = _item(store)
    resp, body = _call(
        store,
        "add_item_annotation",
        "POST",
        f"/api/knowledge/items/{item}/annotations",
        match_info={"id": item},
        body={"quote": "  "},
    )
    assert resp.status == 400 and "quote" in body["error"]


def test_a_non_integer_occurrence_is_a_400(store):
    item = _item(store)
    resp, body = _call(
        store,
        "add_item_annotation",
        "POST",
        f"/api/knowledge/items/{item}/annotations",
        match_info={"id": item},
        body={"quote": "passage", "occurrence": "third"},
    )
    assert resp.status == 400 and "occurrence" in body["error"]


def test_posting_to_an_unknown_item_is_404(store):
    resp, _ = _call(
        store,
        "add_item_annotation",
        "POST",
        "/api/knowledge/items/ghost/annotations",
        match_info={"id": "ghost"},
        body={"quote": "passage"},
    )
    assert resp.status == 404


def test_delete_endpoint_removes_then_404s(store):
    item = _item(store)
    row = store.add_annotation(item, "gone soon")

    resp, _ = _call(
        store,
        "delete_item_annotation",
        "DELETE",
        f"/api/knowledge/annotations/{row['id']}",
        match_info={"id": row["id"]},
    )
    assert resp.status == 200
    assert store.list_annotations(item) == []

    resp, _ = _call(
        store,
        "delete_item_annotation",
        "DELETE",
        f"/api/knowledge/annotations/{row['id']}",
        match_info={"id": row["id"]},
    )
    assert resp.status == 404


def test_the_annotation_routes_are_registered():
    """A handler nobody routed to is unreachable — assert the wiring, not just the function."""
    from personalclaw.dashboard.handlers import knowledge as H

    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=None)
    app["knowledge_llm_pool"] = object()
    H.setup_knowledge_routes(app)

    registered = {
        (r.method, str(getattr(r.resource, "canonical", ""))) for r in app.router.routes()
    }
    assert ("GET", "/api/knowledge/items/{id}/annotations") in registered
    assert ("POST", "/api/knowledge/items/{id}/annotations") in registered
    assert ("DELETE", "/api/knowledge/annotations/{id}") in registered
