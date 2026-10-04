"""A content page's save never overwrites a change made elsewhere.

Five content surfaces save a WHOLE document built from the copy the page read: the file editor
(``POST /api/file-write``, and "Save as artifact", whose body is the same draft and which writes
it through to the file), an artifact's body (``PATCH /api/artifacts/{slug}``), a knowledge item's
body (``PATCH /api/knowledge/items/{id}``), an intent (``POST /api/knowledge/intents`` with an
id) and a watched source's spec + budget (``PATCH /api/knowledge/sources/{id}``). Each wrote the
page's copy over whatever was stored — another tab's save, or the gateway's own: the agent's
``edit_file``/``write_file``/``artifact_update``, a rename's relink. The contract
(`personalclaw/stale_write.py`): the read carries the revision, the write names it in
``If-Match``, and a stale one is ``409 stale_write`` with nothing written. Tag edits are per-name
operations instead, applied to what is stored when they land.
"""

from __future__ import annotations

import json
import os
import re as _re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

pytestmark = pytest.mark.asyncio

SECRET = "AKIAIOSFODNN7EXAMPLE"


def revision_of(document):
    """Imported per call, so this file collects on a tree that predates the module and each test
    reports its own verdict there."""
    from personalclaw.stale_write import revision_of as _revision_of

    return _revision_of(document)


def _based_on(revision: str | None) -> dict[str, str]:
    return {"If-Match": f'"{revision}"'} if revision is not None else {}


def _code(body: dict) -> str:
    return body["error"]["code"] if isinstance(body.get("error"), dict) else ""


# ── the file editor and "Save as artifact" ──────────────────────────────────────────


@pytest.fixture
def explorer(tmp_path):
    """The dashboard's file roots are exactly *tmp_path* (the file I/O suite's own seam), for the
    explorer and for the places a file-backed artifact may point (`artifacts/source_files`)."""
    real_realpath = os.path.realpath
    roots = [("Test", str(tmp_path))]
    with (
        patch("os.path.expanduser", side_effect=lambda p: p.replace("~", str(tmp_path))),
        patch("os.path.realpath", side_effect=real_realpath),
        patch("pathlib.Path.home", return_value=tmp_path),
        patch("personalclaw.dashboard.handlers.files._dashboard_roots", return_value=roots),
        patch("personalclaw.file_roots.dashboard_roots", return_value=roots),
    ):
        yield tmp_path


@pytest.fixture
def artifacts(tmp_path):
    from personalclaw.artifacts import registry
    from personalclaw.artifacts.native import NativeArtifactProvider

    provider = NativeArtifactProvider(root=tmp_path / "artifact-store")
    with patch.object(registry, "get_provider", return_value=provider):
        yield provider


def _app() -> web.Application:
    from personalclaw.artifacts.handlers import register_artifact_routes
    from personalclaw.dashboard.handlers import api_file_read, api_file_watch, api_file_write

    app = web.Application()
    state = MagicMock()
    state._sessions = {}
    app["state"] = state
    app.router.add_get("/api/file-read", api_file_read)
    app.router.add_post("/api/file-write", api_file_write)
    app.router.add_get("/api/file-watch", api_file_watch)
    register_artifact_routes(app)
    return app


async def _read_file(c: TestClient, path) -> tuple[str, str | None]:
    """What the editor paints: the text, and the revision the same read reported (its ETag)."""
    resp = await c.get("/api/file-read", params={"path": str(path)})
    assert resp.status == 200, await resp.text()
    etag = resp.headers.get("ETag")
    return await resp.text(), (etag.strip('"') if etag else None)


async def _write_file(c: TestClient, path, content: str, base: str | None):
    return await c.post(
        "/api/file-write", json={"path": str(path), "content": content}, headers=_based_on(base)
    )


async def _save_as_artifact(c: TestClient, path, content: str, base: str | None):
    return await c.post(
        "/api/artifacts",
        json={
            "name": "Brief",
            "content": content,
            "source": "manual",
            "source_path": str(path),
            "kind": "markdown",
        },
        headers=_based_on(base),
    )


def _agent_tools(root):
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    return NativeBuiltinToolProvider(cwd=root)


