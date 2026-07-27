"""ARTIFACTS S1 — collection field + server-backed dedup hint.

Covers: the collection field round-trips (model + provider create/update/list-filter +
tolerant read of a pre-collection meta.json), find_similar by-slug, and the REST
create 409 similar_artifact_exists (+ ?force=1 bypass)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.artifacts import registry
from personalclaw.artifacts.handlers import register_artifact_routes
from personalclaw.artifacts.models import Artifact
from personalclaw.artifacts.native import NativeArtifactProvider


@pytest.fixture
def provider(tmp_path) -> NativeArtifactProvider:
    return NativeArtifactProvider(root=tmp_path / "artifacts")


class TestCollectionModel:
    def test_roundtrips_through_to_dict_from_dict(self):
        a = Artifact(slug="s", name="N", collection="Dashboards")
        assert a.to_dict(persist=True)["collection"] == "Dashboards"
        assert Artifact.from_dict(a.to_dict(persist=True)).collection == "Dashboards"

    def test_tolerant_read_of_pre_collection_meta(self):
        # A meta.json written before the field exists → loads with "".
        old = {"slug": "s", "name": "N", "kind": "widget"}
        assert Artifact.from_dict(old).collection == ""


class TestCollectionProvider:
    def test_create_persists_and_list_filters(self, provider):
        provider.create(name="A", content="x", collection="Reports")
        provider.create(name="B", content="y", collection="Reports")
        provider.create(name="C", content="z")  # uncollected
        assert {a.slug for a in provider.list(collection="Reports")} == {"a", "b"}
        assert provider.list(collection="Nope") == []

    def test_update_reassigns_collection_metadata_only(self, provider):
        art = provider.create(name="A", content="x")
        assert art.version == 1
        upd = provider.update(art.slug, collection="Pinned")
        assert upd is not None and upd.collection == "Pinned"
        assert upd.version == 1  # metadata-only, no version bump

    def test_collection_survives_reload(self, provider):
        provider.create(name="A", content="x", collection="Keep")
        # a fresh provider over the same root reads the persisted meta.json
        fresh = NativeArtifactProvider(root=provider._ensure_root().parent / "artifacts")
        got = fresh.get("a")
        assert got is not None and got.collection == "Keep"


class TestFindSimilar:
    def test_matches_by_derived_slug(self, provider):
        provider.create(name="Sales Dashboard", content="x")
        sim = provider.find_similar("Sales Dashboard")
        assert sim is not None and sim.slug == "sales-dashboard"

    def test_none_when_no_match(self, provider):
        provider.create(name="Sales Dashboard", content="x")
        assert provider.find_similar("Totally Different") is None

    def test_empty_name_is_none(self, provider):
        assert provider.find_similar("") is None


async def _client(provider) -> TestClient:
    app = web.Application()
    state = MagicMock()
    state._restricted_keys = set()
    state._sessions = {}
    app["state"] = state
    register_artifact_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.fixture
def patched_native(provider):
    with patch.object(registry, "get_provider", return_value=provider):
        yield provider


@pytest.mark.asyncio
async def test_rest_create_dedup_409_then_force(patched_native) -> None:
    client = await _client(patched_native)
    try:
        r1 = await client.post(
            "/api/artifacts", json={"name": "Sales Dashboard", "content": "<div>1</div>"}
        )
        assert r1.status == 201
        slug1 = (await r1.json())["slug"]

        # Second save, same name, no slug/force → 409 similar_artifact_exists.
        r2 = await client.post(
            "/api/artifacts", json={"name": "Sales Dashboard", "content": "<div>2</div>"}
        )
        assert r2.status == 409
        body = await r2.json()
        assert body["error"] == "similar_artifact_exists"
        assert body["similar"]["slug"] == slug1

        # No "-2" twin was minted.
        listing = await (await client.get("/api/artifacts")).json()
        assert [a["slug"] for a in listing["artifacts"]] == [slug1]

        # ?force=1 bypasses → a new (disambiguated) artifact is created.
        r3 = await client.post(
            "/api/artifacts?force=1", json={"name": "Sales Dashboard", "content": "<div>3</div>"}
        )
        assert r3.status == 201
        assert (await r3.json())["slug"] != slug1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rest_collection_roundtrips(patched_native) -> None:
    client = await _client(patched_native)
    try:
        r = await client.post(
            "/api/artifacts", json={"name": "Q3", "content": "<div/>", "collection": "Reports"}
        )
        slug = (await r.json())["slug"]
        detail = await (await client.get(f"/api/artifacts/{slug}")).json()
        assert detail["collection"] == "Reports"
        # reassign via PATCH
        await client.patch(f"/api/artifacts/{slug}", json={"collection": "Archive"})
        detail2 = await (await client.get(f"/api/artifacts/{slug}")).json()
        assert detail2["collection"] == "Archive"
        # filter by collection
        listing = await (await client.get("/api/artifacts?collection=Archive")).json()
        assert slug in [a["slug"] for a in listing["artifacts"]]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rest_source_path_save_still_dedups_by_path_not_name(
    patched_native, tmp_path
) -> None:
    """A file-backed save (source_path) keeps its own dedup path — the name-similarity
    409 must NOT fire for it (source_path bumps the existing one instead)."""
    client = await _client(patched_native)
    try:
        p = str(tmp_path / "widget.html")
        r1 = await client.post(
            "/api/artifacts", json={"name": "W", "content": "1", "source_path": p}
        )
        assert r1.status == 201
        # same source_path → update (200), not a name-409
        r2 = await client.post(
            "/api/artifacts", json={"name": "W", "content": "2", "source_path": p}
        )
        assert r2.status == 200
    finally:
        await client.close()
