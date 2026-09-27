"""An installed app cannot change your models: your model providers and model bindings are yours.

**The holes, measured on origin/main ed188cae1 (#3672) with the fixture app below.**

1. ``/api/model-providers`` and ``/api/models`` were in no ``SECURITY_ROUTE_FAMILIES`` root, so an
   app that declared them reached all 45 routes in both. A model provider says where your model
   calls go and which of your keys goes with them; a binding says which model each use runs on.
   ``PUT /api/model-providers/Yours`` from ``probe-models`` answered 200, and the provider your
   chats are bound to was rebuilt on the app's endpoint with your key. The app could add a
   provider of its own and bind your chats to it, and read your providers and bindings. The rest
   of both families was open the same way: the routing table and its proposals, a use's settings
   (which hold its routing pin), your Hugging Face token, model downloads, runtime installs, the
   models on this machine, and your usage.
2. The onboarding wizard's one-click bind (``POST /api/onboarding/local-model/bind``) does both in
   one request, and its endpoint check admits loopback, where an app's own backend listens. An app
   that answered Ollama's ``/api/tags`` there had its server added as "Local Ollama" and your chat
   model moved onto it.

Every app-side case goes through the REAL token middleware and the REAL
``app_permission_middleware``, for a fixture app you installed through preview → consent → install
(``POST /api/apps/preview``, then ``POST /api/apps`` with its digest), with the token you minted for
it (``POST /api/apps/{name}/token``) presented both ways an app presents one: as a Bearer header
beside your cookie (its SDK) and as the credential itself (its backend). Each refusal is read back
as the Security Event Log row that names the app.

The route matrix answers with a stand-in for each handler, at its real route template: a request the
middleware lets through is recorded instead of run. On main each app request there would otherwise
run the real handler (a pip install, a model download, a write to your credential store, a deleted
model), so the stand-in is what makes the demonstration safe to run. The provider, binding and
onboarding cases run the real handlers, against a private model registry and a loopback stand-in
for Ollama.
"""

from __future__ import annotations

import json
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import DummyCookieJar, web
from aiohttp.test_utils import TestClient, TestServer

# Imported before any test patches `config_dir`: a module first imported under a patch keeps the
# mock bound.
from personalclaw import seed_local_model
from personalclaw.apps import manager
from personalclaw.dashboard.handlers import local_model, model_registry
from personalclaw.dashboard.handlers import providers as model_providers
from personalclaw.dashboard.handlers.apps import register_app_routes

APP = "probe-models"
#: Both families, and the onboarding routes, so no refusal here is "path not declared".
DECLARES = ["/api/models", "/api/model-providers", "/api/onboarding"]
FAMILIES = ("/api/model-providers", "/api/models")
#: The port the token middleware names its session cookie after (``pc_token_{port}``).
PORT = 10000

#: The model type an installed model app registers, and your provider of it.
VENDOR = "vendor-models"
YOURS = "Yours"
YOUR_MODEL = "vendor-chat-1"
YOUR_ENDPOINT = "https://yours.example/v1"
YOUR_KEY = "fake-key-1"
#: Where a hostile write points your model calls, and the provider an app would add there.
ATTACKER = "https://collector.attacker.example/v1"
THEIRS = "Theirs"
THEIR_MODEL = "helpful:latest"
BIND = "/api/onboarding/local-model/bind"

