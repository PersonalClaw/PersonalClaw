"""An installed app reaches only its own provider, and editing a disabled app's instance leaves it
off.

**The holes, measured on origin/main d09e61b96 (#3661) with the fixture apps below.**

1. ``/api/providers`` was in no ``SECURITY_ROUTE_FAMILIES`` root, so an app that declared it
   reached every provider route of every app. A provider's settings say where it connects and
   which of its credentials it connects with: ``PATCH /api/providers/probe-bravo/config`` from
   ``probe-alpha`` answered 200, and Bravo's running provider was rebuilt on the new endpoint with
   Bravo's own key. Its instances are the same settings, one record each, and could be added,
   repointed, removed and test-run. The reads handed out another app's endpoint and instance
   list, and the list route every provider you have.
2. The MCP Tool Servers card (``/api/providers/mcp-tools/instances``) is every server in
   ``mcp.json``, not that app's: creating one names a command the gateway launches, and its
   ``test`` runs it — the owner-only ``/api/mcp`` by another road.
3. ``instance_routes._refresh_multi_instance_provider_safe`` ran ``registry.enable(name)`` after
   every instance write, so editing a disabled app's instance switched its tool provider back on,
   with its code imported and its tools offered to your agents. ``apply_saved_settings``, the
   settings routes' version of the same step, retried a provider that had failed while its app was
   enabled — and an app this core cannot host is exactly that once startup refuses it, so a
   settings save started it.

The apps are installed once for the module (``shared_app_rows.SharedApp``), through preview →
consent → install, in each of the two shapes a provider's settings take: one settings record
(:data:`SETTINGS`) and one record per instance (:data:`INSTANCES`). Each install is Alpha, whose
token the rows mint, and Bravo, which you configured; the instances install has the MCP Tool
Servers card's app as well. Every row runs on that install behind the REAL token middleware, the
REAL ``app_permission_middleware`` and the REAL app and provider routes, on a gateway started
from the home as a restart starts one: every installed app loaded from its files. The token you
mint for an app is presented both ways an app presents one: as a Bearer header beside your cookie
(its SDK) and as the only credential (its backend). Each refusal is read back as the Security
Event Log row that names the app. Before every row the home is put back to what the install left
and checked by digest, every session is signed out, the fenced registries are read around it, and
the row gets a gateway of its own, which takes every fixture provider back out when it closes. A
refused row reads your home before and after the app's requests, so a refusal that wrote anything
fails its own row. Each family's end-to-end refusal stays its own test, on apps installed for it
alone.
"""

from __future__ import annotations

import functools
import json
import re
import sys
import types
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import DummyCookieJar, web
from aiohttp.test_utils import TestClient, TestServer
from shared_app_rows import (
    RecordedRows,
    Row,
    SharedApp,
    home_files,
    planted_rows,
    recording_sel,
    written_since,
)
from table_rows import row
from test_apps_cannot_change_your_models import PORT, _Gateway

# Imported before any test patches `config_dir`: a module first imported under a patch keeps the
# mock bound.
import personalclaw.sdk.tool  # noqa: F401
from personalclaw.apps import manager
from personalclaw.dashboard.handlers.apps import register_app_routes
from personalclaw.providers import instance_routes
from personalclaw.providers import registry as registry_module
from personalclaw.providers import routes as provider_routes

pytestmark = pytest.mark.security


ALPHA = "probe-alpha"
BRAVO = "probe-bravo"
#: The first-party MCP Tool Servers app's name (`providers/mcp_instances.MCP_TOOLS_EXTENSION`).
MCP_TOOLS = "mcp-tools"
#: Where a hostile write points another app's provider.
ATTACKER = "https://collector.attacker.example/v1"
#: Where you pointed Bravo, and the key you gave it, which Bravo's provider connects with.
BRAVO_AT = "https://bravo.example"
BRAVO_KEY = "fake-key-1"
#: The key an instance you add carries.
INSTANCE_KEY = "fake-key-main"
#: What Bravo's running provider was built from, in each shape, as you configured it.
BRAVO_SERVES = {
    "settings": [{"endpoint": BRAVO_AT, "api_key": BRAVO_KEY}],
    "instances": [{"endpoint": BRAVO_AT, "api_key": INSTANCE_KEY}],
}
WIRE = "_providerscope_wire"

#: The two ways an app presents the token you mint for it: beside your cookie, as its SDK sends it,
#: and as the only credential, as its backend does.
WAYS = {"as its SDK": False, "as its backend": True}

_SCHEMA = {
    "type": "object",
    "properties": {
        "endpoint": {"type": "string", "x-meta": {"label": "Endpoint"}},
        "api_key": {"type": "string", "x-meta": {"label": "API key", "sensitive": True}},
    },
}

_PROVIDER_PY = '''\
"""A fixture tool provider: it offers no tool, and records each build with the settings it had."""

import _providerscope_wire as wire


class ProbeTools:
    display_name = "Probe"

    def __init__(self, config):
        self.config = dict(config or {})
        if __NAME__:
            self.name = __NAME__

    async def list_tools(self):
        return []

    async def invoke(self, tool_name, arguments):
        raise KeyError(tool_name)


def create_tools(config=None):
    wire.built.append((__APP__, dict(config or {})))
    return ProbeTools(config)
'''


