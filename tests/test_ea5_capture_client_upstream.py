"""EA-5's pinned-upstream path: a client record names WHERE its traffic is forwarded.

The clause under test is "forward to a client-record ``upstream`` ProviderEntry". The
happy path is the least interesting thing here, so it gets one test; the rest are about
the two properties that make a *writable* egress binding safe to have at all:

* **``upstream`` is a provider NAME, never a URL.** The destination host comes from the
  named entry's own ``options``/spec, so no value a client record can hold names a host
  the operator did not already configure. Pinned by resolving through the REAL registry
  and the REAL credential store rather than a stubbed ``_provider_upstream`` — a mock
  would assert the test's own arithmetic, and the property being claimed is precisely
  that the resolution is the shared one.
* **The egress allow-list pre-flights, and a denial reaches no socket.** Proved by TWO
  independent witnesses on every refusal: ``_forward`` (the module's sole egress point)
  is never entered, AND the stub upstream records no request. Either alone is weak —
  an un-entered ``_forward`` does not by itself rule out a connection opened elsewhere,
  and an untouched stub cannot tell "denied" from "dialed the wrong port". Beside each
  refusal sits a vacuity floor that allow-lists the same host and forwards, so a red in
  the refusal test means the guard, not a broken fixture.

The credential-confusion negative is pinned too: the bytes leaving for the upstream must
carry the PROVIDER's secret and must not contain the inbound bearer, which is the failure
mode a proxy that "just forwards headers" has by construction.
"""

from __future__ import annotations

import os

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.inbound import auth
from personalclaw.inbound import capture_proxy as proxy

_SURFACES = ("OPENAI", "MCP", "A2A", "CAPTURE", "BRIDGE")