class TestTheFileEditor:
    async def test_the_second_save_from_the_same_copy_is_refused(self, explorer) -> None:
        f = explorer / "notes.md"
        f.write_text("one\ntwo\n", encoding="utf-8")
        async with TestClient(TestServer(_app())) as c:
            text, base = await _read_file(c, f)  # both tabs paint this
            first = await _write_file(c, f, text + "tab a\n", base)
            assert first.status == 200, await first.text()
            second = await _write_file(c, f, text + "tab b\n", base)
            assert second.status == 409
            assert _code(await second.json()) == "stale_write"
        assert f.read_text(encoding="utf-8") == "one\ntwo\ntab a\n"

    async def test_a_save_that_names_no_copy_is_refused(self, explorer) -> None:
        f = explorer / "notes.md"
        f.write_text("one\n", encoding="utf-8")
        async with TestClient(TestServer(_app())) as c:
            resp = await _write_file(c, f, "overwritten\n", None)
            assert resp.status == 428
            assert _code(await resp.json()) == "revision_required"
        assert f.read_text(encoding="utf-8") == "one\n"

    async def test_the_agents_edit_between_read_and_save_is_not_undone(self, explorer) -> None:
        f = explorer / "notes.md"
        f.write_text("one\ntwo\n", encoding="utf-8")
        async with TestClient(TestServer(_app())) as c:
            text, base = await _read_file(c, f)
            # The agent's own tool, the way a turn runs it, while the page is open.
            done = await _agent_tools(explorer)._t_edit_file(
                {"path": "notes.md", "old_str": "two", "new_str": "TWO"}
            )
            assert done.success, done.error
            resp = await _write_file(c, f, text + "mine\n", base)
            assert resp.status == 409
            assert _code(await resp.json()) == "stale_write"
        assert f.read_text(encoding="utf-8") == "one\nTWO\n"

    async def test_the_save_answers_with_the_revision_the_file_now_reads_at(self, explorer) -> None:
        f = explorer / "notes.md"
        f.write_text("one\n", encoding="utf-8")
        async with TestClient(TestServer(_app())) as c:
            text, base = await _read_file(c, f)
            saved = await (await _write_file(c, f, text + "two\n", base)).json()
            _, reread = await _read_file(c, f)
            assert saved["revision"] == reread
            # A page that stays open saves again from the answer, not from its first read.
            again = await _write_file(c, f, "one\ntwo\nthree\n", saved["revision"])
            assert again.status == 200
        assert f.read_text(encoding="utf-8") == "one\ntwo\nthree\n"

    async def test_the_revision_is_of_the_redacted_text_the_read_served(self, explorer) -> None:
        f = explorer / "env.txt"
        f.write_text(f"aws_key = {SECRET}\n", encoding="utf-8")
        async with TestClient(TestServer(_app())) as c:
            text, base = await _read_file(c, f)
        assert SECRET not in text
        assert base == revision_of(text)
        # Positive control: the raw bytes would have a different revision.
        assert base != revision_of(f.read_text(encoding="utf-8"))

    async def test_a_truncated_read_names_no_revision(self, explorer) -> None:
        f = explorer / "big.log"
        f.write_bytes(b"x" * 512_010)
        async with TestClient(TestServer(_app())) as c:
            resp = await c.get("/api/file-read", params={"path": str(f)})
            assert resp.status == 200
            assert resp.headers.get("X-Truncated") == "true"
            assert resp.headers.get("ETag") is None

    async def test_a_watch_frame_names_the_revision_the_read_reports(self, explorer) -> None:
        # CRLF on purpose: the watch used to read in text mode, which rewrote every \r\n to \n,
        # so the page got one text from the read and another from the watch.
        f = explorer / "notes.md"
        f.write_bytes(b"one\r\ntwo\r\n")
        async with TestClient(TestServer(_app())) as c:
            text, base = await _read_file(c, f)
            resp = await c.get("/api/file-watch", params={"path": str(f)})
            buf = b""
            async for chunk in resp.content.iter_any():
                buf += chunk
                if b"\n\n" in buf:
                    break
            resp.close()
        line = next(ln for ln in buf.decode().split("\n") if ln.startswith("data: "))
        frame = json.loads(line[6:])
        assert frame["content"] == text == "one\r\ntwo\r\n"
        assert frame["revision"] == base


