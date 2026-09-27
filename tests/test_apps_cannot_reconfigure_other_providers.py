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

Every app-side case goes through the REAL ``app_permission_middleware`` in front of the REAL app
and provider routes, for fixture apps the owner installed through preview → consent → install
(``POST /api/apps/preview``, then ``POST /api/apps`` with its digest), and reads the Security
Event Log row that names the app. Names this change adds are imported inside the tests, so on a
tree without them each test fails on its own rather than the module failing to collect.
"""

from __future__ import annotations

import json
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Iterator
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

# Imported before any test patches `config_dir`: a module first imported under a patch keeps the
# mock bound.
import personalclaw.sdk.tool  # noqa: F401
from personalclaw.apps import manager
from personalclaw.dashboard.handlers.apps import register_app_routes
from personalclaw.providers import instance_routes
from personalclaw.providers import registry as registry_module
from personalclaw.providers import routes as provider_routes

ALPHA = "probe-alpha"
BRAVO = "probe-bravo"
#: The first-party MCP Tool Servers app's name (`providers/mcp_instances.MCP_TOOLS_EXTENSION`).
MCP_TOOLS = "mcp-tools"
#: The test gateway's stand-in for an app token's claim: the dev middleware sets `request["app"]`
#: from a verified app token, and this sets it from a header, so one gateway serves you and both
#: apps.
AS_APP = "X-Probe-App"
#: Where a hostile write points another app's provider.
ATTACKER = "https://collector.attacker.example/v1"
#: The key you gave Bravo, which Bravo's provider connects with.
BRAVO_KEY = "sk-bravo-0123456789"
WIRE = "_providerscope_wire"

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


# ── the process-wide state the fixtures reach ─────────────────────────────────────────────────


class _Board:
    """The availability board, without the child process that measures: every card is available."""

    def __init__(self) -> None:
        self.rechecked: list[str] = []

    def read(self, name: str, implementation: str) -> Any:
        from personalclaw.providers.availability import AVAILABLE, Availability

        return Availability(AVAILABLE)

    def recheck(self, name: str) -> None:
        self.rechecked.append(name)


@pytest.fixture
def wire() -> Iterator[types.ModuleType]:
    module = types.ModuleType(WIRE)
    module.built = []  # type: ignore[attr-defined]
    sys.modules[WIRE] = module
    yield module
    sys.modules.pop(WIRE, None)


@pytest.fixture
def board(monkeypatch) -> _Board:
    fake = _Board()
    monkeypatch.setattr(provider_routes, "get_availability_board", lambda: fake)
    return fake


@pytest.fixture
def home(tmp_path, monkeypatch, wire, board) -> Iterator[Path]:
    """An isolated home and a fresh provider registry. Teardown takes every fixture provider back
    out of the registries it reached, so no test answers for the next."""
    import personalclaw.config.loader as loader
    from personalclaw import mcp_client
    from personalclaw.tool_providers import registry as tools

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(mcp_client, "_registry", None)
    registry_module.reset_provider_registry()
    yield tmp_path
    registry = registry_module.get_provider_registry()
    for name in list(registry._extensions):
        registry.disable(name)
    registry_module.reset_provider_registry()
    for provider in list(tools.list_providers()):
        if str(getattr(provider, "name", "")).startswith((ALPHA, BRAVO, MCP_TOOLS)):
            tools.unregister_provider(provider)
    here = str(tmp_path)
    for name, module in list(sys.modules.items()):
        if str(getattr(module, "__file__", "") or "").startswith(here):
            sys.modules.pop(name, None)


@pytest.fixture
def sel_rows():
    """Every row the permission middleware writes (it imports ``sel`` at the refusal)."""
    fake = MagicMock()
    with patch("personalclaw.sel.sel", return_value=fake):
        yield fake.log_api_access


def _denials(rows: MagicMock, app: str, path: str) -> list:
    return [
        c
        for c in rows.call_args_list
        if c.kwargs.get("caller") == f"app:{app}"
        and c.kwargs.get("outcome") == "denied"
        and c.kwargs.get("resources") == path
    ]


# ── the gateway a user's clicks and an app's backend reach ─────────────────────────────────────


class _Gateway:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    async def call(
        self, method: str, path: str, body: Any = None, *, as_app: str = ""
    ) -> tuple[int, Any]:
        headers = {AS_APP: as_app} if as_app else {}
        resp = await self.client.request(method, path, json=body, headers=headers)
        text = await resp.text()
        try:
            return resp.status, json.loads(text)
        except ValueError:
            return resp.status, text

    async def ok(self, method: str, path: str, body: Any = None, *, as_app: str = "") -> Any:
        status, data = await self.call(method, path, body, as_app=as_app)
        assert status < 300, f"{method} {path} as {as_app or 'you'} → {status}: {data}"
        return data

    async def install(self, source: Path) -> None:
        """The owner's install: review, then install with the digest the review returned."""
        review = await self.ok("POST", "/api/apps/preview", {"source": str(source)})
        assert review.get("consent"), review
        await self.ok("POST", "/api/apps", {"source": str(source), "consent": review["consent"]})

    async def add_instance(self, app: str, endpoint: str, *, as_app: str = "") -> str:
        body = {"display_name": "Main", "config": {"endpoint": endpoint, "api_key": "sk-main"}}
        made = await self.ok("POST", f"/api/providers/{app}/instances", body, as_app=as_app)
        return str(made["instance"]["id"])


