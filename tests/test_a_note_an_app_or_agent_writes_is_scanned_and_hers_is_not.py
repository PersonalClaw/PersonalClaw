"""A note an app, an agent or a workflow writes is scanned before Knowledge keeps it; hers is not.

Knowledge took a note's text from anyone who wrote one: an app through the API, the agent with its
knowledge tools (often text it read on the web), a workflow's persist step. None of it was
scanned, because the ingest scanned only what a document reader read of a file. Now the text of
a note that is not the owner's own is read by the content scan, by the rules an upload's text is
read by, before anything is kept of it. Each of these doors has someone to answer, so a refusal
answers that one, as an upload's does, and nothing is made or changed.

What the owner writes herself in the app is her own words, as what she writes in the chat is, and
the chat never scans her message: so a note she writes is kept as she wrote it, whatever it says.

The refused text is the scanner's own example of content it refuses: a shopping list with an
invisible right-to-left override character in it. The same list without it is ordinary content.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.knowledge as K
from personalclaw.apps.permissions import scoped_to_app
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.sel import sel

#: The scanner's own example of content it refuses: a list with a right-to-left override in it.
OVERRIDE_LIST = "Shopping list for Saturday: \u202eeggs\u202c, flour, apples."
#: The same list without the override: ordinary content.
CLEAN_LIST = "Shopping list for Saturday: eggs, flour, apples."
#: The status line of an item whose text the content scan refused.
REFUSED = "Its text failed the content safety scan, so nothing was made from it."


def _scan_rows() -> list[tuple[str, str]]:
    return [
        (r["caller_identity"], r["outcome"])
        for r in sel().recent(limit=200)
        if r.get("operation") == "upload_scan"
    ]


# ── the API: an app's note, and hers ─────────────────────────────────────────


@pytest.fixture
def store(tmp_path) -> KnowledgeStore:
    return KnowledgeStore(os.path.join(tmp_path, "k.db"))


def _req(store, method, body, match_info=None, base=None):
    from personalclaw.knowledge_providers.native import create_native_provider

    sink: list[str] = []
    provider = create_native_provider(store, enqueue=sink.append)
    app = web.Application()
    app["state"] = SimpleNamespace(
        knowledge_store=store,
        knowledge_provider=lambda: provider,
        knowledge_ingest_queue=lambda: SimpleNamespace(
            enqueue=sink.append, enqueue_background=sink.append
        ),
    )
    headers = {"If-Match": f'"{base}"'} if base is not None else {}
    req = make_mocked_request(method, "/", app=app, match_info=match_info or {}, headers=headers)

    async def _json():
        return body

    req.json = _json
    return req


async def _create(store, body, *, as_app=""):
    from personalclaw.dashboard.handlers import knowledge as H

    with scoped_to_app(as_app):
        resp = await H.create_item(_req(store, "POST", body))
    return resp.status, json.loads(resp.body)


def _notes(store) -> list[dict]:
    rows = store.db.execute("SELECT id FROM items ORDER BY created_at").fetchall()
    return [store.get_item(r["id"]) for r in rows]


@pytest.mark.asyncio
async def test_an_apps_note_the_scan_refuses_is_refused_and_nothing_is_made(store):
    status, said = await _create(
        store, {"type": "note", "title": "Saturday", "content": OVERRIDE_LIST}, as_app="growth"
    )

    assert status == 422
    assert said["error"] == {"code": "upload_content_refused", "message": REFUSED}
    assert _notes(store) == []
    assert _scan_rows() == [("uploads.content_scan:knowledge", "rejected")]

    status, made = await _create(
        store, {"type": "note", "title": "Saturday", "content": CLEAN_LIST}, as_app="growth"
    )
    assert status == 201 and made["content"] == CLEAN_LIST


@pytest.mark.asyncio
async def test_an_apps_title_is_read_with_its_text(store):
    status, said = await _create(
        store, {"type": "note", "title": OVERRIDE_LIST, "content": "Errands."}, as_app="growth"
    )

    assert status == 422 and said["error"]["code"] == "upload_content_refused"
    assert _notes(store) == []


@pytest.mark.asyncio
async def test_a_note_she_writes_herself_is_kept_as_she_wrote_it(store):
    """Her own words, as in the chat: the composer never scans what she writes, so neither does
    the note she writes in Knowledge."""
    status, made = await _create(
        store, {"type": "note", "title": "Saturday", "content": OVERRIDE_LIST}
    )

    assert status == 201
    assert made["content"] == OVERRIDE_LIST
    assert _scan_rows() == []


def _base(store, item_id):
    from personalclaw.dashboard.handlers import knowledge as H

    resp = H._content_revision({"content": store.get_item(item_id)["content"]})
    return resp


@pytest.mark.asyncio
async def test_an_apps_edit_the_scan_refuses_changes_nothing(store):
    from personalclaw.dashboard.handlers import knowledge as H

    item_id = store.create_typed_item(item_type="note", title="Errands", content=CLEAN_LIST)

    with scoped_to_app("growth"):
        resp = await H.update_item(
            _req(
                store,
                "PATCH",
                {"content": OVERRIDE_LIST},
                match_info={"id": item_id},
                base=_base(store, item_id),
            )
        )
    assert resp.status == 422
    assert json.loads(resp.body)["error"] == {
        "code": "upload_content_refused",
        "message": "Its text failed the content safety scan, so the item was not changed.",
    }
    assert store.get_item(item_id)["content"] == CLEAN_LIST

    with scoped_to_app("growth"):
        resp = await H.update_item(
            _req(
                store,
                "PATCH",
                {"content": CLEAN_LIST + " And seed potatoes."},
                match_info={"id": item_id},
                base=_base(store, item_id),
            )
        )
    assert resp.status == 200
    assert store.get_item(item_id)["content"] == CLEAN_LIST + " And seed potatoes."


@pytest.mark.asyncio
async def test_her_own_edit_is_kept_as_she_wrote_it(store):
    from personalclaw.dashboard.handlers import knowledge as H

    item_id = store.create_typed_item(item_type="note", title="Errands", content=CLEAN_LIST)
    resp = await H.update_item(
        _req(
            store,
            "PATCH",
            {"content": OVERRIDE_LIST},
            match_info={"id": item_id},
            base=_base(store, item_id),
        )
    )

    assert resp.status == 200
    assert store.get_item(item_id)["content"] == OVERRIDE_LIST


# ── the agent's knowledge tools ──────────────────────────────────────────────


@pytest.fixture
def tools(tmp_path):
    import personalclaw.agents.native.builtin_tools as bt

    with (
        patch.object(K, "_store", None),
        patch("personalclaw.knowledge.knowledge_db_path", lambda: os.path.join(tmp_path, "k.db")),
        patch.object(bt, "_enrich_in_background", lambda item_id: None),
    ):
        yield bt.NativeBuiltinToolProvider()


def _library() -> list[dict]:
    store = K.get_knowledge_store()
    rows = store.db.execute("SELECT id FROM items").fetchall()
    return [store.get_item(r["id"]) for r in rows]


@pytest.mark.asyncio
async def test_a_note_the_agent_saves_is_scanned_before_it_is_kept(tools):
    refused = await tools.invoke(
        "knowledge_create", {"type": "note", "title": "Saturday", "content": OVERRIDE_LIST}
    )

    assert not refused.success and REFUSED in (refused.error or "")
    assert _library() == []
    assert _scan_rows() == [("uploads.content_scan:knowledge", "rejected")]

    made = await tools.invoke(
        "knowledge_create", {"type": "note", "title": "Saturday", "content": CLEAN_LIST}
    )
    assert made.success
    assert [i["content"] for i in _library()] == [CLEAN_LIST]


@pytest.mark.asyncio
async def test_an_edit_the_agent_makes_is_scanned_before_it_is_kept(tools):
    made = await tools.invoke(
        "knowledge_create", {"type": "note", "title": "Saturday", "content": CLEAN_LIST}
    )
    item_id = made.output.rsplit(" ", 1)[-1]

    refused = await tools.invoke("knowledge_update", {"id": item_id, "content": OVERRIDE_LIST})

    assert not refused.success
    assert "failed the content safety scan" in (refused.error or "")
    assert "was not changed" in (refused.error or "")
    assert K.get_knowledge_store().get_item(item_id)["content"] == CLEAN_LIST

    edited = await tools.invoke(
        "knowledge_update", {"id": item_id, "content": CLEAN_LIST + " And seed potatoes."}
    )
    assert edited.success
    assert K.get_knowledge_store().get_item(item_id)["content"].endswith("seed potatoes.")


@pytest.mark.asyncio
async def test_a_decision_the_agent_logs_is_scanned_before_it_is_kept(tools):
    def _decision(summary: str) -> dict:
        return {
            "summary": summary,
            "expectation": "The beds are planted by May.",
            "confidence": 0.7,
            "domain": "other",
        }

    refused = await tools.invoke("log_decision", _decision(OVERRIDE_LIST))

    assert not refused.success and REFUSED in (refused.error or "")
    assert _library() == []

    logged = await tools.invoke("log_decision", _decision("Plant the beds in April"))
    assert logged.success
    assert [i["title"] for i in _library()] == ["Plant the beds in April"]


# ── a workflow's persist step ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_note_a_workflow_persists_is_scanned_before_it_is_written():
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.knowledge_persist_provider import (
        KnowledgePersistActionProvider,
    )

    async def persist(content: str):
        return await KnowledgePersistActionProvider().execute(
            {"kind": "fact", "title": "Market day", "content": content, "unsourced": True},
            ActionContext(event="workflow_node", payload={}),
        )

    refused = await persist(OVERRIDE_LIST)

    assert not refused.success and refused.error == REFUSED
    from personalclaw.knowledge.store import knowledge_db_path

    written = KnowledgeStore(db_path=str(knowledge_db_path()))
    assert written.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0

    kept = await persist(CLEAN_LIST)
    assert kept.success, kept.error
    (content,) = [r[0] for r in written.db.execute("SELECT content FROM items")]
    assert content == CLEAN_LIST