class TestSaveAsArtifact:
    async def test_the_second_save_from_the_same_copy_is_refused(self, explorer, artifacts) -> None:
        f = explorer / "brief.md"
        f.write_text("draft\n", encoding="utf-8")
        async with TestClient(TestServer(_app())) as c:
            text, base = await _read_file(c, f)
            first = await _save_as_artifact(c, f, text + "a\n", base)
            assert first.status == 201, await first.text()
            # The second save lands on the artifact the first created (source_path dedup) and
            # would write its body through to the file the first save already rewrote.
            second = await _save_as_artifact(c, f, text + "b\n", base)
            assert second.status == 409
            assert _code(await second.json()) == "stale_write"
        assert f.read_text(encoding="utf-8") == "draft\na\n"

    async def test_a_save_that_names_no_copy_is_refused(self, explorer, artifacts) -> None:
        f = explorer / "brief.md"
        f.write_text("draft\n", encoding="utf-8")
        async with TestClient(TestServer(_app())) as c:
            resp = await _save_as_artifact(c, f, "overwritten\n", None)
            assert resp.status == 428
            assert _code(await resp.json()) == "revision_required"
        assert f.read_text(encoding="utf-8") == "draft\n"
        assert artifacts.list() == []

    async def test_the_agents_write_between_read_and_save_is_not_undone(
        self, explorer, artifacts
    ) -> None:
        f = explorer / "brief.md"
        f.write_text("draft\n", encoding="utf-8")
        async with TestClient(TestServer(_app())) as c:
            text, base = await _read_file(c, f)
            done = await _agent_tools(explorer)._t_write_file(
                {"path": "brief.md", "content": "the agent's rewrite\n"}
            )
            assert done.success, done.error
            resp = await _save_as_artifact(c, f, text + "mine\n", base)
            assert resp.status == 409
        assert f.read_text(encoding="utf-8") == "the agent's rewrite\n"
        assert artifacts.list() == []


# ── an artifact's body and tags ─────────────────────────────────────────────────────


async def _read_artifact(c: TestClient, slug: str) -> tuple[str, str | None]:
    body = await (await c.get(f"/api/artifacts/{slug}")).json()
    return body["content"], body.get("content_revision")


async def _save_body(c: TestClient, slug: str, content: str, base: str | None):
    return await c.patch(
        f"/api/artifacts/{slug}",
        json={"content": content, "snapshot": False, "event_type": "edited"},
        headers=_based_on(base),
    )


class TestAnArtifactsBody:
    async def test_the_second_save_from_the_same_copy_is_refused(self, artifacts) -> None:
        artifacts.create(name="Doc", content="v1", kind="markdown")
        async with TestClient(TestServer(_app())) as c:
            body, base = await _read_artifact(c, "doc")
            first = await _save_body(c, "doc", body + " tab a", base)
            assert first.status == 200, await first.text()
            second = await _save_body(c, "doc", body + " tab b", base)
            assert second.status == 409
            assert _code(await second.json()) == "stale_write"
        assert artifacts.get("doc").content == "v1 tab a"

    async def test_a_save_that_names_no_copy_is_refused(self, artifacts) -> None:
        artifacts.create(name="Doc", content="v1", kind="markdown")
        async with TestClient(TestServer(_app())) as c:
            resp = await _save_body(c, "doc", "overwritten", None)
            assert resp.status == 428
            assert _code(await resp.json()) == "revision_required"
        assert artifacts.get("doc").content == "v1"

    async def test_the_agents_update_between_read_and_save_is_not_undone(self, artifacts) -> None:
        from personalclaw.mcp_artifacts import _call_tool

        artifacts.create(name="Doc", content="v1", kind="markdown")
        async with TestClient(TestServer(_app())) as c:
            body, base = await _read_artifact(c, "doc")
            # The agent's `artifact_update`, through the MCP tool path a turn uses, over the
            # version its own read named.
            with patch("personalclaw.mcp_artifacts._resolve_session_key", return_value="d:c-1"):
                read = _call_tool("artifact_get", {"slug": "doc"})
                agent_base = _re.search(r"Base: (v\d+-[0-9a-f]{16})", read).group(1)
                out = _call_tool(
                    "artifact_update", {"slug": "doc", "content": "agent v2", "base": agent_base}
                )
            assert "version 2" in out, out
            resp = await _save_body(c, "doc", body + " mine", base)
            assert resp.status == 409
        assert artifacts.get("doc").content == "agent v2"

    async def test_a_write_to_the_file_it_points_at_makes_a_copy_stale(
        self, explorer, artifacts
    ) -> None:
        f = explorer / "live.md"
        f.write_text("from disk\n", encoding="utf-8")
        artifacts.create(name="Live", content="from disk\n", kind="markdown", source_path=str(f))
        async with TestClient(TestServer(_app())) as c:
            body, base = await _read_artifact(c, "live")
            done = await _agent_tools(explorer)._t_write_file(
                {"path": "live.md", "content": "the agent's rewrite\n"}
            )
            assert done.success, done.error
            resp = await _save_body(c, "live", body + "mine\n", base)
            assert resp.status == 409
        assert f.read_text(encoding="utf-8") == "the agent's rewrite\n"

    async def test_the_save_answers_with_the_new_revision(self, artifacts) -> None:
        artifacts.create(name="Doc", content="v1", kind="markdown")
        async with TestClient(TestServer(_app())) as c:
            body, base = await _read_artifact(c, "doc")
            saved = await (await _save_body(c, "doc", "v2", base)).json()
            assert saved["content_revision"] == revision_of("v2")
            again = await _save_body(c, "doc", "v3", saved["content_revision"])
            assert again.status == 200
        assert artifacts.get("doc").content == "v3"

    async def test_the_revision_is_of_the_redacted_body(self, artifacts) -> None:
        artifacts.create(name="Doc", content=f"key {SECRET}", kind="markdown")
        async with TestClient(TestServer(_app())) as c:
            body, base = await _read_artifact(c, "doc")
        assert SECRET not in body
        assert base == revision_of(body)