def _bundle(root: Path, name: str, *, shape: str) -> Path:
    """A fixture app that ships one tool provider and declares `/api/providers` and `/api/apps`.

    ``shape`` is where its settings live: ``settings`` (one settings record, edited through
    ``/config``) or ``instances`` (``multiInstance``, one record per instance). A multi-instance
    provider's name is the registry's (``{app}:{instance id}``), as for ``openai-tools``; the
    registry builds an app named ``mcp-tools`` once, from its settings, so that one names itself."""
    d = root / "bundles" / name
    d.mkdir(parents=True, exist_ok=True)
    provider_name = name if shape == "settings" or name == MCP_TOOLS else None
    (d / "provider.py").write_text(
        _PROVIDER_PY.replace("__NAME__", repr(provider_name)).replace("__APP__", repr(name))
    )
    provider: dict[str, Any] = {
        "type": "tool",
        "implementation": "provider:create_tools",
        "settingsSchema": _SCHEMA,
    }
    if shape == "instances":
        provider["multiInstance"] = True
    manifest = {
        "name": name,
        "version": "1.0.0",
        "displayName": name.replace("-", " ").title(),
        "description": "A fixture app whose provider connects to an endpoint with a key.",
        "permissions": {"api": ["/api/providers", "/api/apps"]},
        "provider": provider,
    }
    (d / "app.json").write_text(json.dumps(manifest, indent=1))
    return d


# ── the gateway a user's clicks and an app's requests reach ────────────────────────────────────


class _Board:
    """The availability board, without the child process that measures: every card is available."""

    def __init__(self) -> None:
        self.rechecked: list[str] = []

    def read(self, name: str, implementation: str) -> Any:
        from personalclaw.providers.availability import AVAILABLE, Availability

        return Availability(AVAILABLE)

    def recheck(self, name: str) -> None:
        self.rechecked.append(name)


#: The writes that replace a whole settings record, and so name the copy they were built from in
#: `If-Match` (`personalclaw/stale_write.py`): the SPA reads the record first and sends the revision
#: that read reported, and so does this gateway's caller, with the same credential. A caller the
#: read refuses gets no revision, and its write is refused before any handler runs, as before.
_WHOLE_RECORD_WRITES = re.compile(
    r"/api/providers/[^/]+/config|/api/providers/[^/]+/instances/[^/]+|/api/apps/[^/]+/config"
)


def _revision(status: int, text: str) -> str:
    try:
        read = json.loads(text)
    except ValueError:
        return ""
    record = read.get("instance", read) if isinstance(read, dict) else {}
    if status >= 300 or not isinstance(record, dict):
        return ""
    return str(record.get("revision") or "")


class _ProviderGateway(_Gateway):
    """The family's gateway (``test_apps_cannot_change_your_models._Gateway``), with the wire
    Bravo's and Alpha's code records each build on, and the availability board."""

    def __init__(
        self, client: TestClient, owner_token: str, wire: types.ModuleType, board: _Board
    ) -> None:
        super().__init__(client, owner_token)
        self.wire = wire
        self.board = board

    async def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        app_token: str = "",
        as_backend: bool = False,
        headers: dict[str, str] | None = None,
        form: Any = None,
    ) -> tuple[int, str]:
        headers = dict(headers or {})
        if method in ("PATCH", "PUT") and _WHOLE_RECORD_WRITES.fullmatch(path):
            read = await super().call("GET", path, app_token=app_token, as_backend=as_backend)
            revision = _revision(*read)
            if revision:
                headers["If-Match"] = f'"{revision}"'
        return await super().call(
            method,
            path,
            body,
            app_token=app_token,
            as_backend=as_backend,
            headers=headers,
            form=form,
        )

    async def both_ways(
        self, token: str, method: str, path: str, body: Any = None
    ) -> list[tuple[int, str]]:
        """The app's request, sent once each way it presents *token* (:data:`WAYS`)."""
        return [
            await self.call(method, path, body, app_token=token, as_backend=alone)
            for alone in WAYS.values()
        ]

    async def add_instance(
        self, app: str, endpoint: str, *, app_token: str = "", as_backend: bool = False
    ) -> str:
        """An instance of *app*'s provider at *endpoint*: yours, or the app's with *app_token*."""
        body = {"display_name": "Main", "config": {"endpoint": endpoint, "api_key": INSTANCE_KEY}}
        path = f"/api/providers/{app}/instances"
        status, text = await self.call(
            "POST", path, body, app_token=app_token, as_backend=as_backend
        )
        assert status < 300, f"POST {path} → {status}: {text}"
        return str(json.loads(text)["instance"]["id"])


def _start_providers() -> None:
    """Every installed app loaded from its files, as a gateway restart loads them."""
    from personalclaw.apps import app_runtime

    registry_module.reset_provider_registry()
    app_runtime.start_installed(gateway=False)


def _stop_providers() -> None:
    """Every fixture provider taken back out of the registries it reached, and its code out of this
    process, so the next start imports the files on disk."""
    from personalclaw.config.loader import config_dir
    from personalclaw.tool_providers import registry as tools

    registry = registry_module.get_provider_registry()
    for ext in registry.list_extensions():
        registry.disable(ext.name, ext.capability)
    registry_module.reset_provider_registry()
    for provider in list(tools.list_providers()):
        if str(getattr(provider, "name", "")).startswith((ALPHA, BRAVO, MCP_TOOLS)):
            tools.unregister_provider(provider)
    here = str(config_dir())
    for name, module in list(sys.modules.items()):
        if str(getattr(module, "__file__", "") or "").startswith(here):
            sys.modules.pop(name, None)


def _restart() -> None:
    _stop_providers()
    _start_providers()


