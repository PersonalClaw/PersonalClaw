"""Tests for the external-agent capture proxy's TRANSPORT half (EXTERNAL-ACCESS §7.1).

Three properties carry the security weight here, so most of these tests are about
refusals and about ORDER rather than about outcomes:

* **Loopback forever.** ``allow_remote`` is set True in the refusal test on purpose —
  the refusal must stand, because capture never reads that field at all.
* **The guard pre-flights before any connection.** Asserted by proving the sole egress
  function was never entered on a deny, not merely that the response was a 502. A guard
  that ran after connecting would produce the identical status code.
* **An empty allow-list refuses.** With a vacuity floor beside it (an allow-listed host
  IS permitted) so the refusal is the allow-list working rather than everything being
  broken.

The remaining tests pin the latency contract: bytes reach the caller before recording
starts, and a recorder that fails cannot cost the caller its response.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.inbound import auth
from personalclaw.inbound import capture_proxy as proxy

_SURFACES = ("OPENAI", "MCP", "A2A", "CAPTURE", "BRIDGE")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """A private home per test, and no surface token leaking between them.

    Cleared on BOTH sides: `create_surface_token` mirrors into ``os.environ`` itself, so
    a token minted mid-test is a variable monkeypatch never recorded and never undoes —
    it would read as "this surface has a valid token" in every later test in this worker.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    for surface in _SURFACES:
        monkeypatch.delenv(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", raising=False)
    yield
    for surface in _SURFACES:
        os.environ.pop(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", None)
    from personalclaw.llm.registry import reset_default_registry

    reset_default_registry()


def _enable(
    monkeypatch,
    *,
    enabled=True,
    allow_remote=False,
    allowlist=(),
    master=True,
    nested=False,
):
    """Point ``AppConfig.load()`` at an external-access config without writing config.json.

    ``nested`` selects which spelling of the allow-list is present — the
    ``capture.upstream_allowlist`` §7.1 describes, or the flat
    ``capture_upstream_allowlist`` the neighbouring ``capture_retention_days`` follows.
    Both are exercised because this module reads whichever exists and neither field is
    owned here.
    """
    from personalclaw.config.loader import AppConfig, ExternalAccessConfig
    from personalclaw.config.loader import ExternalAccessSurfaceConfig as Surface

    cfg = AppConfig()
    surface = Surface(enabled=enabled, allow_remote=allow_remote)
    cfg.external_access = ExternalAccessConfig(enabled=master, capture=surface)
    if nested:
        setattr(surface, "upstream_allowlist", list(allowlist))
    else:
        setattr(cfg.external_access, "capture_upstream_allowlist", list(allowlist))
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))
    return cfg


async def _proxy_client() -> TestClient:
    app = web.Application()
    proxy.register_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _stub_upstream(*, sse=False) -> tuple[TestClient, dict]:
    """A local upstream that records what it was handed. Loopback, so no DNS at all.

    Returned as a client rather than a bare port so its lifecycle is the test's.
    """
    seen: dict = {}

    async def _handle(request: web.Request) -> web.StreamResponse:
        seen["path"] = request.path
        seen["raw"] = await request.read()
        seen["headers"] = dict(request.headers)
        if not sse:
            return web.json_response({"ok": True, "id": "resp-1"})
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'data: {"delta":"a"}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_post("/v1/chat/completions", _handle)
    app.router.add_post("/v1/messages", _handle)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, seen


def _base_of(upstream: TestClient) -> str:
    return f"http://127.0.0.1:{upstream.server.port}/v1"


def _token() -> str:
    return auth.create_surface_token(proxy.CAPTURE_SURFACE)


# ── 1. A disabled surface is 404, and refuses before the body is read ─────────


