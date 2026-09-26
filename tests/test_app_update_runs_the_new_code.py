"""An app update runs the new version at once — or says a restart is needed, and why.

The app loader used to cache an app's modules by file path, and an update swaps the new files in
at the SAME path, so every re-import after an update or a reinstall handed back the old module.
Measured on origin/main 0b487d9c7 (2026-09-26) with the ``reload-probe`` app below, whose every
part names its version, updated v1 → v2 through the routes a user's clicks send (review, then
consent):

| What the app runs            | After an update on main                                     |
|------------------------------|-------------------------------------------------------------|
| a tool                       | answers ``v1``                                              |
| a model type (import-time)   | builds ``v1`` instances; v2's ``register_type`` is refused  |
| a channel receiver           | replaced at once (#3628), but the new instance is v1 code   |
| an MCP server it ships       | the v1 server process keeps answering                       |
| its backend process          | ``v2`` — a fresh process                                    |
| its background worker        | the v1 process keeps running                                |
| its UI bundle                | same URL, no revalidation header: a browser may keep v1    |
| its modules                  | every v1 module stays in ``sys.modules``                    |
| a reinstall (remove/force)   | the same: v1's cached modules answer for v2's files         |

The contract now: after an update, or an uninstall followed by a reinstall, only the new version
runs. Every lifecycle transition goes through one per-app unload/load path
(``apps/app_runtime.py``), and a part that cannot be unloaded in-process makes the update say a
restart is needed, and why.
"""

from __future__ import annotations

import asyncio
import gc
import json
import sys
import textwrap
import threading
import types
import weakref
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

# Imported before any test patches `config_dir` (see test_saved_app_settings_apply): the probe
# imports the SDK, and a module first imported under a patch keeps the mock bound.
import personalclaw.sdk.background  # noqa: F401
import personalclaw.sdk.channel  # noqa: F401
import personalclaw.sdk.model  # noqa: F401
import personalclaw.sdk.tool  # noqa: F401
from personalclaw import channel_transports
from personalclaw.apps import manager
from personalclaw.dashboard.handlers.apps import register_app_routes
from personalclaw.dashboard.handlers.channels import api_channels_list
from personalclaw.providers import registry as registry_module
from personalclaw.providers import routes as provider_routes

APP = "reload-probe"
MODEL_TYPE = "reload-probe"
WIRE = "_reload_probe_wire"
_WAIT_SECS = 60.0

# ── the probe app ──────────────────────────────────────────────────────────────────────────────
# Each part is a file whose only difference between versions is the VERSION it names. The
# in-process parts import a sibling package by its bare name (`probe_runtime`), the way every
# shipped channel app's modules import each other.

_VERSION_PY = "VERSION = {version!r}\n"

_CHANNEL_PY = '''
"""The probe's channel: its receiver says which version is receiving."""

import sys

from probe_runtime.version import VERSION

from personalclaw.sdk.channel import ChannelTransportProvider


class ProbeChannel(ChannelTransportProvider):
    def __init__(self, config=None):
        self.config = dict(config or {})
        self.entry = None

    name = property(lambda self: "reload-probe")
    display_name = property(lambda self: "Reload Probe")

    async def connect(self):
        return True

    async def disconnect(self):
        return None

    async def send(self, message):
        return True

    async def health(self):
        return {"state": "ready", "detail": "Reload probe " + VERSION + " is receiving"}

    async def start_inbound(self, services):
        self.entry = sys.modules["_reload_probe_wire"].attach(VERSION)

    async def stop_inbound(self):
        if self.entry is not None:
            sys.modules["_reload_probe_wire"].detach(self.entry)
            self.entry = None
'''