@asynccontextmanager
async def _gateway(routes: list[tuple[str, str, Any]]) -> AsyncIterator[_ProviderGateway]:
    """The real token middleware, permission middleware and request boundary, in front of the app
    routes, the provider routes and *routes*, on a gateway started from the home: every installed
    app's providers loaded from its files when it opens, and taken out when it closes."""
    from personalclaw import mcp_client
    from personalclaw.dashboard.request_boundary import request_boundary_middleware
    from personalclaw.dashboard.server import app_permission_middleware
    from personalclaw.dashboard.token_auth import generate_token, token_auth_middleware

    wire = types.ModuleType(WIRE)
    wire.built = []  # type: ignore[attr-defined]
    board = _Board()
    kept = mcp_client._registry
    sys.modules[WIRE] = wire
    mcp_client._registry = None
    try:
        with patch.object(provider_routes, "get_availability_board", lambda: board):
            _start_providers()
            app = web.Application(
                middlewares=[
                    token_auth_middleware(port=PORT, local_only=False),
                    app_permission_middleware,
                    request_boundary_middleware(),
                ]
            )
            register_app_routes(app)
            provider_routes.register_routes(app)
            instance_routes.register_instance_routes(app)
            for method, template, handler in routes:
                app.router.add_route(method, template, handler)
            owner = generate_token("owner", ttl_seconds=600)
            async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
                yield _ProviderGateway(client, owner, wire, board)
    finally:
        _stop_providers()
        mcp_client._registry = kept
        sys.modules.pop(WIRE, None)


# ── the shared installs ────────────────────────────────────────────────────────────────────────


async def _bravo_configured(gw: _ProviderGateway, home: Path) -> None:
    """Bravo installed beside Alpha, and pointed where you point it, with its key."""
    await gw.install(_bundle(home, BRAVO, shape="settings"))
    await gw.ok(
        "PATCH", f"/api/providers/{BRAVO}/config", {"endpoint": BRAVO_AT, "api_key": BRAVO_KEY}
    )


async def _bravo_and_the_card_installed(gw: _ProviderGateway, home: Path) -> None:
    """Bravo and the MCP Tool Servers card's app installed beside Alpha, and one instance of
    Bravo's that you added."""
    for name in (BRAVO, MCP_TOOLS):
        await gw.install(_bundle(home, name, shape="instances"))
    await gw.add_instance(BRAVO, BRAVO_AT)


#: Alpha and Bravo with one settings record each, Bravo's yours.
SETTINGS = SharedApp(
    ALPHA,
    functools.partial(_bundle, name=ALPHA, shape="settings"),
    _gateway,
    prepare=_bravo_configured,
)
#: Alpha, Bravo and the MCP Tool Servers card's app with one record per instance, Bravo's one
#: instance yours.
INSTANCES = SharedApp(
    ALPHA,
    functools.partial(_bundle, name=ALPHA, shape="instances"),
    _gateway,
    prepare=_bravo_and_the_card_installed,
)
SHAPES = {"settings": SETTINGS, "instances": INSTANCES}


@pytest.fixture
def home(tmp_path, monkeypatch) -> Iterator[Path]:
    """A home of this test's own, with every session signed out before and after."""
    import personalclaw.config.loader as loader
    from personalclaw.dashboard.token_auth import revoke_all_sessions

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    revoke_all_sessions()
    yield tmp_path
    revoke_all_sessions()


@pytest.fixture
def sel_rows() -> Iterator[MagicMock]:
    """Every Security Event Log row the test's requests write, the token middleware's included."""
    fake = MagicMock()
    with recording_sel(fake):
        yield fake.log_api_access


# ── what the home and the running providers hold ───────────────────────────────────────────────


def _stored(app: str) -> dict[str, Any]:
    from personalclaw.providers.settings import load_stored

    return load_stored(app)


def _instance(app: str, instance_id: str) -> Any:
    from personalclaw.providers.instances import get_instance

    return get_instance(app, instance_id)


def _ids(app: str) -> list[str]:
    from personalclaw.providers.instances import list_instances

    return sorted(i.id for i in list_instances(app))


def _live(app: str) -> bool:
    """Whether *app*'s provider is switched on in this gateway."""
    ext = registry_module.get_provider_registry().get(app)
    return ext is not None and ext.enabled


def _tool_providers(app: str) -> list[Any]:
    from personalclaw.tool_providers.registry import list_providers

    return [p for p in list_providers() if str(getattr(p, "name", "")).split(":", 1)[0] == app]


def _serving(app: str) -> list[dict[str, Any]]:
    """The settings each provider *app* serves your agents was built from."""
    return sorted((dict(p.config) for p in _tool_providers(app)), key=json.dumps)


def _mcp_servers(home: Path) -> set[str]:
    """The servers ``mcp.json`` names — what the gateway launches."""
    path = home / "mcp.json"
    if not path.exists():
        return set()
    return set(json.loads(path.read_text()).get("mcpServers", {}))


def _refusals(r: Row, method: str, path: str, *, app: str = ALPHA) -> list:
    """The rows the permission middleware wrote refusing *app* this request, in this row."""
    return [
        c
        for c in r.sel.call_args_list
        if c.kwargs.get("caller") == f"app:{app}"
        and c.kwargs.get("outcome") == "denied"
        and c.kwargs.get("source") == "app_permissions"
        and c.kwargs.get("resources") == path
        and c.kwargs.get("operation") == f"{method} {path}"
    ]


async def _token(gw: _ProviderGateway, app: str) -> str:
    """The token you mint for *app*, as its SDK asks for one on mount."""
    return str((await gw.ok("POST", f"/api/apps/{app}/token"))["token"])


def _ok(answers: list[tuple[int, str]]) -> list[Any]:
    for status, text in answers:
        assert status < 300, f"{status}: {text}"
    return [json.loads(text) for _status, text in answers]


# ── 1. Another app's provider is refused before its handler runs ───────────────────────────────


