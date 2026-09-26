"""Settings → Providers: a model instance's state is MEASURED, and a failure says what failed.

Measured on a live gateway before this change:

* a keyless OpenAI instance read "✓ Configured" beside its own Test saying "No API key or
  endpoint configured" — the badge was derived from a credential's PRESENCE;
* an instance whose key the vendor had rejected tested as "No models returned (check
  key/endpoint)" — the 401 was swallowed by the fail-soft listing on the way;
* Settings → Models still offered that instance's models, re-sending the rejected key on every
  load (one validation log held 51 identical 401 warnings);
* a malformed endpoint was saved, and only failed later as ``not%20a%20url/api/tags``;
* an Ollama nothing was listening at read "No downloadable models listed.", and its Test said
  ``[Errno 61] Connect call failed``;
* in the container image, a refused ``localhost`` endpoint gave no hint that localhost there
  is the container itself.

Everything here drives real routes against real loopback servers; the only stand-in is the
provider APP (a catalog shaped like the apps repo's ``openai-models``), because core ships
none of those apps.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.config import loader as config_loader
from personalclaw.llm.catalog import (
    ConnectionResult,
    ModelCatalog,
    ModelDiscoveryError,
    openai_compatible_list_models,
)

VENDOR_TYPE = "vendor-models"
GOOD_KEY = "sk-good-NOT-A-REAL-KEY"


# ── a real loopback vendor ──────────────────────────────────────────────────────


class _Vendor:
    """An OpenAI-shaped ``/v1/models`` that answers 200 for ``GOOD_KEY`` and 401 otherwise.

    ``hits`` counts every request, which is how "the key is not sent again" is measured.
    """

    def __init__(self) -> None:
        self.hits = 0
        outer = self

        class _H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
                outer.hits += 1
                if self.path != "/v1/models":
                    return outer._write(self, 404, {"error": {"message": "not found"}})
                if self.headers.get("Authorization") != f"Bearer {GOOD_KEY}":
                    return outer._write(self, 401, {"error": {"message": "invalid api key"}})
                outer._write(self, 200, {"object": "list", "data": [{"id": "vendor-chat-1"}]})

            def log_message(self, *args: object) -> None:
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()

    @staticmethod
    def _write(handler: BaseHTTPRequestHandler, status: int, body: Any) -> None:
        raw = json.dumps(body).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}/v1"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()
        self._thread.join(timeout=5)


class _VendorCatalog(ModelCatalog):
    """Shaped like the apps repo's ``openai-models`` catalog: it lists through core's
    fail-soft discovery, and its connection test reports an empty list as the failure."""

    def __init__(self, options: dict[str, Any], *, model: str = "") -> None:
        self._endpoint = str(options.get("endpoint") or "")
        self._key = str(options.get("api_key") or "")

    async def list_models(self):  # type: ignore[override]
        return await openai_compatible_list_models(self._endpoint, self._key)

    async def test_connection(self) -> ConnectionResult:
        models = await self.list_models()
        if not models:
            return ConnectionResult(ok=False, detail="No models returned (check key/endpoint)")
        return ConnectionResult(ok=True, model_count=len(models))


@pytest.fixture()
def allow_loopback_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery against loopback, as an operator who set ``allow_private`` sees it. Only the
    POLICY is substituted; the real fetch, guard and host classification all still run."""
    from personalclaw.net import CONNECTOR

    monkeypatch.setattr(
        "personalclaw.sdk.net.egress_policy_for",
        lambda base: CONNECTOR.with_overrides(allow_private=True),
    )


@pytest.fixture()
def vendor(allow_loopback_egress: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Vendor]:
    """The vendor server, and a private LLM registry in which the vendor's app is installed."""
    from personalclaw.dashboard.handlers import providers as model_providers
    from personalclaw.llm import registry as llm_registry
    from personalclaw.llm.capabilities import Capability, ProviderCapability

    registry = llm_registry.ProviderRegistry()
    # What an installed model app registers on import: its inference type, and its catalog.
    registry.register_type(
        ProviderCapability(
            type=VENDOR_TYPE,
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=0,
        ),
        lambda **kw: None,
    )
    registry.register_catalog(VENDOR_TYPE, _VendorCatalog)
    monkeypatch.setattr(llm_registry, "get_default_registry", lambda: registry)
    # The typed media registries are process-wide; a config write only re-reads them.
    monkeypatch.setattr(model_providers, "_refresh_media_registries", lambda: None)
    srv = _Vendor()
    try:
        yield srv
    finally:
        srv.close()


