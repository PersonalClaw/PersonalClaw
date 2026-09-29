"""A title a person gave an item survives enrichment, even when it is the file's own name.

Measured: an audio memo uploaded through Add knowledge, its title field filled with the file's
name and kept — ``2026-09-29-dog-walk-talk.m4a`` — was renamed by enrichment to "Section 6
Crash Demo Requirements", and "@dog" no longer found it. Enrichment replaced a file's title
while it still equalled the upload's file name, and a kept name is equal to it by definition:
provenance was inferred from the value. It is now recorded (``items.title_source``) wherever a
person sets a title, and enrichment never replaces one; its suggestion stays as ``ai_title``,
which the item page offers.

Driven through the upload's own item writer, the PATCH the create form sends, and the real
ingest runner.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import knowledge as H
from personalclaw.knowledge.pipeline import ensure_nodes_registered
from personalclaw.knowledge.pipeline.runner import _run_insights
from personalclaw.knowledge.store import KnowledgeStore

MEMO = "2026-09-29-dog-walk-talk.m4a"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


@pytest.fixture()
def store(tmp_path):
    ensure_nodes_registered()
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


class _TitlingPool:
    async def send(self, prompt, timeout=None):
        return json.dumps({"title": "Section 6 Crash Demo Requirements", "summary": "s"})


def _request(store, method, *, body=None, item_id=None):
    app = web.Application()
    app["state"] = SimpleNamespace(
        knowledge_store=store,
        knowledge_provider=lambda: _provider(store),
        knowledge_ingest_queue=lambda: SimpleNamespace(
            enqueue=lambda _i: None, enqueue_background=lambda _i: None
        ),
    )
    req = make_mocked_request(method, "/", app=app, match_info={"id": item_id} if item_id else {})
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[method-assign]
    return req


def _provider(store):
    from personalclaw.knowledge_providers.native import create_native_provider

    return create_native_provider(store, enqueue=lambda _i: None)


def _upload(store, tmp_path, name=MEMO) -> str:
    """The item an upload writes (`_store_file_item`): titled with the file's name."""
    src = tmp_path / name
    src.write_bytes(b"not really audio, and the pipeline never reads it here")
    item, is_new = H._store_file_item(store, str(src), name, mime="audio/mp4")
    assert is_new and item["title"] == name
    return item["id"]


def _patch_title(store, item_id, title):
    resp = asyncio.run(
        H.update_item(_request(store, "PATCH", body={"title": title}, item_id=item_id))
    )
    assert resp.status == 200, resp.body


def _enrich(store, item_id):
    item = store.get_item(item_id)
    assert asyncio.run(_run_insights(store, item_id, "transcript text", _TitlingPool())) is None
    return store.get_item(item_id), item


def test_a_title_she_kept_as_the_file_name_survives_enrichment(store, tmp_path):
    iid = _upload(store, tmp_path)
    _patch_title(store, iid, MEMO)  # the create form sends the title she kept

    after, _ = _enrich(store, iid)

    assert after["title"] == MEMO
    assert after["ai_title"] == "Section 6 Crash Demo Requirements", "kept as the suggestion"
    assert after["title_source"] == "user"


def test_an_upload_nobody_titled_still_takes_the_suggested_title(store, tmp_path):
    """The vacuity arm: with no title given (a cleared field, a drop with no form), the file
    name is only a placeholder and enrichment replaces it, as it always did."""
    iid = _upload(store, tmp_path)

    after, _ = _enrich(store, iid)

    assert after["title"] == "Section 6 Crash Demo Requirements"
    assert not after.get("title_source")


def test_a_title_typed_when_writing_a_note_is_recorded_as_hers(store):
    resp = asyncio.run(
        H.create_item(
            _request(store, "POST", body={"type": "note", "title": "Kitchen", "content": "x"})
        )
    )
    assert resp.status == 201
    assert json.loads(resp.body)["title_source"] == "user"

    blank = asyncio.run(
        H.create_item(_request(store, "POST", body={"type": "note", "content": "only a body"}))
    )
    assert not json.loads(blank.body).get("title_source"), "a seeded title is not hers"


def test_a_title_she_typed_that_equals_the_content_prefix_is_still_hers(store):
    """Enrichment replaced a note's title when it equalled the first 60 characters of its
    body — the placeholder a blank title gets. One she typed is kept whatever it says."""
    body = "Groceries for the week: oats, lentils, rice"
    resp = asyncio.run(
        H.create_item(
            _request(store, "POST", body={"type": "note", "title": body, "content": body})
        )
    )
    iid = json.loads(resp.body)["id"]

    after, _ = _enrich(store, iid)

    assert after["title"] == body