@pytest.mark.asyncio
async def test_disabled_surface_is_404_without_reading_the_body(monkeypatch):
    """404 rather than 403 so an off surface does not confirm its own existence, and the
    refusal lands before the prompt is consumed — an off surface that reads a prompt has
    already handled the data it was turned off to stop handling."""
    _enable(monkeypatch, enabled=False)
    token = _token()

    def _boom(self):  # noqa: ANN001 — patching aiohttp's Request.read
        raise AssertionError("the request body was read")

    monkeypatch.setattr(web.Request, "read", _boom, raising=True)
    client = await _proxy_client()
    try:
        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=json.dumps({"model": "gpt-4o", "messages": []}),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 404
        assert (await resp.json())["error"] == "not available"

        # Vacuity floor for the patch above: an ADMITTED request does reach the read, so
        # the 404 genuinely skipped it rather than the patch being inert.
        _enable(monkeypatch, enabled=True, allowlist=("example.invalid",))
        admitted = await client.post(
            proxy.ROUTE_OPENAI,
            data=json.dumps({"model": "gpt-4o", "messages": []}),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert admitted.status == 500
    finally:
        await client.close()


# ── 2. Loopback forever — allow_remote is never consulted ────────────────────


@pytest.mark.asyncio
async def test_non_loopback_is_refused_even_with_a_correct_token(monkeypatch):
    """A correct bearer AND ``allow_remote=True`` must still be refused.

    ``allow_remote`` is set here deliberately: the assertion is not "remote is off", it
    is "capture has no remote mode for a setting to turn on". If a future edit routed
    this through `auth.peer_allowed`'s remote branch, this test is what goes red.
    """
    _enable(monkeypatch, enabled=True, allow_remote=True, allowlist=("example.invalid",))
    token = _token()
    monkeypatch.setattr(auth, "_peer_host", lambda request: "203.0.113.9")
    client = await _proxy_client()
    try:
        for route in (proxy.ROUTE_OPENAI, proxy.ROUTE_ANTHROPIC):
            resp = await client.post(
                route,
                data=json.dumps({"model": "m", "messages": []}),
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status == 403, route
            assert (await resp.json())["error"] == "forbidden"
    finally:
        await client.close()


# ── 3. Loopback + bad/absent bearer → 401 ────────────────────────────────────


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not-the-token"}, {"Authorization": "Bearer "}],
    ids=["absent", "wrong", "empty"],
)
@pytest.mark.asyncio
async def test_loopback_with_a_bad_or_absent_bearer_is_401(monkeypatch, headers):
    _enable(monkeypatch, enabled=True, allowlist=("example.invalid",))
    _token()  # a valid token EXISTS; the caller just isn't presenting it
    client = await _proxy_client()
    try:
        resp = await client.post(
            proxy.ROUTE_OPENAI, data=json.dumps({"model": "m"}), headers=headers
        )
        assert resp.status == 401
        assert (await resp.json())["error"] == "unauthorized"
    finally:
        await client.close()


# ── 4. A fully-admitted request forwards VERBATIM ────────────────────────────


@pytest.mark.asyncio
async def test_admitted_request_forwards_the_body_verbatim(monkeypatch):
    """Byte-identical, not merely semantically equal.

    The proxy parses the body to read ``stream``/``model``, so the risk this pins is a
    re-serialisation: key order, spacing and duplicate keys all survive because the bytes
    forwarded are the bytes read.
    """
    upstream, seen = await _stub_upstream()
    client = await _proxy_client()
    try:
        _enable(monkeypatch, enabled=True, allowlist=("127.0.0.1",))
        token = _token()
        # Deliberately odd formatting: whitespace and key order a re-serialiser would lose.
        raw = b'{"model":"gpt-4o",  "messages":[{"role":"user","content":"hi"}],"stream":false}'
        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=raw,
            headers={
                "Authorization": f"Bearer {token}",
                proxy.UPSTREAM_KEY_HEADER: "client-key",
                proxy.UPSTREAM_BASE_HEADER: _base_of(upstream),
            },
        )
        assert resp.status == 200
        assert seen["raw"] == raw
        assert seen["path"] == "/v1/chat/completions"
        assert (await resp.json()) == {"ok": True, "id": "resp-1"}
    finally:
        await client.close()
        await upstream.close()


@pytest.mark.asyncio
async def test_anthropic_dialect_forwards_to_messages(monkeypatch):
    upstream, seen = await _stub_upstream()
    client = await _proxy_client()
    try:
        _enable(monkeypatch, enabled=True, allowlist=("127.0.0.1",))
        token = _token()
        raw = b'{"model":"claude-3","messages":[]}'
        resp = await client.post(
            proxy.ROUTE_ANTHROPIC,
            data=raw,
            headers={
                "Authorization": f"Bearer {token}",
                proxy.UPSTREAM_KEY_HEADER: "client-key",
                proxy.UPSTREAM_BASE_HEADER: _base_of(upstream),
            },
        )
        assert resp.status == 200
        assert seen["raw"] == raw
        assert seen["path"] == "/v1/messages"
        # Anthropic's dialect authenticates with x-api-key, not a bearer.
        assert seen["headers"]["x-api-key"] == "client-key"
        assert "Authorization" not in seen["headers"]
        assert seen["headers"]["anthropic-version"]
    finally:
        await client.close()
        await upstream.close()


# ── 5. The guard pre-flight runs BEFORE any connection ───────────────────────