@asynccontextmanager
async def _gateway() -> AsyncIterator[_Gateway]:
    from personalclaw.dashboard.server import app_permission_middleware

    @web.middleware
    async def identity(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        caller = request.headers.get(AS_APP, "")
        if caller:
            request["app"] = caller
        return await handler(request)

    app = web.Application(middlewares=[identity, app_permission_middleware])
    register_app_routes(app)
    provider_routes.register_routes(app)
    instance_routes.register_instance_routes(app)
    async with TestClient(TestServer(app)) as client:
        yield _Gateway(client)


async def _installed(gw: _Gateway, home: Path, shape: str, *names: str) -> None:
    for name in names:
        await gw.install(_bundle(home, name, shape=shape))


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


def _mcp_servers(home: Path) -> set[str]:
    """The servers ``mcp.json`` names — what the gateway launches."""
    path = home / "mcp.json"
    if not path.exists():
        return set()
    return set(json.loads(path.read_text()).get("mcpServers", {}))


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


async def _victim(gw: _Gateway, home: Path, shape: str) -> str:
    """Install Alpha and Bravo in *shape*, configure Bravo as you would, and return the id of
    Bravo's instance (``""`` for the settings shape)."""
    await _installed(gw, home, shape, ALPHA, BRAVO)
    if shape == "settings":
        yours = {"endpoint": "https://bravo.example", "api_key": BRAVO_KEY}
        await gw.ok("PATCH", f"/api/providers/{BRAVO}/config", yours)
        return ""
    return await gw.add_instance(BRAVO, "https://bravo.example")


class TestAnAppCannotReconfigureAnotherAppsProvider:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "template", "shape", "body"), PROVIDER_ROUTES, ids=lambda v: str(v)
    )
    async def test_every_route_refuses_another_apps_provider(
        self, home, sel_rows, wire, board, method, template, shape, body
    ) -> None:
        async with _gateway() as gw:
            instance_id = await _victim(gw, home, shape)
            built = list(wire.built)
            path = template.replace("{name}", BRAVO).replace("{id}", instance_id)
            status, answer = await gw.call(method, path, body, as_app=ALPHA)
        assert status == 403, answer
        assert "is not this app" in str(answer), answer
        assert _denials(sel_rows, ALPHA, path), "the refusal leaves an SEL row naming the app"
        assert "bravo.example" not in str(answer), "nothing of Bravo's settings comes back"
        assert wire.built == built, "no code of Bravo's ran for Alpha's request"
        assert board.rechecked == []

    @pytest.mark.asyncio
    async def test_its_provider_keeps_connecting_where_you_pointed_it(self, home, sel_rows) -> None:
        """The write names only the endpoint. The key stays Bravo's, so a provider rebuilt from
        the result connects to the attacker with Bravo's key."""
        async with _gateway() as gw:
            await _victim(gw, home, "settings")
            status, _answer = await gw.call(
                "PATCH", f"/api/providers/{BRAVO}/config", {"endpoint": ATTACKER}, as_app=ALPHA
            )
        live = registry_module.get_provider_registry().get(BRAVO).provider_instance
        assert live.config == {"endpoint": "https://bravo.example", "api_key": BRAVO_KEY}
        assert _stored(BRAVO)["endpoint"] == "https://bravo.example"
        assert status == 403

    @pytest.mark.asyncio
    async def test_its_instances_are_not_repointed_added_or_removed(self, home, sel_rows) -> None:
        async with _gateway() as gw:
            instance_id = await _victim(gw, home, "instances")
            base = f"/api/providers/{BRAVO}/instances"
            repoint = await gw.call(
                "PUT", f"{base}/{instance_id}", {"config": {"endpoint": ATTACKER}}, as_app=ALPHA
            )
            add = await gw.call(
                "POST",
                base,
                {"display_name": "Mine", "config": {"endpoint": ATTACKER}},
                as_app=ALPHA,
            )
            remove = await gw.call("DELETE", f"{base}/{instance_id}", as_app=ALPHA)
        assert [repoint[0], add[0], remove[0]] == [403, 403, 403], (repoint, add, remove)
        assert _ids(BRAVO) == [instance_id]
        assert _instance(BRAVO, instance_id).config["endpoint"] == "https://bravo.example"
        (served,) = _tool_providers(BRAVO)
        assert served.config["endpoint"] == "https://bravo.example", "the running provider too"