class TestAnArtifactsTags:
    async def test_two_tabs_adding_different_tags_keep_both(self, artifacts) -> None:
        artifacts.create(name="Doc", content="x", tags=["base"])
        async with TestClient(TestServer(_app())) as c:
            for tag in ("alpha", "beta"):  # each tab painted ["base"]
                resp = await c.patch("/api/artifacts/doc", json={"add_tags": [tag]})
                assert resp.status == 200, await resp.text()
        assert artifacts.get("doc").tags == ["base", "alpha", "beta"]

    async def test_a_remove_from_a_stale_tab_removes_only_that_tag(self, artifacts) -> None:
        artifacts.create(name="Doc", content="x", tags=["a", "b"])
        artifacts.update("doc", tags=["a", "b", "c"], actor="agent")  # added since the read
        async with TestClient(TestServer(_app())) as c:
            resp = await c.patch("/api/artifacts/doc", json={"remove_tags": ["a"]})
            assert resp.status == 200
        assert artifacts.get("doc").tags == ["b", "c"]

    async def test_the_whole_list_form_is_refused(self, artifacts) -> None:
        artifacts.create(name="Doc", content="x", tags=["keep"])
        async with TestClient(TestServer(_app())) as c:
            resp = await c.patch("/api/artifacts/doc", json={"tags": ["only-mine"]})
            assert resp.status == 400
            assert "add_tags" in (await resp.json())["error"]
        assert artifacts.get("doc").tags == ["keep"]


# ── knowledge: an item's body and tags, an intent, a watched source ─────────────────


@pytest.fixture
def kstore(tmp_path):
    from personalclaw.knowledge.store import KnowledgeStore

    return KnowledgeStore(str(tmp_path / "knowledge.db"))


async def _k(handler, store, method, path, *, body=None, match=None, base=None):
    """Drive a knowledge handler the way its own suites do: a mocked request on a real store."""
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    req = make_mocked_request(
        method, path, app=app, match_info=match or {}, headers=_based_on(base)
    )

    async def _json():
        return body or {}

    req.json = _json  # type: ignore[method-assign]
    resp = await handler(req)
    return resp, json.loads(resp.body)


async def _read_item(store, iid: str) -> dict:
    from personalclaw.dashboard.handlers import knowledge as H

    resp, item = await _k(
        H.get_item, store, "GET", f"/api/knowledge/items/{iid}", match={"id": iid}
    )
    assert resp.status == 200
    return item


async def _patch_item(store, iid: str, body: dict, base: str | None = None):
    from personalclaw.dashboard.handlers import knowledge as H

    return await _k(
        H.update_item,
        store,
        "PATCH",
        f"/api/knowledge/items/{iid}",
        body={"reingest": False, **body},
        match={"id": iid},
        base=base,
    )


