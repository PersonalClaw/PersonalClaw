"""POST /api/projects/import — the preview says what WOULD happen, the import what DID (F-62).

The Projects page now calls this route (it had no caller), and it shows the route's own
`summary` sentence in the preview dialog before anything is written. That sentence was built for
the finished import only, so a preview answered "2 entities imported" for an archive nothing had
touched — a UI that shows a server-composed sentence shows it as a fact.
"""

from contextlib import asynccontextmanager
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.tasks import registry
from personalclaw.tasks.handlers import register_task_routes
from personalclaw.workflows import project_archive as pa
from personalclaw.workflows.project_export import plan_export


@asynccontextmanager
async def _client(tmp_path):
    registry._providers.clear()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=tmp_path),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path),
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch("personalclaw.workflows.store.config_dir", return_value=tmp_path),
        patch("personalclaw.workflows.leases.config_dir", return_value=tmp_path),
        patch("personalclaw.concurrency.config_dir", return_value=tmp_path),
        patch("personalclaw.loop.files.config_dir", return_value=tmp_path),
    ):
        from personalclaw.dashboard.request_boundary import request_boundary_middleware

        app = web.Application(middlewares=[request_boundary_middleware()])
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


def _archive() -> bytes:
    files = {"project.json": b"{}", "context/overview.md": b"# Overview"}
    plan = plan_export("p-src", project_name="Ingest rework", files=files)
    return pa.write_archive(plan, files)


def _form(data: bytes) -> aiohttp.FormData:
    form = aiohttp.FormData()
    form.add_field("file", data, filename="ingest-rework.zip", content_type="application/zip")
    return form


async def _project_names(client) -> list[str]:
    body = await (await client.get("/api/projects")).json()
    rows = body if isinstance(body, list) else body.get("projects", [])
    return [p["name"] for p in rows]


@pytest.mark.asyncio
async def test_a_preview_says_what_would_be_imported_and_writes_nothing(tmp_path):
    async with _client(tmp_path) as client:
        before = await _project_names(client)
        r = await client.post("/api/projects/import?preview=1", data=_form(_archive()))
        assert r.status == 200, await r.text()
        body = await r.json()
        assert body["preview"] is True
        assert sorted(body["accepted"]) == ["context/overview.md", "project.json"]
        assert "2 entities would be imported" in body["summary"]
        assert "entities imported" not in body["summary"], "a preview must not claim it happened"
        assert await _project_names(client) == before, "a preview writes nothing"


@pytest.mark.asyncio
async def test_the_import_itself_says_what_it_did(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.post("/api/projects/import", data=_form(_archive()))
        assert r.status == 201, await r.text()
        body = await r.json()
        assert body["preview"] is False
        assert body["project_id"]
        assert "2 entities imported" in body["summary"]
        assert sorted(body["written"]) == [
            "context/overview.md",
            "project.json",
        ], "as the summary says"
        assert "Ingest rework" in await _project_names(client)


@pytest.mark.asyncio
async def test_an_imported_project_is_a_new_project_not_the_source_again(tmp_path):
    """Measured before the UI could reach this: exporting a project and importing the archive on
    the same machine left TWO projects with the source's id. The importer created a new project —
    new id, a collision-free "(imported-1)" name — and then wrote the archive's `project.json`
    over that record verbatim, so the new project read back as the source: its id, its name. Every
    action on the "copy" (a rename, a delete) then addressed the original."""
    async with _client(tmp_path) as client:
        r = await client.post(
            "/api/projects",
            json={"name": "Ingest rework", "brief": "Rework the ingest path."},
        )
        source_id = (await r.json())["id"]
        archive = await (await client.get(f"/api/projects/{source_id}/export")).read()

        r = await client.post("/api/projects/import", data=_form(archive))
        assert r.status == 201, await r.text()
        new_id = (await r.json())["project_id"]
        assert new_id != source_id

        projects = (await (await client.get("/api/projects")).json())["projects"]
        ids = [p["id"] for p in projects]
        assert len(ids) == len(set(ids)), f"duplicate project ids after import: {ids}"
        imported = await (await client.get(f"/api/projects/{new_id}")).json()
        assert imported["id"] == new_id
        assert imported["name"] == "Ingest rework (imported-1)"
        assert imported["brief"] == "Rework the ingest path.", "what the record may carry, it does"
        source = await (await client.get(f"/api/projects/{source_id}")).json()
        assert source["name"] == "Ingest rework"


@pytest.mark.asyncio
async def test_an_upload_larger_than_any_importable_archive_is_refused_as_it_streams(
    tmp_path, monkeypatch
):
    """The page can now send any file it is handed, and the route streamed the whole upload to a
    temp file before the planner looked at it — so an upload of any size was written to disk in
    full, only to be refused for the size its contents declared. Every importable archive is
    bounded (`MAX_TOTAL_EXTRACTED` of contents), so an upload past `MAX_ARCHIVE_BYTES` is refused
    while it streams, and what was written of it is removed."""
    import tempfile

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(uploads))
    monkeypatch.setattr(pa, "MAX_ARCHIVE_BYTES", 64 * 1024, raising=False)
    async with _client(tmp_path) as client:
        before = await _project_names(client)
        r = await client.post("/api/projects/import?preview=1", data=_form(b"\0" * (256 * 1024)))
        assert r.status == 413, await r.text()
        body = await r.json()
        assert body["error"]["code"] == "request_too_large"
        assert "64 KiB" in body["error"]["message"]
        assert await _project_names(client) == before
    assert list(uploads.iterdir()) == [], "the partial upload is removed"
