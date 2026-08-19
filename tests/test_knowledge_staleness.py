"""Staleness of a synthesized item, and the two routes a "sources changed" banner needs.

WF2KNO-11 clause A. What is worth asserting here is not that the flag flips — it is that the
flag flips for a *defensible* reason and stays down for everything else:

1. **A fresh synthesis is not stale.** The material that existed when it was written is not
   new material. Getting this wrong lights the banner on every document permanently, which
   is the failure mode that makes readers ignore banners.
2. **The COUNT, not just the boolean.** The banner names a number, so the number is what the
   test pins. A rule that answered "stale: true" with the wrong count would still pass a
   boolean assertion.
3. **The two rules are separable.** A tagged newcomer moves ``new_source_items``; touching a
   CITED source moves ``changed_sources``. Asserting one total would let either rule rot
   silently behind the other.
4. **An observed item is never stale.** "Stale" on a note would mean a different thing
   (the world moved on from a fact) with a different remedy, so the rule is asserted
   directly rather than left to be inferred from the synthesized cases.
"""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.knowledge.staleness import staleness_for
from personalclaw.knowledge.store import KnowledgeStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An isolated store under tmp_path. Nothing here may reach the developer's own home."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _item(store, item_type: str, title: str, tags=None) -> str:
    return store.create_typed_item(
        item_type=item_type, title=title, content=f"body of {title}", tags=tags or []
    )


def _cite(store, item_id: str, source_item_id: str) -> None:
    """Record one per-marker citation.

    ``item_citations`` and ``set_item_citations`` arrive with the citation half of this same
    change; until then the table is created here so the cited-source rule is exercised rather
    than skipped. Once the store owns the table, ``CREATE TABLE IF NOT EXISTS`` is a no-op and
    the store method is preferred.
    """
    setter = getattr(store, "set_item_citations", None)
    if setter is not None:
        setter(item_id, [{"marker": 1, "source_item_id": source_item_id, "chunk_index": 0}])
        return
    store.db.execute(
        "CREATE TABLE IF NOT EXISTS item_citations ("
        "item_id TEXT NOT NULL, marker INTEGER NOT NULL, source_item_id TEXT NOT NULL, "
        "chunk_index INTEGER, excerpt TEXT)"
    )
    store.db.execute(
        "INSERT INTO item_citations (item_id, marker, source_item_id, chunk_index, excerpt) "
        "VALUES (?, ?, ?, ?, ?)",
        (item_id, 1, source_item_id, 0, ""),
    )
    store.db.commit()


def _get_staleness(store, item_id: str):
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    req = make_mocked_request(
        "GET", f"/api/knowledge/items/{item_id}/staleness", app=app, match_info={"id": item_id}
    )
    from personalclaw.dashboard.handlers import knowledge as H

    resp = _run(H.get_item_staleness(req))
    return resp, json.loads(resp.body)


def _post_regenerate(store, item_id: str):
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    req = make_mocked_request(
        "POST", f"/api/knowledge/items/{item_id}/regenerate", app=app, match_info={"id": item_id}
    )
    from personalclaw.dashboard.handlers import knowledge as H

    resp = _run(H.regenerate_item(req))
    return resp, json.loads(resp.body)


def test_a_fresh_synthesis_is_not_stale(store):
    """Material that predates the synthesis is not new material."""
    _item(store, "note", "Existing source", tags=["alpha"])
    insight = _item(store, "insight", "Overview of alpha", tags=["alpha"])

    report = staleness_for(store, insight)

    assert report.stale is False
    assert report.new_source_items == 0
    assert report.changed_sources == 0
    assert "alpha" in report.scope


def test_a_new_tagged_item_makes_the_synthesis_stale_and_is_counted(store):
    insight = _item(store, "insight", "Overview of alpha", tags=["alpha"])
    _item(store, "note", "Arrived afterwards", tags=["alpha"])

    report = staleness_for(store, insight)

    assert report.stale is True
    assert report.new_source_items == 1
    assert report.changed_sources == 0


def test_only_tag_sharing_non_synthesized_items_count(store):
    """Three exclusions, each of which would inflate the banner's number if dropped."""
    insight = _item(store, "insight", "Overview of alpha", tags=["alpha"])
    _item(store, "note", "Unrelated", tags=["beta"])
    _item(store, "report", "A sibling synthesis", tags=["alpha"])
    archived = _item(store, "note", "Archived newcomer", tags=["alpha"])
    store.update_item(archived, status="archived", touch=False)
    _item(store, "note", "The only real newcomer", tags=["alpha"])

    assert staleness_for(store, insight).new_source_items == 1


def test_touching_a_cited_source_bumps_changed_sources(store):
    """The cited source is untagged, so only rule (a) can move — the rules stay separable."""
    source = _item(store, "note", "Cited source")
    insight = _item(store, "insight", "Overview of alpha", tags=["alpha"])
    _cite(store, insight, source)
    assert staleness_for(store, insight).changed_sources == 0

    store.update_item(source, content="rewritten underneath the synthesis")
    report = staleness_for(store, insight)

    assert report.changed_sources == 1
    assert report.new_source_items == 0
    assert report.stale is True


def test_a_non_synthesized_item_is_never_stale(store):
    note = _item(store, "note", "An observed note", tags=["alpha"])
    _item(store, "note", "Arrived afterwards", tags=["alpha"])

    report = staleness_for(store, note)

    assert report.stale is False
    assert report.new_source_items == 0
    assert report.changed_sources == 0
    assert "not a synthesized item" in report.scope


def test_an_unknown_item_raises_rather_than_reporting_fresh(store):
    with pytest.raises(KeyError):
        staleness_for(store, "ghost")


def test_the_staleness_route_returns_the_count(store):
    insight = _item(store, "insight", "Overview of alpha", tags=["alpha"])
    _item(store, "note", "Arrived afterwards", tags=["alpha"])

    resp, body = _get_staleness(store, insight)

    assert resp.status == 200
    assert body["item_id"] == insight
    assert body["stale"] is True
    assert body["new_source_items"] == 1
    assert body["changed_sources"] == 0
    assert body["checked_at"] and "alpha" in body["scope"]


def test_the_staleness_route_404s_on_an_unknown_id(store):
    resp, body = _get_staleness(store, "ghost")

    assert resp.status == 404
    assert body == {"error": "not found"}


def test_regenerate_404s_on_an_unknown_id(store):
    resp, body = _post_regenerate(store, "ghost")

    assert resp.status == 404
    assert body == {"error": "not found"}


def test_regenerate_refuses_an_observed_item(store):
    """The banner never offers this, but the route is reachable by hand."""
    note = _item(store, "note", "An observed note")

    resp, body = _post_regenerate(store, note)

    assert resp.status == 400
    assert "synthesized" in body["error"]


def test_regenerate_without_the_update_pipeline_is_an_explicit_503(store, monkeypatch):
    """A missing update module is a stated unavailability, not a traceback."""
    insight = _item(store, "insight", "Overview of alpha", tags=["alpha"])
    monkeypatch.setitem(sys.modules, "personalclaw.knowledge.updates", None)

    resp, body = _post_regenerate(store, insight)

    assert resp.status == 503
    assert body["reason"] == "updates_unavailable"
    assert "unavailable" in body["error"]
