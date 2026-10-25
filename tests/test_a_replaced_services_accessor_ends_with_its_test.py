"""A test that replaces the action services' accessor leaves every module the real one.

🔴 The defect. Many tests replace the accessor by its path,
``monkeypatch.setattr("personalclaw.action_providers.services.get_action_services", lambda: ...)``.
Undoing that puts the services module's name back, but a module IMPORTED while it was replaced
bound the replacement by name (``from personalclaw.action_providers.services import
get_action_services``) and kept it. ``test_workflows_versions.py``'s trigger-fired run was the
first on its worker to import ``notify_provider`` that way, so every later notify on that worker
reached that test's namespace: ``test_action_services_protocol.py``'s notify test failed with
"'types.SimpleNamespace' object has no attribute 'state'" (breadth random-order/3, 2026-10-23),
and with another test's state in place of its own (random-order/4, seed 82, run 40199713134).

``tests/conftest.py``'s ``_reset_action_services`` now starts every test by putting the real
accessor back in every module of ours that binds it, as ``_restore_provider_registry`` does for
the provider registry's. A module that binds it is made here the way a provider is: imported
while the accessor is replaced.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import conftest
import pytest

from personalclaw.action_providers import services

RESET = conftest._reset_action_services._get_wrapped_function()

#: What a module that binds the accessor says it reads.
_BINDS = """
from personalclaw.action_providers.services import get_action_services


def services():
    return get_action_services()
"""

_LEFT = SimpleNamespace(workflows="the earlier test's")


def _bound_while_replaced(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str):
    """A module named *name* that binds the accessor, imported while a test had replaced it."""
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(_BINDS))
    monkeypatch.syspath_prepend(str(tmp_path))
    request.addfinalizer(lambda: sys.modules.pop(name, None))
    with pytest.MonkeyPatch.context() as replaced:
        replaced.setattr(
            "personalclaw.action_providers.services.get_action_services", lambda: _LEFT
        )
        module = importlib.import_module(name)
    # The record of loaded modules is taken once per setup, before this module existed.
    monkeypatch.setattr(conftest, "_LOADED_MODULES", conftest._LoadedModules())
    assert services.get_action_services.__module__ == services.__name__, "the undo ran"
    assert module.services() is _LEFT, "the defect's shape: it kept the replacement"
    return module


def test_a_module_that_bound_a_replaced_accessor_gets_the_real_one_back(
    request, tmp_path, monkeypatch
) -> None:
    probe = _bound_while_replaced(request, tmp_path, monkeypatch, "test_binds_the_accessor_probe")

    reset = RESET()
    next(reset)
    try:
        assert probe.get_action_services is services.get_action_services
        wired = services.ActionServices(state=SimpleNamespace(notify=lambda *_: None))
        services.set_action_services(wired)
        assert probe.services() is wired, "it reads what is wired now"
    finally:
        with pytest.raises(StopIteration):
            next(reset)
    assert services.get_action_services() is None, "and the fixture still clears what was wired"


def test_a_module_that_is_not_ours_is_left_as_it_is(request, tmp_path, monkeypatch) -> None:
    """Only our modules (``conftest._OUR_MODULES``) are re-pointed: another's name is its own."""
    other = _bound_while_replaced(
        request, tmp_path, monkeypatch, "elsewhere_binds_the_accessor_probe"
    )

    conftest._put_back_the_action_services_accessor()

    assert other.services() is _LEFT