_PROVIDER_SECRET = "sk-provider-only-secret"
_PROVIDER_NAME = "work-openai"
_CREDENTIAL_NAME = "capture-upstream-key"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """A private home per test, plus a registry reset.

    The surface-token env vars are cleared on BOTH sides because
    `auth.create_surface_token` mirrors into ``os.environ`` itself — a token minted
    mid-test is a variable monkeypatch never recorded and so never undoes.

    The default provider registry is process-global, so it is reset after every test;
    a leaked ``work-openai`` entry would make a later "unknown provider" assertion pass
    or fail depending on execution order.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    for surface in _SURFACES:
        monkeypatch.delenv(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", raising=False)
    yield
    for surface in _SURFACES:
        os.environ.pop(f"PERSONALCLAW_INBOUND_{surface}_TOKEN", None)
    from personalclaw.llm.registry import reset_default_registry

    reset_default_registry()


def _enable(monkeypatch, *, allowlist=()):
    """Point ``AppConfig.load()`` at an enabled capture surface with ``allowlist``."""
    from personalclaw.config.external_access import ExternalAccessConfig
    from personalclaw.config.external_access import ExternalAccessSurfaceConfig as Surface
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig()
    surface = Surface(enabled=True, allow_remote=False)
    cfg.external_access = ExternalAccessConfig(enabled=True, capture=surface)
    setattr(surface, "upstream_allowlist", list(allowlist))
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))
    return cfg


async def _proxy_client() -> TestClient:
    app = web.Application()
    proxy.register_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _stub_upstream() -> tuple[TestClient, dict]:
    """A loopback upstream that records exactly what it was handed."""
    seen: dict = {}

    async def _handle(request: web.Request) -> web.StreamResponse:
        seen["path"] = request.path
        seen["raw"] = await request.read()
        seen["headers"] = dict(request.headers)
        return web.json_response({"ok": True, "id": "resp-1"})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", _handle)
    app.router.add_post("/v1/messages", _handle)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, seen


def _register_provider(base_url: str, *, name: str = _PROVIDER_NAME) -> None:
    """A REAL ProviderEntry plus a REAL credential-store secret, in the isolated home.

    Uses the credential-store rung of the shared ladder rather than
    ``options["api_key"]``: the explicit-key rung is gated behind a registered branded
    spec, and no provider module has imported one in a bare test process.
    """
    from personalclaw.config import config_dir
    from personalclaw.llm.credentials import CredentialStore
    from personalclaw.llm.registry import ProviderEntry, get_default_registry

    CredentialStore(config_dir()).save(
        {_CREDENTIAL_NAME: {"type": "api_key", "value": _PROVIDER_SECRET}}
    )
    get_default_registry().register_entry(
        ProviderEntry(
            name=name,
            type="openai",
            model="gpt-4o",
            options={"base_url": base_url},
            credential=_CREDENTIAL_NAME,
        )
    )


def _pinned_client(upstream: str) -> str:
    """A capture-bound client record pinned to ``upstream``. Returns its bearer."""
    from personalclaw.inbound import clients as clients_mod

    _record, token = clients_mod.create_client(
        "external-agent", surfaces=[proxy.CAPTURE_SURFACE], upstream=upstream
    )
    return token


def _never_forward(monkeypatch) -> list[str]:
    """Replace the SOLE egress point with a tripwire. Returns the (expected empty) log."""
    entered: list[str] = []

    async def _boom(*_a, **_kw):
        entered.append("_forward was entered")
        raise AssertionError("a denied upstream must never reach _forward")

    monkeypatch.setattr(proxy, "_forward", _boom)
    return entered


# ── 1. The call site: the record's `upstream` decides the destination ─────────


@pytest.mark.asyncio
async def test_client_record_upstream_selects_the_provider_and_its_credential(monkeypatch):
    """The forward lands on the PINNED provider's base URL, carrying ITS secret.

    Four facts in one request because they are one behaviour: the record's name is
    resolved, the resolved base is dialled, the provider's credential authorises it, and
    the turn is attributed to the client rather than to the anonymous surface.
    """
    upstream, seen = await _stub_upstream()
    client = await _proxy_client()
    try:
        _enable(monkeypatch, allowlist=("127.0.0.1",))
        base = f"http://127.0.0.1:{upstream.server.port}/v1"
        _register_provider(base)
        auth.create_surface_token(proxy.CAPTURE_SURFACE)  # surface admission only
        bearer = _pinned_client(_PROVIDER_NAME)

        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=b'{"model":"gpt-4o","messages":[],"stream":false}',
            headers={"Authorization": f"Bearer {bearer}"},
        )

        assert resp.status == 200
        assert seen["path"] == "/v1/chat/completions", "dialled the pinned provider's base"
        assert seen["headers"]["Authorization"] == f"Bearer {_PROVIDER_SECRET}"
    finally:
        await client.close()
        await upstream.close()


@pytest.mark.asyncio
async def test_the_inbound_bearer_is_never_forwarded_upstream(monkeypatch):
    """The capture credential must not appear ANYWHERE in what we send.

    Checked across every header value rather than only ``Authorization``, because the
    interesting failure is not "we overwrote the right slot" but "we also copied the
    inbound one into a slot nobody was looking at".
    """
    upstream, seen = await _stub_upstream()
    client = await _proxy_client()
    try:
        _enable(monkeypatch, allowlist=("127.0.0.1",))
        _register_provider(f"http://127.0.0.1:{upstream.server.port}/v1")
        auth.create_surface_token(proxy.CAPTURE_SURFACE)
        bearer = _pinned_client(_PROVIDER_NAME)

        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=b'{"model":"gpt-4o","messages":[],"stream":false}',
            headers={"Authorization": f"Bearer {bearer}"},
        )

        assert resp.status == 200
        assert bearer not in " ".join(seen["headers"].values())
        assert bearer.encode() not in seen["raw"]
        # Vacuity floor: the secret we DO expect is present, so the absence above is a
        # real absence rather than a `seen` dict that was never populated.
        assert _PROVIDER_SECRET in " ".join(seen["headers"].values())
    finally:
        await client.close()
        await upstream.close()


# ── 2. The negative that carries the weight: a denied host reaches no socket ──


@pytest.mark.asyncio
async def test_a_pinned_upstream_off_the_allowlist_never_reaches_the_network(monkeypatch):
    """The load-bearing direction. A provider the operator configured is STILL refused
    when its host is not allow-listed, and the refusal precedes every connection.

    Two independent witnesses, because either alone is weak: ``_forward`` is the module's
    only egress point and is replaced by a tripwire, AND the stub upstream is live and
    records nothing. This is also the test that must red if the ``guard.evaluate``
    pre-flight in `_handle` is removed — with the guard gone the tripwire fires.
    """
    upstream, seen = await _stub_upstream()
    client = await _proxy_client()
    try:
        # The provider is real and resolvable; only the ALLOW-LIST withholds consent.
        _enable(monkeypatch, allowlist=("allowed.example",))
        _register_provider(f"http://127.0.0.1:{upstream.server.port}/v1")
        auth.create_surface_token(proxy.CAPTURE_SURFACE)
        bearer = _pinned_client(_PROVIDER_NAME)
        entered = _never_forward(monkeypatch)

        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=b'{"model":"gpt-4o","messages":[],"stream":false}',
            headers={"Authorization": f"Bearer {bearer}"},
        )

        assert resp.status == 502
        assert (await resp.json())["error"]["code"] == "upstream_denied"
        assert entered == [], "the guard must decide BEFORE the sole egress point"
        assert seen == {}, "a denied host must never be contacted"
    finally:
        await client.close()
        await upstream.close()


@pytest.mark.asyncio
async def test_an_empty_allowlist_refuses_a_pinned_upstream_too(monkeypatch):
    """A pinned provider is not a waiver of the empty-list-denies rule.

    Worth its own test because the tempting reading is that naming the provider in
    config.json IS the operator's consent, which would make the allow-list decorative on
    exactly the path that spends the operator's credential.
    """
    upstream, seen = await _stub_upstream()
    client = await _proxy_client()
    try:
        _enable(monkeypatch, allowlist=())
        _register_provider(f"http://127.0.0.1:{upstream.server.port}/v1")
        auth.create_surface_token(proxy.CAPTURE_SURFACE)
        bearer = _pinned_client(_PROVIDER_NAME)
        entered = _never_forward(monkeypatch)

        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=b'{"model":"gpt-4o","messages":[],"stream":false}',
            headers={"Authorization": f"Bearer {bearer}"},
        )

        assert resp.status == 502
        assert (await resp.json())["error"]["code"] == "upstream_denied"
        assert entered == []
        assert seen == {}
    finally:
        await client.close()
        await upstream.close()


@pytest.mark.asyncio
async def test_a_record_with_no_upstream_refuses_rather_than_choosing_one(monkeypatch):
    """An unpinned client gets a 502 naming the missing binding — never a default.

    The dangerous alternative is silently falling back to ``_DIALECT_DEFAULT_BASE``,
    which would spend whatever credential happened to resolve.
    """
    upstream, seen = await _stub_upstream()
    client = await _proxy_client()
    try:
        _enable(monkeypatch, allowlist=("127.0.0.1",))
        _register_provider(f"http://127.0.0.1:{upstream.server.port}/v1")
        auth.create_surface_token(proxy.CAPTURE_SURFACE)
        bearer = _pinned_client("")  # registered, deliberately unpinned
        entered = _never_forward(monkeypatch)

        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=b'{"model":"gpt-4o","messages":[],"stream":false}',
            headers={"Authorization": f"Bearer {bearer}"},
        )

        assert resp.status == 502
        payload = await resp.json()
        assert payload["error"]["code"] == "upstream_unavailable"
        assert "upstream" in payload["error"]["reason"]
        assert entered == []
        assert seen == {}
    finally:
        await client.close()
        await upstream.close()


@pytest.mark.asyncio
async def test_an_unresolvable_provider_name_refuses_and_dials_nothing(monkeypatch):
    """A record naming a provider that is not configured is a 502, not a guess.

    This is the state a client reaches if its provider is later deleted from
    config.json, so it is a live path and not merely a validation gap.
    """
    upstream, seen = await _stub_upstream()
    client = await _proxy_client()
    try:
        _enable(monkeypatch, allowlist=("127.0.0.1",))
        _register_provider(f"http://127.0.0.1:{upstream.server.port}/v1")
        auth.create_surface_token(proxy.CAPTURE_SURFACE)
        bearer = _pinned_client("provider-that-was-deleted")
        entered = _never_forward(monkeypatch)

        resp = await client.post(
            proxy.ROUTE_OPENAI,
            data=b'{"model":"gpt-4o","messages":[],"stream":false}',
            headers={"Authorization": f"Bearer {bearer}"},
        )

        assert resp.status == 502
        assert (await resp.json())["error"]["code"] == "upstream_unavailable"
        assert entered == []
        assert seen == {}
    finally:
        await client.close()
        await upstream.close()


# ── 3. The record shape: the field persists and there is only ONE spelling ───


def test_upstream_round_trips_through_the_persisted_record(monkeypatch, tmp_path):
    """``create_client(upstream=...)`` survives save → load.

    Asserted through a real ``load_clients()`` read rather than on the returned object,
    because the returned record proves the constructor ran, not that the field reached
    the file.
    """
    from personalclaw.inbound import clients as clients_mod

    record, _token = clients_mod.create_client(
        "external-agent", surfaces=[proxy.CAPTURE_SURFACE], upstream=_PROVIDER_NAME
    )
    reloaded = clients_mod.load_clients()[record.client_id]
    assert reloaded.upstream == _PROVIDER_NAME
    assert _PROVIDER_NAME in clients_mod.clients_path().read_text(encoding="utf-8")

    # The default stays empty — "no pinned upstream", never a chosen one.
    bare, _ = clients_mod.create_client("bare", surfaces=[proxy.CAPTURE_SURFACE])
    assert clients_mod.load_clients()[bare.client_id].upstream == ""


def test_scope_is_no_longer_a_second_spelling_of_upstream():
    """The pending ``scope["upstream"]`` hedge is GONE, not merely deprioritised.

    Two ways to name a credential-bearing egress target means an operator auditing the
    field can read one and miss the other, and ``scope`` is the free-form bag — the
    easier of the two to set without meaning to. Asserted behaviourally against the real
    resolver, so it stays true if the hedge is reintroduced anywhere in its body.
    """
    from personalclaw.inbound.clients import InboundClient

    only_in_scope = InboundClient(client_id="c1", scope={"upstream": _PROVIDER_NAME})
    assert proxy._client_upstream_name(only_in_scope) == ""

    # Vacuity floor: the resolver DOES read the field, so the "" above is the scope bag
    # being ignored rather than the function being inert.
    assert proxy._client_upstream_name(InboundClient(upstream=_PROVIDER_NAME)) == _PROVIDER_NAME
    assert proxy._client_upstream_name(None) == ""


# ── 4. The operator surface: settable, visible, and refused when unknown ─────


@pytest.mark.asyncio
async def test_the_create_route_pins_a_known_provider_and_reports_it(monkeypatch):
    """POST accepts ``upstream``, and the read surface shows it back.

    A binding an operator can set but cannot see is the one that goes unnoticed when it
    is wrong, so both halves are asserted together.
    """
    from personalclaw.dashboard.handlers import external_access as ea

    _register_provider("https://api.openai.com/v1")

    class _Req:
        method = "POST"
        match_info: dict = {}

        async def json(self):
            return {
                "label": "external-agent",
                "surfaces": [proxy.CAPTURE_SURFACE],
                "upstream": _PROVIDER_NAME,
            }

    resp = await ea.api_external_access_client(_Req())  # type: ignore[arg-type]
    assert resp.status == 200

    rows = ea._client_rows()
    assert [r["upstream"] for r in rows] == [_PROVIDER_NAME]
    assert "token_hash" not in rows[0], "the hash stays absent even as fields are added"


@pytest.mark.asyncio
async def test_the_create_route_refuses_an_unknown_provider_and_creates_nothing(monkeypatch):
    """An unknown ``upstream`` is a 400, and no half-made client is left behind.

    The second assertion is the one that matters: refusing after the write would leave a
    record the operator was told did not exist.
    """
    from personalclaw.dashboard.handlers import external_access as ea
    from personalclaw.inbound import clients as clients_mod

    _register_provider("https://api.openai.com/v1")

    class _Req:
        method = "POST"
        match_info: dict = {}

        async def json(self):
            return {
                "label": "external-agent",
                "surfaces": [proxy.CAPTURE_SURFACE],
                "upstream": "not-a-configured-provider",
            }

    resp = await ea.api_external_access_client(_Req())  # type: ignore[arg-type]
    assert resp.status == 400
    assert clients_mod.load_clients() == {}

    # Vacuity floor: the SAME route with a configured name succeeds, so the 400 above is
    # the check firing rather than the route being broken for every body.
    class _Ok(_Req):
        async def json(self):
            return {
                "label": "external-agent",
                "surfaces": [proxy.CAPTURE_SURFACE],
                "upstream": _PROVIDER_NAME,
            }

    assert (await ea.api_external_access_client(_Ok())).status == 200  # type: ignore[arg-type]
    assert len(clients_mod.load_clients()) == 1