def _app() -> web.Application:
    from personalclaw.dashboard.handlers import model_registry
    from personalclaw.dashboard.handlers import providers as H
    from personalclaw.dashboard.handlers.doctor import api_degraded
    from personalclaw.dashboard.handlers_system import api_onboarding

    app = web.Application()
    app.router.add_get("/api/model-providers", H.api_providers_list)
    app.router.add_post("/api/model-providers", H.api_provider_create)
    app.router.add_put("/api/model-providers/{name}", H.api_provider_update)
    app.router.add_post("/api/model-providers/{name}/test", H.api_provider_test)
    app.router.add_get("/api/onboarding", api_onboarding)
    app.router.add_get("/api/resilience/degraded", api_degraded)
    model_registry.register_model_registry_routes(app)
    return app


@asynccontextmanager
async def _client() -> AsyncIterator[TestClient]:
    async with TestClient(TestServer(_app())) as client:
        yield client


async def _create(client: TestClient, name: str, **options: str) -> None:
    r = await client.post(
        "/api/model-providers", json={"name": name, "type": VENDOR_TYPE, "options": options}
    )
    assert r.status == 200, await r.text()


async def _row(client: TestClient, name: str) -> dict[str, Any]:
    body = await (await client.get("/api/model-providers")).json()
    return next(p for p in body["providers"] if p["name"] == name)


async def _settled_row(client: TestClient, name: str, within: float = 20.0) -> dict[str, Any]:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        row = await _row(client, name)
        if row["connection"]["state"] != "checking":
            return row
        await asyncio.sleep(0.1)
    raise AssertionError(f"{name}'s connection never settled within {within:.0f} s")


def _stored_providers() -> list[dict[str, Any]]:
    path = config_loader.config_dir() / "config.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("providers", [])


# ── the badge is a measurement ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_keyless_instance_is_measured_not_badged_configured(vendor: _Vendor) -> None:
    async with _client() as client:
        await _create(client, "keyless", endpoint=vendor.endpoint)
        row = await _row(client, "keyless")
        assert (
            row.get("credential_status") != "ok"
        ), "an instance with no key anywhere was reported as having its credential"
        assert row["connection"]["state"] == "checking", row
        row = await _settled_row(client, "keyless")
    assert row["connection"]["state"] == "failed"
    assert "401" in row["connection"]["detail"], row["connection"]


@pytest.mark.asyncio
async def test_testing_an_instance_whose_key_is_rejected_says_so(vendor: _Vendor) -> None:
    async with _client() as client:
        await _create(client, "revoked", endpoint=vendor.endpoint, api_key="sk-revoked")
        body = await (await client.post("/api/model-providers/revoked/test")).json()
    assert body["ok"] is False
    assert "No models returned" not in body["message"], body["message"]
    assert "rejected the credential (HTTP 401)" in body["message"], body["message"]
    assert body["connection"]["rejected_credential"] is True


@pytest.mark.asyncio
async def test_a_working_instance_tests_connected(vendor: _Vendor) -> None:
    """The floor under the two above: a measurement, not a pessimism."""
    async with _client() as client:
        await _create(client, "working", endpoint=vendor.endpoint, api_key=GOOD_KEY)
        body = await (await client.post("/api/model-providers/working/test")).json()
        row = await _row(client, "working")
    assert body["ok"] is True and body["status"] == "connected", body
    assert row["connection"]["state"] == "connected"
    assert row["key_in_store"] is False  # the key is in the instance's options, not the store


# ── a rejected key is not re-sent, and its models are not offered ────────────


@pytest.mark.asyncio
async def test_a_rejected_key_is_not_sent_again_on_every_page_load(vendor: _Vendor) -> None:
    async with _client() as client:
        await _create(client, "revoked", endpoint=vendor.endpoint, api_key="sk-revoked")
        await client.post("/api/model-providers/revoked/test")  # the Test button measures it
        before = vendor.hits
        for _ in range(3):
            available = await (await client.get("/api/models/available")).json()
            chat = await (await client.get("/api/models/chat")).json()
    assert (
        vendor.hits == before
    ), f"{vendor.hits - before} more requests carried a key the vendor had already rejected"
    row = next(p for p in available["providers"] if p["name"] == "revoked")
    assert row["models"] == []
    assert "rejected the credential" in row["error"], row
    assert not [m for m in chat if m["provider"] == "revoked"], chat


