"""P6b — typed knowledge items: first-class fields, source-level typed create,
tags-as-array serialization, the /items + /providers endpoints, and the native
knowledge_* agent tools."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.knowledge as K
from personalclaw.knowledge.store import KnowledgeStore


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(os.path.join(tmp_path, "k.db"))


# ── store: typed items + first-class fields ──


class TestTypedStore:
    def test_create_typed_note(self, store):
        nid = store.create_typed_item(item_type="note", title="Idea", content="a b c", tags=["x"])
        item = store.get_item(nid)
        assert item["type"] == "note" and item["item_type"] == "note"
        assert item["tags"] == ["x"] and item["word_count"] == 3
        assert item["provider"] == "native" and item["is_pinned"] is False

    def test_create_bookmark_records_url(self, store):
        bid = store.create_typed_item(item_type="bookmark", title="X", url="https://e.com")
        item = store.get_item(bid)
        assert item["type"] == "bookmark" and item["url"] == "https://e.com"

    def test_tags_serialize_as_array(self, store):
        nid = store.create_typed_item(item_type="note", title="T", content="", tags=["a", "b"])
        assert store.get_item(nid)["tags"] == ["a", "b"]

    def test_update_typed_fields(self, store):
        nid = store.create_typed_item(item_type="note", title="T", content="")
        store.update_item(nid, is_pinned=1, tags=["z"], url="https://u")
        item = store.get_item(nid)
        assert item["is_pinned"] is True and item["tags"] == ["z"] and item["url"] == "https://u"

    def test_word_count_recomputed_on_content_change(self, store):
        """word_count tracks content edits + file-upload backfill (create-fast leaves
        a file item at content='', word_count=0 until the graph backfills the text)."""
        # Simulate a file item: created empty, content backfilled by the runner later.
        fid = store.create_typed_item(item_type="document", title="doc", content="")
        assert store.get_item(fid)["word_count"] == 0
        store.update_item(fid, content="one two three four five")
        assert store.get_item(fid)["word_count"] == 5
        # An edit shrinking the content updates it down too.
        store.update_item(fid, content="just two")
        assert store.get_item(fid)["word_count"] == 2

    def test_reopen_is_non_destructive(self, tmp_path):
        # Re-opening a store preserves items and keeps the first-class columns.
        path = os.path.join(tmp_path, "k.db")
        s1 = KnowledgeStore(path)
        iid = s1.create_typed_item(item_type="note", title="keep", content="x")
        s1.db.close()
        s2 = KnowledgeStore(path)  # re-open → _migrate runs idempotently
        cols = {r[1] for r in s2.db.execute("PRAGMA table_info(items)").fetchall()}
        assert {"url", "is_pinned", "provider", "insights"} <= cols
        # One item = one logical doc; legacy source/chunk columns are gone.
        assert "source_id" not in cols and "chunk_index" not in cols
        assert s2.get_item(iid)["title"] == "keep"


# ── handlers: POST /items + /providers ──


def _app(store, enqueued=None):
    app = web.Application()
    from personalclaw.knowledge_providers.native import create_native_provider

    # create_item now routes through the native provider (registers + enqueues for
    # node-graph ingestion, #30). Provide one over the test store; ``enqueued`` (if
    # given) collects re-enrich enqueues from the update handler.
    sink = enqueued if enqueued is not None else []
    provider = create_native_provider(store, enqueue=sink.append)
    app["state"] = SimpleNamespace(
        knowledge_store=store,
        knowledge_provider=lambda: provider,
        knowledge_ingest_queue=lambda: SimpleNamespace(enqueue=sink.append),
    )
    return app


def _req(store, method, body=None, match_info=None, enqueued=None):
    app = _app(store, enqueued=enqueued)
    req = make_mocked_request(method, "/", app=app, match_info=match_info or {})
    if body is not None:

        async def _json():
            return body

        req.json = _json
    return req


class TestHandlers:
    def test_create_item_endpoint(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        resp = _run(
            H.create_item(
                _req(store, "POST", {"type": "note", "title": "N", "content": "hi", "tags": ["t"]})
            )
        )
        assert resp.status == 201
        body = json.loads(resp.body)
        assert body["type"] == "note" and body["tags"] == ["t"]

    def test_create_bookmark_requires_url(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        resp = _run(H.create_item(_req(store, "POST", {"type": "bookmark", "title": "X"})))
        assert resp.status == 400

    def test_create_bookmark_rejects_non_http_scheme(self, store):
        """A bookmark must be an http(s) web page — javascript:/data:/file: URLs are
        rejected (unscrapeable, and a stored XSS vector if rendered as a link)."""
        from personalclaw.dashboard.handlers import knowledge as H

        for bad in (
            "javascript:alert(1)",
            "data:text/html,<script>x</script>",
            "file:///etc/passwd",
            "not a url",
        ):
            resp = _run(H.create_item(_req(store, "POST", {"type": "bookmark", "url": bad})))
            assert resp.status == 400, f"{bad!r} should be rejected"
        # A real web URL is accepted.
        ok = _run(
            H.create_item(_req(store, "POST", {"type": "bookmark", "url": "https://example.com"}))
        )
        assert ok.status == 201

    def test_create_rejects_whitespace_only_content(self, store):
        """A note with only whitespace content (and no title/url) has nothing to store."""
        from personalclaw.dashboard.handlers import knowledge as H

        resp = _run(H.create_item(_req(store, "POST", {"type": "note", "content": "   \n  "})))
        assert resp.status == 400

    def test_update_rejects_non_http_url(self, store):
        """Editing a url can't smuggle in a javascript:/data: scheme that create blocks."""
        from personalclaw.dashboard.handlers import knowledge as H

        iid = store.create_typed_item(item_type="bookmark", title="Ex", url="https://example.com")
        store.db.commit()
        resp = _run(
            H.update_item(
                _req(store, "PATCH", {"url": "javascript:alert(1)"}, match_info={"id": iid})
            )
        )
        assert resp.status == 400
        # The stored url is unchanged (the bad edit didn't persist).
        assert store.get_item(iid)["url"] == "https://example.com"
        # A real url edit still works.
        ok = _run(
            H.update_item(
                _req(store, "PATCH", {"url": "https://example.org"}, match_info={"id": iid})
            )
        )
        assert ok.status == 200

    def test_bookmark_dedup_returns_existing(self, store):
        """Re-saving a bookmark URL already in the space returns the existing item
        (200), not a duplicate (201)."""
        from personalclaw.dashboard.handlers import knowledge as H

        r1 = _run(
            H.create_item(_req(store, "POST", {"type": "bookmark", "url": "https://example.com/x"}))
        )
        assert r1.status == 201
        first_id = json.loads(r1.body)["id"]
        r2 = _run(
            H.create_item(_req(store, "POST", {"type": "bookmark", "url": "https://example.com/x"}))
        )
        assert r2.status == 200
        assert json.loads(r2.body)["id"] == first_id  # same item, no dup
        # A different URL still creates a new item.
        r3 = _run(
            H.create_item(_req(store, "POST", {"type": "bookmark", "url": "https://example.com/y"}))
        )
        assert r3.status == 201 and json.loads(r3.body)["id"] != first_id

    def test_create_rejects_unknown_type(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        resp = _run(
            H.create_item(_req(store, "POST", {"type": "nonsense", "title": "X", "content": "y"}))
        )
        assert resp.status == 400

    def test_create_rejects_media_type_via_json(self, store):
        """Media/document types carry file bytes — JSON create must reject them (they
        come via /ingest), so we never make a media item with no file."""
        from personalclaw.dashboard.handlers import knowledge as H

        for t in ("image", "audio", "video", "pdf", "sheet", "slides"):
            resp = _run(H.create_item(_req(store, "POST", {"type": t, "title": "x"})))
            assert resp.status == 400, t
        # text + bookmark still author fine.
        ok = _run(
            H.create_item(_req(store, "POST", {"type": "note", "title": "n", "content": "c"}))
        )
        assert ok.status == 201

    def test_journal_blank_title_defaults_to_date(self, store):
        """A journal created with no title gets a date-driven title (enrichment never
        AI-titles journals, so it must not be left a truncated content slug)."""
        from datetime import datetime

        from personalclaw.dashboard.handlers import knowledge as H

        resp = _run(
            H.create_item(
                _req(
                    store,
                    "POST",
                    {
                        "type": "journal",
                        "content": "A long first journal entry about the day's work and reflections.",  # noqa: E501
                    },
                )
            )
        )
        assert resp.status == 201
        body = json.loads(resp.body)
        assert body["title"] == datetime.now().strftime("%B %-d, %Y")
        # A non-journal with blank title still falls back to the content slug.
        resp2 = _run(
            H.create_item(
                _req(
                    store,
                    "POST",
                    {
                        "type": "note",
                        "content": "Note body that becomes the seeded title prefix here.",
                    },
                )
            )
        )
        assert (
            json.loads(resp2.body)["title"]
            == "Note body that becomes the seeded title prefix here."[:60].strip()
        )

    def test_journal_editable_same_day(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        jid = store.create_typed_item(item_type="journal", title="J", content="today's entry")
        resp = _run(
            H.update_item(_req(store, "PATCH", {"content": "edited same day"}, {"id": jid}))
        )
        assert resp.status == 200
        assert store.get_item(jid)["content"] == "edited same day"

    def test_journal_immutable_after_creation_day(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        jid = store.create_typed_item(item_type="journal", title="J", content="yesterday's entry")
        # Backdate creation to a prior day.
        store.db.execute("UPDATE items SET created_at = '2026-01-01T08:00:00' WHERE id = ?", (jid,))
        store.db.commit()
        resp = _run(
            H.update_item(_req(store, "PATCH", {"content": "rewrite history"}, {"id": jid}))
        )
        assert resp.status == 403
        assert store.get_item(jid)["content"] == "yesterday's entry"  # unchanged
        # Curation metadata (pin) is still editable on a past journal.
        ok = _run(H.update_item(_req(store, "PATCH", {"is_pinned": True}, {"id": jid})))
        assert ok.status == 200 and store.get_item(jid)["is_pinned"] is True

    def test_note_always_editable(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        nid = store.create_typed_item(item_type="note", title="N", content="body")
        store.db.execute("UPDATE items SET created_at = '2026-01-01T08:00:00' WHERE id = ?", (nid,))
        store.db.commit()
        resp = _run(H.update_item(_req(store, "PATCH", {"content": "edited later"}, {"id": nid})))
        assert resp.status == 200  # only journals are immutable

    def test_cannot_change_text_item_to_media_type(self, store):
        """A fileless text item can't become a media/document type it has no file for
        (would render a broken card/preview); text↔text still works."""
        from personalclaw.dashboard.handlers import knowledge as H

        nid = store.create_typed_item(item_type="note", title="N", content="x")
        bad = _run(
            H.update_item(
                _req(store, "PATCH", {"type": "image", "item_type": "image"}, {"id": nid})
            )
        )
        assert bad.status == 400 and store.get_item(nid)["type"] == "note"  # unchanged
        bad2 = _run(H.update_item(_req(store, "PATCH", {"item_type": "bookmark"}, {"id": nid})))
        assert bad2.status == 400  # needs a url
        # note → gist (text→text) is fine.
        ok = _run(H.update_item(_req(store, "PATCH", {"item_type": "gist"}, {"id": nid})))
        assert ok.status == 200 and store.get_item(nid)["type"] == "gist"

    def test_content_edit_reenriches(self, store):
        """Editing content/url re-enqueues the item for ingestion (re-extract insights,
        entities, embedding) — matching the agent knowledge_update tool. Curation-only
        edits (tags/pin) do NOT trigger a re-enrich."""
        from personalclaw.dashboard.handlers import knowledge as H

        nid = store.create_typed_item(item_type="note", title="N", content="old body")
        enq: list[str] = []
        resp = _run(
            H.update_item(_req(store, "PATCH", {"content": "new body"}, {"id": nid}, enqueued=enq))
        )
        assert resp.status == 200 and json.loads(resp.body)["reenriching"] is True
        assert nid in enq
        assert store.get_item(nid)["processing_status"] == "queued"
        # A tags-only edit must NOT re-enrich.
        enq2: list[str] = []
        resp2 = _run(
            H.update_item(_req(store, "PATCH", {"tags": ["x"]}, {"id": nid}, enqueued=enq2))
        )
        assert json.loads(resp2.body)["reenriching"] is False and enq2 == []

    def test_content_edit_reingest_false_skips_enrich(self, store):
        """A content edit with reingest=false updates the body but does NOT re-run the
        enrichment node-graph (the user opted out — a quick fix, no model pass)."""
        from personalclaw.dashboard.handlers import knowledge as H

        nid = store.create_typed_item(item_type="note", title="N", content="old body")
        enq: list[str] = []
        resp = _run(
            H.update_item(
                _req(
                    store,
                    "PATCH",
                    {"content": "fixed typo", "reingest": False},
                    {"id": nid},
                    enqueued=enq,
                )
            )
        )
        assert resp.status == 200 and json.loads(resp.body)["reenriching"] is False
        assert enq == []  # not re-enqueued
        assert store.get_item(nid)["content"] == "fixed typo"  # but the edit persisted

    def test_providers_lists_native(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        resp = _run(H.list_providers(_req(store, "GET")))
        body = json.loads(resp.body)
        assert body["providers"][0]["name"] == "native"
        assert body["providers"][0]["always_on"] is True

    def _regen(self, store, body):
        from personalclaw.dashboard.handlers import knowledge as H

        enq: list[str] = []
        app = web.Application()
        app["state"] = SimpleNamespace(
            knowledge_store=store,
            knowledge_ingest_queue=lambda: SimpleNamespace(enqueue=enq.append),
        )
        req = make_mocked_request("POST", "/", app=app)

        async def _json():
            return body

        req.json = _json
        resp = _run(H.regenerate_intelligence(req))
        return json.loads(resp.body), enq

    def test_regenerate_missing_only(self, store):
        a = store.create_typed_item(item_type="note", title="A", content="x")  # no insights
        b = store.create_typed_item(item_type="note", title="B", content="y")
        store.update_item(b, insights={"summary": "done"})
        store.db.commit()
        body, enq = self._regen(store, {"scope": "missing"})
        assert body["queued"] == 1 and a in enq and b not in enq

    def test_regenerate_all(self, store):
        a = store.create_typed_item(item_type="note", title="A", content="x")
        b = store.create_typed_item(item_type="note", title="B", content="y")
        store.update_item(b, insights={"summary": "done"})
        store.db.commit()
        body, enq = self._regen(store, {"scope": "all"})
        assert body["queued"] == 2 and set(enq) == {a, b}

    def test_regenerate_excludes_archived(self, store):
        """Batch re-enrichment skips archived items — no model calls on put-away content."""
        a = store.create_typed_item(item_type="note", title="A", content="x")
        arch = store.create_typed_item(item_type="note", title="Arch", content="y")
        store.update_item(arch, is_archived=1)
        store.db.commit()
        body, enq = self._regen(store, {"scope": "all"})
        assert a in enq and arch not in enq and body["queued"] == 1

    def _list(self, store, query=""):
        from personalclaw.dashboard.handlers import knowledge as H

        app = _app(store)
        req = make_mocked_request(
            "GET", "/api/knowledge/items" + (f"?{query}" if query else ""), app=app
        )
        resp = _run(H.list_items(req))
        return json.loads(resp.body)

    def test_list_pinned_floats_to_top(self, store):
        store.create_typed_item(item_type="note", title="first", content="a")
        pid = store.create_typed_item(item_type="note", title="pinned", content="b")
        store.create_typed_item(item_type="note", title="third", content="c")
        store.update_item(pid, is_pinned=1)
        store.db.commit()
        body = self._list(store)
        assert body["items"][0]["title"] == "pinned"

    def test_list_filters_by_provider(self, store):
        """The ?provider= query param scopes the list to one source (vision: items are
        filterable by provider). A NULL provider counts as native."""
        store.create_typed_item(
            item_type="note", title="native one", content="a", provider="native"
        )
        store.create_typed_item(
            item_type="note", title="from notion", content="b", provider="notion"
        )
        store.db.commit()
        native = self._list(store, "provider=native")
        assert [i["title"] for i in native["items"]] == ["native one"] and native["total"] == 1
        notion = self._list(store, "provider=notion")
        assert [i["title"] for i in notion["items"]] == ["from notion"] and notion["total"] == 1
        # An unknown provider matches nothing (was previously ignored → returned all).
        assert self._list(store, "provider=ghost")["total"] == 0

    def test_list_hides_archived_by_default(self, store):
        store.create_typed_item(item_type="note", title="visible", content="a")
        aid = store.create_typed_item(item_type="note", title="gone", content="b")
        store.update_item(aid, is_archived=1)
        store.db.commit()
        body = self._list(store)
        titles = [it["title"] for it in body["items"]]
        assert "visible" in titles and "gone" not in titles
        assert body["total"] == 1

    def test_list_include_archived_shows_them(self, store):
        store.create_typed_item(item_type="note", title="visible", content="a")
        aid = store.create_typed_item(item_type="note", title="archived", content="b")
        store.update_item(aid, is_archived=1)
        store.db.commit()
        body = self._list(store, "include_archived=1")
        titles = [it["title"] for it in body["items"]]
        assert "visible" in titles and "archived" in titles

    def test_list_truncates_large_content(self, store):
        """The list view ships a content PREVIEW, not every item's full body (payload
        win for large docs). The detail view (get_item) still returns full content."""
        from personalclaw.dashboard.handlers import knowledge as H

        big = "x" * 5000
        iid = store.create_typed_item(item_type="note", title="Big", content=big)
        body = self._list(store)
        it = next(i for i in body["items"] if i["id"] == iid)
        assert len(it["content"]) < 500 and it.get("content_truncated") is True
        # Full content still available via get_item.
        full = json.loads(_run(H.get_item(_req(store, "GET", match_info={"id": iid}))).body)
        assert len(full["content"]) == 5000 and "content_truncated" not in full

    def test_list_entities_parses_aliases_and_redacts_description(self, store):
        """The entities endpoint returns aliases as a real array (not the stored JSON
        string, matching the tags contract) and scrubs credentials from the LLM-derived
        description before it reaches the client."""
        from personalclaw.dashboard.handlers import knowledge as H

        store.add_entity(
            "DeployBot",
            "service",
            aliases=["db", "deployer"],
            description="Service whose root key is AKIAIOSFODNN7EXAMPLE for CI.",
        )
        store.db.commit()
        app = _app(store)
        req = make_mocked_request("GET", "/api/knowledge/entities?q=DeployBot", app=app)
        ents = json.loads(_run(H.list_entities(req)).body)
        ent = next(e for e in ents if e["name"] == "DeployBot")
        assert ent["aliases"] == ["db", "deployer"]  # real array, not '["db",...]'
        assert "AKIAIOSFODNN7EXAMPLE" not in ent["description"] and "REDACTED" in ent["description"]

    def test_get_item_embedded_entities_parse_and_redact(self, store):
        """GET /items/{id} embeds the item's entities — they must parse aliases to an
        array and redact the description, same as the /entities endpoint (the detail
        page's 'More details' panel reads them)."""
        from personalclaw.dashboard.handlers import knowledge as H

        iid = store.create_typed_item(item_type="note", title="N", content="body")
        eid = store.add_entity(
            "Vault",
            "service",
            aliases=["kv"],
            description="Holds the AKIAIOSFODNN7EXAMPLE secret key.",
        )
        store.add_mention(iid, eid)
        store.db.commit()
        req = make_mocked_request("GET", "/", app=_app(store), match_info={"id": iid})
        body = json.loads(_run(H.get_item(req)).body)
        ent = next(e for e in body["entities"] if e["name"] == "Vault")
        assert ent["aliases"] == ["kv"]  # parsed array, not '["kv"]'
        assert "AKIAIOSFODNN7EXAMPLE" not in ent["description"] and "REDACTED" in ent["description"]


# ── native agent tools ──


class TestKnowledgeTools:
    def test_create_search_get_round_trip(self, tmp_path):
        import personalclaw.agents.native.builtin_tools as bt

        prov = bt.NativeBuiltinToolProvider()
        with (
            patch.object(K, "_store", None),
            patch(
                "personalclaw.knowledge.knowledge_db_path", lambda: os.path.join(tmp_path, "k.db")
            ),
        ):
            r = _run(
                prov.invoke(
                    "knowledge_create",
                    {"type": "note", "title": "Recipe", "content": "boil water", "tags": ["food"]},
                )
            )
            assert r.success and "created knowledge note" in r.output
            item_id = r.output.rsplit(" ", 1)[-1]
            g = _run(prov.invoke("knowledge_get", {"id": item_id}))
            assert g.success and "Recipe" in g.output and "boil water" in g.output
            srch = _run(prov.invoke("knowledge_search", {"query": "boil"}))
            assert srch.success and "Recipe" in srch.output

    def test_create_gist_carries_language(self, tmp_path):
        """An agent creating a gist can set its language (for syntax highlighting),
        matching the create form."""
        import personalclaw.agents.native.builtin_tools as bt

        prov = bt.NativeBuiltinToolProvider()
        with (
            patch.object(K, "_store", None),
            patch(
                "personalclaw.knowledge.knowledge_db_path", lambda: os.path.join(tmp_path, "k.db")
            ),
        ):
            r = _run(
                prov.invoke(
                    "knowledge_create",
                    {
                        "type": "gist",
                        "title": "snippet",
                        "content": "x=1",
                        "gist_language": "python",
                    },
                )
            )
            assert r.success
            iid = r.output.rsplit(" ", 1)[-1]
            assert K.get_knowledge_store().get_item(iid)["gist_language"] == "python"

    def test_create_rejects_media_type(self, tmp_path):
        import personalclaw.agents.native.builtin_tools as bt

        prov = bt.NativeBuiltinToolProvider()
        with (
            patch.object(K, "_store", None),
            patch(
                "personalclaw.knowledge.knowledge_db_path", lambda: os.path.join(tmp_path, "k.db")
            ),
        ):
            r = _run(prov.invoke("knowledge_create", {"type": "image", "title": "x"}))
            assert not r.success and "unsupported type" in r.error

    def test_create_untitled_journal_gets_date_title(self, tmp_path):
        """An agent creating a journal without a title gets the entry's DATE as the title
        (journals are date-driven; enrichment never AI-titles them) — matching the HTTP
        create handler, not a content-slug that would stick forever."""
        import re

        import personalclaw.agents.native.builtin_tools as bt

        prov = bt.NativeBuiltinToolProvider()
        with (
            patch.object(K, "_store", None),
            patch(
                "personalclaw.knowledge.knowledge_db_path", lambda: os.path.join(tmp_path, "k.db")
            ),
        ):
            r = _run(
                prov.invoke("knowledge_create", {"type": "journal", "content": "Daily reflection."})
            )
            assert r.success
            iid = r.output.rsplit(" ", 1)[-1]
            title = K.get_knowledge_store().get_item(iid)["title"]
            # A month-name date like "June 18, 2026" — not the content slug.
            assert re.match(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$", title), title

    def test_update_sets_gist_language_only_for_gists(self, tmp_path):
        """An agent can correct a gist's language via knowledge_update (parity with the
        HTTP PATCH + the create tool). On a non-gist item the language is ignored, not
        stamped onto a meaningless column."""
        import personalclaw.agents.native.builtin_tools as bt

        prov = bt.NativeBuiltinToolProvider()
        with (
            patch.object(K, "_store", None),
            patch(
                "personalclaw.knowledge.knowledge_db_path", lambda: os.path.join(tmp_path, "k.db")
            ),
        ):
            store = K.get_knowledge_store()
            gid = store.create_typed_item(item_type="gist", title="snip", content="SELECT 1")
            nid = store.create_typed_item(item_type="note", title="n", content="hello")
            store.db.commit()

            # Set the language on the gist.
            r = _run(prov.invoke("knowledge_update", {"id": gid, "gist_language": "sql"}))
            assert r.success
            assert store.get_item(gid)["gist_language"] == "sql"

            # The same arg on a note is silently ignored (note has no language).
            r2 = _run(prov.invoke("knowledge_update", {"id": nid, "gist_language": "sql"}))
            assert r2.success and "only applies to gist" in r2.output
            assert not (store.get_item(nid).get("gist_language") or "")

    def test_update_enforces_journal_immutability(self, tmp_path):
        """The agent knowledge_update tool enforces the SAME journal immutability the HTTP
        PATCH does: a past-day journal's content/title can't be edited, but tags/pin can.
        Otherwise an agent could mutate a record the UI + HTTP path forbid."""
        import personalclaw.agents.native.builtin_tools as bt

        prov = bt.NativeBuiltinToolProvider()
        with (
            patch.object(K, "_store", None),
            patch(
                "personalclaw.knowledge.knowledge_db_path", lambda: os.path.join(tmp_path, "k.db")
            ),
        ):
            store = K.get_knowledge_store()
            jid = store.create_typed_item(
                item_type="journal", title="June 1, 2026", content="old entry"
            )
            store.db.execute(
                "UPDATE items SET created_at=? WHERE id=?", ("2026-06-01T09:00:00", jid)
            )
            store.db.commit()

            # Past-day content edit → blocked, content preserved.
            r = _run(prov.invoke("knowledge_update", {"id": jid, "content": "rewrite history"}))
            assert not r.success and "immutable" in r.error
            assert store.get_item(jid)["content"] == "old entry"

            # Curation (tags) on the same past-day journal → allowed.
            r2 = _run(prov.invoke("knowledge_update", {"id": jid, "tags": ["retro"]}))
            assert r2.success and store.get_item(jid)["tags"] == ["retro"]

            # A journal created TODAY is still editable.
            jt = store.create_typed_item(item_type="journal", title="today", content="x")
            store.db.commit()
            r3 = _run(prov.invoke("knowledge_update", {"id": jt, "content": "edited"}))
            assert r3.success and store.get_item(jt)["content"] == "edited"

    def test_get_surfaces_summary_and_insights(self, tmp_path):
        """knowledge_get gives the agent the distilled enrichment (summary, key points,
        action items), not just raw content — so it needn't re-read the whole doc."""
        import personalclaw.agents.native.builtin_tools as bt

        prov = bt.NativeBuiltinToolProvider()
        with (
            patch.object(K, "_store", None),
            patch(
                "personalclaw.knowledge.knowledge_db_path", lambda: os.path.join(tmp_path, "k.db")
            ),
        ):
            store = K.get_knowledge_store()
            iid = store.create_typed_item(
                item_type="note", title="Doc", content="long body text here"
            )
            store.update_item(
                iid,
                summary="A crisp summary.",
                insights={
                    "summary": "A crisp summary.",
                    "key_points": ["kp one", "kp two"],
                    "action_items": ["do this"],
                },
            )
            store.db.commit()
            g = _run(prov.invoke("knowledge_get", {"id": iid}))
            assert g.success
            assert "summary: A crisp summary." in g.output
            assert "kp one" in g.output and "kp two" in g.output
            assert "do this" in g.output

    def test_get_surfaces_file_shape(self, tmp_path):
        """knowledge_get gives the agent a file-backed item's shape (dimensions, size,
        mime) — it can't open the bytes, so it needs to know what it's dealing with."""
        import personalclaw.agents.native.builtin_tools as bt

        prov = bt.NativeBuiltinToolProvider()
        with (
            patch.object(K, "_store", None),
            patch(
                "personalclaw.knowledge.knowledge_db_path", lambda: os.path.join(tmp_path, "k.db")
            ),
        ):
            store = K.get_knowledge_store()
            iid = store.create_typed_item(
                item_type="image",
                title="photo",
                content="a sunset",
                extra={
                    "file_path": "/x/y.png",
                    "mime_type": "image/png",
                    "file_size": 2048,
                    "file_metadata": {"width": 640, "height": 480},
                },
            )
            g = _run(prov.invoke("knowledge_get", {"id": iid}))
            assert g.success and "file:" in g.output
            assert "640x480" in g.output and "image/png" in g.output

    def test_get_signals_pending_enrichment(self, tmp_path):
        """knowledge_get on a still-processing item flags that enrichment is pending,
        so the agent doesn't read missing insights as 'this item has none'."""
        import personalclaw.agents.native.builtin_tools as bt

        prov = bt.NativeBuiltinToolProvider()
        with (
            patch.object(K, "_store", None),
            patch(
                "personalclaw.knowledge.knowledge_db_path", lambda: os.path.join(tmp_path, "k.db")
            ),
        ):
            store = K.get_knowledge_store()
            iid = store.create_typed_item(
                item_type="note",
                title="WIP",
                content="body",
                extra={"processing_status": "processing"},
            )
            store.db.commit()
            g = _run(prov.invoke("knowledge_get", {"id": iid}))
            assert g.success and "still enriching" in g.output
            # A done item shows no such notice.
            store.update_item(iid, processing_status="done")
            store.db.commit()
            assert "still enriching" not in _run(prov.invoke("knowledge_get", {"id": iid})).output

    def test_update_round_trip(self, tmp_path):
        import personalclaw.agents.native.builtin_tools as bt

        prov = bt.NativeBuiltinToolProvider()
        with (
            patch.object(K, "_store", None),
            patch(
                "personalclaw.knowledge.knowledge_db_path", lambda: os.path.join(tmp_path, "k.db")
            ),
        ):
            r = _run(
                prov.invoke(
                    "knowledge_create", {"type": "note", "title": "Old", "content": "old body"}
                )
            )
            item_id = r.output.rsplit(" ", 1)[-1]
            u = _run(
                prov.invoke("knowledge_update", {"id": item_id, "title": "New", "tags": ["x"]})
            )
            assert u.success and "updated knowledge item" in u.output
            g = _run(prov.invoke("knowledge_get", {"id": item_id}))
            assert "New" in g.output and "x" in g.output

    def test_update_missing_item(self, tmp_path):
        import personalclaw.agents.native.builtin_tools as bt

        prov = bt.NativeBuiltinToolProvider()
        with (
            patch.object(K, "_store", None),
            patch(
                "personalclaw.knowledge.knowledge_db_path", lambda: os.path.join(tmp_path, "k.db")
            ),
        ):
            u = _run(prov.invoke("knowledge_update", {"id": "nope", "title": "x"}))
            assert not u.success and "not found" in u.error

    def test_update_requires_a_field(self, tmp_path):
        import personalclaw.agents.native.builtin_tools as bt

        prov = bt.NativeBuiltinToolProvider()
        with (
            patch.object(K, "_store", None),
            patch(
                "personalclaw.knowledge.knowledge_db_path", lambda: os.path.join(tmp_path, "k.db")
            ),
        ):
            r = _run(
                prov.invoke("knowledge_create", {"type": "note", "title": "T", "content": "c"})
            )
            item_id = r.output.rsplit(" ", 1)[-1]
            u = _run(prov.invoke("knowledge_update", {"id": item_id}))
            assert not u.success and "no updatable fields" in u.error

    def test_stats_overview(self, tmp_path):
        import personalclaw.agents.native.builtin_tools as bt

        prov = bt.NativeBuiltinToolProvider()
        with (
            patch.object(K, "_store", None),
            patch(
                "personalclaw.knowledge.knowledge_db_path", lambda: os.path.join(tmp_path, "k.db")
            ),
        ):
            _run(
                prov.invoke(
                    "knowledge_create",
                    {"type": "note", "title": "A", "content": "x", "tags": ["t1", "shared"]},
                )
            )
            _run(
                prov.invoke(
                    "knowledge_create",
                    {"type": "gist", "title": "B", "content": "y", "tags": ["shared"]},
                )
            )
            s = _run(prov.invoke("knowledge_stats", {}))
            assert s.success
            assert "2 items" in s.output
            assert "note: 1" in s.output and "gist: 1" in s.output
            assert "shared (2)" in s.output

    def test_stats_empty(self, tmp_path):
        import personalclaw.agents.native.builtin_tools as bt

        prov = bt.NativeBuiltinToolProvider()
        with (
            patch.object(K, "_store", None),
            patch(
                "personalclaw.knowledge.knowledge_db_path", lambda: os.path.join(tmp_path, "k.db")
            ),
        ):
            s = _run(prov.invoke("knowledge_stats", {}))
            assert s.success and "empty" in s.output.lower()


# ── P3: structured insights (cross-cutting intelligence layer) ──


class _FakePool:
    """Stand-in LLMPool that returns a canned response from .send()."""

    def __init__(self, response: str):
        self._response = response

    async def send(self, prompt, timeout=0):  # noqa: ARG002
        return self._response


class TestInsightsExtractor:
    def test_parses_category_keyed_blob(self):
        from personalclaw.knowledge.insights import InsightsExtractor

        resp = json.dumps(
            {
                "summary": "A note about boiling water.",
                "key_points": ["heat to 100C", "use a kettle"],
                "topics": ["cooking", "water"],
                "action_items": [],
            }
        )
        out = _run(InsightsExtractor(pool=_FakePool(resp)).extract("boil water in a kettle"))
        assert out["summary"] == "A note about boiling water."
        assert out["key_points"] == ["heat to 100C", "use a kettle"]
        assert out["topics"] == ["cooking", "water"]
        assert "action_items" not in out  # empty lists are dropped

    def test_parses_fenced_json(self):
        from personalclaw.knowledge.insights import InsightsExtractor

        resp = "```json\n" + json.dumps({"summary": "S", "key_points": ["a"]}) + "\n```"
        out = _run(InsightsExtractor(pool=_FakePool(resp)).extract("x y z"))
        assert out["summary"] == "S" and out["key_points"] == ["a"]

    def test_empty_on_no_pool_or_no_content(self):
        from personalclaw.knowledge.insights import InsightsExtractor

        assert _run(InsightsExtractor(pool=None).extract("anything")) == {}
        assert _run(InsightsExtractor(pool=_FakePool("{}")).extract("   ")) == {}

    def test_empty_on_unparseable(self):
        from personalclaw.knowledge.insights import InsightsExtractor

        assert _run(InsightsExtractor(pool=_FakePool("not json at all")).extract("x")) == {}


class TestGenerateIntelligence:
    def test_endpoint_reenqueues_full_pipeline(self, store):
        """Single-item Regenerate re-enqueues the FULL ingestion pipeline (not a
        narrow insights-only pass) so insights+entities+intents+embed all refresh
        consistently — same as a content edit / batch regen."""
        from personalclaw.dashboard.handlers import knowledge as H

        item_id = store.create_typed_item(
            item_type="note", title="N", content="boil water in a kettle"
        )
        enq: list[str] = []
        app = _app(store, enqueued=enq)
        req = make_mocked_request("POST", "/", app=app, match_info={"id": item_id})
        resp = _run(H.generate_intelligence(req))
        assert resp.status == 200
        assert item_id in enq  # re-enqueued for full ingestion
        assert json.loads(resp.body)["processing_status"] == "queued"

    def test_endpoint_404_on_missing_item(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        app = _app(store)
        req = make_mocked_request("POST", "/", app=app, match_info={"id": "nope"})
        resp = _run(H.generate_intelligence(req))
        assert resp.status == 404


# ── P2: media classification + previewable media items + file serving ──


class TestMediaClassify:
    def test_classify_and_binary(self):
        from personalclaw.knowledge import media

        assert media.classify("photo.PNG") == "image"
        assert media.classify("clip.mp4") == "video"
        assert media.classify("song.flac") == "audio"
        assert media.classify("report.pdf") == "pdf"
        assert media.classify("sheet.xlsx") == "sheet"
        assert media.classify("notes.xyz") is None
        # Common camera/phone image formats are accepted (HEIC = the default iPhone
        # photo) rather than rejected as unsupported — stored even if thumbnailing the
        # bytes isn't possible without an optional codec.
        assert media.classify("IMG_0001.heic") == "image"
        assert media.classify("scan.tiff") == "image" and media.classify("scan.tif") == "image"
        # Binary media is stored previewable AND run through its node-graph.
        assert media.is_binary_media("photo.png") is True
        assert media.is_binary_media("IMG.heic") is True
        assert media.is_binary_media("report.pdf") is False
        assert media.guess_mime("photo.png") == "image/png"

    def test_classify_routes_source_code_to_gist(self):
        """A source-code upload is a gist (code), not a generic document — so it gets
        syntax highlighting + a "Gist · <Language>" label. classify() returns 'gist'
        and code_language() supplies the highlight.js language id."""
        from personalclaw.knowledge import media

        assert media.classify("algo.py") == "gist" and media.code_language("algo.py") == "python"
        assert media.classify("app.ts") == "gist" and media.code_language("app.ts") == "typescript"
        assert media.classify("main.go") == "gist" and media.code_language("main.go") == "go"
        assert media.classify("schema.sql") == "gist" and media.code_language("schema.sql") == "sql"
        assert media.classify("lib.rs") == "gist" and media.code_language("lib.rs") == "rust"
        # Prose/markup stays a document (read inline, no language).
        assert (
            media.classify("readme.md") == "document" and media.code_language("readme.md") is None
        )
        assert media.classify("notes.txt") == "document"
        # A code file is NOT binary media (it's text-backed, stored as content).
        assert media.is_binary_media("algo.py") is False

    def test_classify_common_extension_variants(self):
        """Full-word / sibling extensions a user might upload aren't rejected: .markdown/
        .text → document, .tsv → sheet (tab-delimited table), .m4v → video (mp4-playable)."""
        from personalclaw.knowledge import media

        assert media.classify("notes.markdown") == "document"
        assert media.classify("readme.text") == "document"
        assert media.classify("table.tsv") == "sheet"
        assert media.classify("clip.m4v") == "video"
        # .m4v plays as video/mp4 in the browser (not the legacy x-m4v mimetypes returns).
        assert media.guess_mime("clip.m4v") == "video/mp4"

    def test_classify_uses_mime_to_disambiguate_webm(self):
        """A browser audio recording is audio/webm — the .webm extension alone maps to
        video, so the mime hint must steer it to audio (else it runs the video graph)."""
        from personalclaw.knowledge import media

        assert media.classify("rec.webm", "audio/webm") == "audio"
        assert media.classify("clip.webm", "video/webm") == "video"
        assert media.classify("x.webm") == "video"  # no hint → extension default
        assert media.classify("voice.ogg", "audio/ogg") == "audio"
        # A non-media mime never overrides a concrete extension mapping.
        assert media.classify("doc.pdf", "application/octet-stream") == "pdf"
        assert media.classify("x.png", "application/octet-stream") == "image"


class TestMediaItems:
    def _files_dir(self, tmp_path, monkeypatch):
        d = tmp_path / "kfiles"
        d.mkdir()
        monkeypatch.setattr("personalclaw.knowledge.knowledge_files_dir", lambda: str(d))
        return d

    def test_store_file_item_keeps_file(self, store, tmp_path, monkeypatch):
        from personalclaw.dashboard.handlers import knowledge as H

        self._files_dir(tmp_path, monkeypatch)
        src = tmp_path / "in.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)  # not a real image; thumbnail just no-ops
        item, is_new = H._store_file_item(store, str(src), "in.png")
        assert is_new
        assert item["type"] == "image"
        assert item["file_path"] and Path(item["file_path"]).is_file()
        assert item["mime_type"] == "image/png" and item["file_size"] > 0
        # Original temp was moved, not copied.
        assert not src.exists()
        # Queued for node-graph ingestion (Image graph runs after the caller enqueues).
        assert item["processing_status"] == "queued"

    def test_store_file_item_webm_audio_classifies_and_mimes_as_audio(
        self, store, tmp_path, monkeypatch
    ):
        """A browser audio recording is audio/webm — store it as an AUDIO item with an
        audio/* mime (the .webm extension alone would make it video/webm)."""
        from personalclaw.dashboard.handlers import knowledge as H

        self._files_dir(tmp_path, monkeypatch)
        src = tmp_path / "rec.webm"
        src.write_bytes(b"\x1a\x45\xdf\xa3" + b"x" * 64)  # EBML header bytes + filler
        item, is_new = H._store_file_item(store, str(src), "recording.webm", mime="audio/webm")
        assert is_new
        assert item["type"] == "audio"  # not video
        assert item["mime_type"] == "audio/webm"  # honors the upload mime, not video/webm
        # A genuine video/webm still stores as video.
        src2 = tmp_path / "clip.webm"
        src2.write_bytes(b"\x1a\x45\xdf\xa3" + b"y" * 64)
        item2, _ = H._store_file_item(store, str(src2), "clip.webm", mime="video/webm")
        assert item2["type"] == "video" and item2["mime_type"] == "video/webm"

    def test_store_file_item_dedups_identical_content(self, store, tmp_path, monkeypatch):
        """Re-storing byte-identical content into the same space returns the existing
        item (is_new False), not a duplicate — and doesn't leave an orphan file."""
        from personalclaw.dashboard.handlers import knowledge as H

        files_dir = self._files_dir(tmp_path, monkeypatch)
        data = b"identical bytes for dedup check"
        a = tmp_path / "a.txt"
        a.write_bytes(data)
        item1, new1 = H._store_file_item(store, str(a), "a.txt")
        assert new1
        b = tmp_path / "b.txt"
        b.write_bytes(data)  # same content, different name
        item2, new2 = H._store_file_item(store, str(b), "b.txt")
        assert new2 is False and item2["id"] == item1["id"]  # dedup hit
        assert store.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
        # The redundant copy was removed (only the original's stored file remains).
        assert len([p for p in files_dir.iterdir() if p.is_file()]) == 1

    def test_store_file_item_document_is_one_logical_doc(self, store, tmp_path, monkeypatch):
        """A document upload is ONE item (not chunk rows) — file extraction +
        chunking happen inside the graph/embedder."""
        from personalclaw.dashboard.handlers import knowledge as H

        self._files_dir(tmp_path, monkeypatch)
        src = tmp_path / "report.pdf"
        src.write_bytes(b"%PDF-1.4 fake")
        item, _ = H._store_file_item(store, str(src), "report.pdf")
        assert item["type"] == "pdf"
        assert item["file_path"] and item["processing_status"] == "queued"
        # Exactly one row exists for this upload — no chunk fan-out.
        rows = store.db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        assert rows == 1

    def test_store_file_item_source_code_becomes_gist_with_language(
        self, store, tmp_path, monkeypatch
    ):
        """A source-code upload becomes a text-backed GIST: its content IS the code (read
        inline, no file on disk), the language is stamped for highlighting, and it's one
        logical doc queued for the passthrough graph."""
        from personalclaw.dashboard.handlers import knowledge as H

        self._files_dir(tmp_path, monkeypatch)
        code = "def f(x):\n    return x * 2  # double\n"
        src = tmp_path / "algo.py"
        src.write_text(code)
        item, is_new = H._store_file_item(store, str(src), "algo.py")
        assert is_new
        assert item["type"] == "gist"
        assert item["gist_language"] == "python"
        assert item["content"] == code  # content IS the code, read inline
        assert not item.get("file_path")  # text-backed, not a file item
        assert item["processing_status"] == "queued"
        assert item["word_count"] > 0
        # The temp upload was consumed (moved/unlinked), not left behind.
        assert not src.exists()
        # Re-uploading byte-identical code dedups against the existing gist.
        src2 = tmp_path / "copy.py"
        src2.write_text(code)
        item2, new2 = H._store_file_item(store, str(src2), "copy.py")
        assert new2 is False and item2["id"] == item["id"]

    def test_serve_path_guard_rejects_outside_root(self, store, tmp_path, monkeypatch):
        from personalclaw.dashboard.handlers import knowledge as H

        self._files_dir(tmp_path, monkeypatch)
        # An item whose file_path points OUTSIDE the files dir must not serve.
        evil = tmp_path / "secret.png"
        evil.write_bytes(b"data")
        iid = store.create_typed_item(item_type="image", title="x", extra={"file_path": str(evil)})
        path, mime = H._serve_item_path(store, iid, thumbnail=False)
        assert path is None and mime == ""

    def test_serve_path_returns_in_root_file(self, store, tmp_path, monkeypatch):
        from personalclaw.dashboard.handlers import knowledge as H

        d = self._files_dir(tmp_path, monkeypatch)
        good = d / "ok.png"
        good.write_bytes(b"data")
        iid = store.create_typed_item(
            item_type="image", title="x", extra={"file_path": str(good), "mime_type": "image/png"}
        )
        path, mime = H._serve_item_path(store, iid, thumbnail=False)
        assert path == good.resolve() and mime == "image/png"

    def test_delete_removes_media_files(self, store, tmp_path, monkeypatch):
        from personalclaw.dashboard.handlers import knowledge as H

        d = self._files_dir(tmp_path, monkeypatch)
        f = d / "del.png"
        f.write_bytes(b"data")
        t = d / "del.thumb.webp"
        t.write_bytes(b"thumb")
        iid = store.create_typed_item(
            item_type="image", title="x", extra={"file_path": str(f), "thumbnail_path": str(t)}
        )
        req = make_mocked_request("DELETE", "/", app=_app(store), match_info={"id": iid})
        resp = _run(H.delete_item(req))
        assert resp.status == 200
        assert not f.exists() and not t.exists()

    def test_delete_removes_derived_media_artifacts(self, store, tmp_path, monkeypatch):
        """A video/audio item's av_split/frame_extract nodes write derived files named
        '<item_id>.audio.wav' / '<item_id>.frame_NNN.jpg' / '<item_id>.dense*' into the
        files dir, tracked in NO db column. Delete must sweep them by the '<item_id>.'
        prefix — otherwise every deleted video leaks its frames + split audio."""
        from personalclaw.dashboard.handlers import knowledge as H

        d = self._files_dir(tmp_path, monkeypatch)
        src = d / "abc123-source.mp4"
        src.write_bytes(b"movie")
        iid = store.create_typed_item(item_type="video", title="v", extra={"file_path": str(src)})
        # derived artifacts the media pipeline would have written (named by item id)
        derived = [
            d / f"{iid}.audio.wav",
            d / f"{iid}.frame_001.jpg",
            d / f"{iid}.frame_002.jpg",
            d / f"{iid}.dense_001.jpg",
        ]
        for p in derived:
            p.write_bytes(b"x")
        # a sibling item's artifacts (different id) must NOT be swept
        other = d / "0000ffff-other.frame_001.jpg"
        other.write_bytes(b"keep")
        req = make_mocked_request("DELETE", "/", app=_app(store), match_info={"id": iid})
        resp = _run(H.delete_item(req))
        assert resp.status == 200
        assert not src.exists()
        assert all(not p.exists() for p in derived)  # every derived artifact gone
        assert other.exists()  # another item's files untouched

    def test_delete_spares_file_outside_files_dir(self, store, tmp_path, monkeypatch):
        """Defense-in-depth: delete cleanup only unlinks files inside the knowledge
        files dir — a corrupt file_path pointing elsewhere is never deleted."""
        from personalclaw.dashboard.handlers import knowledge as H

        self._files_dir(tmp_path, monkeypatch)
        outside = tmp_path / "important.txt"
        outside.write_bytes(b"do not delete")
        iid = store.create_typed_item(
            item_type="image", title="x", extra={"file_path": str(outside)}
        )
        req = make_mocked_request("DELETE", "/", app=_app(store), match_info={"id": iid})
        resp = _run(H.delete_item(req))
        assert resp.status == 200
        assert outside.exists()  # outside-root file untouched


class TestSkillSynthesisParsing:
    """The intent→skill synthesis uses a delimited (not JSON) contract because the
    procedure is multi-line markdown. The parser must be robust to a model that
    echoes the template more than once or wraps headers in markdown bold."""

    def test_parses_clean_delimited_response(self):
        from personalclaw.dashboard.handlers.knowledge import _parse_skill_sections

        resp = "DESCRIPTION: Track X\nTRIGGERS: a, b\nPROCEDURE:\n1. do a\n2. do b"
        p = _parse_skill_sections(resp)
        assert p["description"] == "Track X"
        assert p["triggers"] == "a, b"
        assert p["procedure"] == "1. do a\n2. do b"

    def test_procedure_anchors_to_last_header_no_leak(self):
        from personalclaw.dashboard.handlers.knowledge import _parse_skill_sections

        # Model echoed prose first, then the structured block.
        resp = (
            "Here is the skill.\n\nSome prose copy...\n\n"
            "DESCRIPTION: D\nTRIGGERS: t\nPROCEDURE:\n1. real step"
        )
        p = _parse_skill_sections(resp)
        assert p["procedure"] == "1. real step"
        assert "DESCRIPTION:" not in p["procedure"]
        assert "PROCEDURE:" not in p["procedure"]

    def test_bold_headers_and_no_header_fallback(self):
        from personalclaw.dashboard.handlers.knowledge import _parse_skill_sections

        assert (
            _parse_skill_sections("**DESCRIPTION:** D\n**PROCEDURE:**\nstep")["description"] == "D"
        )
        # No headers at all → whole body is the procedure.
        assert _parse_skill_sections("just do the thing")["procedure"] == "just do the thing"

    def test_slugify_intent(self):
        from personalclaw.dashboard.handlers.knowledge import _slugify_intent

        assert _slugify_intent("homelab-improve", "x") == "homelab-improve"
        assert _slugify_intent("", "Track My Stuff!") == "track-my-stuff"
        assert _slugify_intent("", "") == "intent-skill"


class TestItemGraphShape:
    """GET /items/{id}/graph exposes the ingestion node-graph shape for the mini-DAG."""

    def test_note_graph_is_passthrough_plus_terminals(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        nid = store.create_typed_item(item_type="note", title="N", content="x")
        resp = _run(H.get_item_graph(_req(store, "GET", match_info={"id": nid})))
        body = json.loads(resp.body)
        types = [n["node_type"] for n in body["nodes"]]
        assert types == ["passthrough", "insights", "entities", "intents", "embed"]
        # Linear chain through the terminal stages.
        assert {"from": "passthrough", "to": "insights"} in body["edges"]
        assert {"from": "intents", "to": "embed"} in body["edges"]

    def test_image_graph_has_parallel_nodes_and_terminals(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        iid = store.create_typed_item(item_type="image", title="i.png")
        resp = _run(H.get_item_graph(_req(store, "GET", match_info={"id": iid})))
        types = {n["node_type"] for n in json.loads(resp.body)["nodes"]}
        assert {"exif", "ocr", "vision", "consolidate"} <= types  # graph nodes
        assert {"insights", "entities", "intents", "embed"} <= types  # terminals appended

    def test_video_graph_edges_deduped(self, store):
        """The video DAG routes video_classify→vision via two conditions (visual,
        talking-head); the shape view collapses them to ONE edge (no duplicate line)."""
        from personalclaw.dashboard.handlers import knowledge as H

        iid = store.create_typed_item(item_type="video", title="v.mp4")
        body = json.loads(_run(H.get_item_graph(_req(store, "GET", match_info={"id": iid}))).body)
        edge_pairs = [(e["from"], e["to"]) for e in body["edges"]]
        assert len(edge_pairs) == len(set(edge_pairs))  # no duplicates
        assert ("video_classify", "vision") in edge_pairs

    def test_graph_404_for_missing_item(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        resp = _run(H.get_item_graph(_req(store, "GET", match_info={"id": "nope"})))
        assert resp.status == 404


class TestEntityRelated:
    """The entity sidebar's 'Connected to' section (by-name/{name}/related)."""

    def test_related_entities_with_direction(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        a = store.add_entity("FeebasService", "service")
        b = store.add_entity("GraphQL", "technology")
        store.add_entity_relation(a, b, "uses")
        store.db.commit()
        # From GraphQL's side: incoming 'uses' from FeebasService.
        resp = _run(H.get_entity_related(_req(store, "GET", match_info={"name": "GraphQL"})))
        rel = json.loads(resp.body)["related"]
        assert len(rel) == 1
        assert rel[0]["name"] == "FeebasService" and rel[0]["relation_type"] == "uses"
        assert rel[0]["outgoing"] is False
        # From FeebasService's side: outgoing.
        resp2 = _run(H.get_entity_related(_req(store, "GET", match_info={"name": "FeebasService"})))
        assert json.loads(resp2.body)["related"][0]["outgoing"] is True

    def test_related_empty_for_unknown_entity(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        resp = _run(H.get_entity_related(_req(store, "GET", match_info={"name": "Nope"})))
        assert json.loads(resp.body)["related"] == []


class TestArchivedHiddenFromRetrieval:
    """Archived items are hidden from related-items and the entity 'mentioned in' list
    (matching the search + default-list semantics)."""

    def _link(self, store, item_id, ename):
        eid = store.add_entity(ename, "concept")
        store.add_mention(item_id, eid)
        store.db.commit()
        return eid

    def test_related_items_excludes_archived(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        a = store.create_typed_item(item_type="note", title="A", content="x")
        b = store.create_typed_item(item_type="note", title="B-archived", content="y")
        eid = self._link(store, a, "SharedThing")
        store.add_mention(b, eid)
        store.db.commit()
        # Before archiving, B is related to A.
        rel = json.loads(_run(H.get_related_items(_req(store, "GET", match_info={"id": a}))).body)
        assert any(r["id"] == b for r in rel)
        # Archive B → drops out of A's related.
        store.update_item(b, is_archived=1)
        store.db.commit()
        rel2 = json.loads(_run(H.get_related_items(_req(store, "GET", match_info={"id": a}))).body)
        assert not any(r["id"] == b for r in rel2)

    def test_entity_items_excludes_archived(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        iid = store.create_typed_item(item_type="note", title="MentionsZeta", content="about Zeta")
        self._link(store, iid, "Zeta")
        store.update_item(iid, is_archived=1)
        store.db.commit()
        items = json.loads(
            _run(H.get_entity_items(_req(store, "GET", match_info={"name": "Zeta"}))).body
        )
        assert not any(it["id"] == iid for it in items)

    def test_entity_items_is_mentions_based_not_text_match(self, store):
        """entity_items comes from the mentions table (the graph's links), not an FTS
        text match — so a canonical entity links its item even when the text used a
        variant, and an item that merely contains the word is NOT a false positive."""
        from personalclaw.dashboard.handlers import knowledge as H

        # Item A is linked to entity 'DynamoDB' in the graph, but its text says 'ddb'.
        a = store.create_typed_item(
            item_type="note", title="Storage", content="we use ddb for the table"
        )
        self._link(store, a, "DynamoDB")
        # Item B contains the word 'DynamoDB' in text but is NOT linked in the graph.
        store.create_typed_item(
            item_type="note", title="Aside", content="unrelated note that name-drops DynamoDB once"
        )
        store.db.commit()
        items = json.loads(
            _run(H.get_entity_items(_req(store, "GET", match_info={"name": "DynamoDB"}))).body
        )
        ids = {it["id"] for it in items}
        assert a in ids  # linked item included despite text variant
        assert len(ids) == 1  # the unlinked text-only mention is NOT a false positive
        # Unknown entity → empty, not an error.
        assert (
            json.loads(
                _run(
                    H.get_entity_items(_req(store, "GET", match_info={"name": "Nonexistent"}))
                ).body
            )
            == []
        )


class TestStaleEmbeddingCount:
    """_stale_embedding_count flags items whose stored vector dimension != the active
    model's — the cue the UI uses to offer a re-embed after a model switch."""

    def _embed(self, store, item_id, dim):
        import struct

        store.db.execute(
            "UPDATE items SET embedding = ? WHERE id = ?",
            (struct.pack(f"{dim}f", *([0.1] * dim)), item_id),
        )
        store.db.commit()

    def test_counts_only_dimension_mismatches(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        cur = store.create_typed_item(item_type="note", title="Current", content="x")
        old = store.create_typed_item(item_type="note", title="Old model", content="y")
        store.create_typed_item(item_type="note", title="Unembedded", content="z")  # no vector
        store.db.commit()
        self._embed(store, cur, 384)  # active-model dimension
        self._embed(store, old, 768)  # a previous model's dimension

        class _Emb:
            model = "all-MiniLM-L6-v2"

            def is_available(self):
                return True

            def dim(self):
                return 384

        assert H._stale_embedding_count(store, _Emb()) == 1  # only the 768-dim item

    def test_zero_when_embedder_unavailable_or_dimless(self, store):
        from personalclaw.dashboard.handlers import knowledge as H

        iid = store.create_typed_item(item_type="note", title="E", content="x")
        self._embed(store, iid, 768)
        # No embedder → 0 (can't know the active dim, don't guess everything is stale).
        assert H._stale_embedding_count(store, None) == 0

        class _Unavail:
            def is_available(self):
                return False

            def dim(self):
                return None

        assert H._stale_embedding_count(store, _Unavail()) == 0
