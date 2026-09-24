"""EA-5: the capture proxy refuses an upstream redirect instead of following it.

The module's own security thesis is that ``_forward`` is the SOLE place a socket is
opened and the egress guard runs strictly before it, "so a denied host is never dialed".
``aiohttp``'s ``session.post`` defaults to ``allow_redirects=True``, which broke exactly
that claim: a 3xx from the allow-listed upstream made the client open a SECOND connection
to a ``Location`` the upstream chose and the guard never evaluated — including a private
or link-local one. The pre-flight is a decision about ONE url; following a hop silently
widens it to any url the upstream names.

``net/client.py`` already treats this as the hazard it is: ``allow_redirects=False`` plus
a per-hop re-``evaluate``, whose comment names "the gap an ``allow_redirects=True`` client
leaves open". This module takes the fail-closed half of the same rule, because a streaming
relay that may already have written the caller's first bytes cannot re-enter a fetch loop.

The load-bearing assertion is that the redirect TARGET is never contacted. A test that
only checked the status code could not tell "not followed" from "followed, then ignored",
so the target here is a second real listener that records being hit.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.inbound import capture_proxy as proxy
from tests.test_ea5_capture_proxy import (  # reuse the surface's own harness
    _base_of,
    _enable,
    _proxy_client,
    _token,
)


async def _redirect_target() -> tuple[TestClient, dict]:
    """Stands in for the host a redirect would reach. Records ANY contact."""
    hit: dict = {"count": 0}

    async def _handle(request: web.Request) -> web.Response:
        hit["count"] += 1
        hit["path"] = request.path
        return web.json_response({"leaked": True})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", _handle)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, hit


async def _redirecting_upstream(location: str, status: int = 302) -> TestClient:
    """An allow-listed upstream that answers a redirect to *location*."""

    async def _handle(request: web.Request) -> web.Response:
        raise web.HTTPFound(location) if status == 302 else web.HTTPMovedPermanently(location)

    app = web.Application()
    app.router.add_post("/v1/chat/completions", _handle)
    app.router.add_post("/v1/messages", _handle)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302])
async def test_a_redirect_is_refused_and_the_target_is_never_contacted(monkeypatch, status):
    target, hit = await _redirect_target()
    upstream = await _redirecting_upstream(f"{_base_of(target)}/v1/chat/completions", status=status)
    client = await _proxy_client()
    try:
        _enable(monkeypatch, enabled=True, allowlist=("127.0.0.1",))
        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=b'{"model":"gpt-4o","messages":[],"stream":false}',
            headers={
                "Authorization": f"Bearer {_token()}",
                proxy.UPSTREAM_KEY_HEADER: "client-key",
                proxy.UPSTREAM_BASE_HEADER: _base_of(upstream),
            },
        )
        assert resp.status == 502
        payload = await resp.json()
        assert payload["error"]["code"] == "upstream_redirected", payload
        assert hit["count"] == 0, (
            "the redirect target was CONTACTED — the guard evaluated only the original "
            "host, so following the hop dials a host it never saw"
        )
    finally:
        await client.close()
        await upstream.close()
        await target.close()


@pytest.mark.asyncio
async def test_the_refusal_names_the_hop_so_an_operator_can_fix_the_client_record(monkeypatch):
    # A moved endpoint is the innocent case and by far the common one. Reporting only
    # "upstream failed" would leave the operator with nothing to edit.
    target, _hit = await _redirect_target()
    moved = f"{_base_of(target)}/v1/chat/completions"
    upstream = await _redirecting_upstream(moved)
    client = await _proxy_client()
    try:
        _enable(monkeypatch, enabled=True, allowlist=("127.0.0.1",))
        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=b'{"model":"gpt-4o","messages":[],"stream":false}',
            headers={
                "Authorization": f"Bearer {_token()}",
                proxy.UPSTREAM_KEY_HEADER: "client-key",
                proxy.UPSTREAM_BASE_HEADER: _base_of(upstream),
            },
        )
        payload = await resp.json()
        # `error_extra` merges INSIDE the error object — the envelope's actionable half.
        assert payload["error"]["location"].endswith("/v1/chat/completions"), payload
        assert moved.split("/v1/")[0] in payload["error"]["location"], payload
        assert payload["error"]["recovery_hints"], "a refusal with no way out"
    finally:
        await client.close()
        await upstream.close()
        await target.close()


def test_the_refused_status_set_matches_the_other_egress_path():
    """Both egress paths must recognise the same hop, or one of them has a hole."""
    import inspect

    from personalclaw.net import client as net_client

    src = inspect.getsource(net_client)
    for status in proxy._REDIRECT_STATUSES:
        assert str(status) in src, (
            f"{status} is refused here but net/client.py does not re-evaluate it — "
            "the two egress paths disagree about what a redirect is"
        )