# ── 2. What an app reads is its own provider ────────────────────────────────────────────────────


class TestAnAppReadsOnlyItsOwnProvider:
    @pytest.mark.asyncio
    async def test_the_list_holds_only_the_apps_own(self, home) -> None:
        async with _gateway() as gw:
            await _installed(gw, home, "settings", ALPHA, BRAVO)
            as_alpha = await gw.ok("GET", "/api/providers", as_app=ALPHA)
            tools_only = await gw.ok("GET", "/api/providers?type=tool", as_app=ALPHA)
            yours = await gw.ok("GET", "/api/providers")
        assert [p["name"] for p in as_alpha["providers"]] == [ALPHA]
        assert [p["name"] for p in tools_only["providers"]] == [ALPHA]
        names = {p["name"] for p in yours["providers"]}
        assert {ALPHA, BRAVO, "personalclaw-filesystem"} <= names, "you still list every one"

    @pytest.mark.asyncio
    async def test_head_is_the_same_read(self, home, sel_rows) -> None:
        async with _gateway() as gw:
            await _victim(gw, home, "settings")
            resp = await gw.client.head(f"/api/providers/{BRAVO}/config", headers={AS_APP: ALPHA})
        assert resp.status == 403
        assert _denials(sel_rows, ALPHA, f"/api/providers/{BRAVO}/config")


# ── 3. You reach every provider, and an app reaches its own ─────────────────────────────────────


