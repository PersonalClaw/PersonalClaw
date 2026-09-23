"""Stub-level contract tests for the app-template tool provider.

Contract: personalclaw.sdk.tool:ToolProvider

These run with no network, no credentials and no gateway: they assert the provider
SHAPE core depends on, so a change that breaks registration fails here first. Add your
behaviour tests beside them as you fill the stub in.
"""

from __future__ import annotations

from provider import AppTemplateProvider, create_provider

CONTRACT_METHODS = ("display_name", "invoke", "list_tools", "name")


def test_factory_returns_the_provider() -> None:
    assert isinstance(create_provider({}), AppTemplateProvider)


def test_factory_accepts_no_config() -> None:
    assert isinstance(create_provider(None), AppTemplateProvider)


def test_nothing_abstract_is_left() -> None:
    """An unimplemented abstract method makes the provider uninstantiable."""
    assert not getattr(AppTemplateProvider, "__abstractmethods__", frozenset())


def test_registers_under_the_app_name() -> None:
    """Every per-type registry keys a provider by `.name`."""
    assert create_provider({}).name == "app-template"


def test_declares_its_display_name() -> None:
    assert create_provider({}).display_name == "App Template"


def test_every_contract_method_is_declared_on_the_stub() -> None:
    """Inherited-but-unimplemented is the drift this catches."""
    for name in CONTRACT_METHODS:
        assert name in vars(AppTemplateProvider), f"{name} is not implemented on the stub"


def test_settings_reach_the_provider() -> None:
    assert create_provider({"timeout_secs": 5})._timeout == 5
