"""A service worker this gateway didn't serve removes itself.

Driven through the real token middleware (``dashboard/worker_eviction.py``): the worker it answers
deletes the origin's caches and unregisters, and serves nothing while it does. A plain request is
answered as it always was.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from browser_sign_in import browser_token

from personalclaw.dashboard import session_store, token_auth, worker_eviction

PORT = 10000
COOKIE = f"pc_token_{PORT}"
UPDATE_CHECK = {"Service-Worker": "script", "Sec-Fetch-Dest": "serviceworker"}


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import personalclaw.config.loader as loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    token_auth.use_ephemeral_secret(b"worker-eviction")
    token_auth.revoke_all_sessions()
    yield tmp_path
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


async def _real_worker(request: web.Request) -> web.Response:
    return web.Response(text="// the dashboard's own worker", content_type="application/javascript")


async def _client() -> TestClient:
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    app.router.add_get("/sw.js", _real_worker)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _removes_itself(text: str) -> None:
    assert "registration.unregister()" in text
    assert "caches.delete" in text
    assert "addEventListener('fetch'" not in text, "a worker that serves while it leaves"


@pytest.mark.asyncio
async def test_the_worker_answered_deletes_the_caches_and_unregisters() -> None:
    client = await _client()
    try:
        checked = await client.get("/sw.js", headers=UPDATE_CHECK)
        assert checked.status == 200
        assert checked.content_type == "application/javascript"
        assert checked.headers["Cache-Control"] == "no-store"
        _removes_itself(await checked.text())
        plain = await client.get("/sw.js")
        assert plain.status in (302, 403), "a plain request is refused as it always was"
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/other-sw.js", "/api/sessions", "/assets/x.js"])
async def test_another_worker_script_gets_the_worker_that_removes_itself(path: str) -> None:
    client = await _client()
    try:
        owner = browser_token("owner", 600)
        for headers in ({}, {"Cookie": f"{COOKIE}={owner}"}):
            checked = await client.get(path, headers={**UPDATE_CHECK, **headers})
            assert checked.status == 200, path
            _removes_itself(await checked.text())
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_dashboards_own_worker_is_still_served() -> None:
    client = await _client()
    try:
        owner = browser_token("owner", 600)
        checked = await client.get(
            "/sw.js", headers={**UPDATE_CHECK, "Cookie": f"{COOKIE}={owner}"}
        )
        assert await checked.text() == "// the dashboard's own worker"
    finally:
        await client.close()


def test_the_check_is_keyed_on_the_fetch_never_on_a_cookie() -> None:
    class _Req:
        def __init__(self, path: str, headers: dict[str, str]) -> None:
            self.path, self.headers = path, headers

    assert worker_eviction.serves_no_worker(_Req("/x.js", {"Service-Worker": "script"}))
    assert not worker_eviction.serves_no_worker(_Req("/sw.js", {"Service-Worker": "script"}))
    assert not worker_eviction.serves_no_worker(_Req("/x.js", {"Cookie": "pc_token_10000=a"}))