class TestYoursAndTheAppsOwn:
    @pytest.mark.asyncio
    async def test_you_reconfigure_any_apps_provider(self, home, board) -> None:
        async with _gateway() as gw:
            await _victim(gw, home, "settings")
            await gw.ok(
                "PATCH", f"/api/providers/{BRAVO}/config", {"endpoint": "https://b2.example"}
            )
            await gw.ok("GET", f"/api/providers/{BRAVO}/config")
            await gw.ok("POST", f"/api/providers/{BRAVO}/availability")
        assert _stored(BRAVO)["endpoint"] == "https://b2.example"
        assert board.rechecked == [BRAVO]

    @pytest.mark.asyncio
    async def test_you_manage_any_apps_instances(self, home) -> None:
        async with _gateway() as gw:
            instance_id = await _victim(gw, home, "instances")
            base = f"/api/providers/{BRAVO}/instances"
            await gw.ok(
                "PUT", f"{base}/{instance_id}", {"config": {"endpoint": "https://b2.example"}}
            )
            await gw.ok("POST", f"{base}/{instance_id}/test")
            second = await gw.add_instance(BRAVO, "https://b3.example")
            await gw.ok("DELETE", f"{base}/{second}")
        assert _ids(BRAVO) == [instance_id]
        (served,) = _tool_providers(BRAVO)
        assert served.config["endpoint"] == "https://b2.example"

    @pytest.mark.asyncio
    async def test_an_app_reconfigures_its_own_provider(self, home, board) -> None:
        async with _gateway() as gw:
            await _installed(gw, home, "settings", ALPHA, BRAVO)
            path = f"/api/providers/{ALPHA}"
            await gw.ok("PATCH", f"{path}/config", {"endpoint": "https://a.example"}, as_app=ALPHA)
            read = await gw.ok("GET", f"{path}/config", as_app=ALPHA)
            await gw.ok("GET", path, as_app=ALPHA)
            await gw.ok("GET", f"{path}/schema", as_app=ALPHA)
            await gw.ok("POST", f"{path}/availability", as_app=ALPHA)
        assert read["config"]["endpoint"] == "https://a.example"
        assert _stored(ALPHA)["endpoint"] == "https://a.example"
        live = registry_module.get_provider_registry().get(ALPHA).provider_instance
        assert live.config["endpoint"] == "https://a.example", "the save reached its provider"
        assert board.rechecked == [ALPHA]

    @pytest.mark.asyncio
    async def test_an_app_manages_its_own_instances(self, home) -> None:
        async with _gateway() as gw:
            await _installed(gw, home, "instances", ALPHA, BRAVO)
            base = f"/api/providers/{ALPHA}/instances"
            first = await gw.add_instance(ALPHA, "https://a1.example", as_app=ALPHA)
            second = await gw.add_instance(ALPHA, "https://a2.example", as_app=ALPHA)
            await gw.ok(
                "PUT",
                f"{base}/{first}",
                {"config": {"endpoint": "https://a3.example"}},
                as_app=ALPHA,
            )
            await gw.ok("POST", f"{base}/{first}/test", as_app=ALPHA)
            await gw.ok("DELETE", f"{base}/{second}", as_app=ALPHA)
            listed = await gw.ok("GET", base, as_app=ALPHA)
            one = await gw.ok("GET", f"{base}/{first}", as_app=ALPHA)
        assert [i["id"] for i in listed["instances"]] == [first]
        assert one["instance"]["config"]["endpoint"] == "https://a3.example"
        assert "sk-main" not in json.dumps(listed), "its own key stays masked"
        (served,) = _tool_providers(ALPHA)
        assert served.config["endpoint"] == "https://a3.example"


# ── 4. The MCP Tool Servers card is every server the gateway launches ──────────────────────────


class TestTheMcpToolServersCardIsYours:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("caller", [MCP_TOOLS, ALPHA])
    async def test_no_app_defines_or_reads_a_server_through_it(
        self, home, sel_rows, caller
    ) -> None:
        async with _gateway() as gw:
            await _installed(gw, home, "instances", MCP_TOOLS, ALPHA)
            base = f"/api/providers/{MCP_TOOLS}/instances"
            evil = {
                "display_name": "evil",
                "config": {"transport": "stdio", "command": "sh", "args": "-c id"},
            }
            made = await gw.call("POST", base, evil, as_app=caller)
            listed = await gw.call("GET", base, as_app=caller)
            yours = await gw.call("GET", base)
        assert made[0] == 403, made
        assert listed[0] == 403, listed
        assert "evil" not in _mcp_servers(home), "no server was defined"
        assert _denials(sel_rows, caller, base)
        assert yours[0] == 200, "you still reach the card"

    @pytest.mark.asyncio
    async def test_the_app_named_for_it_is_refused_as_owner_only(self, home, sel_rows) -> None:
        """The ownership rule alone would pass an app installed under the name ``mcp-tools``: the
        card's instances are not that app's, they are every server in ``mcp.json``."""
        async with _gateway() as gw:
            await _installed(gw, home, "instances", MCP_TOOLS)
            status, text = await gw.call(
                "POST",
                f"/api/providers/{MCP_TOOLS}/instances",
                {"display_name": "evil", "config": {"command": "sh"}},
                as_app=MCP_TOOLS,
            )
        assert status == 403
        assert "owner-only capability" in str(text), text
        assert "MCP servers" in str(text), text


