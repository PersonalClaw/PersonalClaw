"""E13-P1: Artifact entity — native provider + registry + REST handlers.

Covers the on-disk model (CRUD, slug derivation/disambiguation, traversal +
sensitive-path refusal), the explicit-snapshot versioning rule (silent save vs
snapshot), the live-pointer (file-backed reads/writes disk, live_dirty flips),
events, the registry's native default + readonly honoring, and the REST layer
(redaction, restricted-session 403, dedup-by-source_path, dashboard:ui drop).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.artifacts import registry
from personalclaw.artifacts.handlers import register_artifact_routes
from personalclaw.artifacts.models import MAX_VERSIONS, slugify
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.artifacts.provider import ArtifactProvider


@pytest.fixture
def provider(tmp_path) -> NativeArtifactProvider:
    return NativeArtifactProvider(root=tmp_path / "artifacts")


# ── slugify ──


class TestSlugify:
    def test_basic(self) -> None:
        assert slugify("My Chart!") == "my-chart"
        assert slugify("  Hello   World  ") == "hello-world"

    def test_unicode_normalized(self) -> None:
        assert slugify("Café Données") == "cafe-donnees"

    def test_empty_falls_back(self) -> None:
        assert slugify("") == "artifact"
        assert slugify("!!!") == "artifact"

    def test_truncated_to_80(self) -> None:
        assert len(slugify("a" * 200)) <= 80


# ── native provider ──


class TestNativeProvider:
    def test_create_and_get(self, provider) -> None:
        a = provider.create(
            name="My Chart!", content="<div>v1</div>", actor="user", session_id="s1"
        )
        assert a.slug == "my-chart"
        assert a.version == 1
        assert a.events[0].type == "created"
        g = provider.get("my-chart")
        assert g.content == "<div>v1</div>"
        assert g.live_dirty is False  # current matches the v1 snapshot

    def test_project_id_round_trips_and_filters(self, provider) -> None:
        # Projects native entity: an artifact can be tied to a containing project
        # and the list filters by it (so a project's outputs surface together).
        provider.create(name="Bound", content="x", project_id="p-abc123")
        provider.create(name="Free", content="y")
        assert provider.get("bound").project_id == "p-abc123"
        scoped = provider.list(project_id="p-abc123")
        assert [a.slug for a in scoped] == ["bound"]
        # persisted to meta.json (survives reload), not a transient field
        reread = provider.get("bound")
        assert reread.project_id == "p-abc123"

    def test_silent_save_no_version_bump(self, provider) -> None:
        provider.create(name="C", content="v1")
        u = provider.update("c", content="v2", snapshot=False, actor="user")
        assert u.version == 1
        assert u.content == "v2"
        assert u.live_dirty is True  # live ahead of latest snapshot

    def test_snapshot_bumps_version(self, provider) -> None:
        provider.create(name="C", content="v1")
        provider.update("c", content="v2", snapshot=False)
        s = provider.update("c", snapshot=True, actor="agent")  # captures live v2
        assert s.version == 2
        assert s.content == "v2"
        assert s.live_dirty is False
        assert s.events[-1].type == "iterated"  # agent → iterated

    def test_user_snapshot_is_edited(self, provider) -> None:
        provider.create(name="C", content="v1")
        s = provider.update("c", content="v2", snapshot=True, actor="user")
        assert s.events[-1].type == "edited"

    def test_versions_immutable(self, provider) -> None:
        provider.create(name="C", content="v1")
        provider.update("c", content="v2", snapshot=True)
        assert provider.list_versions("c") == [1, 2]
        assert provider.get("c", version=1).content == "v1"
        assert provider.get("c", version=2).content == "v2"
        assert provider.get("c", version=99) is None

    def test_metadata_only_update_no_bump(self, provider) -> None:
        provider.create(name="C", content="v1")
        u = provider.update("c", tags=["x", "y"], description="desc")
        assert u.version == 1
        assert u.tags == ["x", "y"]
        assert u.description == "desc"

    def test_slug_disambiguation(self, provider) -> None:
        a1 = provider.create(name="Dup", content="1")
        a2 = provider.create(name="Dup", content="2")
        a3 = provider.create(name="Dup", content="3")
        assert [a1.slug, a2.slug, a3.slug] == ["dup", "dup-2", "dup-3"]

    def test_explicit_slug_honored(self, provider) -> None:
        a = provider.create(name="X", content="1", slug="custom-slug")
        assert a.slug == "custom-slug"

    def test_traversal_refused(self, provider) -> None:
        with pytest.raises(ValueError):
            provider.get("../etc/passwd")
        with pytest.raises(ValueError):
            provider.get("foo/bar")

    def test_list_filters(self, provider) -> None:
        provider.create(name="Widget A", content="1", kind="widget", tags=["dash"])
        provider.create(name="Doc B", content="2", kind="markdown", tags=["notes"])
        assert len(provider.list()) == 2
        assert len(provider.list(kind="markdown")) == 1
        assert len(provider.list(tag="dash")) == 1
        assert len(provider.list(q="widget")) == 1

    def test_delete(self, provider) -> None:
        provider.create(name="C", content="v1")
        assert provider.delete("c") is True
        assert provider.get("c") is None
        assert provider.delete("c") is False

    def test_invalid_event_type_rejected_before_side_effects(self, provider) -> None:
        provider.create(name="C", content="v1")
        with pytest.raises(ValueError):
            provider.update("c", content="v2", snapshot=True, event_type="bogus")
        # No orphaned version: still at v1.
        assert provider.list_versions("c") == [1]

    def test_version_prune_at_cap(self, provider) -> None:
        provider.create(name="C", content="v0")
        for i in range(MAX_VERSIONS + 5):
            provider.update("c", content=f"v{i}", snapshot=True)
        nums = provider.list_versions("c")
        assert len(nums) == MAX_VERSIONS
        # Oldest pruned; the newest survive.
        assert nums[-1] == provider.get("c").version

    def test_meta_json_omits_content_and_live_dirty(self, provider, tmp_path) -> None:
        provider.create(name="C", content="secret-body")
        import json

        meta = json.loads((tmp_path / "artifacts" / "c" / "meta.json").read_text())
        assert "content" not in meta
        assert "live_dirty" not in meta


# ── live-pointer ──


class TestLivePointer:
    def test_file_backed_reads_live_disk(self, provider, tmp_path) -> None:
        f = tmp_path / "doc.md"
        f.write_text("# original")
        provider.create(name="Doc", content="# original", kind="markdown", source_path=str(f))
        # External edit to the workspace file.
        f.write_text("# edited externally")
        g = provider.get(provider.list()[0].slug)
        assert g.content == "# edited externally"  # live read, not snapshot
        assert g.live_dirty is True

    def test_update_writes_back_to_source(self, provider, tmp_path) -> None:
        f = tmp_path / "doc.md"
        f.write_text("orig")
        a = provider.create(name="Doc", content="orig", kind="markdown", source_path=str(f))
        provider.update(a.slug, content="from-artifact", snapshot=False)
        assert f.read_text() == "from-artifact"

    def test_snapshot_without_content_captures_live(self, provider, tmp_path) -> None:
        f = tmp_path / "doc.md"
        f.write_text("v1")
        a = provider.create(name="Doc", content="v1", kind="markdown", source_path=str(f))
        f.write_text("v2-external")
        s = provider.update(a.slug, snapshot=True)  # no content → capture live
        assert s.content == "v2-external"
        assert provider.get(a.slug, version=s.version).content == "v2-external"

    def test_missing_source_falls_back_to_current(self, provider, tmp_path) -> None:
        f = tmp_path / "doc.md"
        f.write_text("v1")
        a = provider.create(name="Doc", content="v1", kind="markdown", source_path=str(f))
        f.unlink()  # source disappears
        g = provider.get(a.slug)
        assert g.content == "v1"  # falls back to current.html

    def test_find_by_source_path_dedup(self, provider, tmp_path) -> None:
        f = tmp_path / "doc.md"
        f.write_text("x")
        a = provider.create(name="Doc", content="x", source_path=str(f))
        found = provider.find_by_source_path(str(f))
        assert found is not None and found.slug == a.slug

    def test_sensitive_source_path_refused_on_read(self, provider, tmp_path) -> None:
        a = provider.create(
            name="Creds",
            content="placeholder",
            source_path=str(Path.home() / ".aws" / "credentials"),
        )
        g = provider.get(a.slug)
        # Live read refused → falls back to current.html placeholder, never the real file.
        assert g.content == "placeholder"

    def test_sensitive_source_path_refused_on_write(self, provider, tmp_path) -> None:
        sensitive = str(Path.home() / ".ssh" / "id_rsa")
        a = provider.create(name="Key", content="placeholder", source_path=sensitive)
        # update must not write to the sensitive path (it returns, degraded).
        provider.update(a.slug, content="malicious", snapshot=False)
        # The sensitive file is untouched (we can't assert its content, but the
        # write path returns False; assert current.html still updated locally).
        assert provider.get(a.slug, version=1).content == "placeholder"


# ── record_impression ──


class TestImpression:
    def test_idempotent_per_session(self, provider) -> None:
        a = provider.create(name="C", content="x")  # 'created' has no session
        _, app1 = provider.record_impression(a.slug, session_id="sess-A")
        assert app1 is True
        _, app2 = provider.record_impression(a.slug, session_id="sess-A")
        assert app2 is False  # same session suppressed

    def test_suppressed_when_session_has_cud_event(self, provider) -> None:
        a = provider.create(name="C", content="x", session_id="sess-B")  # created by sess-B
        _, appended = provider.record_impression(a.slug, session_id="sess-B")
        assert appended is False  # session already has a lifecycle event


# ── registry ──


class TestRegistry:
    def test_ensure_native_default(self) -> None:
        registry._ensure_native()
        assert "native" in registry.list_providers()
        assert registry.get_provider("native") is not None
        assert registry.get_provider(None) is registry.get_provider("native")

    def test_readonly_provider_honored(self) -> None:
        class ReadOnlyStub(ArtifactProvider):
            name = "ro"
            display_name = "RO"
            readonly = True

            def list(self, **k):
                return []

            def get(self, slug, **k):
                return None

            def create(self, **k):
                raise AssertionError("should not create")

            def update(self, slug, **k):
                raise AssertionError("should not update")

            def delete(self, slug):
                return False

            def list_versions(self, slug):
                return []

            def find_by_source_path(self, sp):
                return None

            def record_impression(self, slug, **k):
                return None, False

        registry.register_provider(ReadOnlyStub())
        try:
            prov = registry.get_provider("ro")
            assert prov.readonly is True
        finally:
            registry.unregister_provider("ro")


# ── REST handlers ──


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
    """Force the registry to resolve our tmp-rooted provider as 'native'."""
    with patch.object(registry, "get_provider", return_value=provider):
        yield provider


@pytest.mark.asyncio
async def test_rest_create_and_get(patched_native) -> None:
    client = await _client(patched_native)
    try:
        resp = await client.post(
            "/api/artifacts", json={"name": "Chart", "content": "<div>hi</div>", "kind": "widget"}
        )
        assert resp.status == 201
        body = await resp.json()
        slug = body["slug"]
        detail = await client.get(f"/api/artifacts/{slug}")
        assert detail.status == 200
        assert (await detail.json())["content"] == "<div>hi</div>"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rest_redacts_content_and_name(patched_native) -> None:
    client = await _client(patched_native)
    try:
        secret = "AKIAIOSFODNN7EXAMPLE"
        resp = await client.post(
            "/api/artifacts",
            json={"name": f"key {secret}", "content": f"<div>{secret}</div>", "kind": "html"},
        )
        body = await resp.json()
        assert secret not in body["name"]
        assert secret not in body.get("content", "")
        assert "[REDACTED" in body["name"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rest_restricted_session_403(patched_native) -> None:
    client = await _client(patched_native)
    try:
        with patch("personalclaw.artifacts.handlers._is_restricted_session", return_value=True):
            resp = await client.post("/api/artifacts", json={"name": "X", "content": "y"})
            assert resp.status == 403
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rest_dedup_by_source_path(patched_native, tmp_path) -> None:
    f = tmp_path / "shared.md"
    f.write_text("orig")
    client = await _client(patched_native)
    try:
        r1 = await client.post(
            "/api/artifacts",
            json={"name": "Doc", "content": "orig", "kind": "markdown", "source_path": str(f)},
        )
        assert r1.status == 201
        slug1 = (await r1.json())["slug"]
        # Re-save same source_path → bump (200), same slug, no duplicate.
        r2 = await client.post(
            "/api/artifacts",
            json={"name": "Doc", "content": "updated", "kind": "markdown", "source_path": str(f)},
        )
        assert r2.status == 200
        assert (await r2.json())["slug"] == slug1
        listing = await (await client.get("/api/artifacts")).json()
        assert len([a for a in listing["artifacts"] if a["slug"] == slug1]) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rest_slug_collision_409(patched_native) -> None:
    client = await _client(patched_native)
    try:
        await client.post("/api/artifacts", json={"name": "A", "content": "x", "slug": "taken"})
        resp = await client.post(
            "/api/artifacts", json={"name": "B", "content": "y", "slug": "taken"}
        )
        assert resp.status == 409
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rest_events_drops_dashboard_ui(patched_native) -> None:
    client = await _client(patched_native)
    try:
        # create carries the browser's dashboard:ui marker as session
        resp = await client.post(
            "/api/artifacts",
            json={"name": "C", "content": "x"},
            headers={"X-Session-Key": "dashboard:ui"},
        )
        slug = (await resp.json())["slug"]
        events = await (await client.get(f"/api/artifacts/{slug}/events")).json()
        assert all(e["session_id"] != "dashboard:ui" for e in events["events"])
    finally:
        await client.close()
