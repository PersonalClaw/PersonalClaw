"""Gateway startup runs an app exactly as enabling it does — and so does the Providers switch.

#3645 routed install, enable, update, disable and the three uninstall rungs through one
unload/load (``apps/app_runtime.py``). Two entry points still loaded apps their own way, measured
on origin/main 33d20e10f (2026-09-26) with the ``boot-probe`` app below:

* **Gateway startup** (``providers.loader.load_all_extensions``) registered no proposal kinds and
  started no background worker (the first watchdog sweep comes 30 s later); it skipped every app
  that declares its providers under ``providers`` alone — no tool, no model type, no channel, no
  prompts; and it imported the code of an enabled app this core cannot host.
* **The Settings → Providers switch** (``POST /api/providers/{name}/enable|disable``) toggled the
  provider instances only. Off left the app's modules loaded and its model type building, and the
  Apps page still said the app was on; on handed back the module already loaded, so a changed
  file never ran — not even the fix to a provider whose start had failed.

Startup now loads every enabled app through ``app_runtime.load`` (all of them in one call: every
app's code first, then every app's processes). The switch is its app's enable and disable — the
web client's ``setActivation``, the Apps page's own call (``providerSwitchIsItsApp.test.tsx``) —
and the two provider routes are gone: an app that declared ``/api/providers`` could reach them,
and switch another app's code on, which ``POST /api/apps/{name}/enable`` leaves to the owner.
"""

from __future__ import annotations

import json
import sys
import textwrap
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

# Imported before any test patches `config_dir`: the probe imports the SDK, and a module first
# imported under a patch keeps the mock bound.
import personalclaw.sdk.channel  # noqa: F401
import personalclaw.sdk.model  # noqa: F401
import personalclaw.sdk.sandbox  # noqa: F401
import personalclaw.sdk.tool  # noqa: F401
from personalclaw import channel_transports
from personalclaw.apps import manager
from personalclaw.providers import registry as registry_module

APP = "boot-probe"
MODEL_TYPE = "boot-probe"
TIER_APP = "zzz-tier"
SANDBOXED_APP = "aaa-sandboxed"

# ── the probe app: a tool, a model type and a channel, each naming its version ─────────────────

_PROVIDER_PY = '''
"""The probe's in-process code: a tool, a model type and a channel, each naming its version."""

from probe_runtime.version import VERSION

from personalclaw.sdk.channel import ChannelTransportProvider
from personalclaw.sdk.model import (
    Capability,
    ProviderCapability,
    ProviderResolutionError,
    get_default_registry,
)
from personalclaw.sdk.tool import ToolDefinition, ToolProvider, ToolResult


class VersionTools(ToolProvider):
    version = VERSION

    @property
    def name(self):
        return "boot-probe"

    @property
    def display_name(self):
        return "Boot Probe"

    async def list_tools(self):
        return [ToolDefinition(name="probe_version", description="Which version answers.")]

    async def invoke(self, tool_name, arguments):
        return ToolResult(success=True, output=VERSION)


class ProbeChannel(ChannelTransportProvider):
    name = property(lambda self: "boot-probe")
    display_name = property(lambda self: "Boot Probe")

    async def connect(self):
        return True

    async def disconnect(self):
        return None

    async def send(self, message):
        return True

    async def health(self):
        return {"state": "offline", "detail": "Boot probe " + VERSION}

    async def start_inbound(self, services):
        return None

    async def stop_inbound(self):
        return None


class ProbeModel:
    def __init__(self, entry):
        self.version = VERSION


def create_tools(config=None):
    if (config or {}).get("broken"):
        raise RuntimeError("the probe was told to fail")
    return VersionTools()


def create_channel(config=None):
    return ProbeChannel()


def create_model(config=None):
    return None  # multi-instance: its entries are built through the type registered below


try:
    get_default_registry().register_type(
        ProviderCapability(
            type="boot-probe",
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=False,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=0,
        ),
        lambda *, entry, session_key=None, **kw: ProbeModel(entry),
    )
except ProviderResolutionError:
    pass  # already registered — the shape every shipped model app has
'''

_PROMPT_YAML = """\
_entity: prompt
use_case: boot_probe_summary
name: boot-probe-summary
kind: user
category: internal
description: Summarise for the boot probe.
content: Summarise this.
"""

_BACKEND_PY = """\
import http.server
import os

http.server.ThreadingHTTPServer(
    ("127.0.0.1", int(os.environ["PORT"])), http.server.BaseHTTPRequestHandler
).serve_forever()
"""

_WORKER_PY = """\
import time

while True:
    time.sleep(0.05)
"""

