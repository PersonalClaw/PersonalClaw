"""A capability switched off takes back what its code registered, and leaves the app's (#3843).

A capability's factory may register into core's process-wide registries as it is built: an agent
CLI app's factory publishes the ``acp:<cli>`` runtime every chat on that agent resolves through.
Off means deregistered, so switching that capability off withdraws its runtime while the app's
other capabilities keep theirs, and switching it on again brings it back. A factory that registers
and then fails leaves nothing of its own behind: a failed capability does not half run.

What the app's modules register as they load is the app's, whichever capability's build first
imported them, so it stays while the app is on and leaves with the app, as before.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_each_capability_has_its_own_switch import _gateway

from personalclaw import app_code
from personalclaw.apps import app_manager, manager
from personalclaw.llm.registry import get_default_registry
from personalclaw.providers import registry as registry_module

pytestmark = pytest.mark.security

APP = "agent-kit"
CLIS = ("kit-a", "kit-b", "kit-c", "kit-body")

#: Each capability's factory registers its own runtime; ``create_c`` raises after registering.
#: The module body registers one more as it is imported, which is the app's.
_PROVIDER_PY = """
from personalclaw.sdk.acp import register_acp_cli_entry


def _runtime(cli):
    register_acp_cli_entry(cli=cli, dialect="generic", command=["kit-cli"], model="")


_runtime("kit-body")


def create_a(config):
    _runtime("kit-a")


def create_b(config):
    _runtime("kit-b")


def create_c(config):
    _runtime("kit-c")
    raise RuntimeError("the kit CLI refused its sign-in")
"""


def _agent(id_: str, **extra: Any) -> dict[str, Any]:
    return {
        "type": "agent",
        "implementation": f"provider:create_{id_}",
        "id": id_,
        "label": f"Kit {id_.upper()}",
    } | extra


@pytest.fixture
def kit(tmp_path, monkeypatch) -> Path:
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    reg = registry_module.ProviderRegistry()
    reg.register_type_handler("agent", registry_module.EntitySeamHandler(source_of_truth="test"))
    monkeypatch.setattr(registry_module, "_registry", reg)
    yield tmp_path
    for cli in CLIS:
        get_default_registry().unregister_entry(f"acp:{cli}")
    app_code.release(APP)


def _install(root: Path, providers: list[dict[str, Any]]) -> None:
    d = root / "bundle" / APP
    d.mkdir(parents=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Agent Kit",
        "description": "A demo app with two agent runtimes.",
        "launches": [{"program": "kit-cli", "why": "The kit CLI does each chat's work."}],
        "providers": providers,
    }
    (d / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "provider.py").write_text(_PROVIDER_PY, encoding="utf-8")
    res = app_manager.install(d, confirm=True)
    assert res.ok, res.error


def _runtimes() -> set[str]:
    names = {e.name for e in get_default_registry().list_entries()}
    return {cli for cli in CLIS if f"acp:{cli}" in names}


@pytest.mark.asyncio
async def test_switching_an_agent_capability_off_withdraws_its_runtime(kit) -> None:
    _install(kit, [_agent("a"), _agent("b")])
    assert _runtimes() == {"kit-a", "kit-b", "kit-body"}
    async with TestClient(TestServer(_gateway())) as client:
        off = await client.post(f"/api/apps/{APP}/capabilities/a/off")
        assert off.status == 200, await off.text()
        assert _runtimes() == {"kit-b", "kit-body"}, "only that capability's runtime leaves"
        on = await client.post(f"/api/apps/{APP}/capabilities/a/on")
        assert on.status == 200, await on.text()
        assert (await on.json())["status"] == "running"
        assert _runtimes() == {"kit-a", "kit-b", "kit-body"}, "and comes back with it"


def test_a_capability_that_fails_after_registering_leaves_nothing_of_its_own(kit) -> None:
    _install(kit, [_agent("a"), _agent("c")])
    rec = registry_module.get_provider_registry().get(APP, "c")
    assert rec is not None and not rec.enabled and "refused its sign-in" in rec.error
    assert _runtimes() == {"kit-a", "kit-body"}, "a failed capability's runtime never resolves"


@pytest.mark.asyncio
async def test_what_the_apps_module_registers_as_it_loads_stays_with_the_app(kit) -> None:
    """``a`` is built first, so its build imports the module; the module's registration is still
    the app's, not ``a``'s."""
    _install(kit, [_agent("a"), _agent("b")])
    async with TestClient(TestServer(_gateway())) as client:
        assert (await client.post(f"/api/apps/{APP}/capabilities/a/off")).status == 200
        assert (await client.post(f"/api/apps/{APP}/capabilities/b/off")).status == 200
    assert _runtimes() == {"kit-body"}, "every capability off: the app's own registration stays"
    app_manager.disable(APP)
    assert _runtimes() == set(), "the app off: everything its code registered leaves"


def test_the_app_switched_off_takes_back_every_capabilitys_runtime(kit) -> None:
    _install(kit, [_agent("a"), _agent("b")])
    app_manager.disable(APP)
    assert _runtimes() == set(), "a capability still on leaves with its app"


def test_releasing_the_apps_code_takes_back_its_capabilities_registrations(kit) -> None:
    """An unload releases the app's code whatever its records say: a capability's registrations
    are the app's code's too, so none outlives it."""
    _install(kit, [_agent("a"), _agent("b")])
    app_code.release(APP)
    assert _runtimes() == set()


def test_a_settings_rebuild_keeps_one_runtime_per_capability(kit) -> None:
    _install(kit, [_agent("a"), _agent("b")])
    first = get_default_registry().get_entry("acp:kit-a")
    registry_module.get_provider_registry().rebuild(APP, "a")
    again = get_default_registry().get_entry("acp:kit-a")
    assert again is not first, "rebuilt from the current settings"
    assert _runtimes() == {"kit-a", "kit-b", "kit-body"}
    registry_module.get_provider_registry().disable(APP, "a")
    assert _runtimes() == {"kit-b", "kit-body"}, "the rebuilt one is the one taken back"
