"""The library home's read route (KNOWLEDGE-LIBRARY S3, T3.3 / `KL-8`).

`GET /api/knowledge/library-home` answers the four questions the library landing surface asks —
what did I just add, what am I part-way through, what did I keep, what is on my shelves — in ONE
read, so a failure is one failure the client can name instead of three populated shelves beside a
fourth that is blank for an invisible reason.

What these tests hold, and why each one is here rather than assumed:

* **The route is REGISTERED.** A handler with no `app.router.add_get` is the inert-control shape
  this repo keeps finding; the definition existing proves nothing. Asserted against the real
  route table, with an obviously-bogus sibling path asserted ABSENT so the check cannot pass
  vacuously.
* **Each shelf selects on its own column** — and, more to the point, each shelf EXCLUDES what the
  library list excludes. A home that shows archived rows, mirrored artifacts or
  `DEFAULT_LIST_EXCLUDED_KINDS` puts items on the landing surface that vanish the moment the user
  clicks through to the list.
* **A collection count cannot disagree with the shelf it labels.** `list_collections`'
  `item_count` counts every membership row including archived items, while `resolve_collection`
  hides them — so the existing rail number can EXCEED what opening the shelf shows. This route
  derives the count from the same predicate as the resolve, and the test proves the equality on
  the exact input that separates the two (a shelf holding one live and one archived item).
* **Empty is a real answer, not an error.** Four empty shelves on a fresh library must be a 200
  with empty lists, because the surface's empty states are what turn that into a sentence.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import knowledge as H
from personalclaw.knowledge.artifact_ingest import ARTIFACT_ITEM_TYPE
from personalclaw.knowledge.semantics import DEFAULT_LIST_EXCLUDED_KINDS
from personalclaw.knowledge.store import KnowledgeStore


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(tmp_path / "k.db")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _home(store, query: str = ""):
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    req = make_mocked_request("GET", f"/api/knowledge/library-home{query}", app=app)
    resp = _run(H.library_home(req))
    return resp, json.loads(resp.body)


def _item(store, title: str, **fields) -> str:
    iid = store.create_typed_item(item_type="note", title=title, content=f"body of {title}")
    if fields:
        store.update_item(iid, touch=False, **fields)
    return iid


def _titles(rows) -> list[str]:
    return [r["title"] for r in rows]


# ── The call site: is the route reachable at all? ─────────────────────────────


def test_the_library_home_route_is_registered_on_the_real_route_table():
    """A handler nothing routes to is a handler nobody can call. The bogus path is the
    vacuity guard: without it a broken `canonical` read would pass this test trivially."""
    app = web.Application()
    H.setup_knowledge_routes(app)
    routes = {(r.method, str(r.resource.canonical)) for r in app.router.routes()}
    assert ("GET", "/api/knowledge/library-home") in routes
    assert ("GET", "/api/knowledge/library-home-nope") not in routes
    # It is a READ. `add_get` also registers HEAD; any WRITE verb here would be a different
    # review, so the rail names the two allowed methods rather than counting them.
    assert {m for (m, p) in routes if p == "/api/knowledge/library-home"} <= {"GET", "HEAD"}


# ── The three item shelves ───────────────────────────────────────────────────


def test_each_shelf_selects_on_its_own_column(store):
    plain = _item(store, "Plain")
    reading = _item(store, "Half read", read_state="reading")
    starred = _item(store, "Kept", favorited=1)
    both = _item(store, "Kept and half read", read_state="reading", favorited=1)

    _, body = _home(store)
    assert set(_titles(body["recently_added"])) == {
        "Plain",
        "Half read",
        "Kept",
        "Kept and half read",
    }
    assert set(_titles(body["continue_reading"])) == {"Half read", "Kept and half read"}
    assert set(_titles(body["favorites"])) == {"Kept", "Kept and half read"}
    assert {plain, reading, starred, both}  # ids used; keeps the fixture honest


def test_recently_added_is_ordered_by_created_at_newest_first(store):
    for title, created in (
        ("Oldest", "2026-01-01"),
        ("Newest", "2026-08-01"),
        ("Middle", "2026-04-01"),
    ):
        iid = _item(store, title)
        store.db.execute("UPDATE items SET created_at = ? WHERE id = ?", (created, iid))
    store.db.commit()

    _, body = _home(store)
    assert _titles(body["recently_added"]) == ["Newest", "Middle", "Oldest"]


def test_a_shelf_hides_exactly_what_the_library_list_hides(store):
    """Archived rows, mirrored artifacts and the indexed-not-listed kinds. A home shelf that
    disagreed with the list it links into would show rows that disappear on click-through."""
    _item(store, "Live")
    _item(store, "Archived", is_archived=1, favorited=1, read_state="reading")
    artifact = store.create_typed_item(
        item_type=ARTIFACT_ITEM_TYPE, title="Mirrored artifact", content="x"
    )
    store.update_item(artifact, touch=False, favorited=1, read_state="reading")
    excluded_kind = sorted(DEFAULT_LIST_EXCLUDED_KINDS)[0]
    hidden = _item(store, "Scheduled report", favorited=1, read_state="reading")
    # `kind` is written directly: it is a taxonomy axis, not one of `update_item`'s fields.
    store.db.execute("UPDATE items SET kind = ? WHERE id = ?", (excluded_kind, hidden))
    store.db.commit()
    assert store.get_item(hidden)["kind"] == excluded_kind  # the row IS the excluded kind

    _, body = _home(store)
    for shelf in ("recently_added", "continue_reading", "favorites"):
        assert _titles(body[shelf]) == (["Live"] if shelf == "recently_added" else []), shelf


def test_the_shelf_limit_is_honoured_and_clamped(store):
    for i in range(30):
        _item(store, f"Note {i:02d}")

    _, default = _home(store)
    assert len(default["recently_added"]) == H._HOME_SHELF_LIMIT

    _, asked = _home(store, "?limit=3")
    assert len(asked["recently_added"]) == 3

    # A caller asking for the whole library gets the cap, not the library.
    _, capped = _home(store, "?limit=9999")
    assert len(capped["recently_added"]) == H._HOME_SHELF_MAX

    resp, _ = _home(store, "?limit=abc")
    assert resp.status == 400


# ── Per-collection counts ────────────────────────────────────────────────────


def test_a_manual_shelfs_count_equals_what_opening_the_shelf_shows(store):
    """🔑 The count and the items come from the same predicate.

    This is the input that separates the two implementations: `list_collections`' `item_count`
    counts BOTH members, `resolve_collection` returns only the live one. A rail number that says
    2 over a shelf that opens onto 1 row is the "count maintained beside a table" defect.
    """
    cid = store.create_collection(name="Recipes")
    live = _item(store, "Live member")
    archived = _item(store, "Archived member", is_archived=1)
    assert store.add_to_collection(cid, live)
    assert store.add_to_collection(cid, archived)

    _, body = _home(store)
    shelf = next(c for c in body["collections"] if c["id"] == cid)
    assert shelf["count"] == len(store.resolve_collection(cid, limit=500)) == 1
    assert shelf["count_capped"] is False
    # And the divergence this route deliberately does not repeat:
    rail = next(c for c in store.list_collections() if c["id"] == cid)
    assert rail["item_count"] == 2


def test_a_smart_shelfs_count_is_the_resolve_itself(store):
    _item(store, "Sourdough starter", content="sourdough")
    cid = store.create_collection(name="Bread", kind="smart", query="sourdough")

    _, body = _home(store)
    shelf = next(c for c in body["collections"] if c["id"] == cid)
    assert shelf["kind"] == "smart"
    assert shelf["count"] == len(store.resolve_collection(cid, limit=H._HOME_SMART_COUNT_CAP))
    assert shelf["count_capped"] is False


def test_a_shelf_with_no_members_reports_zero_rather_than_being_dropped(store):
    cid = store.create_collection(name="Someday")
    _, body = _home(store)
    assert [(c["name"], c["count"]) for c in body["collections"]] == [("Someday", 0)]
    assert cid


# ── Empty is an answer ───────────────────────────────────────────────────────


def test_a_fresh_library_returns_four_empty_shelves_and_a_200(store):
    """Empty is not an error. The surface's own empty states are what make this a sentence
    rather than a blank region — and they only get the chance if this is a 200."""
    resp, body = _home(store)
    assert resp.status == 200
    assert body == {
        "recently_added": [],
        "continue_reading": [],
        "favorites": [],
        "collections": [],
    }
