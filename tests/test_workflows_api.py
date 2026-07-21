"""/api/workflows HTTP CRUD endpoints."""

from __future__ import annotations

import tempfile

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.workflows import registry
from personalclaw.workflows.handlers import register_workflow_routes
from personalclaw.workflows.native import NativeWorkflowProvider


@pytest.fixture
def temp_native():
    """Swap the registry's native provider for a temp-dir one (isolation)."""
    saved = dict(registry._providers)
    registry._providers.clear()
    registry.register_provider(NativeWorkflowProvider(storage_dir=tempfile.mkdtemp()))
    yield
    registry._providers.clear()
    registry._providers.update(saved)


async def _client() -> TestClient:
    app = web.Application()
    register_workflow_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_crud_roundtrip(temp_native):
    client = await _client()
    try:
        # create
        resp = await client.post(
            "/api/workflows",
            json={
                "name": "git-commit",
                "description": "flow",
                "scope": "workspace",
                "scope_ref": "/repo/a",
                "match_text": "commit changes",
                "steps": [{"title": "Test"}, {"title": "Commit"}],
            },
        )
        assert resp.status == 201
        created = await resp.json()
        wid = created["id"]
        assert created["name"] == "git-commit"
        assert created["scope"] == "workspace"
        assert "match_embedding" not in created  # vector omitted from responses

        # get
        resp = await client.get(f"/api/workflows/{wid}")
        assert resp.status == 200
        assert (await resp.json())["description"] == "flow"

        # list + scope filter
        resp = await client.get("/api/workflows")
        body = await resp.json()
        assert body["total"] == 1
        resp = await client.get("/api/workflows?scope=workspace&scope_ref=/repo/a")
        assert (await resp.json())["total"] == 1
        resp = await client.get("/api/workflows?scope=workspace&scope_ref=/repo/b")
        assert (await resp.json())["total"] == 0

        # update
        resp = await client.put(f"/api/workflows/{wid}", json={"description": "updated"})
        assert resp.status == 200
        assert (await resp.json())["description"] == "updated"

        # delete
        resp = await client.delete(f"/api/workflows/{wid}")
        assert resp.status == 200
        resp = await client.get(f"/api/workflows/{wid}")
        assert resp.status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_create_requires_name(temp_native):
    client = await _client()
    try:
        resp = await client.post("/api/workflows", json={"description": "no name"})
        assert resp.status == 400
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_providers_endpoint(temp_native):
    client = await _client()
    try:
        resp = await client.get("/api/workflows/providers")
        assert resp.status == 200
        assert "native" in (await resp.json())["providers"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_unknown_404(temp_native):
    client = await _client()
    try:
        resp = await client.get("/api/workflows/wf-nope")
        assert resp.status == 404
    finally:
        await client.close()
