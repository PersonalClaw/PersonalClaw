"""A deployed artifact's page runs in an origin of its own, and its own capability serves it.

A deployed artifact is model-authored, so the page it serves is untrusted. It is served on the
dashboard's own origin, and what keeps it from acting as the dashboard is the response itself:

* **Every answer the serve route gives carries a CSP ``sandbox`` directive** (scripts allowed,
  nothing else), so the browser runs the page in an opaque origin however it is opened: framed in
  the dashboard, in a tab, or by any other route. From there the page cannot reach the dashboard
  window, the owner's cookie, web storage or a service worker of its own. The browser half of that
  is driven in ``web/e2e/deployedArtifacts.spec.ts``; this file holds the server half, across
  every branch the serve route answers through.
* **The deployment's capability authorizes its own files, and nothing else does.** A page in an
  opaque origin is cross-site to the gateway, so the browser sends its sub-resource requests (its
  script, its stylesheet, its fonts) without the session cookie. The URL a deployment is published
  at therefore carries an unguessable capability, minted on every deploy and gone on teardown, and
  the serve route reads it in place of the session. Without it nothing is served; the session alone
  serves nothing either. It is never written to the audit log.
"""

from __future__ import annotations

import ast
import inspect
import logging
import re
import textwrap
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

from personalclaw.artifacts import handlers as artifact_handlers
from personalclaw.artifacts import registry
from personalclaw.artifacts.deploy import SERVE_URL_PREFIX, ArtifactDeployStore
from personalclaw.artifacts.handlers import register_artifact_routes
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.server import _security_headers_middleware
from personalclaw.dashboard.token_auth import (
    bind_token_ip,
    generate_token,
    mark_consumed,
    revoke_all_sessions,
    token_auth_middleware,
)

#: What a served page is allowed to do in its sandbox: run its own scripts. No same-origin, no
#: popups, no top-level navigation, no forms, no modals, no downloads.
EXPECTED_SANDBOX = ["allow-scripts"]


@pytest.fixture
def provider(tmp_path) -> NativeArtifactProvider:
    return NativeArtifactProvider(root=tmp_path / "artifacts")


@pytest.fixture
def native(provider):
    with patch.object(registry, "get_provider", return_value=provider):
        yield provider


@pytest.fixture(autouse=True)
def _no_sessions():
    revoke_all_sessions()
    yield
    revoke_all_sessions()