_PROVIDER_PY = '''
"""The probe's in-process code: a tool, a model type and a channel, each naming its version."""

import sys
import threading

from probe_runtime.channel import ProbeChannel
from probe_runtime.version import VERSION

from personalclaw.sdk.channel import trust_mode
from personalclaw.sdk.model import (
    Capability,
    MediaCatalog,
    ProviderCapability,
    ProviderResolutionError,
    get_default_registry,
    register_media_catalog,
)
from personalclaw.sdk.sidecar import SidecarRunner, get_runner, register_runner
from personalclaw.sdk.tool import ToolDefinition, ToolProvider, ToolResult

MODEL_TYPE = "reload-probe"


class ProbeRunner(SidecarRunner):
    """A sidecar runner, registered on first use the way voice-clone-tts registers its own."""

    version = VERSION

    def stop(self):
        sys.modules["_reload_probe_wire"].runner_stopped.append(VERSION)
        super().stop()


class VersionTools(ToolProvider):
    def __init__(self, config=None):
        self.config = dict(config or {})
        if self.config.get("linger"):
            # A thread the app starts and never stops: code the platform cannot take back.
            threading.Thread(target=_linger, name="reload-probe-linger", daemon=True).start()

    @property
    def name(self):
        return "reload-probe"

    @property
    def display_name(self):
        return "Reload Probe"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="probe_version",
                description="Which version of the reload probe answers.",
                requires_approval=False,
            )
        ]

    async def invoke(self, tool_name, arguments):
        if get_runner("reload-probe") is None:
            register_runner(ProbeRunner(app="reload-probe", worker=__file__))
        return ToolResult(success=True, output=VERSION)


def _linger():
    sys.modules["_reload_probe_wire"].linger.wait()


def create_tools(config=None):
    return VersionTools(config)


def create_channel(config=None):
    return ProbeChannel(config)


class ProbeModel:
    """What the model type builds: just enough to say which version built it."""

    def __init__(self, entry):
        self.entry = entry
        self.version = VERSION


def _factory(*, entry, session_key=None, **kwargs):
    return ProbeModel(entry)


def create_model(config=None):
    return None  # multi-instance: its entries are built through the type registered below


# Import-time registrations, written exactly the way the shipped model apps write them.
try:
    get_default_registry().register_type(
        ProviderCapability(
            type=MODEL_TYPE,
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=False,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=0,
        ),
        _factory,
    )
except ProviderResolutionError:
    pass  # already registered (idempotent against reload)

register_media_catalog("tts", MODEL_TYPE, MediaCatalog(default_model="voice-" + VERSION))


def _on_yolo_off(reason):
    sys.modules["_reload_probe_wire"].yolo_off.append(VERSION)


trust_mode.register_on_disable(_on_yolo_off)
'''

_MCP_SERVER_PY = '''
"""The probe's MCP server: a process of its own, answering with its version."""

from mcp.server.fastmcp import FastMCP

VERSION = {version!r}
server = FastMCP("reload-probe")


@server.tool()
def probe_version() -> str:
    """Which version of the reload probe's MCP server answers."""
    return VERSION


if __name__ == "__main__":
    server.run()
'''

_BACKEND_PY = '''
"""The probe's backend: a process of its own, answering with its version."""

import http.server
import os

VERSION = {version!r}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = (VERSION if self.path.startswith("/version") else "ok").encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return None


http.server.ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
'''

_WORKER_PY = {
    # Written against the SDK, the way an app author is told to write one.
    "sdk": '''
"""The probe's background worker: records which process runs it, and at which version."""

import json
import os

from personalclaw.sdk.background import BackgroundWorker, run_worker

VERSION = {version!r}


class Probe(BackgroundWorker):
    poll_interval = 0.05

    def run_once(self, ctx):
        record = {{"pid": os.getpid(), "version": VERSION}}
        (ctx.data_dir / "worker.json").write_text(json.dumps(record))


if __name__ == "__main__":
    run_worker(Probe())
''',
    # A plain loop: the supervisor starts and stops it all the same.
    "loop": '''
"""The probe's background worker as a plain loop: which process runs it, at which version."""

import json
import os
import pathlib
import time

VERSION = {version!r}
record = pathlib.Path(os.environ["PERSONALCLAW_APP_DATA_DIR"]) / "worker.json"
while True:
    record.write_text(json.dumps({{"pid": os.getpid(), "version": VERSION}}))
    time.sleep(0.05)
''',
}

_UI_MJS = "export function mount(el) {{ el.textContent = 'Reload probe {version}' }}\n"

_PARTS = ("tool", "model", "channel", "mcp", "backend", "worker", "ui")


