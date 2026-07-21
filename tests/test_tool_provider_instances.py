"""Multi-instance TOOL providers must surface one live provider per enabled
instance (regression for the openai-tools "instances never surface tools" gap).

ToolTypeHandler.create() must mirror ModelTypeHandler: for a multiInstance tool
app it iterates the enabled instances (list_instances) and returns a LIST of
providers, and register()/deregister() must normalize that list. Previously it
loaded only the singleton app config → one hollow provider → the instances a
user added via "Add instance" never became live tool providers.
"""

from __future__ import annotations

import pytest

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