# ── 5. A disabled app stays off when one of its instances changes ──────────────────────────────


async def _switched_off(gw: _Gateway, home: Path) -> str:
    """Alpha installed, running one instance, then switched off from the Apps page."""
    await _installed(gw, home, "instances", ALPHA)
    instance_id = await gw.add_instance(ALPHA, "https://a1.example")
    assert _live(ALPHA) and _tool_providers(ALPHA), "it ran while it was on"
    await gw.ok("POST", f"/api/apps/{ALPHA}/disable")
    assert not _live(ALPHA) and not _tool_providers(ALPHA)
    return instance_id


def _off(built_before: list, wire: types.ModuleType) -> None:
    assert not _live(ALPHA), "its provider stays off"
    assert not _tool_providers(ALPHA), "none of its tools is offered"
    assert wire.built == built_before, "none of its code ran"
    assert manager._read_installed(ALPHA).enabled is False, "the app is still disabled"


class TestADisabledAppStaysOff:
    @pytest.mark.asyncio
    async def test_editing_its_instance_saves_and_loads_nothing(self, home, wire) -> None:
        async with _gateway() as gw:
            instance_id = await _switched_off(gw, home)
            built = list(wire.built)
            await gw.ok(
                "PUT",
                f"/api/providers/{ALPHA}/instances/{instance_id}",
                {"config": {"endpoint": "https://a2.example"}},
            )
        assert _instance(ALPHA, instance_id).config["endpoint"] == "https://a2.example", "saved"
        _off(built, wire)

    @pytest.mark.asyncio
    async def test_adding_or_removing_an_instance_loads_nothing(self, home, wire) -> None:
        async with _gateway() as gw:
            instance_id = await _switched_off(gw, home)
            built = list(wire.built)
            added = await gw.add_instance(ALPHA, "https://a2.example")
            _off(built, wire)
            await gw.ok("DELETE", f"/api/providers/{ALPHA}/instances/{instance_id}")
        assert _ids(ALPHA) == [added]
        _off(built, wire)

    @pytest.mark.asyncio
    async def test_switching_it_on_runs_the_instance_it_saved(self, home, wire) -> None:
        async with _gateway() as gw:
            instance_id = await _switched_off(gw, home)
            await gw.ok(
                "PUT",
                f"/api/providers/{ALPHA}/instances/{instance_id}",
                {"config": {"endpoint": "https://a2.example"}},
            )
            await gw.ok("POST", f"/api/apps/{ALPHA}/enable")
        (served,) = _tool_providers(ALPHA)
        assert served.config["endpoint"] == "https://a2.example"

    @pytest.mark.asyncio
    async def test_a_running_apps_instance_change_still_takes_effect(self, home) -> None:
        async with _gateway() as gw:
            await _installed(gw, home, "instances", ALPHA)
            instance_id = await gw.add_instance(ALPHA, "https://a1.example")
            await gw.ok(
                "PUT",
                f"/api/providers/{ALPHA}/instances/{instance_id}",
                {"config": {"endpoint": "https://a2.example"}},
            )
        (served,) = _tool_providers(ALPHA)
        assert served.config["endpoint"] == "https://a2.example"


# ── 6. An app this core cannot host stays refused when its settings change ─────────────────────