def _probe(
    root: Path, version: str, *, parts: tuple[str, ...] = _PARTS, worker: str = "sdk"
) -> Path:
    """The probe's source directory at ``version`` (``v1`` → 1.0.0), for the Store to install."""
    semver = f"{version.lstrip('v')}.0.0"
    d = root / f"src-{version}-{'-'.join(parts)}-{worker}" / APP
    (d / "probe_runtime").mkdir(parents=True)
    (d / "probe_runtime" / "__init__.py").write_text('"""The probe\'s own package."""\n')
    (d / "probe_runtime" / "version.py").write_text(_VERSION_PY.format(version=version))
    (d / "probe_runtime" / "channel.py").write_text(textwrap.dedent(_CHANNEL_PY))
    (d / "provider.py").write_text(textwrap.dedent(_PROVIDER_PY))
    manifest: dict[str, Any] = {
        "name": APP,
        "version": semver,
        "displayName": "Reload Probe",
        "description": "Every kind of code an app runs, each naming its version.",
        "providers": [],
    }
    if "tool" in parts:
        manifest["providers"].append(
            {
                "type": "tool",
                "implementation": "provider:create_tools",
                "settingsSchema": {
                    "type": "object",
                    "properties": {"linger": {"type": "boolean", "default": False}},
                },
            }
        )
    if "model" in parts:
        manifest["providers"].append(
            {
                "type": "model",
                "implementation": "provider:create_model",
                "providerType": MODEL_TYPE,
                "multiInstance": True,
                "capabilities": ["chat"],
            }
        )
    if "channel" in parts:
        manifest["providers"].append(
            {"type": "channel", "implementation": "provider:create_channel"}
        )
    if "mcp" in parts:
        (d / "mcp_server.py").write_text(textwrap.dedent(_MCP_SERVER_PY).format(version=version))
        manifest["mcpServers"] = {
            "version": {"command": sys.executable, "args": ["mcp_server.py"], "poolable": True}
        }
    if "backend" in parts:
        (d / "backend").mkdir()
        (d / "backend" / "server.py").write_text(
            textwrap.dedent(_BACKEND_PY).format(version=version)
        )
        manifest["backend"] = {
            "entryPoint": "backend/server.py",
            "type": "python",
            "port": "auto",
            "healthCheck": "/health",
        }
    if "worker" in parts:
        (d / "worker.py").write_text(textwrap.dedent(_WORKER_PY[worker]).format(version=version))
        manifest["permissions"] = {"backgroundTasks": True, "storage": True}
    if "ui" in parts:
        (d / "ui").mkdir()
        (d / "ui" / "index.mjs").write_text(_UI_MJS.format(version=version))
        manifest["ui"] = {
            "pages": [
                {
                    "route": f"/apps/{APP}",
                    "label": "Reload Probe",
                    "entryPoint": "index.mjs",
                    "mountFunction": "mount",
                }
            ]
        }
    # The first provider is the app's `provider` (whose settings schema is the app's config
    # surface), any others hang off `providers` — the shape telegram-channel ships.
    declared = manifest.pop("providers")
    if declared:
        manifest["provider"] = declared[0]
    if declared[1:]:
        manifest["providers"] = declared[1:]
    (d / "app.json").write_text(json.dumps(manifest, indent=1))
    return d


# ── the process-wide state the probe touches ───────────────────────────────────────────────────


class _Wire:
    """Where the probe's in-process parts report: who receives, which yolo callbacks ran."""

    def __init__(self) -> None:
        self.receiving: list[dict[str, str]] = []
        self.yolo_off: list[str] = []
        self.runner_stopped: list[str] = []
        self.linger = threading.Event()

    def attach(self, version: str) -> dict[str, str]:
        entry = {"version": version}
        self.receiving.append(entry)
        return entry

    def detach(self, entry: dict[str, str]) -> None:
        if entry in self.receiving:
            self.receiving.remove(entry)


@pytest.fixture
def wire() -> Iterator[_Wire]:
    w = _Wire()
    module = types.ModuleType(WIRE)
    for attr in ("attach", "detach"):
        setattr(module, attr, getattr(w, attr))
    module.yolo_off = w.yolo_off  # type: ignore[attr-defined]
    module.runner_stopped = w.runner_stopped  # type: ignore[attr-defined]
    module.linger = w.linger  # type: ignore[attr-defined]
    sys.modules[WIRE] = module
    yield w
    w.linger.set()  # release a thread the probe left running
    sys.modules.pop(WIRE, None)