#: Every route in both families, and the onboarding door to both, each with what a hostile call
#: would send. Railed below against the route census, so a route added tomorrow has to be listed.
MODEL_ROUTES: list[tuple[str, str, Any]] = [
    # model providers
    ("GET", "/api/model-providers", None),
    (
        "POST",
        "/api/model-providers",
        {"name": THEIRS, "type": VENDOR, "model": THEIR_MODEL, "options": {"endpoint": ATTACKER}},
    ),
    ("PUT", "/api/model-providers/{name}", {"options": {"endpoint": ATTACKER}}),
    ("DELETE", "/api/model-providers/{name}", None),
    ("POST", "/api/model-providers/{name}/test", None),
    ("POST", "/api/model-providers/{name}/selftest", None),
    ("GET", "/api/model-providers/{name}/models", None),
    ("GET", "/api/model-providers/{name}/search", None),
    ("GET", "/api/model-providers/{name}/show", None),
    ("POST", "/api/model-providers/{name}/pull", {"model": THEIR_MODEL}),
    ("POST", "/api/model-providers/{name}/models/delete", {"model": YOUR_MODEL}),
    # which model each use runs on, and how it is routed
    ("GET", "/api/models/active", None),
    ("PUT", "/api/models/active/{use_case}", {"models": [f"{THEIRS}:{THEIR_MODEL}"]}),
    ("GET", "/api/models/chat", None),
    ("GET", "/api/models/available", None),
    ("GET", "/api/models/use-cases/{use_case}/settings", None),
    ("PUT", "/api/models/use-cases/{use_case}/settings", {"pin": f"{THEIRS}:{THEIR_MODEL}"}),
    ("GET", "/api/models/routing-policy", None),
    ("PUT", "/api/models/routing-policy", {"use_case": "chat", "pin": f"{THEIRS}:{THEIR_MODEL}"}),
    ("GET", "/api/models/routing-proposals", None),
    ("POST", "/api/models/routing-proposals/{id}/accept", None),
    ("DELETE", "/api/models/routing-proposals/{id}", None),
    ("GET", "/api/models/telemetry", None),
    ("GET", "/api/models/health", None),
    # your Hugging Face token
    ("GET", "/api/models/hf-token/status", None),
    ("PUT", "/api/models/hf-token", {"token": "fake-hf-token-theirs"}),
    ("DELETE", "/api/models/hf-token", None),
    # the models this machine downloads, installs, holds and runs
    ("GET", "/api/models/downloads", None),
    ("POST", "/api/models/downloads", {"provider": "ollama", "model": THEIR_MODEL}),
    ("DELETE", "/api/models/downloads/{id}", None),
    ("GET", "/api/models/downloads/{id}/stream", None),
    ("GET", "/api/models/downloads/cleanup-candidates", None),
    ("POST", "/api/models/downloads/cleanup", {"confirm": True}),
    ("POST", "/api/models/sidecar/{provider}/install", None),
    ("DELETE", "/api/models/sidecar/{provider}/install", None),
    ("GET", "/api/models/sidecar/{provider}/install/status", None),
    ("GET", "/api/models/loaded", None),
    ("POST", "/api/models/unload", {"provider": "ollama"}),
    ("GET", "/api/models/local/{provider}/health", None),
    ("GET", "/api/models/local/{provider}/search", None),
    ("POST", "/api/models/local/{provider}/selftest", None),
    ("DELETE", "/api/models/local/{provider}/{model}", None),
    ("GET", "/api/models/embedding/reindex", None),
    ("POST", "/api/models/embedding/reindex", None),
    ("GET", "/api/models/embedding/reindex/{id}/stream", None),
    # the onboarding wizard's one-click bind: a provider and your chat binding in one request
    ("POST", BIND, {"endpoint": "http://127.0.0.1:11434"}),
]

_PATH_PARAMS = {
    "name": YOURS,
    "use_case": "chat",
    "id": "job-1",
    "provider": "ollama",
    "model": YOUR_MODEL,
}


def _path(template: str) -> str:
    for key, value in _PATH_PARAMS.items():
        template = template.replace("{" + key + "}", value)
    return template


def _bundle(root: Path) -> Path:
    """A fixture app that declares both families and the onboarding routes, and ships nothing."""
    d = root / "bundles" / APP
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Probe Models",
        "description": "A fixture app that declares the routes your models are set up through.",
        "permissions": {"api": DECLARES},
    }
    (d / "app.json").write_text(json.dumps(manifest, indent=1))
    return d


# ── the process-wide state the fixtures reach ─────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _fresh_sessions() -> Iterator[None]:
    from personalclaw.dashboard.token_auth import revoke_all_sessions

    revoke_all_sessions()
    yield
    revoke_all_sessions()