_TOOL = {
    "type": "tool",
    "implementation": "provider:create_tools",
    "settingsSchema": {
        "type": "object",
        "properties": {"broken": {"type": "boolean", "default": False}},
    },
}
_MODEL = {
    "type": "model",
    "implementation": "provider:create_model",
    "providerType": MODEL_TYPE,
    "multiInstance": True,
    "capabilities": ["chat"],
}
_CHANNEL = {"type": "channel", "implementation": "provider:create_channel"}


def _write_probe(root: Path, version: str, *, shape: str = "provider", **extra: Any) -> Path:
    """The probe's files at *version* under *root*. ``shape`` is how it declares its providers:
    ``provider`` (the first as ``provider``, the rest under ``providers`` — telegram-channel's
    shape) or ``providers`` (all of them under ``providers``)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "probe_runtime").mkdir(exist_ok=True)
    (root / "probe_runtime" / "__init__.py").write_text("")
    (root / "probe_runtime" / "version.py").write_text(f"VERSION = {version!r}\n")
    (root / "provider.py").write_text(textwrap.dedent(_PROVIDER_PY))
    (root / "prompts").mkdir(exist_ok=True)
    (root / "prompts" / "summary.yaml").write_text(_PROMPT_YAML)
    (root / "mcp_server.py").write_text("import time\ntime.sleep(3600)\n")
    (root / "backend").mkdir(exist_ok=True)
    (root / "backend" / "server.py").write_text(_BACKEND_PY)
    (root / "worker.py").write_text(_WORKER_PY)
    providers = [_TOOL, _MODEL, _CHANNEL]
    manifest: dict[str, Any] = {
        "name": APP,
        "version": f"{version.lstrip('v')}.0.0",
        "displayName": "Boot Probe",
        "description": "Every kind of thing an app registers and runs.",
        "prompts": ["prompts/summary.yaml"],
        "mcpServers": {"version": {"command": sys.executable, "args": ["mcp_server.py"]}},
        "backend": {"entryPoint": "backend/server.py", "type": "python", "port": "auto"},
        "permissions": {
            "backgroundTasks": True,
            "storage": True,
            "proposals": [{"kind_suffix": "draft", "label": "Draft"}],
        },
        **extra,
    }
    if shape == "provider":
        manifest["provider"], manifest["providers"] = providers[0], providers[1:]
    else:
        manifest["providers"] = providers
    (root / "app.json").write_text(json.dumps(manifest, indent=1))
    return root


def _installed_before_the_restart(home: Path, **probe: Any) -> None:
    """The home an installed, enabled probe leaves behind when the gateway stops: its files, its
    install record, and the MCP servers its install wrote into ``mcp.json``."""
    from personalclaw.apps import mcp_bridge
    from personalclaw.apps.manifest import AppManifest

    live = _write_probe(manager.app_dir(APP), "v1", **probe)
    (live / "data").mkdir(exist_ok=True)
    manager._write_installed(
        APP, manager.InstalledApp(name=APP, version="1.0.0", displayName="Boot Probe", enabled=True)
    )
    mcp_bridge.register_app_mcp_servers(AppManifest.from_json_file(live / "app.json"))


# ── the process-wide state the probe reaches ───────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path, monkeypatch) -> Iterator[Path]:
    """An isolated home holding only what a test puts there, app children allowed to start, and
    startup's native-app seeding and package repair out of the way. Teardown takes the probe
    back out of every process-wide registry it reached."""
    import personalclaw.config.loader as loader
    from personalclaw import mcp_client

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.delenv("PERSONALCLAW_SKIP_APP_WORKERS", raising=False)
    monkeypatch.delenv("PERSONALCLAW_SKIP_APP_BACKENDS", raising=False)
    monkeypatch.setattr("personalclaw.apps.app_manager.seed_builtin_apps", lambda: [])
    monkeypatch.setattr("personalclaw.apps.app_manager.repair_app_packages", lambda: [])
    monkeypatch.setattr(mcp_client, "_registry", None)
    registry_module.reset_provider_registry()
    yield tmp_path
    _take_back(tmp_path, APP, TIER_APP, SANDBOXED_APP)


def _take_back(home: Path, *apps: str) -> None:
    from personalclaw.apps import app_manager, app_runtime
    from personalclaw.apps.backend_runtime import get_backend_supervisor
    from personalclaw.apps.worker_runtime import get_worker_supervisor
    from personalclaw.llm.registry import get_default_registry
    from personalclaw.providers.loader import stop_extension_watchdogs

    stop_extension_watchdogs()
    for app in apps:
        app_runtime.unload(app, app_manager._manifest_of(app), forget=True)
        get_backend_supervisor().unhold(app)
        get_worker_supervisor().unhold(app)
    registry_module.reset_provider_registry()
    llm = get_default_registry()
    for table in (llm._factories, llm._capabilities, llm._readiness, llm._catalog_factories):
        table.pop(MODEL_TYPE, None)
    for entry in [e.name for e in llm.list_entries() if e.type == MODEL_TYPE]:
        llm.unregister_entry(entry)
    channel_transports.unregister_transport(APP)
    for name, module in list(sys.modules.items()):
        if str(getattr(module, "__file__", "") or "").startswith(str(home)):
            sys.modules.pop(name, None)


def _running() -> dict[str, Any]:
    """Everything of the probe that is registered or running in this gateway right now."""
    from personalclaw import notification_kinds
    from personalclaw.apps import mcp_bridge, prompt_registry
    from personalclaw.apps.backend_runtime import get_backend_supervisor
    from personalclaw.apps.worker_runtime import get_worker_supervisor
    from personalclaw.llm.registry import get_default_registry
    from personalclaw.tool_providers.registry import get_provider

    primary = registry_module.get_provider_registry().get(APP)
    worker = get_worker_supervisor().get(APP, "worker")
    uses = [prompt_registry.get(u) for u in prompt_registry.use_cases()]
    return {
        "providers": sorted(
            (r.provider_config.type, r.enabled, r.error)
            for r in (primary.chain() if primary else [])
        ),
        "tool": getattr(get_provider(APP), "version", None),
        "model type": MODEL_TYPE in get_default_registry()._factories,
        "channel": channel_transports.get_transport(APP) is not None,
        "proposal kinds": sorted(
            k.kind for k in notification_kinds.all_kinds() if k.source == f"app:{APP}"
        ),
        "prompt use-cases": sorted(u.use_case for u in uses if u is not None and u.app == APP),
        "mcp servers": mcp_bridge.app_mcp_server_keys(APP),
        "backend": get_backend_supervisor().get(APP) is not None,
        "worker": worker is not None and worker.is_alive(),
    }


# ── startup ────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("shape", ["provider", "providers"])
def test_startup_starts_everything_an_enable_starts(home, shape):
    from personalclaw.apps import app_manager
    from personalclaw.providers.loader import load_all_extensions

    _installed_before_the_restart(home, shape=shape)

    load_all_extensions()  # the gateway's startup
    at_startup = _running()
    # Vacuity floor: the snapshot sees a running app, so equality below is not two empty dicts.
    assert at_startup["mcp servers"] == [f"{APP}:version"]

    assert app_manager.disable(APP) and app_manager.enable(APP)
    after_an_enable = _running()
    assert after_an_enable["tool"] == "v1" and after_an_enable["worker"] is True, after_an_enable

    assert at_startup == after_an_enable, "startup started a different app from the one enable does"


def test_startup_keeps_what_the_owner_set_on_an_apps_mcp_server(home):
    """Startup writes an app's MCP servers now (every load does), so it must not undo the owner's
    choices on them: a server switched off on the MCP page stays off across a restart."""
    import json as _json

    _installed_before_the_restart(home)
    mcp = home / "mcp.json"
    data = _json.loads(mcp.read_text())
    data["mcpServers"][f"{APP}:version"].update({"disabled": True, "disabledTools": ["probe"]})
    mcp.write_text(_json.dumps(data))

    from personalclaw.providers.loader import load_all_extensions

    load_all_extensions()

    entry = _json.loads(mcp.read_text())["mcpServers"][f"{APP}:version"]
    assert entry.get("disabled") is True and entry.get("disabledTools") == ["probe"], entry
    assert entry["args"] == ["mcp_server.py"] and entry["cwd"] == str(manager.app_dir(APP))


def test_startup_refuses_an_enabled_app_this_core_cannot_host(home):
    """Enable refuses an app whose ``minPersonalClawVersion`` this core does not meet. A core
    downgraded under an app that is already enabled reached boot, which imported its code and
    registered its tool anyway; only its backend was skipped, and the watchdog started that 30
    seconds later."""
    from personalclaw.apps.backend_runtime import _check_and_revive, get_backend_supervisor
    from personalclaw.apps.worker_runtime import get_worker_supervisor
    from personalclaw.providers.loader import load_all_extensions

    _installed_before_the_restart(home, minPersonalClawVersion="99.0.0")

    load_all_extensions()
    _check_and_revive()  # one pass of each watchdog, as the gateway runs every 30 s
    get_worker_supervisor().sweep()

    running = _running()
    assert running["tool"] is None, "the code of an app this core cannot host ran"
    assert not running["model type"] and not running["channel"]
    assert running["providers"] and all(not on for _t, on, _e in running["providers"])
    assert all("99.0.0" in why for _t, _on, why in running["providers"]), running["providers"]
    assert get_backend_supervisor().get(APP) is None, "its backend was started"
    assert running["worker"] is False, "its worker was started"


def test_startup_registers_every_apps_code_before_any_app_starts_a_process(home):
    """Order across apps is the guarantee startup's own path had: every provider registered
    before any backend launched. A backend that launches through another app's sandbox tier
    (``backend.sandbox``) refuses to start while that tier is unregistered, and ``aaa-sandboxed``
    sorts before ``zzz-tier``."""
    from personalclaw.apps.backend_runtime import get_backend_supervisor
    from personalclaw.providers.loader import load_all_extensions

    tier = manager.app_dir(TIER_APP)
    tier.mkdir(parents=True)
    (tier / "provider.py").write_text(textwrap.dedent("""
            from personalclaw.sdk.sandbox import SandboxHandle, SandboxProvider


            class _Passthrough(SandboxHandle):
                def __init__(self, argv):
                    self._argv = list(argv)

                @property
                def argv(self):
                    return list(self._argv)

                async def exec(self, **kwargs):
                    raise NotImplementedError

                def cleanup(self):
                    return None


            class Tier(SandboxProvider):
                name = "zzz-tier"
                display_name = "ZZZ tier"

                def available(self):
                    return True

                def wrap(self, spec, argv):
                    return _Passthrough(argv)


            def create_provider(config=None):
                return Tier()
            """))
    (tier / "app.json").write_text(
        json.dumps(
            {
                "name": TIER_APP,
                "version": "1.0.0",
                "displayName": "ZZZ tier",
                "description": "A sandbox tier another app's backend launches through.",
                "provider": {"type": "sandbox", "implementation": "provider:create_provider"},
            }
        )
    )
    sandboxed = manager.app_dir(SANDBOXED_APP)
    (sandboxed / "backend").mkdir(parents=True)
    (sandboxed / "backend" / "server.py").write_text(_BACKEND_PY)
    (sandboxed / "app.json").write_text(
        json.dumps(
            {
                "name": SANDBOXED_APP,
                "version": "1.0.0",
                "displayName": "Sandboxed",
                "description": "A backend that launches through the ZZZ tier.",
                "backend": {
                    "entryPoint": "backend/server.py",
                    "type": "python",
                    "port": "auto",
                    "sandbox": TIER_APP,
                },
            }
        )
    )
    for app in (TIER_APP, SANDBOXED_APP):
        manager._write_installed(
            app, manager.InstalledApp(name=app, version="1.0.0", displayName=app, enabled=True)
        )

    load_all_extensions()

    assert (
        get_backend_supervisor().get(SANDBOXED_APP) is not None
    ), "a backend launched before the sandbox tier it runs in was registered"


@pytest.mark.asyncio
async def test_a_stop_before_the_gateway_is_up_leaves_no_app_process_running(home, monkeypatch):
    """The gateway's own stop (``GatewayOrchestrator._shutdown``) stops every app process, but its
    signal handlers are installed late in start-up, after the update check. Measured on a dev
    gateway: a Ctrl-C during that check cancelled start-up and ended the process without it, and
    the probe's backend and worker kept running, re-parented to init. Startup starts the worker
    now too, where the first watchdog sweep used to, so a stop in that window left both behind."""
    import asyncio

    from personalclaw import gateway
    from personalclaw.apps.backend_runtime import get_backend_supervisor
    from personalclaw.apps.worker_runtime import get_worker_supervisor
    from personalclaw.config.loader import AppConfig
    from personalclaw.providers.loader import load_all_extensions

    _installed_before_the_restart(home)
    at_the_update_check = asyncio.Event()

    async def start_up(self: Any) -> None:
        load_all_extensions()  # the dashboard's start runs it, before the update check
        at_the_update_check.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(gateway.GatewayOrchestrator, "run", start_up)
    cfg = AppConfig()
    monkeypatch.setattr(cfg, "load_credentials", lambda: {})
    starting = asyncio.create_task(gateway.run_gateway(cfg, no_dashboard=True, no_crons=True))
    await asyncio.wait_for(at_the_update_check.wait(), timeout=30)
    backend = get_backend_supervisor().get(APP)
    worker = get_worker_supervisor().get(APP, "worker")
    assert backend is not None and worker is not None and worker.is_alive(), _running()
    processes = [backend.proc, worker.proc]
    assert all(p is not None and p.poll() is None for p in processes)

    starting.cancel()  # what asyncio.run does to the start-up it is running, on a Ctrl-C
    with pytest.raises(asyncio.CancelledError):
        await starting

    still_running = [p.args for p in processes if p is not None and p.poll() is None]
    assert still_running == [], f"an app process outlived the stop: {still_running}"


# ── the Settings → Providers switch ────────────────────────────────────────────────────────────


@asynccontextmanager
async def _providers_page() -> AsyncIterator[TestClient]:
    """What Settings → Providers sends: its list, and — for a provider's switch — the app's own
    enable and disable (the web client's ``setActivation``)."""
    from personalclaw.dashboard.handlers.apps import register_app_routes
    from personalclaw.providers import routes as provider_routes

    app = web.Application()
    register_app_routes(app)
    provider_routes.register_routes(app)
    async with TestClient(TestServer(app)) as client:
        yield client


def _install(home: Path, **probe: Any) -> None:
    from personalclaw.apps import app_manager

    result = app_manager.install(_write_probe(home / "src" / APP, "v1", **probe), confirm=True)
    assert result.ok, result.error


@pytest.mark.asyncio
async def test_switching_on_a_provider_that_failed_to_start_runs_the_fixed_files(home):
    """A provider whose start failed shows its switch off while its app is on, so switching it on
    enables an app that is already enabled. That loaded the app a second time on top of the first:
    the module already loaded was handed back, so the fix to its file on disk never ran, and the
    switch sprang back with the old failure. It now starts the app again from its files."""
    from personalclaw.providers.settings import ProviderSettings

    _install(home)
    ProviderSettings.update(APP, {"broken": True})
    registry_module.get_provider_registry().rebuild(APP)
    tool = registry_module.get_provider_registry().get(APP)
    assert tool is not None and not tool.enabled and "told to fail" in tool.error
    (manager.app_dir(APP) / "provider.py").write_text(
        (manager.app_dir(APP) / "provider.py")
        .read_text()
        .replace('raise RuntimeError("the probe was told to fail")', "pass")
    )

    async with _providers_page() as page:
        resp = await page.post(f"/api/apps/{APP}/enable")  # what its switch sends
        assert resp.status == 200, await resp.text()
        answer = await resp.json()
        cards = (await (await page.get("/api/providers")).json())["providers"]
    assert answer["providerErrors"] == [], "the provider was retried on the code that failed"
    card = next(c for c in cards if c["name"] == APP and c["provider"]["type"] == "tool")
    assert card["enabled"] is True and card["error"] == "", card
    assert _running()["tool"] == "v1"


# ── the rails: neither entry point grows a path of its own again ───────────────────────────────


def _calls(source: str) -> set[str]:
    import ast

    return {
        (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", ""))
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call)
    }


def test_startup_has_no_loader_of_its_own():
    """Asserted by AST over the whole startup module, so a mention in a docstring is not a call:
    nothing there registers or enables a provider, seeds a prompt or a skill, writes an MCP server
    or starts an app's process itself. It walks the installed apps into ``app_runtime``, which
    loads each one through ``load`` — the function install, enable and update end with."""
    import inspect

    from personalclaw.apps import app_runtime
    from personalclaw.providers import loader

    called = _calls(inspect.getsource(loader))
    by_hand = {
        "register",
        "enable",
        "seed_app_prompts",
        "seed_app_skills",
        "register_app_mcp_servers",
        "register_app_proposal_kinds",
        "start_app_backend",
        "start_app_workers",
    } & called
    assert by_hand == set(), f"startup does part of an app's load by itself again: {by_hand}"
    assert "start_installed" in called, called
    assert "load" in _calls(inspect.getsource(app_runtime.start_installed))


def test_a_provider_has_no_switch_of_its_own():
    """A provider is switched by switching its app. A providers route that toggled the instances
    left the app's code loaded, and it sat outside the owner-only app routes: an app that declared
    ``/api/providers`` could switch another app's code on through it."""
    import ast
    import inspect

    from personalclaw.providers import routes

    registered = {
        node.args[0].value
        for node in ast.walk(ast.parse(inspect.getsource(routes.register_routes)))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr.startswith("add_")
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert "/api/providers/{name}/availability" in registered, registered  # it reads the routes
    switches = sorted(r for r in registered if r.rsplit("/", 1)[-1] in {"enable", "disable"})
    assert switches == [], f"a provider has a switch of its own again: {switches}"