def _probe_modules(home: Path) -> dict[str, types.ModuleType]:
    """Every loaded module whose code comes from under ``home`` — the probe's, in any version."""
    here = str(home)
    out: dict[str, types.ModuleType] = {}
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None) or ""
        if isinstance(path, str) and path.startswith(here):
            out[name] = module
    return out


@pytest.fixture
def home(tmp_path, monkeypatch, wire) -> Iterator[Path]:
    """An isolated home, a fresh provider registry, and app children allowed to start.

    Teardown takes the probe back out of every process-wide registry it can reach by hand, so a
    version one test loaded can never answer in the next (main has nothing that would).
    """
    import personalclaw.config.loader as loader
    from personalclaw import mcp_client, media_catalogs, trust_mode
    from personalclaw.apps.backend_runtime import get_backend_supervisor
    from personalclaw.apps.worker_runtime import get_worker_supervisor
    from personalclaw.llm.registry import get_default_registry
    from personalclaw.local_models import sidecar
    from personalclaw.tool_providers import registry as tools

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.delenv("PERSONALCLAW_SKIP_APP_WORKERS", raising=False)
    monkeypatch.setattr(mcp_client, "_registry", None)
    registry_module.reset_provider_registry()
    yield tmp_path
    registry = registry_module.get_provider_registry()
    for name in list(registry._extensions):
        registry.disable(name)
    registry_module.reset_provider_registry()
    get_worker_supervisor().stop(APP)
    get_backend_supervisor().stop(APP)
    live = mcp_client._registry
    if live is not None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(live.shutdown_all())
        finally:
            loop.close()
    llm = get_default_registry()
    for table in (llm._factories, llm._capabilities, llm._readiness, llm._catalog_factories):
        table.pop(MODEL_TYPE, None)
    for entry in [e.name for e in llm.list_entries() if e.type == MODEL_TYPE]:
        llm.unregister_entry(entry)
    for by_type in media_catalogs._catalogs.values():
        by_type.pop(MODEL_TYPE, None)
    trust_mode._TRUST._on_disable[:] = [
        cb
        for cb in trust_mode._TRUST._on_disable
        if not str(getattr(cb, "__module__", "")).startswith("_pclaw_app_reload_probe")
    ]
    tools.unregister_provider("reload-probe")
    channel_transports.unregister_transport("reload-probe")
    sidecar._runners.pop(APP, None)
    for name in _probe_modules(tmp_path):
        sys.modules.pop(name, None)


# ── the routes a user's clicks send ────────────────────────────────────────────────────────────


class _Gateway:
    def __init__(self, client: TestClient, orch: Any) -> None:
        self.client, self.orch = client, orch

    async def call(self, method: str, path: str, body: Any = None) -> Any:
        resp = await self.client.request(method, path, json=body)
        assert resp.status < 300, f"{method} {path} → {resp.status}: {await resp.text()}"
        return await resp.json()

    async def install(self, source: Path) -> dict[str, Any]:
        review = await self.call("POST", "/api/apps/preview", {"source": str(source)})
        return await self.call(
            "POST", "/api/apps", {"source": str(source), "consent": review["consent"]}
        )

    async def update(self, source: Path) -> dict[str, Any]:
        review = await self.call("POST", "/api/apps/preview", {"source": str(source), "name": APP})
        return await self.call(
            "POST", f"/api/apps/{APP}/update", {"source": str(source), "consent": review["consent"]}
        )


@asynccontextmanager
async def _gateway():
    from personalclaw.config.loader import AppConfig
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg)
    app = web.Application()
    register_app_routes(app)
    provider_routes.register_routes(app)
    app.router.add_get("/api/channels", api_channels_list)
    async with TestClient(TestServer(app)) as client:
        orch.deliver_channel_inbound = _no_inbound
        yield _Gateway(client, orch)
        await channel_transports.unbind_inbound()  # what the gateway's shutdown does


async def _no_inbound(provider: str, msg: Any, *, is_dm: bool = True) -> None:
    return None


