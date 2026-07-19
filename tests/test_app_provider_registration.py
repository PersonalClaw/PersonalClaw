"""A provider-only app must register its provider into the live system (A1 + loader).

The original surviving extension path: an app that ships a Python module with a
provider factory (no backend, no UI) must, on install+enable, have its factory
loaded from the installed app dir, instantiated, and registered into the matching
subsystem registry — and deregistered on disable. Proven here with a real
SearchProvider fixture written to disk and driven through the actual loader.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager


@pytest.fixture(autouse=True)
def _isolate_apps(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    # Fresh provider registry per test so registrations don't leak.
    from personalclaw.providers import registry as reg
    monkeypatch.setattr(reg, "_registry", None, raising=False)
    return tmp_path


_PROVIDER_PY = textwrap.dedent("""
    from personalclaw.search_providers.base import SearchCapabilities, SearchHit, SearchProvider, SearchResult
    class FixtureSearch(SearchProvider):
        @property
        def name(self): return "fixture-search"
        @property
        def display_name(self): return "Fixture Search"
        async def is_available(self): return True
        def capabilities(self): return SearchCapabilities(depths=("balanced",))
        async def search(self, query, *, max_results=10, depth=None, **_):
            return SearchResult(results=[SearchHit(url="https://x", title="X "+query)], provider=self.name, query=query)
    def create_provider(config=None): return FixtureSearch()
""")


def _provider_app(tmp_path: Path) -> Path:
    d = tmp_path / "src" / "fixture-search"
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps({
        "name": "fixture-search", "version": "1.0.0", "displayName": "Fixture Search",
        "description": "provider-only fixture",
        "provider": {"type": "search", "implementation": "provider:create_provider", "capabilities": ["search"]},
    }), encoding="utf-8")
    (d / "provider.py").write_text(_PROVIDER_PY, encoding="utf-8")
    return d


@pytest.mark.asyncio
async def test_provider_only_app_registers_and_is_callable(tmp_path):
    from personalclaw.search_providers.registry import get_provider, list_providers, unregister_provider

    # clean any stale registration from a prior run
    unregister_provider("fixture-search")

    res = app_manager.install(_provider_app(tmp_path))
    assert res.ok, res.error

    # install() registers + enables providers → it should be live + callable.
    prov = get_provider("fixture-search")
    assert prov is not None, [p.name for p in list_providers()]
    out = await prov.search("hello")
    assert out.results and out.results[0].title == "X hello"

    # disable deregisters it from the live registry
    assert app_manager.disable("fixture-search")
    assert get_provider("fixture-search") is None

    # re-enable restores it
    assert app_manager.enable("fixture-search")
    assert get_provider("fixture-search") is not None
    unregister_provider("fixture-search")


@pytest.mark.asyncio
async def test_uninstall_deactivates_provider_keeps_registration(tmp_path):
    """Uninstall = deactivate: the live provider is torn down (off) but the
    extension registration is KEPT (disabled) so re-install/enable restores it."""
    from personalclaw.providers.registry import get_provider_registry
    from personalclaw.search_providers.registry import get_provider, unregister_provider

    unregister_provider("fixture-search")
    res = app_manager.install(_provider_app(tmp_path))
    assert res.ok, res.error
    ext_registry = get_provider_registry()
    assert ext_registry.get("fixture-search") is not None

    assert app_manager.uninstall("fixture-search")
    # Torn down in the domain registry (off) but still KNOWN (disabled) in the
    # extension registry — and the files remain so it can be re-enabled.
    assert get_provider("fixture-search") is None
    ext = ext_registry.get("fixture-search")
    assert ext is not None and ext.enabled is False


@pytest.mark.asyncio
async def test_force_uninstall_fully_deregisters_provider(tmp_path):
    """Force-uninstall must FORGET the provider entirely — no disabled ghost in
    the extension registry."""
    from personalclaw.providers.registry import get_provider_registry
    from personalclaw.search_providers.registry import get_provider, unregister_provider

    unregister_provider("fixture-search")
    res = app_manager.install(_provider_app(tmp_path))
    assert res.ok, res.error
    ext_registry = get_provider_registry()
    assert ext_registry.get("fixture-search") is not None

    assert app_manager.force_uninstall("fixture-search")
    assert get_provider("fixture-search") is None
    assert ext_registry.get("fixture-search") is None
    assert "fixture-search" not in [e.name for e in ext_registry.list_extensions()]


def _provider_app_named(tmp_path: Path, app_name: str, provider_name: str) -> Path:
    # Two apps that BOTH ship a top-level `provider.py` but expose different
    # SearchProviders — the module-name-collision regression.
    d = tmp_path / "src" / app_name
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps({
        "name": app_name, "version": "1.0.0", "displayName": app_name,
        "description": "collision fixture",
        "provider": {"type": "search", "implementation": "provider:create_provider", "capabilities": ["search"]},
    }), encoding="utf-8")
    body = textwrap.dedent(f"""
        from personalclaw.search_providers.base import SearchCapabilities, SearchHit, SearchProvider, SearchResult
        class P(SearchProvider):
            @property
            def name(self): return "{provider_name}"
            @property
            def display_name(self): return "{provider_name}"
            async def is_available(self): return True
            def capabilities(self): return SearchCapabilities(depths=("balanced",))
            async def search(self, query, *, max_results=10, depth=None, **_):
                return SearchResult(results=[SearchHit(url="https://{provider_name}", title="{provider_name}")], provider=self.name, query=query)
        def create_provider(config=None): return P()
    """)
    (d / "provider.py").write_text(body, encoding="utf-8")
    return d


def test_two_apps_same_module_name_dont_collide(tmp_path):
    """Two apps both shipping provider.py must BOTH load — not have the second
    silently get the first's cached module from sys.modules."""
    from personalclaw.search_providers.registry import get_provider, unregister_provider

    for pn in ("prov-alpha", "prov-beta"):
        unregister_provider(pn)
    app_manager.install(_provider_app_named(tmp_path, "app-alpha", "prov-alpha"))
    app_manager.install(_provider_app_named(tmp_path, "app-beta", "prov-beta"))

    a = get_provider("prov-alpha")
    b = get_provider("prov-beta")
    assert a is not None and a.name == "prov-alpha"
    assert b is not None and b.name == "prov-beta", "second app collided with first's module"
    unregister_provider("prov-alpha")
    unregister_provider("prov-beta")