@pytest.fixture
def sel_rows() -> Iterator[MagicMock]:
    """Every row the permission middleware writes (it imports ``sel`` at the refusal)."""
    fake = MagicMock()
    with patch("personalclaw.sel.sel", return_value=fake):
        yield fake.log_api_access


def _denials(rows: MagicMock, path: str) -> list:
    return [
        c
        for c in rows.call_args_list
        if c.kwargs.get("caller") == f"app:{APP}"
        and c.kwargs.get("outcome") == "denied"
        and c.kwargs.get("source") == "app_permissions"
        and c.kwargs.get("resources") == path
    ]


@pytest.fixture
def models(monkeypatch) -> Any:
    """A private model registry in which a model app is installed: the type it registers."""
    from personalclaw.llm import registry as llm_registry
    from personalclaw.llm.capabilities import Capability, ProviderCapability

    registry = llm_registry.ProviderRegistry()
    registry.register_type(
        ProviderCapability(
            type=VENDOR,
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=0,
        ),
        lambda **kw: None,
    )
    monkeypatch.setattr(llm_registry, "get_default_registry", lambda: registry)
    # The typed media registries are process-wide; a provider write only re-reads them.
    monkeypatch.setattr(model_providers, "_refresh_media_registries", lambda: None)
    return registry


class _Ollama:
    """Ollama's model list on loopback, naming one chat model — an Ollama on your machine, or an
    app's backend answering as one. ``hits`` counts every request it gets."""

    def __init__(self) -> None:
        self.hits = 0
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
                outer.hits += 1
                found = self.path == "/api/tags"
                body = {"models": [{"name": THEIR_MODEL, "model": THEIR_MODEL}]} if found else {}
                raw = json.dumps(body).encode()
                self.send_response(200 if found else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def ollama(monkeypatch) -> Iterator[_Ollama]:
    """The loopback Ollama, with the Ollama provider app installed — the bind's one precondition
    this file does not install for real (core ships no copy of that app)."""
    monkeypatch.setattr(seed_local_model, "_installed_provider_app", lambda: True)
    server = _Ollama()
    try:
        yield server
    finally:
        server.close()


# ── the gateway your clicks and an app's requests reach ────────────────────────────────────────

Handler = Any


class _Gateway:
    def __init__(self, client: TestClient, owner_token: str) -> None:
        self.client = client
        self._owner = owner_token

    async def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        app_token: str = "",
        as_backend: bool = False,
    ) -> tuple[int, str]:
        """One request: yours (your cookie), or an app's with *app_token* — beside your cookie as
        its SDK sends it, or, *as_backend*, as the only credential, the way its backend does."""
        headers: dict[str, str] = {}
        params: dict[str, str] = {}
        if app_token and as_backend:
            params["token"] = app_token
        else:
            headers["Cookie"] = f"pc_token_{PORT}={self._owner}"
            if app_token:
                headers["Authorization"] = f"Bearer {app_token}"
        kwargs: dict[str, Any] = {"headers": headers, "params": params}
        if body is not None:
            kwargs["json"] = body
        resp = await self.client.request(method, path, **kwargs)
        return resp.status, await resp.text()

    async def ok(self, method: str, path: str, body: Any = None) -> Any:
        status, text = await self.call(method, path, body)
        assert status < 300, f"{method} {path} as you → {status}: {text}"
        return json.loads(text)

    async def bind(self, use_case: str, models: list[str]) -> Any:
        """Your binding, the way the Models page saves it: the chain replaces the use case's whole
        chain, so it names the revision of the chain it was read at (`If-Match`)."""
        base = (await self.ok("GET", "/api/models/active"))["revisions"][use_case]
        resp = await self.client.put(
            f"/api/models/active/{use_case}",
            json={"models": models},
            headers={"Cookie": f"pc_token_{PORT}={self._owner}", "If-Match": f'"{base}"'},
        )
        text = await resp.text()
        assert (
            resp.status < 300
        ), f"PUT /api/models/active/{use_case} as you → {resp.status}: {text}"
        return json.loads(text)

    async def install(self, source: Path) -> None:
        """Your install: review, then install with the digest the review returned."""
        review = await self.ok("POST", "/api/apps/preview", {"source": str(source)})
        assert review.get("consent"), review
        await self.ok("POST", "/api/apps", {"source": str(source), "consent": review["consent"]})

    async def app_token(self) -> str:
        """The token you mint for the app, as its SDK asks for one on mount."""
        return str((await self.ok("POST", f"/api/apps/{APP}/token"))["token"])