async def _eventually(condition, timeout: float = _WAIT_SECS) -> bool:
    """Whether ``condition()`` holds within ``timeout`` — the loop keeps running meanwhile."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        value = condition()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return True
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.05)


async def _tool_output() -> str:
    from personalclaw.tool_providers.registry import get_provider

    provider = get_provider("reload-probe")
    assert provider is not None, "the probe's tool provider is not registered"
    return (await provider.invoke("probe_version", {})).output


# ── what runs after an update ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_tool_answers_with_the_new_version_after_an_update(home):
    async with _gateway() as gw:
        await gw.install(_probe(home, "v1", parts=("tool",)))
        assert await _tool_output() == "v1"

        await gw.update(_probe(home, "v2", parts=("tool",)))
        assert await _tool_output() == "v2", "the tool still runs the version it replaced"


@pytest.mark.asyncio
async def test_a_model_type_the_app_registers_builds_the_new_version(home):
    from personalclaw.llm.registry import ProviderEntry, get_default_registry

    llm = get_default_registry()
    entry = ProviderEntry(name="probe-chat", type=MODEL_TYPE, model="m")
    async with _gateway() as gw:
        await gw.install(_probe(home, "v1", parts=("model",)))
        llm.register_entry(entry)
        assert llm.build("probe-chat").version == "v1"

        await gw.update(_probe(home, "v2", parts=("model",)))
        assert llm.build("probe-chat").version == "v2", "chat would still build the old version"


@pytest.mark.asyncio
async def test_a_channels_receiver_runs_the_new_version_after_an_update(home, wire):
    async with _gateway() as gw:
        await gw.install(_probe(home, "v1", parts=("channel",)))
        await gw.orch._start_channel_inbound()
        assert await _eventually(lambda: wire.receiving == [{"version": "v1"}], timeout=5)

        await gw.update(_probe(home, "v2", parts=("channel",)))
        assert await _eventually(
            lambda: wire.receiving == [{"version": "v2"}], timeout=5
        ), f"receiving after the update: {wire.receiving}"
        await channel_transports.settled()
        listing = await gw.call("GET", "/api/channels")
        health = next(c["health"] for c in listing["channels"] if c["name"] == "reload-probe")
        assert health["detail"] == "Reload probe v2 is receiving"


@pytest.mark.asyncio
async def test_an_mcp_server_the_app_ships_is_the_new_version_after_an_update(home):
    from personalclaw.mcp_client import get_mcp_client_registry

    async def answer() -> str:
        registry = get_mcp_client_registry()
        assert registry is not None
        conn = registry.get(f"{APP}:version")
        assert conn is not None, "the app's MCP server is not configured"
        ok, output = await conn.call_tool("probe_version", {})
        assert ok, output
        return output

    async with _gateway() as gw:
        await gw.install(_probe(home, "v1", parts=("mcp",)))
        assert await answer() == "v1"

        await gw.update(_probe(home, "v2", parts=("mcp",)))
        assert await answer() == "v2", "the old MCP server process still answers"


@pytest.mark.asyncio
async def test_the_backend_process_is_the_new_version_after_an_update(home):
    import aiohttp

    from personalclaw.apps.backend_runtime import get_backend_supervisor

    async def answer() -> str:
        async def up() -> str:
            running = get_backend_supervisor().get(APP)
            if running is None:
                return ""
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{running.base_url}/version") as resp:
                        return await resp.text()
            except aiohttp.ClientError:
                return ""

        seen: list[str] = []

        async def settled() -> bool:
            seen.append(await up())
            return bool(seen[-1])

        assert await _eventually(settled), "the backend never answered"
        return seen[-1]

    async with _gateway() as gw:
        await gw.install(_probe(home, "v1", parts=("backend",)))
        assert await answer() == "v1"

        await gw.update(_probe(home, "v2", parts=("backend",)))
        assert await answer() == "v2", "the old backend process still answers"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["sdk", "loop"])
async def test_the_background_worker_is_the_new_version_after_an_update(home, body):
    from personalclaw.apps.worker_runtime import get_worker_supervisor

    record = home / "apps" / APP / "data" / "worker.json"
    supervisor = get_worker_supervisor()

    def running(version: str) -> bool:
        supervisor.sweep()  # one watchdog pass, as the gateway's sweeper runs every few seconds
        rec = supervisor.get(APP, "worker")
        try:
            seen = json.loads(record.read_text())
        except (OSError, ValueError):
            return False
        return rec is not None and rec.is_alive() and seen == {"pid": rec.pid, "version": version}

    async with _gateway() as gw:
        await gw.install(_probe(home, "v1", parts=("worker",), worker=body))
        assert await _eventually(lambda: running("v1")), "the v1 worker never ran"

        await gw.update(_probe(home, "v2", parts=("worker",), worker=body))
        assert await _eventually(
            lambda: running("v2"), timeout=15
        ), f"after the update the worker record says {record.read_text()}"


@pytest.mark.asyncio
async def test_the_ui_bundle_url_changes_and_is_revalidated_after_an_update(home):
    async with _gateway() as gw:

        async def bundle() -> tuple[str, str, str, str]:
            detail = await gw.call("GET", f"/api/apps/{APP}")
            listed = await gw.call("GET", "/api/apps")
            summary = next(a for a in listed["apps"] if a["name"] == APP)
            revision = str(detail.get("uiRevision") or "")
            resp = await gw.client.get(f"/apps/{APP}/ui/index.mjs?v={revision}")
            assert resp.status == 200
            listed_revision = str(summary.get("uiRevision") or "")
            return (
                revision,
                listed_revision,
                await resp.text(),
                resp.headers.get("Cache-Control", ""),
            )

        await gw.install(_probe(home, "v1", parts=("ui",)))
        rev1, _listed1, body1, _cache1 = await bundle()
        assert "Reload probe v1" in body1

        await gw.update(_probe(home, "v2", parts=("ui",)))
        rev2, listed2, body2, cache2 = await bundle()
        assert "Reload probe v2" in body2
        assert rev1 and rev2 and rev1 != rev2, "the page would import the same URL again"
        assert listed2 == rev2, "the Library list and the app page would load different URLs"
        assert "no-cache" in cache2, "a browser may serve the old bundle without asking"


# ── nothing of the old version stays loaded ────────────────────────────────────────────────────


def _definitions(modules: dict[str, types.ModuleType]) -> list[weakref.ref]:
    """A weak reference to every function and class the given modules define."""
    refs: list[weakref.ref] = []
    for module in modules.values():
        for value in vars(module).values():
            if (
                isinstance(value, (types.FunctionType, type))
                and value.__module__ == module.__name__
            ):
                refs.append(weakref.ref(value))
    return refs


@pytest.mark.asyncio
async def test_no_module_of_the_old_version_is_left_loaded_or_reachable(home, wire):
    parts = ("tool", "model", "channel")
    async with _gateway() as gw:
        await gw.install(_probe(home, "v1", parts=parts))
        await gw.orch._start_channel_inbound()
        assert await _eventually(lambda: wire.receiving == [{"version": "v1"}], timeout=5)
        old = _probe_modules(home)
        assert {"probe_runtime", "probe_runtime.version", "probe_runtime.channel"} <= set(old)
        old_code = _definitions(old)
        assert len(old_code) >= 6, "vacuity floor: the probe defines at least six things"

        await gw.update(_probe(home, "v2", parts=parts))
        await channel_transports.settled()

        now = _probe_modules(home)
        survivors = sorted(n for n, m in now.items() if old.get(n) is m)
        assert survivors == [], f"v1 modules still loaded: {survivors}"
        assert sys.modules["probe_runtime.version"].VERSION == "v2"

        del old, now
        gc.collect()
        alive = [r() for r in old_code if r() is not None]
        assert (
            alive == []
        ), f"v1 code still reachable: {[getattr(o, '__qualname__', o) for o in alive]}"


@pytest.mark.asyncio
async def test_what_the_old_version_registered_is_taken_back(home, wire):
    """Import-time registrations, and one made later from the app's own call (a sidecar)."""
    from personalclaw import media_catalogs, trust_mode
    from personalclaw.local_models.sidecar import get_runner

    async with _gateway() as gw:
        await gw.install(_probe(home, "v1", parts=("tool", "model")))
        assert await _tool_output() == "v1"  # registers v1's sidecar runner on first use
        assert get_runner(APP).version == "v1"

        await gw.update(_probe(home, "v2", parts=("tool", "model")))
        assert wire.runner_stopped == ["v1"], "the old version's sidecar runner was not stopped"
        assert await _tool_output() == "v2"
        assert get_runner(APP).version == "v2"
        assert media_catalogs.get_media_catalog("tts", MODEL_TYPE).default_model == "voice-v2"
        trust_mode._TRUST._fire_disable("manual")
        assert wire.yolo_off == ["v2"], "the old version's callback still runs"


