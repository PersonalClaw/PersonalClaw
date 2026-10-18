"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_app_catalog_provenance.py``

Its checks:
``tests/rails/test_app_catalog_provenance_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest

from personalclaw.apps import catalog, manager
from personalclaw.providers import loader


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Sandbox every path the catalog touches, and drop the real default git source.

    ``available_catalog()`` shallow-clones every configured git source, and the shipped
    default is the real published repo — without this the tests here would reach
    github.com. The module-global scan caches are process-wide, so they are cleared on both
    sides."""
    import personalclaw.config.loader as cfg
    from personalclaw import inbox as _inbox
    from personalclaw.providers import entity_routes as _er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(catalog, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(_er, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(_inbox, "config_dir", lambda: tmp_path)
    native = tmp_path / "native"
    native.mkdir()
    monkeypatch.setattr(loader, "BUNDLED_DIR", native)
    monkeypatch.setenv("PERSONALCLAW_FIRST_PARTY_APPS_DIR", str(tmp_path / "no-first-party"))
    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ())
    catalog._git_scan_cache.clear()
    catalog._registry_cache.clear()
    yield tmp_path
    catalog._git_scan_cache.clear()
    catalog._registry_cache.clear()