#: Every route under `/api/providers/{name}`, the settings shape its handler serves, and a body it
#: accepts. Railed below against the route census, so a route added tomorrow must be listed here.
PROVIDER_ROUTES: list[tuple[str, str, str, Any]] = [
    ("GET", "/api/providers/{name}", "settings", None),
    ("GET", "/api/providers/{name}/schema", "settings", None),
    ("GET", "/api/providers/{name}/config", "settings", None),
    ("PATCH", "/api/providers/{name}/config", "settings", {"endpoint": ATTACKER}),
    ("POST", "/api/providers/{name}/availability", "settings", None),
    ("GET", "/api/providers/{name}/instances", "instances", None),
    (
        "POST",
        "/api/providers/{name}/instances",
        "instances",
        {"display_name": "Second", "config": {"endpoint": ATTACKER}},
    ),
    ("GET", "/api/providers/{name}/instances/{id}", "instances", None),
    (
        "PUT",
        "/api/providers/{name}/instances/{id}",
        "instances",
        {"config": {"endpoint": ATTACKER}},
    ),
    ("DELETE", "/api/providers/{name}/instances/{id}", "instances", None),
    ("POST", "/api/providers/{name}/instances/{id}/test", "instances", None),
]

#: The same read as `GET`, and the Apps page's settings route, which writes the file `PATCH
#: /api/providers/{name}/config` does and is held to the calling app by the same row, before its
#: handler runs (#3614 checked it inside the handler).
SETTINGS_ALSO: list[tuple[str, str, Any]] = [
    ("HEAD", "/api/providers/{name}/config", None),
    ("GET", "/api/apps/{name}/config", None),
    ("PUT", "/api/apps/{name}/config", {"endpoint": ATTACKER}),
]


def _another_apps(method: str, template: str, body: Any, shape: str):
    """Alpha's request for Bravo's provider refused both ways as not Alpha's, each refusal leaving
    an SEL row naming Alpha and the path; nothing of Bravo's settings in the answer, none of
    Bravo's code run, your home as it was, and Bravo's running provider connecting where you
    pointed it, with its own key. A write names only the endpoint: the key stays Bravo's, so a
    provider rebuilt from the result would connect to the attacker with Bravo's key."""

    async def run(r: Row) -> None:
        (instance_id,) = _ids(BRAVO) if "{id}" in template else ("",)
        path = template.replace("{name}", BRAVO).replace("{id}", instance_id)
        built = list(r.gw.wire.built)
        assert _serving(BRAVO) == BRAVO_SERVES[shape], "Bravo runs as you configured it"
        before = home_files(r.home)
        theirs = await r.gw.both_ways(r.token, method, path, body)
        written = written_since(r.home, before)
        for status, text in theirs:
            assert status == 403, text
            assert method == "HEAD" or "is not this app" in text, text
            assert "bravo.example" not in text, "nothing of Bravo's settings comes back"
        assert not written, f"a refusal changed your home: {written}"
        assert len(_refusals(r, method, path)) == 2, "each refusal leaves an SEL row naming the app"
        assert r.gw.wire.built == built, "no code of Bravo's ran for Alpha's request"
        assert r.gw.board.rechecked == []
        assert _serving(BRAVO) == BRAVO_SERVES[shape], "and connects where you pointed it"

    return run


#: A read and a write added tomorrow under `/api/providers/{name}`, which no declaration covers.
BRAND_NEW = [
    ("GET", "/api/providers/{name}/brand-new"),
    ("POST", "/api/providers/{name}/brand-new"),
]


def _brand_new_routes(reached: list[tuple[str, str]]) -> list[tuple[str, str, Any]]:
    """A stand-in at each :data:`BRAND_NEW` route, recording who reached it and at which path."""

    async def stand_in(request: web.Request) -> web.Response:
        reached.append((request.get("app", ""), request.path))
        return web.json_response({"reached": True})

    return [(method, template, stand_in) for method, template in BRAND_NEW]


def _a_new_route_fails_closed(reached: list[tuple[str, str]]):
    """A route added under ``/api/providers`` is refused to every app until someone declares whether
    an app may reach it, a read as well as a write, even one naming the app's own provider. You
    reach it."""

    async def run(r: Row) -> None:
        reached.clear()
        paths = []
        for method, template in BRAND_NEW:
            path = template.replace("{name}", ALPHA)
            paths.append(path)
            before = home_files(r.home)
            theirs = await r.gw.both_ways(r.token, method, path)
            written = written_since(r.home, before)
            yours = await r.gw.call(method, path)
            for status, text in theirs:
                assert status == 403, f"{method}: {text}"
                assert "has not declared whether an app may reach it" in text, f"{method}: {text}"
            assert not written, f"a refusal changed your home: {written}"
            assert len(_refusals(r, method, path)) == 2, f"{method}: each refusal leaves an SEL row"
            assert yours[0] == 200, yours
        assert reached == [("", path) for path in paths], "only your requests reached it"

    return run


def _settings_table(reached: list[tuple[str, str]]) -> list[tuple[str, Any]]:
    """Every settings route asked for Bravo's provider, and a route nobody declared yet."""
    routes = [(m, t, b) for m, t, shape, b in PROVIDER_ROUTES if shape == "settings"]
    rows = [
        (f"{m} {t}", _another_apps(m, t, b, "settings")) for m, t, b in [*routes, *SETTINGS_ALSO]
    ]
    rows.append(("a route added under /api/providers", _a_new_route_fails_closed(reached)))
    return rows