@pytest.mark.asyncio
async def test_disabling_an_app_takes_back_everything_its_code_registered(home, wire):
    """Disable is also "uninstall" (the deactivate rung): nothing of the app may keep answering."""
    from personalclaw import media_catalogs, trust_mode
    from personalclaw.llm.registry import (
        ProviderEntry,
        ProviderResolutionError,
        get_default_registry,
    )
    from personalclaw.local_models.sidecar import get_runner

    llm = get_default_registry()
    async with _gateway() as gw:
        await gw.install(_probe(home, "v1", parts=("tool", "model")))
        llm.register_entry(ProviderEntry(name="probe-chat", type=MODEL_TYPE, model="m"))
        assert await _tool_output() == "v1"

        await gw.call("POST", f"/api/apps/{APP}/disable")
        with pytest.raises(ProviderResolutionError, match="which no loaded app provides"):
            llm.build("probe-chat")
        assert wire.runner_stopped == ["v1"] and get_runner(APP) is None
        assert media_catalogs.get_media_catalog("tts", MODEL_TYPE) is None
        trust_mode._TRUST._fire_disable("manual")
        assert wire.yolo_off == []
        assert _probe_modules(home) == {}, "a disabled app's code is still loaded"


@pytest.mark.asyncio
async def test_an_app_updated_while_disabled_runs_the_new_version_when_enabled(home):
    async with _gateway() as gw:
        await gw.install(_probe(home, "v1", parts=("tool",)))
        assert await _tool_output() == "v1"
        await gw.call("POST", f"/api/apps/{APP}/disable")

        await gw.update(_probe(home, "v2", parts=("tool",)))
        await gw.call("POST", f"/api/apps/{APP}/enable")
        assert await _tool_output() == "v2"