@pytest.mark.asyncio
async def test_guard_preflight_runs_before_any_connection(monkeypatch):
    """Ordering, not just outcome.

    A guard that ran *after* connecting would return the same 502. So this asserts the
    sole egress function was never entered, and that the guard WAS consulted — together
    those two facts are the ordering claim.
    """
    _enable(monkeypatch, enabled=True, allowlist=("allowed.example",))
    token = _token()

    contacted: list[str] = []

    async def _never(*args, **kwargs):
        contacted.append("upstream was dialed")
        raise AssertionError("a denied host must never be contacted")

    consulted: list[str] = []
    real_evaluate = proxy.evaluate

    def _spy(url, policy, **kw):
        consulted.append(url)
        return real_evaluate(url, policy, **kw)

    monkeypatch.setattr(proxy, "_forward", _never)
    monkeypatch.setattr(proxy, "evaluate", _spy)

    client = await _proxy_client()
    try:
        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=json.dumps({"model": "m"}),
            headers={
                "Authorization": f"Bearer {token}",
                proxy.UPSTREAM_KEY_HEADER: "client-key",
                proxy.UPSTREAM_BASE_HEADER: "https://denied.example/v1",
            },
        )
        assert resp.status == 502
        payload = await resp.json()
        assert payload["error"] == "upstream denied"
        assert "allow-list" in payload["reason"]
        assert consulted == ["https://denied.example/v1/chat/completions"]
        assert contacted == []
    finally:
        await client.close()


# ── 6. An empty allow-list refuses — with a vacuity floor beside it ──────────


@pytest.mark.parametrize("nested", [False, True], ids=["flat-field", "nested-field"])
@pytest.mark.asyncio
async def test_empty_allowlist_refuses_every_upstream(monkeypatch, nested):
    """Fail-CLOSED is the correct direction for an egress allow-list.

    "The operator has named no upstream host" must mean "there is nowhere to go", not
    "anywhere is fine". A permissive empty list would make this route an open relay that
    spends the operator's credential on whatever host a local agent names.
    """
    _enable(monkeypatch, enabled=True, allowlist=(), nested=nested)
    token = _token()
    upstream, seen = await _stub_upstream()
    client = await _proxy_client()
    try:
        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=json.dumps({"model": "m"}),
            headers={
                "Authorization": f"Bearer {token}",
                proxy.UPSTREAM_KEY_HEADER: "client-key",
                proxy.UPSTREAM_BASE_HEADER: _base_of(upstream),
            },
        )
        assert resp.status == 502
        assert (await resp.json())["error"] == "upstream denied"
        assert seen == {}, "an empty allow-list must not reach the upstream at all"
    finally:
        await client.close()
        await upstream.close()


@pytest.mark.parametrize("nested", [False, True], ids=["flat-field", "nested-field"])
@pytest.mark.asyncio
async def test_allowlisted_host_is_permitted(monkeypatch, nested):
    """The vacuity floor for the test above: the SAME host, once named, is reached.

    Without this, an empty-list refusal is consistent with the proxy being broken for
    every host under every configuration.
    """
    upstream, seen = await _stub_upstream()
    client = await _proxy_client()
    try:
        _enable(monkeypatch, enabled=True, allowlist=("127.0.0.1",), nested=nested)
        token = _token()
        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=json.dumps({"model": "m"}),
            headers={
                "Authorization": f"Bearer {token}",
                proxy.UPSTREAM_KEY_HEADER: "client-key",
                proxy.UPSTREAM_BASE_HEADER: _base_of(upstream),
            },
        )
        assert resp.status == 200
        assert seen["path"] == "/v1/chat/completions"
    finally:
        await client.close()
        await upstream.close()


# ── 7. Streaming: bytes first, recording after ───────────────────────────────