@pytest.mark.asyncio
async def test_a_listing_that_swallowed_a_refusal_is_an_error_row_not_an_empty_one(
    vendor: _Vendor,
) -> None:
    """The first load, before any check has landed: the refusal the fail-soft listing
    swallowed is the row's error, where it used to be an empty list reading "no models"."""
    async with _client() as client:
        await _create(client, "revoked", endpoint=vendor.endpoint, api_key="sk-revoked")
        available = await (await client.get("/api/models/available")).json()
    row = next(p for p in available["providers"] if p["name"] == "revoked")
    assert row["models"] == []
    assert "rejected the credential (HTTP 401)" in row.get("error", ""), row


def test_the_same_rejected_key_is_logged_once_not_once_per_load(
    allow_loopback_egress: None, caplog: pytest.LogCaptureFixture
) -> None:
    srv = _Vendor()
    try:
        with caplog.at_level(logging.WARNING, logger="personalclaw.llm.catalog"):
            for _ in range(3):
                assert asyncio.run(openai_compatible_list_models(srv.endpoint, "sk-one")) == []
            assert asyncio.run(openai_compatible_list_models(srv.endpoint, "sk-two")) == []
    finally:
        srv.close()
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 2, f"expected one warning per rejected key, got {warnings}"
    assert all("401" in w for w in warnings)


# ── status surfaces answer from the cache ───────────────────────────────────


@pytest.mark.asyncio
async def test_onboarding_and_the_degraded_report_say_the_chat_provider_is_not_answering(
    vendor: _Vendor,
) -> None:
    """Readiness makes no network calls by design, so a bound provider that is DOWN read as
    set up. Both status routes now carry its last MEASURED connection — and reading them
    measures nothing (the vendor's hit count does not move)."""
    async with _client() as client:
        await _create(client, "revoked", endpoint=vendor.endpoint, api_key="sk-revoked")
        r = await client.put("/api/models/active/chat", json={"models": ["revoked:vendor-chat-1"]})
        assert r.status == 200, await r.text()

        hits = vendor.hits
        onboarding = await (await client.get("/api/onboarding")).json()
        assert onboarding["chat_provider_connection"] is None, "nothing was measured yet"
        assert vendor.hits == hits, "a status read measured the provider"

        await client.post("/api/model-providers/revoked/test")
        hits = vendor.hits
        onboarding = await (await client.get("/api/onboarding")).json()
        degraded = await (await client.get("/api/resilience/degraded")).json()
    assert vendor.hits == hits, "a status read measured the provider"
    for status in (onboarding["chat_provider_connection"], degraded["chat_provider"]):
        assert status["provider"] == "revoked"
        assert status["state"] == "failed"
        assert status["rejected_credential"] is True
        assert "401" in status["detail"]


# ── an endpoint is checked when it is written ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint, says",
    [
        ("not a url", "isn't a URL"),
        ("localhost:11434", "isn't a URL"),
        ("http://localhost:99999", "invalid port"),
    ],
)
async def test_a_malformed_endpoint_is_refused_when_it_is_saved(
    vendor: _Vendor, endpoint: str, says: str
) -> None:
    async with _client() as client:
        r = await client.post(
            "/api/model-providers",
            json={"name": "bad", "type": VENDOR_TYPE, "options": {"endpoint": endpoint}},
        )
        body = await r.json()
        assert r.status == 400, body
        assert says in body["error"] and "http://" in body["error"], body
        assert "bad" not in {p["name"] for p in _stored_providers()}

        await _create(client, "good", endpoint=vendor.endpoint)
        r = await client.put("/api/model-providers/good", json={"options": {"endpoint": endpoint}})
        assert r.status == 400, await r.text()
    stored = next(p for p in _stored_providers() if p["name"] == "good")
    assert stored["options"]["endpoint"] == vendor.endpoint, "a refused edit was saved anyway"