@pytest.mark.asyncio
@pytest.mark.parametrize("rung", ["?remove=1", "?force=1"])
async def test_a_reinstall_runs_only_the_new_version(home, rung):
    from personalclaw.llm.registry import ProviderEntry, get_default_registry

    llm = get_default_registry()
    async with _gateway() as gw:
        await gw.install(_probe(home, "v1", parts=("tool", "model")))
        llm.register_entry(ProviderEntry(name="probe-chat", type=MODEL_TYPE, model="m"))
        assert await _tool_output() == "v1"
        assert llm.build("probe-chat").version == "v1"

        await gw.call("DELETE", f"/api/apps/{APP}{rung}")
        await gw.install(_probe(home, "v2", parts=("tool", "model")))
        assert await _tool_output() == "v2"
        assert llm.build("probe-chat").version == "v2"


# ── what cannot be unloaded says so ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_update_that_leaves_old_code_running_says_a_restart_is_needed(home, wire):
    async with _gateway() as gw:
        await gw.install(_probe(home, "v1", parts=("tool",)))
        await gw.call("PUT", f"/api/apps/{APP}/config", {"linger": True})
        assert await _eventually(
            lambda: any(t.name == "reload-probe-linger" for t in threading.enumerate()), timeout=5
        )

        result = await gw.update(_probe(home, "v2", parts=("tool",)))
        assert result["ok"] is True
        assert result["restart_required"] is True
        assert "reload-probe-linger" in result["restart_reason"], result["restart_reason"]
        assert await _tool_output() == "v2", "what could be reloaded still runs the new version"

        status = await gw.call("GET", f"/api/apps/{APP}")
        assert status["restartReason"] == result["restart_reason"]


def _app_processes() -> dict[str, Any]:
    """The probe's backend and worker processes, as their supervisors started them."""
    from personalclaw.apps.backend_runtime import get_backend_supervisor
    from personalclaw.apps.worker_runtime import get_worker_supervisor

    backend = get_backend_supervisor().get(APP)
    worker = get_worker_supervisor().get(APP, "worker")
    assert backend is not None and backend.proc is not None, "the backend never started"
    assert worker is not None and worker.proc is not None, "the worker never started"
    return {"backend": backend.proc, "worker": worker.proc}


