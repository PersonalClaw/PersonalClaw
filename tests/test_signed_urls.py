"""A signed resource URL stands in for its pair beside its own cookie, and nowhere else.

What the browser loads by itself (media, downloads, new tabs) can't carry the pair header, so the
page has each such URL signed (``POST /api/auth/signed-urls``, ``dashboard/signed_urls.py``). Driven
over the real token middleware, the real mint and the real security headers on a real
``TestServer``, with a stand-in at one signable route.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from browser_sign_in import browser_token, pair_of
from shared_app_rows import recording_sel
from yarl import URL

from personalclaw.dashboard import session_store, signed_urls, token_auth
from personalclaw.dashboard.server import _security_headers_middleware
from personalclaw.dashboard.sign_in_pair import PAIR_HEADER

PORT = 10000
COOKIE = f"pc_token_{PORT}"
RAW = "/api/artifacts/cat/raw?version=2"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import personalclaw.config.loader as loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    token_auth.use_ephemeral_secret(b"signed-urls")
    token_auth.revoke_all_sessions()
    yield tmp_path
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


async def _raw(request: web.Request) -> web.Response:
    # A route that chooses its own caching, as an image route does.
    return web.Response(
        body=b"cat", content_type="image/png", headers={"Cache-Control": "max-age=3600"}
    )


async def _client() -> TestClient:
    app = web.Application(
        middlewares=[_security_headers_middleware, token_auth.token_auth_middleware(port=PORT)]
    )
    app.router.add_get("/api/artifacts/{slug}/raw", _raw)
    app.router.add_get("/api/sessions", _raw)
    app.router.add_post("/api/auth/signed-urls", signed_urls.api_signed_urls)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _page(token: str) -> dict[str, str]:
    return {"Cookie": f"{COOKIE}={token}", PAIR_HEADER: pair_of(token)}


async def _signed(client: TestClient, token: str, *urls: str) -> list[dict[str, Any]]:
    resp = await client.post(
        "/api/auth/signed-urls", json={"urls": list(urls)}, headers=_page(token)
    )
    assert resp.status == 200, await resp.text()
    assert resp.headers["Cache-Control"] == "no-store"
    return (await resp.json())["urls"]


def test_the_signable_routes_are_exactly_the_nine_a_browser_loads_by_itself() -> None:
    assert signed_urls.SIGNABLE_ROUTES == (
        "/api/artifacts/{slug}/raw",
        "/api/file-raw",
        "/api/knowledge/items/{id}/file",
        "/api/knowledge/items/{id}/thumbnail",
        "/api/rooms/{room_id}/export",
        "/api/chat/sessions/{session}/export",
        "/api/inbox/{id}/attachments/{aid}",
        "/api/projects/{project_id}/export",
        "/api/dashboard/views/{view_id}/tiles/refresh",
    )
    assert signed_urls.signable("GET", "/api/artifacts/cat/raw")
    assert signed_urls.signable("HEAD", "/api/file-raw")
    assert not signed_urls.signable("POST", "/api/file-raw")
    assert not signed_urls.signable("GET", "/api/artifacts/cat/raw/more")
    assert not signed_urls.signable("GET", "/api/sessions")


@pytest.mark.asyncio
async def test_a_signed_url_loads_beside_its_cookie_and_is_kept_out_of_every_cache() -> None:
    client = await _client()
    try:
        owner = browser_token("owner", 600)
        (row,) = await _signed(client, owner, RAW)
        assert row["url"] == RAW and "pc_sig=" in row["signed"] and "pc_exp=" in row["signed"]
        assert row["exp"] <= time.time() + signed_urls.MAX_SIGNED_SECS + 1
        loaded = await client.get(row["signed"], headers={"Cookie": f"{COOKIE}={owner}"})
        assert loaded.status == 200, await loaded.text()
        assert loaded.headers["Cache-Control"] == "private, no-store"
        unsigned = await client.get(RAW, headers={"Cookie": f"{COOKIE}={owner}"})
        assert unsigned.status == 403
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_signature_admits_nothing_it_was_not_made_for() -> None:
    """Another session's cookie, no cookie, another path, another method, another query, after
    its expiry, with an extra `pc_` key: each refused."""
    client = await _client()
    try:
        owner = browser_token("owner", 600)
        other = browser_token("owner", 600)
        (row,) = await _signed(client, owner, RAW)
        signed = URL(row["signed"])
        mine = {"Cookie": f"{COOKIE}={owner}"}
        assert (
            await client.get(str(signed), headers={"Cookie": f"{COOKIE}={other}"})
        ).status == 403
        assert (await client.get(str(signed))).status == 403
        moved = signed.with_path("/api/artifacts/dog/raw")
        assert (await client.get(str(moved), headers=mine)).status == 403
        assert (await client.post(str(signed), headers=mine)).status == 403
        changed = signed.update_query({"version": "3"})
        assert (await client.get(str(changed), headers=mine)).status == 403
        extra = signed.update_query({"pc_extra": "1"})
        assert (await client.get(str(extra), headers=mine)).status == 403
        expired = signed.update_query({"pc_exp": str(int(time.time()) - 1)})
        refused = await client.get(str(expired), headers=mine)
        assert refused.status == 403
        error = (await refused.json())["error"]
        assert error["detail"]["reason"] == "signature_refused"
        assert "X-Auth-Required" not in refused.headers, "a refused signature signs the page out"
        on_another_route = URL("/api/sessions").with_query(signed.query)
        assert (await client.get(str(on_another_route), headers=mine)).status == 403
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_mint_signs_only_for_a_page_with_its_pair_and_only_this_gateways_urls() -> None:
    client = await _client()
    try:
        owner = browser_token("owner", 600)
        cookie_alone = await client.post(
            "/api/auth/signed-urls", json={"urls": [RAW]}, headers={"Cookie": f"{COOKIE}={owner}"}
        )
        assert cookie_alone.status == 403
        script = token_auth.generate_token("owner", ttl_seconds=600)
        as_script = await client.post(
            "/api/auth/signed-urls",
            json={"urls": [RAW]},
            headers={"Authorization": f"Bearer {script}"},
        )
        assert as_script.status == 403
        assert (await as_script.json())["error"]["code"] == "signed_urls_need_a_page"
        rows = await _signed(
            client,
            owner,
            "/api/sessions",
            "https://example.com/api/file-raw?path=a",
            "data:image/png;base64,AA",
            f"{RAW}#frag",
            "/api/file-raw?path=a&pc_x=1",
            f"{client.make_url(RAW)}",
        )
        assert [r.get("error") for r in rows[:5]] == ["not_signable"] * 5
        assert "pc_sig=" in rows[5]["signed"], "an absolute URL to this gateway is signed"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_signature_is_in_no_security_event() -> None:
    recorder = MagicMock()
    client = await _client()
    try:
        owner = browser_token("owner", 600)
        with recording_sel(recorder):
            (row,) = await _signed(client, owner, RAW)
            await client.get(row["signed"], headers={"Cookie": f"{COOKIE}={owner}"})
            await client.get(row["signed"] + "x", headers={"Cookie": f"{COOKIE}={owner}"})
        signature = URL(row["signed"]).query["pc_sig"]
        assert all(signature not in repr(call) for call in recorder.mock_calls)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_signed_head_on_a_tiles_refresh_reads_and_never_refreshes(monkeypatch) -> None:
    """aiohttp routes a HEAD to the GET's handler, and this handler's other branch is the POST's
    forced refresh. A signed HEAD with a body naming ``force`` reads the row, and runs nothing."""
    from personalclaw.dashboard import tile_refresh
    from personalclaw.dashboard.handlers import views as views_h

    refreshed: list[Any] = []

    async def _refresh(*args: Any, **kwargs: Any) -> Any:
        refreshed.append((args, kwargs))
        raise AssertionError("a HEAD ran the refresh")

    monkeypatch.setattr(tile_refresh, "refresh_tile", _refresh)
    monkeypatch.setattr(tile_refresh, "last_row", lambda view_id, ref: {"ref": ref})
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    path = "/api/dashboard/views/{view_id}/tiles/refresh"
    app.router.add_get(path, views_h.api_dashboard_view_tile_refresh)
    app.router.add_post("/api/auth/signed-urls", signed_urls.api_signed_urls)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        token = browser_token("owner", 600)
        url = "/api/dashboard/views/main/tiles/refresh?ref=weather"
        [row] = await _signed(client, token, url)
        head = await client.head(
            row["signed"],
            headers={"Cookie": f"{COOKIE}={token}", "Content-Type": "application/json"},
            data=b'{"ref": "weather", "force": true}',
        )
        assert head.status == 200, await head.text()
        assert refreshed == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_signature_never_outlasts_its_sign_in_and_dies_with_it() -> None:
    client = await _client()
    try:
        owner = browser_token("owner", 600)
        (row,) = await _signed(client, owner, RAW)
        assert row["exp"] <= time.time() + 600
        token_auth.sign_out([token_auth.token_nonce(owner)], token_auth.END_SIGNED_OUT, actor="t")
        after = await client.get(row["signed"], headers={"Cookie": f"{COOKIE}={owner}"})
        assert after.status in (401, 403), "a signature outlived its sign-in"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_signed_head_over_the_wire_opens_and_a_bearer_beside_it_does_not() -> None:
    client = await _client()
    try:
        owner = browser_token("owner", 600)
        (row,) = await _signed(client, owner, RAW)
        head = await client.head(row["signed"], headers={"Cookie": f"{COOKIE}={owner}"})
        assert head.status == 200
        bearer = await client.get(row["signed"], headers={"Authorization": f"Bearer {owner}"})
        assert bearer.status == 403, "a signature stands in beside a Bearer"
        assert (await bearer.json())["error"]["detail"]["reason"] == "pair_missing"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_mint_takes_up_to_its_cap_and_refuses_one_more() -> None:
    client = await _client()
    try:
        owner = browser_token("owner", 600)
        assert len(await _signed(client, owner, *([RAW] * signed_urls.MAX_URLS))) == 64
        over = await client.post(
            "/api/auth/signed-urls",
            json={"urls": [RAW] * (signed_urls.MAX_URLS + 1)},
            headers=_page(owner),
        )
        assert over.status == 400
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_scheme_relative_url_is_refused_beside_the_others() -> None:
    client = await _client()
    try:
        owner = browser_token("owner", 600)
        far, near = await _signed(client, owner, "//evil.example" + RAW, RAW)
        assert far == {"url": "//evil.example" + RAW, "error": "not_signable"}
        assert "pc_sig=" in near["signed"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_an_expiry_the_mint_could_not_have_written_is_refused() -> None:
    """The same second count in other digits reads as the same number, and the signature covers
    the number: only the mint's own ASCII form is accepted."""
    client = await _client()
    try:
        owner = browser_token("owner", 600)
        (row,) = await _signed(client, owner, RAW)
        signed = URL(row["signed"])
        expiry = signed.query["pc_exp"].translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
        assert int(expiry) == int(signed.query["pc_exp"])
        forged = signed.update_query({"pc_exp": expiry})
        resp = await client.get(str(forged), headers={"Cookie": f"{COOKIE}={owner}"})
        assert resp.status == 403
    finally:
        await client.close()