@asynccontextmanager
async def _gateway(routes: list[tuple[str, str, Handler]]) -> AsyncIterator[_Gateway]:
    """The real token middleware and app permission middleware, in front of the app routes and
    *routes* — registered as the gateway registers them, so ``HEAD`` answers on every read."""
    from personalclaw.dashboard.server import app_permission_middleware
    from personalclaw.dashboard.token_auth import generate_token, token_auth_middleware

    app = web.Application(
        middlewares=[token_auth_middleware(port=PORT, local_only=False), app_permission_middleware]
    )
    register_app_routes(app)
    for method, template, handler in routes:
        if method == "GET":
            app.router.add_get(template, handler)
        else:
            app.router.add_route(method, template, handler)
    owner = generate_token("owner", ttl_seconds=600)
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        yield _Gateway(client, owner)


def _stand_ins(reached: list[tuple[str, str]]) -> list[tuple[str, str, Handler]]:
    """A stand-in for every handler in :data:`MODEL_ROUTES`, recording who reached it."""

    async def stand_in(request: web.Request) -> web.Response:
        reached.append((request.get("app", ""), request.method))
        return web.json_response({"reached": True})

    return [(method, template, stand_in) for method, template, _body in MODEL_ROUTES]


def _real_routes() -> list[tuple[str, str, Handler]]:
    """The real handlers of the routes your provider, binding and onboarding flows use, at the
    templates the gateway registers them under (railed against the census below)."""
    return [
        ("GET", "/api/model-providers", model_providers.api_providers_list),
        ("POST", "/api/model-providers", model_providers.api_provider_create),
        ("PUT", "/api/model-providers/{name}", model_providers.api_provider_update),
        ("DELETE", "/api/model-providers/{name}", model_providers.api_provider_delete),
        ("GET", "/api/models/active", model_registry.api_models_active),
        ("PUT", "/api/models/active/{use_case}", model_registry.api_models_active_set),
        ("POST", BIND, local_model.api_local_model_bind),
    ]


async def _your_models(gw: _Gateway) -> None:
    """Your setup: a model provider holding your key, and your chats bound to its model."""
    yours = {"endpoint": YOUR_ENDPOINT, "api_key": YOUR_KEY}
    body = {"name": YOURS, "type": VENDOR, "model": YOUR_MODEL, "options": yours}
    await gw.ok("POST", "/api/model-providers", body)
    await gw.bind("chat", [f"{YOURS}:{YOUR_MODEL}"])


def _stored_providers() -> list[str]:
    from personalclaw.config.loader import config_path

    path = config_path()
    document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return [str(p.get("name")) for p in document.get("providers", [])]


def _chat_binding() -> list[str]:
    from personalclaw.providers.use_cases import load_active_models

    return list(load_active_models().get("chat") or [])


# ── 1. Every route in both families, and the onboarding bind, is yours ──────────────────────────


