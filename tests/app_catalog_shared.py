"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_app_catalog.py``

Its checks:
``tests/rails/test_app_catalog_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest

from personalclaw.apps import catalog, manager
from personalclaw.providers import loader


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    # catalog binds config_dir into its OWN namespace at import (from ... import
    # config_dir), so patch it there too — otherwise catalog._sources_path() escapes
    # the sandbox and reads/writes the real ~/.personalclaw/apps/app-sources.json.
    monkeypatch.setattr(catalog, "config_dir", lambda: tmp_path)
    # The update-surfacing path persists its notified high-water mark via
    # entity_routes (entity_settings/app_updates.json) and falls back to an InboxStore
    # under inbox.config_dir — both bind config_dir at import into their own namespace,
    # so patch those too or the "no re-nag" state would touch the real ~/.personalclaw.
    from personalclaw import inbox as _inbox
    from personalclaw.providers import entity_routes as _er

    monkeypatch.setattr(_er, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(_inbox, "config_dir", lambda: tmp_path)
    native = tmp_path / "native"
    native.mkdir()
    monkeypatch.setattr(loader, "BUNDLED_DIR", native)
    # Neutralize the always-present first-party default source so these tests see a
    # clean local-source baseline (env set to a nonexistent path disables it).
    monkeypatch.setenv("PERSONALCLAW_FIRST_PARTY_APPS_DIR", str(tmp_path / "no-first-party"))
    return tmp_path