@pytest.mark.asyncio
async def test_sse_reaches_the_caller_before_recording_completes(monkeypatch):
    """Stream-first, and a deliberately slow recorder proves it.

    The recorder blocks on an Event the test controls. If recording were on the hot path
    the response would never complete, so the assertion "the caller has all its bytes
    while the recorder is still pending" is exactly the §7.1 latency contract.
    """
    upstream, _seen = await _stub_upstream(sse=True)
    client = await _proxy_client()
    release = asyncio.Event()
    recorded: list[dict] = []

    async def _slow(**kwargs):
        await release.wait()
        recorded.append(kwargs)
        return "session-1"

    monkeypatch.setattr(proxy, "_recorder", lambda: _slow)
    try:
        _enable(monkeypatch, enabled=True, allowlist=("127.0.0.1",))
        token = _token()
        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=json.dumps({"model": "gpt-4o", "stream": True}),
            headers={
                "Authorization": f"Bearer {token}",
                proxy.UPSTREAM_KEY_HEADER: "client-key",
                proxy.UPSTREAM_BASE_HEADER: _base_of(upstream),
            },
        )
        assert resp.status == 200
        body = await resp.read()
        assert b'data: {"delta":"a"}' in body
        assert b"data: [DONE]" in body
        # The caller has EVERY byte while the recorder is still blocked.
        assert recorded == []

        release.set()
        await proxy.drain_recordings()
        assert len(recorded) == 1
        turn = recorded[0]
        assert turn["dialect"] == proxy.DIALECT_OPENAI
        assert turn["model_requested"] == "gpt-4o"
        assert 'data: {"delta":"a"}' in turn["stream_text"]
    finally:
        release.set()
        await proxy.drain_recordings()
        await client.close()
        await upstream.close()


@pytest.mark.asyncio
async def test_non_stream_turn_is_recorded_with_both_bodies(monkeypatch):
    upstream, _seen = await _stub_upstream()
    client = await _proxy_client()
    recorded: list[dict] = []

    async def _rec(**kwargs):
        recorded.append(kwargs)
        return "session-2"

    monkeypatch.setattr(proxy, "_recorder", lambda: _rec)
    try:
        _enable(monkeypatch, enabled=True, allowlist=("127.0.0.1",))
        token = _token()
        resp = await client.post(
            proxy.ROUTE_ANTHROPIC,
            data=json.dumps({"model": "claude-3", "messages": [{"role": "user"}]}),
            headers={
                "Authorization": f"Bearer {token}",
                proxy.UPSTREAM_KEY_HEADER: "client-key",
                proxy.UPSTREAM_BASE_HEADER: _base_of(upstream),
            },
        )
        assert resp.status == 200
        await proxy.drain_recordings()
        assert len(recorded) == 1
        turn = recorded[0]
        assert turn["dialect"] == proxy.DIALECT_ANTHROPIC
        assert turn["request_body"]["model"] == "claude-3"
        assert turn["response_body"] == {"ok": True, "id": "resp-1"}
        assert turn["stream_text"] == ""
        assert turn["latency_ms"] >= 0
    finally:
        await proxy.drain_recordings()
        await client.close()
        await upstream.close()


# ── 8. Passthrough uses the client's key; the operator's never leaves ────────


def _register_operator_provider(monkeypatch, base: str, secret: str) -> str:
    """A ProviderEntry whose credential resolves through the SHARED ladder.

    ``options["api_key"]`` is hop 2 of `sdk.provider_helpers`' order — the per-instance
    key the Add-Provider flow persists — so this exercises the real resolver rather than
    a shortcut the proxy invented.
    """
    from personalclaw.llm.registry import ProviderEntry, get_default_registry

    # Via `sdk.model`, the stable re-export apps use. Importing `BrandedProviderSpec`
    # straight from `provider_helpers` trips the circular import that module's own
    # "imported LAST" comment describes.
    from personalclaw.sdk.model import BrandedProviderSpec
    from personalclaw.sdk.provider_helpers import _REGISTERED_SPECS

    spec = BrandedProviderSpec(type="stubprov", default_base_url=base)
    monkeypatch.setitem(_REGISTERED_SPECS, "stubprov", spec)
    get_default_registry().register_entry(
        ProviderEntry(
            name="op-provider",
            type="stubprov",
            model="m",
            options={"api_key": secret, "base_url": base},
        )
    )
    return "op-provider"


class _FakeClient:
    """The minimum of an `InboundClient` this surface reads: an id and an upstream."""

    def __init__(self, upstream: str) -> None:
        self.client_id = "agent-7"
        self.upstream = upstream
        self.scope: dict = {}


@pytest.mark.asyncio
async def test_provider_mode_uses_the_operators_key(monkeypatch):
    """The vacuity floor for the passthrough test below.

    Without it, "the operator's key never went upstream" is also satisfied by a proxy
    that could never resolve an operator key at all.
    """
    upstream, seen = await _stub_upstream()
    client = await _proxy_client()
    try:
        _enable(monkeypatch, enabled=True, allowlist=("127.0.0.1",))
        token = _token()
        name = _register_operator_provider(monkeypatch, _base_of(upstream), "OPERATOR-SECRET")
        monkeypatch.setattr(proxy, "_lookup_client", lambda presented: (_FakeClient(name), ""))
        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=json.dumps({"model": "m"}),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200
        assert seen["headers"]["Authorization"] == "Bearer OPERATOR-SECRET"
    finally:
        await client.close()
        await upstream.close()