def _parse_csp(header: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for chunk in header.split(";"):
        parts = chunk.split()
        if parts:
            out[parts[0].lower()] = parts[1:]
    return out


def _app(*, with_auth: bool) -> web.Application:
    """The serve route behind the gateway's own outer middlewares: the security-header defaults
    every response passes through, and (``with_auth``) the dashboard's session check."""
    middlewares: list = [_security_headers_middleware]
    if with_auth:
        middlewares.append(token_auth_middleware())
    app = web.Application(middlewares=middlewares)
    state = MagicMock()
    state._sessions = {}
    app["state"] = state
    register_artifact_routes(app)
    return app


async def _client(*, with_auth: bool = False) -> TestClient:
    client = TestClient(TestServer(_app(with_auth=with_auth)))
    await client.start_server()
    return client


def _owner_cookie() -> dict[str, str]:
    token = generate_token("owner", ttl_seconds=300)
    bind_token_ip(token, "127.0.0.1")
    mark_consumed(token)
    return {"pc_token_10000": token}


async def _deploy(client: TestClient, slug: str, **kw) -> str:
    resp = await client.post(f"/api/artifacts/{slug}/deploy", **kw)
    assert resp.status == 200, await resp.text()
    return (await resp.json())["deployment"]["url"]


def _capability_of(url: str) -> str:
    """The segment after the slug in ``/artifacts/serve/<slug>/<capability>/``."""
    parts = url.removeprefix(f"{SERVE_URL_PREFIX}/").strip("/").split("/")
    assert len(parts) == 2, f"a deployment's URL names its slug and its capability: {url}"
    return parts[1]


def _with_capability(url: str, capability: str) -> str:
    slug = url.removeprefix(f"{SERVE_URL_PREFIX}/").split("/")[0]
    return f"{SERVE_URL_PREFIX}/{slug}/{capability}/"


# ── every answer the serve route gives runs in an opaque origin ─────────────────────────────────


def _assert_opaque_origin_fence(resp, what: str) -> None:
    csp = resp.headers.get("Content-Security-Policy", "")
    directives = _parse_csp(csp)
    assert (
        directives.get("sandbox") == EXPECTED_SANDBOX
    ), f"{what}: the response does not put its page in an origin of its own: {csp!r}"
    # A service worker would outlive the deployment and keep answering its URL.
    assert directives.get("worker-src") == ["'none'"], f"{what}: {csp!r}"
    # The fence the page already had stays.
    assert directives.get("connect-src") == ["'none'"], f"{what}: {csp!r}"
    assert directives.get("default-src") == ["'none'"], f"{what}: {csp!r}"
    assert resp.headers.get("Cross-Origin-Opener-Policy") == "same-origin", what
    assert resp.headers.get("Referrer-Policy") == "no-referrer", what
    assert resp.headers.get("X-Content-Type-Options") == "nosniff", what
    denied = resp.headers.get("Permissions-Policy", "")
    for feature in ("camera", "microphone", "geolocation", "payment", "usb", "clipboard-read"):
        assert f"{feature}=()" in denied, f"{what}: {feature} is not denied in {denied!r}"


#: Each branch the serve route answers through, as (label, how to reach it).
BRANCHES = (
    "the artifact's own body as its entry",
    "an entry document on disk",
    "one of its own files",
    "the capability URL without its trailing slash",
    "a capability this deployment does not hold",
    "the URL without a capability",
    "the bare slug",
    "a path that leaves its files",
    "a file that is not there",
    "a component with no bundle",
    "an artifact gone from the store",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", BRANCHES)
async def test_every_answer_the_serve_route_gives_carries_the_sandbox(native, branch: str) -> None:
    prov = native
    store = ArtifactDeployStore(prov.root)
    kind = "react" if branch == "a component with no bundle" else "html"
    art = prov.create(name="Trip planner", content="<h1>Trip planner</h1>", kind=kind)
    files = store.files_root(art.slug)
    files.mkdir(parents=True)
    (files / "app.js").write_text("document.title = 'planner'")
    if branch == "an entry document on disk":
        (files / "index.html").write_text("<h1>from disk</h1><script src='app.js'></script>")
    client = await _client()
    try:
        if kind == "react":
            # Registered directly: the deploy route would build first, and the branch under
            # test is the one a registry row with no bundle behind it reaches.
            url = store.deploy(art.slug).url
        else:
            url = await _deploy(client, art.slug)
        if branch == "an artifact gone from the store":
            import shutil

            shutil.rmtree(prov.root / art.slug)
        reach = {
            "the artifact's own body as its entry": (url, 200),
            "an entry document on disk": (url, 200),
            "one of its own files": (url + "app.js", 200),
            "the capability URL without its trailing slash": (url.rstrip("/"), 308),
            "a capability this deployment does not hold": (
                _with_capability(url, "A" * 43) + "app.js",
                404,
            ),
            "the URL without a capability": (f"{SERVE_URL_PREFIX}/{art.slug}/", 404),
            "the bare slug": (f"{SERVE_URL_PREFIX}/{art.slug}", 404),
            "a path that leaves its files": (url + "%2e%2e%2f%2e%2e%2fmeta.json", 403),
            "a file that is not there": (url + "nope.js", 404),
            "a component with no bundle": (url, 404),
            "an artifact gone from the store": (url, 404),
        }
        path, status = reach[branch]
        resp = await client.get(URL(path, encoded=True), allow_redirects=False)
        assert resp.status == status, f"{branch}: {resp.status} {await resp.text()}"
        _assert_opaque_origin_fence(resp, branch)
    finally:
        await client.close()


def test_the_serve_route_answers_only_through_its_fenced_exits() -> None:
    """The rail behind the branch list above: a branch added later cannot answer without the
    fence. Every ``return`` in the serve handler hands back one of the two fenced exits, and both
    exits build their response with the serve headers."""
    exits = {"_served", "_refuse_serve"}
    handler = ast.parse(
        textwrap.dedent(inspect.getsource(artifact_handlers.serve_deployed_artifact))
    )
    returns = [n for n in ast.walk(handler) if isinstance(n, ast.Return)]
    assert returns, "the serve handler returns nothing the rail can read"
    for ret in returns:
        call = ret.value
        name = (
            call.func.id if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) else ""
        )
        assert name in exits, f"line {ret.lineno}: the serve handler answers without its fence"
    # And no response is built in the handler itself, outside those exits.
    built = [
        n
        for n in ast.walk(handler)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "web"
    ]
    assert built == [], "the serve handler builds a response of its own"
    for name in exits:
        source = inspect.getsource(getattr(artifact_handlers, name))
        assert "SERVE_HEADERS" in source, f"{name} answers without the serve headers"


# ── the capability serves the page; without it nothing is served ───────────────────────────────


@pytest.mark.asyncio
async def test_a_deployed_page_and_its_own_files_load_without_the_session_cookie(native) -> None:
    """The sandboxed page's own requests carry no cookie, and they still load: the deployment's
    capability is what authorizes them. Measured through the dashboard's real session check."""
    prov = native
    art = prov.create(name="Trip planner", content="<h1>planner</h1>", kind="html")
    files = ArtifactDeployStore(prov.root).files_root(art.slug)
    files.mkdir(parents=True)
    (files / "app.js").write_text("document.title = 'planner'")
    (files / "app.css").write_text("h1 { color: rgb(1, 2, 3) }")
    client = await _client(with_auth=True)
    try:
        url = await _deploy(client, art.slug, cookies=_owner_cookie())
        client.session.cookie_jar.clear()  # the page's own requests: no session at all
        for path, needle in ((url, "planner"), (url + "app.js", "title"), (url + "app.css", "rgb")):
            resp = await client.get(path)
            assert resp.status == 200, f"{path}: {resp.status}"
            assert needle in await resp.text()
            # Its module scripts and fonts are fetched in CORS mode from the opaque origin.
            assert resp.headers.get("Access-Control-Allow-Origin") == "*", path
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_without_its_capability_nothing_is_served(native) -> None:
    prov = native
    art = prov.create(name="Trip planner", content="<h1>planner</h1>", kind="html")
    other = prov.create(name="Recipe box", content="<h1>recipes</h1>", kind="html")
    client = await _client(with_auth=True)
    try:
        url = await _deploy(client, art.slug, cookies=_owner_cookie())
        other_url = await _deploy(client, other.slug, cookies=_owner_cookie())
        client.session.cookie_jar.clear()
        # Another deployment's capability opens nothing of this one.
        mixed = _with_capability(url, _capability_of(other_url))
        for path in (mixed, _with_capability(url, "B" * 43), mixed + "index.html"):
            resp = await client.get(path)
            assert resp.status == 404, path
            assert await resp.text() == "refused"
            assert "Access-Control-Allow-Origin" not in resp.headers
        # The URL with no capability stays behind the session, and the session alone serves
        # nothing: there is no capability in it to read.
        bare = f"{SERVE_URL_PREFIX}/{art.slug}/"
        assert (await client.get(bare, allow_redirects=False)).status in (302, 401, 403)
        owner = await client.get(bare, cookies=_owner_cookie(), allow_redirects=False)
        assert owner.status == 404
        assert "planner" not in await owner.text()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_each_deploy_mints_a_new_capability_and_the_old_url_stops_answering(native) -> None:
    prov = native
    art = prov.create(name="Trip planner", content="<h1>planner</h1>", kind="html")
    client = await _client()
    try:
        first = await _deploy(client, art.slug)
        second = await _deploy(client, art.slug)
        assert _capability_of(first) != _capability_of(second)
        # Unguessable: 256 bits in the URL-safe alphabet.
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", _capability_of(second))
        assert (await client.get(first)).status == 404
        assert (await client.get(second)).status == 200
        listed = (await (await client.get("/api/artifacts/deployed")).json())["deployments"]
        assert [d["url"] for d in listed] == [second]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_tearing_a_deployment_down_revokes_its_capability(native) -> None:
    prov = native
    art = prov.create(name="Trip planner", content="<h1>planner</h1>", kind="html")
    client = await _client()
    try:
        url = await _deploy(client, art.slug)
        assert (await client.get(url)).status == 200
        assert (await client.delete(f"/api/artifacts/{art.slug}/deploy")).status == 200
        assert (await client.get(url)).status == 404
        # Deploying again publishes it at a new URL; the revoked one stays dead.
        again = await _deploy(client, art.slug)
        assert again != url
        assert (await client.get(url)).status == 404
        assert (await client.get(again)).status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_deployment_recorded_without_a_capability_is_not_listed_or_served(native) -> None:
    """A record from before deployments carried a capability can never be named by a URL, so it is
    dropped on read rather than listed as a page that does not answer."""
    prov = native
    art = prov.create(name="Trip planner", content="<h1>planner</h1>", kind="html")
    store = ArtifactDeployStore(prov.root)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    stamp = "2026-09-01T00:00:00+00:00"
    store.path.write_text(
        f'[{{"slug": "{art.slug}", "entry": "index.html", "created_at": "{stamp}"}}]'
    )
    client = await _client()
    try:
        listed = (await (await client.get("/api/artifacts/deployed")).json())["deployments"]
        assert listed == []
        assert (await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/")).status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_capability_is_never_written_to_the_audit_log(native) -> None:
    prov = native
    art = prov.create(name="Trip planner", content="<h1>planner</h1>", kind="html")
    audit = MagicMock()
    with (
        patch.object(artifact_handlers, "sel", return_value=audit),
        patch.object(token_auth, "_sel_fn", return_value=audit),
    ):
        client = await _client(with_auth=True)
        try:
            url = await _deploy(client, art.slug, cookies=_owner_cookie())
            capability = _capability_of(url)
            client.session.cookie_jar.clear()
            assert (await client.get(url)).status == 200
            assert (await client.get(url + "nope.js")).status == 404
            assert (await client.get(url.rstrip("/"), allow_redirects=False)).status == 308
            assert (await client.get(url + "%2e%2e%2fmeta.json")).status in (403, 404)
            await client.delete(f"/api/artifacts/{art.slug}/deploy", cookies=_owner_cookie())
            assert (await client.get(url)).status == 404
        finally:
            await client.close()
    assert audit.method_calls, "nothing was audited, so the scan below would read nothing"
    assert capability not in repr(audit.method_calls)


@pytest.mark.asyncio
async def test_the_gateway_keeps_no_request_log_that_could_hold_the_capability(
    tmp_path, monkeypatch, caplog
) -> None:
    """Measured on the real gateway, whose runner is the one that would write an access log line
    (the request line, capability and all) for every served file."""
    import aiohttp

    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.server as server_mod

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    monkeypatch.setattr(server_mod, "_DIST_DIR", dist)
    monkeypatch.setattr(handlers_core_mod, "_DIST_DIR", dist)
    prov = NativeArtifactProvider(root=tmp_path / "home" / "artifacts")
    art = prov.create(name="Trip planner", content="<h1>planner</h1>", kind="html")
    caplog.set_level(logging.DEBUG)
    with patch.object(registry, "get_provider", return_value=prov):
        runner, _state = await server_mod.start_dashboard(sessions=MagicMock(count=0), port=0)
        base = f"http://127.0.0.1:{runner.addresses[0][1]}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{base}/api/artifacts/{art.slug}/deploy") as resp:
                    assert resp.status == 200, await resp.text()
                    url = (await resp.json())["deployment"]["url"]
                for path in (url, url + "nope.js", url.rstrip("/")):
                    async with session.get(base + path, allow_redirects=False) as resp:
                        assert resp.status in (200, 308, 404)
        finally:
            await runner.cleanup()
    capability = _capability_of(url)
    assert caplog.records, "nothing was logged at all, so the scan below would read nothing"
    assert all(capability not in record.getMessage() for record in caplog.records)


def test_only_a_capability_shaped_serve_path_skips_the_session_check() -> None:
    """The serve route authenticates itself by its capability, so the dashboard's session check
    lets exactly that shape through to it — and nothing else under the serve prefix, nor beside."""

    def skips(path: str) -> bool:
        return any(t.fullmatch(path) for t in token_auth._BYPASS_TEMPLATES)

    capability = "Q" * 43
    for path in (
        f"{SERVE_URL_PREFIX}/trip-planner/{capability}",
        f"{SERVE_URL_PREFIX}/trip-planner/{capability}/",
        f"{SERVE_URL_PREFIX}/trip-planner/{capability}/assets/app.js",
    ):
        assert skips(path), path
    for path in (
        f"{SERVE_URL_PREFIX}/trip-planner",
        f"{SERVE_URL_PREFIX}/trip-planner/",
        f"{SERVE_URL_PREFIX}/trip-planner/index.html",
        f"{SERVE_URL_PREFIX}/trip-planner/{capability[:-1]}/",
        f"{SERVE_URL_PREFIX}/Trip_Planner/{capability}/",
        f"/api/artifacts/trip-planner/{capability}/",
        f"/artifacts/trip-planner/{capability}/",
    ):
        assert not skips(path), path
    # No prefix or exact exemption covers the serve route wholesale.
    assert not any(f"{SERVE_URL_PREFIX}/x/".startswith(p) for p in token_auth._BYPASS_PREFIXES)
    assert not any(p.startswith(SERVE_URL_PREFIX) for p in token_auth._BYPASS_EXACT)
