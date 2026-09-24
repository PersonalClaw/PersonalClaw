"""PEP-8 — local static artifact deploy: the serve route, its containment spine,
the CSP fence, and teardown.

The three clauses this file exists to hold, each asserted behaviourally (a rail that
only greps for a guard's name cannot catch a neutered guard):

* **Renders + is interactable** — a deployed html/widget artifact answers at
  ``/artifacts/serve/<slug>/`` with its own body, and an extra file under the
  artifact's ``webapp/`` root (the script that makes it interactive) is served too.
* **A traversal attempt is refused** — every escape shape (``..``, percent-encoded
  ``..``, absolute, backslash, a symlink out of the root) is refused, and the target
  file's bytes never appear in a response.
* **The fence is real** — the CSP header is parsed into directives and the VALUES
  are asserted (``connect-src 'none'`` etc.), so weakening a directive reds this file
  rather than passing on mere header presence.
* **Teardown removes the route** — after teardown (explicit, or by deleting the
  artifact) the path 404s instead of serving a stale page.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

from personalclaw.artifacts import registry
from personalclaw.artifacts.deploy import (
    ARTIFACT_SERVE_CSP,
    DEFAULT_ENTRY,
    MAX_DEPLOYMENTS,
    SERVE_URL_PREFIX,
    ArtifactDeployStore,
    rejects_path,
    resolve_served_file,
)
from personalclaw.artifacts.handlers import register_artifact_routes
from personalclaw.artifacts.native import NativeArtifactProvider

SECRET_BODY = "TOP-SECRET-OUTSIDE-THE-ROOT"


@pytest.fixture
def provider(tmp_path) -> NativeArtifactProvider:
    return NativeArtifactProvider(root=tmp_path / "artifacts")


@pytest.fixture
def store(provider) -> ArtifactDeployStore:
    return ArtifactDeployStore(provider.root)


@pytest.fixture
def patched_native(provider):
    with patch.object(registry, "get_provider", return_value=provider):
        yield provider


async def _client(app_provider) -> TestClient:
    app = web.Application()
    state = MagicMock()
    state._restricted_keys = set()
    state._sessions = {}
    app["state"] = state
    register_artifact_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _parse_csp(header: str) -> dict[str, list[str]]:
    """``"default-src 'none'; connect-src 'none'"`` → ``{"default-src": ["'none'"], …}``."""
    out: dict[str, list[str]] = {}
    for chunk in header.split(";"):
        parts = chunk.split()
        if parts:
            out[parts[0].lower()] = parts[1:]
    return out


# ── the path spine ───────────────────────────────────────────────────────────


class TestRejectsPath:
    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "../secret.txt",
            "a/../../secret.txt",
            "%2e%2e/secret.txt",
            "%2e%2e%2fsecret.txt",
            "..%2fsecret.txt",
            "/etc/passwd",
            "~/.ssh/id_rsa",
            "dir\\..\\secret.txt",
            "C:\\secret.txt",
            "http://evil.test/x",
            "a\x00.html",
        ],
    )
    def test_refuses_every_escape_shape(self, bad: str) -> None:
        assert rejects_path(bad) is True

    @pytest.mark.parametrize("ok", ["index.html", "assets/app.js", "a/b/c.css", "x.y.z.json"])
    def test_allows_ordinary_relative_paths(self, ok: str) -> None:
        assert rejects_path(ok) is False


class TestResolveServedFile:
    def test_resolves_a_real_nested_file(self, tmp_path) -> None:
        root = tmp_path / "webapp"
        (root / "assets").mkdir(parents=True)
        target = root / "assets" / "app.js"
        target.write_text("console.log(1)")
        assert resolve_served_file(root, "assets/app.js") == target.resolve()

    def test_refuses_dotdot_even_when_the_target_exists(self, tmp_path) -> None:
        root = tmp_path / "webapp"
        root.mkdir()
        (tmp_path / "secret.txt").write_text(SECRET_BODY)
        assert resolve_served_file(root, "../secret.txt") is None

    def test_refuses_a_symlink_pointing_out_of_the_root(self, tmp_path) -> None:
        root = tmp_path / "webapp"
        root.mkdir()
        outside = tmp_path / "secret.txt"
        outside.write_text(SECRET_BODY)
        (root / "escape.txt").symlink_to(outside)
        assert resolve_served_file(root, "escape.txt") is None

    def test_refuses_a_symlinked_directory_component(self, tmp_path) -> None:
        root = tmp_path / "webapp"
        root.mkdir()
        outside_dir = tmp_path / "elsewhere"
        outside_dir.mkdir()
        (outside_dir / "secret.txt").write_text(SECRET_BODY)
        (root / "link").symlink_to(outside_dir)
        assert resolve_served_file(root, "link/secret.txt") is None

    def test_never_serves_a_directory_as_an_index(self, tmp_path) -> None:
        root = tmp_path / "webapp"
        (root / "sub").mkdir(parents=True)
        (root / "sub" / "index.html").write_text("<p>x</p>")
        assert resolve_served_file(root, "sub") is None

    def test_missing_file_is_none(self, tmp_path) -> None:
        root = tmp_path / "webapp"
        root.mkdir()
        assert resolve_served_file(root, "nope.html") is None


# ── the deploy registry ──────────────────────────────────────────────────────


class TestDeployStore:
    def test_deploy_then_read_back_and_teardown(self, store) -> None:
        dep = store.deploy("my-app")
        assert dep.slug == "my-app"
        assert dep.entry == DEFAULT_ENTRY
        assert dep.url == f"{SERVE_URL_PREFIX}/my-app/"
        assert store.is_deployed("my-app") is True
        assert store.teardown("my-app") is True
        assert store.is_deployed("my-app") is False
        assert store.teardown("my-app") is False  # second teardown is a no-op

    def test_deploy_is_idempotent_and_refreshes_entry(self, store) -> None:
        store.deploy("my-app")
        store.deploy("my-app", entry="main.html")
        deployments = store.list()
        assert [d.slug for d in deployments] == ["my-app"]
        assert deployments[0].entry == "main.html"

    def test_survives_reload_through_a_fresh_store(self, store, provider) -> None:
        store.deploy("my-app")
        fresh = ArtifactDeployStore(provider.root)
        assert fresh.is_deployed("my-app") is True

    def test_refuses_an_invalid_slug_and_an_escaping_entry(self, store) -> None:
        with pytest.raises(ValueError):
            store.deploy("../etc")
        with pytest.raises(ValueError):
            store.deploy("my-app", entry="../../secret.txt")
        assert store.is_deployed("my-app") is False

    def test_files_root_is_inside_the_artifact_directory(self, store, provider) -> None:
        assert store.files_root("my-app") == provider.root / "my-app" / "webapp"
        with pytest.raises(ValueError):
            store.files_root("../escape")

    def test_caps_the_registry(self, store) -> None:
        for i in range(MAX_DEPLOYMENTS):
            store.deploy(f"app-{i}")
        with pytest.raises(ValueError):
            store.deploy("one-too-many")

    def test_corrupt_registry_reads_as_empty(self, store) -> None:
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("{not json")
        assert store.list() == []


# ── the serve route ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deployed_widget_renders_and_serves_its_own_files(patched_native) -> None:
    """The render + interact clause: the entry document AND the script beside it."""
    prov = patched_native
    art = prov.create(name="My App", content="<h1>hi</h1><script src='app.js'></script>")
    files = ArtifactDeployStore(prov.root).files_root(art.slug)
    files.mkdir(parents=True)
    (files / "app.js").write_text("document.title='clicked'")
    client = await _client(prov)
    try:
        assert (await client.post(f"/api/artifacts/{art.slug}/deploy")).status == 200

        page = await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/")
        assert page.status == 200
        assert page.content_type == "text/html"
        assert "<h1>hi</h1>" in await page.text()

        script = await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/app.js")
        assert script.status == 200
        assert "clicked" in await script.text()
        assert script.content_type in ("text/javascript", "application/javascript")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_entry_file_on_disk_wins_over_the_body(patched_native) -> None:
    prov = patched_native
    art = prov.create(name="My App", content="<h1>body</h1>")
    files = ArtifactDeployStore(prov.root).files_root(art.slug)
    files.mkdir(parents=True)
    (files / "index.html").write_text("<h1>from-disk</h1>")
    client = await _client(prov)
    try:
        await client.post(f"/api/artifacts/{art.slug}/deploy")
        page = await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/")
        assert "from-disk" in await page.text()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_slash_less_url_redirects_to_the_directory_form(patched_native) -> None:
    prov = patched_native
    art = prov.create(name="My App", content="<h1>hi</h1>")
    client = await _client(prov)
    try:
        await client.post(f"/api/artifacts/{art.slug}/deploy")
        r = await client.get(f"{SERVE_URL_PREFIX}/{art.slug}", allow_redirects=False)
        assert r.status == 308
        assert r.headers["Location"] == f"{SERVE_URL_PREFIX}/{art.slug}/"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_undeployed_and_unknown_slugs_are_not_served(patched_native) -> None:
    prov = patched_native
    art = prov.create(name="My App", content="<h1>hi</h1>")
    client = await _client(prov)
    try:
        # The route exists, but nothing is published for this slug yet.
        assert (await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/")).status == 404
        assert (await client.get(f"{SERVE_URL_PREFIX}/nope/")).status == 404
    finally:
        await client.close()


# ── traversal ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        "%2e%2e%2f%2e%2e%2fsecret.txt",
        "..%2f..%2fsecret.txt",
        "sub%2f%2e%2e%2f%2e%2e%2f%2e%2e%2fsecret.txt",
        "%2f%2fetc%2fpasswd",
    ],
)
async def test_traversal_is_refused_and_leaks_nothing(patched_native, raw: str) -> None:
    prov = patched_native
    art = prov.create(name="My App", content="<h1>hi</h1>")
    store = ArtifactDeployStore(prov.root)
    files = store.files_root(art.slug)
    files.mkdir(parents=True)
    # The file a traversal would reach: one level above the artifact's files root.
    (files.parent / "secret.txt").write_text(SECRET_BODY)
    (prov.root / "secret.txt").write_text(SECRET_BODY)
    client = await _client(prov)
    try:
        await client.post(f"/api/artifacts/{art.slug}/deploy")
        resp = await client.get(URL(f"{SERVE_URL_PREFIX}/{art.slug}/{raw}", encoded=True))
        assert resp.status in (403, 404)
        assert SECRET_BODY not in await resp.text()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_symlink_out_of_the_root_is_refused_over_http(patched_native, tmp_path) -> None:
    prov = patched_native
    art = prov.create(name="My App", content="<h1>hi</h1>")
    files = ArtifactDeployStore(prov.root).files_root(art.slug)
    files.mkdir(parents=True)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text(SECRET_BODY)
    (files / "escape.txt").symlink_to(outside)
    client = await _client(prov)
    try:
        await client.post(f"/api/artifacts/{art.slug}/deploy")
        resp = await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/escape.txt")
        assert resp.status in (403, 404)
        assert SECRET_BODY not in await resp.text()
    finally:
        await client.close()


# ── the CSP fence ────────────────────────────────────────────────────────────


class TestCspFenceValue:
    """Directive VALUES, not header presence: weakening one must red here."""

    def test_the_page_cannot_reach_the_gateway_api(self) -> None:
        directives = _parse_csp(ARTIFACT_SERVE_CSP)
        # connect-src is THE fence: fetch/XHR/WebSocket/EventSource/sendBeacon all
        # fall under it, so 'none' is what makes /api unreachable from the document.
        assert directives["connect-src"] == ["'none'"]
        # Nothing may re-open it by inheritance or by navigation.
        assert directives["default-src"] == ["'none'"]
        assert directives["form-action"] == ["'none'"]
        assert directives["base-uri"] == ["'none'"]
        assert directives["object-src"] == ["'none'"]
        # Embeddable in the dashboard's own pane (the in-app open), nowhere else.
        assert directives["frame-ancestors"] == ["'self'"]
        # No directive that could carry a request to /api may name this origin or a
        # wildcard. (script/style/img/font are same-origin FILE loads, not API calls.)
        for name in ("connect-src", "form-action", "base-uri", "object-src", "default-src"):
            assert "'self'" not in directives[name]
            assert "*" not in directives[name]


@pytest.mark.asyncio
async def test_every_served_response_carries_the_fence(patched_native) -> None:
    prov = patched_native
    art = prov.create(name="My App", content="<h1>hi</h1>")
    files = ArtifactDeployStore(prov.root).files_root(art.slug)
    files.mkdir(parents=True)
    (files / "app.js").write_text("1")
    client = await _client(prov)
    try:
        await client.post(f"/api/artifacts/{art.slug}/deploy")
        for path in ("", "app.js"):
            resp = await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/{path}")
            assert resp.status == 200
            directives = _parse_csp(resp.headers["Content-Security-Policy"])
            assert directives["connect-src"] == ["'none'"]
            assert directives["default-src"] == ["'none'"]
            assert directives["form-action"] == ["'none'"]
            assert resp.headers["X-Content-Type-Options"] == "nosniff"
    finally:
        await client.close()


def test_serve_prefix_is_not_auth_bypassed() -> None:
    """ "Behind session auth": the serve prefix must be in no auth-bypass allowlist."""
    from personalclaw.dashboard import token_auth

    assert not any(f"{SERVE_URL_PREFIX}/x".startswith(p) for p in token_auth._BYPASS_PREFIXES)
    assert f"{SERVE_URL_PREFIX}/x" not in token_auth._BYPASS_EXACT


# ── teardown ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_teardown_removes_the_route(patched_native) -> None:
    prov = patched_native
    art = prov.create(name="My App", content="<h1>hi</h1>")
    files = ArtifactDeployStore(prov.root).files_root(art.slug)
    files.mkdir(parents=True)
    (files / "app.js").write_text("1")
    client = await _client(prov)
    try:
        await client.post(f"/api/artifacts/{art.slug}/deploy")
        assert (await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/")).status == 200

        torn = await client.delete(f"/api/artifacts/{art.slug}/deploy")
        assert torn.status == 200
        assert (await torn.json())["removed"] is True

        # The page AND its files are gone — no stale handler keeps serving either.
        assert (await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/")).status == 404
        assert (await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/app.js")).status == 404
        # Un-publishing is not deleting: the artifact itself survives.
        assert (await client.get(f"/api/artifacts/{art.slug}")).status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_deleting_the_artifact_tears_the_deployment_down(patched_native) -> None:
    prov = patched_native
    art = prov.create(name="My App", content="<h1>hi</h1>")
    client = await _client(prov)
    try:
        await client.post(f"/api/artifacts/{art.slug}/deploy")
        assert (await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/")).status == 200
        assert (await client.delete(f"/api/artifacts/{art.slug}")).status == 200
        assert (await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/")).status == 404
        assert ArtifactDeployStore(prov.root).is_deployed(art.slug) is False
    finally:
        await client.close()


# ── the deploy API ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deployed_listing_carries_the_url(patched_native) -> None:
    prov = patched_native
    art = prov.create(name="My App", content="<h1>hi</h1>")
    client = await _client(prov)
    try:
        await client.post(f"/api/artifacts/{art.slug}/deploy")
        rows = (await (await client.get("/api/artifacts/deployed")).json())["deployments"]
        assert [r["slug"] for r in rows] == [art.slug]
        assert rows[0]["url"] == f"{SERVE_URL_PREFIX}/{art.slug}/"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_deploy_refuses_a_non_deployable_kind_and_a_missing_artifact(patched_native) -> None:
    prov = patched_native
    doc = prov.create(name="Notes", content="# hi", kind="markdown")
    client = await _client(prov)
    try:
        bad = await client.post(f"/api/artifacts/{doc.slug}/deploy")
        assert bad.status == 400
        assert "not deployable" in (await bad.json())["error"]
        assert (await client.post("/api/artifacts/ghost/deploy")).status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_restricted_session_may_not_deploy_or_tear_down(patched_native) -> None:
    prov = patched_native
    art = prov.create(name="My App", content="<h1>hi</h1>")
    app = web.Application()
    app["state"] = MagicMock()
    register_artifact_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        with patch("personalclaw.artifacts.handlers._is_restricted_session", return_value=True):
            assert (await client.post(f"/api/artifacts/{art.slug}/deploy")).status == 403
            assert (await client.delete(f"/api/artifacts/{art.slug}/deploy")).status == 403
    finally:
        await client.close()
