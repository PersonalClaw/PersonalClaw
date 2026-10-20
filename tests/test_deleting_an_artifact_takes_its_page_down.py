"""Deleting an artifact takes its deployed page down, whichever path deletes it.

A deployment is a row in ``<home>/artifacts/deployments.json`` keyed by slug, and slugs are
reused: an artifact saved later under the same name takes the same slug. So a deployment left
behind by a delete is not merely stale — a later artifact with that slug would be served at the old
URL without anyone deploying it. Only the REST delete used to tear down; the agent's
``artifact_delete`` (in the gateway, and in the separate ``mcp-core`` process an agent CLI runs)
left the row.

The store's own ``delete`` now takes the page down first, through the same function the REST
undeploy uses (``ArtifactDeployStore.teardown``), so every caller of the store gets it. And a new
artifact never starts out published: a row its slug inherited from a deletion that went around the
store is taken down when the slug is taken again.
"""

from __future__ import annotations

import shutil
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.artifacts import registry
from personalclaw.artifacts.deploy import ArtifactDeployStore
from personalclaw.artifacts.handlers import register_artifact_routes
from personalclaw.artifacts.native import NativeArtifactProvider

PAGE = "<h1>Weekly plan</h1><p>Monday: groceries.</p>"
LATER_PAGE = "<h1>Weekly plan</h1><p>Saved later, never deployed.</p>"


@pytest.fixture
def provider(tmp_path) -> NativeArtifactProvider:
    return NativeArtifactProvider(root=tmp_path / "artifacts")


@pytest.fixture
def native(provider):
    with (
        patch.object(registry, "get_provider", return_value=provider),
        patch("personalclaw.mcp_artifacts._resolve_session_key", return_value="dashboard:chat-1"),
        # An unscoped chat: no Project to ask the gateway about.
        patch("personalclaw.mcp_artifacts._current_project_id", return_value=""),
    ):
        yield provider


@pytest.fixture
def agent_tool():
    from personalclaw.mcp_artifacts import _call_tool

    return _call_tool


async def _client() -> TestClient:
    app = web.Application()
    state = MagicMock()
    state._sessions = {}
    app["state"] = state
    register_artifact_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _deploy(client: TestClient, slug: str) -> str:
    resp = await client.post(f"/api/artifacts/{slug}/deploy")
    assert resp.status == 200, await resp.text()
    return (await resp.json())["deployment"]["url"]


async def _listed(client: TestClient) -> list[str]:
    rows = (await (await client.get("/api/artifacts/deployed")).json())["deployments"]
    return [r["slug"] for r in rows]


@pytest.mark.asyncio
async def test_an_agent_delete_takes_the_page_down_and_a_later_artifact_is_not_served_there(
    native, agent_tool
) -> None:
    saved = agent_tool("artifact_save", {"name": "Weekly plan", "content": PAGE, "kind": "html"})
    assert "Saved artifact" in saved, saved
    slug = "weekly-plan"
    client = await _client()
    try:
        url = await _deploy(client, slug)
        assert "groceries" in await (await client.get(url)).text()

        out = agent_tool("artifact_delete", {"slug": slug})
        assert out == f"Deleted artifact: {slug}", out
        assert (await client.get(url)).status == 404
        assert await _listed(client) == []

        # The agent saves an artifact of the same name later: it takes the same slug, and nobody
        # deployed it, so nothing serves it — not at the old URL, not anywhere.
        again = agent_tool(
            "artifact_save", {"name": "Weekly plan", "content": LATER_PAGE, "kind": "html"}
        )
        assert f"(slug: {slug}," in again, again
        resp = await client.get(url)
        assert resp.status == 404
        assert "never deployed" not in await resp.text()
        assert await _listed(client) == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_delete_from_another_process_takes_the_page_down(native, tmp_path) -> None:
    """An agent CLI's tools run in ``personalclaw mcp-core``, a second process that writes the
    store directly; no listener in the gateway hears its delete. The page still comes down, because
    the delete records the teardown itself, on disk, where the serve route reads it."""
    gateway_side = native
    art = gateway_side.create(name="Weekly plan", content=PAGE, kind="html")
    client = await _client()
    try:
        url = await _deploy(client, art.slug)
        other_process = NativeArtifactProvider(root=gateway_side.root)
        assert other_process.delete(art.slug) is True
        assert (await client.get(url)).status == 404
        assert await _listed(client) == []
        gateway_side.create(name="Weekly plan", content=LATER_PAGE, kind="html")
        assert (await client.get(url)).status == 404
        assert await _listed(client) == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_every_delete_path_tears_down_through_the_rest_undeploy_function(
    native, agent_tool
) -> None:
    """REST delete, the agent's delete and the REST undeploy all reach one function."""
    calls: list[str] = []
    real = ArtifactDeployStore.teardown

    def spy(self, slug):
        calls.append(slug)
        return real(self, slug)

    client = await _client()
    try:
        with patch.object(ArtifactDeployStore, "teardown", spy):
            for name in ("Undeployed by hand", "Deleted over REST", "Deleted by the agent"):
                native.create(name=name, content=PAGE, kind="html")
            for slug in ("undeployed-by-hand", "deleted-over-rest", "deleted-by-the-agent"):
                await _deploy(client, slug)
            calls.clear()  # creating an artifact checks its slug starts unpublished
            assert (await client.delete("/api/artifacts/undeployed-by-hand/deploy")).status == 200
            assert (await client.delete("/api/artifacts/deleted-over-rest")).status == 200
            assert agent_tool("artifact_delete", {"slug": "deleted-by-the-agent"}).startswith(
                "Deleted artifact"
            )
        assert calls == ["undeployed-by-hand", "deleted-over-rest", "deleted-by-the-agent"]
        assert await _listed(client) == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_page_left_up_by_a_deletion_around_the_store_comes_down_when_its_slug_is_reused(
    native,
) -> None:
    """A folder removed by hand (or by any program) leaves its deployment row. The next artifact
    to take that slug does not inherit it."""
    art = native.create(name="Weekly plan", content=PAGE, kind="html")
    client = await _client()
    try:
        url = await _deploy(client, art.slug)
        shutil.rmtree(native.root / art.slug)
        later = native.create(name="Weekly plan", content=LATER_PAGE, kind="html")
        assert later.slug == art.slug
        resp = await client.get(url)
        assert resp.status == 404
        assert "never deployed" not in await resp.text()
        assert await _listed(client) == []
        # Deployed on purpose, it is served — at a URL of its own.
        fresh = await _deploy(client, later.slug)
        assert fresh != url
        assert "never deployed" in await (await client.get(fresh)).text()
    finally:
        await client.close()


def test_a_delete_that_cannot_take_the_page_down_keeps_the_artifact(native) -> None:
    """Refused rather than half-done: an artifact still there and still published can be retried;
    an artifact gone and still published is the state this prevents."""
    art = native.create(name="Weekly plan", content=PAGE, kind="html")
    store = ArtifactDeployStore(native.root)
    store.deploy(art.slug)
    with patch.object(ArtifactDeployStore, "teardown", side_effect=OSError("disk full")):
        assert native.delete(art.slug) is False
    assert native.get(art.slug) is not None
    assert store.is_deployed(art.slug)
