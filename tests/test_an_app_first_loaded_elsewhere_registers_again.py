"""An app's provider type reaches the registry every test resolves through, whichever registry was
in place when some earlier test on the worker first imported the app.

An app registers its type once, as its module first runs, and the module is then cached for the
worker's life. A test that ran it on a registry of its own took the type away when its registry
went, and every later test on the worker got the cached module and no type: an Ollama entry at
localhost read as unpriced and its prompt was scanned as one leaving the machine, depending only on
which tests had run before. The suite's registry guard forgets such an app when the test ends, so
its next import registers the type where the next test looks.
"""

from __future__ import annotations

import sys

import conftest
import pytest

from personalclaw.apps.native_contract import load_bundle_module, namespaced_module_name
from personalclaw.llm import registry as registry_mod
from personalclaw.llm.registry import ProviderResolutionError

APP = "registers-once"
TYPE = "registers-once-type"

_PROVIDER = f"""
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import get_default_registry

CAPABILITY = ProviderCapability(
    type="{TYPE}",
    capabilities=frozenset({{Capability.CHAT}}),
    supports_streaming=True,
    supports_tools=False,
    supports_embeddings=False,
    supports_vision=False,
    max_context_tokens=0,
)


def _factory(**_kwargs):
    raise NotImplementedError


get_default_registry().register_type(CAPABILITY, _factory)
"""


@pytest.fixture
def bundle(tmp_path):
    folder = tmp_path / APP
    folder.mkdir()
    (folder / "provider.py").write_text(_PROVIDER, encoding="utf-8")
    name = namespaced_module_name(APP, "provider")
    yield folder
    module = sys.modules.pop(name, None)
    if module is not None:
        registry_mod.get_default_registry()._forget_type(TYPE, module._factory)


def _has_type(registry) -> bool:
    try:
        registry.capability_of(TYPE)
    except ProviderResolutionError:
        return False
    return True


def test_an_app_first_imported_on_a_registry_of_its_own_registers_again_where_it_belongs(bundle):
    registry = registry_mod.get_default_registry()
    before = conftest._app_modules()

    # An earlier test, on a registry of its own, is the first to import the app.
    registry_mod.reset_default_registry()
    try:
        first = load_bundle_module(bundle, APP, "provider")
    finally:
        registry_mod.set_default_registry(registry)
    # CONTROL: the hazard is real. Cached, the app never runs again, and the type is not here.
    assert load_bundle_module(bundle, APP, "provider") is first
    assert not _has_type(registry)

    # The guard runs as that test ends; the next test's import of the app registers its type here.
    conftest._forget_apps_registered_elsewhere(registry, before)
    again = load_bundle_module(bundle, APP, "provider")
    assert again is not first
    assert _has_type(registry)


def test_an_app_that_registered_where_it_belongs_stays_the_one_module(bundle):
    """An app whose type is in the registry keeps its one cached module: a second copy of its
    classes is what a test holding the first could no longer recognise."""
    registry = registry_mod.get_default_registry()
    before = conftest._app_modules()
    first = load_bundle_module(bundle, APP, "provider")
    conftest._forget_apps_registered_elsewhere(registry, before)
    assert load_bundle_module(bundle, APP, "provider") is first
    assert _has_type(registry)