@pytest.mark.asyncio
async def test_passthrough_uses_the_client_key_and_never_the_operators(monkeypatch):
    """Passthrough is EXCLUSIVE: the operator's credential is not merely unsent, it is
    never resolved. Asserted against every header and the body, because "we didn't put
    it in the auth header" is a weaker claim than "it is nowhere in the request"."""
    upstream, seen = await _stub_upstream()
    client = await _proxy_client()
    try:
        _enable(monkeypatch, enabled=True, allowlist=("127.0.0.1",))
        token = _token()
        name = _register_operator_provider(monkeypatch, _base_of(upstream), "OPERATOR-SECRET")
        monkeypatch.setattr(proxy, "_lookup_client", lambda presented: (_FakeClient(name), ""))
        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=json.dumps({"model": "m"}),
            headers={
                "Authorization": f"Bearer {token}",
                proxy.UPSTREAM_KEY_HEADER: "CLIENT-OWN-KEY",
                proxy.UPSTREAM_BASE_HEADER: _base_of(upstream),
            },
        )
        assert resp.status == 200
        assert seen["headers"]["Authorization"] == "Bearer CLIENT-OWN-KEY"
        blob = json.dumps(seen["headers"]) + seen["raw"].decode("utf-8")
        assert "OPERATOR-SECRET" not in blob
        # Nor does the capture surface token itself ever travel upstream.
        assert token not in blob
    finally:
        await client.close()
        await upstream.close()


@pytest.mark.asyncio
async def test_no_bound_upstream_and_no_passthrough_refuses(monkeypatch):
    """Fail loud rather than picking a provider. A silently-chosen upstream would spend a
    credential the caller never named."""
    _enable(monkeypatch, enabled=True, allowlist=("127.0.0.1",))
    token = _token()
    client = await _proxy_client()
    try:
        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=json.dumps({"model": "m"}),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 502
        payload = await resp.json()
        assert payload["error"] == "upstream unavailable"
        assert "no upstream is bound" in payload["reason"]
    finally:
        await client.close()


# ── 9. A failing recorder cannot cost the caller its response ────────────────


@pytest.mark.asyncio
async def test_a_raising_recorder_does_not_break_the_response(monkeypatch):
    upstream, _seen = await _stub_upstream()
    client = await _proxy_client()

    async def _boom(**kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(proxy, "_recorder", lambda: _boom)
    try:
        _enable(monkeypatch, enabled=True, allowlist=("127.0.0.1",))
        token = _token()
        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=json.dumps({"model": "m"}),
            headers={
                "Authorization": f"Bearer {token}",
                proxy.UPSTREAM_KEY_HEADER: "client-key",
                proxy.UPSTREAM_BASE_HEADER: _base_of(upstream),
            },
        )
        assert resp.status == 200
        assert (await resp.json()) == {"ok": True, "id": "resp-1"}
        await proxy.drain_recordings()  # must not propagate the RuntimeError
    finally:
        await client.close()
        await upstream.close()


@pytest.mark.asyncio
async def test_a_missing_capture_store_still_forwards(monkeypatch):
    """Ordering resilience (§7.1): the transport and the store ship independently, so a
    store that has not landed yet must cost a warning, not the response."""
    upstream, seen = await _stub_upstream()
    client = await _proxy_client()
    monkeypatch.setattr(proxy, "_recorder", lambda: None)
    try:
        _enable(monkeypatch, enabled=True, allowlist=("127.0.0.1",))
        token = _token()
        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=json.dumps({"model": "m"}),
            headers={
                "Authorization": f"Bearer {token}",
                proxy.UPSTREAM_KEY_HEADER: "client-key",
                proxy.UPSTREAM_BASE_HEADER: _base_of(upstream),
            },
        )
        assert resp.status == 200
        assert seen["path"] == "/v1/chat/completions"
        await proxy.drain_recordings()
    finally:
        await client.close()
        await upstream.close()


# ── Routes are registered, and both are literal ──────────────────────────────


def test_both_routes_register_as_literal_posts():
    """Literal paths cannot be shadowed by the ``{...}`` patterns in
    `dashboard/server.py`; this pins that they stay literal."""
    app = web.Application()
    proxy.register_routes(app)
    posts = {
        r.resource.canonical
        for r in app.router.routes()
        if r.method == "POST" and r.resource is not None
    }
    assert proxy.ROUTE_OPENAI in posts
    assert proxy.ROUTE_ANTHROPIC in posts
    assert "{" not in proxy.ROUTE_OPENAI + proxy.ROUTE_ANTHROPIC
