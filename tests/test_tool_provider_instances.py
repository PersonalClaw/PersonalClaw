"""Multi-instance TOOL providers must surface one live provider per enabled
instance (regression for the openai-tools "instances never surface tools" gap).

ToolTypeHandler.create() must mirror ModelTypeHandler: for a multiInstance tool
app it iterates the enabled instances (list_instances) and returns a LIST of
providers, and register()/deregister() must normalize that list. Previously it
loaded only the singleton app config → one hollow provider → the instances a
user added via "Add instance" never became live tool providers.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.providers import instances as inst_mod
from personalclaw.providers.registry import ToolTypeHandler


class _FakeToolProvider:
    """A minimal tool provider built from an instance config."""

    def __init__(self, config: dict):
        self.endpoint = config.get("endpoint", "")
        # unique name per endpoint (mirrors OpenAIToolProvider's slug name)
        self.name = f"openai-{self.endpoint.rsplit('/', 1)[-1] or 'x'}"


class _Cfg:
    def __init__(self, multi: bool):
        self.type = "tool"
        self.multiInstance = multi
        self.implementation = "provider:create_openai_tool_provider"
        self.capabilities = ["tool_execution"]


class _Ext:
    def __init__(self, name: str, multi: bool = True):
        self.name = name
        self.provider_config = _Cfg(multi)


@pytest.fixture
def _cfg_home(tmp_path, monkeypatch):
    # Redirect the instance store under tmp_path.
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def _stub_factory(monkeypatch):
    monkeypatch.setattr(
        "personalclaw.providers.loader.load_factory",
        lambda ext: (lambda config=None: _FakeToolProvider(config or {})),
    )


def test_create_iterates_enabled_instances(_cfg_home, monkeypatch):
    _stub_factory(monkeypatch)
    inst_mod.create_instance(
        "openai-tools", display_name="a", config={"endpoint": "https://a.example/1"}
    )
    inst_mod.create_instance(
        "openai-tools", display_name="b", config={"endpoint": "https://b.example/2"}
    )
    # disable the "b" instance (match by endpoint, not creation order).
    b = next(
        i
        for i in inst_mod.list_instances("openai-tools")
        if i.config.get("endpoint", "").startswith("https://b")
    )
    inst_mod.update_instance("openai-tools", b.id, enabled=False)

    handler = ToolTypeHandler()
    result = handler.create(_Ext("openai-tools", multi=True))
    assert isinstance(result, list)
    # Only the ENABLED instance yields a provider.
    assert len(result) == 1
    assert result[0].endpoint == "https://a.example/1"
    assert getattr(result[0], "instance_id", None)  # tagged with its instance id


def test_create_returns_none_when_no_enabled_instances(_cfg_home, monkeypatch):
    _stub_factory(monkeypatch)
    # no instances at all
    handler = ToolTypeHandler()
    assert handler.create(_Ext("openai-tools", multi=True)) is None


def test_register_and_deregister_normalize_a_list(_cfg_home, monkeypatch):
    _stub_factory(monkeypatch)
    registered: list[str] = []
    monkeypatch.setattr(
        "personalclaw.tool_providers.registry.register_provider",
        lambda p: registered.append(p.name),
    )
    unregistered: list[str] = []
    monkeypatch.setattr(
        "personalclaw.tool_providers.registry.unregister_provider",
        lambda n: unregistered.append(n),
    )
    handler = ToolTypeHandler()
    p1, p2 = _FakeToolProvider({"endpoint": "https://a/1"}), _FakeToolProvider(
        {"endpoint": "https://b/2"}
    )
    handler.register(_Ext("openai-tools"), [p1, p2])
    assert registered == [p1.name, p2.name]
    handler.deregister(_Ext("openai-tools"), [p1, p2])
    assert unregistered == [p1.name, p2.name]


def test_single_instance_tool_path_unchanged(_cfg_home, monkeypatch):
    # A NON-multiInstance tool app still uses the singleton-config path (one provider).
    _stub_factory(monkeypatch)
    monkeypatch.setattr(
        "personalclaw.providers.settings.ProviderSettings.load",
        staticmethod(lambda name: {"endpoint": "https://single/x"}),
    )
    handler = ToolTypeHandler()
    result = handler.create(_Ext("some-tool", multi=False))
    assert not isinstance(result, list)
    assert result.endpoint == "https://single/x"


# ── multiInstance MODEL providers must re-register too (#3372) ────────────────
#
# instance_routes._refresh_tool_provider_safe used to guard on
# provider_config.type == "tool", so a multiInstance MODEL provider (e.g.
# ollama-models) never got the disable→enable re-registration cycle after
# create/update/delete: its local_models.registry entry — which is what
# _known_provider_names() reads to accept a "provider:model" binding — stayed
# stale until the next gateway restart, even though the instance was created
# and tested successfully. Fixed by widening the guard (renamed
# _refresh_multi_instance_provider_safe) to also cover type == "model". These
# tests drive the real HTTP routes end to end, mirroring the filed repro:
# create an instance → bind it with NO restart → delete removes it again (the
# reverse hole the issue also names — the old provider must not keep serving).


class _FakeLocalModelProvider:
    """Minimal LocalModelProvider duck-type — enough for is_local_model_provider
    (name/display_name/list_models/download_model/delete_model)."""

    def __init__(self, config: dict):
        self.endpoint = config.get("endpoint", "")
        self.name = "fake-multi-model"
        self.display_name = "Fake Multi Model"

    async def list_models(self):
        return []

    async def download_model(self, name: str):
        raise NotImplementedError

    async def delete_model(self, name: str):
        raise NotImplementedError


class _ModelCfg:
    def __init__(self):
        self.type = "model"
        self.multiInstance = True
        self.implementation = "provider:create_fake_multi_model_provider"
        self.capabilities = ["chat"]
        self.settingsSchema = {
            "type": "object",
            "properties": {"endpoint": {"type": "string"}},
        }


@pytest.fixture
def _model_provider_registry(monkeypatch):
    """A REAL ProviderRegistry (not a stub) with one multiInstance MODEL
    extension registered but not yet enabled, so create/update/delete drive the
    actual disable→enable cycle through ModelTypeHandler — exactly like the live
    gateway, not a mocked shortcut."""
    from personalclaw.providers.registry import (
        ModelTypeHandler,
        ProviderRegistry,
        RegisteredProvider,
    )

    registry = ProviderRegistry()
    registry.register_type_handler("model", ModelTypeHandler())
    record = RegisteredProvider(
        name="fake-multi-model", manifest=None, provider_config=_ModelCfg(), enabled=False
    )
    registry._extensions["fake-multi-model"] = record
    monkeypatch.setattr("personalclaw.providers.registry.get_provider_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.providers.loader.load_factory",
        lambda ext: (lambda config=None: _FakeLocalModelProvider(config or {})),
    )
    yield registry
    # The local-model registry is a module-level global: unregister even if an
    # assertion above failed, so this fixture never leaks into another test.
    from personalclaw.local_models.registry import unregister_provider

    unregister_provider("fake-multi-model")


@asynccontextmanager
async def _model_provider_client(tmp_path):
    from personalclaw.dashboard.handlers import model_registry
    from personalclaw.providers import instance_routes

    # 🪤 `config_dir` is NOT the only home seam these routes reach. A successful
    # instance mutation now also runs `_rebuild_agent_config_safe()`, and
    # `rebuild_agent_config()` writes through `agent.AGENTS_DIR`, a MODULE-LEVEL
    # constant frozen at import (`agent.py:93`) — patching `config_dir` alone
    # leaves it pointing at the REAL home, so the write escapes tmp_path and the
    # conftest real-home rail fails the whole session (`agents/personalclaw.json`
    # modified). Redirect the frozen constants too; `_USER_DIR` keeps the merge
    # from reading the real user's `mcp.json` into the rebuilt config.
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch("personalclaw.agent.AGENTS_DIR", tmp_path / "agents"),
        patch("personalclaw.agent._USER_DIR", tmp_path),
    ):
        app = web.Application()
        instance_routes.register_instance_routes(app)
        model_registry.register_model_registry_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client


@pytest.mark.asyncio
async def test_creating_a_multi_instance_model_provider_is_bindable_without_restart(
    tmp_path, _model_provider_registry
):
    async with _model_provider_client(tmp_path) as client:
        from personalclaw.providers.use_cases import _known_provider_names

        assert "fake-multi-model" not in (_known_provider_names() or set())

        r = await client.post(
            "/api/providers/fake-multi-model/instances",
            json={"display_name": "local", "config": {"endpoint": "http://127.0.0.1:11434"}},
        )
        assert r.status == 201, await r.text()

        # In-process, no restart: the prefix is now known...
        assert "fake-multi-model" in (_known_provider_names() or set())
        # ...and the binding PUT the filed issue's step 3 got a 400 on now succeeds.
        r = await client.put(
            "/api/models/active/chat", json={"models": ["fake-multi-model:some-model"]}
        )
        assert r.status == 200, await r.text()


@pytest.mark.asyncio
async def test_deleting_a_multi_instance_model_provider_makes_it_unbindable_again(
    tmp_path, _model_provider_registry
):
    async with _model_provider_client(tmp_path) as client:
        from personalclaw.providers.use_cases import _known_provider_names

        r = await client.post(
            "/api/providers/fake-multi-model/instances",
            json={"display_name": "local", "config": {"endpoint": "http://127.0.0.1:11434"}},
        )
        assert r.status == 201, await r.text()
        instance_id = json.loads(await r.text())["instance"]["id"]

        # Confirm it actually became bindable first, so the assertions below pin
        # the DELETE path specifically rather than degenerating to "never worked".
        r = await client.put(
            "/api/models/active/chat", json={"models": ["fake-multi-model:some-model"]}
        )
        assert r.status == 200, await r.text()

        r = await client.delete(f"/api/providers/fake-multi-model/instances/{instance_id}")
        assert r.status == 200, await r.text()

        # The reverse hole: without a re-registration on delete, the old provider
        # (and its binding eligibility) would keep serving until a restart.
        assert "fake-multi-model" not in (_known_provider_names() or set())
        r = await client.put(
            "/api/models/active/chat", json={"models": ["fake-multi-model:some-model"]}
        )
        assert r.status == 400, await r.text()