@pytest.mark.asyncio
async def test_the_restart_it_asks_for_leaves_no_app_process_of_the_image_it_replaces(home):
    """A Restart re-executes the gateway in place (``os.execve``, same PID), so a process the old
    image left running stays a child of the new one — which does not supervise it (its tables
    start empty) and does not reap it at boot (only a process whose parent died counts as an
    orphan there).

    Measured driving the real gateway: after one Restart the probe ran two backends and two
    workers, and after three, four of each. The extra ones kept the version of their Restart, so
    an update after it stopped only the new image's processes: ``worker.json`` was still being
    written by the v4 worker after the updates to v5 and v6.
    """
    from personalclaw.dashboard.handlers import updates

    async with _gateway() as gw:
        await gw.install(_probe(home, "v1", parts=("backend", "worker")))
        children = _app_processes()

        at_exec: dict[str, bool] = {}

        def execve(*_args: Any) -> None:  # where the new image would take over this PID
            at_exec.update({part: proc.poll() is None for part, proc in children.items()})

        async def close_all() -> None:
            return None

        state = types.SimpleNamespace(sessions=types.SimpleNamespace(close_all=close_all))
        with patch.object(updates.os, "execve", execve):
            await updates._graceful_reexec(state)  # type: ignore[arg-type]

    assert at_exec == {
        "backend": False,
        "worker": False,
    }, f"still running when the new image took over: {at_exec}"


@pytest.mark.asyncio
async def test_a_stopped_gateway_leaves_no_app_process_running(home, monkeypatch):
    """Measured on the dev gateway: Ctrl-C stopped every app backend and left every app worker
    running, re-parented to init, until the next boot reaped it — an app's background work went
    on with no gateway. The stop now ends both, through the same ``stop_processes`` a Restart
    runs."""
    import aiohttp

    from personalclaw.dashboard.server import start_dashboard

    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")
    source = str(_probe(home, "v1", parts=("backend", "worker")))
    runner, _state = await start_dashboard(sessions=MagicMock(count=0), port=0)
    try:
        host, port = runner.addresses[0][:2]
        base = f"http://{host}:{port}"
        async with aiohttp.ClientSession() as http:
            async with http.post(f"{base}/api/apps/preview", json={"source": source}) as resp:
                review = await resp.json()
            body = {"source": source, "consent": review["consent"]}
            async with http.post(f"{base}/api/apps", json=body) as resp:
                assert resp.status == 201, await resp.text()
        children = _app_processes()
    finally:
        await runner.cleanup()

    still_running = {part: proc.poll() is None for part, proc in children.items()}
    assert still_running == {
        "backend": False,
        "worker": False,
    }, f"still running after the gateway stopped: {still_running}"


@pytest.mark.asyncio
async def test_an_app_process_that_will_not_stop_leaves_no_other_running(home):
    """``stop_processes`` is every exit's teardown, so one app must not be able to spoil it.

    Measured in a broad test run: another suite had left a backend record whose process raised
    when stopped, ``stop_all`` gave up at that app, and the probe's backend outlived the stop.
    """
    from personalclaw.apps import app_runtime
    from personalclaw.apps.backend_runtime import RunningBackend, get_backend_supervisor
    from personalclaw.apps.worker_runtime import SupervisedWorker, get_worker_supervisor

    class _Stubborn:
        pid = 0

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            raise RuntimeError("will not stop")

    async with _gateway() as gw:
        await gw.install(_probe(home, "v1", parts=("backend", "worker")))
        children = _app_processes()
        # Ahead of the probe in both tables: a backend is stopped in insertion order and the
        # workers app by app in name order.
        backends = get_backend_supervisor()._procs
        ours = dict(backends)
        backends.clear()
        backends["aaa-stubborn"] = RunningBackend(
            name="aaa-stubborn", port=1, pid=0, proc=_Stubborn()  # type: ignore[arg-type]
        )
        backends.update(ours)
        stubborn = SupervisedWorker(app="aaa-stubborn", worker="w", entry=home / "none.py")
        stubborn.proc = _Stubborn()  # type: ignore[assignment]
        get_worker_supervisor()._workers[stubborn.key] = stubborn
        try:
            app_runtime.stop_processes()
        finally:
            backends.pop("aaa-stubborn", None)
            get_worker_supervisor()._workers.pop(stubborn.key, None)

    still_running = {part: proc.poll() is None for part, proc in children.items()}
    assert still_running == {
        "backend": False,
        "worker": False,
    }, f"one app's failed stop kept these running: {still_running}"