class TestAnAppCannotReachYourModels:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "template", "body"), MODEL_ROUTES, ids=[f"{m} {t}" for m, t, _ in MODEL_ROUTES]
    )
    async def test_an_app_is_refused_and_you_are_not(
        self, home, sel_rows, method, template, body
    ) -> None:
        reached: list[tuple[str, str]] = []
        path = _path(template)
        async with _gateway(_stand_ins(reached)) as gw:
            await gw.install(_bundle(home))
            token = await gw.app_token()
            as_sdk = await gw.call(method, path, body, app_token=token)
            as_backend = await gw.call(method, path, body, app_token=token, as_backend=True)
            yours = await gw.call(method, path, body)
        for status, text in (as_sdk, as_backend):
            assert status == 403, text
            assert "owner-only capability, not grantable to an app" in text, text
        assert len(_denials(sel_rows, path)) == 2, "each refusal leaves an SEL row naming the app"
        assert yours[0] == 200, yours
        assert reached == [("", method)], "only your request reached the handler"

    @pytest.mark.asyncio
    async def test_head_is_the_same_read(self, home, sel_rows) -> None:
        reached: list[tuple[str, str]] = []
        path = "/api/models/hf-token/status"
        async with _gateway(_stand_ins(reached)) as gw:
            await gw.install(_bundle(home))
            token = await gw.app_token()
            status, _text = await gw.call("HEAD", path, app_token=token)
            yours, _ = await gw.call("HEAD", path)
        assert status == 403
        assert _denials(sel_rows, path)
        assert yours == 200 and reached == [("", "HEAD")]


# ── 2. What a refusal protects: where your chats go, and which model answers them ──────────────


class TestAnAppCannotMoveYourChats:
    @pytest.mark.asyncio
    async def test_your_provider_keeps_its_endpoint_and_your_chats_their_model(
        self, home, models, sel_rows
    ) -> None:
        async with _gateway(_real_routes()) as gw:
            await gw.install(_bundle(home))
            token = await gw.app_token()
            await _your_models(gw)
            repoint = await gw.call(
                "PUT",
                f"/api/model-providers/{YOURS}",
                {"options": {"endpoint": ATTACKER}},
                app_token=token,
            )
            add = await gw.call(
                "POST",
                "/api/model-providers",
                {"name": THEIRS, "type": VENDOR, "model": THEIR_MODEL, "options": {}},
                app_token=token,
            )
            rebind = await gw.call(
                "PUT",
                "/api/models/active/chat",
                {"models": [f"{THEIRS}:{THEIR_MODEL}"]},
                app_token=token,
            )
            listing = await gw.call("GET", "/api/model-providers", app_token=token)
            bindings = await gw.call("GET", "/api/models/active", app_token=token)
        live = models.get_entry(YOURS)
        assert live.options.get("endpoint") == YOUR_ENDPOINT, "your chats go where you pointed them"
        assert live.options.get("api_key") == YOUR_KEY
        assert _stored_providers() == [YOURS], "no provider of the app's was added"
        assert _chat_binding() == [f"{YOURS}:{YOUR_MODEL}"], "your chats run on your model"
        answers = [repoint, add, rebind, listing, bindings]
        assert [status for status, _ in answers] == [403] * 5, answers
        assert YOUR_ENDPOINT not in listing[1] and YOUR_MODEL not in bindings[1]
        for path in (f"/api/model-providers/{YOURS}", "/api/model-providers", "/api/models/active"):
            assert _denials(sel_rows, path), path

    @pytest.mark.asyncio
    async def test_the_onboarding_bind_does_not_move_them_onto_the_apps_server(
        self, home, models, ollama, sel_rows
    ) -> None:
        """The app's backend answers as an Ollama on loopback. Bound, your chats would be its."""
        async with _gateway(_real_routes()) as gw:
            await gw.install(_bundle(home))
            token = await gw.app_token()
            await _your_models(gw)
            status, text = await gw.call(
                "POST", BIND, {"endpoint": ollama.endpoint}, app_token=token
            )
        assert _chat_binding() == [f"{YOURS}:{YOUR_MODEL}"], "your chats run on your model"
        assert _stored_providers() == [YOURS], "no provider was added at the app's server"
        assert ollama.hits == 0, "the gateway never asked the app's server anything"
        assert status == 403, text
        assert "model provider" in text, text
        assert _denials(sel_rows, BIND)


# ── 3. You still set up your models ─────────────────────────────────────────────────────────────


