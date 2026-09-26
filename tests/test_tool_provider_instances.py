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
    apps: list[str] = []
    monkeypatch.setattr(
        "personalclaw.tool_providers.registry.register_provider",
        lambda p, app="", **_standing: (registered.append(p.name), apps.append(app)),
    )
    unregistered: list[object] = []
    monkeypatch.setattr(
        "personalclaw.tool_providers.registry.unregister_provider",
        lambda p: unregistered.append(p),
    )
    handler = ToolTypeHandler()
    p1, p2 = _FakeToolProvider({"endpoint": "https://a/1"}), _FakeToolProvider(
        {"endpoint": "https://b/2"}
    )
    handler.register(_Ext("openai-tools"), [p1, p2])
    assert registered == [p1.name, p2.name]
    # Every instance is registered under the app that owns it — the tool seam names that app
    # when one of its tools has a schema no model request can carry.
    assert apps == ["openai-tools", "openai-tools"]
    handler.deregister(_Ext("openai-tools"), [p1, p2])
    # By object: a provider refused at registration never held its name, so removing "whatever is
    # registered under it" would take the provider that does.
    assert unregistered == [p1, p2]


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


# ── a MODEL app's instances are not in this store (#3372, superseded) ────────
#
# #3372 widened `_refresh_multi_instance_provider_safe` to model apps so an instance
# created through THESE routes became bindable without a restart. But nothing chat
# resolves reads this store: chat, discovery and Settings → Providers read config.json
# `providers[]` (/api/model-providers). An Ollama endpoint saved here was a second copy
# nobody used — listed on the Providers page with no Test, Edit or Remove, while chat kept
# talking to whatever `providers[]` said. So the generic routes now REFUSE a model app, and
# #3372's guarantee — bindable with no restart, unbindable once removed — is pinned on the
# one store that chat reads.


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
    """A REAL ProviderRegistry (not a stub) with one multiInstance MODEL extension."""
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
    yield registry


@asynccontextmanager
async def _model_provider_client(tmp_path):
    from personalclaw.dashboard.handlers import model_registry
    from personalclaw.dashboard.handlers import providers as model_providers
    from personalclaw.providers import instance_routes

    # 🪤 `config_dir` is NOT the only home seam these routes reach: a tool-instance
    # mutation runs `_rebuild_agent_config_safe()`, which writes through `agent.agents_dir()`
    # and reads `agent._USER_DIR`. Redirect them too, so nothing here can reach the real home.
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch("personalclaw.agent.agents_dir", lambda: tmp_path / "agents"),
        patch("personalclaw.agent._USER_DIR", tmp_path),
        # The typed media registries are process-wide; a config write re-reads them.
        patch.object(model_providers, "_refresh_media_registries", lambda: None),
    ):
        app = web.Application()
        instance_routes.register_instance_routes(app)
        model_registry.register_model_registry_routes(app)
        app.router.add_post("/api/model-providers", model_providers.api_provider_create)
        app.router.add_delete("/api/model-providers/{name}", model_providers.api_provider_delete)
        async with TestClient(TestServer(app)) as client:
            yield client


@pytest.mark.asyncio
async def test_the_generic_instance_routes_refuse_a_model_app(tmp_path, _model_provider_registry):
    """Every route, not just create — a list or a test answered from this store would
    still show (or probe) the copy chat never reads."""
    base = "/api/providers/fake-multi-model/instances"
    body = {"display_name": "local", "config": {"endpoint": "http://127.0.0.1:11434"}}
    async with _model_provider_client(tmp_path) as client:
        calls = [
            client.get(base),
            client.post(base, json=body),
            client.get(f"{base}/abc123"),
            client.put(f"{base}/abc123", json={"config": {"endpoint": "http://x:1"}}),
            client.delete(f"{base}/abc123"),
            client.post(f"{base}/abc123/test"),
        ]
        for call in calls:
            r = await call
            payload = json.loads(await r.text())
            assert r.status == 400, (r.method, r.url, payload)
            assert payload["error"]["code"] == "model_instances_elsewhere", payload
            assert "Settings → Providers" in payload["error"]["message"]

    assert not (
        tmp_path / "extensions" / "fake-multi-model"
    ).exists(), "a model app's instance was written to the generic store anyway"


def _register_fake_model_type() -> None:
    """Simulate an installed model app having registered its provider type."""
    from personalclaw.llm.capabilities import Capability, ProviderCapability
    from personalclaw.llm.registry import get_default_registry

    reg = get_default_registry()
    if "fake-multi-model" not in reg._capabilities:  # noqa: SLF001
        reg.register_type(
            ProviderCapability(
                type="fake-multi-model",
                capabilities=frozenset({Capability.CHAT}),
                supports_streaming=True,
                supports_tools=False,
                supports_embeddings=False,
                supports_vision=False,
                max_context_tokens=0,
            ),
            lambda **kw: None,
        )


@pytest.mark.asyncio
async def test_a_model_instance_is_bindable_at_once_and_unbindable_once_removed(tmp_path):
    """#3372's guarantee, on the store chat reads: no restart between add and bind, and a
    removed instance stops being bindable (the old provider must not keep serving)."""
    _register_fake_model_type()
    async with _model_provider_client(tmp_path) as client:
        from personalclaw.providers.use_cases import _known_provider_names

        assert "local-models" not in (_known_provider_names() or set())
        r = await client.post(
            "/api/model-providers",
            json={
                "name": "local-models",
                "type": "fake-multi-model",
                "options": {"endpoint": "http://127.0.0.1:11434"},
            },
        )
        assert r.status == 200, await r.text()

        r = await client.put("/api/models/active/chat", json={"models": ["local-models:m1"]})
        assert r.status == 200, await r.text()

        r = await client.delete("/api/model-providers/local-models")
        assert r.status == 200, await r.text()
        assert "local-models" not in (_known_provider_names() or set())
        r = await client.put("/api/models/active/chat", json={"models": ["local-models:m1"]})
        assert r.status == 400, await r.text()