class TestAKnowledgeItemsBody:
    async def test_the_second_save_from_the_same_copy_is_refused(self, kstore) -> None:
        iid = kstore.create_typed_item(item_type="note", title="N", content="alpha")
        base = (await _read_item(kstore, iid)).get("content_revision")
        first, _ = await _patch_item(kstore, iid, {"content": "alpha tab a"}, base)
        assert first.status == 200
        second, body = await _patch_item(kstore, iid, {"content": "alpha tab b"}, base)
        assert second.status == 409
        assert _code(body) == "stale_write"
        assert kstore.get_item(iid)["content"] == "alpha tab a"

    async def test_a_save_that_names_no_copy_is_refused(self, kstore) -> None:
        iid = kstore.create_typed_item(item_type="note", title="N", content="alpha")
        resp, body = await _patch_item(kstore, iid, {"content": "overwritten"})
        assert resp.status == 428
        assert _code(body) == "revision_required"
        assert kstore.get_item(iid)["content"] == "alpha"

    async def test_a_renames_relink_between_read_and_save_is_not_undone(self, kstore) -> None:
        iid = kstore.create_typed_item(item_type="note", title="N", content="see [[Old Name]]")
        base = (await _read_item(kstore, iid)).get("content_revision")
        # Retitling another note rewrites the links to it inside THIS item's body.
        assert kstore.rewrite_wikilinks("Old Name", "New Name")["items"] == 1
        resp, body = await _patch_item(kstore, iid, {"content": "see [[Old Name]] — mine"}, base)
        assert resp.status == 409
        assert kstore.get_item(iid)["content"] == "see [[New Name]]"

    async def test_the_pipelines_own_columns_do_not_make_a_copy_stale(self, kstore) -> None:
        iid = kstore.create_typed_item(item_type="note", title="N", content="alpha")
        base = (await _read_item(kstore, iid)).get("content_revision")
        kstore.update_item(
            iid,
            touch=False,
            processing_status="done",
            insights={"topics": ["x"]},
            summary="an AI summary",
            ai_title="AI title",
        )
        resp, _ = await _patch_item(kstore, iid, {"content": "alpha, edited"}, base)
        assert resp.status == 200
        assert kstore.get_item(iid)["content"] == "alpha, edited"

    async def test_a_scalar_edit_needs_no_revision(self, kstore) -> None:
        iid = kstore.create_typed_item(item_type="note", title="N", content="alpha")
        resp, _ = await _patch_item(kstore, iid, {"title": "Renamed"})
        assert resp.status == 200
        assert kstore.get_item(iid)["title"] == "Renamed"

    async def test_the_save_answers_with_the_new_revision(self, kstore) -> None:
        iid = kstore.create_typed_item(item_type="note", title="N", content="alpha")
        base = (await _read_item(kstore, iid)).get("content_revision")
        _, saved = await _patch_item(kstore, iid, {"content": "beta"}, base)
        assert saved["content_revision"] == revision_of("beta")
        again, _ = await _patch_item(kstore, iid, {"content": "gamma"}, saved["content_revision"])
        assert again.status == 200


class TestAKnowledgeItemsTags:
    async def test_two_tabs_adding_different_tags_keep_both(self, kstore) -> None:
        iid = kstore.create_typed_item(item_type="note", title="N", content="x", tags=["base"])
        for tag in ("alpha", "beta"):  # each tab painted ["base"]
            resp, _ = await _patch_item(kstore, iid, {"add_tags": [tag]})
            assert resp.status == 200
        assert sorted(kstore.get_item(iid)["tags"]) == ["alpha", "base", "beta"]

    async def test_a_remove_from_a_stale_tab_removes_only_that_tag(self, kstore) -> None:
        iid = kstore.create_typed_item(item_type="note", title="N", content="x", tags=["a", "b"])
        kstore.update_item(iid, tags=["a", "b", "c"])  # added since the read
        resp, _ = await _patch_item(kstore, iid, {"remove_tags": ["a"]})
        assert resp.status == 200
        assert sorted(kstore.get_item(iid)["tags"]) == ["b", "c"]

    async def test_the_whole_list_form_is_refused(self, kstore) -> None:
        iid = kstore.create_typed_item(item_type="note", title="N", content="x", tags=["keep"])
        resp, body = await _patch_item(kstore, iid, {"tags": ["only-mine"]})
        assert resp.status == 400
        assert "add_tags" in body["error"]
        assert kstore.get_item(iid)["tags"] == ["keep"]


def _record(intent: dict) -> dict:
    return {k: intent[k] for k in ("id", "goal", "enabled", "enabled_for", "propose_skill")}


async def _upsert(store, body: dict, base: str | None = None):
    from personalclaw.dashboard.handlers import knowledge as H

    return await _k(H.upsert_intent, store, "POST", "/api/knowledge/intents", body=body, base=base)


async def _created_intent(store, goal: str) -> dict:
    resp, body = await _upsert(store, {"goal": goal})
    assert resp.status == 201
    return next(i for i in body["intents"] if i["id"] == body["id"])