_MULTI_PROVIDER_PY = textwrap.dedent("""
    from personalclaw.search_providers.base import SearchCapabilities, SearchHit, SearchProvider, SearchResult
    class _Base(SearchProvider):
        _n = "x"
        @property
        def name(self): return self._n
        @property
        def display_name(self): return self._n
        async def is_available(self): return True
        def capabilities(self): return SearchCapabilities(depths=("balanced",))
        async def search(self, query, *, max_results=10, depth=None, **_):
            return SearchResult(results=[SearchHit(url="https://"+self._n, title=self._n)], provider=self.name, query=query)
    class Primary(_Base): _n = "multi-primary"
    class Secondary(_Base): _n = "multi-secondary"
    def make_primary(config=None): return Primary()
    def make_secondary(config=None): return Secondary()
""")


def _multi_provider_app(tmp_path: Path) -> Path:
    """One app that registers TWO providers of the SAME kind (search) — via the
    singular `provider` plus the plural `providers` list."""
    d = tmp_path / "src" / "multi-app"
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps({
        "name": "multi-app", "version": "1.0.0", "displayName": "Multi",
        "description": "registers multiple providers",
        "provider": {"type": "search", "implementation": "provider:make_primary",
                     "capabilities": ["search"]},
        "providers": [
            {"type": "search", "implementation": "provider:make_secondary",
             "capabilities": ["search"]},
        ],
    }), encoding="utf-8")
    (d / "provider.py").write_text(_MULTI_PROVIDER_PY, encoding="utf-8")
    return d


@pytest.mark.asyncio
async def test_app_registers_multiple_providers(tmp_path):
    """An app may register one or more providers (same or different kinds). Both
    the singular `provider` and each of `providers` go live on install+enable and
    are torn down on disable."""
    from personalclaw.providers.registry import get_provider_registry
    from personalclaw.search_providers.registry import get_provider, unregister_provider

    for pn in ("multi-primary", "multi-secondary"):
        unregister_provider(pn)

    res = app_manager.install(_multi_provider_app(tmp_path))
    assert res.ok, res.error

    # BOTH providers live + callable in the search registry.
    primary, secondary = get_provider("multi-primary"), get_provider("multi-secondary")
    assert primary is not None and secondary is not None
    assert (await secondary.search("q")).results[0].title == "multi-secondary"

    # The extension registry flattens the app's chain to one entry per provider.
    ext_registry = get_provider_registry()
    multi = [e for e in ext_registry.list_extensions() if e.name == "multi-app"]
    assert len(multi) == 2
    impls = {e.provider_config.implementation for e in multi}
    assert impls == {"provider:make_primary", "provider:make_secondary"}

    # Disable tears down BOTH.
    assert app_manager.disable("multi-app")
    assert get_provider("multi-primary") is None
    assert get_provider("multi-secondary") is None
    unregister_provider("multi-primary")
    unregister_provider("multi-secondary")