async def _refused_at_startup(gw: _Gateway, home: Path, shape: str) -> str:
    """Alpha installed and running, then the gateway restarted on a core older than Alpha's floor:
    startup lists it off with the reason, its app still enabled (``app_runtime._refuse``)."""
    from personalclaw.apps import app_runtime

    await _installed(gw, home, shape, ALPHA)
    instance_id = await gw.add_instance(ALPHA, "https://a1.example") if shape == "instances" else ""
    manifest_path = manager.app_dir(ALPHA) / "app.json"
    registry_module.get_provider_registry().disable(ALPHA)
    registry_module.reset_provider_registry()
    manifest = json.loads(manifest_path.read_text())
    manifest["minPersonalClawVersion"] = "99.0.0"
    manifest_path.write_text(json.dumps(manifest))
    app_runtime.start_installed(gateway=False)
    ext = registry_module.get_provider_registry().get(ALPHA)
    assert ext is not None and not ext.enabled and ext.error, "startup refused it"
    assert manager._read_installed(ALPHA).enabled is True
    return instance_id


class TestAnAppThisCoreCannotHostStaysOff:
    @pytest.mark.asyncio
    async def test_a_settings_save_does_not_start_it(self, home, wire) -> None:
        async with _gateway() as gw:
            await _refused_at_startup(gw, home, "settings")
            built = list(wire.built)
            await gw.ok(
                "PATCH", f"/api/providers/{ALPHA}/config", {"endpoint": "https://a2.example"}
            )
        assert _stored(ALPHA)["endpoint"] == "https://a2.example", "saved"
        assert not _live(ALPHA)
        assert "99.0.0" in registry_module.get_provider_registry().get(ALPHA).error
        assert wire.built == built, "none of its code ran"

    @pytest.mark.asyncio
    async def test_an_instance_change_does_not_start_it(self, home, wire) -> None:
        async with _gateway() as gw:
            instance_id = await _refused_at_startup(gw, home, "instances")
            built = list(wire.built)
            await gw.ok(
                "PUT",
                f"/api/providers/{ALPHA}/instances/{instance_id}",
                {"config": {"endpoint": "https://a2.example"}},
            )
        assert not _live(ALPHA) and not _tool_providers(ALPHA)
        assert wire.built == built, "none of its code ran"


# ── 7. An app's own settings route holds it to its own, the same way ───────────────────────────


class TestAnAppWritesOnlyItsOwnSettings:
    """``/api/apps/{name}/config`` writes the file ``PATCH /api/providers/{name}/config`` does, and
    is held to the calling app by the same row, before its handler runs (#3614 checked it inside
    the handler)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["GET", "PUT"])
    async def test_another_apps_settings_are_refused(self, home, sel_rows, method) -> None:
        async with _gateway() as gw:
            await _victim(gw, home, "settings")
            body = {"endpoint": ATTACKER} if method == "PUT" else None
            path = f"/api/apps/{BRAVO}/config"
            status, text = await gw.call(method, path, body, as_app=ALPHA)
        assert status == 403, text
        assert "is not this app" in str(text), text
        assert "bravo.example" not in str(text)
        assert _stored(BRAVO)["endpoint"] == "https://bravo.example"
        assert _denials(sel_rows, ALPHA, path)

    @pytest.mark.asyncio
    async def test_its_own_settings_are_its_own(self, home) -> None:
        async with _gateway() as gw:
            await _installed(gw, home, "settings", ALPHA)
            await gw.ok(
                "PUT", f"/api/apps/{ALPHA}/config", {"endpoint": "https://a.example"}, as_app=ALPHA
            )
            read = await gw.ok("GET", f"/api/apps/{ALPHA}/config", as_app=ALPHA)
        assert read["config"]["endpoint"] == "https://a.example"


# ── 8. The route table declares every provider route ───────────────────────────────────────────


def _provider_census() -> set[str]:
    from personalclaw.manifest_reference import _routes_from_ast

    return {
        f"{r['method']} {r['path']}"
        for r in _routes_from_ast()
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