def _stored_intents(store) -> dict:
    from personalclaw.knowledge.intents import IntentStore

    return {i.id: i for i in IntentStore(Path(store.db_path).parent / "intents.json").load()}


class TestAnIntentEdit:
    async def test_the_second_edit_from_the_same_copy_is_refused(self, kstore) -> None:
        intent = await _created_intent(kstore, "track drive health")
        base = intent.get("revision")
        first, _ = await _upsert(kstore, {**_record(intent), "enabled": False}, base)
        assert first.status == 201
        second, body = await _upsert(kstore, {**_record(intent), "propose_skill": True}, base)
        assert second.status == 409
        assert _code(body) == "stale_write"
        stored = _stored_intents(kstore)[intent["id"]]
        assert (stored.enabled, stored.propose_skill) == (False, False)

    async def test_an_edit_that_names_no_copy_is_refused(self, kstore) -> None:
        intent = await _created_intent(kstore, "track drive health")
        resp, body = await _upsert(kstore, {**_record(intent), "enabled": False})
        assert resp.status == 428
        assert _code(body) == "revision_required"
        assert _stored_intents(kstore)[intent["id"]].enabled is True

    async def test_an_edit_of_an_intent_deleted_since_does_not_resurrect_it(self, kstore) -> None:
        from personalclaw.knowledge.intents import IntentStore

        intent = await _created_intent(kstore, "track drive health")
        IntentStore(Path(kstore.db_path).parent / "intents.json").delete(intent["id"])
        resp, _ = await _upsert(kstore, {**_record(intent), "enabled": False}, intent["revision"])
        assert resp.status == 409
        assert intent["id"] not in _stored_intents(kstore)

    async def test_the_revision_is_of_the_record_not_the_outcome_count(self, kstore) -> None:
        intent = await _created_intent(kstore, "track drive health")
        assert intent["revision"] == revision_of(_record(intent))


async def _sources(store) -> list[dict]:
    from personalclaw.dashboard.handlers import knowledge as H

    resp, body = await _k(H.list_watched_sources, store, "GET", "/api/knowledge/sources")
    assert resp.status == 200
    return body["sources"]


async def _patch_source(store, sid: str, body: dict, base: str | None = None):
    from personalclaw.dashboard.handlers import knowledge as H

    return await _k(
        H.update_watched_source,
        store,
        "PATCH",
        f"/api/knowledge/sources/{sid}",
        body=body,
        match={"id": sid},
        base=base,
    )


def _source(store) -> str:
    return store.create_source(
        name="Changelog",
        provider="watched-page",
        kind="web_page",
        spec={"url": "https://app.example.com/changelog"},
        budget={"max_requests": 4},
    )


class TestAWatchedSourcesSettings:
    async def test_the_second_save_from_the_same_copy_is_refused(self, kstore) -> None:
        sid = _source(kstore)
        (src,) = await _sources(kstore)
        base = src.get("revision")
        first, _ = await _patch_source(
            kstore, sid, {"budget": {**src["budget"], "allow_render": True}}, base
        )
        assert first.status == 200
        second, body = await _patch_source(
            kstore, sid, {"budget": {**src["budget"], "max_requests": 9}}, base
        )
        assert second.status == 409
        assert _code(body) == "stale_write"
        assert kstore.get_source(sid)["budget"] == {"max_requests": 4, "allow_render": True}

    async def test_a_save_that_names_no_copy_is_refused(self, kstore) -> None:
        sid = _source(kstore)
        resp, body = await _patch_source(kstore, sid, {"budget": {"allow_render": True}})
        assert resp.status == 428
        assert _code(body) == "revision_required"
        assert kstore.get_source(sid)["budget"] == {"max_requests": 4}

    async def test_a_poll_between_read_and_save_does_not_make_a_copy_stale(self, kstore) -> None:
        sid = _source(kstore)
        (src,) = await _sources(kstore)
        kstore.record_poll(sid, cursor="c1", new_count=3, health_status="ok")
        resp, body = await _patch_source(
            kstore, sid, {"budget": {**src["budget"], "allow_render": True}}, src["revision"]
        )
        assert resp.status == 200
        assert body["source"]["revision"] == revision_of(
            {"spec": src["spec"], "budget": {**src["budget"], "allow_render": True}}
        )

    async def test_a_scalar_edit_needs_no_revision(self, kstore) -> None:
        sid = _source(kstore)
        resp, _ = await _patch_source(kstore, sid, {"enabled": False})
        assert resp.status == 200
        assert kstore.get_source(sid)["enabled"] is False