@pytest.mark.asyncio
async def test_no_app_reaches_another_apps_settings_or_a_route_not_yet_declared(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SETTINGS.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = _settings_table(reached)
    settings_routes = [r for r in PROVIDER_ROUTES if r[2] == "settings"]
    assert len(rows) == len(settings_routes) + len(SETTINGS_ALSO) + 1, "a route was left out"
    await SETTINGS.run(subtests, rows, _brand_new_routes(reached), label="route")


#: What a server on the MCP Tool Servers card would launch.
EVIL = {"display_name": "evil", "config": {"transport": "stdio", "command": "sh", "args": "-c id"}}
MCP_CARD = f"/api/providers/{MCP_TOOLS}/instances"


def _no_app_defines_or_reads_a_server(caller: str):
    """The card's instances are every server in ``mcp.json``, not the card's app's: an app named for
    it is refused as owner-only, as is any other, naming the MCP servers, and you still reach it.
    The ownership rule alone would pass the app installed under the name ``mcp-tools``."""

    async def run(r: Row) -> None:
        token = r.token if caller == ALPHA else await _token(r.gw, caller)
        before = home_files(r.home)
        made = await r.gw.both_ways(token, "POST", MCP_CARD, EVIL)
        listed = await r.gw.both_ways(token, "GET", MCP_CARD)
        written = written_since(r.home, before)
        yours = await r.gw.call("GET", MCP_CARD)
        for status, text in [*made, *listed]:
            assert status == 403, text
            assert "owner-only capability" in text and "MCP servers" in text, text
        assert not written, f"a refusal changed your home: {written}"
        assert "evil" not in _mcp_servers(r.home), "no server was defined"
        for method in ("POST", "GET"):
            assert len(_refusals(r, method, MCP_CARD, app=caller)) == 2, method
        assert yours[0] == 200, yours

    return run


def _instances_refusals() -> list[tuple[str, Any]]:
    rows = [
        (f"{m} {t}", _another_apps(m, t, b, shape))
        for m, t, shape, b in PROVIDER_ROUTES
        if shape == "instances"
    ]
    rows += [
        (f"the MCP Tool Servers card, as {caller}", _no_app_defines_or_reads_a_server(caller))
        for caller in (MCP_TOOLS, ALPHA)
    ]
    return rows


@pytest.mark.asyncio
async def test_no_app_reaches_another_apps_instances_or_your_mcp_servers(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await INSTANCES.home(tmp_path_factory, monkeypatch)
    rows = _instances_refusals()
    instance_routes_here = [r for r in PROVIDER_ROUTES if r[2] == "instances"]
    assert len(rows) == len(instance_routes_here) + 2, "a route was left out"
    await INSTANCES.run(subtests, rows, [], label="route")


# ── 2. You reach every provider, and an app reaches its own and lists only its own ─────────────


async def _the_list_holds_only_the_apps_own(r: Row) -> None:
    for query in ("", "?type=tool"):
        for listed in _ok(await r.gw.both_ways(r.token, "GET", f"/api/providers{query}")):
            assert [p["name"] for p in listed["providers"]] == [ALPHA], listed
    yours = await r.gw.ok("GET", "/api/providers")
    names = {p["name"] for p in yours["providers"]}
    assert {ALPHA, BRAVO, "personalclaw-filesystem"} <= names, "you still list every one"


async def _you_reconfigure_any_apps_provider(r: Row) -> None:
    await r.gw.ok("PATCH", f"/api/providers/{BRAVO}/config", {"endpoint": "https://b2.example"})
    await r.gw.ok("GET", f"/api/providers/{BRAVO}/config")
    await r.gw.ok("POST", f"/api/providers/{BRAVO}/availability")
    assert _stored(BRAVO)["endpoint"] == "https://b2.example"
    assert _serving(BRAVO) == [{"endpoint": "https://b2.example", "api_key": BRAVO_KEY}]
    assert r.gw.board.rechecked == [BRAVO]


async def _an_app_reconfigures_its_own_provider(r: Row) -> None:
    path = f"/api/providers/{ALPHA}"
    for n, (way, alone) in enumerate(WAYS.items()):
        at = f"https://a{n}.example"
        sent = {"app_token": r.token, "as_backend": alone}
        status, text = await r.gw.call("PATCH", f"{path}/config", {"endpoint": at}, **sent)
        assert status == 200, f"{way}: {text}"
        status, text = await r.gw.call("GET", f"{path}/config", **sent)
        assert status == 200 and json.loads(text)["config"]["endpoint"] == at, f"{way}: {text}"
        for method, sub in (("GET", ""), ("GET", "/schema"), ("POST", "/availability")):
            status, text = await r.gw.call(method, f"{path}{sub}", **sent)
            assert status < 300, f"{way} {method} {sub}: {text}"
        assert _stored(ALPHA)["endpoint"] == at
        assert _serving(ALPHA) == [{"endpoint": at}], "the save reached its provider"
    assert r.gw.board.rechecked == [ALPHA] * len(WAYS)
    assert not r.denials(f"{path}/config")


async def _its_own_settings_are_its_own(r: Row) -> None:
    path = f"/api/apps/{ALPHA}/config"
    for n, (way, alone) in enumerate(WAYS.items()):
        at = f"https://a{n}.example"
        sent = {"app_token": r.token, "as_backend": alone}
        status, text = await r.gw.call("PUT", path, {"endpoint": at}, **sent)
        assert status == 200, f"{way}: {text}"
        status, text = await r.gw.call("GET", path, **sent)
        assert status == 200 and json.loads(text)["config"]["endpoint"] == at, f"{way}: {text}"
    assert not r.denials(path)


@pytest.mark.asyncio
async def test_you_reach_every_apps_settings_and_an_app_its_own(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SETTINGS.home(tmp_path_factory, monkeypatch)
    rows = [
        ("the list holds only the app's own", _the_list_holds_only_the_apps_own),
        ("you reconfigure any app's provider", _you_reconfigure_any_apps_provider),
        ("an app reconfigures its own provider", _an_app_reconfigures_its_own_provider),
        ("an app's own settings route is its own", _its_own_settings_are_its_own),
    ]
    await SETTINGS.run(subtests, rows, [], label="case")


async def _you_manage_any_apps_instances(r: Row) -> None:
    (instance_id,) = _ids(BRAVO)
    base = f"/api/providers/{BRAVO}/instances"
    await r.gw.ok("PUT", f"{base}/{instance_id}", {"config": {"endpoint": "https://b2.example"}})
    await r.gw.ok("POST", f"{base}/{instance_id}/test")
    second = await r.gw.add_instance(BRAVO, "https://b3.example")
    await r.gw.ok("DELETE", f"{base}/{second}")
    assert _ids(BRAVO) == [instance_id]
    assert [c["endpoint"] for c in _serving(BRAVO)] == ["https://b2.example"]


async def _an_app_manages_its_own_instances(r: Row) -> None:
    base = f"/api/providers/{ALPHA}/instances"
    kept: list[str] = []
    for n, (way, alone) in enumerate(WAYS.items()):
        sent = {"app_token": r.token, "as_backend": alone}
        first = await r.gw.add_instance(ALPHA, "https://a1.example", **sent)
        second = await r.gw.add_instance(ALPHA, "https://a2.example", **sent)
        at = f"https://a3-{n}.example"
        _ok(
            [
                await r.gw.call("PUT", f"{base}/{first}", {"config": {"endpoint": at}}, **sent),
                await r.gw.call("POST", f"{base}/{first}/test", **sent),
                await r.gw.call("DELETE", f"{base}/{second}", **sent),
            ]
        )
        listed, one = _ok(
            [
                await r.gw.call("GET", base, **sent),
                await r.gw.call("GET", f"{base}/{first}", **sent),
            ]
        )
        kept.append(first)
        assert sorted(i["id"] for i in listed["instances"]) == sorted(kept), f"{way}: {listed}"
        assert one["instance"]["config"]["endpoint"] == at, f"{way}: {one}"
        assert INSTANCE_KEY not in json.dumps(listed), "its own key stays masked"
    assert sorted(c["endpoint"] for c in _serving(ALPHA)) == [
        f"https://a3-{n}.example" for n in range(len(WAYS))
    ]
    assert not r.denials(base)


@pytest.mark.asyncio
async def test_you_reach_every_apps_instances_and_an_app_its_own(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await INSTANCES.home(tmp_path_factory, monkeypatch)
    rows = [
        ("you manage any app's instances", _you_manage_any_apps_instances),
        ("an app manages its own instances", _an_app_manages_its_own_instances),
    ]
    await INSTANCES.run(subtests, rows, [], label="case")


# ── 3. An app that is off stays off when its settings or one of its instances change ───────────


async def _switched_off(r: Row) -> str:
    """Alpha running one instance of yours, then switched off from the Apps page."""
    instance_id = await r.gw.add_instance(ALPHA, "https://a1.example")
    assert _live(ALPHA) and _tool_providers(ALPHA), "it ran while it was on"
    await r.gw.ok("POST", f"/api/apps/{ALPHA}/disable")
    assert not _live(ALPHA) and not _tool_providers(ALPHA)
    return instance_id


def _off(r: Row, built_before: list) -> None:
    assert not _live(ALPHA), "its provider stays off"
    assert not _tool_providers(ALPHA), "none of its tools is offered"
    assert r.gw.wire.built == built_before, "none of its code ran"
    assert manager._read_installed(ALPHA).enabled is False, "the app is still disabled"


async def _editing_its_instance_saves_and_loads_nothing(r: Row) -> None:
    instance_id = await _switched_off(r)
    built = list(r.gw.wire.built)
    await r.gw.ok(
        "PUT",
        f"/api/providers/{ALPHA}/instances/{instance_id}",
        {"config": {"endpoint": "https://a2.example"}},
    )
    assert _instance(ALPHA, instance_id).config["endpoint"] == "https://a2.example", "saved"
    _off(r, built)


async def _adding_or_removing_an_instance_loads_nothing(r: Row) -> None:
    instance_id = await _switched_off(r)
    built = list(r.gw.wire.built)
    added = await r.gw.add_instance(ALPHA, "https://a2.example")
    _off(r, built)
    await r.gw.ok("DELETE", f"/api/providers/{ALPHA}/instances/{instance_id}")
    assert _ids(ALPHA) == [added]
    _off(r, built)


async def _switching_it_on_runs_the_instance_it_saved(r: Row) -> None:
    instance_id = await _switched_off(r)
    await r.gw.ok(
        "PUT",
        f"/api/providers/{ALPHA}/instances/{instance_id}",
        {"config": {"endpoint": "https://a2.example"}},
    )
    await r.gw.ok("POST", f"/api/apps/{ALPHA}/enable")
    assert [c["endpoint"] for c in _serving(ALPHA)] == ["https://a2.example"]


async def _a_running_apps_instance_change_still_takes_effect(r: Row) -> None:
    instance_id = await r.gw.add_instance(ALPHA, "https://a1.example")
    await r.gw.ok(
        "PUT",
        f"/api/providers/{ALPHA}/instances/{instance_id}",
        {"config": {"endpoint": "https://a2.example"}},
    )
    assert [c["endpoint"] for c in _serving(ALPHA)] == ["https://a2.example"]


def _refused_at_startup(r: Row) -> None:
    """The gateway restarted on a core older than Alpha's floor: startup lists it off with the
    reason, its app still enabled (``app_runtime._refuse``)."""
    manifest_path = manager.app_dir(ALPHA) / "app.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["minPersonalClawVersion"] = "99.0.0"
    manifest_path.write_text(json.dumps(manifest))
    _restart()
    ext = registry_module.get_provider_registry().get(ALPHA)
    assert ext is not None and not ext.enabled and ext.error, "startup refused it"
    assert manager._read_installed(ALPHA).enabled is True


async def _an_instance_change_does_not_start_an_app_this_core_cannot_host(r: Row) -> None:
    instance_id = await r.gw.add_instance(ALPHA, "https://a1.example")
    _refused_at_startup(r)
    built = list(r.gw.wire.built)
    await r.gw.ok(
        "PUT",
        f"/api/providers/{ALPHA}/instances/{instance_id}",
        {"config": {"endpoint": "https://a2.example"}},
    )
    assert not _live(ALPHA) and not _tool_providers(ALPHA)
    assert r.gw.wire.built == built, "none of its code ran"


async def _a_settings_save_does_not_start_an_app_this_core_cannot_host(r: Row) -> None:
    _refused_at_startup(r)
    built = list(r.gw.wire.built)
    await r.gw.ok("PATCH", f"/api/providers/{ALPHA}/config", {"endpoint": "https://a2.example"})
    assert _stored(ALPHA)["endpoint"] == "https://a2.example", "saved"
    assert not _live(ALPHA)
    assert "99.0.0" in registry_module.get_provider_registry().get(ALPHA).error
    assert r.gw.wire.built == built, "none of its code ran"


@pytest.mark.asyncio
async def test_an_app_that_is_off_stays_off_when_its_instances_change(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await INSTANCES.home(tmp_path_factory, monkeypatch)
    rows = [
        (
            "editing its instance saves and loads nothing",
            _editing_its_instance_saves_and_loads_nothing,
        ),
        (
            "adding or removing an instance loads nothing",
            _adding_or_removing_an_instance_loads_nothing,
        ),
        ("switching it on runs the instance it saved", _switching_it_on_runs_the_instance_it_saved),
        (
            "a running app's instance change still takes effect",
            _a_running_apps_instance_change_still_takes_effect,
        ),
        (
            "an instance change does not start an app this core cannot host",
            _an_instance_change_does_not_start_an_app_this_core_cannot_host,
        ),
    ]
    await INSTANCES.run(subtests, rows, [], label="case")


@pytest.mark.asyncio
async def test_a_settings_save_does_not_start_an_app_this_core_cannot_host(
    subtests, tmp_path_factory, monkeypatch
) -> None:
    await SETTINGS.home(tmp_path_factory, monkeypatch)
    rows = [
        (
            "a settings save does not start an app this core cannot host",
            _a_settings_save_does_not_start_an_app_this_core_cannot_host,
        )
    ]
    await SETTINGS.run(subtests, rows, [], label="case")


# ── 4. The route table declares every provider route ───────────────────────────────────────────


def _provider_census() -> set[str]:
    from route_census import routes

    return {
        f"{r['method']} {r['path']}"
        for r in routes()
        if r["path"] == "/api/providers" or r["path"].startswith("/api/providers/")
    }


class TestTheRouteTableDeclaresYourProviders:
    def test_the_family_is_declared(self) -> None:
        from personalclaw.apps.permissions import (
            READ_DECLARED_FAMILIES,
            SECURITY_ROUTE_FAMILIES,
            owner_only_api_reason,
        )
        from personalclaw.providers.mcp_instances import MCP_TOOLS_EXTENSION

        assert "/api/providers" in SECURITY_ROUTE_FAMILIES
        assert "/api/providers" in READ_DECLARED_FAMILIES, "another app's settings are not read"
        assert MCP_TOOLS == MCP_TOOLS_EXTENSION
        assert "MCP servers" in owner_only_api_reason(f"/api/providers/{MCP_TOOLS}/instances")
        assert not owner_only_api_reason(f"/api/providers/{ALPHA}/instances")

    def test_every_provider_route_is_driven_here(self) -> None:
        census = _provider_census()
        assert len(census) >= 12, f"only {len(census)} provider routes — vacuous"
        driven = {f"{m} {t}" for m, t, _shape, _body in PROVIDER_ROUTES} | {"GET /api/providers"}
        assert census == driven, f"provider routes this file never drives: {census - driven}"

    def test_every_app_row_under_a_provider_holds_the_app_to_its_own(self) -> None:
        from personalclaw.apps.permissions import ROUTE_AUTHZ, AppMay, OwnedTarget

        rows = {k: v for k, v in ROUTE_AUTHZ.items() if "/api/providers/{name}" in k}
        assert len(rows) >= 11, f"only {len(rows)} provider rows — vacuous"
        for key, authz in rows.items():
            assert isinstance(authz, AppMay), key
            assert OwnedTarget("name", app=True) in authz.owns, key
        listing = ROUTE_AUTHZ["GET /api/providers"]
        assert isinstance(listing, AppMay) and "only the app's own" in listing.reason


# ── the table's isolation, and a refusal that writes ───────────────────────────────────────────


async def _a_row_that_leaves_state_behind_fails_the_next_row(tmp_path_factory, monkeypatch) -> None:
    """The table's isolation, shown rather than assumed: a row that leaves the home, the SEL rows
    or a fenced registry changed is caught, by name, in the row after it."""
    await SETTINGS.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    probe = _settings_table(reached)[0]
    failed = await planted_rows(SETTINGS, _brand_new_routes(reached), probe)
    name = probe[0]
    # Reset as every row is, a grant left in the home and an SEL row reach nothing after them.
    assert failed["home"]["reset"] == {} and failed["sel"]["reset"] == {}, failed
    # Without the reset, the row after them fails, by name, on the check that guards it.
    assert "a home other than the installed app's" in failed["home"]["no reset"][name]
    assert "planted-grant.json" in failed["home"]["no reset"][name]
    assert "Security Event Log rows" in failed["sel"]["no reset"][name]
    # A fenced registry is caught in the row that changed it, and again in the row after.
    assert set(failed["fenced"]["reset"]) == {"planted fenced", name}, failed["fenced"]
    assert all("left registries" in why for why in failed["fenced"]["reset"].values())


#: The row the planted refusal below answers: pointing Bravo's provider somewhere else.
PLANTED = "PATCH /api/providers/{name}/config"


async def _a_refusal_that_writes_state_fails_its_own_row_and_no_other(
    tmp_path_factory, monkeypatch
) -> None:
    """An ownership check that lets Alpha's write of Bravo's settings through, to a handler that
    saves them and then answers as the refusal does: a 403 saying Bravo is not this app, and an
    SEL row naming the app and the path. Every other request is decided as before, and Bravo's
    running provider is left as it was. Only your home tells it from a refusal, so its row fails
    on that, and the rows either side of it, the home put back before each, pass."""
    from personalclaw import sel
    from personalclaw.dashboard import server
    from personalclaw.providers.settings import ProviderSettings

    await SETTINGS.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = _settings_table(reached)
    at = [name for name, _ in rows].index(PLANTED)
    method, template = PLANTED.split(" ", 1)
    decide = server._ownership_denial
    handle = provider_routes.handle_patch_config

    async def lets_the_planted_write_through(
        request: web.Request, app_name: str, route: str
    ) -> str:
        if (request.method, route) == (method, template):
            return ""
        return await decide(request, app_name, route)

    async def writes_then_refuses(request: web.Request) -> web.StreamResponse:
        app = request.get("app", "")
        if not app:
            return await handle(request)
        name = request.match_info["name"]
        ProviderSettings.update(name, await request.json())
        reason = f"{name!r} is not this app — an app reaches only its own, never another's"
        sel.sel().log_api_access(
            caller=f"app:{app}",
            operation=f"{request.method} {request.path}",
            outcome="denied",
            source="app_permissions",
            resources=request.path,
            error=reason,
        )
        raise web.HTTPForbidden(
            text=f"app {app!r} not permitted to access {request.path}: {reason}"
        )

    monkeypatch.setattr(server, "_ownership_denial", lets_the_planted_write_through)
    monkeypatch.setattr(provider_routes, "handle_patch_config", writes_then_refuses)
    recorded = RecordedRows()
    await SETTINGS.run(recorded, rows[at - 1 : at + 2], _brand_new_routes(reached), label="route")
    assert list(recorded.failed) == [PLANTED], recorded.failed
    written = f"a refusal changed your home: ['apps/{BRAVO}/data/config.json']"
    assert written in recorded.failed[PLANTED], recorded.failed


@pytest.mark.asyncio
async def test_the_table_keeps_each_row_to_itself(subtests, tmp_path_factory, monkeypatch) -> None:
    """Both halves of the table's isolation, each a row: a row that leaves state behind fails the
    row after it, and a refusal that writes state fails its own row and no other."""
    with row(subtests, check="a row that leaves state behind fails the next row"):
        await _a_row_that_leaves_state_behind_fails_the_next_row(tmp_path_factory, monkeypatch)
    with row(subtests, check="a refusal that writes state fails its own row and no other"):
        await _a_refusal_that_writes_state_fails_its_own_row_and_no_other(
            tmp_path_factory, monkeypatch
        )


# ── 5. Each family, end to end, on apps installed for this test alone ──────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", sorted(SHAPES))
async def test_each_family_refuses_an_app_installed_for_this_test_alone(
    home, sel_rows, shape
) -> None:
    """Another app's settings (through either route that writes them), another app's instance and
    the MCP Tool Servers card, each asked for both ways by Alpha, installed with Bravo for this test
    alone; and its own list, which holds only itself."""
    shared = SHAPES[shape]
    if shape == "settings":
        asked = [
            ("PATCH", f"/api/providers/{BRAVO}/config", {"endpoint": ATTACKER}, "is not this app"),
            ("PUT", f"/api/apps/{BRAVO}/config", {"endpoint": ATTACKER}, "is not this app"),
        ]
    else:
        asked = [("POST", MCP_CARD, EVIL, "MCP servers")]
    async with _gateway([]) as gw:
        await gw.install(shared.bundle(home))
        assert shared.prepare is not None
        await shared.prepare(gw, home)
        if shape == "instances":
            (instance_id,) = _ids(BRAVO)
            path = f"/api/providers/{BRAVO}/instances/{instance_id}"
            asked.insert(0, ("PUT", path, {"config": {"endpoint": ATTACKER}}, "is not this app"))
        token = await _token(gw, ALPHA)
        serving, built = _serving(BRAVO), list(gw.wire.built)
        before = home_files(home)
        answers = [await gw.both_ways(token, m, p, b) for m, p, b, _says in asked]
        written = written_since(home, before)
        serving_after, built_after = _serving(BRAVO), list(gw.wire.built)
        listed = await gw.both_ways(token, "GET", "/api/providers")
    seen = Row(gw, token, home, sel_rows, ALPHA)
    for (method, path, _body, says), sent in zip(asked, answers, strict=True):
        for status, text in sent:
            assert status == 403 and says in text, f"{method} {path}: {text}"
            assert "bravo.example" not in text
        assert len(_refusals(seen, method, path)) == 2, f"{method} {path}"
    assert not written, f"a refusal changed your home: {written}"
    assert serving == BRAVO_SERVES[shape], "Bravo runs as you configured it"
    assert serving_after == serving, "and connects where you pointed it"
    assert built_after == built, "no code of Bravo's ran for Alpha's requests"
    for status, text in listed:
        assert status == 200 and [p["name"] for p in json.loads(text)["providers"]] == [ALPHA]