# ── failures in words: the endpoint, the cause, the fix ─────────────────────


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_a_refused_localhost_in_the_container_names_the_hosts_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personalclaw.providers.failure_copy import connectivity_guidance

    refused = ConnectionRefusedError(61, "Connect call failed")
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    copy = connectivity_guidance(refused, endpoint="http://localhost:11434")
    assert copy is not None and "http://localhost:11434" in copy
    assert "host.docker.internal" in copy and "host.lima.internal" in copy
    # Only there: a LAN host refusing, or localhost outside a container, is a stopped server.
    assert "host.docker.internal" not in connectivity_guidance(
        refused, endpoint="http://ollama.lan:11434"
    )
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "pip")
    assert "host.docker.internal" not in connectivity_guidance(
        refused, endpoint="http://localhost:11434"
    )


def test_openai_compatible_discovery_gets_the_same_hint(
    allow_loopback_egress: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LM Studio / vLLM / llama.cpp on the host are the same trap as Ollama."""
    from personalclaw.llm.catalog import openai_compatible_discover_models

    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    endpoint = f"http://127.0.0.1:{_closed_port()}/v1"
    with pytest.raises(ModelDiscoveryError) as caught:
        asyncio.run(openai_compatible_discover_models(endpoint, "sk-test"))
    assert "the connection was refused" in str(caught.value)
    assert "host.docker.internal" in str(caught.value)


def _ollama_catalog(endpoint: str) -> Any:
    from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module

    module = load_bundle_module(NATIVE_DIR / "ollama-models", "ollama-models", "provider")
    return module.OllamaCatalog(endpoint)


def test_an_unreachable_ollama_is_an_error_not_an_empty_model_list() -> None:
    endpoint = f"http://127.0.0.1:{_closed_port()}"
    catalog = _ollama_catalog(endpoint)
    with pytest.raises(ModelDiscoveryError) as caught:
        asyncio.run(catalog.list_models())
    assert f"Could not reach {endpoint} — the connection was refused" in str(caught.value)
    result = asyncio.run(catalog.test_connection())
    assert result.ok is False
    assert endpoint in result.detail and "Errno" not in result.detail, result.detail


class _Tags:
    """A loopback server answering ``/api/tags`` with a fixed status, body and type."""

    def __init__(self, status: int, body: str, content_type: str = "application/json") -> None:
        class _H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                raw = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args: object) -> None:
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self.endpoint = f"http://127.0.0.1:{self._srv.server_address[1]}"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


@pytest.mark.parametrize(
    "status, body, content_type, says",
    [
        (404, "404 page not found", "text/plain", "answered HTTP 404 for the model list"),
        (200, "<html>a web page</html>", "text/html", "answered, but not with Ollama's"),
        (200, '{"models": "not a list"}', "application/json", "answered, but not with Ollama's"),
    ],
)
def test_a_server_that_is_not_ollama_says_so(
    status: int, body: str, content_type: str, says: str
) -> None:
    srv = _Tags(status, body, content_type)
    try:
        with pytest.raises(ModelDiscoveryError) as caught:
            asyncio.run(_ollama_catalog(srv.endpoint).list_models())
    finally:
        srv.close()
    assert says in str(caught.value) and srv.endpoint in str(caught.value)


def test_a_real_ollama_answer_still_lists_its_models() -> None:
    srv = _Tags(200, json.dumps({"models": [{"name": "llama3:8b", "size": 4_700_000_000}]}))
    try:
        models = asyncio.run(_ollama_catalog(srv.endpoint).list_models())
    finally:
        srv.close()
    assert [m.id for m in models] == ["llama3:8b"]


@pytest.mark.asyncio
async def test_an_unreachable_ollama_card_says_so_instead_of_listing_no_models() -> None:
    """The download card: "No downloadable models listed." was its empty state for a server
    that never answered."""
    from personalclaw.local_models import registry as local_registry

    endpoint = f"http://127.0.0.1:{_closed_port()}"
    provider = local_registry._ManagerBackedLocalProvider(  # noqa: SLF001
        "dead-ollama", "Ollama", _ollama_catalog(endpoint)
    )
    local_registry.register_provider(provider, capabilities=["chat"], name="dead-ollama")
    try:
        async with _client() as client:
            available = await (await client.get("/api/models/available")).json()
    finally:
        local_registry.unregister_provider("dead-ollama")
    row = next(p for p in available["providers"] if p["name"] == "dead-ollama")
    assert row["models"] == []
    assert f"Could not reach {endpoint}" in row.get("error", ""), row


# ── one store for a model app's instances ────────────────────────────────────


_MULTI = "multi-models"
_SCHEMA = {"type": "object", "properties": {"endpoint": {"type": "string"}}}


def _plant_multi_instance_app(home: Path) -> Path:
    assert home != Path.home() / ".personalclaw", "refusing to plant a test app in the real home"
    root = home / "apps" / _MULTI
    root.mkdir(parents=True, exist_ok=True)
    (root / "installed.json").write_text(
        json.dumps({"name": _MULTI, "version": "0.1.0", "enabled": True, "origin": "local"})
    )
    (root / "app.json").write_text(
        json.dumps(
            {
                "name": _MULTI,
                "version": "0.1.0",
                "displayName": "Multi Models",
                "description": "A multi-instance model provider.",
                "provider": {
                    "type": "model",
                    "implementation": "provider:create_provider",
                    "multiInstance": True,
                    "capabilities": ["chat"],
                    "settingsSchema": _SCHEMA,
                },
            }
        )
    )
    return root


@pytest.mark.asyncio
async def test_an_app_configured_per_instance_has_no_app_level_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apps → Ollama → Configure saved ``apps/ollama-models/data/config.json`` (200 OK) while
    chat read ``config.json providers[]`` — a second copy nothing read. Every app-level
    config door refuses such an app now, and the listings stop offering one."""
    import sys

    from personalclaw.apps.manifest import AppManifest
    from personalclaw.dashboard.handlers import apps as A
    from personalclaw.providers import routes as provider_routes
    from personalclaw.providers.registry import ProviderRegistry

    root = _plant_multi_instance_app(config_loader.config_dir())
    registry = ProviderRegistry()
    registry.register(AppManifest.from_json_file(root / "app.json"))
    monkeypatch.setattr(provider_routes, "get_provider_registry", lambda: registry)
    try:
        from personalclaw.providers.availability import AvailabilityBoard
    except ImportError:  # a tree without the board still runs every assertion below
        board = None
    else:
        # The list route reads availability; a child that exits at once keeps it cheap.
        board = AvailabilityBoard(argv_for=lambda names: [sys.executable, "-c", "pass"])
        monkeypatch.setattr(provider_routes, "get_availability_board", lambda: board)

    app = web.Application()
    app.router.add_get("/api/apps", A.api_apps_list)
    app.router.add_get("/api/apps/{name}/config", A.api_app_config_get)
    app.router.add_put("/api/apps/{name}/config", A.api_app_config_put)
    provider_routes.register_routes(app)
    async with TestClient(TestServer(app)) as client:
        for method, path in (
            ("GET", f"/api/apps/{_MULTI}/config"),
            ("PUT", f"/api/apps/{_MULTI}/config"),
            ("GET", f"/api/providers/{_MULTI}/config"),
            ("PATCH", f"/api/providers/{_MULTI}/config"),
        ):
            r = await client.request(method, path, json={"endpoint": "http://elsewhere:11434"})
            body = await r.json()
            assert r.status == 409, (method, path, body)
            assert "Settings → Providers" in body["error"], body
        apps = await (await client.get("/api/apps")).json()
        providers = await (await client.get("/api/providers")).json()
        if board is not None:
            await board.shutdown()

    app_row = next(a for a in apps["apps"] if a["name"] == _MULTI)
    assert app_row["hasConfig"] is False and app_row["configuredPerInstance"] is True
    card = next(p for p in providers["providers"] if p["name"] == _MULTI)
    assert card["provider"]["hasConfigSchema"] is False
    assert not (root / "data" / "config.json").exists(), "an app-level config was written"


def test_a_multi_instance_model_app_builds_nothing_from_the_generic_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A record left in the generic instance store is not a provider chat can reach, so the
    type handler no longer builds one from it (it used to, one per record — #3372/#3410)."""
    from personalclaw.apps.manifest import AppManifest
    from personalclaw.providers.instances import create_instance
    from personalclaw.providers.registry import ModelTypeHandler, ProviderRegistry

    root = _plant_multi_instance_app(config_loader.config_dir())
    create_instance(_MULTI, "left over", {"endpoint": "http://127.0.0.1:11434"})
    registry = ProviderRegistry()
    registry.register(AppManifest.from_json_file(root / "app.json"))
    built: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "personalclaw.providers.loader.load_factory",
        lambda ext: lambda config=None: built.append(dict(config or {})) or object(),
    )
    assert ModelTypeHandler().create(registry.get(_MULTI)) is None
    assert built == [], "a provider was built from the generic store's record"