def test_an_encoded_slash_never_signs_the_same_as_a_slash() -> None:
    encoded, plain = URL("/api/file-raw/a%2Fb"), URL("/api/file-raw/a/b")
    assert signed_urls._canonical("GET", encoded.path_safe, encoded.query) != (
        signed_urls._canonical("GET", plain.path_safe, plain.query)
    )


@pytest.mark.asyncio
async def test_a_signature_is_in_no_log_line(caplog) -> None:
    import logging

    client = await _client()
    try:
        owner = browser_token("owner", 600)
        with caplog.at_level(logging.DEBUG):
            (row,) = await _signed(client, owner, RAW)
            await client.get(row["signed"], headers={"Cookie": f"{COOKIE}={owner}"})
        signature = URL(row["signed"]).query["pc_sig"]
        # The test server's own access log is the harness's: the gateway runs with none.
        ours = [r.getMessage() for r in caplog.records if r.name.startswith("personalclaw")]
        assert all(signature not in line for line in ours)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_signed_address_with_an_encoded_percent_opens() -> None:
    """The mint and the check read the same path: an encoded ``%`` stays encoded on both sides."""
    client = await _client()
    try:
        owner = browser_token("owner", 600)
        (row,) = await _signed(client, owner, "/api/artifacts/a%25b/raw")
        opened = await client.get(row["signed"], headers={"Cookie": f"{COOKIE}={owner}"})
        assert opened.status == 200, await opened.text()
    finally:
        await client.close()