class TestYouStillSetUpYourModels:
    @pytest.mark.asyncio
    async def test_you_add_edit_bind_and_remove_a_model_provider(self, home, models) -> None:
        from personalclaw.apps.secret_fields import SECRET_MASK

        moved = "https://yours-2.example/v1"
        async with _gateway(_real_routes()) as gw:
            await gw.install(_bundle(home))  # an app is installed: the permission layer is live
            await _your_models(gw)
            listed = await gw.ok("GET", "/api/model-providers")
            bound = await gw.ok("GET", "/api/models/active")
            # The edit form sends the saved key back masked, which means "unchanged".
            edit = {"options": {"endpoint": moved, "api_key": SECRET_MASK}}
            await gw.ok("PUT", f"/api/model-providers/{YOURS}", edit)
            edited = models.get_entry(YOURS).options
            await gw.ok("DELETE", f"/api/model-providers/{YOURS}")
            after = await gw.ok("GET", "/api/models/active")
        (row,) = [p for p in listed["providers"] if p["name"] == YOURS]
        assert row["options"]["endpoint"] == YOUR_ENDPOINT
        assert YOUR_KEY not in json.dumps(listed), "your key stays masked on the wire"
        assert bound["use_cases"]["chat"] == [f"{YOURS}:{YOUR_MODEL}"]
        assert edited["endpoint"] == moved and edited["api_key"] == YOUR_KEY
        assert not after["use_cases"].get("chat"), "its binding went with it"
        assert _stored_providers() == []

    @pytest.mark.asyncio
    async def test_you_still_bind_the_ollama_on_your_machine(self, home, models, ollama) -> None:
        async with _gateway(_real_routes()) as gw:
            await gw.install(_bundle(home))
            answer = await gw.ok("POST", BIND, {"endpoint": ollama.endpoint})
        assert answer["ok"] is True and answer["model"] == THEIR_MODEL
        assert _chat_binding() == [f"{answer['provider']}:{THEIR_MODEL}"]
        assert answer["provider"] in _stored_providers()


# ── 4. The route table declares every route in both families ───────────────────────────────────


def _census() -> set[str]:
    from personalclaw.manifest_reference import _routes_from_ast

    return {
        f"{r['method']} {r['path']}"
        for r in _routes_from_ast()
        if any(r["path"] == f or r["path"].startswith(f + "/") for f in FAMILIES)
    }


class TestTheRouteTableDeclaresYourModels:
    def test_both_families_are_declared_reads_included(self) -> None:
        from personalclaw.apps.permissions import (
            READ_DECLARED_FAMILIES,
            SECURITY_ROUTE_FAMILIES,
            owner_only_api_reason,
        )

        for family in FAMILIES:
            assert family in SECURITY_ROUTE_FAMILIES, family
            assert family in READ_DECLARED_FAMILIES, f"{family}: an app reads none of it"
        assert "model provider" in owner_only_api_reason(BIND)

    def test_every_route_in_both_families_is_driven_here(self) -> None:
        census = _census()
        assert len(census) >= 45, f"only {len(census)} model routes — vacuous"
        driven = {f"{m} {t}" for m, t, _body in MODEL_ROUTES}
        assert census == driven - {f"POST {BIND}"}, census ^ (driven - {f"POST {BIND}"})
        real = {f"{m} {t}" for m, t, _handler in _real_routes()}
        assert real <= census | {f"POST {BIND}"}, "a real handler at a template nothing registers"

    def test_no_app_is_granted_any_of_them(self) -> None:
        """No shipped app calls these routes. One that needs a read gets an ``AppMay`` row saying
        why it is safe, and a case here."""
        from personalclaw.apps.permissions import ROUTE_AUTHZ, OwnerOnly, security_family

        rows = {
            k: v for k, v in ROUTE_AUTHZ.items() if security_family(k.split(" ", 1)[1]) in FAMILIES
        }
        assert len(rows) >= 45, f"only {len(rows)} rows — vacuous"
        granted = sorted(k for k, v in rows.items() if not isinstance(v, OwnerOnly))
        assert not granted, granted
